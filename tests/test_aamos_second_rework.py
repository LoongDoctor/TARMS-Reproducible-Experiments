import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from aamos_submission_fixture import (  # noqa: E402
    submission_contract as _submission_contract,
)
from tarms_experiments import plotting  # noqa: E402
from tarms_experiments.aamos_experiment import (  # noqa: E402
    run_standard_enhanced_experiment,
)
from tarms_experiments.aamos_statistics import (  # noqa: E402
    analyze_experiment,
)


FIXED_CONFIG_HASH = (
    "f53b29941dccea72b53f8d1176745c8438d9df63a65f8774ecf9d84ff4caeeb0"
)


def _load_runner():
    path = PROJECT_ROOT / "scripts" / "run_aamos_standard_enhanced.py"
    spec = importlib.util.spec_from_file_location(
        "aamos_second_rework_runner", path
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("cannot load AAMOS runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(participants: int = 4, days: int = 6) -> pd.DataFrame:
    rows = []
    for participant in range(participants):
        for day in range(days):
            value = participant * days + day
            rows.append(
                {
                    "participant_id": f"P{participant}",
                    "relative_day": day,
                    "eligible": True,
                    "clean_priority": value % 4,
                    "payload_json": f'{{"row":{value}}}',
                }
            )
    return pd.DataFrame(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AamosDerivationBindingTests(unittest.TestCase):
    def _config(self):
        return yaml.safe_load(
            (
                PROJECT_ROOT / "config" / "aamos00_derivation.yaml"
            ).read_text(encoding="utf-8")
        )

    def _official_flow(self, config):
        official = config["official_release"]
        expected = config["analysis_derivation_expectations"]
        return {
            "participants": expected["participants"],
            "participant_days": expected[
                "daily_questionnaire_participant_days"
            ],
            "eligible_participant_days": expected[
                "eligible_three_item_days"
            ],
            "priority_counts": expected["priority_counts"],
            "source_files": [
                {"name": name, "sha256": digest}
                for name, digest in official[
                    "selected_analysis_source_sha256"
                ].items()
            ],
        }

    def test_submission_cli_rejects_nondefault_config_before_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = self._config()
            custom["payload"]["daily_columns"].remove(
                "daily_triggers"
            )
            custom_path = root / "self_declared_official.yaml"
            custom_path.write_text(
                yaml.safe_dump(custom, sort_keys=False),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        PROJECT_ROOT
                        / "scripts"
                        / "run_aamos_standard_enhanced.py"
                    ),
                    "--source-dir",
                    str(root / "missing-source"),
                    "--derivation-config",
                    str(custom_path),
                    "--output-root",
                    str(root / "results"),
                    "--profile",
                    "submission",
                    "--run-id",
                    "must-not-start",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "submission profile requires the fixed derivation config",
                completed.stderr,
            )
            self.assertNotIn("required AAMOS source is missing", completed.stderr)

    def test_submission_gate_does_not_trust_self_declared_official_release(self):
        runner = _load_runner()
        modified = self._config()
        flow = self._official_flow(modified)
        modified["payload"]["daily_columns"].remove("daily_triggers")
        with self.assertRaisesRegex(ValueError, "fixed derivation config"):
            runner._validate_official_source_flow(flow, modified)

    def test_preview_identity_and_code_archive_bind_actual_custom_config(self):
        runner = _load_runner()
        parameters = inspect.signature(
            runner._code_archive_hash
        ).parameters
        self.assertIn("derivation_config_path", parameters)
        self.assertTrue(hasattr(runner, "_derivation_config_identity"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = self._config()
            custom["payload"]["daily_columns"].remove("daily_triggers")
            custom_path = root / "custom_payload.yaml"
            custom_path.write_text(
                yaml.safe_dump(custom, sort_keys=False),
                encoding="utf-8",
            )
            identity = runner._derivation_config_identity(
                custom_path, custom
            )
            self.assertEqual(
                identity["derivation_config_member"],
                "runtime-config/custom_payload.yaml",
            )
            self.assertNotIn("derivation_config_path", identity)
            self.assertEqual(
                identity["derivation_config_basename"],
                custom_path.name,
            )
            self.assertEqual(
                identity["derivation_config_file_sha256"],
                _sha256(custom_path),
            )
            self.assertNotEqual(
                identity["derivation_config_canonical_sha256"],
                FIXED_CONFIG_HASH,
            )
            self.assertFalse(identity["fixed_submission_config"])
            self.assertNotEqual(
                runner._code_archive_hash(
                    derivation_config_path=custom_path
                ),
                runner._code_archive_hash(
                    derivation_config_path=(
                        PROJECT_ROOT
                        / "config"
                        / "aamos00_derivation.yaml"
                    )
                ),
            )

    def test_submission_source_gate_binds_fixed_config_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            plotting._validate_aamos_submission_source(
                source_path, manifest_path, source
            )
            self.assertNotIn(
                "derivation_config_path",
                json.dumps(manifest, sort_keys=True),
            )
            self.assertEqual(
                manifest["controlled_source"]["identity_sha256"],
                manifest["design"]["code_archive_sha256"],
            )

            source[
                "derivation_config_canonical_sha256"
            ] = "0" * 64
            source.to_csv(source_path, index=False)
            manifest[
                "design"
            ]["derivation_config_canonical_sha256"] = "0" * 64
            manifest["artifacts"][source_path.name] = _sha256(source_path)
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError, "fixed derivation config"
            ):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )


class AamosSubmissionSemanticGateTests(unittest.TestCase):
    def _write(
        self,
        source_path: Path,
        manifest_path: Path,
        source: pd.DataFrame,
        manifest: dict,
    ) -> None:
        source.to_csv(source_path, index=False)
        manifest["artifacts"][source_path.name] = _sha256(source_path)
        manifest_path.write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_probability_and_signed_difference_domains_are_enforced(self):
        cases = (
            ("attack_rejection", "probability metric domain"),
            ("pipeline_risk_difference", "signed difference domain"),
        )
        for metric, message in cases:
            with self.subTest(metric=metric), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_path, manifest_path, source, manifest = (
                    _submission_contract(root)
                )
                row = source.index[source["metric_id"].eq(metric)][0]
                source.loc[row, "numerator_n"] = 20
                source.loc[row, "denominator_N"] = 10
                source.loc[row, "estimate"] = 2.0
                source.loc[row, "ci_low"] = 1.5
                source.loc[row, "ci_high"] = 2.5
                self._write(
                    source_path, manifest_path, source, manifest
                )
                with self.assertRaisesRegex(ValueError, message):
                    plotting._validate_aamos_submission_source(
                        source_path, manifest_path, source
                    )

    def test_zero_denominator_requires_zero_numerator_and_missing_marks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            row = source.index[
                source["metric_id"].eq("attack_rejection")
            ][0]
            source.loc[row, "numerator_n"] = 1
            source.loc[row, "denominator_N"] = 0
            source.loc[row, ["estimate", "ci_low", "ci_high"]] = pd.NA
            source.loc[row, "bootstrap_repetitions_valid"] = 0
            source.loc[row, "bootstrap_repetitions_discarded"] = 2_000
            self._write(source_path, manifest_path, source, manifest)
            with self.assertRaisesRegex(ValueError, "zero denominator"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_signed_difference_reconciles_four_paired_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            row = source.index[
                source["metric_id"].eq("pipeline_risk_difference")
            ][0]
            source.loc[row, "both_reject_n"] = 9
            self._write(source_path, manifest_path, source, manifest)
            with self.assertRaisesRegex(ValueError, "four-cell"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_required_panels_and_exact_mark_cardinality_are_enforced(self):
        cases = (
            (
                lambda source: source.loc[
                    ~source["panel_id"].eq("d")
                ].copy(),
                "required panels",
            ),
            (
                lambda source: source.drop(
                    source.index[
                        source["metric_id"].eq("attack_rejection")
                    ][0]
                ),
                "mark cardinality",
            ),
        )
        for mutate, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_path, manifest_path, source, manifest = (
                    _submission_contract(root)
                )
                source = mutate(source)
                self._write(
                    source_path, manifest_path, source, manifest
                )
                with self.assertRaisesRegex(ValueError, message):
                    plotting._validate_aamos_submission_source(
                        source_path, manifest_path, source
                    )

    def test_duplicate_display_marks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            source = pd.concat(
                [source, source.iloc[[0]]], ignore_index=True
            )
            self._write(source_path, manifest_path, source, manifest)
            with self.assertRaisesRegex(
                ValueError, "duplicate display marks"
            ):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_source_bootstrap_fields_bind_to_manifest_design(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            manifest["design"]["bootstrap"]["repetitions"] = 1_999
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "design bootstrap"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_dataset_doi_and_source_inventory_are_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                _submission_contract(root)
            )
            source["dataset_doi"] = "10.0000/not-aamos"
            source["dataset_source_inventory_sha256"] = "0" * 64
            manifest["dataset"]["doi"] = "10.0000/not-aamos"
            manifest["dataset"]["source_inventory_sha256"] = "0" * 64
            self._write(source_path, manifest_path, source, manifest)
            with self.assertRaisesRegex(ValueError, "dataset contract"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )


class AamosPerSeedMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = run_standard_enhanced_experiment(
            _frame(),
            attack_scenarios=(
                "binding_mismatch",
                "historical_insertion",
            ),
            boundary_scenarios=(
                "permanent_omission",
                "incorrect_priority_rule",
            ),
            rates=(0.25,),
            seeds=(11, 12),
            boundary_rate=0.25,
        )
        cls.analysis = analyze_experiment(
            cls.tables,
            repetitions=40,
            master_seed=17,
            run_id="seed-local-metadata",
        )

    def _row(self, *, scenario, pipeline, metric, seed):
        rows = self.analysis.per_seed_metrics
        selection = rows.loc[
            rows["scenario"].fillna("<clean>").eq(scenario)
            & rows["pipeline"].eq(pipeline)
            & rows["metric_id"].eq(metric)
            & rows["seed"].eq(seed)
        ]
        self.assertEqual(len(selection), 1)
        return selection.iloc[0]

    def test_execution_metadata_is_seed_local_for_clean_attack_boundary(self):
        clean = self._row(
            scenario="<clean>",
            pipeline="all_checks",
            metric="clean_false_rejection",
            seed=11,
        )
        expected_clean = self.tables.clean_decisions.loc[
            self.tables.clean_decisions["pipeline"].eq("all_checks")
            & self.tables.clean_decisions["seed"].eq(11)
        ]
        self.assertEqual(
            clean["simulation_evaluations"], len(expected_clean)
        )
        self.assertEqual(clean["attempted_N"], len(expected_clean))
        self.assertEqual(clean["mutated_N"], 0)
        self.assertEqual(clean["evaluated_N"], len(expected_clean))

        for scenario, table in (
            ("binding_mismatch", self.tables.attack_decisions),
            ("permanent_omission", self.tables.boundary_decisions),
        ):
            row = self._row(
                scenario=scenario,
                pipeline="all_checks",
                metric=(
                    "attack_rejection"
                    if scenario == "binding_mismatch"
                    else "control_no_decision"
                ),
                seed=11,
            )
            expected = table.loc[
                table["scenario"].eq(scenario)
                & table["pipeline"].eq("all_checks")
                & table["seed"].eq(11)
            ]
            self.assertEqual(row["simulation_evaluations"], len(expected))
            self.assertEqual(
                row["attempted_N"],
                int(expected["attempted"].astype(bool).sum()),
            )
            self.assertEqual(
                row["mutated_N"],
                int(expected["mutated"].astype(bool).sum()),
            )
            self.assertEqual(
                row["evaluated_N"],
                int(expected["evaluated"].astype(bool).sum()),
            )
            self.assertEqual(
                row["execution_count_scope"],
                "single_injection_seed",
            )

    def test_unique_attacked_and_mixed_population_counts_are_seed_local(self):
        for seed in (11, 12):
            manifest = self.tables.injection_manifest.loc[
                self.tables.injection_manifest["scenario"].eq(
                    "binding_mismatch"
                )
                & self.tables.injection_manifest["seed"].eq(seed)
            ]
            attack = self._row(
                scenario="binding_mismatch",
                pipeline="all_checks",
                metric="attack_rejection",
                seed=seed,
            )
            self.assertEqual(
                attack["unique_attacked_participant_days"],
                manifest["record_key"].nunique(),
            )

            coverage = self._row(
                scenario="binding_mismatch",
                pipeline="all_checks",
                metric="coverage",
                seed=seed,
            )
            eligible = self.tables.clean_decisions.loc[
                self.tables.clean_decisions["pipeline"].eq("all_checks")
                & self.tables.clean_decisions["seed"].eq(seed)
            ]
            self.assertEqual(
                coverage["simulation_evaluations"], len(eligible)
            )
            self.assertEqual(
                coverage["attempted_N"], len(manifest)
            )

        history = self._row(
            scenario="historical_insertion",
            pipeline="all_checks",
            metric="attack_rejection",
            seed=11,
        )
        history_manifest = self.tables.injection_manifest.loc[
            self.tables.injection_manifest["scenario"].eq(
                "historical_insertion"
            )
            & self.tables.injection_manifest["seed"].eq(11)
        ]
        self.assertEqual(
            history["unique_attacked_participant_days"],
            history_manifest[
                ["participant_id", "history_affected_key"]
            ].drop_duplicates().shape[0],
        )

    def test_pooled_summary_metadata_is_explicit_and_reconciles_seed_rows(self):
        keys = {
            "scenario": "binding_mismatch",
            "pipeline": "all_checks",
            "metric_id": "coverage",
        }
        per_seed = self.analysis.per_seed_metrics
        selected = per_seed.loc[
            per_seed["scenario"].eq(keys["scenario"])
            & per_seed["pipeline"].eq(keys["pipeline"])
            & per_seed["metric_id"].eq(keys["metric_id"])
        ]
        summary = self.analysis.summary
        pooled = summary.loc[
            summary["scenario"].eq(keys["scenario"])
            & summary["pipeline"].eq(keys["pipeline"])
            & summary["metric_id"].eq(keys["metric_id"])
        ]
        self.assertEqual(len(pooled), 1)
        pooled = pooled.iloc[0]
        self.assertIn("execution_count_scope", pooled.index)
        self.assertEqual(
            pooled["execution_count_scope"],
            "pooled_fixed_seed_set",
        )
        for column in (
            "simulation_evaluations",
            "attempted_N",
            "mutated_N",
            "evaluated_N",
        ):
            self.assertEqual(
                pooled[column],
                selected[column].sum(),
                column,
            )
