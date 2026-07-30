"""Submission-grade statistics for the AAMOS controlled integrity experiment."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .aamos_experiment import (
    HISTORY_OPERATION_SCENARIOS,
    METRIC_DEFINITION_VERSION,
    PIPELINES,
    ExperimentTables,
)


BOOTSTRAP_METHOD = "crossed_seed_participant_multinomial"
BOOTSTRAP_INTERVAL_TYPE = "percentile_95"


@dataclass(frozen=True)
class AnalysisOutputs:
    per_seed_metrics: pd.DataFrame
    summary: pd.DataFrame
    attack_stage_matrix: pd.DataFrame
    paired_contrasts: pd.DataFrame


@dataclass
class _AnalysisBlock:
    stable_key: str
    seeds: tuple[int, ...]
    participants: tuple[str, ...]
    metadata: list[dict[str, Any]]
    numerator: np.ndarray
    denominator: np.ndarray


def percentile_interval(samples: np.ndarray) -> tuple[float, float]:
    """Return the unmodified finite-sample percentile interval."""

    finite = np.asarray(samples, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return np.nan, np.nan
    low, high = np.quantile(finite, [0.025, 0.975])
    return float(low), float(high)


def _stable_subseed(
    master_seed: int, definition_version: str, stable_key: str
) -> int:
    payload = (
        f"{int(master_seed)}|{definition_version}|{stable_key}".encode(
            "utf-8"
        )
    )
    return int.from_bytes(
        hashlib.sha256(payload).digest()[:8], "big", signed=False
    )


def _draw_crossed_multiplicities(
    *,
    seed_count: int,
    participant_count: int,
    repetitions: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Draw crossed seed and participant multiplicities for each replicate."""

    if seed_count <= 0 or participant_count <= 0 or repetitions <= 0:
        raise ValueError(
            "seed_count, participant_count, and repetitions must be positive"
        )
    seed_draws = rng.integers(
        0,
        seed_count,
        size=(repetitions, seed_count),
        dtype=np.int64,
    )
    participant_draws = rng.integers(
        0,
        participant_count,
        size=(repetitions, participant_count),
        dtype=np.int64,
    )
    return _multiplicities_from_crossed_occurrence_draws(
        seed_draws,
        participant_draws,
        seed_count=seed_count,
        participant_count=participant_count,
    )


def _multiplicities_from_crossed_occurrence_draws(
    seed_draws: np.ndarray,
    participant_draws: np.ndarray,
    *,
    seed_count: int,
    participant_count: int,
) -> np.ndarray:
    """Combine independent factor draws as crossed multiplicity products."""

    seed_draws = np.asarray(seed_draws, dtype=np.int64)
    participant_draws = np.asarray(
        participant_draws, dtype=np.int64
    )
    if seed_draws.ndim != 2:
        raise ValueError(
            "seed_draws must have [replicate,seed_occurrence] shape"
        )
    if participant_draws.ndim != 2:
        raise ValueError(
            "participant_draws must have "
            "[replicate,participant_occurrence] shape"
        )
    if participant_draws.shape[0] != seed_draws.shape[0]:
        raise ValueError(
            "seed and participant replicate dimensions do not match"
        )
    if participant_draws.shape[1] != participant_count:
        raise ValueError(
            "participant occurrence count must equal participant_count"
        )
    if (
        (seed_draws < 0).any()
        or (seed_draws >= seed_count).any()
        or (participant_draws < 0).any()
        or (participant_draws >= participant_count).any()
    ):
        raise ValueError("occurrence draw index is out of range")
    repetitions = seed_draws.shape[0]
    seed_multiplicities = np.zeros(
        (repetitions, seed_count), dtype=np.int64
    )
    participant_multiplicities = np.zeros(
        (repetitions, participant_count), dtype=np.int64
    )
    replicate_index = np.arange(repetitions, dtype=np.int64)
    for occurrence in range(seed_draws.shape[1]):
        np.add.at(
            seed_multiplicities,
            (replicate_index, seed_draws[:, occurrence]),
            1,
        )
    for occurrence in range(participant_draws.shape[1]):
        np.add.at(
            participant_multiplicities,
            (replicate_index, participant_draws[:, occurrence]),
            1,
        )
    return (
        seed_multiplicities[:, :, np.newaxis]
        * participant_multiplicities[:, np.newaxis, :]
    )


