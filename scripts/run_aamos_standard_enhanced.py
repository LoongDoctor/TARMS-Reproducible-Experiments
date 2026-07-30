#!/usr/bin/env python3
"""Run the prespecified AAMOS-00 controlled protocol-integrity experiment."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_experiment import (  # noqa: E402
    ATTACK_RATES,
    FIXED_SEEDS,
    PIPELINES,
    run_standard_enhanced_experiment,
)
from tarms_experiments.aamos_scenarios import (  # noqa: E402
    BOUNDARY_SCENARIOS,
    REJECT_SCENARIOS,
)
from tarms_experiments.aamos_source import (  # noqa: E402
    AAMOS_ANALYSIS_EXPECTATIONS,
    FIXED_DERIVATION_CONFIG_BASENAME,
    FIXED_DERIVATION_CONFIG_CANONICAL_SHA256,
    FIXED_DERIVATION_CONFIG_FILE_SHA256,
    OFFICIAL_AAMOS_RELEASE,
    build_patient_days,
    canonical_config_sha256,
    source_inventory_sha256,
)
from tarms_experiments.aamos_statistics import (  # noqa: E402
    BOOTSTRAP_INTERVAL_TYPE,
    BOOTSTRAP_METHOD,
    METRIC_DEFINITION_VERSION,
    analyze_experiment,
    build_figure_source_data,
)
from tarms_experiments.release_identity import (  # noqa: E402
    controlled_source_identity,
    controlled_source_members,
    inspect_controlled_snapshot,
)


FIXED_DERIVATION_CONFIG_PATH = (
    PROJECT_ROOT / "config" / FIXED_DERIVATION_CONFIG_BASENAME
)
FIXED_DERIVATION_CONFIG_MEMBER = (
    f"config/{FIXED_DERIVATION_CONFIG_BASENAME}"
)
CANONICAL_ARTIFACT_FILENAMES = (
    "patient_days.csv",
    "clean_decisions.csv",
    "injection_manifest.csv",
    "attack_decisions.csv",
    "boundary_manifest.csv",
    "boundary_decisions.csv",
    "per_seed_metrics.csv",
    "metric_summary.csv",
    "attack_stage_matrix.csv",
    "paired_contrasts.csv",
    "fig_aamos_protocol_integrity_source_data.csv",
    "participant_day_flow.json",
)


class _ArchiveMemberPath(type(Path())):
    """Physical path carrying a distinct logical archive member name."""

    __slots__ = ("archive_member_name",)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_artifact_paths(output: Path) -> tuple[Path, ...]:
    paths = tuple(
        Path(output) / name
        for name in CANONICAL_ARTIFACT_FILENAMES
    )
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "AAMOS run lacks canonical artifacts: "
            + ", ".join(missing)
        )
    return paths


def _archive_member_name(path: Path) -> str:
    logical_name = getattr(path, "archive_member_name", None)
    if logical_name is not None:
        return str(logical_name)
    try:
        return path.resolve().relative_to(
            PROJECT_ROOT.resolve()
        ).as_posix()
    except ValueError:
        return f"runtime-config/{path.name}"


def _code_archive_paths(
    derivation_config_path: Path = FIXED_DERIVATION_CONFIG_PATH,
) -> tuple[Path, ...]:
    config_path = Path(derivation_config_path)
    controlled = [
        PROJECT_ROOT / member
        for member in controlled_source_members(PROJECT_ROOT)
    ]
    if config_path.resolve() != FIXED_DERIVATION_CONFIG_PATH.resolve():
        preview_config = _ArchiveMemberPath(config_path)
        preview_config.archive_member_name = (
            f"runtime-config/{config_path.name}"
        )
        controlled.append(preview_config)
    return tuple(
        sorted(
            controlled,
            key=_archive_member_name,
        )
    )


def _code_archive_hash(
    *,
    derivation_config_path: Path = FIXED_DERIVATION_CONFIG_PATH,
) -> str:
    config_path = Path(derivation_config_path)
    if config_path.resolve() == FIXED_DERIVATION_CONFIG_PATH.resolve():
        return controlled_source_identity(PROJECT_ROOT)

    digest = hashlib.sha256()
    paths = _code_archive_paths(config_path)
    member_names = tuple(_archive_member_name(path) for path in paths)
    if len(member_names) != len(set(member_names)):
        raise ValueError("preview code archive contains duplicate member names")
    for path, member_name in zip(paths, member_names, strict=True):
        digest.update(member_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _derivation_config_identity(
    path: Path,
    config: dict[str, object],
) -> dict[str, object]:
    resolved_path = Path(path).resolve()
    canonical_hash = canonical_config_sha256(config)
    file_hash = _sha256(path)
    member = (
        FIXED_DERIVATION_CONFIG_MEMBER
        if resolved_path == FIXED_DERIVATION_CONFIG_PATH.resolve()
        else f"runtime-config/{path.name}"
    )
    return {
        "derivation_config_member": member,
        "derivation_config_basename": path.name,
        "derivation_config_file_sha256": file_hash,
        "derivation_config_canonical_sha256": canonical_hash,
        "fixed_submission_config": (
            resolved_path == FIXED_DERIVATION_CONFIG_PATH.resolve()
            and path.name == FIXED_DERIVATION_CONFIG_BASENAME
            and canonical_hash
            == FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
            and file_hash == FIXED_DERIVATION_CONFIG_FILE_SHA256
        ),
    }


def _submission_controlled_source_record(
    snapshot_path: Path,
) -> dict[str, object]:
    members = controlled_source_members(PROJECT_ROOT)
    identity = controlled_source_identity(PROJECT_ROOT, members)
    snapshot = inspect_controlled_snapshot(snapshot_path)
    if tuple(snapshot["members"]) != members:
        raise ValueError(
            "controlled-source snapshot members do not match current source"
        )
    if snapshot["identity_sha256"] != identity:
        raise ValueError(
            "controlled-source snapshot identity does not match current source"
        )
    if FIXED_DERIVATION_CONFIG_MEMBER not in members:
        raise ValueError(
            "controlled-source lacks the fixed derivation config member"
        )
    return {
        "identity_sha256": identity,
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "member_count": len(members),
        "derivation_config_member": FIXED_DERIVATION_CONFIG_MEMBER,
    }


def _assert_submission_source_unchanged(
    expected: dict[str, object],
    snapshot_path: Path,
) -> None:
    observed = _submission_controlled_source_record(snapshot_path)
    if observed != expected:
        raise RuntimeError(
            "controlled-source identity changed during the formal run"
        )


def _validate_official_source_flow(
    flow: dict[str, object], config: dict[str, object]
) -> None:
    """Require the fixed Edinburgh release for submission-profile evidence."""

    if (
        canonical_config_sha256(config)
        != FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
    ):
        raise ValueError(
            "submission profile requires the fixed derivation config"
        )
    expected = AAMOS_ANALYSIS_EXPECTATIONS
    scalar_fields = {
        "participants": "participants",
        "participant_days": "daily_questionnaire_participant_days",
        "eligible_participant_days": "eligible_three_item_days",
    }
    for flow_field, expected_field in scalar_fields.items():
        if int(flow.get(flow_field, -1)) != int(expected[expected_field]):
            raise ValueError(
                "AAMOS-00 analysis derivation "
                f"{flow_field} does not match the fixed contract"
            )
    observed_priorities = {
        str(key): int(value)
        for key, value in dict(flow.get("priority_counts", {})).items()
    }
    expected_priorities = {
        str(key): int(value)
        for key, value in dict(expected["priority_counts"]).items()
    }
    if observed_priorities != expected_priorities:
        raise ValueError(
            "official AAMOS-00 priority flow does not match the fixed contract"
        )
    inventory = {
        str(item["name"]): str(item["sha256"])
        for item in flow.get("source_files", [])
    }
    expected_inventory = {
        str(name): str(digest)
        for name, digest in dict(
            OFFICIAL_AAMOS_RELEASE[
                "selected_analysis_source_sha256"
            ]
        ).items()
    }
    if inventory != expected_inventory:
        raise ValueError(
            "official AAMOS-00 source hash set does not match the fixed contract"
        )


def _execution_counts(tables) -> dict[str, list[dict[str, object]]]:
    """Return manifest-reconcilable execution totals per analysis stratum."""

    result: dict[str, list[dict[str, object]]] = {}
    for name, frame in (
        ("attack", tables.attack_decisions),
        ("boundary", tables.boundary_decisions),
    ):
        rows: list[dict[str, object]] = []
        if not frame.empty:
            for key, group in frame.groupby(
                ["scenario", "rate_requested", "pipeline"],
                sort=True,
            ):
                scenario, rate, pipeline = key
                rows.append(
                    {
                        "scenario": str(scenario),
                        "rate_requested": float(rate),
                        "pipeline": str(pipeline),
                        "attempted_N": int(
                            group["attempted"].astype(bool).sum()
                        ),
                        "mutated_N": int(
                            group["mutated"].astype(bool).sum()
                        ),
                        "evaluated_N": int(
                            group["evaluated"].astype(bool).sum()
                        ),
                        "decision_rows_N": int(len(group)),
                    }
                )
        result[name] = rows
    return result


def _atomic_publish(staging: Path, final: Path) -> None:
    """Publish a complete staged run with one same-filesystem rename."""

    if final.exists():
        raise FileExistsError(f"run output already exists: {final}")
    staging.replace(final)


def _formal_geometry_counts() -> dict[str, int]:
    """Return the fixed submission geometry before allocating large tables."""

    eligible_days = 1_582
    seed_count = len(FIXED_SEEDS)
    pipeline_count = len(PIPELINES)
    attack_targets = sum(
        max(1, int(math.floor(eligible_days * rate + 0.5)))
        for rate in ATTACK_RATES
    )
    attack_manifest_rows = (
        len(REJECT_SCENARIOS) * seed_count * attack_targets
    )
    boundary_manifest_rows = (
        len(BOUNDARY_SCENARIOS)
        * seed_count
        * int(math.floor(eligible_days * 0.10 + 0.5))
    )
    clean_decisions = eligible_days * seed_count * pipeline_count
    attack_decisions = attack_manifest_rows * pipeline_count
    boundary_decisions = boundary_manifest_rows * pipeline_count
    return {
        "clean_decisions": clean_decisions,
        "attack_manifest_rows": attack_manifest_rows,
        "attack_decisions": attack_decisions,
        "boundary_manifest_rows": boundary_manifest_rows,
        "boundary_decisions": boundary_decisions,
        "total_decisions": (
            clean_decisions + attack_decisions + boundary_decisions
        ),
    }


def _ru_maxrss_to_kib(
    raw_maxrss: int,
    *,
    system: str | None = None,
) -> int:
    """Normalize platform-specific ``ru_maxrss`` units to KiB."""

    raw_maxrss = int(raw_maxrss)
    if (system or platform.system()) == "Darwin":
        return (raw_maxrss + 1_023) // 1_024
    return raw_maxrss


def _runtime_capacity_record(
    *,
    started_monotonic: float,
    rss_before_kib: int,
) -> dict[str, float | int | str]:
    peak_rss_kib = _ru_maxrss_to_kib(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    )
    return {
        "wall_time_seconds": float(
            time.perf_counter() - started_monotonic
        ),
        "peak_rss_kib": peak_rss_kib,
        "peak_rss_delta_kib": max(
            0, peak_rss_kib - int(rss_before_kib)
        ),
        "peak_rss_unit": "KiB",
    }


def _parse_ints(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _parse_floats(value: str) -> tuple[float, ...]:
    return tuple(
        float(item.strip()) for item in value.split(",") if item.strip()
    )


def _parse_strings(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False)


def main() -> int:
    started_monotonic = time.perf_counter()
    rss_before_kib = _ru_maxrss_to_kib(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--derivation-config",
        type=Path,
        default=FIXED_DERIVATION_CONFIG_PATH,
    )
    parser.add_argument(
        "--output-root", type=Path, default=PROJECT_ROOT / "results"
    )
    parser.add_argument(
        "--controlled-snapshot",
        type=Path,
        help=(
            "frozen controlled-source ZIP; submission defaults to the "
            "output-root sibling controlled-source.zip"
        ),
    )
    parser.add_argument(
        "--profile", choices=("submission", "preview"), default="submission"
    )
    parser.add_argument("--bootstrap-reps", type=int, default=2_000)
    parser.add_argument(
        "--bootstrap-seed", type=int, default=20260722
    )
    parser.add_argument(
        "--rates",
        type=_parse_floats,
        default=ATTACK_RATES,
        help="comma-separated nonzero injection rates (preview only)",
    )
    parser.add_argument(
        "--seeds",
        type=_parse_ints,
        default=FIXED_SEEDS,
        help="comma-separated injection seeds (preview only)",
    )
    parser.add_argument(
        "--attack-scenarios",
        type=_parse_strings,
        default=tuple(REJECT_SCENARIOS),
        help="comma-separated attack scenarios (preview only)",
    )
    parser.add_argument(
        "--boundary-scenarios",
        type=_parse_strings,
        default=tuple(BOUNDARY_SCENARIOS),
        help="comma-separated boundary controls (preview only)",
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()

    if args.bootstrap_reps <= 0:
        parser.error("--bootstrap-reps must be positive")
    if args.profile == "submission":
        if (
            args.derivation_config.resolve()
            != FIXED_DERIVATION_CONFIG_PATH.resolve()
        ):
            parser.error(
                "submission profile requires the fixed derivation config"
            )
        if args.bootstrap_reps < 2_000:
            parser.error(
                "submission profile requires at least 2000 bootstrap repetitions"
            )
        if tuple(args.rates) != ATTACK_RATES:
            parser.error("submission profile requires fixed attack rates")
        if tuple(args.seeds) != FIXED_SEEDS:
            parser.error("submission profile requires the 20 fixed seeds")
        if tuple(args.attack_scenarios) != tuple(REJECT_SCENARIOS):
            parser.error(
                "submission profile requires every prespecified attack scenario"
            )
        if tuple(args.boundary_scenarios) != tuple(BOUNDARY_SCENARIOS):
            parser.error(
                "submission profile requires every boundary-control scenario"
            )

    controlled_snapshot_path: Path | None = None
    controlled_source_record: dict[str, object] | None = None
    if args.profile == "submission":
        controlled_snapshot_path = (
            args.controlled_snapshot
            if args.controlled_snapshot is not None
            else args.output_root.parent / "controlled-source.zip"
        )
        try:
            controlled_source_record = _submission_controlled_source_record(
                controlled_snapshot_path
            )
        except (OSError, ValueError) as error:
            parser.error(
                f"controlled-source snapshot validation failed: {error}"
            )

    created = datetime.now(timezone.utc)
    run_id = args.run_id or created.strftime(
        "aamos-standard-%Y%m%dT%H%M%SZ"
    )
    final_output = args.output_root / "processed" / "aamos" / run_id
    final_output.parent.mkdir(parents=True, exist_ok=True)
    if final_output.exists():
        raise FileExistsError(
            f"run output already exists: {final_output}"
        )
    output = Path(
        tempfile.mkdtemp(
            prefix=f".{run_id}.staging-",
            dir=final_output.parent,
        )
    )
    def cleanup_staging() -> None:
        shutil.rmtree(output, ignore_errors=True)

    atexit.register(cleanup_staging)

    derivation_config = yaml.safe_load(
        args.derivation_config.read_text(encoding="utf-8")
    )
    config_identity = _derivation_config_identity(
        args.derivation_config, derivation_config
    )
    if (
        args.profile == "submission"
        and not config_identity["fixed_submission_config"]
    ):
        parser.error(
            "submission profile requires the fixed derivation config"
        )
    patient_days, flow = build_patient_days(
        args.source_dir, derivation_config
    )
    if args.profile == "submission":
        _validate_official_source_flow(flow, derivation_config)
    eligible = patient_days.loc[
        patient_days["eligible"].astype(bool)
    ].copy()
    dataset_identity = {
        "dataset_name": "AAMOS-00",
        "dataset_doi": str(OFFICIAL_AAMOS_RELEASE["doi"]),
        "dataset_source_inventory_sha256": (
            source_inventory_sha256(flow["source_files"])
        ),
    }
    tables = run_standard_enhanced_experiment(
        eligible,
        attack_scenarios=tuple(args.attack_scenarios),
        boundary_scenarios=tuple(args.boundary_scenarios),
        rates=tuple(args.rates),
        seeds=tuple(args.seeds),
        boundary_rate=0.10,
    )
    analysis = analyze_experiment(
        tables,
        repetitions=args.bootstrap_reps,
        master_seed=args.bootstrap_seed,
        run_id=run_id,
    )
    code_hash = (
        str(controlled_source_record["identity_sha256"])
        if controlled_source_record is not None
        else _code_archive_hash(
            derivation_config_path=args.derivation_config
        )
    )
    figure_source = build_figure_source_data(
        analysis.summary,
        analysis.paired_contrasts,
        run_id=run_id,
        created_utc=created.isoformat(),
        code_commit_or_archive_hash=code_hash,
        bootstrap_master_seed=args.bootstrap_seed,
        derivation_config_identity=config_identity,
        dataset_identity=dataset_identity,
    )

    frames = {
        "patient_days.csv": patient_days,
        "clean_decisions.csv": tables.clean_decisions,
        "injection_manifest.csv": tables.injection_manifest,
        "attack_decisions.csv": tables.attack_decisions,
        "boundary_manifest.csv": tables.boundary_manifest,
        "boundary_decisions.csv": tables.boundary_decisions,
        "per_seed_metrics.csv": analysis.per_seed_metrics,
        "metric_summary.csv": analysis.summary,
        "attack_stage_matrix.csv": analysis.attack_stage_matrix,
        "paired_contrasts.csv": analysis.paired_contrasts,
        "fig_aamos_protocol_integrity_source_data.csv": figure_source,
    }
    for name, frame in frames.items():
        _write_frame(frame, output / name)
    (output / "participant_day_flow.json").write_text(
        json.dumps(flow, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    artifact_paths = _canonical_artifact_paths(output)
    artifact_hashes = {
        path.name: _sha256(path) for path in artifact_paths
    }
    capacity = _runtime_capacity_record(
        started_monotonic=started_monotonic,
        rss_before_kib=rss_before_kib,
    )
    manifest = {
        "schema_version": "1.0",
        "experiment": "aamos",
        "provenance": "public_secondary",
        "run_id": run_id,
        "created_at": created.isoformat(),
        **(
            {"controlled_source": controlled_source_record}
            if controlled_source_record is not None
            else {}
        ),
        "environment": {
            "profile": args.profile,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": _version("numpy"),
            "pandas": _version("pandas"),
            "cryptography": _version("cryptography"),
            "scienceplots": _version("SciencePlots"),
            "bootstrap_repetitions": int(args.bootstrap_reps),
            "bootstrap_master_seed": int(args.bootstrap_seed),
            "injection_seeds": list(args.seeds),
        },
        "artifacts": artifact_hashes,
        "artifact_details": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": artifact_hashes[path.name],
            }
            for path in artifact_paths
        },
        "capacity": {
            **capacity,
            "formal_geometry": _formal_geometry_counts(),
        },
        "dataset": {
            "name": dataset_identity["dataset_name"],
            "doi": dataset_identity["dataset_doi"],
            "official_release_participants": (
                OFFICIAL_AAMOS_RELEASE["participants"]
            ),
            "official_any_modality_participant_days": (
                OFFICIAL_AAMOS_RELEASE[
                    "any_modality_participant_days"
                ]
            ),
            "analysis_participants": flow["participants"],
            "daily_questionnaire_participant_days": flow[
                "participant_days"
            ],
            "eligible_three_item_days": flow[
                "eligible_participant_days"
            ],
            "source_files": flow["source_files"],
            "source_inventory_sha256": dataset_identity[
                "dataset_source_inventory_sha256"
            ],
        },
        "derivation": {
            **flow["derivation"],
            **config_identity,
        },
        "design": {
            "metric_definition_version": METRIC_DEFINITION_VERSION,
            "priority_rule": (
                "derived count of three complete daily symptom "
                "indicators (0-3); not a clinical priority"
            ),
            "rates": [0.0, *args.rates],
            "seeds": list(args.seeds),
            "attack_scenarios": list(args.attack_scenarios),
            "boundary_scenarios": list(args.boundary_scenarios),
            "pipelines": {
                name: list(checks) for name, checks in PIPELINES.items()
            },
            "bootstrap": {
                "method": BOOTSTRAP_METHOD,
                "interval_type": BOOTSTRAP_INTERVAL_TYPE,
                "repetitions": int(args.bootstrap_reps),
                "master_seed": int(args.bootstrap_seed),
            },
            "code_archive_sha256": code_hash,
            **config_identity,
        },
        "population_counts": {
            "unique_participants": int(
                eligible["participant_id"].nunique()
            ),
            "unique_eligible_participant_days": int(len(eligible)),
            "seed_count": int(len(args.seeds)),
            "clean_simulation_evaluations": int(
                len(tables.clean_decisions)
            ),
            "attack_simulation_evaluations": int(
                len(tables.attack_decisions)
            ),
            "boundary_rows": int(len(tables.boundary_decisions)),
            "execution_strata": _execution_counts(tables),
        },
        "boundaries": [
            (
                "AAMOS-00 supplies anonymized respiratory-monitoring payloads "
                "only; all protocol metadata and attack labels are synthetic."
            ),
            (
                "The three-item symptom count is a prespecified "
                "computational rule, not a "
                "diagnosis, treatment recommendation, or clinical outcome."
            ),
            (
                "Crossed seed-participant intervals describe the fixed "
                "controlled design; they do not estimate attack prevalence "
                "or clinical effectiveness."
            ),
        ],
    }
    if (
        controlled_source_record is not None
        and controlled_snapshot_path is not None
    ):
        _assert_submission_source_unchanged(
            controlled_source_record,
            controlled_snapshot_path,
        )
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if (
        controlled_source_record is not None
        and controlled_snapshot_path is not None
    ):
        _assert_submission_source_unchanged(
            controlled_source_record,
            controlled_snapshot_path,
        )
    try:
        _atomic_publish(output, final_output)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    atexit.unregister(cleanup_staging)
    output = final_output
    print(
        json.dumps(
            {
                "run_id": run_id,
                "output": str(output),
                "profile": args.profile,
                "bootstrap_repetitions": args.bootstrap_reps,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
