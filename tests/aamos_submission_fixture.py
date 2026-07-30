"""Shared fixed-design AAMOS submission fixture for evidence-gate tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from tarms_experiments.aamos_experiment import (
    ATTACK_RATES,
    FIXED_SEEDS,
    METRIC_DEFINITION_VERSION,
    PIPELINES,
)
from tarms_experiments.aamos_scenarios import (
    BOUNDARY_SCENARIOS,
    REJECT_SCENARIOS,
)
from tarms_experiments.aamos_source import (
    FIXED_DERIVATION_CONFIG_CANONICAL_SHA256,
    FIXED_DERIVATION_CONFIG_FILE_SHA256,
    OFFICIAL_AAMOS_RELEASE,
    source_inventory_sha256,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def submission_contract(
    root: Path,
) -> tuple[Path, Path, pd.DataFrame, dict[str, object]]:
    """Write a minimal, internally consistent fixed submission design."""

    source_path = (
        root / "fig_aamos_protocol_integrity_source_data.csv"
    )
    pipelines = {
        name: list(checks) for name, checks in PIPELINES.items()
    }
    attack_scenarios = list(REJECT_SCENARIOS)
    boundary_scenarios = list(BOUNDARY_SCENARIOS)
    source_inventory = [
        {"name": name, "sha256": digest}
        for name, digest in sorted(
            OFFICIAL_AAMOS_RELEASE[
                "selected_analysis_source_sha256"
            ].items()
        )
    ]
    inventory_hash = source_inventory_sha256(source_inventory)
    base = {
        "scenario_class": "attack",
        "rate_requested": 0.10,
        "pipeline": "all_checks",
        "comparator_pipeline": pd.NA,
        "comparison_type": pd.NA,
        "numerator_n": 8,
        "denominator_N": 10,
        "estimate": 0.8,
        "ci_low": 0.6,
        "ci_high": 1.0,
        "provenance": "public_secondary",
        "run_id": "submission-run",
        "code_commit_or_archive_hash": "c" * 64,
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "bootstrap_master_seed": 20260722,
        "bootstrap_method": (
            "crossed_seed_participant_multinomial"
        ),
        "bootstrap_interval_type": "percentile_95",
        "bootstrap_repetitions_requested": 2_000,
        "bootstrap_repetitions_valid": 2_000,
        "bootstrap_repetitions_discarded": 0,
        "seed_count": len(FIXED_SEEDS),
        "seed_scope": "pooled_fixed_seed_set",
        "execution_count_scope": "pooled_fixed_seed_set",
        "derivation_config_basename": "aamos00_derivation.yaml",
        "derivation_config_file_sha256": (
            FIXED_DERIVATION_CONFIG_FILE_SHA256
        ),
        "derivation_config_canonical_sha256": (
            FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
        ),
        "dataset_name": "AAMOS-00",
        "dataset_doi": "10.7488/ds/3775",
        "dataset_source_inventory_sha256": inventory_hash,
        "evaluation_arm": "attack_target",
        "denominator_unit": "attacked simulation evaluations",
    }
    rows: list[dict[str, object]] = []

    for scenario in attack_scenarios:
        for pipeline in pipelines:
            rows.append(
                {
                    **base,
                    "panel_id": "a",
                    "metric_id": "attack_rejection",
                    "scenario": scenario,
                    "pipeline": pipeline,
                }
            )
    for scenario in boundary_scenarios:
        rows.append(
            {
                **base,
                "panel_id": "a",
                "metric_id": "control_rejection",
                "scenario": scenario,
                "scenario_class": "boundary_control",
                "pipeline": "all_checks",
                "evaluation_arm": "boundary_control",
                "denominator_unit": (
                    "boundary-control evaluations"
                ),
            }
        )
    for pipeline in pipelines:
        rows.append(
            {
                **base,
                "panel_id": "a",
                "metric_id": "clean_false_rejection",
                "scenario": pd.NA,
                "scenario_class": "clean_control",
                "rate_requested": 0.0,
                "pipeline": pipeline,
                "numerator_n": 0,
                "estimate": 0.0,
                "ci_low": 0.0,
                "evaluation_arm": "clean_control",
                "denominator_unit": (
                    "clean simulation evaluations"
                ),
            }
        )
    for scenario in attack_scenarios:
        comparator = (
            "all_minus_freshness"
            if REJECT_SCENARIOS[scenario] == "history"
            else f"all_minus_{REJECT_SCENARIOS[scenario]}"
        )
        rows.extend(
            [
                {
                    **base,
                    "panel_id": "b",
                    "metric_id": "expected_stage_agreement",
                    "scenario": scenario,
                    "denominator_unit": (
                        "stage-applicable attacked simulation evaluations"
                    ),
                },
                {
                    **base,
                    "panel_id": "b",
                    "metric_id": "pipeline_risk_difference",
                    "scenario": scenario,
                    "comparator_pipeline": comparator,
                    "comparison_type": "matched_pipeline",
                    "evaluation_arm": (
                        "paired_attack_pipelines"
                    ),
                    "denominator_unit": (
                        "paired attacked evaluations"
                    ),
                    "both_reject_n": 1,
                    "attack_only_reject_n": 8,
                    "clean_only_reject_n": 0,
                    "neither_reject_n": 1,
                },
            ]
        )
    for rate in ATTACK_RATES:
        rows.extend(
            [
                {
                    **base,
                    "panel_id": "c",
                    "metric_id": "coverage",
                    "scenario": "mixed_attack",
                    "rate_requested": rate,
                    "evaluation_arm": (
                        "mixed_eligible_population"
                    ),
                    "denominator_unit": (
                        "eligible mixed simulation evaluations"
                    ),
                },
                {
                    **base,
                    "panel_id": "c",
                    "metric_id": "abstention",
                    "scenario": "mixed_attack",
                    "rate_requested": rate,
                    "numerator_n": 2,
                    "estimate": 0.2,
                    "ci_low": 0.0,
                    "ci_high": 0.4,
                    "evaluation_arm": (
                        "mixed_eligible_population"
                    ),
                    "denominator_unit": (
                        "eligible mixed simulation evaluations"
                    ),
                },
                {
                    **base,
                    "panel_id": "d",
                    "metric_id": "covered_agreement",
                    "scenario": "mixed_attack",
                    "rate_requested": rate,
                    "evaluation_arm": (
                        "mixed_eligible_population"
                    ),
                    "denominator_unit": (
                        "covered mixed simulation evaluations"
                    ),
                },
                {
                    **base,
                    "panel_id": "d",
                    "metric_id": "upward_discordance",
                    "scenario": "mixed_attack",
                    "rate_requested": rate,
                    "numerator_n": 0,
                    "estimate": 0.0,
                    "ci_low": 0.0,
                    "ci_high": 0.2,
                    "evaluation_arm": (
                        "mixed_eligible_population"
                    ),
                    "denominator_unit": (
                        "eligible mixed simulation evaluations"
                    ),
                },
                {
                    **base,
                    "panel_id": "d",
                    "metric_id": (
                        "priority_loss_discordance"
                    ),
                    "scenario": "mixed_attack",
                    "rate_requested": rate,
                    "numerator_n": 0,
                    "estimate": 0.0,
                    "ci_low": 0.0,
                    "ci_high": 0.2,
                    "evaluation_arm": (
                        "mixed_eligible_population"
                    ),
                    "denominator_unit": (
                        "eligible mixed simulation evaluations"
                    ),
                },
            ]
        )

    source = pd.DataFrame(rows)
    source.to_csv(source_path, index=False)
    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "experiment": "aamos",
        "provenance": "public_secondary",
        "run_id": "submission-run",
        "created_at": "2026-07-23T00:00:00Z",
        "controlled_source": {
            "identity_sha256": "c" * 64,
            "snapshot_sha256": "d" * 64,
            "member_count": 85,
            "derivation_config_member": (
                "config/aamos00_derivation.yaml"
            ),
        },
        "environment": {
            "profile": "submission",
            "bootstrap_repetitions": 2_000,
            "bootstrap_master_seed": 20260722,
            "injection_seeds": list(FIXED_SEEDS),
        },
        "artifacts": {source_path.name: _sha256(source_path)},
        "dataset": {
            "name": "AAMOS-00",
            "doi": "10.7488/ds/3775",
            "source_files": source_inventory,
            "source_inventory_sha256": inventory_hash,
        },
        "derivation": {
            "config_canonical_sha256": (
                FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
            ),
            "derivation_config_member": (
                "config/aamos00_derivation.yaml"
            ),
            "derivation_config_basename": (
                "aamos00_derivation.yaml"
            ),
            "derivation_config_file_sha256": (
                FIXED_DERIVATION_CONFIG_FILE_SHA256
            ),
            "derivation_config_canonical_sha256": (
                FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
            ),
            "fixed_submission_config": True,
        },
        "design": {
            "code_archive_sha256": "c" * 64,
            "metric_definition_version": (
                METRIC_DEFINITION_VERSION
            ),
            "rates": [0.0, *ATTACK_RATES],
            "seeds": list(FIXED_SEEDS),
            "attack_scenarios": attack_scenarios,
            "boundary_scenarios": boundary_scenarios,
            "pipelines": pipelines,
            "bootstrap": {
                "method": (
                    "crossed_seed_participant_multinomial"
                ),
                "interval_type": "percentile_95",
                "repetitions": 2_000,
                "master_seed": 20260722,
            },
            "derivation_config_basename": (
                "aamos00_derivation.yaml"
            ),
            "derivation_config_member": (
                "config/aamos00_derivation.yaml"
            ),
            "derivation_config_file_sha256": (
                FIXED_DERIVATION_CONFIG_FILE_SHA256
            ),
            "derivation_config_canonical_sha256": (
                FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
            ),
            "fixed_submission_config": True,
        },
    }
    manifest_path = root / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source_path, manifest_path, source, manifest
