"""Deterministic late-update root-conformance experiments."""

from __future__ import annotations

import copy
import random

import pandas as pd

from .encoding import canonical_json_bytes
from .merkle import MerkleTree


CASES = {
    "consistent_set": "accepted",
    "payload_modification": "aborted",
    "record_deletion": "aborted",
    "record_insertion": "aborted",
    "storage_reordering": "accepted",
    "counter_field_swap": "aborted",
}


def _events(seed: int, count: int = 32) -> list[dict[str, object]]:
    rng = random.Random(seed)
    return [
        {
            "did": f"dev-{index % 4}",
            "boot": f"boot-{seed}",
            "counter": index,
            "event_time_s": 1_700_000_000 + index * 60,
            "pef_l_min": rng.randint(250, 600),
        }
        for index in range(count)
    ]


def _canonical_root(events: list[dict[str, object]]) -> bytes:
    payloads = sorted(canonical_json_bytes(event) for event in events)
    return MerkleTree(payloads).root


def _candidate(case: str, events: list[dict[str, object]], seed: int) -> list[dict[str, object]]:
    candidate = copy.deepcopy(events)
    if case == "payload_modification":
        candidate[0]["pef_l_min"] = int(candidate[0]["pef_l_min"]) + 1
    elif case == "record_deletion":
        candidate.pop()
    elif case == "record_insertion":
        candidate.append(
            {
                "did": "dev-extra",
                "boot": f"boot-{seed}",
                "counter": 99_999,
                "event_time_s": 1_700_999_999,
                "pef_l_min": 401,
            }
        )
    elif case == "storage_reordering":
        random.Random(seed + 31).shuffle(candidate)
    elif case == "counter_field_swap":
        candidate[0]["counter"], candidate[1]["counter"] = (
            candidate[1]["counter"],
            candidate[0]["counter"],
        )
    return candidate


def run_late_update_conformance(
    *, repetitions: int, seed: int, run_id: str | None = None
) -> pd.DataFrame:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    run_id = run_id or f"conformance-{seed}"
    rows = []
    for repetition in range(repetitions):
        repetition_seed = seed + repetition
        events = _events(repetition_seed)
        expected_root = _canonical_root(events)
        for case, expected_result in CASES.items():
            observed_root = _canonical_root(_candidate(case, events, repetition_seed))
            observed_result = "accepted" if observed_root == expected_root else "aborted"
            rows.append(
                {
                    "run_id": run_id,
                    "seed": repetition_seed,
                    "repetition": repetition,
                    "case": case,
                    "expected_result": expected_result,
                    "observed_result": observed_result,
                    "matches_rule": observed_result == expected_result,
                    "record_count": len(events),
                    "provenance": "measured",
                }
            )
    return pd.DataFrame(rows)
