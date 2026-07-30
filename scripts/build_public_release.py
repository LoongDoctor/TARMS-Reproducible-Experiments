#!/usr/bin/env python3
"""Build a deterministic, privacy-safe, self-verifying public release."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import ctypes
import errno
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_experiment import (  # noqa: E402
    ATTACK_RATES,
    PIPELINES,
)
from tarms_experiments.aamos_scenarios import (  # noqa: E402
    BOUNDARY_SCENARIOS,
    REJECT_SCENARIOS,
)
from tarms_experiments.release_identity import (  # noqa: E402
    controlled_source_identity,
    controlled_source_members,
    inspect_controlled_snapshot,
)


FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)
FIXED_DERIVATION_MEMBER = "config/aamos00_derivation.yaml"
CONTROLLED_SNAPSHOT_NAME = "controlled-source.zip"
TEST_REPORT_NAME = "test-report.json"
PUBLIC_MANIFEST_NAME = "public_release_manifest.json"
TREE_MANIFEST_NAME = "MANIFEST.sha256"
PUBLIC_RUN_FILES = (
    "run_manifest.json",
    "metric_summary.csv",
    "per_seed_metrics.csv",
    "paired_contrasts.csv",
    "attack_stage_matrix.csv",
    "participant_day_flow.json",
    "fig_aamos_protocol_integrity_source_data.csv",
)
PRIVATE_RUN_FILES = (
    "patient_days.csv",
    "clean_decisions.csv",
    "injection_manifest.csv",
    "attack_decisions.csv",
    "boundary_manifest.csv",
    "boundary_decisions.csv",
)
CANONICAL_RUN_FILES = tuple(
    sorted((*PUBLIC_RUN_FILES[1:], *PRIVATE_RUN_FILES))
)
SUBMISSION_FIGURE_FILES = (
    "fig_03_python_benchmarks.pdf",
    "fig_03_python_benchmarks.png",
    "fig_03_python_benchmarks_source_data.csv",
    "fig_04_component_conformance.pdf",
    "fig_04_component_conformance.png",
    "fig_04_component_conformance_source_data.csv",
    "fig_05_window_tradeoff.pdf",
    "fig_05_window_tradeoff.png",
    "fig_05_window_tradeoff_source_data.csv",
    "fig_06_aamos_protocol_integrity.pdf",
    "fig_06_aamos_protocol_integrity.png",
    "fig_06_aamos_protocol_integrity_source_data.csv",
)
ROOT_FILES = (
    "LICENSE",
    "CITATION.cff",
    "README.md",
    "README_zh.md",
    "Makefile",
    "pyproject.toml",
    "requirements-lock.txt",
)
PUBLIC_DOC_FILES = ("AAMOS_EXPERIMENT_PROTOCOL.md",)
SOURCE_DIRECTORIES = ("config", "scripts", "src", "tests", "fabric")
NON_AAMOS_RESULT_DIRECTORIES = (
    "results/raw/python",
    "results/raw/python_components",
    "results/raw/python_conformance",
    "results/processed/python",
    "results/processed/python_components",
    "results/processed/python_conformance",
)
LEGACY_PUBLIC_MARKERS = (
    "PEND" + "ING_" + "PUBLIC_COMMIT",
    "aamos-submission-" + "20260723-v5",
    "7785" + "a37b",
)
_FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "aamos00",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VerificationRunner = Callable[[Path], Mapping[str, object]]
DirectoryInventory = tuple[tuple[str, str], ...]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(
    path: Path,
    *,
    label: str,
    sealed_inputs: dict[Path, str] | None = None,
) -> dict[str, object]:
    def reject_duplicates(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    data = path.read_bytes()
    if sealed_inputs is not None:
        observed = _sha256_bytes(data)
        previous = sealed_inputs.setdefault(path, observed)
        if previous != observed:
            raise ValueError(
                f"sealed release input changed during build: {path.name}"
            )
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _required_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_run_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("formal run ID must be nonempty")
    path = PurePosixPath(value)
    if (
        len(path.parts) != 1
        or path.name != value
        or value.startswith(".")
        or "\\" in value
    ):
        raise ValueError("formal run ID must be a safe directory name")
    return value


def _regular_source(path: Path, *, label: str) -> Path:
    source = Path(path)
    if source.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"{label} is missing") from error
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _project_input(
    path: Path,
    *,
    project_root: Path,
    label: str,
    directory: bool,
) -> Path:
    root = project_root.resolve(strict=True)
    lexical = Path(os.path.abspath(path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} is outside project root") from error
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(
                f"{label} project input contains a symlink: {cursor}"
            )
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"{label} is missing") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} is outside project root") from error
    if directory:
        if not resolved.is_dir():
            raise ValueError(f"{label} must be a directory")
    elif not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _copy_file(
    source: Path,
    destination: Path,
    *,
    label: str,
    sealed_inputs: Mapping[Path, str],
) -> None:
    regular = _regular_source(source, label=label)
    with regular.open("rb") as input_handle:
        data = input_handle.read()
    expected = sealed_inputs.get(regular)
    if expected is None:
        raise ValueError(f"{label} was not sealed before copying")
    if _sha256_bytes(data) != expected:
        raise ValueError(
            f"sealed release input changed during build: {regular.name}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    destination.chmod(0o755 if source.suffix == ".sh" else 0o644)


def _source_tree_inventory(source: Path) -> DirectoryInventory:
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"source directory is unsafe or missing: {source}")
    root = source.resolve(strict=True)
    entries: list[tuple[str, str]] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name in _FORBIDDEN_PARTS:
                continue
            relative = child.relative_to(root).as_posix()
            if child.is_symlink():
                raise ValueError(f"source tree contains a symlink: {child}")
            if child.is_dir():
                entries.append((relative, "directory"))
                visit(child)
            elif child.is_file():
                entries.append((relative, "file"))
            else:
                raise ValueError(
                    "source tree contains an unsupported special node: "
                    f"{child}"
                )

    visit(root)
    return tuple(entries)


def _seal_file(
    source: Path,
    *,
    label: str,
    sealed_inputs: dict[Path, str],
    project_root: Path | None = None,
) -> Path:
    regular = (
        _project_input(
            source,
            project_root=project_root,
            label=label,
            directory=False,
        )
        if project_root is not None
        else _regular_source(source, label=label)
    )
    observed = _sha256(regular)
    previous = sealed_inputs.setdefault(regular, observed)
    if previous != observed:
        raise ValueError(
            f"sealed release input changed during build: {regular.name}"
        )
    return regular


def _seal_source_tree(
    source: Path,
    *,
    sealed_inputs: dict[Path, str],
    sealed_directories: dict[Path, DirectoryInventory],
    project_root: Path | None = None,
) -> Path:
    if project_root is not None:
        root = _project_input(
            source,
            project_root=project_root,
            label="public source directory",
            directory=True,
        )
    else:
        if source.is_symlink() or not source.is_dir():
            raise ValueError(
                f"source directory is unsafe or missing: {source}"
            )
        root = source.resolve(strict=True)
    inventory = _source_tree_inventory(root)
    sealed_directories[root] = inventory
    for relative, node_type in inventory:
        if node_type == "file":
            _seal_file(
                root / relative,
                label="public source member",
                sealed_inputs=sealed_inputs,
            )
    return root


def _copy_source_tree(
    source: Path,
    destination: Path,
    *,
    sealed_inputs: Mapping[Path, str],
) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"source directory is unsafe or missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or child.name in _FORBIDDEN_PARTS:
            continue
        if child.is_symlink():
            raise ValueError(f"source tree contains a symlink: {child}")
        target = destination / child.name
        if child.is_dir():
            _copy_source_tree(
                child,
                target,
                sealed_inputs=sealed_inputs,
            )
        elif child.is_file():
            _copy_file(
                child,
                target,
                label="public source member",
                sealed_inputs=sealed_inputs,
            )
        else:
            raise ValueError(
                f"source tree contains an unsupported special node: {child}"
            )


def _controlled_contract(
    manifest: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    controlled = manifest.get("controlled_source")
    if not isinstance(controlled, Mapping):
        raise ValueError("formal run manifest lacks controlled_source")
    expected = {
        "identity_sha256": _required_hash(
            snapshot.get("identity_sha256"),
            label="snapshot identity",
        ),
        "snapshot_sha256": _required_hash(
            snapshot.get("snapshot_sha256"),
            label="snapshot hash",
        ),
        "member_count": snapshot.get("member_count"),
        "derivation_config_member": FIXED_DERIVATION_MEMBER,
    }
    if (
        isinstance(expected["member_count"], bool)
        or not isinstance(expected["member_count"], int)
        or expected["member_count"] <= 0
    ):
        raise ValueError("snapshot member count must be positive")
    if dict(controlled) != expected:
        raise ValueError(
            "formal run controlled-source contract does not match snapshot"
        )
    design = manifest.get("design")
    if not isinstance(design, Mapping):
        raise ValueError("formal run manifest lacks design")
    if design.get("code_archive_sha256") != expected["identity_sha256"]:
        raise ValueError(
            "formal run code archive identity does not match snapshot"
        )
    return expected


def _validate_inputs(
    project_root: Path,
    run_dir: Path,
    snapshot_path: Path,
    test_report_path: Path,
) -> tuple[
    Path,
    Path,
    Path,
    Path,
    dict[str, object],
    dict[str, object],
    dict[Path, str],
]:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    run = Path(run_dir)
    if run.is_symlink():
        raise ValueError("formal run directory must not be a symlink")
    run = run.resolve(strict=True)
    if not run.is_dir():
        raise ValueError("formal run directory must be a directory")
    snapshot = _regular_source(
        snapshot_path, label="controlled-source snapshot"
    )
    report_path = _regular_source(
        test_report_path, label="machine test report"
    )
    snapshot_record = inspect_controlled_snapshot(snapshot)
    sealed_inputs = {
        snapshot: _required_hash(
            snapshot_record.get("snapshot_sha256"),
            label="snapshot hash",
        )
    }
    expected_members = controlled_source_members(root)
    if tuple(snapshot_record["members"]) != expected_members:
        raise ValueError(
            "controlled-source snapshot members do not match project"
        )
    current_identity = controlled_source_identity(root)
    if snapshot_record["identity_sha256"] != current_identity:
        raise ValueError(
            "controlled-source snapshot identity does not match project"
        )

    run_manifest_path = _regular_source(
        run / "run_manifest.json", label="formal run manifest"
    )
    run_manifest = _strict_json(
        run_manifest_path,
        label="formal run manifest",
        sealed_inputs=sealed_inputs,
    )
    run_id = _safe_run_id(run_manifest.get("run_id"))
    if run.name != run_id:
        raise ValueError("formal run directory name must equal run ID")
    controlled = _controlled_contract(run_manifest, snapshot_record)

    artifacts = run_manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("formal run artifacts must be an object")
    if set(artifacts) != set(CANONICAL_RUN_FILES):
        raise ValueError(
            "formal run artifacts do not match the canonical contract"
        )
    for name in CANONICAL_RUN_FILES:
        expected = _required_hash(
            artifacts.get(name), label=f"formal artifact {name}"
        )
        artifact = _regular_source(
            run / name, label=f"formal artifact {name}"
        )
        observed = _sha256(artifact)
        if observed != expected:
            raise ValueError(f"formal artifact hash mismatch: {name}")
        sealed_inputs[artifact] = observed

    report = _strict_json(
        report_path,
        label="machine test report",
        sealed_inputs=sealed_inputs,
    )
    if report.get("status") != "passed":
        raise ValueError("machine test report status must be passed")
    if report.get("controlled_source_identity_stable") is not True:
        raise ValueError(
            "machine test report must record stable controlled source"
        )
    if (
        report.get("controlled_source_identity_sha256")
        != controlled["identity_sha256"]
    ):
        raise ValueError(
            "machine test report identity does not match controlled source"
        )
    suites = report.get("suites")
    if not isinstance(suites, Mapping) or set(suites) != {
        "python",
        "chaincode",
        "client",
        "shell",
    }:
        raise ValueError(
            "machine test report must contain exactly four release suites"
        )
    passed_total = 0
    failed_total = 0
    skipped_total = 0
    for name, raw_suite in suites.items():
        if not isinstance(raw_suite, Mapping):
            raise ValueError(f"machine test suite is invalid: {name}")
        command = raw_suite.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError(
                f"machine test suite lacks exact command: {name}"
            )
        if (
            raw_suite.get("exit_code") != 0
            or raw_suite.get("failed_count") != 0
            or raw_suite.get("count_evidence_valid") is not True
        ):
            raise ValueError(
                f"machine test suite did not pass exactly: {name}"
            )
        passed = raw_suite.get("observed_passed_count")
        skipped = raw_suite.get("skipped_count")
        if (
            isinstance(passed, bool)
            or not isinstance(passed, int)
            or passed <= 0
            or isinstance(skipped, bool)
            or not isinstance(skipped, int)
            or skipped < 0
        ):
            raise ValueError(
                f"machine test suite counts are invalid: {name}"
            )
        if not isinstance(raw_suite.get("stdout"), str) or not isinstance(
            raw_suite.get("stderr"), str
        ):
            raise ValueError(
                f"machine test suite output is invalid: {name}"
            )
        passed_total += passed
        failed_total += int(raw_suite["failed_count"])
        skipped_total += skipped
    totals = report.get("totals")
    if not isinstance(totals, Mapping) or dict(totals) != {
        "failed_count": failed_total,
        "observed_passed_count": passed_total,
        "skipped_count": skipped_total,
    }:
        raise ValueError("machine test report totals do not reconcile")
    environment = report.get("environment")
    if (
        not isinstance(environment, Mapping)
        or environment.get("node_version_valid") is not True
        or environment.get("npm_version_valid") is not True
        or not isinstance(environment.get("python_version"), str)
        or not environment["python_version"]
    ):
        raise ValueError("machine test report environment is invalid")
    return (
        root,
        run,
        snapshot,
        report_path,
        run_manifest,
        controlled,
        sealed_inputs,
    )


def _assert_inputs_unchanged(sealed_inputs: Mapping[Path, str]) -> None:
    for path, expected in sealed_inputs.items():
        current = _regular_source(path, label="sealed release input")
        if _sha256(current) != expected:
            raise ValueError(
                f"sealed release input changed during build: {path.name}"
            )


def _assert_directories_unchanged(
    sealed_directories: Mapping[Path, DirectoryInventory],
) -> None:
    for path, expected in sealed_directories.items():
        if _source_tree_inventory(path) != expected:
            raise ValueError(
                f"sealed release directory changed during build: {path.name}"
            )


def _rate_key(value: object) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("Fig. 6 mark rate must be finite")
    return round(numeric, 12)


def _aamos_mark_key(row: object) -> tuple[object, ...]:
    metric = str(getattr(row, "metric_id"))
    scenario = getattr(row, "scenario")
    comparator = getattr(row, "comparator_pipeline")
    return (
        str(getattr(row, "panel_id")),
        metric,
        "" if pd.isna(scenario) else str(scenario),
        _rate_key(getattr(row, "rate_requested")),
        str(getattr(row, "pipeline")),
        "" if pd.isna(comparator) else str(comparator),
    )


def _expected_aamos_marks() -> set[tuple[object, ...]]:
    expected: set[tuple[object, ...]] = set()
    primary_rate = _rate_key(0.10)
    for scenario in REJECT_SCENARIOS:
        for pipeline in PIPELINES:
            expected.add(
                (
                    "a",
                    "attack_rejection",
                    scenario,
                    primary_rate,
                    pipeline,
                    "",
                )
            )
    for scenario in BOUNDARY_SCENARIOS:
        expected.add(
            (
                "a",
                "control_rejection",
                scenario,
                primary_rate,
                "all_checks",
                "",
            )
        )
    for pipeline in PIPELINES:
        expected.add(
            (
                "a",
                "clean_false_rejection",
                "",
                0.0,
                pipeline,
                "",
            )
        )
    for scenario, stage in REJECT_SCENARIOS.items():
        comparator = (
            "all_minus_freshness"
            if stage == "history"
            else f"all_minus_{stage}"
        )
        expected.add(
            (
                "b",
                "expected_stage_agreement",
                scenario,
                primary_rate,
                "all_checks",
                "",
            )
        )
        expected.add(
            (
                "b",
                "pipeline_risk_difference",
                scenario,
                primary_rate,
                "all_checks",
                comparator,
            )
        )
    for rate in ATTACK_RATES:
        for metric in ("coverage", "abstention"):
            expected.add(
                (
                    "c",
                    metric,
                    "mixed_attack",
                    _rate_key(rate),
                    "all_checks",
                    "",
                )
            )
        for metric in (
            "covered_agreement",
            "upward_discordance",
            "priority_loss_discordance",
        ):
            expected.add(
                (
                    "d",
                    metric,
                    "mixed_attack",
                    _rate_key(rate),
                    "all_checks",
                    "",
                )
            )
    return expected


def _read_figure_source(path: Path, *, label: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, float_precision="round_trip")
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as error:
        raise ValueError(f"{label} is not a valid CSV: {error}") from error


def _validate_submission_figures(
    *,
    project_root: Path,
    figure_dir: Path,
    run: Path,
    run_manifest: Mapping[str, object],
    controlled: Mapping[str, object],
    sealed_inputs: dict[Path, str],
) -> Path:
    figures = Path(figure_dir)
    if figures.is_symlink():
        raise ValueError("submission figure directory must not be a symlink")
    try:
        figures = figures.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("submission figure directory is missing") from error
    if not figures.is_dir():
        raise ValueError("submission figure directory must be a directory")
    try:
        figures.relative_to(project_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "submission figure directory must be outside the frozen project"
        )
    observed_names = {
        child.name
        for child in figures.iterdir()
        if not child.name.startswith(".")
    }
    if observed_names != set(SUBMISSION_FIGURE_FILES):
        raise ValueError(
            "submission figure directory must contain exactly the "
            "12 required artifacts"
        )
    for name in SUBMISSION_FIGURE_FILES:
        _seal_file(
            figures / name,
            label=f"submission figure artifact {name}",
            sealed_inputs=sealed_inputs,
        )

    formal_path = _regular_source(
        run / "fig_aamos_protocol_integrity_source_data.csv",
        label="formal Fig. 6 source",
    )
    figure_path = _regular_source(
        figures / "fig_06_aamos_protocol_integrity_source_data.csv",
        label="submission Fig. 6 source",
    )
    formal = _read_figure_source(formal_path, label="formal Fig. 6 source")
    figure = _read_figure_source(
        figure_path, label="submission Fig. 6 source"
    )
    expected_marks = _expected_aamos_marks()
    for label, source in (("formal", formal), ("submission", figure)):
        required = {
            "panel_id",
            "metric_id",
            "scenario",
            "rate_requested",
            "pipeline",
            "comparator_pipeline",
            "run_id",
            "code_commit_or_archive_hash",
        }
        if not required.issubset(source.columns):
            raise ValueError(f"{label} Fig. 6 source lacks required columns")
        marks = [_aamos_mark_key(row) for row in source.itertuples()]
        if len(marks) != 235 or set(marks) != expected_marks:
            raise ValueError(
                f"{label} Fig. 6 source violates the exact 235-mark contract"
            )
        if set(source["run_id"].astype(str)) != {
            str(run_manifest["run_id"])
        }:
            raise ValueError(f"{label} Fig. 6 source has the wrong run ID")
        if set(source["code_commit_or_archive_hash"].astype(str)) != {
            str(controlled["identity_sha256"])
        }:
            raise ValueError(
                f"{label} Fig. 6 source has the wrong controlled identity"
            )
    try:
        pd.testing.assert_frame_equal(
            formal,
            figure,
            check_dtype=False,
            check_exact=False,
            rtol=0,
            atol=1e-12,
        )
    except AssertionError as error:
        raise ValueError(
            "submission Fig. 6 source does not match formal run source"
        ) from error
    return figures


def _reject_legacy_markers(public_root: Path) -> None:
    for path in sorted(public_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in LEGACY_PUBLIC_MARKERS:
            if marker in text:
                raise ValueError(
                    f"public artifact contains legacy marker {marker}: "
                    f"{path.relative_to(public_root).as_posix()}"
                )


def _reject_release_destination(root: Path, release_dir: Path) -> tuple[Path, Path]:
    final = Path(release_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    parent = final.parent.resolve(strict=True)
    final = parent / final.name
    zip_path = final.with_suffix(".zip")
    for target in (final, zip_path):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"release destination exists: {target}")
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError(
                "release destinations must be outside the frozen project"
            )
    return final, zip_path


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renamex_np
        rename.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        status = rename(
            os.fsencode(source),
            os.fsencode(destination),
            0x00000004,
        )
        if status == 0:
            return
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(
                code, os.strerror(code), str(destination)
            )
        raise OSError(code, os.strerror(code), str(destination))
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise RuntimeError(
                "atomic no-replace directory publication is unavailable"
            )
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        status = rename(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,
        )
        if status == 0:
            return
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(
                code, os.strerror(code), str(destination)
            )
        raise OSError(code, os.strerror(code), str(destination))
    if os.name == "nt":
        os.rename(source, destination)
        return
    raise RuntimeError(
        "atomic no-replace directory publication is unsupported "
        f"on {sys.platform}"
    )


def _publish_verified_release(
    *,
    staged_public: Path,
    staged_zip: Path,
    final: Path,
    final_zip: Path,
) -> None:
    try:
        os.link(staged_zip, final_zip, follow_symlinks=False)
    except FileExistsError as error:
        raise FileExistsError(
            f"release destination appeared during verification: {final_zip}"
        ) from error
    try:
        _rename_directory_noreplace(staged_public, final)
    except FileExistsError as error:
        try:
            if os.path.samestat(staged_zip.stat(), final_zip.stat()):
                final_zip.unlink()
        except (FileNotFoundError, OSError):
            pass
        raise FileExistsError(
            f"release destination appeared during verification: {final}"
        ) from error
    finally:
        if staged_public.exists():
            try:
                if os.path.samestat(staged_zip.stat(), final_zip.stat()):
                    final_zip.unlink()
            except (FileNotFoundError, OSError):
                pass


def _public_manifest(
    public_root: Path,
    run_manifest: Mapping[str, object],
    controlled: Mapping[str, object],
    run_member: str,
) -> dict[str, object]:
    artifacts: dict[str, str] = {}
    for path in sorted(public_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"public staging tree contains symlink: {path}")
        if path.is_file():
            relative = path.relative_to(public_root).as_posix()
            if relative not in (PUBLIC_MANIFEST_NAME, TREE_MANIFEST_NAME):
                artifacts[relative] = _sha256(path)

    formal_artifacts = run_manifest["artifacts"]
    assert isinstance(formal_artifacts, Mapping)
    omissions = {
        name: {
            "canonical_sha256": _required_hash(
                formal_artifacts[name],
                label=f"omitted artifact {name}",
            ),
            "published": False,
            "reason": (
                "Participant-keyed or per-evaluation canonical table; "
                "regenerate locally from the cited public dataset and "
                "fixed controlled source."
            ),
        }
        for name in PRIVATE_RUN_FILES
    }
    run_manifest_hash = artifacts.get(run_member)
    if run_manifest_hash is None:
        raise ValueError("public staging tree lacks the formal run manifest")
    return {
        "schema_version": 1,
        "release_status": "local_v6_candidate",
        "run_id": run_manifest["run_id"],
        "formal_run_manifest_member": run_member,
        "formal_run_manifest_sha256": run_manifest_hash,
        "controlled_source": dict(controlled),
        "public_artifacts": artifacts,
        "locally_regenerated_artifacts": omissions,
        "published_canonical_artifacts": list(PUBLIC_RUN_FILES[1:]),
        "claim_boundaries": list(run_manifest.get("boundaries", [])),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o644)


def _write_tree_manifest(public_root: Path) -> None:
    lines = []
    manifest_path = public_root / TREE_MANIFEST_NAME
    for path in sorted(public_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"public staging tree contains symlink: {path}")
        if path.is_file() and path != manifest_path:
            relative = path.relative_to(public_root).as_posix()
            lines.append(f"{_sha256(path)}  {relative}\n")
    manifest_path.write_text(
        "".join(lines),
        encoding="utf-8",
        newline="\n",
    )
    manifest_path.chmod(0o644)


def _zip_mode(path: Path) -> int:
    return 0o100755 if path.suffix == ".sh" else 0o100644


def _write_deterministic_zip(public_root: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.comment = b""
        for path in sorted(public_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(public_root).as_posix()
            info = zipfile.ZipInfo(
                f"public/{relative}",
                date_time=FIXED_ZIP_DATETIME,
            )
            info.create_system = 3
            info.external_attr = _zip_mode(path) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.extra = b""
            info.comment = b""
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def _load_script(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load verification script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "fresh extraction verification command failed: "
            + " ".join(command)
            + "\n"
            + result.stdout
            + result.stderr
        )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def verify_fresh_extraction(public_root: Path) -> dict[str, object]:
    """Run all release checks from a newly extracted public ZIP."""

    root = Path(public_root).resolve(strict=True)
    extracted_identity = _load_script(
        root / "src" / "tarms_experiments" / "release_identity.py",
        "_tarms_extracted_release_identity",
    )
    snapshot_report = extracted_identity.inspect_controlled_snapshot(
        root / CONTROLLED_SNAPSHOT_NAME
    )

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(root / "src")
    commands = [
        _run_checked(
            [
                sys.executable,
                "scripts/verify_public_release.py",
                "--manifest",
                PUBLIC_MANIFEST_NAME,
                "--project-root",
                ".",
            ],
            cwd=root,
            env=environment,
        ),
        _run_checked(
            [
                sys.executable,
                "scripts/verify_tree_manifest.py",
                "--project-root",
                ".",
            ],
            cwd=root,
            env=environment,
        ),
        _run_checked(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            cwd=root,
            env=environment,
        ),
    ]
    for package in ("fabric/chaincode", "fabric/client"):
        commands.append(
            _run_checked(
                [
                    "npm",
                    "--prefix",
                    package,
                    "ci",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ],
                cwd=root,
                env=environment,
            )
        )
        commands.append(
            _run_checked(
                ["npm", "--prefix", package, "test"],
                cwd=root,
                env=environment,
            )
        )
    for member in (
        "fabric/network/bootstrap.sh",
        "fabric/network/run_experiments.sh",
        "fabric/network/teardown.sh",
    ):
        commands.append(
            _run_checked(
                ["bash", "-n", member],
                cwd=root,
                env=environment,
            )
        )
    return {
        "commands": commands,
        "controlled_snapshot": snapshot_report,
        "status": "ok",
    }


def _safe_extract(zip_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if not names:
            raise ValueError("public ZIP is empty")
        for name in names:
            member = PurePosixPath(name)
            if (
                member.is_absolute()
                or ".." in member.parts
                or not member.parts
                or member.parts[0] != "public"
            ):
                raise ValueError(f"unsafe public ZIP member: {name}")
        archive.extractall(destination)
    root = destination / "public"
    if not root.is_dir():
        raise ValueError("public ZIP lacks the fixed public root")
    return root


def build_public_release(
    *,
    project_root: str | Path,
    run_dir: str | Path,
    controlled_snapshot: str | Path,
    test_report: str | Path,
    submission_figure_dir: str | Path,
    release_dir: str | Path,
    verification_runner: VerificationRunner = verify_fresh_extraction,
) -> dict[str, object]:
    """Build, verify, and publish a deterministic public directory and ZIP."""

    (
        root,
        run,
        snapshot,
        report_path,
        run_manifest,
        controlled,
        sealed_inputs,
    ) = _validate_inputs(
        Path(project_root),
        Path(run_dir),
        Path(controlled_snapshot),
        Path(test_report),
    )
    figures = _validate_submission_figures(
        project_root=root,
        figure_dir=Path(submission_figure_dir),
        run=run,
        run_manifest=run_manifest,
        controlled=controlled,
        sealed_inputs=sealed_inputs,
    )
    sealed_directories: dict[Path, DirectoryInventory] = {}
    for name in ROOT_FILES:
        _seal_file(
            root / name,
            label=f"required public file {name}",
            sealed_inputs=sealed_inputs,
            project_root=root,
        )
    for name in PUBLIC_DOC_FILES:
        _seal_file(
            root / "docs" / name,
            label=f"required public documentation {name}",
            sealed_inputs=sealed_inputs,
            project_root=root,
        )
    for name in SOURCE_DIRECTORIES:
        _seal_source_tree(
            root / name,
            sealed_inputs=sealed_inputs,
            sealed_directories=sealed_directories,
            project_root=root,
        )
    for name in NON_AAMOS_RESULT_DIRECTORIES:
        _seal_source_tree(
            root / name,
            sealed_inputs=sealed_inputs,
            sealed_directories=sealed_directories,
            project_root=root,
        )
    _seal_file(
        root / "data" / "README.md",
        label="data reproduction instructions",
        sealed_inputs=sealed_inputs,
        project_root=root,
    )
    final, final_zip = _reject_release_destination(
        root, Path(release_dir)
    )
    staging_container = Path(
        tempfile.mkdtemp(
            prefix=f".{final.name}.staging-",
            dir=final.parent,
        )
    )
    staged_public = staging_container / "public"
    staged_zip = staging_container / f"{final.name}.zip"
    try:
        staged_public.mkdir()
        for name in ROOT_FILES:
            _copy_file(
                root / name,
                staged_public / name,
                label=f"required public file {name}",
                sealed_inputs=sealed_inputs,
            )
        for name in PUBLIC_DOC_FILES:
            _copy_file(
                root / "docs" / name,
                staged_public / "docs" / name,
                label=f"required public documentation {name}",
                sealed_inputs=sealed_inputs,
            )
        for name in SOURCE_DIRECTORIES:
            _copy_source_tree(
                root / name,
                staged_public / name,
                sealed_inputs=sealed_inputs,
            )
        for name in NON_AAMOS_RESULT_DIRECTORIES:
            _copy_source_tree(
                root / name,
                staged_public / name,
                sealed_inputs=sealed_inputs,
            )
        _copy_file(
            root / "data" / "README.md",
            staged_public / "data" / "README.md",
            label="data reproduction instructions",
            sealed_inputs=sealed_inputs,
        )
        _copy_file(
            snapshot,
            staged_public / CONTROLLED_SNAPSHOT_NAME,
            label="controlled-source snapshot",
            sealed_inputs=sealed_inputs,
        )
        _copy_file(
            report_path,
            staged_public / TEST_REPORT_NAME,
            label="machine test report",
            sealed_inputs=sealed_inputs,
        )

        run_id = str(run_manifest["run_id"])
        public_run = (
            staged_public
            / "results"
            / "processed"
            / "aamos"
            / run_id
        )
        for name in PUBLIC_RUN_FILES:
            _copy_file(
                run / name,
                public_run / name,
                label=f"public formal artifact {name}",
                sealed_inputs=sealed_inputs,
            )
        public_figures = (
            staged_public / "results" / "figures" / "submission"
        )
        for name in SUBMISSION_FIGURE_FILES:
            _copy_file(
                figures / name,
                public_figures / name,
                label=f"submission figure artifact {name}",
                sealed_inputs=sealed_inputs,
            )

        _reject_legacy_markers(staged_public)
        run_member = (
            f"results/processed/aamos/{run_id}/run_manifest.json"
        )
        manifest = _public_manifest(
            staged_public,
            run_manifest,
            controlled,
            run_member,
        )
        _write_json(
            staged_public / PUBLIC_MANIFEST_NAME,
            manifest,
        )
        _write_tree_manifest(staged_public)
        _write_deterministic_zip(staged_public, staged_zip)

        with tempfile.TemporaryDirectory(
            prefix="tarms-public-extraction-"
        ) as extraction:
            extracted_root = _safe_extract(
                staged_zip, Path(extraction)
            )
            verification = dict(
                verification_runner(extracted_root)
            )
        if verification.get("status") not in (None, "ok"):
            raise RuntimeError("fresh extraction verification did not pass")
        _assert_inputs_unchanged(sealed_inputs)
        _assert_directories_unchanged(sealed_directories)
        if controlled_source_identity(root) != controlled["identity_sha256"]:
            raise ValueError(
                "controlled source changed during public release build"
            )

        _publish_verified_release(
            staged_public=staged_public,
            staged_zip=staged_zip,
            final=final,
            final_zip=final_zip,
        )
        return {
            "release_dir": str(final),
            "status": "ok",
            "verification": verification,
            "zip_path": str(final_zip),
            "zip_sha256": _sha256(final_zip),
        }
    finally:
        shutil.rmtree(staging_container, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and verify a deterministic TARMS public release."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--test-report", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = build_public_release(
        project_root=arguments.project_root,
        run_dir=arguments.run_dir,
        controlled_snapshot=arguments.snapshot,
        test_report=arguments.test_report,
        submission_figure_dir=arguments.figure_dir,
        release_dir=arguments.release_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
