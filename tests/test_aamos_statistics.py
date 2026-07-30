import math
import sys
import time
import unittest
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_experiment import (  # noqa: E402
    PIPELINES,
    run_standard_enhanced_experiment,
)
from tarms_experiments.aamos_statistics import (  # noqa: E402
    METRIC_DEFINITION_VERSION,
    _draw_crossed_multiplicities,
    _ratios_from_multiplicities,
    analyze_experiment,
    build_figure_source_data,
    percentile_interval,
)


def _frame() -> pd.DataFrame:
    rows = []
    for participant in range(4):
        for day in range(6):
            value = participant * 6 + day
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


class AamosStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = _frame()
        cls.tables = run_standard_enhanced_experiment(
            cls.frame,
            attack_scenarios=("binding_mismatch", "mixed_attack"),
            boundary_scenarios=("permanent_omission",),
            rates=(0.25, 0.50),
            seeds=(11, 12),
            boundary_rate=0.25,
        )
        cls.analysis = analyze_experiment(
            cls.tables,
            repetitions=120,
            master_seed=17,
            run_id="unit-run",
        )

    def test_clean_and_attack_denominators_are_disjoint(self):
        metrics = self.analysis.per_seed_metrics
        clean = metrics.loc[metrics["metric_id"] == "clean_false_rejection"]
        attack = metrics.loc[metrics["metric_id"] == "attack_rejection"]
        self.assertEqual(set(clean["evaluation_arm"]), {"clean_control"})
        self.assertEqual(set(attack["evaluation_arm"]), {"attack_target"})
        self.assertTrue(clean["scenario"].isna().all())
        self.assertTrue(attack["scenario"].notna().all())
        self.assertFalse(
            (attack["scenario_class"] == "boundary_control").any()
        )

    def test_manifest_and_decisions_reconcile(self):
        manifest = self.tables.injection_manifest
        attacked = self.tables.attack_decisions
        counts = attacked.groupby("pair_key").size()
        self.assertTrue((counts == len(PIPELINES)).all())
        self.assertEqual(set(counts.index), set(manifest["pair_key"]))
        self.assertEqual(
            (
                int(attacked["attempted"].sum()),
                int(attacked["mutated"].sum()),
                int(attacked["evaluated"].sum()),
            ),
            (len(attacked), len(attacked), len(attacked)),
        )

    def test_mixed_population_replacement_identity(self):
        scenario, rate, seed, pipeline = (
            "binding_mismatch",
            0.25,
            11,
            "all_checks",
        )
        clean = self.tables.clean_decisions
        attack = self.tables.attack_decisions
        selected = attack.loc[
            (attack["scenario"] == scenario)
            & (attack["rate_requested"] == rate)
            & (attack["seed"] == seed)
            & (attack["pipeline"] == pipeline)
        ]
        target_keys = set(selected["record_key"])
        clean_group = clean.loc[
            (clean["seed"] == seed) & (clean["pipeline"] == pipeline)
        ]
        explicit = pd.concat(
            [
                clean_group.loc[
                    ~clean_group["record_key"].isin(target_keys)
                ],
                selected,
            ],
            ignore_index=True,
        )
        expected_coverage = int(explicit["covered"].sum())
        row = self.analysis.per_seed_metrics.loc[
            (self.analysis.per_seed_metrics["scenario"] == scenario)
            & (self.analysis.per_seed_metrics["rate_requested"] == rate)
            & (self.analysis.per_seed_metrics["seed"] == seed)
            & (self.analysis.per_seed_metrics["pipeline"] == pipeline)
            & (self.analysis.per_seed_metrics["metric_id"] == "coverage")
        ].iloc[0]
        self.assertEqual(
            (int(row["numerator_n"]), int(row["denominator_N"])),
            (expected_coverage, len(explicit)),
        )

    def test_metric_partition_identities_per_seed(self):
        rows = self.analysis.per_seed_metrics
        mixed = rows.loc[
            rows["metric_id"].isin(
                {
                    "coverage",
                    "abstention",
                    "covered_agreement",
                    "upward_discordance",
                    "priority_loss_discordance",
                }
            )
        ]
        key = ["scenario", "rate_requested", "seed", "pipeline"]
        for _, group in mixed.groupby(key, dropna=False):
            values = group.set_index("metric_id")
            eligible = int(values.loc["coverage", "denominator_N"])
            covered = int(values.loc["coverage", "numerator_n"])
            abstained = int(values.loc["abstention", "numerator_n"])
            agreement = int(
                values.loc["covered_agreement", "numerator_n"]
            )
            upward = int(values.loc["upward_discordance", "numerator_n"])
            loss = int(
                values.loc["priority_loss_discordance", "numerator_n"]
            )
            self.assertEqual(covered + abstained, eligible)
            self.assertEqual(agreement + upward + loss, covered)

    def test_rejected_upward_label_does_not_create_output_discordance(self):
        rows = self.analysis.per_seed_metrics
        selection = rows.loc[
            (rows["scenario"] == "binding_mismatch")
            & (rows["rate_requested"] == 0.25)
            & (rows["pipeline"] == "all_checks")
        ]
        upward = selection.loc[
            selection["metric_id"] == "upward_discordance",
            "numerator_n",
        ]
        abstention = selection.loc[
            selection["metric_id"] == "abstention", "numerator_n"
        ]
        self.assertTrue((upward == 0).all())
        self.assertTrue((abstention > 0).all())

    def test_expected_stage_denominator_is_unconditional_and_not_applicable_is_na(self):
        rows = self.analysis.per_seed_metrics
        full = rows.loc[
            (rows["scenario"] == "binding_mismatch")
            & (rows["pipeline"] == "all_checks")
            & (rows["metric_id"] == "expected_stage_agreement")
        ]
        signature_only = rows.loc[
            (rows["scenario"] == "binding_mismatch")
            & (rows["pipeline"] == "signature_only")
            & (rows["metric_id"] == "expected_stage_agreement")
        ]
        self.assertTrue((full["denominator_N"] > 0).all())
        self.assertTrue(
            (full["numerator_n"] == full["denominator_N"]).all()
        )
        self.assertTrue((signature_only["denominator_N"] == 0).all())
        self.assertTrue(signature_only["estimate"].isna().all())
        self.assertFalse(
            (
                rows.loc[
                    rows["scenario_class"] == "boundary_control",
                    "metric_id",
                ]
                == "expected_stage_agreement"
            ).any()
        )

    def test_permanent_omission_is_reported_as_no_decision_not_acceptance(self):
        rows = self.analysis.per_seed_metrics.loc[
            self.analysis.per_seed_metrics["scenario"]
            == "permanent_omission"
        ]
        no_decision = rows.loc[
            rows["metric_id"] == "control_no_decision"
        ]
        rejection = rows.loc[
            rows["metric_id"] == "control_rejection"
        ]
        acceptance = rows.loc[
            rows["metric_id"] == "control_acceptance"
        ]
        self.assertTrue(
            (
                no_decision["numerator_n"]
                == no_decision["denominator_N"]
            ).all()
        )
        self.assertTrue((rejection["denominator_N"] == 0).all())
        self.assertTrue((acceptance["denominator_N"] == 0).all())
        self.assertTrue(rejection["estimate"].isna().all())
        self.assertTrue(acceptance["estimate"].isna().all())

    def test_pair_keys_are_complete_and_four_cells_reconstruct_rd(self):
        pairs = self.analysis.paired_contrasts
        self.assertFalse(
            pairs.duplicated(
                [
                    "contrast_id",
                    "scenario",
                    "rate_requested",
                    "seed",
                    "pipeline",
                    "comparator_pipeline",
                ]
            ).any()
        )
        for row in pairs.itertuples(index=False):
            cell_sum = (
                row.both_reject_n
                + row.attack_only_reject_n
                + row.clean_only_reject_n
                + row.neither_reject_n
            )
            self.assertEqual(cell_sum, row.denominator_N)
            reconstructed = (
                row.attack_only_reject_n - row.clean_only_reject_n
            ) / row.denominator_N
            self.assertAlmostEqual(reconstructed, row.estimate)

    def test_per_seed_counts_sum_to_pooled_summary(self):
        per_seed = self.analysis.per_seed_metrics
        summary = self.analysis.summary
        metric_keys = [
            "scenario",
            "rate_requested",
            "pipeline",
            "comparator_pipeline",
            "metric_id",
        ]
        pooled = (
            per_seed.groupby(metric_keys, dropna=False, as_index=False)
            .agg(
                numerator_n=("numerator_n", "sum"),
                denominator_N=("denominator_N", "sum"),
            )
            .sort_values(metric_keys, na_position="first")
            .reset_index(drop=True)
        )
        observed = (
            summary[metric_keys + ["numerator_n", "denominator_N"]]
            .sort_values(metric_keys, na_position="first")
            .reset_index(drop=True)
        )
        for column in ("scenario", "comparator_pipeline"):
            pooled[column] = pooled[column].fillna("__NA__")
            observed[column] = observed[column].fillna("__NA__")
        pd.testing.assert_frame_equal(pooled, observed, check_dtype=False)

    def test_zero_denominator_replicates_are_counted(self):
        numerator = np.zeros((2, 3, 1), dtype=float)
        denominator = np.zeros_like(numerator)
        multiplicities = np.ones((7, 2, 3), dtype=np.int64)
        ratios = _ratios_from_multiplicities(
            numerator, denominator, multiplicities
        )
        self.assertTrue(np.isnan(ratios).all())
        valid = int(np.isfinite(ratios[:, 0]).sum())
        self.assertEqual((valid, 7 - valid), (0, 7))
        summary_row = self.analysis.summary.loc[
            (self.analysis.summary["scenario"] == "binding_mismatch")
            & (self.analysis.summary["pipeline"] == "signature_only")
            & (
                self.analysis.summary["metric_id"]
                == "expected_stage_agreement"
            )
        ].iloc[0]
        self.assertEqual(
            (
                summary_row["bootstrap_repetitions_valid"],
                summary_row["bootstrap_repetitions_discarded"],
            ),
            (0, 120),
        )

    def test_scripted_multiplicities_match_explicit_cluster_repetition(self):
        numerator = np.array(
            [
                [[1.0, 0.0], [2.0, 1.0]],
                [[3.0, 1.0], [4.0, 2.0]],
            ]
        )
        denominator = np.ones_like(numerator)
        multiplicities = np.array(
            [
                [[2, 0], [1, 1]],
                [[0, 2], [2, 0]],
            ],
            dtype=np.int64,
        )
        actual = _ratios_from_multiplicities(
            numerator, denominator, multiplicities
        )
        expected = []
        for replicate in multiplicities:
            replicate_values = []
            for metric in range(numerator.shape[2]):
                num = 0.0
                den = 0.0
                for seed in range(numerator.shape[0]):
                    for participant in range(numerator.shape[1]):
                        count = int(replicate[seed, participant])
                        num += count * numerator[seed, participant, metric]
                        den += count * denominator[seed, participant, metric]
                replicate_values.append(num / den)
            expected.append(replicate_values)
        np.testing.assert_allclose(actual, np.asarray(expected))

    def test_shared_weights_preserve_paired_risk_difference(self):
        left = np.array([[1.0, 0.0], [1.0, 1.0]])
        right = np.array([[0.0, 0.0], [1.0, 0.0]])
        denominator = np.ones_like(left)
        numerator = np.stack([left, right, left - right], axis=2)
        denominators = np.stack(
            [denominator, denominator, denominator], axis=2
        )
        weights = np.array(
            [
                [[2, 0], [1, 1]],
                [[0, 2], [2, 0]],
            ],
            dtype=np.int64,
        )
        samples = _ratios_from_multiplicities(
            numerator, denominators, weights
        )
        np.testing.assert_allclose(
            samples[:, 2], samples[:, 0] - samples[:, 1]
        )

    def test_crossed_multiplicity_is_outer_product_of_factor_counts(self):
        weights = _draw_crossed_multiplicities(
            seed_count=2,
            participant_count=2,
            repetitions=20,
            rng=np.random.default_rng(77),
        )
        self.assertTrue((weights.sum(axis=(1, 2)) == 4).all())
        for replicate in weights:
            self.assertEqual(
                int(replicate[0, 0] * replicate[1, 1]),
                int(replicate[0, 1] * replicate[1, 0]),
            )

    def test_duplicate_seed_occurrences_share_one_participant_draw(self):
        class StubRng:
            def integers(self, low, high, size, dtype):
                del low, high
                return np.zeros(size, dtype=dtype)

        rng = StubRng()
        weights = _draw_crossed_multiplicities(
            seed_count=2,
            participant_count=2,
            repetitions=3,
            rng=rng,
        )
        self.assertTrue((weights.sum(axis=2)[:, 0] == 4).all())
        self.assertTrue((weights.sum(axis=2)[:, 1] == 0).all())

    def test_deterministic_stable_substreams_and_unforced_percentiles(self):
        shuffled_tables = type(self.tables)(
            clean_decisions=self.tables.clean_decisions.sample(
                frac=1, random_state=1
            ).reset_index(drop=True),
            injection_manifest=self.tables.injection_manifest.sample(
                frac=1, random_state=2
            ).reset_index(drop=True),
            attack_decisions=self.tables.attack_decisions.sample(
                frac=1, random_state=3
            ).reset_index(drop=True),
            boundary_manifest=self.tables.boundary_manifest.sample(
                frac=1, random_state=4
            ).reset_index(drop=True),
            boundary_decisions=self.tables.boundary_decisions.sample(
                frac=1, random_state=5
            ).reset_index(drop=True),
        )
        repeated = analyze_experiment(
            shuffled_tables,
            repetitions=120,
            master_seed=17,
            run_id="unit-run",
        )
        order = [
            "scenario",
            "rate_requested",
            "pipeline",
            "comparator_pipeline",
            "metric_id",
        ]
        left = self.analysis.summary.sort_values(
            order, na_position="first"
        ).reset_index(drop=True)
        right = repeated.summary.sort_values(
            order, na_position="first"
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)

        changed_seed = analyze_experiment(
            shuffled_tables,
            repetitions=120,
            master_seed=18,
            run_id="unit-run",
        ).summary.sort_values(order, na_position="first").reset_index(
            drop=True
        )
        pd.testing.assert_series_equal(
            left["numerator_n"],
            changed_seed["numerator_n"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            left["denominator_N"],
            changed_seed["denominator_N"],
            check_names=False,
        )

        samples = np.array([0.0, 0.0, 0.0, 1.0])
        low, high = percentile_interval(samples)
        expected = np.quantile(samples, [0.025, 0.975])
        self.assertEqual((low, high), tuple(expected))

    def test_adding_unrelated_stratum_does_not_change_existing_intervals(self):
        extra_boundary = self.tables.boundary_decisions.copy()
        extra_boundary["scenario"] = "unrelated_control"
        extra_boundary["pair_key"] = (
            extra_boundary["pair_key"].astype(str) + "|unrelated"
        )
        extra_manifest = self.tables.boundary_manifest.copy()
        extra_manifest["scenario"] = "unrelated_control"
        extra_manifest["pair_key"] = (
            extra_manifest["pair_key"].astype(str) + "|unrelated"
        )
        extended = type(self.tables)(
            clean_decisions=self.tables.clean_decisions,
            injection_manifest=self.tables.injection_manifest,
            attack_decisions=self.tables.attack_decisions,
            boundary_manifest=pd.concat(
                [self.tables.boundary_manifest, extra_manifest],
                ignore_index=True,
            ),
            boundary_decisions=pd.concat(
                [self.tables.boundary_decisions, extra_boundary],
                ignore_index=True,
            ),
        )
        extended_summary = analyze_experiment(
            extended,
            repetitions=120,
            master_seed=17,
            run_id="unit-run",
        ).summary
        extended_summary = extended_summary.loc[
            extended_summary["scenario"] != "unrelated_control"
        ]
        order = [
            "scenario",
            "rate_requested",
            "pipeline",
            "comparator_pipeline",
            "metric_id",
        ]
        left = self.analysis.summary.sort_values(
            order, na_position="first"
        ).reset_index(drop=True)
        right = extended_summary.sort_values(
            order, na_position="first"
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)

    def test_all_participants_remain_in_bootstrap_universe(self):
        summary = self.analysis.summary
        attacked = summary.loc[
            summary["metric_id"] == "attack_rejection"
        ]
        self.assertEqual(
            set(attacked["participant_clusters"]), {self.frame["participant_id"].nunique()}
        )
        self.assertEqual(
            set(summary["metric_definition_version"]),
            {METRIC_DEFINITION_VERSION},
        )

    def test_vectorized_performance_smoke_exceeds_five_times(self):
        rng = np.random.default_rng(99)
        numerator = rng.integers(0, 8, size=(8, 12, 6)).astype(float)
        denominator = numerator + rng.integers(
            1, 8, size=numerator.shape
        )
        weights = _draw_crossed_multiplicities(
            seed_count=8,
            participant_count=12,
            repetitions=400,
            rng=np.random.default_rng(101),
        )

        vector_times = []
        for _ in range(3):
            started = time.perf_counter()
            vector = _ratios_from_multiplicities(
                numerator, denominator, weights
            )
            vector_times.append(time.perf_counter() - started)

        started = time.perf_counter()
        rows = []
        for replicate in weights:
            metric_rows = []
            for metric in range(numerator.shape[2]):
                num = 0.0
                den = 0.0
                for seed in range(numerator.shape[0]):
                    for participant in range(numerator.shape[1]):
                        count = replicate[seed, participant]
                        num += count * numerator[seed, participant, metric]
                        den += count * denominator[seed, participant, metric]
                metric_rows.append(num / den)
            rows.append(metric_rows)
        slow = np.asarray(rows)
        slow_time = time.perf_counter() - started
        np.testing.assert_allclose(vector, slow)
        self.assertGreater(slow_time / np.median(vector_times), 5.0)

    def test_figure_source_data_has_one_row_per_plotted_mark(self):
        source = build_figure_source_data(
            self.analysis.summary,
            self.analysis.paired_contrasts,
            run_id="unit-run",
            created_utc="2026-07-23T00:00:00Z",
            code_commit_or_archive_hash="abc123",
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
        required = {
            "panel_id",
            "metric_id",
            "numerator_n",
            "denominator_N",
            "estimate",
            "ci_low",
            "ci_high",
            "run_id",
            "dataset_doi",
            "metric_definition_version",
            "bootstrap_repetitions_requested",
            "bootstrap_repetitions_valid",
            "bootstrap_repetitions_discarded",
        }
        self.assertTrue(required.issubset(source.columns))
        self.assertFalse(
            source.duplicated(
                [
                    "panel_id",
                    "scenario",
                    "rate_requested",
                    "pipeline",
                    "comparator_pipeline",
                    "metric_id",
                ]
            ).any()
        )


if __name__ == "__main__":
    unittest.main()
