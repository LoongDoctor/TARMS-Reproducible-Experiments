"""Local protocol primitives exercised by the TARMS experiments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .encoding import canonical_json_bytes


EVENT_TAG = b"TARMS-EVENT-v1\x00"


class CounterConflictError(ValueError):
    pass


class CompareAndSwapConflict(ValueError):
    pass


class VersionContinuityError(ValueError):
    pass


class AdmissionResult(str, Enum):
    NEW = "NEW"
    IDEMPOTENT = "IDEMPOTENT"


class AcceptanceIndex:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, int, str, int], str] = {}
        self._lock = RLock()

    def accept_once(
        self,
        did: str,
        keyver: int,
        boot: str,
        counter: int,
        event_digest: str,
    ) -> AdmissionResult:
        key = (did, int(keyver), boot, int(counter))
        with self._lock:
            if key in self._entries:
                if self._entries[key] == event_digest:
                    return AdmissionResult.IDEMPOTENT
                raise CounterConflictError(f"counter conflict at admission key: {key!r}")
            self._entries[key] = event_digest
            return AdmissionResult.NEW

    def read(self, did: str, keyver: int, boot: str, counter: int) -> str | None:
        with self._lock:
            return self._entries.get((did, int(keyver), boot, int(counter)))


@dataclass(frozen=True)
class LatestState:
    aid: str
    version: int


class VersionStore:
    def __init__(self) -> None:
        self._latest: dict[str, LatestState] = {}
        self._lock = RLock()

    def initialize(self, kappa: str, aid: str, version: int = 1) -> LatestState:
        if int(version) != 1:
            raise VersionContinuityError("initial version must be 1")
        state = LatestState(aid=aid, version=int(version))
        with self._lock:
            if kappa in self._latest:
                raise CompareAndSwapConflict(f"latest state already exists for {kappa!r}")
            self._latest[kappa] = state
        return state

    def read_latest(self, kappa: str) -> LatestState:
        with self._lock:
            if kappa not in self._latest:
                raise KeyError(kappa)
            return self._latest[kappa]

    def update_latest_cas(
        self,
        kappa: str,
        expected_aid: str,
        expected_version: int,
        new_aid: str,
        new_version: int,
    ) -> LatestState:
        with self._lock:
            current = self.read_latest(kappa)
            if current.aid != expected_aid or current.version != int(expected_version):
                raise CompareAndSwapConflict(
                    f"stale latest expectation for {kappa!r}: "
                    f"expected ({expected_aid}, {expected_version}), "
                    f"found ({current.aid}, {current.version})"
                )
            if int(new_version) != int(expected_version) + 1:
                raise VersionContinuityError(
                    f"new version {new_version} must equal expected version "
                    f"{expected_version} plus one"
                )
            state = LatestState(aid=new_aid, version=int(new_version))
            self._latest[kappa] = state
            return state


def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _event_message(event: Mapping[str, Any]) -> bytes:
    return EVENT_TAG + canonical_json_bytes(event)


def sign_event(private_key: Ed25519PrivateKey, event: Mapping[str, Any]) -> bytes:
    return private_key.sign(_event_message(event))


def verify_event_signature(
    public_key: Ed25519PublicKey,
    event: Mapping[str, Any],
    signature: bytes,
) -> bool:
    try:
        public_key.verify(signature, _event_message(event))
    except InvalidSignature:
        return False
    return True
