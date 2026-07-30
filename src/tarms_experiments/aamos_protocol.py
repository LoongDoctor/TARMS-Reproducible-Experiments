"""Synthetic TARMS envelopes and immutable verifier state for AAMOS-00."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from collections.abc import Sequence

import pandas as pd
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .encoding import canonical_json_bytes
from .merkle import MerkleTree, ProofStep, verify_proof
from .protocol import sign_event, verify_event_signature


ALL_CHECKS = (
    "signature",
    "device",
    "binding",
    "admission",
    "merkle",
    "freshness",
    "authorization",
)

RecordKey = tuple[str, int]


@dataclass(frozen=True)
class DeviceRegistrySnapshot:
    version: int
    active_device_ids: frozenset[str]
    revoked_device_ids: frozenset[str] = frozenset()

    def revoke(self, device_id: str) -> "DeviceRegistrySnapshot":
        if device_id not in self.active_device_ids:
            raise ValueError(f"device is not active: {device_id}")
        return DeviceRegistrySnapshot(
            version=self.version + 1,
            active_device_ids=self.active_device_ids - {device_id},
            revoked_device_ids=self.revoked_device_ids | {device_id},
        )


@dataclass(frozen=True)
class ProtocolEnvelope:
    participant_id: str
    relative_day: int
    clean_priority: int
    payload_json: str
    device_id: str
    presented_device_id: str
    counter: int
    signature: bytes
    device_active: bool
    bound_participant: str
    merkle_leaf: bytes
    merkle_index: int
    merkle_count: int
    merkle_proof: tuple[ProofStep, ...]
    merkle_root: bytes
    anchor_id: str
    anchor_version: int
    authorized_requester: str

    def signed_event(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "relative_day": int(self.relative_day),
            "clean_priority": int(self.clean_priority),
            "payload_json": self.payload_json,
            "device_id": self.device_id,
            "counter": int(self.counter),
        }


@dataclass(frozen=True)
class ProtocolProfile:
    """Authoritative verifier inputs; injectors receive but never alter these."""

    public_key: Ed25519PublicKey
    participant_id: str
    accepted_counter_digests: tuple[tuple[int, bytes], ...]
    trusted_merkle_root: bytes
    latest_anchor_id: str
    latest_version: int
    requester: str
    device_registry: DeviceRegistrySnapshot
    prior_envelopes: tuple[ProtocolEnvelope, ...]
    authorized_late_keys: frozenset[RecordKey] = frozenset()

    @property
    def active_device_ids(self) -> frozenset[str]:
        return self.device_registry.active_device_ids

    @property
    def accepted_counters(self) -> frozenset[int]:
        """Backward-compatible immutable view of occupied admission slots."""

        return frozenset(
            counter for counter, _ in self.accepted_counter_digests
        )

    def accepted_digest(self, counter: int) -> bytes | None:
        """Return the digest occupying ``counter``, if any."""

        for occupied_counter, digest in self.accepted_counter_digests:
            if occupied_counter == int(counter):
                return digest
        return None


@dataclass(frozen=True)
class VerificationDecision:
    accepted: bool
    failure_stage: str
    failure_reason: str


@dataclass(frozen=True)
class HistorySnapshot:
    participant_id: str
    version: int
    envelopes: tuple[ProtocolEnvelope, ...]
    input_order: tuple[RecordKey, ...]
    canonical_order: tuple[RecordKey, ...]
    merkle_root: bytes
    anchor_id: str

    def envelope_for(self, key: RecordKey) -> ProtocolEnvelope:
        for envelope in self.envelopes:
            if record_key(envelope) == key:
                return envelope
        raise KeyError(key)


def record_key(envelope: ProtocolEnvelope) -> RecordKey:
    return (envelope.participant_id, envelope.relative_day)


def envelope_digest(envelope: ProtocolEnvelope) -> bytes:
    """Return the synthetic admission digest for one canonical event."""

    return hashlib.sha256(
        canonical_json_bytes(envelope.signed_event())
    ).digest()


def _private_key(seed: int, participant_id: str) -> Ed25519PrivateKey:
    material = hashlib.sha256(
        f"AAMOS-TARMS|{int(seed)}|{participant_id}".encode()
    ).digest()
    return Ed25519PrivateKey.from_private_bytes(material)


def _required_patient_day_columns(frame: pd.DataFrame) -> None:
    required = {
        "participant_id",
        "relative_day",
        "eligible",
        "clean_priority",
        "payload_json",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "patient-day table missing columns: " + ", ".join(missing)
        )


def _build_clean_histories(
    patient_days: pd.DataFrame, seed: int
) -> list[tuple[ProtocolEnvelope, ProtocolProfile]]:
    _required_patient_day_columns(patient_days)
    eligible = patient_days.loc[patient_days["eligible"].astype(bool)].copy()
    if eligible.empty:
        raise ValueError("patient-day table contains no eligible rows")
    if eligible.duplicated(["participant_id", "relative_day"]).any():
        raise ValueError("duplicate participant/day in envelope input")
    eligible["participant_id"] = eligible["participant_id"].astype(str)
    eligible["relative_day"] = eligible["relative_day"].astype("int64")
    eligible = eligible.sort_values(
        ["participant_id", "relative_day"], kind="stable"
    ).reset_index(drop=True)

    histories: list[tuple[ProtocolEnvelope, ProtocolProfile]] = []
    for participant_id, group in eligible.groupby("participant_id", sort=True):
        private_key = _private_key(seed, participant_id)
        public_key = private_key.public_key()
        device_id = f"device-{participant_id}"
        signed: list[ProtocolEnvelope] = []
        for counter, row in enumerate(group.itertuples(index=False), start=1):
            base = ProtocolEnvelope(
                participant_id=participant_id,
                relative_day=int(row.relative_day),
                clean_priority=int(row.clean_priority),
                payload_json=str(row.payload_json),
                device_id=device_id,
                presented_device_id=device_id,
                counter=counter,
                signature=b"",
                device_active=True,
                bound_participant=participant_id,
                merkle_leaf=b"",
                merkle_index=counter - 1,
                merkle_count=len(group),
                merkle_proof=(),
                merkle_root=b"",
                anchor_id="",
                anchor_version=len(group),
                authorized_requester="respiratory-clinician",
            )
            signature = sign_event(private_key, base.signed_event())
            leaf = canonical_json_bytes(base.signed_event())
            signed.append(replace(base, signature=signature, merkle_leaf=leaf))

        tree = MerkleTree(item.merkle_leaf for item in signed)
        anchor_id = f"anchor-{participant_id}-{tree.root.hex()[:16]}"
        envelopes = [
            replace(
                item,
                merkle_proof=tuple(tree.proof(index)),
                merkle_root=tree.root,
                anchor_id=anchor_id,
            )
            for index, item in enumerate(signed)
        ]
        for index, envelope in enumerate(envelopes):
            prior = tuple(envelopes[:index])
            profile = ProtocolProfile(
                public_key=public_key,
                participant_id=participant_id,
                accepted_counter_digests=tuple(
                    (item.counter, envelope_digest(item))
                    for item in prior
                ),
                trusted_merkle_root=tree.root,
                latest_anchor_id=anchor_id,
                latest_version=len(envelopes),
                requester="respiratory-clinician",
                device_registry=DeviceRegistrySnapshot(
                    version=1,
                    active_device_ids=frozenset({device_id}),
                ),
                prior_envelopes=prior,
            )
            histories.append((envelope, profile))
    return histories


def build_clean_history(
    patient_days: pd.DataFrame, seed: int
) -> list[tuple[ProtocolEnvelope, ProtocolProfile]]:
    """Build canonical participant histories with state as of each record."""

    return _build_clean_histories(patient_days, seed)


def build_clean_envelopes(
    patient_days: pd.DataFrame, seed: int
) -> list[ProtocolEnvelope]:
    """Build canonical participant-history envelopes with genuine Merkle proofs."""

    return [envelope for envelope, _ in _build_clean_histories(patient_days, seed)]


def build_clean_envelope(
    *,
    participant_id: str,
    relative_day: int,
    clean_priority: int,
    payload_json: str,
    seed: int,
) -> tuple[ProtocolEnvelope, ProtocolProfile]:
    """Backward-compatible scalar wrapper over the canonical history builder."""

    frame = pd.DataFrame(
        [
            {
                "participant_id": str(participant_id),
                "relative_day": int(relative_day),
                "eligible": True,
                "clean_priority": int(clean_priority),
                "payload_json": str(payload_json),
            }
        ]
    )
    return _build_clean_histories(frame, seed)[0]


def build_history_snapshot(
    envelopes: Sequence[ProtocolEnvelope], *, version: int
) -> HistorySnapshot:
    """Commit an input collection after canonical participant/day ordering."""

    material = tuple(envelopes)
    if not material:
        raise ValueError("history snapshot requires at least one record")
    if version <= 0:
        raise ValueError("history version must be positive")
    participants = {item.participant_id for item in material}
    if len(participants) != 1:
        raise ValueError("history snapshot must contain exactly one participant")
    input_order = tuple(record_key(item) for item in material)
    if len(set(input_order)) != len(input_order):
        raise ValueError("history snapshot contains duplicate participant/day keys")
    canonical = tuple(
        sorted(material, key=lambda item: (item.participant_id, item.relative_day))
    )
    canonical_order = tuple(record_key(item) for item in canonical)
    leaves = tuple(
        canonical_json_bytes(item.signed_event()) for item in canonical
    )
    tree = MerkleTree(leaves)
    participant_id = next(iter(participants))
    anchor_id = (
        f"anchor-{participant_id}-v{version}-{tree.root.hex()[:16]}"
    )
    committed = tuple(
        replace(
            item,
            merkle_leaf=leaves[index],
            merkle_index=index,
            merkle_count=len(canonical),
            merkle_proof=tuple(tree.proof(index)),
            merkle_root=tree.root,
            anchor_id=anchor_id,
            anchor_version=version,
        )
        for index, item in enumerate(canonical)
    )
    return HistorySnapshot(
        participant_id=participant_id,
        version=version,
        envelopes=committed,
        input_order=input_order,
        canonical_order=canonical_order,
        merkle_root=tree.root,
        anchor_id=anchor_id,
    )


def profile_for_snapshot(
    snapshot: HistorySnapshot,
    template: ProtocolProfile,
    *,
    authorized_late_keys: frozenset[RecordKey] | None = None,
) -> ProtocolProfile:
    """Bind an immutable verifier profile to a committed history snapshot."""

    if snapshot.participant_id != template.participant_id:
        raise ValueError("snapshot/profile participant mismatch")
    return replace(
        template,
        accepted_counter_digests=(),
        trusted_merkle_root=snapshot.merkle_root,
        latest_anchor_id=snapshot.anchor_id,
        latest_version=snapshot.version,
        prior_envelopes=(),
        authorized_late_keys=(
            template.authorized_late_keys
            if authorized_late_keys is None
            else authorized_late_keys
        ),
    )


def build_history_state(
    patient_days: pd.DataFrame, *, seed: int, version: int = 1
) -> tuple[HistorySnapshot, ProtocolProfile]:
    """Build a committed collection and its authoritative verifier snapshot."""

    pairs = _build_clean_histories(patient_days, seed)
    participants = {envelope.participant_id for envelope, _ in pairs}
    if len(participants) != 1:
        raise ValueError("history state requires exactly one participant")
    snapshot = build_history_snapshot(
        [envelope for envelope, _ in pairs], version=version
    )
    profile = profile_for_snapshot(snapshot, pairs[0][1])
    return snapshot, profile


def resign_envelope(
    envelope: ProtocolEnvelope,
    *,
    seed: int,
    relative_day: int | None = None,
    clean_priority: int | None = None,
    payload_json: str | None = None,
    counter: int | None = None,
) -> ProtocolEnvelope:
    """Create a valid pre-signing alternative, before a history is committed."""

    changed = replace(
        envelope,
        relative_day=(
            envelope.relative_day if relative_day is None else int(relative_day)
        ),
        clean_priority=(
            envelope.clean_priority
            if clean_priority is None
            else int(clean_priority)
        ),
        payload_json=(
            envelope.payload_json if payload_json is None else str(payload_json)
        ),
        counter=envelope.counter if counter is None else int(counter),
        signature=b"",
        merkle_leaf=b"",
        merkle_index=0,
        merkle_count=1,
        merkle_proof=(),
        merkle_root=b"",
        anchor_id="",
        anchor_version=1,
    )
    private_key = _private_key(seed, changed.participant_id)
    signature = sign_event(private_key, changed.signed_event())
    return replace(
        changed,
        signature=signature,
        merkle_leaf=canonical_json_bytes(changed.signed_event()),
    )


def _history_record_bytes(envelope: ProtocolEnvelope) -> bytes:
    return canonical_json_bytes(envelope.signed_event()) + envelope.signature


def validate_successor_transition(
    before: HistorySnapshot,
    after: HistorySnapshot,
    profile: ProtocolProfile,
) -> VerificationDecision:
    """Validate canonical reorder or an authorized append-only late successor."""

    if (
        before.participant_id != profile.participant_id
        or not hmac.compare_digest(
            before.merkle_root, profile.trusted_merkle_root
        )
        or before.anchor_id != profile.latest_anchor_id
        or before.version != profile.latest_version
    ):
        return _reject("history", "predecessor_not_trusted")
    if after.participant_id != before.participant_id:
        return _reject("history", "successor_participant_mismatch")

    after_profile = profile_for_snapshot(after, profile)
    transition_checks = tuple(
        check for check in ALL_CHECKS if check != "admission"
    )
    for envelope in after.envelopes:
        record_decision = verify_envelope(
            envelope, after_profile, transition_checks
        )
        if not record_decision.accepted:
            return record_decision

    before_by_key = {
        record_key(envelope): envelope for envelope in before.envelopes
    }
    after_by_key = {
        record_key(envelope): envelope for envelope in after.envelopes
    }
    before_keys = set(before_by_key)
    after_keys = set(after_by_key)
    removed = before_keys - after_keys
    added = after_keys - before_keys
    changed = {
        key
        for key in before_keys & after_keys
        if _history_record_bytes(before_by_key[key])
        != _history_record_bytes(after_by_key[key])
    }

    if after.version == before.version:
        if removed or added or changed:
            return _reject("history", "same_version_content_changed")
        if not hmac.compare_digest(after.merkle_root, before.merkle_root):
            return _reject("history", "same_version_root_changed")
        return VerificationDecision(True, "none", "accepted")

    if after.version != before.version + 1:
        return _reject("history", "successor_version_not_contiguous")
    if removed:
        return _reject("history", "historical_record_deleted")
    if changed:
        return _reject("history", "historical_record_modified")
    if added - profile.authorized_late_keys:
        return _reject(
            "history", "historical_record_inserted_without_authorization"
        )
    if not added:
        return _reject("history", "successor_has_no_effective_set_change")
    return VerificationDecision(True, "none", "accepted")


def profile_after_acceptance(
    profile: ProtocolProfile, envelope: ProtocolEnvelope
) -> ProtocolProfile:
    """Return authoritative post-acceptance state without mutating either input."""

    if envelope.participant_id != profile.participant_id:
        raise ValueError("cannot accept an envelope for another participant")
    if not hmac.compare_digest(
        envelope.merkle_root, profile.trusted_merkle_root
    ):
        raise ValueError("cannot accept an envelope outside the trusted history")
    digest = envelope_digest(envelope)
    occupied = profile.accepted_digest(envelope.counter)
    if occupied is not None:
        if hmac.compare_digest(occupied, digest):
            return profile
        raise ValueError("envelope counter contains a different digest")
    return replace(
        profile,
        accepted_counter_digests=tuple(
            sorted(
                (
                    *profile.accepted_counter_digests,
                    (int(envelope.counter), digest),
                ),
                key=lambda item: item[0],
            )
        ),
        prior_envelopes=(*profile.prior_envelopes, envelope),
    )


def profile_with_counter_conflict(
    profile: ProtocolProfile, envelope: ProtocolEnvelope
) -> ProtocolProfile:
    """Occupy an envelope's slot with a deterministic different digest."""

    existing = [
        (counter, digest)
        for counter, digest in profile.accepted_counter_digests
        if counter != int(envelope.counter)
    ]
    conflicting = hashlib.sha256(
        b"AAMOS-counter-conflict|" + envelope_digest(envelope)
    ).digest()
    if hmac.compare_digest(conflicting, envelope_digest(envelope)):
        raise AssertionError("counter-conflict digest must differ")
    existing.append((int(envelope.counter), conflicting))
    return replace(
        profile,
        accepted_counter_digests=tuple(
            sorted(existing, key=lambda item: item[0])
        ),
    )


