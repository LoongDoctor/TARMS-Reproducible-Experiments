"""Tests for deterministic, privacy-safe public release construction."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from pathlib import PurePosixPath
import zipfile

import pandas as pd

from tarms_experiments import release_identity
from tarms_experiments.aamos_experiment import ATTACK_RATES, PIPELINES
from tarms_experiments.aamos_scenarios import (
    BOUNDARY_SCENARIOS,
    REJECT_SCENARIOS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = PROJECT_ROOT / "scripts" / "build_public_release.py"
PUBLIC_RUN_FILES = (
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
FIGURE_FILES = (
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
FORBIDDEN_MEMBERS = (
    "node_modules/secret.js",
    ".venv/secret.py",
    "data/aamos00/anonym_aamos00_dailyquestionnaire.csv",
    "results/processed/aamos/formal-v6/attack_decisions.csv",
    "results/processed/aamos/formal-v6/patient_days.csv",
    "docs/AAMOS_RESULTS_AUDIT.md",
    "docs/EXPERIMENT_EVIDENCE_REGISTER.md",
    ".partial-output",
)


def _load_script(name: str):
    path = PROJECT_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_project_fixture(destination: Path) -> None:
    destination.mkdir(parents=True)
    for name in (
        "LICENSE",
        "CITATION.cff",
        "README.md",
        "README_zh.md",
        "Makefile",
        "pyproject.toml",
        "requirements-lock.txt",
    ):
        shutil.copy2(PROJECT_ROOT / name, destination / name)
    for name in ("config", "docs", "scripts", "src", "tests"):
        shutil.copytree(PROJECT_ROOT / name, destination / name)
    shutil.copytree(
        PROJECT_ROOT / "fabric",
        destination / "fabric",
        ignore=shutil.ignore_patterns("node_modules", "__pycache__"),
    )
    (destination / "data").mkdir()
    shutil.copy2(
        PROJECT_ROOT / "data" / "README.md",
        destination / "data" / "README.md",
    )


def _fixture_fig6_source(
    *,
    run_id: str,
    controlled_identity: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(
        panel: str,
        metric: str,
        scenario: str | None,
        rate: float,
        pipeline: str,
        comparator: str | None = None,
    ) -> None:
        rows.append(
            {
                "panel_id": panel,
                "metric_id": metric,
                "scenario": scenario,
                "rate_requested": rate,
                "pipeline": pipeline,
                "comparator_pipeline": comparator,
                "run_id": run_id,
                "code_commit_or_archive_hash": controlled_identity,
                "estimate": 0.0,
            }
        )

    for scenario in REJECT_SCENARIOS:
        for pipeline in PIPELINES:
            add("a", "attack_rejection", scenario, 0.10, pipeline)
    for scenario in BOUNDARY_SCENARIOS:
        add("a", "control_rejection", scenario, 0.10, "all_checks")
    for pipeline in PIPELINES:
        add("a", "clean_false_rejection", None, 0.0, pipeline)
    for scenario, stage in REJECT_SCENARIOS.items():
        comparator = (
            "all_minus_freshness"
            if stage == "history"
            else f"all_minus_{stage}"
        )
        add(
            "b",
            "expected_stage_agreement",
            scenario,
            0.10,
            "all_checks",
        )
        add(
            "b",
            "pipeline_risk_difference",
            scenario,
            0.10,
            "all_checks",
            comparator,
        )
    for rate in ATTACK_RATES:
        for metric in ("coverage", "abstention"):
            add("c", metric, "mixed_attack", rate, "all_checks")
        for metric in (
            "covered_agreement",
            "upward_discordance",
            "priority_loss_discordance",
        ):
            add("d", metric, "mixed_attack", rate, "all_checks")
    return pd.DataFrame(rows)


def _make_fixture(base: Path) -> dict[str, Path]:
    project = base / "project"
    _copy_project_fixture(project)

    (project / "node_modules").mkdir()
    (project / "node_modules" / "secret.js").write_text(
        "do not publish\n", encoding="utf-8"
    )
    (project / ".venv").mkdir()
    (project / ".venv" / "secret.py").write_text(
        "do not publish\n", encoding="utf-8"
    )
    raw = project / "data" / "aamos00"
    raw.mkdir()
    (raw / "anonym_aamos00_dailyquestionnaire.csv").write_text(
        "participant_id,private\nP01,yes\n", encoding="utf-8"
    )
    (project / ".partial-output").write_text(
        "incomplete\n", encoding="utf-8"
    )
    for name in (
        "results/raw/python",
        "results/raw/python_components",
        "results/raw/python_conformance",
        "results/processed/python",
        "results/processed/python_components",
        "results/processed/python_conformance",
    ):
        source = PROJECT_ROOT / name
        shutil.copytree(source, project / name)

    snapshot = base / "controlled-source.zip"
    snapshot_record = release_identity.write_controlled_snapshot(
        project, snapshot
    )
    controlled = {
        "identity_sha256": snapshot_record["identity_sha256"],
        "snapshot_sha256": snapshot_record["snapshot_sha256"],
        "member_count": snapshot_record["member_count"],
        "derivation_config_member": "config/aamos00_derivation.yaml",
    }

    run = project / "results" / "processed" / "aamos" / "formal-v6"
    run.mkdir(parents=True)
    artifacts: dict[str, str] = {}
    for name in (*PUBLIC_RUN_FILES, *PRIVATE_RUN_FILES):
        path = run / name
        if path.suffix == ".json":
            path.write_text('{"eligible_participant_days": 1582}\n')
        elif name == "fig_aamos_protocol_integrity_source_data.csv":
            source = _fixture_fig6_source(
                run_id="formal-v6",
                controlled_identity=str(controlled["identity_sha256"]),
            )
            source.to_csv(path, index=False, lineterminator="\n")
        else:
            path.write_text(f"name,value\n{name},1\n", encoding="utf-8")
        artifacts[name] = _sha256(path)
    run_manifest = {
        "schema_version": "1.0",
        "run_id": "formal-v6",
        "controlled_source": controlled,
        "design": {
            "code_archive_sha256": controlled["identity_sha256"],
        },
        "artifacts": artifacts,
    }
    _write_json(run / "run_manifest.json", run_manifest)

    figures = base / "v6-figures"
    figures.mkdir(parents=True)
    for name in FIGURE_FILES:
        path = figures / name
        if name == "fig_06_aamos_protocol_integrity_source_data.csv":
            shutil.copy2(
                run / "fig_aamos_protocol_integrity_source_data.csv",
                path,
            )
        else:
            path.write_bytes(f"deterministic {name}\n".encode())

    test_report = base / "test-report.json"
    _write_json(
        test_report,
        {
            "status": "passed",
            "controlled_source_identity_sha256": controlled[
                "identity_sha256"
            ],
            "controlled_source_identity_stable": True,
            "environment": {
                "node_version": "v22.22.2",
                "node_version_valid": True,
                "npm_version": "10.9.7",
                "npm_version_valid": True,
                "python_version": "3.12.11",
            },
            "suites": {
                name: {
                    "command": [name, "test"],
                    "count_evidence_valid": True,
                    "exit_code": 0,
                    "failed_count": 0,
                    "observed_passed_count": count,
                    "skipped_count": 0,
                    "stderr": "",
                    "stdout": "ok\n",
                }
                for name, count in (
                    ("python", 1),
                    ("chaincode", 1),
                    ("client", 1),
                    ("shell", 3),
                )
            },
            "totals": {
                "failed_count": 0,
                "observed_passed_count": 6,
                "skipped_count": 0,
            },
        },
    )
    return {
        "project": project,
        "figures": figures,
        "run": run,
        "snapshot": snapshot,
        "test_report": test_report,
    }


class PublicReleaseBuilderTests(unittest.TestCase):
    def test_excludes_private_inputs_and_builds_identical_fresh_zips(self):
        builder = _load_script("build_public_release.py")
        verifier = _load_script("verify_public_release.py")
        tree_verifier = _load_script("verify_tree_manifest.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            extracted_roots: list[Path] = []

            def verify_extraction(root: Path) -> dict[str, object]:
                extracted_roots.append(root)
                public = verifier.verify_public_release(
                    root / "public_release_manifest.json", root
                )
                tree = tree_verifier.verify_tree_manifest(root)
                return {"public": public, "tree": tree}

            first = builder.build_public_release(
                project_root=fixture["project"],
                run_dir=fixture["run"],
                controlled_snapshot=fixture["snapshot"],
                test_report=fixture["test_report"],
                submission_figure_dir=fixture["figures"],
                release_dir=base / "left" / "public",
                verification_runner=verify_extraction,
            )
            second = builder.build_public_release(
                project_root=fixture["project"],
                run_dir=fixture["run"],
                controlled_snapshot=fixture["snapshot"],
                test_report=fixture["test_report"],
                submission_figure_dir=fixture["figures"],
                release_dir=base / "right" / "public",
                verification_runner=verify_extraction,
            )

            self.assertEqual(2, len(extracted_roots))
            self.assertTrue(
                all(root.name == "public" for root in extracted_roots)
            )
            self.assertEqual(
                Path(first["zip_path"]).read_bytes(),
                Path(second["zip_path"]).read_bytes(),
            )
            release_root = Path(first["release_dir"])
            manifest = json.loads(
                (release_root / "public_release_manifest.json").read_text()
            )
            manifest_text = json.dumps(manifest, sort_keys=True)
            tree_text = (release_root / "MANIFEST.sha256").read_text()
            with zipfile.ZipFile(first["zip_path"]) as archive:
                zip_members = tuple(archive.namelist())
            self.assertTrue(
                all(
                    PurePosixPath(member).parts[0] == "public"
                    for member in zip_members
                )
            )
            relative_zip_members = {
                PurePosixPath(*PurePosixPath(member).parts[1:]).as_posix()
                for member in zip_members
            }

            for forbidden in FORBIDDEN_MEMBERS:
                self.assertFalse((release_root / forbidden).exists())
                self.assertNotIn(forbidden, manifest_text)
                self.assertNotIn(forbidden, relative_zip_members)
            self.assertTrue(
                (
                    release_root / "docs" / "AAMOS_EXPERIMENT_PROTOCOL.md"
                ).is_file()
            )
            self.assertNotIn(
                "public_release_manifest.json",
                manifest["public_artifacts"],
            )
            self.assertEqual(
                "results/processed/aamos/formal-v6/run_manifest.json",
                manifest["formal_run_manifest_member"],
            )
            self.assertIn(
                "public_release_manifest.json",
                tree_text,
            )
            self.assertFalse(
                (
                    release_root
                    / "results/processed/aamos/formal-v6"
                    / "public_release_manifest.json"
                ).exists()
            )
            for name in PUBLIC_RUN_FILES:
                self.assertTrue(
                    (
                        release_root
                        / "results/processed/aamos/formal-v6"
                        / name
                    ).is_file()
                )
            for name in FIGURE_FILES:
                self.assertTrue(
                    (
                        release_root
                        / "results/figures/submission"
                        / name
                    ).is_file()
                )

    def test_rejects_invalid_report_and_symlinked_release_inputs(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            report = json.loads(
                fixture["test_report"].read_text(encoding="utf-8")
            )
            report["status"] = "failed"
            _write_json(fixture["test_report"], report)

            with self.assertRaisesRegex(ValueError, "status"):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=base / "bad-report" / "public",
                    verification_runner=lambda root: {"status": "ok"},
                )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            figure = fixture["figures"] / "fig_03_python_benchmarks.pdf"
            outside = base / "outside.pdf"
            outside.write_bytes(figure.read_bytes())
            figure.unlink()
            figure.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "symlink"):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=base / "symlink" / "public",
                    verification_runner=lambda root: {"status": "ok"},
                )

    def test_json_input_mutation_after_parse_is_rejected(self):
        builder = _load_script("build_public_release.py")
        for target_label in (
            "formal run manifest",
            "machine test report",
        ):
            with self.subTest(target_label=target_label):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    fixture = _make_fixture(base)
                    final = base / "json-race" / "public"
                    original_parser = builder._strict_json

                    def parse_then_mutate(path: Path, **kwargs):
                        value = original_parser(path, **kwargs)
                        if kwargs.get("label") == target_label:
                            path.write_text(
                                '{"status":"failed-after-parse"}\n',
                                encoding="utf-8",
                            )
                        return value

                    builder._strict_json = parse_then_mutate
                    with self.assertRaisesRegex(
                        ValueError, "changed during build"
                    ):
                        builder.build_public_release(
                            project_root=fixture["project"],
                            run_dir=fixture["run"],
                            controlled_snapshot=fixture["snapshot"],
                            test_report=fixture["test_report"],
                            submission_figure_dir=fixture["figures"],
                            release_dir=final,
                            verification_runner=lambda root: {
                                "status": "ok"
                            },
                        )
                    self.assertFalse(final.exists())
                    self.assertFalse(final.with_suffix(".zip").exists())
                    builder._strict_json = original_parser

    def test_project_input_parent_symlink_is_rejected(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            external_docs = base / "external-docs"
            external_docs.mkdir()
            (
                external_docs / "AAMOS_EXPERIMENT_PROTOCOL.md"
            ).write_text(
                "# external content that must not be published\n",
                encoding="utf-8",
            )
            shutil.rmtree(fixture["project"] / "docs")
            (fixture["project"] / "docs").symlink_to(
                external_docs,
                target_is_directory=True,
            )

            with self.assertRaisesRegex(
                ValueError, "project input.*symlink|outside project root"
            ):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=base / "parent-symlink" / "public",
                    verification_runner=lambda root: {"status": "ok"},
                )

    def test_rejects_fig6_source_that_is_not_bound_to_formal_run(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            figure_source = (
                fixture["figures"]
                / "fig_06_aamos_protocol_integrity_source_data.csv"
            )
            source = pd.read_csv(figure_source)
            source.loc[0, "estimate"] = 0.25
            source.to_csv(
                figure_source,
                index=False,
                lineterminator="\n",
            )

            with self.assertRaisesRegex(
                ValueError, "Fig. 6 source.*formal"
            ):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=base / "unbound-figure" / "public",
                    verification_runner=lambda root: {"status": "ok"},
                )

    def test_figure_mutation_after_binding_validation_is_rejected(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            final = base / "figure-race" / "public"
            original_validation = builder._validate_submission_figures

            def validate_then_mutate(**kwargs):
                figures = original_validation(**kwargs)
                (
                    fixture["figures"]
                    / "fig_03_python_benchmarks_source_data.csv"
                ).write_text(
                    "changed after figure binding\n",
                    encoding="utf-8",
                )
                return figures

            builder._validate_submission_figures = validate_then_mutate
            with self.assertRaisesRegex(ValueError, "changed during build"):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=final,
                    verification_runner=lambda root: {"status": "ok"},
                )
            self.assertFalse(final.exists())
            self.assertFalse(final.with_suffix(".zip").exists())

    def test_input_mutation_during_verification_prevents_publication(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            final = base / "mutation" / "public"

            def mutate_input(root: Path) -> dict[str, object]:
                (fixture["run"] / "patient_days.csv").write_text(
                    "changed during verification\n",
                    encoding="utf-8",
                )
                return {"status": "ok"}

            with self.assertRaisesRegex(ValueError, "changed during build"):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=final,
                    verification_runner=mutate_input,
                )

            self.assertFalse(final.exists())
            self.assertFalse(final.with_suffix(".zip").exists())

    def test_copied_input_mutation_during_verification_prevents_publication(
        self,
    ):
        builder = _load_script("build_public_release.py")
        mutation_cases = (
            (
                "readme",
                lambda fixture: fixture["project"] / "README.md",
                "changed public documentation\n",
            ),
            (
                "result",
                lambda fixture: (
                    fixture["project"]
                    / "results"
                    / "processed"
                    / "python"
                    / "python-20260723T020649Z"
                    / "python_microbenchmark_summary.csv"
                ),
                "changed aggregate result\n",
            ),
            (
                "figure",
                lambda fixture: (
                    fixture["figures"]
                    / "fig_03_python_benchmarks_source_data.csv"
                ),
                "changed figure source\n",
            ),
        )
        for label, locate, replacement in mutation_cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    fixture = _make_fixture(base)
                    final = base / label / "public"

                    def mutate_input(root: Path) -> dict[str, object]:
                        locate(fixture).write_text(
                            replacement,
                            encoding="utf-8",
                        )
                        return {"status": "ok"}

                    with self.assertRaisesRegex(
                        ValueError, "changed during build"
                    ):
                        builder.build_public_release(
                            project_root=fixture["project"],
                            run_dir=fixture["run"],
                            controlled_snapshot=fixture["snapshot"],
                            test_report=fixture["test_report"],
                            submission_figure_dir=fixture["figures"],
                            release_dir=final,
                            verification_runner=mutate_input,
                        )
                    self.assertFalse(final.exists())
                    self.assertFalse(final.with_suffix(".zip").exists())

    def test_source_tree_membership_mutation_prevents_publication(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            final = base / "membership" / "public"

            def add_source_member(root: Path) -> dict[str, object]:
                (fixture["project"] / "src" / "unexpected.py").write_text(
                    "raise RuntimeError('not frozen')\n",
                    encoding="utf-8",
                )
                return {"status": "ok"}

            with self.assertRaisesRegex(
                ValueError, "directory changed during build"
            ):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=final,
                    verification_runner=add_source_member,
                )
            self.assertFalse(final.exists())
            self.assertFalse(final.with_suffix(".zip").exists())

    def test_destination_appearing_during_verification_is_not_overwritten(
        self,
    ):
        builder = _load_script("build_public_release.py")
        for target_kind in ("directory", "zip"):
            with self.subTest(target_kind=target_kind):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    fixture = _make_fixture(base)
                    final = base / target_kind / "public"
                    sentinel: dict[str, object] = {}

                    def create_sentinel(root: Path) -> dict[str, object]:
                        final.parent.mkdir(parents=True, exist_ok=True)
                        if target_kind == "zip":
                            path = final.with_suffix(".zip")
                            path.write_bytes(b"do not overwrite\n")
                        else:
                            path = final
                            path.mkdir()
                        sentinel["path"] = path
                        sentinel["inode"] = path.stat().st_ino
                        return {"status": "ok"}

                    with self.assertRaisesRegex(
                        FileExistsError,
                        "destination appeared during verification",
                    ):
                        builder.build_public_release(
                            project_root=fixture["project"],
                            run_dir=fixture["run"],
                            controlled_snapshot=fixture["snapshot"],
                            test_report=fixture["test_report"],
                            submission_figure_dir=fixture["figures"],
                            release_dir=final,
                            verification_runner=create_sentinel,
                        )
                    path = sentinel["path"]
                    assert isinstance(path, Path)
                    self.assertEqual(sentinel["inode"], path.stat().st_ino)
                    if target_kind == "zip":
                        self.assertEqual(
                            b"do not overwrite\n",
                            path.read_bytes(),
                        )

    def test_verification_failure_publishes_neither_directory_nor_zip(self):
        builder = _load_script("build_public_release.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = _make_fixture(base)
            final = base / "release" / "public"

            def fail_verification(root: Path) -> dict[str, object]:
                self.assertTrue(
                    (root / "public_release_manifest.json").is_file()
                )
                raise RuntimeError("fresh extraction verification failed")

            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                builder.build_public_release(
                    project_root=fixture["project"],
                    run_dir=fixture["run"],
                    controlled_snapshot=fixture["snapshot"],
                    test_report=fixture["test_report"],
                    submission_figure_dir=fixture["figures"],
                    release_dir=final,
                    verification_runner=fail_verification,
                )

            self.assertFalse(final.exists())
            self.assertFalse(final.with_suffix(".zip").exists())
            self.assertEqual([], list(final.parent.glob(".*.staging-*")))


if __name__ == "__main__":
    unittest.main()
