"""Execute prespecified baseline and ablation matrices over AAMOS payloads.

The module keeps three analysis populations deliberately separate:

* ``clean_decisions`` contains every eligible participant-day once per seed and
  pipeline, with no scenario/rate replication;
* ``injection_manifest`` is pipeline-independent and contains only selected
  attack targets;
* ``attack_decisions`` contains only those selected targets, once per pipeline.

Capability-boundary controls have their own manifest and decision table.
Record-level mutations can be reconstructed as
``clean_all - clean_targets + attack_targets`` without duplicating clean rows.
History transitions remain operation-level estimands unless a coherent joint
successor deployment is explicitly implemented.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from . import aamos_protocol, aamos_scenarios
from .aamos_protocol import (
    ALL_CHECKS,
    ProtocolEnvelope,
    ProtocolProfile,
    build_clean_history,
    verify_envelope,
)


METRIC_DEFINITION_VERSION = "aamos-integrity-v4"
ATTACK_RATES = (0.01, 0.05, 0.10, 0.20)
FIXED_RATES = (0.0, *ATTACK_RATES)
FIXED_SEEDS = tuple(range(20260722, 20260742))
HISTORY_OPERATION_SCENARIOS = frozenset(
    {
        "historical_modification",
        "historical_deletion",
        "historical_insertion",
    }
)

PIPELINES: dict[str, tuple[str, ...]] = {
    "unverified": (),
    "signature_only": ("signature",),
    "signature_admission": ("signature", "admission"),
    "signature_binding_admission": (
        "signature",
        "binding",
        "admission",
    ),
    "all_checks": ALL_CHECKS,
    **{
        f"all_minus_{check}": tuple(
            item for item in ALL_CHECKS if item != check
        )
        for check in ALL_CHECKS
    },
}


_FALLBACK_TRIGGERED_CHECKS: dict[str, tuple[str, ...]] = {
    "payload_after_signing": ("signature", "merkle"),
    "wrong_device": ("device",),
    "revoked_device": ("device",),
    "binding_mismatch": ("binding",),
    "counter_conflict": ("admission",),
    "tampered_merkle_leaf": ("merkle",),
    "tampered_merkle_path": ("merkle",),
    "tampered_merkle_root": ("merkle",),
    "stale_latest_pointer": ("freshness",),
    "authorization_substitution": ("authorization",),
    "historical_modification": ("history",),
    "historical_deletion": ("history",),
    "historical_insertion": ("history",),
    "mixed_attack": (
        "signature",
        "binding",
        "admission",
        "merkle",
    ),
}

_MUTATION_METADATA: dict[str, tuple[str, str, str]] = {
    "payload_after_signing": ("payload", "modify_after_signing", "none"),
    "wrong_device": ("device_identity", "substitute", "none"),
    "revoked_device": ("device_registry", "revoke", "none"),
    "binding_mismatch": ("participant_device_binding", "substitute", "none"),
    "counter_conflict": (
        "admission_index",
        "same_slot_different_digest",
        "none",
    ),
    "tampered_merkle_leaf": ("merkle_leaf", "modify", "none"),
    "tampered_merkle_path": ("merkle_path", "modify", "none"),
    "tampered_merkle_root": ("merkle_root", "modify", "none"),
    "stale_latest_pointer": ("latest_pointer", "stale", "none"),
    "authorization_substitution": ("request_context", "substitute", "none"),
    "historical_modification": ("history", "modify", "none"),
    "historical_deletion": ("history", "delete", "none"),
    "historical_insertion": ("history", "insert", "none"),
    "mixed_attack": ("multiple_protocol_fields", "mixed", "none"),
    "pre_signing_false_payload": ("source_truth", "pre_signing_falsehood", "none"),
    "permanent_omission": ("event_availability", "omit", "none"),
    "clinical_measurement_error": ("measurement", "error", "none"),
    "incorrect_priority_rule": ("priority_rule", "mis_specify", "none"),
    "legitimate_late_arrival": ("delivery_order", "legitimate_late", "none"),
    "canonical_reorder": ("canonical_order", "reorder", "none"),
}


@dataclass(frozen=True)
class ExperimentTables:
    clean_decisions: pd.DataFrame
    injection_manifest: pd.DataFrame
    attack_decisions: pd.DataFrame
    boundary_manifest: pd.DataFrame
    boundary_decisions: pd.DataFrame


@dataclass(frozen=True)
class _ScenarioExecution:
    envelope: ProtocolEnvelope
    profile: ProtocolProfile
    metadata: Mapping[str, object]
    collection_decision: object | None = None
    collection_evaluated: bool = False


def _stable_integer(*parts: object) -> int:
    digest = hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _record_key(participant_id: object, relative_day: object) -> str:
    participant = str(participant_id)
    return f"{len(participant)}:{participant}:{int(relative_day)}"


def _pair_key(
    *, seed: int, scenario: str, rate: float, record_key: str
) -> str:
    return (
        f"seed={int(seed)};scenario={scenario};rate={float(rate):.6f};"
        f"record={record_key}"
    )


def _operation_identity(
    *,
    seed: int,
    scenario: str,
    rate: float,
    participant_id: str,
    affected_key: str,
) -> str:
    """Identify the materialized transition, not its requested payload source."""

    return (
        f"seed={int(seed)};scenario={scenario};rate={float(rate):.6f};"
        f"participant={participant_id};affected={affected_key}"
    )


def _allocate_hamilton(
    sizes: Mapping[object, int],
    total: int,
    *,
    tie_seed: int,
) -> dict[object, int]:
    """Allocate ``total`` proportionally with deterministic randomized ties."""

    if total < 0:
        raise ValueError("allocation total cannot be negative")
    size_total = sum(int(value) for value in sizes.values())
    if total > size_total:
        raise ValueError("allocation exceeds available rows")
    if size_total == 0:
        return {key: 0 for key in sizes}
    exact = {
        key: int(value) * total / size_total for key, value in sizes.items()
    }
    allocated = {
        key: min(int(value), int(np.floor(exact[key])))
        for key, value in sizes.items()
    }
    remaining = total - sum(allocated.values())
    order = sorted(
        sizes,
        key=lambda key: (
            -(exact[key] - np.floor(exact[key])),
            _stable_integer(tie_seed, repr(key)),
            repr(key),
        ),
    )
    while remaining:
        changed = False
        for key in order:
            if allocated[key] < int(sizes[key]):
                allocated[key] += 1
                remaining -= 1
                changed = True
                if remaining == 0:
                    break
        if not changed:  # defensive; capacity was checked above
            raise RuntimeError("Hamilton allocation exhausted capacity")
    return allocated


def select_stratified_targets(
    frame: pd.DataFrame, *, rate: float, seed: int
) -> list[Any]:
    """Select a deterministic participant/priority-stratified target sample.

    Participant and priority margins are allocated independently and reconciled
    through a small integer max-flow.  A seeded draw is then made inside each
    joint stratum.  This preserves the exact rounded target count while
    avoiding dataframe-index-dependent pair identities.
    """

    if not 0 < rate <= 1:
        raise ValueError("rate must be in (0, 1]")
    required = {
        "participant_id",
        "clean_priority",
        "eligible",
        "relative_day",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            "target frame missing columns: " + ", ".join(missing)
        )
    eligible = frame.loc[frame["eligible"].astype(bool)].copy()
    if eligible.empty:
        raise ValueError("no eligible participant-days")
    total = max(1, int(np.floor(len(eligible) * float(rate) + 0.5)))

    participant_sizes = {
        str(participant): int(len(group))
        for participant, group in eligible.groupby(
            "participant_id", sort=True
        )
    }
    priority_sizes = {
        int(priority): int(len(group))
        for priority, group in eligible.groupby(
            "clean_priority", sort=True
        )
    }
    if total >= len(participant_sizes):
        # Once the target budget can represent every cluster, guarantee at
        # least one target per participant and allocate the remainder
        # proportionally over residual participant-days.
        residual = {
            participant: size - 1
            for participant, size in participant_sizes.items()
        }
        residual_counts = _allocate_hamilton(
            residual,
            total - len(participant_sizes),
            tie_seed=_stable_integer(seed, "participant-margin"),
        )
        participant_counts = {
            participant: 1 + residual_counts[participant]
            for participant in participant_sizes
        }
    else:
        participant_counts = _allocate_hamilton(
            participant_sizes,
            total,
            tie_seed=_stable_integer(seed, "participant-margin"),
        )
    priority_counts = _allocate_hamilton(
        priority_sizes,
        total,
        tie_seed=_stable_integer(seed, "priority-margin"),
    )
    cell_sizes = {
        (str(participant), int(priority)): int(len(group))
        for (participant, priority), group in eligible.groupby(
            ["participant_id", "clean_priority"], sort=True
        )
    }

    # Solve the small integer transportation problem (<=22×4) as a
    # deterministic max-flow so both participant and priority marginals are
    # respected exactly whenever the rounded marginals are jointly feasible.
    source = ("source",)
    sink = ("sink",)
    capacity: dict[tuple[object, object], int] = {}
    adjacency: dict[object, set[object]] = {}

    def add_edge(left: object, right: object, value: int) -> None:
        capacity[(left, right)] = int(value)
        capacity.setdefault((right, left), 0)
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)

    for participant, count in participant_counts.items():
        add_edge(source, ("participant", participant), count)
    for (participant, priority), size in cell_sizes.items():
        add_edge(
            ("participant", participant),
            ("priority", priority),
            size,
        )
    for priority, count in priority_counts.items():
        add_edge(("priority", priority), sink, count)

    flow = 0
    while flow < total:
        parent: dict[object, object | None] = {source: None}
        queue = [source]
        for node in queue:
            neighbours = sorted(
                adjacency.get(node, ()),
                key=lambda value: (
                    _stable_integer(seed, repr(node), repr(value)),
                    repr(value),
                ),
            )
            for neighbour in neighbours:
                if (
                    neighbour not in parent
                    and capacity[(node, neighbour)] > 0
                ):
                    parent[neighbour] = node
                    queue.append(neighbour)
                    if neighbour == sink:
                        break
            if sink in parent:
                break
        if sink not in parent:
            break
        amount = total - flow
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            amount = min(amount, capacity[(previous, node)])
            node = previous
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            capacity[(previous, node)] -= amount
            capacity[(node, previous)] += amount
            node = previous
        flow += amount

    cell_counts: dict[tuple[str, int], int] = {}
    if flow == total:
        for (participant, priority), size in cell_sizes.items():
            remaining = capacity[
                (("participant", participant), ("priority", priority))
            ]
            cell_counts[(participant, priority)] = size - remaining
    else:
        # Rare incompatible rounded margins: preserve the participant margin
        # and allocate priorities within each participant.  The fallback is
        # explicit and deterministic rather than silently changing total N.
        cell_counts = {}
        for participant, participant_total in participant_counts.items():
            sizes = {
                priority: cell_sizes.get((participant, priority), 0)
                for priority in priority_sizes
            }
            cell_counts.update(
                {
                    (participant, priority): count
                    for priority, count in _allocate_hamilton(
                        sizes,
                        participant_total,
                        tie_seed=_stable_integer(
                            seed, "priority-within", participant
                        ),
                    ).items()
                }
            )

    selected: list[Any] = []
    for participant, priority in sorted(cell_counts):
        count = cell_counts[(participant, priority)]
        if not count:
            continue
        group = eligible.loc[
            eligible["participant_id"].astype(str) == participant
        ]
        group = group.loc[
            group["clean_priority"].astype(int) == priority
        ].sort_values(["relative_day"], kind="stable")
        candidates = group.index.to_numpy()
        rng = np.random.default_rng(
            _stable_integer(seed, priority, participant)
        )
        chosen = rng.choice(candidates, size=count, replace=False)
        selected.extend(chosen.tolist())
    return sorted(
        selected,
        key=lambda index: (
            str(frame.loc[index, "participant_id"]),
            int(frame.loc[index, "relative_day"]),
        ),
    )


def _triggered_checks(scenario: str) -> tuple[str, ...]:
    provider = getattr(aamos_scenarios, "scenario_failure_checks", None)
    if callable(provider):
        supplied = tuple(str(value) for value in provider(scenario))
        if supplied:
            return supplied
    if scenario in _FALLBACK_TRIGGERED_CHECKS:
        return _FALLBACK_TRIGGERED_CHECKS[scenario]
    expected = getattr(aamos_scenarios, "REJECT_SCENARIOS", {}).get(
        scenario
    )
    return (str(expected),) if expected else ()


def expected_pipeline_outcome(
    scenario: str, enabled_checks: Sequence[str]
) -> tuple[str, str, bool]:
    """Return prespecified outcome, first failure stage, and ESA applicability."""

    triggered = set(_triggered_checks(scenario))
    if "history" in triggered:
        collection_enabled = {
            "merkle",
            "freshness",
        }.issubset(enabled_checks)
        return (
            ("reject", "history", True)
            if collection_enabled
            else ("accept", "none", False)
        )
    for check in ALL_CHECKS:
        if check in triggered and check in enabled_checks:
            return "reject", check, True
    return "accept", "none", False


def _scenario_class(scenario: str) -> str:
    if scenario in getattr(aamos_scenarios, "REJECT_SCENARIOS", {}):
        return "attack"
    if scenario in getattr(aamos_scenarios, "BOUNDARY_SCENARIOS", ()):
        return "boundary_control"
    raise ValueError(f"unknown AAMOS scenario: {scenario}")


def _boundary_expected_outcome(scenario: str) -> str:
    provider = getattr(aamos_scenarios, "scenario_expected_outcome", None)
    if callable(provider):
        return str(provider(scenario))
    if scenario in {
        "pre_signing_false_payload",
        "permanent_omission",
        "clinical_measurement_error",
        "incorrect_priority_rule",
    }:
        return "undetectable"
    return "accept"


def _metadata_mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _prepare_profile(
    envelope: ProtocolEnvelope,
    profile: ProtocolProfile,
    scenario: str,
) -> ProtocolProfile:
    provider = getattr(aamos_scenarios, "prepare_scenario_profile", None)
    if callable(provider):
        return provider(envelope, profile, scenario=scenario)
    return profile


def _execute_scenario(
    envelope: ProtocolEnvelope,
    profile: ProtocolProfile,
    *,
    scenario: str,
    seed: int,
    record_key: str,
    history_state: tuple[object, ProtocolProfile] | None = None,
) -> _ScenarioExecution:
    collection_scenarios = set(
        getattr(aamos_scenarios, "COLLECTION_SCENARIOS", ())
    )
    if scenario in collection_scenarios:
        if history_state is None:
            raise ValueError(
                f"collection scenario {scenario} requires history state"
            )
        before, history_profile = history_state
        executor = getattr(aamos_scenarios, "apply_history_scenario")
        kwargs: dict[str, object] = {
            "scenario": scenario,
            "seed": int(seed),
        }
        # Newer history interfaces may bind the operation to the manifest
        # target.  Older interfaces choose a deterministic affected key.
        import inspect

        if "affected_key" in inspect.signature(executor).parameters:
            kwargs["affected_key"] = (
                envelope.participant_id,
                int(envelope.relative_day),
            )
        result = executor(before, history_profile, **kwargs)
        affected = getattr(result, "affected_key", None)
        representative = envelope
        if affected is not None:
            after_snapshot = getattr(result, "after", None)
            if after_snapshot is not None:
                try:
                    representative = after_snapshot.envelope_for(affected)
                except KeyError:
                    # A deleted target has no candidate record.  Baselines that
                    # omit either collection predicate can only validate a
                    # surviving candidate record and cannot infer the absence.
                    after_envelopes = tuple(after_snapshot.envelopes)
                    if after_envelopes:
                        representative = after_envelopes[0]
        elif getattr(result, "after", None) is not None:
            after_envelopes = tuple(result.after.envelopes)
            if after_envelopes:
                representative = after_envelopes[0]
        metadata = {
            "history_operation": getattr(result, "operation", scenario),
            "history_requested_key": (
                ""
                if getattr(result, "requested_key", None) is None
                else (
                    f"{result.requested_key[0]}:"
                    f"{int(result.requested_key[1])}"
                )
            ),
            "history_affected_key": (
                ""
                if affected is None
                else f"{affected[0]}:{int(affected[1])}"
            ),
            "history_before_version": getattr(
                result, "before_version", np.nan
            ),
            "history_after_version": getattr(
                result, "after_version", np.nan
            ),
            "history_before_root": getattr(
                result, "before_root", b""
            ).hex(),
            "history_after_root": getattr(
                result, "after_root", b""
            ).hex(),
            "history_requested_key_origin": (
                "AAMOS_observed_participant_day"
            ),
            "history_affected_key_origin": (
                "synthetic_unauthorized_effective_set_key"
                if scenario == "historical_insertion"
                else (
                    "AAMOS_observed_withheld_then_restored_record"
                    if scenario == "legitimate_late_arrival"
                    else "AAMOS_observed_participant_day"
                )
            ),
        }
        decision = getattr(result, "decision", None)
        profile_for_snapshot = getattr(
            aamos_protocol, "profile_for_snapshot", None
        )
        candidate_profile = (
            profile_for_snapshot(result.after, history_profile)
            if callable(profile_for_snapshot)
            else getattr(result, "verifier_profile", history_profile)
        )
        return _ScenarioExecution(
            envelope=representative,
            profile=candidate_profile,
            metadata=metadata,
            collection_decision=decision,
            collection_evaluated=decision is not None,
        )

    prepared_profile = _prepare_profile(envelope, profile, scenario)
    rng = np.random.default_rng(
        _stable_integer(seed, scenario, record_key, "mutation")
    )
    executor = getattr(aamos_scenarios, "execute_scenario", None)
    if callable(executor):
        result = executor(
            envelope,
            prepared_profile,
            scenario=scenario,
            rng=rng,
        )
    else:
        result = aamos_scenarios.apply_scenario(
            envelope, prepared_profile, scenario=scenario
        )

    metadata: dict[str, object] = {}
    if isinstance(result, tuple):
        if len(result) == 2:
            mutated, changed_profile = result
        elif len(result) == 3:
            mutated, changed_profile, supplied_metadata = result
            metadata = _metadata_mapping(supplied_metadata)
        else:
            raise TypeError("scenario tuple result must contain 2 or 3 items")
    elif hasattr(result, "envelope") and hasattr(result, "profile"):
        mutated = result.envelope
        changed_profile = result.profile
        metadata = _metadata_mapping(getattr(result, "metadata", None))
    else:
        raise TypeError(
            "scenario result must provide envelope and verifier profile"
        )
    if not isinstance(mutated, ProtocolEnvelope):
        raise TypeError("scenario did not return a ProtocolEnvelope")
    if not isinstance(changed_profile, ProtocolProfile):
        raise TypeError("scenario did not return a ProtocolProfile")
    return _ScenarioExecution(
        envelope=mutated,
        profile=changed_profile,
        metadata=metadata,
    )


def _mutation_metadata(
    scenario: str, supplied: Mapping[str, object] | None = None
) -> dict[str, object]:
    mutation_object, operation, direction = _MUTATION_METADATA.get(
        scenario, ("protocol_state", "scenario_defined", "none")
    )
    result: dict[str, object] = {
        "mutation_object": mutation_object,
        "mutation_operation": operation,
        "attack_direction": direction,
    }
    result.update(dict(supplied or {}))
    return result


def _decision_fields(
    *,
    envelope: ProtocolEnvelope,
    profile: ProtocolProfile,
    pipeline: str,
    checks: tuple[str, ...],
    override: object | None = None,
    evaluated: bool = True,
) -> dict[str, object]:
    if not evaluated:
        return {
            "pipeline": pipeline,
            "enabled_checks": "|".join(checks),
            "accepted": pd.NA,
            "failure_stage": pd.NA,
            "failure_reason": "no_decision",
            "output_priority": np.nan,
            "covered": False,
        }
    decision = (
        override
        if override is not None
        else verify_envelope(envelope, profile, checks)
    )
    accepted = bool(decision.accepted)
    output_priority: float | int = (
        int(envelope.clean_priority) if accepted else np.nan
    )
    return {
        "pipeline": pipeline,
        "enabled_checks": "|".join(checks),
        "accepted": accepted,
        "failure_stage": decision.failure_stage,
        "failure_reason": decision.failure_reason,
        "output_priority": output_priority,
        "covered": bool(accepted and not pd.isna(output_priority)),
    }


def _eligible_frame(frame: pd.DataFrame) -> pd.DataFrame:
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
            "patient-day frame missing columns: " + ", ".join(missing)
        )
    eligible = frame.loc[frame["eligible"].astype(bool)].copy()
    if eligible.empty:
        raise ValueError("patient-day frame contains no eligible rows")
    if eligible.duplicated(["participant_id", "relative_day"]).any():
        raise ValueError("duplicate eligible participant-day")
    eligible["participant_id"] = eligible["participant_id"].astype(str)
    eligible["relative_day"] = eligible["relative_day"].astype(int)
    return eligible


def _manifest_rows(
    frame: pd.DataFrame,
    *,
    scenarios: Iterable[str],
    rates: Iterable[float],
    seeds: Iterable[int],
    scenario_class: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    eligible_n = len(frame)
    for scenario in scenarios:
        if _scenario_class(scenario) != scenario_class:
            raise ValueError(
                f"{scenario} is not a {scenario_class} scenario"
            )
        mutation = _mutation_metadata(scenario)
        for rate in rates:
            if float(rate) == 0:
                continue
            for seed in seeds:
                targets = select_stratified_targets(
                    frame, rate=float(rate), seed=int(seed)
                )
                realized = len(targets) / eligible_n
                for ordinal, index in enumerate(targets, start=1):
                    source = frame.loc[index]
                    record_key = _record_key(
                        source["participant_id"], source["relative_day"]
                    )
                    pair_key = _pair_key(
                        seed=int(seed),
                        scenario=scenario,
                        rate=float(rate),
                        record_key=record_key,
                    )
                    history_operation = (
                        scenario in HISTORY_OPERATION_SCENARIOS
                    )
                    rows.append(
                        {
                            "scenario": scenario,
                            "scenario_class": scenario_class,
                            "expected_primary_stage": getattr(
                                aamos_scenarios, "REJECT_SCENARIOS", {}
                            ).get(scenario, "none"),
                            "expected_outcome": (
                                "reject"
                                if scenario_class == "attack"
                                else _boundary_expected_outcome(scenario)
                            ),
                            "rate_requested": float(rate),
                            "rate_realized": float(realized),
                            "seed": int(seed),
                            "participant_id": str(
                                source["participant_id"]
                            ),
                            "relative_day": int(source["relative_day"]),
                            "record_key": record_key,
                            "pair_key": pair_key,
                            "targeted": True,
                            "injected": scenario_class == "attack",
                            "target_stratum": (
                                f"participant={source['participant_id']}|"
                                f"priority={int(source['clean_priority'])}"
                            ),
                            "target_ordinal": int(ordinal),
                            "target_count_rule": (
                                "round_half_up(total_eligible*rate), minimum 1; "
                                "one per participant when target_N permits"
                            ),
                            "eligible_N": int(eligible_n),
                            "clean_priority": int(
                                source["clean_priority"]
                            ),
                            "attacked_priority": int(
                                source["clean_priority"]
                            ),
                            "mixed_metric_applicable": (
                                not history_operation
                            ),
                            "estimand": (
                                "operation_level_protocol_transition"
                                if history_operation
                                else "eligible_day_record_replacement"
                            ),
                            "joint_deployment": not history_operation,
                            "operation_identity": (
                                ""
                                if history_operation
                                else pair_key
                            ),
                            **mutation,
                            "metric_definition_version": (
                                METRIC_DEFINITION_VERSION
                            ),
                        }
                    )
    return rows


def _rows_to_frame(
    rows: list[dict[str, object]], columns: Sequence[str]
) -> pd.DataFrame:
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=list(columns))


def run_standard_enhanced_experiment(
    frame: pd.DataFrame,
    *,
    attack_scenarios: tuple[str, ...] | None = None,
    boundary_scenarios: tuple[str, ...] | None = None,
    rates: tuple[float, ...] = ATTACK_RATES,
    seeds: tuple[int, ...] = FIXED_SEEDS,
    boundary_rate: float = 0.10,
) -> ExperimentTables:
    """Execute clean, attack-target, and boundary-control populations."""

    eligible = _eligible_frame(frame)
    attack_scenarios = (
        tuple(getattr(aamos_scenarios, "REJECT_SCENARIOS", {}))
        if attack_scenarios is None
        else tuple(attack_scenarios)
    )
    boundary_scenarios = (
        tuple(getattr(aamos_scenarios, "BOUNDARY_SCENARIOS", ()))
        if boundary_scenarios is None
        else tuple(boundary_scenarios)
    )
    rates = tuple(float(rate) for rate in rates if float(rate) != 0.0)
    seeds = tuple(int(seed) for seed in seeds)
    if not seeds:
        raise ValueError("at least one injection seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("injection seeds must be unique")

    histories_by_seed: dict[
        int, dict[str, tuple[ProtocolEnvelope, ProtocolProfile]]
    ] = {}
    history_states_by_seed: dict[
        int, dict[str, tuple[object, ProtocolProfile]]
    ] = {}
    clean_rows: list[dict[str, object]] = []
    for seed in seeds:
        history = build_clean_history(eligible, seed)
        by_key: dict[str, tuple[ProtocolEnvelope, ProtocolProfile]] = {}
        for envelope, profile in history:
            record_key = _record_key(
                envelope.participant_id, envelope.relative_day
            )
            by_key[record_key] = (envelope, profile)
            for pipeline, checks in PIPELINES.items():
                decision = _decision_fields(
                    envelope=envelope,
                    profile=profile,
                    pipeline=pipeline,
                    checks=checks,
                )
                clean_rows.append(
                    {
                        "evaluation_arm": "clean_control",
                        "scenario_class": "clean_control",
                        "seed": int(seed),
                        "participant_id": envelope.participant_id,
                        "relative_day": int(envelope.relative_day),
                        "record_key": record_key,
                        "pair_key": f"seed={seed};record={record_key}",
                        "targeted": False,
                        "injected": False,
                        "clean_priority": int(envelope.clean_priority),
                        "observed_priority": int(
                            envelope.clean_priority
                        ),
                        "metric_definition_version": (
                            METRIC_DEFINITION_VERSION
                        ),
                        **decision,
                    }
                )
        histories_by_seed[seed] = by_key
        history_states: dict[str, tuple[object, ProtocolProfile]] = {}
        build_history_state = getattr(
            aamos_protocol, "build_history_state", None
        )
        if callable(build_history_state):
            for participant_id, group in eligible.groupby(
                "participant_id", sort=True
            ):
                history_states[str(participant_id)] = build_history_state(
                    group, seed=seed, version=1
                )
        history_states_by_seed[seed] = history_states

    manifest_rows = _manifest_rows(
        eligible,
        scenarios=attack_scenarios,
        rates=rates,
        seeds=seeds,
        scenario_class="attack",
    )
    boundary_manifest_rows = _manifest_rows(
        eligible,
        scenarios=boundary_scenarios,
        rates=(float(boundary_rate),),
        seeds=seeds,
        scenario_class="boundary_control",
    )

    def evaluate_manifest(
        manifest: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        clean_lookup = {
            (
                int(row["seed"]),
                str(row["record_key"]),
                str(row["pipeline"]),
            ): row
            for row in clean_rows
        }
        for target in manifest:
            seed = int(target["seed"])
            record_key = str(target["record_key"])
            envelope, profile = histories_by_seed[seed][record_key]
            execution = _execute_scenario(
                envelope,
                profile,
                scenario=str(target["scenario"]),
                seed=seed,
                record_key=record_key,
                history_state=history_states_by_seed[seed].get(
                    envelope.participant_id
                ),
            )
            mutation = _mutation_metadata(
                str(target["scenario"]), execution.metadata
            )
            target.update(
                {
                    key: value
                    for key, value in mutation.items()
                    if key.startswith("history_")
                }
            )
            if str(target["scenario"]) in HISTORY_OPERATION_SCENARIOS:
                affected_key = str(
                    target.get("history_affected_key", "")
                )
                target["operation_identity"] = _operation_identity(
                    seed=seed,
                    scenario=str(target["scenario"]),
                    rate=float(target["rate_requested"]),
                    participant_id=str(target["participant_id"]),
                    affected_key=affected_key,
                )
            for pipeline, checks in PIPELINES.items():
                expected_outcome, expected_stage, applicable = (
                    expected_pipeline_outcome(
                        str(target["scenario"]), checks
                    )
                    if target["scenario_class"] == "attack"
                    else (
                        str(target["expected_outcome"]),
                        "none",
                        False,
                    )
                )
                collection_gate = {
                    "merkle",
                    "freshness",
                }.issubset(checks)
                collection_override = (
                    execution.collection_decision
                    if collection_gate
                    and execution.collection_evaluated
                    else None
                )
                no_collection_decision = (
                    str(target["scenario"])
                    in set(
                        getattr(
                            aamos_scenarios,
                            "COLLECTION_SCENARIOS",
                            (),
                        )
                    )
                    and not execution.collection_evaluated
                )
                decision = _decision_fields(
                    envelope=execution.envelope,
                    profile=execution.profile,
                    pipeline=pipeline,
                    checks=checks,
                    override=collection_override,
                    evaluated=not no_collection_decision,
                )
                clean = clean_lookup[(seed, record_key, pipeline)]
                rejected = (
                    False
                    if pd.isna(decision["accepted"])
                    else not bool(decision["accepted"])
                )
                rows.append(
                    {
                        **{
                            key: target[key]
                            for key in (
                                "scenario",
                                "scenario_class",
                                "expected_primary_stage",
                                "rate_requested",
                                "rate_realized",
                                "seed",
                                "participant_id",
                                "relative_day",
                                "record_key",
                                "pair_key",
                                "targeted",
                                "injected",
                                "clean_priority",
                                "mixed_metric_applicable",
                                "estimand",
                                "joint_deployment",
                                "operation_identity",
                                "metric_definition_version",
                            )
                        },
                        **{
                            key: mutation[key]
                            for key in (
                                "mutation_object",
                                "mutation_operation",
                                "attack_direction",
                            )
                        },
                        "evaluation_arm": (
                            "attack_target"
                            if target["scenario_class"] == "attack"
                            else "boundary_control"
                        ),
                        "expected_outcome": expected_outcome,
                        "expected_first_stage": expected_stage,
                        "stage_applicable": bool(applicable),
                        "attempted": True,
                        "mutated": True,
                        "evaluated": not no_collection_decision,
                        "observed_priority": int(
                            execution.envelope.clean_priority
                        ),
                        "clean_accepted": bool(clean["accepted"]),
                        "clean_output_priority": clean[
                            "output_priority"
                        ],
                        "clean_covered": bool(clean["covered"]),
                        **decision,
                        "stage_hit": bool(
                            applicable
                            and rejected
                            and decision["failure_stage"]
                            == expected_stage
                        ),
                    }
                )
        return rows

    attack_rows = evaluate_manifest(manifest_rows)
    boundary_rows = evaluate_manifest(boundary_manifest_rows)
    default_manifest_columns = [
            "scenario",
            "scenario_class",
            "rate_requested",
            "rate_realized",
            "seed",
            "participant_id",
            "relative_day",
            "record_key",
            "pair_key",
            "targeted",
            "injected",
        ]

    def manifest_columns(
        rows: list[dict[str, object]],
    ) -> list[str]:
        if not rows:
            return default_manifest_columns
        return list(
            dict.fromkeys(
                key for row in rows for key in row
            )
        )

    attack_manifest_columns = manifest_columns(manifest_rows)
    boundary_manifest_columns = manifest_columns(boundary_manifest_rows)
    return ExperimentTables(
        clean_decisions=_rows_to_frame(
            clean_rows,
            [
                "evaluation_arm",
                "seed",
                "participant_id",
                "relative_day",
                "record_key",
                "pipeline",
                "accepted",
            ],
        ),
        injection_manifest=_rows_to_frame(
            manifest_rows, attack_manifest_columns
        ),
        attack_decisions=_rows_to_frame(
            attack_rows,
            [
                "scenario",
                "scenario_class",
                "expected_primary_stage",
                "rate_requested",
                "rate_realized",
                "seed",
                "participant_id",
                "relative_day",
                "record_key",
                "pair_key",
                "targeted",
                "injected",
                "clean_priority",
                "mixed_metric_applicable",
                "estimand",
                "joint_deployment",
                "operation_identity",
                "metric_definition_version",
                "mutation_object",
                "mutation_operation",
                "attack_direction",
                "evaluation_arm",
                "pipeline",
                "accepted",
            ],
        ),
        boundary_manifest=_rows_to_frame(
            boundary_manifest_rows, boundary_manifest_columns
        ),
        boundary_decisions=_rows_to_frame(
            boundary_rows,
            [
                "scenario",
                "scenario_class",
                "expected_primary_stage",
                "rate_requested",
                "rate_realized",
                "seed",
                "participant_id",
                "relative_day",
                "record_key",
                "pair_key",
                "targeted",
                "injected",
                "clean_priority",
                "mixed_metric_applicable",
                "estimand",
                "joint_deployment",
                "operation_identity",
                "metric_definition_version",
                "mutation_object",
                "mutation_operation",
                "attack_direction",
                "evaluation_arm",
                "pipeline",
                "accepted",
            ],
        ),
    )


def run_attack_matrix(
    frame: pd.DataFrame,
    *,
    scenarios: tuple[str, ...],
    rates: tuple[float, ...],
    seeds: tuple[int, ...],
) -> pd.DataFrame:
    """Compatibility wrapper returning scenario decisions in one table."""

    attacks = tuple(
        scenario
        for scenario in scenarios
        if _scenario_class(scenario) == "attack"
    )
    boundaries = tuple(
        scenario
        for scenario in scenarios
        if _scenario_class(scenario) == "boundary_control"
    )
    outputs = run_standard_enhanced_experiment(
        frame,
        attack_scenarios=attacks,
        boundary_scenarios=boundaries,
        rates=rates,
        seeds=seeds,
        boundary_rate=float(rates[0]),
    )
    decision_tables = [
        table
        for table in (
            outputs.attack_decisions,
            outputs.boundary_decisions,
        )
        if not table.empty
    ]
    if not decision_tables:
        return pd.DataFrame()
    if len(decision_tables) == 1:
        return decision_tables[0].reset_index(drop=True)
    return pd.concat(decision_tables, ignore_index=True, sort=False)


def design_contract() -> str:
    """Return a stable JSON representation for run manifests and hashing."""

    return json.dumps(
        {
            "metric_definition_version": METRIC_DEFINITION_VERSION,
            "rates": list(FIXED_RATES),
            "seeds": list(FIXED_SEEDS),
            "pipelines": {
                name: list(checks) for name, checks in PIPELINES.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