def _reject(stage: str, reason: str) -> VerificationDecision:
    return VerificationDecision(False, stage, reason)


def verify_envelope(
    envelope: ProtocolEnvelope,
    profile: ProtocolProfile,
    enabled_checks: tuple[str, ...] = ALL_CHECKS,
) -> VerificationDecision:
    unknown = sorted(set(enabled_checks) - set(ALL_CHECKS))
    if unknown:
        raise ValueError("unknown verifier checks: " + ", ".join(unknown))
    checks = set(enabled_checks)
    event = envelope.signed_event()
    if "signature" in checks and not verify_event_signature(
        profile.public_key, event, envelope.signature
    ):
        return _reject("signature", "signature_invalid")
    if "device" in checks and (
        not envelope.device_active
        or envelope.device_id not in profile.device_registry.active_device_ids
        or envelope.device_id in profile.device_registry.revoked_device_ids
        or envelope.presented_device_id != envelope.device_id
    ):
        return _reject("device", "device_inactive_or_unknown")
    if "binding" in checks and (
        envelope.bound_participant != envelope.participant_id
        or envelope.participant_id != profile.participant_id
    ):
        return _reject("binding", "patient_device_binding_mismatch")
    if "admission" in checks:
        occupied = profile.accepted_digest(envelope.counter)
        if occupied is not None and not hmac.compare_digest(
            occupied, envelope_digest(envelope)
        ):
            return _reject("admission", "counter_conflict")
    if "merkle" in checks:
        if not hmac.compare_digest(
            envelope.merkle_root, profile.trusted_merkle_root
        ):
            return _reject("merkle", "root_not_trusted")
        canonical_leaf = canonical_json_bytes(event)
        if not hmac.compare_digest(envelope.merkle_leaf, canonical_leaf):
            return _reject("merkle", "leaf_event_mismatch")
        if not verify_proof(
            envelope.merkle_leaf,
            envelope.merkle_index,
            envelope.merkle_count,
            envelope.merkle_proof,
            envelope.merkle_root,
        ):
            return _reject("merkle", "membership_invalid")
    if "freshness" in checks and (
        envelope.anchor_id != profile.latest_anchor_id
        or envelope.anchor_version != profile.latest_version
    ):
        return _reject("freshness", "latest_pointer_mismatch")
    if "authorization" in checks and (
        envelope.authorized_requester != profile.requester
    ):
        return _reject("authorization", "request_context_unauthorized")
    return VerificationDecision(True, "none", "accepted")