def _ratios_from_multiplicities(
    numerator: np.ndarray,
    denominator: np.ndarray,
    multiplicities: np.ndarray,
) -> np.ndarray:
    """Apply shared cluster weights to every metric in one stratum."""

    numerator = np.asarray(numerator, dtype=float)
    denominator = np.asarray(denominator, dtype=float)
    multiplicities = np.asarray(multiplicities, dtype=np.int64)
    if numerator.shape != denominator.shape or numerator.ndim != 3:
        raise ValueError(
            "numerator and denominator must share [seed,participant,metric] shape"
        )
    if multiplicities.ndim != 3:
        raise ValueError(
            "multiplicities must have [replicate,seed,participant] shape"
        )
    if multiplicities.shape[1:] != numerator.shape[:2]:
        raise ValueError(
            "multiplicity seed/participant dimensions do not match statistics"
        )
    weighted_numerator = np.einsum(
        "bsp,spm->bm", multiplicities, numerator, optimize=True
    )
    weighted_denominator = np.einsum(
        "bsp,spm->bm", multiplicities, denominator, optimize=True
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        values = weighted_numerator / weighted_denominator
    values[weighted_denominator == 0] = np.nan
    return values


def _validate_tables(tables: ExperimentTables) -> None:
    clean = tables.clean_decisions
    attack = tables.attack_decisions
    manifest = tables.injection_manifest
    clean_key = ["seed", "participant_id", "relative_day", "pipeline"]
    if clean.empty:
        raise ValueError("clean_decisions cannot be empty")
    if clean.duplicated(clean_key).any():
        raise ValueError("clean_decisions key is not unique")
    if set(clean["evaluation_arm"]) != {"clean_control"}:
        raise ValueError("clean_decisions contains a non-clean arm")
    if not attack.empty:
        if set(attack["evaluation_arm"]) != {"attack_target"}:
            raise ValueError("attack_decisions contains a non-attack arm")
        if not attack["injected"].astype(bool).all():
            raise ValueError("attack_decisions contains an uninjected row")
        if attack.duplicated(["pair_key", "pipeline"]).any():
            raise ValueError("attack decision pair/pipeline key is not unique")
        observed = attack.groupby("pair_key").size()
        pipeline_count = clean["pipeline"].nunique()
        if not (observed == pipeline_count).all():
            raise ValueError(
                "each manifest target must have one decision per pipeline"
            )
        if set(observed.index) != set(manifest["pair_key"]):
            raise ValueError("manifest and attack decisions do not reconcile")
        flags = attack[["attempted", "mutated", "evaluated"]].astype(bool)
        if not flags.all(axis=None):
            raise ValueError(
                "attempted, mutated, and evaluated must all be true"
            )
    boundary = tables.boundary_decisions
    boundary_manifest = tables.boundary_manifest
    if not boundary.empty:
        if set(boundary["scenario_class"]) != {
            "boundary_control"
        }:
            raise ValueError("boundary table contains an attack scenario")
        if boundary_manifest.empty:
            raise ValueError(
                "boundary manifest is empty for non-empty decisions"
            )
        if boundary_manifest["pair_key"].duplicated().any():
            raise ValueError("boundary manifest pair key is not unique")
        if boundary.duplicated(["pair_key", "pipeline"]).any():
            raise ValueError(
                "boundary decision pair/pipeline key is not unique"
            )
        observed = boundary.groupby("pair_key").size()
        pipeline_count = clean["pipeline"].nunique()
        if not (observed == pipeline_count).all():
            raise ValueError(
                "each boundary manifest target must have one decision "
                "per pipeline"
            )
        if set(observed.index) != set(boundary_manifest["pair_key"]):
            raise ValueError(
                "boundary manifest and decisions do not reconcile"
            )
        if not boundary[["attempted", "mutated"]].astype(bool).all(
            axis=None
        ):
            raise ValueError(
                "boundary attempted and mutated flags must be true"
            )
        evaluated = boundary["evaluated"].astype(bool)
        if boundary.loc[evaluated, "accepted"].isna().any():
            raise ValueError(
                "evaluated boundary decision cannot have a null outcome"
            )
        if boundary.loc[~evaluated, "accepted"].notna().any():
            raise ValueError(
                "unevaluated boundary decision must have a null outcome"
            )
    elif not boundary_manifest.empty:
        raise ValueError(
            "boundary manifest is non-empty for empty decisions"
        )


def _decision_components(
    frame: pd.DataFrame,
    *,
    clean_prefix: str = "",
) -> pd.DataFrame:
    accepted_column = f"{clean_prefix}accepted"
    covered_column = f"{clean_prefix}covered"
    output_column = f"{clean_prefix}output_priority"
    rejected = ~frame[accepted_column].astype(bool)
    covered = frame[covered_column].astype(bool)
    output = pd.to_numeric(frame[output_column], errors="coerce")
    clean_priority = pd.to_numeric(frame["clean_priority"], errors="raise")
    return pd.DataFrame(
        {
            "seed": frame["seed"].astype(int),
            "participant_id": frame["participant_id"].astype(str),
            "rejected": rejected.astype(np.int64),
            "total": np.ones(len(frame), dtype=np.int64),
            "covered": covered.astype(np.int64),
            "abstained": (~covered).astype(np.int64),
            "agreement": (
                covered & output.eq(clean_priority)
            ).astype(np.int64),
            "upward": (
                covered & output.gt(clean_priority)
            ).astype(np.int64),
            "priority_loss": (
                covered & output.lt(clean_priority)
            ).astype(np.int64),
        },
        index=frame.index,
    )


def _aggregate_components(
    components: pd.DataFrame,
    *,
    seeds: tuple[int, ...],
    participants: tuple[str, ...],
    value_columns: Iterable[str],
) -> dict[str, np.ndarray]:
    seed_index = {value: index for index, value in enumerate(seeds)}
    participant_index = {
        value: index for index, value in enumerate(participants)
    }
    value_columns = tuple(value_columns)
    result = {
        value: np.zeros((len(seeds), len(participants)), dtype=float)
        for value in value_columns
    }
    if components.empty:
        return result
    grouped = (
        components.groupby(["seed", "participant_id"], sort=True)[
            list(value_columns)
        ]
        .sum()
        .reset_index()
    )
    for row in grouped.itertuples(index=False):
        s = seed_index[int(row.seed)]
        p = participant_index[str(row.participant_id)]
        for value in value_columns:
            result[value][s, p] = float(getattr(row, value))
    return result


def _metadata(
    *,
    metric_id: str,
    scenario: object,
    scenario_class: str,
    rate: float,
    pipeline: str,
    evaluation_arm: str,
    denominator_unit: str,
    comparator_pipeline: object = None,
    comparison_type: object = None,
    expected_outcome: object = None,
    expected_first_stage: object = None,
    rate_realized: float | None = None,
    enabled_checks: object = None,
    comparator_enabled_checks: object = None,
    comparator_definition: object = None,
    simulation_evaluations: int = 0,
    attempted_N: int = 0,
    mutated_N: int = 0,
    evaluated_N: int = 0,
    unique_attacked_participant_days: object = None,
    mixed_metric_applicable: object = None,
    estimand: object = None,
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "scenario": scenario,
        "scenario_class": scenario_class,
        "rate_requested": float(rate),
        "rate_realized": (
            float(rate) if rate_realized is None else float(rate_realized)
        ),
        "pipeline": pipeline,
        "comparator_pipeline": comparator_pipeline,
        "comparison_type": comparison_type,
        "evaluation_arm": evaluation_arm,
        "denominator_unit": denominator_unit,
        "expected_outcome": expected_outcome,
        "expected_first_stage": expected_first_stage,
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "enabled_checks": enabled_checks,
        "comparator_enabled_checks": comparator_enabled_checks,
        "comparator_definition": comparator_definition,
        "simulation_evaluations": int(simulation_evaluations),
        "attempted_N": int(attempted_N),
        "mutated_N": int(mutated_N),
        "evaluated_N": int(evaluated_N),
        "unique_attacked_participant_days": (
            unique_attacked_participant_days
        ),
        "mixed_metric_applicable": mixed_metric_applicable,
        "estimand": estimand,
    }


def _append_metric(
    metadata: list[dict[str, Any]],
    numerators: list[np.ndarray],
    denominators: list[np.ndarray],
    *,
    meta: dict[str, Any],
    numerator: np.ndarray,
    denominator: np.ndarray,
) -> None:
    metadata.append(meta)
    numerators.append(np.asarray(numerator, dtype=float))
    denominators.append(np.asarray(denominator, dtype=float))


def _clean_cache(
    clean: pd.DataFrame,
    *,
    seeds: tuple[int, ...],
    participants: tuple[str, ...],
) -> dict[str, dict[str, np.ndarray]]:
    result: dict[str, dict[str, np.ndarray]] = {}
    for pipeline, group in clean.groupby("pipeline", sort=True):
        result[str(pipeline)] = _aggregate_components(
            _decision_components(group),
            seeds=seeds,
            participants=participants,
            value_columns=(
                "rejected",
                "total",
                "covered",
                "abstained",
                "agreement",
                "upward",
                "priority_loss",
            ),
        )
    return result


def _paired_cells(
    left_rejected: pd.Series, right_rejected: pd.Series
) -> dict[str, int]:
    left = left_rejected.astype(bool)
    right = right_rejected.astype(bool)
    return {
        "both_reject_n": int((left & right).sum()),
        "attack_only_reject_n": int((left & ~right).sum()),
        "clean_only_reject_n": int((~left & right).sum()),
        "neither_reject_n": int((~left & ~right).sum()),
    }


def _paired_row(
    *,
    contrast_id: str,
    contrast_type: str,
    scenario: str,
    rate: float,
    seed: int,
    pipeline: str,
    comparator: str,
    left_rejected: pd.Series,
    right_rejected: pd.Series,
) -> dict[str, Any]:
    cells = _paired_cells(left_rejected, right_rejected)
    denominator = sum(cells.values())
    numerator = (
        cells["attack_only_reject_n"]
        - cells["clean_only_reject_n"]
    )
    return {
        "contrast_id": contrast_id,
        "contrast_type": contrast_type,
        "scenario": scenario,
        "rate_requested": float(rate),
        "seed": int(seed),
        "pipeline": pipeline,
        "comparator_pipeline": comparator,
        **cells,
        "numerator_n": int(numerator),
        "denominator_N": int(denominator),
        "estimate": float(numerator / denominator),
        "metric_definition_version": METRIC_DEFINITION_VERSION,
    }


def _matching_ablation(expected_stage: str) -> str:
    if expected_stage == "history":
        return "all_minus_freshness"
    candidate = f"all_minus_{expected_stage}"
    return candidate if expected_stage != "none" else "unverified"


def _build_blocks(
    tables: ExperimentTables,
) -> tuple[
    list[_AnalysisBlock],
    pd.DataFrame,
    tuple[int, ...],
    tuple[str, ...],
    int,
]:
    clean = tables.clean_decisions
    attack = tables.attack_decisions
    boundary = tables.boundary_decisions
    attack_manifest = tables.injection_manifest
    seeds = tuple(sorted(int(value) for value in clean["seed"].unique()))
    participants = tuple(
        sorted(str(value) for value in clean["participant_id"].unique())
    )
    eligible_days = int(
        clean[["participant_id", "relative_day"]].drop_duplicates().shape[0]
    )
    clean_by_pipeline = _clean_cache(
        clean, seeds=seeds, participants=participants
    )
    blocks: list[_AnalysisBlock] = []
    paired_rows: list[dict[str, Any]] = []

    clean_meta: list[dict[str, Any]] = []
    clean_num: list[np.ndarray] = []
    clean_den: list[np.ndarray] = []
    for pipeline in sorted(clean_by_pipeline):
        values = clean_by_pipeline[pipeline]
        pipeline_frame = clean.loc[clean["pipeline"] == pipeline]
        simulation_evaluations = len(pipeline_frame)
        _append_metric(
            clean_meta,
            clean_num,
            clean_den,
            meta=_metadata(
                metric_id="clean_false_rejection",
                scenario=None,
                scenario_class="clean_control",
                rate=0.0,
                pipeline=pipeline,
                evaluation_arm="clean_control",
                denominator_unit="clean simulation evaluations",
                enabled_checks="|".join(PIPELINES[pipeline]),
                simulation_evaluations=simulation_evaluations,
                attempted_N=simulation_evaluations,
                mutated_N=0,
                evaluated_N=simulation_evaluations,
                mixed_metric_applicable=False,
                estimand="clean_eligible_day_evaluation",
            ),
            numerator=values["rejected"],
            denominator=values["total"],
        )
    blocks.append(
        _AnalysisBlock(
            stable_key="clean|0.000000",
            seeds=seeds,
            participants=participants,
            metadata=clean_meta,
            numerator=np.stack(clean_num, axis=2),
            denominator=np.stack(clean_den, axis=2),
        )
    )

    attack_keys = ["scenario", "rate_requested"]
    for (scenario, rate), stratum in attack.groupby(
        attack_keys, sort=True
    ):
        scenario = str(scenario)
        rate = float(rate)
        meta: list[dict[str, Any]] = []
        nums: list[np.ndarray] = []
        dens: list[np.ndarray] = []
        realized = float(stratum["rate_realized"].iloc[0])
        manifest_stratum = attack_manifest.loc[
            (attack_manifest["scenario"].astype(str) == scenario)
            & np.isclose(
                pd.to_numeric(
                    attack_manifest["rate_requested"],
                    errors="raise",
                ),
                rate,
            )
        ]
        history_operation = scenario in HISTORY_OPERATION_SCENARIOS
        if history_operation and "history_affected_key" in manifest_stratum:
            unique_attacked = int(
                manifest_stratum[
                    ["participant_id", "history_affected_key"]
                ]
                .drop_duplicates()
                .shape[0]
            )
        else:
            unique_attacked = int(
                manifest_stratum["record_key"].nunique()
            )
        mixed_metric_applicable = bool(
            stratum["mixed_metric_applicable"].astype(bool).all()
        )
        estimands = set(stratum["estimand"].astype(str))
        estimand = (
            next(iter(estimands))
            if len(estimands) == 1
            else "mixed_estimands"
        )
        for pipeline, pipeline_frame in stratum.groupby(
            "pipeline", sort=True
        ):
            pipeline = str(pipeline)
            attempted_n = int(
                pipeline_frame["attempted"].astype(bool).sum()
            )
            mutated_n = int(
                pipeline_frame["mutated"].astype(bool).sum()
            )
            evaluated_n = int(
                pipeline_frame["evaluated"].astype(bool).sum()
            )
            simulation_evaluations = len(pipeline_frame)
            attack_components = _aggregate_components(
                _decision_components(pipeline_frame),
                seeds=seeds,
                participants=participants,
                value_columns=(
                    "rejected",
                    "total",
                    "covered",
                    "abstained",
                    "agreement",
                    "upward",
                    "priority_loss",
                ),
            )
            expected_outcomes = set(
                pipeline_frame["expected_outcome"].astype(str)
            )
            expected_stages = set(
                pipeline_frame["expected_first_stage"].astype(str)
            )
            expected_outcome = (
                next(iter(expected_outcomes))
                if len(expected_outcomes) == 1
                else "mixed"
            )
            expected_stage = (
                next(iter(expected_stages))
                if len(expected_stages) == 1
                else "mixed"
            )
            base_meta = dict(
                scenario=scenario,
                scenario_class="attack",
                rate=rate,
                pipeline=pipeline,
                rate_realized=realized,
                expected_outcome=expected_outcome,
                expected_first_stage=expected_stage,
                enabled_checks="|".join(PIPELINES[pipeline]),
                simulation_evaluations=simulation_evaluations,
                attempted_N=attempted_n,
                mutated_N=mutated_n,
                evaluated_N=evaluated_n,
                unique_attacked_participant_days=unique_attacked,
                mixed_metric_applicable=mixed_metric_applicable,
                estimand=estimand,
            )
            _append_metric(
                meta,
                nums,
                dens,
                meta=_metadata(
                    metric_id="attack_rejection",
                    evaluation_arm="attack_target",
                    denominator_unit="attacked simulation evaluations",
                    **base_meta,
                ),
                numerator=attack_components["rejected"],
                denominator=attack_components["total"],
            )

            stage_frame = pd.DataFrame(
                {
                    "seed": pipeline_frame["seed"].astype(int),
                    "participant_id": pipeline_frame[
                        "participant_id"
                    ].astype(str),
                    "stage_hit": pipeline_frame["stage_hit"]
                    .astype(bool)
                    .astype(int),
                    "stage_applicable": pipeline_frame[
                        "stage_applicable"
                    ]
                    .astype(bool)
                    .astype(int),
                    "rejected": (
                        ~pipeline_frame["accepted"].astype(bool)
                    ).astype(int),
                }
            )
            stage = _aggregate_components(
                stage_frame,
                seeds=seeds,
                participants=participants,
                value_columns=(
                    "stage_hit",
                    "stage_applicable",
                    "rejected",
                ),
            )
            _append_metric(
                meta,
                nums,
                dens,
                meta=_metadata(
                    metric_id="expected_stage_agreement",
                    evaluation_arm="attack_target",
                    denominator_unit=(
                        "stage-applicable attacked simulation evaluations"
                    ),
                    **base_meta,
                ),
                numerator=stage["stage_hit"],
                denominator=stage["stage_applicable"],
            )
            _append_metric(
                meta,
                nums,
                dens,
                meta=_metadata(
                    metric_id="conditional_stage_attribution",
                    evaluation_arm="attack_target",
                    denominator_unit="rejected attacked simulation evaluations",
                    **base_meta,
                ),
                numerator=stage["stage_hit"],
                denominator=stage["rejected"],
            )

            if not mixed_metric_applicable:
                continue
            target_clean = _aggregate_components(
                _decision_components(
                    pipeline_frame, clean_prefix="clean_"
                ),
                seeds=seeds,
                participants=participants,
                value_columns=(
                    "rejected",
                    "total",
                    "covered",
                    "abstained",
                    "agreement",
                    "upward",
                    "priority_loss",
                ),
            )
            clean_all = clean_by_pipeline[pipeline]
            mixed = {
                value: (
                    clean_all[value]
                    - target_clean[value]
                    + attack_components[value]
                )
                for value in clean_all
            }
            mixed_simulation_evaluations = eligible_days * len(seeds)
            for metric_id, numerator_name, denominator_name, unit in (
                (
                    "coverage",
                    "covered",
                    "total",
                    "eligible mixed simulation evaluations",
                ),
                (
                    "abstention",
                    "abstained",
                    "total",
                    "eligible mixed simulation evaluations",
                ),
                (
                    "covered_agreement",
                    "agreement",
                    "covered",
                    "covered mixed simulation evaluations",
                ),
                (
                    "upward_discordance",
                    "upward",
                    "total",
                    "eligible mixed simulation evaluations",
                ),
                (
                    "priority_loss_discordance",
                    "priority_loss",
                    "total",
                    "eligible mixed simulation evaluations",
                ),
            ):
                mixed_meta = dict(base_meta)
                mixed_meta["simulation_evaluations"] = (
                    mixed_simulation_evaluations
                )
                _append_metric(
                    meta,
                    nums,
                    dens,
                    meta=_metadata(
                        metric_id=metric_id,
                        evaluation_arm="mixed_eligible_population",
                        denominator_unit=unit,
                        **mixed_meta,
                    ),
                    numerator=mixed[numerator_name],
                    denominator=mixed[denominator_name],
                )

            attack_clean_difference = (
                attack_components["rejected"]
                - target_clean["rejected"]
            )
            _append_metric(
                meta,
                nums,
                dens,
                meta=_metadata(
                    metric_id="attack_clean_risk_difference",
                    evaluation_arm="paired_attack_clean",
                    denominator_unit="paired target evaluations",
                    comparator_pipeline=f"clean:{pipeline}",
                    comparison_type="attack_minus_clean",
                    **base_meta,
                ),
                numerator=attack_clean_difference,
                denominator=attack_components["total"],
            )
            for seed_value, seed_frame in pipeline_frame.groupby(
                "seed", sort=True
            ):
                paired_rows.append(
                    _paired_row(
                        contrast_id=f"attack-clean|{pipeline}",
                        contrast_type="attack_minus_clean",
                        scenario=scenario,
                        rate=rate,
                        seed=int(seed_value),
                        pipeline=pipeline,
                        comparator=f"clean:{pipeline}",
                        left_rejected=~seed_frame["accepted"].astype(bool),
                        right_rejected=~seed_frame[
                            "clean_accepted"
                        ].astype(bool),
                    )
                )

        rejected_wide = (
            stratum.assign(
                rejected=~stratum["accepted"].astype(bool)
            )
            .pivot(
                index=[
                    "pair_key",
                    "seed",
                    "participant_id",
                    "relative_day",
                ],
                columns="pipeline",
                values="rejected",
            )
            .reset_index()
        )
        primary_stage = str(stratum["expected_primary_stage"].iloc[0])
        comparators = [
            ("matched_pipeline", _matching_ablation(primary_stage)),
            ("baseline", "unverified"),
        ]
        seen: set[str] = set()
        for comparison_type, comparator in comparators:
            if comparator in seen or comparator not in rejected_wide.columns:
                continue
            seen.add(comparator)
            pair_frame = pd.DataFrame(
                {
                    "seed": rejected_wide["seed"].astype(int),
                    "participant_id": rejected_wide[
                        "participant_id"
                    ].astype(str),
                    "difference": (
                        rejected_wide["all_checks"].astype(int)
                        - rejected_wide[comparator].astype(int)
                    ),
                    "total": np.ones(len(rejected_wide), dtype=int),
                }
            )
            pair_values = _aggregate_components(
                pair_frame,
                seeds=seeds,
                participants=participants,
                value_columns=("difference", "total"),
            )
            _append_metric(
                meta,
                nums,
                dens,
                meta=_metadata(
                    metric_id="pipeline_risk_difference",
                    scenario=scenario,
                    scenario_class="attack",
                    rate=rate,
                    rate_realized=realized,
                    pipeline="all_checks",
                    comparator_pipeline=comparator,
                    comparison_type=comparison_type,
                    evaluation_arm="paired_attack_pipelines",
                    denominator_unit="paired attacked evaluations",
                    expected_outcome="reject",
                    expected_first_stage=primary_stage,
                    enabled_checks="|".join(
                        PIPELINES["all_checks"]
                    ),
                    comparator_enabled_checks="|".join(
                        PIPELINES[comparator]
                    ),
                    comparator_definition=(
                        "predefined_history_without_freshness"
                        if primary_stage == "history"
                        and comparison_type == "matched_pipeline"
                        else (
                            f"predefined_without_{primary_stage}"
                            if comparison_type == "matched_pipeline"
                            else "unverified_baseline"
                        )
                    ),
                    simulation_evaluations=len(rejected_wide),
                    attempted_N=len(rejected_wide),
                    mutated_N=len(rejected_wide),
                    evaluated_N=len(rejected_wide),
                    unique_attacked_participant_days=unique_attacked,
                    mixed_metric_applicable=mixed_metric_applicable,
                    estimand=estimand,
                ),
                numerator=pair_values["difference"],
                denominator=pair_values["total"],
            )
            for seed_value, seed_frame in rejected_wide.groupby(
                "seed", sort=True
            ):
                paired_rows.append(
                    _paired_row(
                        contrast_id=(
                            f"pipeline-{comparison_type}|"
                            f"all_checks|{comparator}"
                        ),
                        contrast_type=comparison_type,
                        scenario=scenario,
                        rate=rate,
                        seed=int(seed_value),
                        pipeline="all_checks",
                        comparator=comparator,
                        left_rejected=seed_frame[
                            "all_checks"
                        ].astype(bool),
                        right_rejected=seed_frame[comparator].astype(bool),
                    )
                )

        blocks.append(
            _AnalysisBlock(
                stable_key=f"{scenario}|{rate:.6f}",
                seeds=seeds,
                participants=participants,
                metadata=meta,
                numerator=np.stack(nums, axis=2),
                denominator=np.stack(dens, axis=2),
            )
        )

    if not boundary.empty:
        for (scenario, rate), stratum in boundary.groupby(
            ["scenario", "rate_requested"], sort=True
        ):
            meta = []
            nums = []
            dens = []
            realized = float(stratum["rate_realized"].iloc[0])
            for pipeline, pipeline_frame in stratum.groupby(
                "pipeline", sort=True
            ):
                evaluated_frame = pipeline_frame.loc[
                    pipeline_frame["evaluated"].astype(bool)
                ]
                attempted_n = int(
                    pipeline_frame["attempted"].astype(bool).sum()
                )
                mutated_n = int(
                    pipeline_frame["mutated"].astype(bool).sum()
                )
                evaluated_n = int(
                    pipeline_frame["evaluated"].astype(bool).sum()
                )
                execution_meta = {
                    "enabled_checks": "|".join(
                        PIPELINES[str(pipeline)]
                    ),
                    "simulation_evaluations": len(pipeline_frame),
                    "attempted_N": attempted_n,
                    "mutated_N": mutated_n,
                    "evaluated_N": evaluated_n,
                    "mixed_metric_applicable": False,
                    "estimand": "boundary_control_operation",
                }
                values = _aggregate_components(
                    _decision_components(evaluated_frame),
                    seeds=seeds,
                    participants=participants,
                    value_columns=("rejected", "total"),
                )
                for metric_id, numerator in (
                    ("control_rejection", values["rejected"]),
                    (
                        "control_acceptance",
                        values["total"] - values["rejected"],
                    ),
                ):
                    _append_metric(
                        meta,
                        nums,
                        dens,
                        meta=_metadata(
                            metric_id=metric_id,
                            scenario=str(scenario),
                            scenario_class="boundary_control",
                            rate=float(rate),
                            rate_realized=realized,
                            pipeline=str(pipeline),
                            evaluation_arm="boundary_control",
                            denominator_unit="boundary-control evaluations",
                            expected_outcome=str(
                                pipeline_frame[
                                    "expected_outcome"
                                ].iloc[0]
                            ),
                            expected_first_stage=None,
                            **execution_meta,
                        ),
                        numerator=numerator,
                        denominator=values["total"],
                    )
                no_decision_selection = pipeline_frame.loc[
                    ~pipeline_frame["evaluated"].astype(bool)
                ]
                no_decision_frame = pd.DataFrame(
                    {
                        "seed": no_decision_selection["seed"].astype(
                            int
                        ),
                        "participant_id": no_decision_selection[
                            "participant_id"
                        ].astype(str),
                        "no_decision": np.ones(
                            len(no_decision_selection), dtype=int
                        ),
                        "total": np.ones(
                            len(no_decision_selection), dtype=int
                        ),
                    }
                )
                no_decision = _aggregate_components(
                    no_decision_frame,
                    seeds=seeds,
                    participants=participants,
                    value_columns=("no_decision", "total"),
                )
                _append_metric(
                    meta,
                    nums,
                    dens,
                    meta=_metadata(
                        metric_id="control_no_decision",
                        scenario=str(scenario),
                        scenario_class="boundary_control",
                        rate=float(rate),
                        rate_realized=realized,
                        pipeline=str(pipeline),
                        evaluation_arm="boundary_control",
                        denominator_unit="targeted boundary controls",
                        expected_outcome=str(
                            pipeline_frame["expected_outcome"].iloc[0]
                        ),
                        expected_first_stage=None,
                        **execution_meta,
                    ),
                    numerator=no_decision["no_decision"],
                    denominator=(
                        values["total"] + no_decision["total"]
                    ),
                )
            blocks.append(
                _AnalysisBlock(
                    stable_key=f"boundary|{scenario}|{float(rate):.6f}",
                    seeds=seeds,
                    participants=participants,
                    metadata=meta,
                    numerator=np.stack(nums, axis=2),
                    denominator=np.stack(dens, axis=2),
                )
            )

    paired = pd.DataFrame(paired_rows)
    return blocks, paired, seeds, participants, eligible_days


def _rate_key(value: object) -> float:
    return round(float(value), 12)


def _per_seed_execution_counts(
    tables: ExperimentTables,
) -> dict[str, dict[tuple[object, ...], dict[str, object]]]:
    """Index actual decision and manifest counts at one-seed grain."""

    clean_counts: dict[tuple[object, ...], dict[str, object]] = {}
    for (pipeline, seed), group in tables.clean_decisions.groupby(
        ["pipeline", "seed"], sort=False
    ):
        row_count = int(len(group))
        clean_counts[(str(pipeline), int(seed))] = {
            "simulation_evaluations": row_count,
            "attempted_N": row_count,
            "mutated_N": 0,
            "evaluated_N": row_count,
            "unique_attacked_participant_days": None,
        }

    def decision_counts(
        frame: pd.DataFrame,
    ) -> dict[tuple[object, ...], dict[str, object]]:
        result: dict[tuple[object, ...], dict[str, object]] = {}
        for key, group in frame.groupby(
            ["scenario", "rate_requested", "pipeline", "seed"],
            sort=False,
        ):
            scenario, rate, pipeline, seed = key
            result[
                (
                    str(scenario),
                    _rate_key(rate),
                    str(pipeline),
                    int(seed),
                )
            ] = {
                "simulation_evaluations": int(len(group)),
                "attempted_N": int(
                    group["attempted"].astype(bool).sum()
                ),
                "mutated_N": int(
                    group["mutated"].astype(bool).sum()
                ),
                "evaluated_N": int(
                    group["evaluated"].astype(bool).sum()
                ),
                "unique_attacked_participant_days": None,
            }
        return result

    attack_counts = decision_counts(tables.attack_decisions)
    boundary_counts = decision_counts(tables.boundary_decisions)
    unique_attack: dict[tuple[object, ...], int] = {}
    for key, group in tables.injection_manifest.groupby(
        ["scenario", "rate_requested", "seed"], sort=False
    ):
        scenario, rate, seed = key
        if str(scenario) in HISTORY_OPERATION_SCENARIOS:
            unique_count = int(
                group[
                    ["participant_id", "history_affected_key"]
                ]
                .drop_duplicates()
                .shape[0]
            )
        else:
            unique_count = int(group["record_key"].nunique())
        unique_attack[
            (str(scenario), _rate_key(rate), int(seed))
        ] = unique_count
    for key, counts in attack_counts.items():
        scenario, rate, _, seed = key
        counts["unique_attacked_participant_days"] = unique_attack[
            (scenario, rate, seed)
        ]
    return {
        "clean": clean_counts,
        "attack": attack_counts,
        "boundary_control": boundary_counts,
    }


def _per_seed_rows(
    blocks: list[_AnalysisBlock],
    tables: ExperimentTables,
) -> pd.DataFrame:
    execution = _per_seed_execution_counts(tables)
    rows: list[dict[str, Any]] = []
    for block in blocks:
        for metric_index, meta in enumerate(block.metadata):
            numerator = block.numerator[:, :, metric_index].sum(axis=1)
            denominator = block.denominator[:, :, metric_index].sum(axis=1)
            for seed_index, seed in enumerate(block.seeds):
                den = float(denominator[seed_index])
                num = float(numerator[seed_index])
                scenario_class = str(meta["scenario_class"])
                if scenario_class == "clean_control":
                    counts = dict(
                        execution["clean"][
                            (str(meta["pipeline"]), int(seed))
                        ]
                    )
                else:
                    key = (
                        str(meta["scenario"]),
                        _rate_key(meta["rate_requested"]),
                        str(meta["pipeline"]),
                        int(seed),
                    )
                    counts = dict(execution[scenario_class][key])
                    if (
                        meta["evaluation_arm"]
                        == "mixed_eligible_population"
                    ):
                        counts["simulation_evaluations"] = execution[
                            "clean"
                        ][(str(meta["pipeline"]), int(seed))][
                            "simulation_evaluations"
                        ]
                rows.append(
                    {
                        **meta,
                        **counts,
                        "seed": int(seed),
                        "seed_scope": "single_injection_seed",
                        "execution_count_scope": (
                            "single_injection_seed"
                        ),
                        "numerator_n": num,
                        "denominator_N": den,
                        "estimate": num / den if den else np.nan,
                        "participant_clusters": len(block.participants),
                    }
                )
    result = pd.DataFrame(rows)
    for column in ("numerator_n", "denominator_N"):
        integer_like = (
            pd.to_numeric(result[column], errors="coerce")
            .dropna()
            .mod(1)
            .eq(0)
            .all()
        )
        if integer_like:
            result[column] = result[column].astype("int64")
    return result


def _bootstrap_block_samples(
    block: _AnalysisBlock,
    *,
    repetitions: int,
    master_seed: int,
) -> np.ndarray:
    """Apply one deterministic crossed weight tensor to every block metric."""

    rng = np.random.default_rng(
        _stable_subseed(
            master_seed,
            METRIC_DEFINITION_VERSION,
            block.stable_key,
        )
    )
    multiplicities = _draw_crossed_multiplicities(
        seed_count=len(block.seeds),
        participant_count=len(block.participants),
        repetitions=repetitions,
        rng=rng,
    )
    return _ratios_from_multiplicities(
        block.numerator, block.denominator, multiplicities
    )


def _summary_rows(
    blocks: list[_AnalysisBlock],
    *,
    repetitions: int,
    master_seed: int,
    run_id: str,
    eligible_days: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        samples = _bootstrap_block_samples(
            block,
            repetitions=repetitions,
            master_seed=master_seed,
        )
        pooled_num = block.numerator.sum(axis=(0, 1))
        pooled_den = block.denominator.sum(axis=(0, 1))
        for metric_index, meta in enumerate(block.metadata):
            valid = np.isfinite(samples[:, metric_index])
            valid_count = int(valid.sum())
            discarded = int(repetitions - valid_count)
            low, high = percentile_interval(samples[:, metric_index])
            denominator = float(pooled_den[metric_index])
            numerator = float(pooled_num[metric_index])
            rows.append(
                {
                    **meta,
                    "run_id": run_id,
                    "seed_scope": "pooled_fixed_seed_set",
                    "execution_count_scope": (
                        "pooled_fixed_seed_set"
                    ),
                    "numerator_n": numerator,
                    "denominator_N": denominator,
                    "estimate": (
                        numerator / denominator
                        if denominator
                        else np.nan
                    ),
                    "ci_low": low,
                    "ci_high": high,
                    "seed_count": len(block.seeds),
                    "participant_clusters": len(block.participants),
                    "unique_participants": len(block.participants),
                    "unique_eligible_participant_days": eligible_days,
                    "bootstrap_master_seed": int(master_seed),
                    "bootstrap_method": BOOTSTRAP_METHOD,
                    "bootstrap_interval_type": (
                        BOOTSTRAP_INTERVAL_TYPE
                    ),
                    "bootstrap_repetitions_requested": int(repetitions),
                    "bootstrap_repetitions_valid": valid_count,
                    "bootstrap_repetitions_discarded": discarded,
                }
            )
    result = pd.DataFrame(rows)
    for column in (
        "numerator_n",
        "denominator_N",
        "simulation_evaluations",
    ):
        values = pd.to_numeric(result[column], errors="coerce")
        if values.dropna().mod(1).eq(0).all():
            result[column] = values.astype("int64")
    return result


def _attack_stage_matrix(attack: pd.DataFrame) -> pd.DataFrame:
    if attack.empty:
        return pd.DataFrame(
            columns=[
                "scenario",
                "rate_requested",
                "pipeline",
                "expected_first_stage",
                "failure_stage",
                "accepted",
                "numerator_n",
                "denominator_N",
                "proportion",
            ]
        )
    keys = [
        "scenario",
        "rate_requested",
        "pipeline",
        "expected_outcome",
        "expected_first_stage",
        "stage_applicable",
        "failure_stage",
        "accepted",
    ]
    matrix = (
        attack.groupby(keys, dropna=False, sort=True)
        .size()
        .rename("numerator_n")
        .reset_index()
    )
    totals = (
        attack.groupby(
            ["scenario", "rate_requested", "pipeline"], sort=True
        )
        .size()
        .rename("denominator_N")
        .reset_index()
    )
    matrix = matrix.merge(
        totals,
        on=["scenario", "rate_requested", "pipeline"],
        validate="many_to_one",
    )
    matrix["proportion"] = (
        matrix["numerator_n"] / matrix["denominator_N"]
    )
    matrix["metric_definition_version"] = METRIC_DEFINITION_VERSION
    return matrix


def analyze_experiment(
    tables: ExperimentTables,
    *,
    repetitions: int = 2_000,
    master_seed: int = 20260722,
    run_id: str = "aamos-standard-enhanced",
) -> AnalysisOutputs:
    """Compute all denominators, paired effects, and two-stage intervals."""

    if repetitions <= 0:
        raise ValueError("bootstrap repetitions must be positive")
    _validate_tables(tables)
    blocks, paired, _, _, eligible_days = _build_blocks(tables)
    per_seed = _per_seed_rows(blocks, tables)
    summary = _summary_rows(
        blocks,
        repetitions=repetitions,
        master_seed=master_seed,
        run_id=run_id,
        eligible_days=eligible_days,
    )
    return AnalysisOutputs(
        per_seed_metrics=per_seed,
        summary=summary,
        attack_stage_matrix=_attack_stage_matrix(
            tables.attack_decisions
        ),
        paired_contrasts=paired,
    )


def summarize_with_two_stage_ci(
    decisions: pd.DataFrame,
    *,
    repetitions: int = 2_000,
    seed: int = 20260722,
) -> pd.DataFrame:
    """Retired compatibility guard for the old attacked-only interface."""

    del decisions, repetitions, seed
    raise ValueError(
        "attacked-only summary is retired; call analyze_experiment with "
        "clean, manifest, attack, and boundary tables"
    )


def _pooled_pair_cells(paired: pd.DataFrame) -> pd.DataFrame:
    if paired.empty:
        return paired
    keys = [
        "contrast_type",
        "scenario",
        "rate_requested",
        "pipeline",
        "comparator_pipeline",
    ]
    columns = [
        "both_reject_n",
        "attack_only_reject_n",
        "clean_only_reject_n",
        "neither_reject_n",
    ]
    result = (
        paired.groupby(keys, dropna=False, as_index=False)[columns]
        .sum()
        .reset_index(drop=True)
    )
    return result.rename(columns={"contrast_type": "comparison_type"})


def build_figure_source_data(
    summary: pd.DataFrame,
    paired_contrasts: pd.DataFrame,
    *,
    run_id: str,
    created_utc: str,
    code_commit_or_archive_hash: str,
    bootstrap_master_seed: int,
    derivation_config_identity: Mapping[str, object],
    dataset_identity: Mapping[str, object],
) -> pd.DataFrame:
    """Select and fully annotate the exact marks used by the 2×2 figure."""

    attack_rates = sorted(
        float(value)
        for value in summary.loc[
            summary["metric_id"] == "attack_rejection",
            "rate_requested",
        ].dropna().unique()
    )
    if not attack_rates:
        raise ValueError("figure source requires attack_rejection rows")
    primary_rate = (
        0.10 if any(np.isclose(attack_rates, 0.10)) else attack_rates[0]
    )
    control_rates = sorted(
        float(value)
        for value in summary.loc[
            summary["metric_id"] == "control_rejection",
            "rate_requested",
        ].dropna().unique()
    )
    primary_control_rate = (
        0.10
        if any(np.isclose(control_rates, 0.10))
        else (control_rates[0] if control_rates else np.nan)
    )
    panel_a = summary.loc[
        (
            (summary["metric_id"] == "attack_rejection")
            & np.isclose(summary["rate_requested"], primary_rate)
        )
        | (summary["metric_id"] == "clean_false_rejection")
        | (
            (summary["metric_id"] == "control_rejection")
            & (summary["pipeline"] == "all_checks")
            & np.isclose(
                summary["rate_requested"], primary_control_rate
            )
        )
    ].copy()
    panel_a["panel_id"] = "a"

    panel_b = summary.loc[
        (
            (summary["metric_id"] == "expected_stage_agreement")
            & (summary["pipeline"] == "all_checks")
            & np.isclose(summary["rate_requested"], primary_rate)
        )
        | (
            (summary["metric_id"] == "pipeline_risk_difference")
            & (summary["comparison_type"] == "matched_pipeline")
            & np.isclose(summary["rate_requested"], primary_rate)
        )
    ].copy()
    panel_b["panel_id"] = "b"

    available_scenarios = list(
        summary.loc[
            summary["metric_id"] == "coverage", "scenario"
        ].dropna().unique()
    )
    display_scenario = (
        "mixed_attack"
        if "mixed_attack" in available_scenarios
        else str(sorted(available_scenarios)[0])
    )
    panel_c = summary.loc[
        (summary["scenario"] == display_scenario)
        & (summary["pipeline"] == "all_checks")
        & summary["metric_id"].isin({"coverage", "abstention"})
    ].copy()
    panel_c["panel_id"] = "c"
    panel_d = summary.loc[
        (summary["scenario"] == display_scenario)
        & (summary["pipeline"] == "all_checks")
        & summary["metric_id"].isin(
            {
                "covered_agreement",
                "upward_discordance",
                "priority_loss_discordance",
            }
        )
    ].copy()
    panel_d["panel_id"] = "d"

    source = pd.concat(
        [panel_a, panel_b, panel_c, panel_d],
        ignore_index=True,
        sort=False,
    )
    source["display_order"] = np.arange(len(source), dtype=int)
    source["created_utc"] = created_utc
    source["provenance"] = "public_secondary"
    for name in (
        "dataset_name",
        "dataset_doi",
        "dataset_source_inventory_sha256",
    ):
        source[name] = dataset_identity[name]
    for name in (
        "derivation_config_basename",
        "derivation_config_file_sha256",
        "derivation_config_canonical_sha256",
    ):
        source[name] = derivation_config_identity[name]
    source["derivation_version"] = "aamos00-derivation-v1"
    source["code_commit_or_archive_hash"] = code_commit_or_archive_hash
    source["bootstrap_master_seed"] = int(bootstrap_master_seed)
    source["run_id"] = run_id

    cells = _pooled_pair_cells(paired_contrasts)
    if not cells.empty:
        source = source.merge(
            cells,
            on=[
                "comparison_type",
                "scenario",
                "rate_requested",
                "pipeline",
                "comparator_pipeline",
            ],
            how="left",
            validate="many_to_one",
        )
    for column in (
        "both_reject_n",
        "attack_only_reject_n",
        "clean_only_reject_n",
        "neither_reject_n",
    ):
        if column not in source:
            source[column] = np.nan

    conditional_aliases = {
        "attack_rejection": {
            "attack_rejected_n": "numerator_n",
            "attacked_N": "denominator_N",
        },
        "clean_false_rejection": {
            "clean_rejected_n": "numerator_n",
            "clean_N": "denominator_N",
        },
        "expected_stage_agreement": {
            "stage_match_n": "numerator_n",
            "stage_applicable_N": "denominator_N",
        },
        "coverage": {
            "covered_n": "numerator_n",
            "eligible_N": "denominator_N",
        },
        "covered_agreement": {
            "covered_agreement_n": "numerator_n",
            "covered_N": "denominator_N",
        },
        "upward_discordance": {
            "upward_n": "numerator_n",
            "eligible_N": "denominator_N",
        },
        "priority_loss_discordance": {
            "priority_loss_n": "numerator_n",
            "eligible_N": "denominator_N",
        },
        "pipeline_risk_difference": {
            "paired_N": "denominator_N",
            "risk_difference": "estimate",
            "risk_difference_ci_low": "ci_low",
            "risk_difference_ci_high": "ci_high",
        },
    }
    alias_columns = sorted(
        {
            destination
            for mapping in conditional_aliases.values()
            for destination in mapping
        }
    )
    for column in alias_columns:
        source[column] = np.nan
    for metric_id, mapping in conditional_aliases.items():
        selection = source["metric_id"] == metric_id
        for destination, origin in mapping.items():
            source.loc[selection, destination] = source.loc[
                selection, origin
            ]
    required = [
        "run_id",
        "created_utc",
        "provenance",
        "dataset_name",
        "dataset_doi",
        "dataset_source_inventory_sha256",
        "derivation_version",
        "derivation_config_basename",
        "derivation_config_file_sha256",
        "derivation_config_canonical_sha256",
        "metric_definition_version",
        "code_commit_or_archive_hash",
        "scenario",
        "scenario_class",
        "expected_outcome",
        "expected_first_stage",
        "rate_requested",
        "rate_realized",
        "pipeline",
        "comparator_pipeline",
        "comparison_type",
        "enabled_checks",
        "comparator_enabled_checks",
        "comparator_definition",
        "seed_count",
        "seed_scope",
        "execution_count_scope",
        "bootstrap_master_seed",
        "bootstrap_method",
        "bootstrap_interval_type",
        "bootstrap_repetitions_requested",
        "bootstrap_repetitions_valid",
        "bootstrap_repetitions_discarded",
        "unique_participants",
        "unique_eligible_participant_days",
        "unique_attacked_participant_days",
        "simulation_evaluations",
        "attempted_N",
        "mutated_N",
        "evaluated_N",
        "mixed_metric_applicable",
        "estimand",
        "evaluation_arm",
        "metric_id",
        "denominator_unit",
        "numerator_n",
        "denominator_N",
        "estimate",
        "ci_low",
        "ci_high",
        "panel_id",
        "display_order",
        "attack_rejected_n",
        "attacked_N",
        "clean_rejected_n",
        "clean_N",
        "stage_match_n",
        "stage_applicable_N",
        "covered_n",
        "eligible_N",
        "covered_agreement_n",
        "covered_N",
        "upward_n",
        "priority_loss_n",
        "paired_N",
        "both_reject_n",
        "attack_only_reject_n",
        "clean_only_reject_n",
        "neither_reject_n",
        "risk_difference",
        "risk_difference_ci_low",
        "risk_difference_ci_high",
    ]
    for column in required:
        if column not in source:
            source[column] = np.nan
    return source[required].sort_values(
        ["panel_id", "display_order"], kind="stable"
    ).reset_index(drop=True)
