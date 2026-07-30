import hashlib
import importlib.util
import inspect
import json
import resource
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from aamos_submission_fixture import submission_contract  # noqa: E402
from tarms_experiments import aamos_statistics as statistics  # noqa: E402
from tarms_experiments import plotting  # noqa: E402
from tarms_experiments.aamos_experiment import (  # noqa: E402
    METRIC_DEFINITION_VERSION,
    PIPELINES,
    run_standard_enhanced_experiment,
)


def _load_runner():
    path = PROJECT_ROOT / "scripts" / "run_aamos_standard_enhanced.py"
    spec = importlib.util.spec_from_file_location("aamos_standard_runner", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
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


class AamosHistoryEstimandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = run_standard_enhanced_experiment(
            _frame(),
            attack_scenarios=(
                "historical_modification",
                "historical_deletion",
                "historical_insertion",
            ),
            boundary_scenarios=("incorrect_priority_rule",),
            rates=(0.50,),
            seeds=(11,),
            boundary_rate=0.50,
        )
        cls.analysis = statistics.analyze_experiment(
            cls.tables,
            repetitions=80,
            master_seed=17,
            run_id="history-estimand",
        )

    def test_history_operations_are_explicitly_operation_level(self):
        self.assertEqual(
            METRIC_DEFINITION_VERSION, "aamos-integrity-v4"
        )
        manifest = self.tables.injection_manifest
        history = manifest.loc[
            manifest["scenario"].isin(
                {
                    "historical_modification",
                    "historical_deletion",
                    "historical_insertion",
                }
            )
        ]
        self.assertEqual(set(history["mixed_metric_applicable"]), {False})
        self.assertEqual(
            set(history["estimand"]),
            {"operation_level_protocol_transition"},
        )
        self.assertEqual(set(history["joint_deployment"]), {False})

        history_summary = self.analysis.summary.loc[
            self.analysis.summary["scenario"].isin(
                set(history["scenario"])
            )
        ]
        allowed = {
            "attack_rejection",
            "expected_stage_agreement",
            "conditional_stage_attribution",
            "pipeline_risk_difference",
        }
        self.assertTrue(set(history_summary["metric_id"]).issubset(allowed))
        self.assertFalse(
            history_summary["metric_id"]
            .isin(
                {
                    "coverage",
                    "abstention",
                    "covered_agreement",
                    "upward_discordance",
                    "priority_loss_discordance",
                    "attack_clean_risk_difference",
                }
            )
            .any()
        )

    def test_materialized_deletion_and_insertion_do_not_restore_target_output(self):
        decisions = self.tables.attack_decisions
        for scenario in ("historical_deletion", "historical_insertion"):
            accepted = decisions.loc[
                (decisions["scenario"] == scenario)
                & (decisions["pipeline"] == "unverified")
                & decisions["accepted"].astype(bool)
            ]
            self.assertFalse(accepted.empty)
            pd.testing.assert_series_equal(
                accepted["output_priority"].reset_index(drop=True),
                accepted["observed_priority"]
                .astype(float)
                .reset_index(drop=True),
                check_names=False,
            )

    def test_insertion_alternatives_share_actual_operation_identity(self):
        insertion = self.tables.injection_manifest.loc[
            self.tables.injection_manifest["scenario"]
            == "historical_insertion"
        ]
        repeated = insertion.loc[
            insertion["participant_id"].duplicated(keep=False)
        ]
        self.assertFalse(repeated.empty)
        for _, group in repeated.groupby("participant_id"):
            self.assertEqual(group["history_affected_key"].nunique(), 1)
            self.assertEqual(group["operation_identity"].nunique(), 1)
        self.assertLess(
            insertion["operation_identity"].nunique(),
            len(insertion),
        )

    def test_incorrect_priority_rule_outputs_mutated_envelope_priority(self):
        rows = self.tables.boundary_decisions.loc[
            (self.tables.boundary_decisions["scenario"]
             == "incorrect_priority_rule")
            & self.tables.boundary_decisions["accepted"].astype(bool)
        ]
        self.assertFalse(rows.empty)
        self.assertTrue(
            (
                rows["output_priority"].astype(float)
                == rows["observed_priority"].astype(float)
            ).all()
        )
        self.assertTrue(
            (
                rows["observed_priority"].astype(int)
                != rows["clean_priority"].astype(int)
            ).all()
        )

    def test_decision_tables_are_narrow_and_build_blocks_does_not_copy_inputs(self):
        manifest_only = {
            "target_count_rule",
            "target_stratum",
            "target_ordinal",
            "eligible_N",
            "history_before_root",
            "history_after_root",
        }
        self.assertTrue(
            manifest_only.isdisjoint(self.tables.attack_decisions.columns)
        )
        decision_bytes_per_row = (
            self.tables.attack_decisions.memory_usage(deep=True).sum()
            / len(self.tables.attack_decisions)
        )
        self.assertLess(decision_bytes_per_row, 1_800)
        source = inspect.getsource(statistics._build_blocks)
        self.assertNotIn("tables.clean_decisions.copy()", source)
        self.assertNotIn("tables.attack_decisions.copy()", source)
        self.assertNotIn("tables.boundary_decisions.copy()", source)


class AamosBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = run_standard_enhanced_experiment(
            _frame(),
            attack_scenarios=("binding_mismatch", "mixed_attack"),
            boundary_scenarios=("permanent_omission",),
            rates=(0.25,),
            seeds=(11, 12),
            boundary_rate=0.25,
        )

    def test_run_id_is_provenance_only_and_does_not_change_statistics(self):
        left = statistics.analyze_experiment(
            self.tables,
            repetitions=120,
            master_seed=17,
            run_id="rerun-a",
        ).summary
        right = statistics.analyze_experiment(
            self.tables,
            repetitions=120,
            master_seed=17,
            run_id="rerun-b",
        ).summary
        order = [
            "scenario",
            "rate_requested",
            "pipeline",
            "comparator_pipeline",
            "metric_id",
        ]
        left = left.sort_values(order, na_position="first").reset_index(
            drop=True
        )
        right = right.sort_values(order, na_position="first").reset_index(
            drop=True
        )
        self.assertEqual(set(left["run_id"]), {"rerun-a"})
        self.assertEqual(set(right["run_id"]), {"rerun-b"})
        self.assertEqual(
            left.drop(columns="run_id").to_csv(index=False),
            right.drop(columns="run_id").to_csv(index=False),
        )

    def test_production_occurrence_aggregation_matches_slow_reference_exactly(self):
        seed_draws = np.array([[0, 0], [1, 0]], dtype=np.int64)
        participant_draws = np.array(
            [
                [0, 1],
                [0, 0],
            ],
            dtype=np.int64,
        )
        actual = statistics._multiplicities_from_crossed_occurrence_draws(
            seed_draws,
            participant_draws,
            seed_count=2,
            participant_count=2,
        )
        expected = np.zeros((2, 2, 2), dtype=np.int64)
        for replicate in range(seed_draws.shape[0]):
            seed_counts = np.bincount(
                seed_draws[replicate], minlength=2
            )
            participant_counts = np.bincount(
                participant_draws[replicate], minlength=2
            )
            expected[replicate] = np.outer(
                seed_counts, participant_counts
            )
        np.testing.assert_array_equal(actual, expected)

    def test_production_occurrence_identity_has_exact_small_pmf(self):
        counts = {}
        for bit_pattern in range(2**2):
            draws = np.array(
                [
                    [
                        (bit_pattern >> position) & 1
                        for position in range(2)
                    ]
                ],
                dtype=np.int64,
            )
            weights = statistics._multiplicities_from_crossed_occurrence_draws(
                np.array([[0, 0]], dtype=np.int64),
                draws,
                seed_count=1,
                participant_count=2,
            )
            key = tuple(weights[0, 0])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(
            counts,
            {(4, 0): 1, (2, 2): 2, (0, 4): 1},
        )

    def test_production_block_uses_one_shared_weight_tensor(self):
        numerator = np.array(
            [
                [[1.0, 0.0, 1.0], [0.0, 0.0, 0.0]],
                [[1.0, 1.0, 0.0], [1.0, 0.0, 1.0]],
            ]
        )
        denominator = np.ones_like(numerator)
        block = statistics._AnalysisBlock(
            stable_key="shared",
            seeds=(11, 12),
            participants=("P0", "P1"),
            metadata=[{}, {}, {}],
            numerator=numerator,
            denominator=denominator,
        )
        samples = statistics._bootstrap_block_samples(
            block,
            repetitions=50,
            master_seed=17,
        )
        np.testing.assert_allclose(
            samples[:, 2], samples[:, 0] - samples[:, 1]
        )

    def test_official_geometry_2000_rep_sampler_and_ratio_smoke(self):
        runner = _load_runner()
        started = time.perf_counter()
        rss_before_kib = runner._ru_maxrss_to_kib(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        )
        rng = np.random.default_rng(99)
        numerator = rng.integers(
            0, 8, size=(20, 22, 110)
        ).astype(float)
        denominator = numerator + rng.integers(
            1, 8, size=numerator.shape
        )
        for block_index in range(63):
            block = statistics._AnalysisBlock(
                stable_key=f"official-capacity-{block_index}",
                seeds=tuple(range(20)),
                participants=tuple(
                    f"P{participant}" for participant in range(22)
                ),
                metadata=[{}] * 110,
                numerator=numerator,
                denominator=denominator,
            )
            samples = statistics._bootstrap_block_samples(
                block,
                repetitions=2_000,
                master_seed=20260722,
            )
        elapsed = time.perf_counter() - started
        rss_after_kib = runner._ru_maxrss_to_kib(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        )
        self.assertEqual(samples.shape, (2_000, 110))
        self.assertLess(elapsed, 30.0)
        self.assertLess(
            rss_after_kib - rss_before_kib,
            768 * 1024,
        )


class AamosCardinalityAndSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = run_standard_enhanced_experiment(
            _frame(),
            attack_scenarios=(
                "binding_mismatch",
                "mixed_attack",
                "historical_deletion",
                "historical_insertion",
            ),
            boundary_scenarios=(
                "permanent_omission",
                "canonical_reorder",
                "incorrect_priority_rule",
            ),
            rates=(0.25,),
            seeds=(11, 12),
            boundary_rate=0.50,
        )
        cls.analysis = statistics.analyze_experiment(
            cls.tables,
            repetitions=80,
            master_seed=17,
            run_id="source-run",
        )
        cls.source = statistics.build_figure_source_data(
            cls.analysis.summary,
            cls.analysis.paired_contrasts,
            run_id="source-run",
            created_utc="2026-07-23T00:00:00Z",
            code_commit_or_archive_hash="code-hash",
            bootstrap_master_seed=17,
            derivation_config_identity={
                "derivation_config_basename": "fixture.yaml",
                "derivation_config_file_sha256": "f" * 64,
                "derivation_config_canonical_sha256": "c" * 64,
            },
            dataset_identity={
                "dataset_name": "AAMOS-00",
                "dataset_doi": "10.7488/ds/3775",
                "dataset_source_inventory_sha256": "d" * 64,
            },
        )

    def test_boundary_manifest_and_decisions_reconcile_symmetrically(self):
        manifest = self.tables.boundary_manifest
        decisions = self.tables.boundary_decisions
        self.assertFalse(manifest["pair_key"].duplicated().any())
        counts = decisions.groupby("pair_key").size()
        self.assertEqual(set(counts.index), set(manifest["pair_key"]))
        self.assertTrue((counts == len(PIPELINES)).all())
        evaluated = decisions["evaluated"].astype(bool)
        self.assertTrue(
            decisions.loc[evaluated, "accepted"].notna().all()
        )
        self.assertTrue(
            decisions.loc[~evaluated, "accepted"].isna().all()
        )

        broken = type(self.tables)(
            clean_decisions=self.tables.clean_decisions,
            injection_manifest=self.tables.injection_manifest,
            attack_decisions=self.tables.attack_decisions,
            boundary_manifest=manifest.iloc[:-1].copy(),
            boundary_decisions=decisions,
        )
        with self.assertRaisesRegex(ValueError, "boundary manifest"):
            statistics.analyze_experiment(
                broken,
                repetitions=10,
                master_seed=1,
                run_id="broken",
            )
        broken_decisions = decisions.copy()
        broken_decisions.loc[
            broken_decisions.index[0], "attempted"
        ] = False
        broken_flags = type(self.tables)(
            clean_decisions=self.tables.clean_decisions,
            injection_manifest=self.tables.injection_manifest,
            attack_decisions=self.tables.attack_decisions,
            boundary_manifest=manifest,
            boundary_decisions=broken_decisions,
        )
        with self.assertRaisesRegex(ValueError, "attempted and mutated"):
            statistics.analyze_experiment(
                broken_flags,
                repetitions=10,
                master_seed=1,
                run_id="broken",
            )

    def test_history_comparator_is_predefined_and_present(self):
        history = self.analysis.summary.loc[
            self.analysis.summary["scenario"].isin(
                {"historical_deletion", "historical_insertion"}
            )
            & (self.analysis.summary["metric_id"]
               == "pipeline_risk_difference")
            & (self.analysis.summary["comparison_type"] == "matched_pipeline")
        ]
        self.assertFalse(history.empty)
        self.assertEqual(
            set(history["comparator_pipeline"]),
            {"all_minus_freshness"},
        )
        self.assertEqual(
            set(history["comparator_definition"]),
            {"predefined_history_without_freshness"},
        )

    def test_figure_source_aliases_and_execution_counts_reconcile(self):
        source = self.source
        alias_contract = {
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
            },
        }
        alias_columns = {
            alias
            for contract in alias_contract.values()
            for alias in contract
        }
        for row in source.itertuples(index=False):
            expected = alias_contract.get(row.metric_id, {})
            for alias in alias_columns:
                value = getattr(row, alias)
                if alias in expected:
                    origin = getattr(row, expected[alias])
                    if pd.isna(origin):
                        self.assertTrue(pd.isna(value))
                    else:
                        self.assertEqual(value, origin)
                else:
                    self.assertTrue(
                        pd.isna(value),
                        f"{row.metric_id} unexpectedly populated {alias}",
                    )

        required = {
            "enabled_checks",
            "seed_scope",
            "attempted_N",
            "mutated_N",
            "evaluated_N",
            "unique_attacked_participant_days",
            "simulation_evaluations",
            "mixed_metric_applicable",
            "estimand",
        }
        self.assertTrue(required.issubset(source.columns))
        attack = source.loc[source["metric_id"] == "attack_rejection"]
        self.assertTrue((attack["simulation_evaluations"]
                         == attack["attacked_N"]).all())
        self.assertTrue(
            (attack["attempted_N"] == attack["attacked_N"]).all()
        )
        self.assertTrue(
            (attack["mutated_N"] == attack["attacked_N"]).all()
        )
        covered = source.loc[
            source["metric_id"] == "covered_agreement"
        ]
        self.assertTrue(
            (
                covered["simulation_evaluations"]
                == covered["unique_eligible_participant_days"]
                * covered["seed_count"]
            ).all()
        )
        self.assertTrue(
            (
                covered["simulation_evaluations"]
                >= covered["denominator_N"]
            ).all()
        )
        self.assertTrue(
            pd.to_numeric(
                attack["unique_attacked_participant_days"],
                errors="raise",
            ).gt(0).all()
        )

    def test_panel_b_uses_shared_scenario_y_index_and_missing_marks_are_na(self):
        panel_b = self.source.loc[self.source["panel_id"] == "b"]
        layout = plotting._aamos_panel_b_layout(panel_b)
        stage = layout["stage"].set_index("scenario")
        mechanism = layout["matched_pipeline"].set_index("scenario")
        self.assertEqual(list(stage.index), list(mechanism.index))
        self.assertEqual(
            stage["plot_y"].tolist(), mechanism["plot_y"].tolist()
        )
        self.assertTrue(
            mechanism.loc[
                mechanism["estimate"].isna(), "plot_y"
            ].notna().all()
        )

    def test_panel_a_separates_attack_and_boundary_estimands(self):
        panel_a = self.source.loc[self.source["panel_id"] == "a"]
        groups = plotting._aamos_panel_a_groups(panel_a)
        self.assertEqual(
            set(groups["attacks"]["metric_id"]),
            {"attack_rejection"},
        )
        self.assertEqual(
            set(groups["controls"]["metric_id"]),
            {"control_rejection"},
        )
        self.assertEqual(
            set(groups["controls"]["rate_requested"]),
            {0.50},
        )
        self.assertTrue(
            set(groups["attacks"]["scenario"]).isdisjoint(
                set(groups["controls"]["scenario"])
            )
        )
        self.assertEqual(
            plotting._aamos_panel_a_control_pipelines(
                groups["controls"]
            ),
            ["all_checks"],
        )
        labels = plotting._aamos_panel_a_control_labels(
            [
                "canonical_reorder",
                "clinical_measurement_error",
                "incorrect_priority_rule",
                "legitimate_late_arrival",
                "permanent_omission",
                "pre_signing_false_payload",
            ]
        )
        self.assertEqual(
            labels,
            [
                "Reorder",
                "Measurement error",
                "Symptom-count rule error",
                "Late arrival",
                "Permanent omission",
                "False payload",
            ],
        )

    def test_rate_interval_marks_preserve_ci_and_panel_d_denominators_split(self):
        panel_c = self.source.loc[
            (self.source["panel_id"] == "c")
            & (self.source["metric_id"] == "coverage")
        ]
        marks = plotting._aamos_rate_interval_marks(panel_c)
        np.testing.assert_allclose(
            marks["xerr_low"],
            marks["estimate"] - marks["ci_low"],
        )
        np.testing.assert_allclose(
            marks["xerr_high"],
            marks["ci_high"] - marks["estimate"],
        )
        panel_d = self.source.loc[self.source["panel_id"] == "d"]
        facets = plotting._aamos_panel_d_facets(panel_d)
        self.assertEqual(
            set(facets["conditional"]["metric_id"]),
            {"covered_agreement"},
        )
        self.assertEqual(
            set(facets["all_day"]["metric_id"]),
            {"upward_discordance", "priority_loss_discordance"},
        )
        self.assertEqual(
            plotting._aamos_panel_d_axis_labels(),
            ("Covered outputs", "All eligible days"),
        )


