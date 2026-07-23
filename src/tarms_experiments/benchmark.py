"""Measured local protocol microbenchmarks."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable, Iterable
from typing import Any, Mapping, Sequence

import pandas as pd
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .encoding import canonical_json_bytes
from .merkle import MerkleTree, verify_proof
from .protocol import (
    AcceptanceIndex,
    VersionStore,
    sign_event,
    verify_event_signature,
)


RAW_COLUMNS = (
    "run_id",
    "seed",
    "batch_size",
    "repetition",
    "stage",
    "duration_ns",
    "record_count",
    "late_count",
    "provenance",
)

STAGES = (
    "sign_batch",
    "verify_batch",
    "merkle_build",
    "proof_verify",
    "signature_admission_batch",
    "late_rebuild",
)


def _private_key(seed: int, batch_size: int) -> Ed25519PrivateKey:
    material = hashlib.sha256(f"TARMS|{seed}|{batch_size}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(material)


def _events(batch_size: int, seed: int, prefix: str = "record") -> list[dict[str, Any]]:
    return [
        {
            "did": f"dev-{(index + seed) % 31:02d}",
            "keyver": 1 + (index + seed) % 3,
            "boot": f"boot-{seed:08x}",
            "counter": index,
            "event_time_s": 1_700_000_000 + index * 60,
            "pef_l_min": 250 + ((index * 17 + seed) % 351),
            "kind": prefix,
        }
        for index in range(batch_size)
    ]


def signature_admission_loop(
    events: Sequence[Mapping[str, Any]],
    signatures: Sequence[bytes],
    public_key: Any,
) -> int | bool:
    """Verify event signatures, then apply the in-memory admission state machine."""
    if len(events) != len(signatures):
        raise ValueError("events and signatures must have the same length")
    index = AcceptanceIndex()
    for event, signature in zip(events, signatures, strict=True):
        if not verify_event_signature(public_key, event, signature):
            return False
        payload = canonical_json_bytes(event)
        index.accept_once(
            str(event["did"]),
            int(event["keyver"]),
            str(event["boot"]),
            int(event["counter"]),
            hashlib.sha256(payload).hexdigest(),
        )
    return len(events)


def _stage_functions(batch_size: int, seed: int) -> dict[str, Callable[[], object]]:
    private_key = _private_key(seed, batch_size)
    public_key = private_key.public_key()
    events = _events(batch_size, seed)
    payloads = [canonical_json_bytes(event) for event in events]
    signatures = [sign_event(private_key, event) for event in events]
    prepared_tree = MerkleTree(payloads)
    proof_index = batch_size // 2
    late_count = max(1, batch_size // 32)
    late_events = _events(late_count, seed + 1_000_003, prefix="late")
    late_payloads = [canonical_json_bytes(event) for event in late_events]

    def sign_batch() -> object:
        return [sign_event(private_key, event) for event in events]

    def verify_batch() -> object:
        return all(
            verify_event_signature(public_key, event, signature)
            for event, signature in zip(events, signatures, strict=True)
        )

    def merkle_build() -> object:
        return MerkleTree(payloads).root

    def proof_verify() -> object:
        proof = prepared_tree.proof(proof_index)
        return verify_proof(
            payloads[proof_index],
            proof_index,
            len(payloads),
            proof,
            prepared_tree.root,
        )

    def signature_admission_batch() -> object:
        return signature_admission_loop(events, signatures, public_key)

    def late_rebuild() -> object:
        old_root = MerkleTree(payloads).root.hex()
        new_root = MerkleTree(payloads + late_payloads).root.hex()
        store = VersionStore()
        store.initialize("window", old_root, 1)
        return store.update_latest_cas("window", old_root, 1, new_root, 2)

    return {
        "sign_batch": sign_batch,
        "verify_batch": verify_batch,
        "merkle_build": merkle_build,
        "proof_verify": proof_verify,
        "signature_admission_batch": signature_admission_batch,
        "late_rebuild": late_rebuild,
    }


def _time_call(function: Callable[[], object]) -> int:
    start = time.perf_counter_ns()
    result = function()
    elapsed = time.perf_counter_ns() - start
    if result is False:
        raise RuntimeError("benchmarked protocol operation returned False")
    return max(1, elapsed)


def run_microbenchmark(
    *,
    batch_sizes: Iterable[int],
    warmups: int,
    repetitions: int,
    seed: int,
    run_id: str | None = None,
) -> pd.DataFrame:
    if warmups < 0 or repetitions <= 0:
        raise ValueError("warmups must be nonnegative and repetitions positive")
    run_id = run_id or f"python-{seed}"
    rows: list[dict[str, Any]] = []
    for batch_size in batch_sizes:
        batch_size = int(batch_size)
        if batch_size <= 0:
            raise ValueError("batch sizes must be positive")
        sub_seed = int.from_bytes(
            hashlib.sha256(f"{seed}|{batch_size}".encode()).digest()[:8], "big"
        )
        functions = _stage_functions(batch_size, sub_seed)
        for stage in STAGES:
            for _ in range(warmups):
                functions[stage]()
        rng = random.Random(sub_seed)
        for repetition in range(repetitions):
            order = list(STAGES)
            rng.shuffle(order)
            for stage in order:
                rows.append(
                    {
                        "run_id": run_id,
                        "seed": sub_seed,
                        "batch_size": batch_size,
                        "repetition": repetition,
                        "stage": stage,
                        "duration_ns": _time_call(functions[stage]),
                        "record_count": batch_size,
                        "late_count": max(1, batch_size // 32),
                        "provenance": "measured",
                    }
                )
    return pd.DataFrame(rows, columns=RAW_COLUMNS)
