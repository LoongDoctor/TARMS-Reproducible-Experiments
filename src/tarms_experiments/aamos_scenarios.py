"""Immutable record- and history-level scenarios for the AAMOS experiment."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from .aamos_protocol import (
    ALL_CHECKS,
    HistorySnapshot,
    ProtocolEnvelope,
    ProtocolProfile,
    RecordKey,
    VerificationDecision,
    build_history_snapshot,
    profile_for_snapshot,
    profile_after_acceptance,
    profile_with_counter_conflict,
    record_key,
    resign_envelope,
    validate_successor_transition,
    verify_envelope,
)
from .merkle import ProofStep


REJECT_SCENARIOS = {
    "payload_after_signing": "signature",
    "wrong_device": "device",
    "revoked_device": "device",
    "binding_mismatch": "binding",
    "counter_conflict": "admission",
    "tampered_merkle_leaf": "merkle",
    "tampered_merkle_path": "merkle",
    "tampered_merkle_root": "merkle",
    "stale_latest_pointer": "freshness",
    "authorization_substitution": "authorization",
    "historical_modification": "history",
    "historical_deletion": "history",
    "historical_insertion": "history",
    "mixed_attack": "signature",
}

BOUNDARY_SCENARIOS = (
    "idempotent_retransmission",
    "pre_signing_false_payload",
    "permanent_omission",
    "clinical_measurement_error",
    "incorrect_priority_rule",
    "legitimate_late_arrival",
    "canonical_reorder",
)

COLLECTION_SCENARIOS = frozenset(
    {
        "historical_modification",
        "historical_deletion",
        "historical_insertion",
        *(
            scenario
            for scenario in BOUNDARY_SCENARIOS
            if scenario != "idempotent_retransmission"
        ),
    }
)


@dataclass(frozen=True)
class HistoryScenarioResult:
    operation: str
    before: HistorySnapshot
    after: HistorySnapshot
    requested_key: RecordKey | None
    affected_key: RecordKey | None
    decision: VerificationDecision | None
    record_decisions: tuple[tuple[RecordKey, VerificationDecision], ...]
    verifier_profile: ProtocolProfile

    @property
    def before_keys(self) -> tuple[RecordKey, ...]:
        return self.before.canonical_order

    @property
    def after_keys(self) -> tuple[RecordKey, ...]:
        return self.after.canonical_order

    @property
    def before_root(self) -> bytes:
        return self.before.merkle_root

    @property
    def after_root(self) -> bytes:
        return self.after.merkle_root

    @property
    def before_version(self) -> int:
        return self.before.version

    @property
    def after_version(self) -> int:
        return self.after.version


def _flip_first(value: bytes) -> bytes:
    if not value:
        raise ValueError("scenario requires a non-empty byte string")
    return bytes([value[0] ^ 1]) + value[1:]


def _changed_payload(payload_json: str, marker: str) -> str:
    try:
        value = json.loads(payload_json)
    except json.JSONDecodeError:
        value = {"original_payload": payload_json}
    if not isinstance(value, dict):
        value = {"original_payload": value}
    value[f"scenario_{marker}"] = True
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _middle_key(snapshot: HistorySnapshot) -> RecordKey:
    return snapshot.canonical_order[len(snapshot.canonical_order) // 2]


def _replace_record(
    snapshot: HistorySnapshot,
    replacement: ProtocolEnvelope,
    *,
    version: int,
) -> HistorySnapshot:
    key = record_key(replacement)
    material = [
        replacement if record_key(item) == key else item
        for item in snapshot.envelopes
    ]
    return build_history_snapshot(material, version=version)


def _new_record(
    snapshot: HistorySnapshot,
    *,
    source_record: ProtocolEnvelope,
    relative_day: int,
    seed: int,
) -> ProtocolEnvelope:
    return resign_envelope(
        source_record,
        seed=seed,
        relative_day=relative_day,
        clean_priority=source_record.clean_priority,
        payload_json=source_record.payload_json,
        counter=max(item.counter for item in snapshot.envelopes) + 1,
    )


def _record_decisions(
    snapshot: HistorySnapshot, profile: ProtocolProfile
) -> tuple[tuple[RecordKey, VerificationDecision], ...]:
    return tuple(
        (record_key(envelope), verify_envelope(envelope, profile, ALL_CHECKS))
        for envelope in snapshot.envelopes
    )


def mutate_envelope(
    envelope: ProtocolEnvelope,
    scenario: str,
    rng: Any,
    *,
    profile: ProtocolProfile | None = None,
) -> ProtocolEnvelope:
    """Mutate only an adversarial record; collection cases use their own API."""

    del rng
    if scenario in COLLECTION_SCENARIOS:
        raise ValueError(
            f"{scenario} is a collection-level scenario; "
            "use apply_history_scenario"
        )
    if scenario == "revoked_device":
        raise ValueError(
            "revoked_device requires an authoritative registry snapshot"
        )
    if scenario == "payload_after_signing":
        return replace(envelope, payload_json=envelope.payload_json + " ")
    if scenario == "wrong_device":
        return replace(envelope, presented_device_id="device-unregistered")
    if scenario == "binding_mismatch":
        return replace(envelope, bound_participant="participant-other")
    if scenario in {"counter_conflict", "idempotent_retransmission"}:
        raise ValueError(
            f"{scenario} changes admission state; use apply_scenario"
        )
    if scenario == "tampered_merkle_leaf":
        return replace(envelope, merkle_leaf=_flip_first(envelope.merkle_leaf))
    if scenario == "tampered_merkle_path":
        if not envelope.merkle_proof:
            raise ValueError("Merkle path scenario requires a multi-leaf history")
        first = envelope.merkle_proof[0]
        changed = ProofStep(first.side, _flip_first(first.sibling))
        return replace(
            envelope, merkle_proof=(changed, *envelope.merkle_proof[1:])
        )
    if scenario == "tampered_merkle_root":
        return replace(envelope, merkle_root=_flip_first(envelope.merkle_root))
    if scenario == "stale_latest_pointer":
        return replace(envelope, anchor_version=envelope.anchor_version - 1)
    if scenario == "authorization_substitution":
        return replace(envelope, authorized_requester="requester-other")
    if scenario == "mixed_attack":
        return replace(
            envelope,
            payload_json=envelope.payload_json + " ",
            bound_participant="participant-other",
            merkle_root=_flip_first(envelope.merkle_root),
        )
    raise ValueError(f"unknown AAMOS scenario: {scenario}")


def apply_scenario(
    envelope: ProtocolEnvelope,
    profile: ProtocolProfile,
    *,
    scenario: str,
) -> tuple[ProtocolEnvelope, ProtocolProfile]:
    """Apply a scalar record scenario without mutating either input object."""

    if scenario == "revoked_device":
        revoked_registry = profile.device_registry.revoke(envelope.device_id)
        return envelope, replace(profile, device_registry=revoked_registry)
    if scenario == "counter_conflict":
        return envelope, profile_with_counter_conflict(profile, envelope)
    if scenario == "idempotent_retransmission":
        return envelope, profile_after_acceptance(profile, envelope)
    if scenario == "mixed_attack":
        return (
            mutate_envelope(envelope, scenario, None, profile=profile),
            profile_with_counter_conflict(profile, envelope),
        )
    return mutate_envelope(envelope, scenario, None, profile=profile), profile


def apply_history_scenario(
    before: HistorySnapshot,
    profile: ProtocolProfile,
    *,
    scenario: str,
    seed: int,
    affected_key: RecordKey | None = None,
) -> HistoryScenarioResult:
    """Execute a real collection operation and return immutable audit evidence."""

    if scenario not in COLLECTION_SCENARIOS:
        raise ValueError(f"not a collection-level scenario: {scenario}")
    requested_key = affected_key
    if requested_key is not None and requested_key not in before.canonical_order:
        raise ValueError(f"affected_key is not in predecessor: {requested_key!r}")
    target_key = requested_key or _middle_key(before)
    target = before.envelope_for(target_key)

    if scenario in {
        "pre_signing_false_payload",
        "clinical_measurement_error",
        "incorrect_priority_rule",
    }:
        if scenario == "incorrect_priority_rule":
            replacement = resign_envelope(
                target,
                seed=seed,
                clean_priority=(target.clean_priority + 1) % 4,
            )
        else:
            replacement = resign_envelope(
                target,
                seed=seed,
                payload_json=_changed_payload(target.payload_json, scenario),
            )
        after = _replace_record(before, replacement, version=before.version)
        after_profile = profile_for_snapshot(after, profile)
        committed = after.envelope_for(target_key)
        decision = verify_envelope(committed, after_profile, ALL_CHECKS)
        return HistoryScenarioResult(
            operation=scenario,
            before=before,
            after=after,
            requested_key=requested_key,
            affected_key=target_key,
            decision=decision,
            record_decisions=((target_key, decision),),
            verifier_profile=after_profile,
        )

    if scenario == "permanent_omission":
        material = [
            item for item in before.envelopes if record_key(item) != target_key
        ]
        after = build_history_snapshot(material, version=before.version)
        after_profile = profile_for_snapshot(after, profile)
        return HistoryScenarioResult(
            operation=scenario,
            before=before,
            after=after,
            requested_key=requested_key,
            affected_key=target_key,
            decision=None,
            record_decisions=_record_decisions(after, after_profile),
            verifier_profile=after_profile,
        )

    if scenario == "canonical_reorder":
        after = build_history_snapshot(
            tuple(reversed(before.envelopes)), version=before.version
        )
        decision = validate_successor_transition(before, after, profile)
        return HistoryScenarioResult(
            operation=scenario,
            before=before,
            after=after,
            requested_key=requested_key,
            affected_key=None,
            decision=decision,
            record_decisions=(),
            verifier_profile=profile,
        )

    if scenario == "legitimate_late_arrival":
        predecessor_material = [
            envelope
            for envelope in before.envelopes
            if record_key(envelope) != target_key
        ]
        if not predecessor_material:
            raise ValueError(
                "legitimate late arrival requires another predecessor record"
            )
        predecessor = build_history_snapshot(
            predecessor_material, version=before.version
        )
        after = build_history_snapshot(
            (*predecessor.envelopes, target),
            version=predecessor.version + 1,
        )
        transition_profile = profile_for_snapshot(
            predecessor,
            profile,
            authorized_late_keys=profile.authorized_late_keys | {target_key},
        )
        decision = validate_successor_transition(
            predecessor, after, transition_profile
        )
        return HistoryScenarioResult(
            operation=scenario,
            before=predecessor,
            after=after,
            requested_key=requested_key,
            affected_key=target_key,
            decision=decision,
            record_decisions=(),
            verifier_profile=transition_profile,
        )

    if scenario == "historical_modification":
        replacement = resign_envelope(
            target,
            seed=seed,
            payload_json=_changed_payload(
                target.payload_json, "historical_modification"
            ),
        )
        after = _replace_record(
            before, replacement, version=before.version + 1
        )
    elif scenario == "historical_deletion":
        after = build_history_snapshot(
            [
                item
                for item in before.envelopes
                if record_key(item) != target_key
            ],
            version=before.version + 1,
        )
    elif scenario == "historical_insertion":
        added = _new_record(
            before,
            source_record=target,
            relative_day=max(key[1] for key in before.canonical_order) + 1,
            seed=seed,
        )
        target_key = record_key(added)
        after = build_history_snapshot(
            (*before.envelopes, added), version=before.version + 1
        )
    else:  # pragma: no cover - exhaustive guard for type checkers
        raise AssertionError(scenario)

    decision = validate_successor_transition(before, after, profile)
    return HistoryScenarioResult(
        operation=scenario,
        before=before,
        after=after,
        requested_key=requested_key,
        affected_key=target_key,
        decision=decision,
        record_decisions=(),
        verifier_profile=profile,
    )