class AamosSubmissionGateAndRunnerTests(unittest.TestCase):
    def _source_and_manifest(self, root: Path):
        source_path, manifest_path, _, _ = submission_contract(root)
        return source_path, manifest_path

    def test_submission_source_gate_binds_artifact_run_code_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path = self._source_and_manifest(root)
            source = pd.read_csv(source_path)
            plotting._validate_aamos_submission_source(
                source_path, manifest_path, source
            )

            source.loc[0, "estimate"] = 0.7
            source.to_csv(source_path, index=False)
            with self.assertRaisesRegex(ValueError, "artifact SHA-256"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][source_path.name] = _sha256(source_path)
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "numerator/denominator"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_submission_source_gate_rejects_wrong_identity_and_empty_artifacts(self):
        cases = (
            ("run_id", "wrong-run", "run_id"),
            (
                "code_commit_or_archive_hash",
                "wrong-code",
                "code archive",
            ),
            (
                "metric_definition_version",
                "wrong-version",
                "metric definition",
            ),
        )
        for column, value, message in cases:
            with self.subTest(column=column), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_path, manifest_path = self._source_and_manifest(root)
                source = pd.read_csv(source_path)
                source.loc[0, column] = value
                source.to_csv(source_path, index=False)
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                manifest["artifacts"][source_path.name] = _sha256(
                    source_path
                )
                manifest_path.write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, message):
                    plotting._validate_aamos_submission_source(
                        source_path, manifest_path, source
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path = self._source_and_manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"] = {}
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "artifact"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, pd.read_csv(source_path)
                )

    def test_submission_source_gate_reconciles_bootstrap_and_ci_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path = self._source_and_manifest(root)
            source = pd.read_csv(source_path)
            source.loc[0, "bootstrap_repetitions_valid"] = 1_974
            source.to_csv(source_path, index=False)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][source_path.name] = _sha256(source_path)
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "valid and discarded"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

            source.loc[0, "bootstrap_repetitions_valid"] = 2_000
            source.loc[0, "ci_low"] = np.nan
            source.to_csv(source_path, index=False)
            manifest["artifacts"][source_path.name] = _sha256(source_path)
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "CI must be finite"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_code_archive_covers_all_execution_dependencies(self):
        runner = _load_runner()
        relative = {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in runner._code_archive_paths()
        }
        required = {
            "config/aamos00_derivation.yaml",
            "scripts/run_aamos_standard_enhanced.py",
            "scripts/make_figures.py",
            "src/tarms_experiments/encoding.py",
            "src/tarms_experiments/merkle.py",
            "src/tarms_experiments/protocol.py",
            "src/tarms_experiments/plotting.py",
        }
        self.assertTrue(required.issubset(relative))
        all_package_modules = {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in (PROJECT_ROOT / "src" / "tarms_experiments").glob(
                "*.py"
            )
        }
        self.assertTrue(all_package_modules.issubset(relative))

    def test_submission_official_source_hash_and_flow_gate(self):
        runner = _load_runner()
        config = yaml.safe_load(
            (PROJECT_ROOT / "config" / "aamos00_derivation.yaml").read_text(
                encoding="utf-8"
            )
        )
        official = config["official_release"]
        expected = config["analysis_derivation_expectations"]
        flow = {
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
        runner._validate_official_source_flow(flow, config)
        flow["source_files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "official AAMOS-00"):
            runner._validate_official_source_flow(flow, config)

    def test_execution_counts_and_atomic_publish_are_reconciled(self):
        runner = _load_runner()
        tables = run_standard_enhanced_experiment(
            _frame(),
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=("permanent_omission",),
            rates=(0.25,),
            seeds=(11,),
            boundary_rate=0.25,
        )
        counts = runner._execution_counts(tables)
        attack = counts["attack"]
        boundary = counts["boundary"]
        self.assertEqual(
            sum(row["attempted_N"] for row in attack),
            int(tables.attack_decisions["attempted"].sum()),
        )
        self.assertEqual(
            sum(row["evaluated_N"] for row in boundary),
            int(tables.boundary_decisions["evaluated"].sum()),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / ".run.staging"
            final = root / "run"
            staging.mkdir()
            (staging / "artifact.txt").write_text(
                "complete", encoding="utf-8"
            )
            runner._atomic_publish(staging, final)
            self.assertFalse(staging.exists())
            self.assertEqual(
                (final / "artifact.txt").read_text(encoding="utf-8"),
                "complete",
            )

    def test_peak_rss_conversion_rounds_darwin_bytes_up_to_kib(self):
        runner = _load_runner()
        converter = getattr(runner, "_ru_maxrss_to_kib", None)
        self.assertTrue(callable(converter))
        self.assertEqual(converter(1_048_576, system="Darwin"), 1_024)
        self.assertEqual(converter(1_048_577, system="Darwin"), 1_025)

    def test_peak_rss_conversion_preserves_linux_kib(self):
        runner = _load_runner()
        converter = getattr(runner, "_ru_maxrss_to_kib", None)
        self.assertTrue(callable(converter))
        self.assertEqual(converter(1_048_577, system="Linux"), 1_048_577)

    def test_formal_geometry_and_runtime_capacity_record_are_explicit(self):
        runner = _load_runner()
        geometry = runner._formal_geometry_counts()
        self.assertEqual(
            geometry,
            {
                "clean_decisions": 379_680,
                "attack_manifest_rows": 159_320,
                "attack_decisions": 1_911_840,
                "boundary_manifest_rows": 22_120,
                "boundary_decisions": 265_440,
                "total_decisions": 2_556_960,
            },
        )
        started = time.perf_counter() - 0.01
        with (
            mock.patch.object(
                runner.resource,
                "getrusage",
                return_value=mock.Mock(ru_maxrss=1_048_577),
            ),
            mock.patch.object(
                runner.platform,
                "system",
                return_value="Darwin",
            ),
        ):
            capacity = runner._runtime_capacity_record(
                started_monotonic=started,
                rss_before_kib=1_024,
            )
        self.assertGreater(capacity["wall_time_seconds"], 0)
        self.assertEqual(capacity["peak_rss_kib"], 1_025)
        self.assertEqual(capacity["peak_rss_delta_kib"], 1)
        self.assertEqual(capacity["peak_rss_unit"], "KiB")

    def test_failed_runner_removes_staging_and_never_creates_final_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "empty-source"
            output = root / "output"
            source.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        PROJECT_ROOT
                        / "scripts"
                        / "run_aamos_standard_enhanced.py"
                    ),
                    "--source-dir",
                    str(source),
                    "--output-root",
                    str(output),
                    "--profile",
                    "preview",
                    "--bootstrap-reps",
                    "1",
                    "--rates",
                    "0.01",
                    "--seeds",
                    "20260722",
                    "--run-id",
                    "failed-run",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            parent = output / "processed" / "aamos"
            self.assertFalse((parent / "failed-run").exists())
            self.assertEqual(
                list(parent.glob(".failed-run.staging-*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
