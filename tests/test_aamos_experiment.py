import sys
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_experiment import (  # noqa: E402
    ATTACK_RATES,
    FIXED_SEEDS,
    PIPELINES,
    expected_pipeline_outcome,
    run_attack_matrix,
    run_standard_enhanced_experiment,
    select_stratified_targets,
)
from tarms_experiments.aamos_scenarios import REJECT_SCENARIOS  # noqa: E402


class AamosExperimentTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "participant_id": [f"P{i // 8}" for i in range(40)],
                "relative_day": [i % 8 for i in range(40)],
                "eligible": [True] * 39 + [False],
                "clean_priority": [i % 4 for i in range(40)],
                "payload_json": [f'{{"row":{i}}}' for i in range(40)],
            }
        )

    def test_target_selection_is_reproducible_and_jointly_stratified(self):
        balanced = self.frame.iloc[:32].assign(eligible=True)
        left = select_stratified_targets(balanced, rate=0.25, seed=7)
        right = select_stratified_targets(balanced, rate=0.25, seed=7)
        self.assertEqual(left, right)
        self.assertEqual(len(left), 8)
        selected = balanced.loc[left]
        self.assertEqual(
            selected.groupby("clean_priority").size().tolist(),
            [2, 2, 2, 2],
        )
        self.assertGreaterEqual(selected["participant_id"].nunique(), 3)

    def test_target_selection_includes_every_participant_when_budget_permits(self):
        frame = pd.DataFrame(
            {
                "participant_id": ["rare", *(["common"] * 9)],
                "relative_day": list(range(10)),
                "eligible": [True] * 10,
                "clean_priority": [0, 0, 1, 2, 3, 0, 1, 2, 3, 0],
                "payload_json": ["{}"] * 10,
            }
        )
        selected = select_stratified_targets(
            frame, rate=0.20, seed=20260722
        )
        self.assertEqual(len(selected), 2)
        self.assertEqual(
            set(frame.loc[selected, "participant_id"]),
            {"rare", "common"},
        )

    def test_formal_constants_cover_clean_control_and_twenty_seeds(self):
        self.assertEqual(ATTACK_RATES, (0.01, 0.05, 0.10, 0.20))
        self.assertEqual(FIXED_SEEDS, tuple(range(20260722, 20260742)))

    def test_all_checks_rejects_and_matching_ablation_accepts(self):
        decisions = run_attack_matrix(
            self.frame,
            scenarios=("binding_mismatch",),
            rates=(0.20,),
            seeds=(11,),
        )
        full = decisions.loc[decisions["pipeline"] == "all_checks"]
        minus = decisions.loc[
            decisions["pipeline"] == "all_minus_binding"
        ]
        self.assertTrue((~full["accepted"]).all())
        self.assertTrue((full["failure_stage"] == "binding").all())
        self.assertTrue(minus["accepted"].all())

    def test_clean_controls_are_unique_and_not_rate_or_scenario_duplicated(self):
        outputs = run_standard_enhanced_experiment(
            self.frame,
            attack_scenarios=("binding_mismatch", "payload_after_signing"),
            boundary_scenarios=("permanent_omission",),
            rates=(0.10, 0.20),
            seeds=(11, 12),
            boundary_rate=0.10,
        )
        clean = outputs.clean_decisions
        key = ["seed", "participant_id", "relative_day", "pipeline"]
        self.assertFalse(clean.duplicated(key).any())
        self.assertEqual(
            len(clean),
            2 * int(self.frame["eligible"].sum()) * len(PIPELINES),
        )
        self.assertEqual(set(clean["evaluation_arm"]), {"clean_control"})
        self.assertNotIn("scenario", clean.columns)
        self.assertNotIn("rate_requested", clean.columns)

    def test_manifest_is_pipeline_independent_and_attack_rows_are_targets_only(self):
        outputs = run_standard_enhanced_experiment(
            self.frame,
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=(),
            rates=(0.10,),
            seeds=(11,),
        )
        manifest = outputs.injection_manifest
        attacked = outputs.attack_decisions
        self.assertNotIn("pipeline", manifest.columns)
        self.assertTrue(manifest["targeted"].all())
        self.assertTrue(manifest["injected"].all())
        self.assertEqual(len(attacked), len(manifest) * len(PIPELINES))
        self.assertFalse(
            attacked.duplicated(["pair_key", "pipeline"]).any()
        )
        self.assertEqual(
            set(attacked["pair_key"]), set(manifest["pair_key"])
        )
        self.assertTrue(attacked["attempted"].all())
        self.assertTrue(attacked["mutated"].all())
        self.assertTrue(attacked["evaluated"].all())

    def test_boundary_controls_are_kept_out_of_attack_tables(self):
        outputs = run_standard_enhanced_experiment(
            self.frame,
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=("permanent_omission", "canonical_reorder"),
            rates=(0.10,),
            seeds=(11,),
        )
        self.assertEqual(
            set(outputs.attack_decisions["scenario_class"]), {"attack"}
        )
        self.assertEqual(
            set(outputs.boundary_decisions["scenario_class"]),
            {"boundary_control"},
        )
        self.assertTrue(
            set(outputs.injection_manifest["scenario"]).isdisjoint(
                set(outputs.boundary_manifest["scenario"])
            )
        )

    def test_pipeline_expected_stage_is_predefined_per_pipeline(self):
        self.assertEqual(
            expected_pipeline_outcome(
                "binding_mismatch", PIPELINES["all_checks"]
            ),
            ("reject", "binding", True),
        )
        self.assertEqual(
            expected_pipeline_outcome(
                "binding_mismatch", PIPELINES["signature_only"]
            ),
            ("accept", "none", False),
        )
        self.assertEqual(
            expected_pipeline_outcome(
                "mixed_attack", PIPELINES["all_minus_signature"]
            ),
            ("reject", "binding", True),
        )
        self.assertEqual(
            expected_pipeline_outcome(
                "historical_modification", PIPELINES["all_checks"]
            ),
            ("reject", "history", True),
        )
        self.assertEqual(
            expected_pipeline_outcome(
                "historical_modification",
                PIPELINES["all_minus_merkle"],
            ),
            ("accept", "none", False),
        )

    def test_history_checks_require_merkle_and_freshness_and_omission_is_no_decision(self):
        outputs = run_standard_enhanced_experiment(
            self.frame,
            attack_scenarios=(
                "historical_modification",
                "historical_deletion",
                "historical_insertion",
            ),
            boundary_scenarios=("permanent_omission", "canonical_reorder"),
            rates=(0.20,),
            seeds=(11,),
            boundary_rate=0.20,
        )
        history = outputs.attack_decisions
        full = history.loc[history["pipeline"] == "all_checks"]
        minus_binding = history.loc[
            history["pipeline"] == "all_minus_binding"
        ]
        minus_merkle = history.loc[
            history["pipeline"] == "all_minus_merkle"
        ]
        minus_freshness = history.loc[
            history["pipeline"] == "all_minus_freshness"
        ]
        self.assertTrue((~full["accepted"].astype(bool)).all())
        self.assertTrue((full["failure_stage"] == "history").all())
        self.assertTrue((~minus_binding["accepted"].astype(bool)).all())
        self.assertTrue(minus_merkle["accepted"].astype(bool).all())
        self.assertTrue(minus_freshness["accepted"].astype(bool).all())
        insertion_manifest = outputs.injection_manifest.loc[
            outputs.injection_manifest["scenario"]
            == "historical_insertion"
        ]
        self.assertEqual(
            set(insertion_manifest["history_affected_key_origin"]),
            {"synthetic_unauthorized_effective_set_key"},
        )
        self.assertEqual(
            set(insertion_manifest["history_requested_key_origin"]),
            {"AAMOS_observed_participant_day"},
        )

        omission = outputs.boundary_decisions.loc[
            outputs.boundary_decisions["scenario"] == "permanent_omission"
        ]
        reorder = outputs.boundary_decisions.loc[
            outputs.boundary_decisions["scenario"] == "canonical_reorder"
        ]
        self.assertTrue((~omission["evaluated"].astype(bool)).all())
        self.assertTrue(omission["accepted"].isna().all())
        self.assertTrue(reorder["evaluated"].astype(bool).all())
        self.assertTrue(reorder["accepted"].astype(bool).all())

    def test_every_attack_pipeline_matches_predefined_first_stage(self):
        outputs = run_standard_enhanced_experiment(
            self.frame,
            attack_scenarios=tuple(REJECT_SCENARIOS),
            boundary_scenarios=(),
            rates=(0.20,),
            seeds=(11,),
        )
        for row in outputs.attack_decisions.itertuples(index=False):
            with self.subTest(
                scenario=row.scenario, pipeline=row.pipeline
            ):
                if row.expected_outcome == "reject":
                    self.assertFalse(bool(row.accepted))
                    self.assertEqual(
                        row.failure_stage, row.expected_first_stage
                    )
                    self.assertTrue(bool(row.stage_hit))
                else:
                    self.assertTrue(bool(row.accepted))
                    self.assertEqual(row.expected_first_stage, "none")

    def test_stable_keys_do_not_depend_on_dataframe_index(self):
        left = run_standard_enhanced_experiment(
            self.frame.reset_index(drop=True),
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=(),
            rates=(0.20,),
            seeds=(11,),
        )
        right = run_standard_enhanced_experiment(
            self.frame.reset_index(drop=True).set_axis(range(100, 140)),
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=(),
            rates=(0.20,),
            seeds=(11,),
        )
        self.assertEqual(
            sorted(left.injection_manifest["pair_key"]),
            sorted(right.injection_manifest["pair_key"]),
        )


if __name__ == "__main__":
    unittest.main()
