import sys
import subprocess
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos import (  # noqa: E402
    cluster_bootstrap_metrics,
    compute_integrity_metrics,
    inject_integrity_violations,
    prepare_patient_days,
    resample_participant_clusters,
)


class AamosMetricTests(unittest.TestCase):
    def test_legacy_cli_is_explicitly_retired(self):
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts/run_aamos_analysis.py")],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("retired", completed.stderr)
        self.assertIn("unified TARMS verifier", completed.stderr)

    def test_column_mapping_builds_auditable_patient_day_flow(self):
        source_path = PROJECT_ROOT / "tests/fixtures/aamos_minimal.csv"

        prepared, flow = prepare_patient_days(
            source_path,
            {
                "participant_id": "participant",
                "date": "date",
                "clean_priority": "priority",
                "eligible": "eligible",
            },
        )

        self.assertEqual(len(prepared), 8)
        self.assertEqual(flow["source_rows"], 8)
        self.assertEqual(flow["included_patient_days"], 8)
        self.assertEqual(flow["participants"], 4)
        self.assertEqual(flow["excluded_missing_required"], 0)

    def test_metrics_use_explicit_eligible_and_covered_denominators(self):
        frame = pd.DataFrame(
            {
                "participant_id": ["A", "A", "B", "B"],
                "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"]),
                "eligible": [True, True, True, True],
                "clean_priority": [0, 1, 2, 1],
                "attacked_priority": [1, 0, 2, 1],
                "verified_priority": [np.nan, np.nan, 2, 1],
                "accepted": [False, False, True, True],
            }
        )

        result = compute_integrity_metrics(frame).set_index("metric")

        self.assertEqual((result.loc["coverage", "n"], result.loc["coverage", "N"]), (2, 4))
        self.assertEqual((result.loc["abstention", "n"], result.loc["abstention", "N"]), (2, 4))
        self.assertEqual((result.loc["covered_agreement", "n"], result.loc["covered_agreement", "N"]), (2, 2))
        self.assertEqual((result.loc["upward_discordance", "n"], result.loc["upward_discordance", "N"]), (1, 4))
        self.assertEqual((result.loc["priority_loss_discordance", "n"], result.loc["priority_loss_discordance", "N"]), (1, 4))

    def test_legacy_injector_refuses_to_preassign_results(self):
        source = pd.read_csv(PROJECT_ROOT / "tests/fixtures/aamos_minimal.csv")
        source = source.rename(
            columns={"participant": "participant_id", "priority": "clean_priority"}
        )
        source["date"] = pd.to_datetime(source["date"])
        source["eligible"] = source["eligible"].astype(bool)

        with self.assertRaisesRegex(RuntimeError, "retired.*unified verifier"):
            inject_integrity_violations(source, seed=11, rate=0.25)

    def test_participant_resampling_keeps_whole_clusters(self):
        frame = pd.DataFrame(
            {
                "participant_id": ["A", "A", "B"],
                "value": [1, 2, 3],
            }
        )

        sampled = resample_participant_clusters(frame, ["A", "A"])

        self.assertEqual(len(sampled), 4)
        self.assertEqual(sampled.groupby("bootstrap_cluster").size().tolist(), [2, 2])
        self.assertEqual(set(sampled["participant_id"]), {"A"})

    def test_cluster_bootstrap_returns_bounded_intervals(self):
        source = pd.read_csv(PROJECT_ROOT / "tests/fixtures/aamos_minimal.csv")
        source = source.rename(
            columns={"participant": "participant_id", "priority": "clean_priority"}
        )
        source["date"] = pd.to_datetime(source["date"])
        source["eligible"] = source["eligible"].astype(bool)
        source["attacked_priority"] = source["clean_priority"]
        source["verified_priority"] = source["clean_priority"].astype(float)
        source["accepted"] = True
        source.loc[[0, 3], "attacked_priority"] = [1, 0]
        source.loc[[0, 3], "verified_priority"] = np.nan
        source.loc[[0, 3], "accepted"] = False

        intervals = cluster_bootstrap_metrics(source, repetitions=100, seed=17)

        self.assertTrue(((intervals["ci_low"] >= 0) & (intervals["ci_high"] <= 1)).all())
        self.assertTrue((intervals["ci_low"] <= intervals["estimate"]).all())
        self.assertTrue((intervals["estimate"] <= intervals["ci_high"]).all())


if __name__ == "__main__":
    unittest.main()
