"""Measured component-level acceptance and rejection conformance cases."""

from __future__ import annotations

import hashlib

import pandas as pd
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .encoding import canonical_json_bytes
from .merkle import MerkleTree, ProofStep, verify_proof
from .protocol import (
    AdmissionResult,
    AcceptanceIndex,
    CompareAndSwapConflict,
    CounterConflictError,
    VersionContinuityError,
    VersionStore,
    sign_event,
    verify_event_signature,
)


COMPONENT_CASES = {
    "valid_signature": ("Signature", "accepted"),
    "forged_signature": ("Signature", "rejected"),
    "modified_signed_payload": ("Signature", "rejected"),
    "first_admission": ("AcceptOnce", "accepted"),
    "idempotent_retransmission": ("AcceptOnce", "accepted"),
    "counter_conflict": ("AcceptOnce", "rejected"),
    "valid_merkle_proof": ("Merkle proof", "accepted"),
    "modified_merkle_proof": ("Merkle proof", "rejected"),
    "valid_cas": ("Latest CAS", "accepted"),
    "stale_latest_pointer": ("Latest CAS", "rejected"),
    "skipped_version": ("Latest CAS", "rejected"),
}


def _key(seed: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(f"component|{seed}".encode()).digest()
    )


def _event(seed: int) -> dict[str, object]:
    return {
        "did": f"dev-{seed % 17}",
        "keyver": 1 + seed % 3,
        "boot": f"boot-{seed}",
        "counter": seed % 100_000,
        "event_time_s": 1_700_000_000 + seed,
        "pef_l_min": 250 + seed % 351,
    }


def _execute_case(case: str, seed: int) -> str:
    private_key = _key(seed)
    event = _event(seed)
    signature = sign_event(private_key, event)
    public_key = private_key.public_key()

    if case == "valid_signature":
        accepted = verify_event_signature(public_key, event, signature)
    elif case == "forged_signature":
        forged = bytearray(signature)
        forged[0] ^= 0x01
        accepted = verify_event_signature(public_key, event, bytes(forged))
    elif case == "modified_signed_payload":
        accepted = verify_event_signature(
            public_key, {**event, "pef_l_min": int(event["pef_l_min"]) + 1}, signature
        )
    elif case in {"first_admission", "idempotent_retransmission", "counter_conflict"}:
        index = AcceptanceIndex()
        first = index.accept_once("dev", 2, "boot", 1, "digest-original")
        if case == "first_admission":
            accepted = first is AdmissionResult.NEW
        elif case == "idempotent_retransmission":
            repeated = index.accept_once("dev", 2, "boot", 1, "digest-original")
            accepted = repeated is AdmissionResult.IDEMPOTENT
        else:
            try:
                index.accept_once("dev", 2, "boot", 1, "digest-conflict")
            except CounterConflictError:
                accepted = False
            else:
                accepted = True
    elif case in {"valid_merkle_proof", "modified_merkle_proof"}:
        payloads = [canonical_json_bytes(_event(seed + offset)) for offset in range(8)]
        tree = MerkleTree(payloads)
        proof = tree.proof(3)
        if case == "modified_merkle_proof":
            first = proof[0]
            sibling = bytearray(first.sibling)
            sibling[0] ^= 0x01
            proof[0] = ProofStep(first.side, bytes(sibling))
        accepted = verify_proof(payloads[3], 3, len(payloads), proof, tree.root)
    else:
        store = VersionStore()
        store.initialize("window", "aid-v1")
        try:
            if case == "valid_cas":
                store.update_latest_cas("window", "aid-v1", 1, "aid-v2", 2)
            elif case == "stale_latest_pointer":
                store.update_latest_cas("window", "stale", 1, "aid-v2", 2)
            elif case == "skipped_version":
                store.update_latest_cas("window", "aid-v1", 1, "aid-v3", 3)
            accepted = True
        except (CompareAndSwapConflict, VersionContinuityError):
            accepted = False
    return "accepted" if accepted else "rejected"


def run_component_conformance(
    *, repetitions: int, seed: int, run_id: str | None = None
) -> pd.DataFrame:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    run_id = run_id or f"components-{seed}"
    rows = []
    for repetition in range(repetitions):
        repetition_seed = seed + repetition
        for case, (component, expected_result) in COMPONENT_CASES.items():
            observed_result = _execute_case(case, repetition_seed)
            rows.append(
                {
                    "run_id": run_id,
                    "seed": repetition_seed,
                    "repetition": repetition,
                    "component": component,
                    "case": case,
                    "expected_result": expected_result,
                    "observed_result": observed_result,
                    "matches_rule": observed_result == expected_result,
                    "provenance": "measured",
                }
            )
    return pd.DataFrame(rows)
