import sys
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.stats import summarize_observations  # noqa: E402


class StatisticsTests(unittest.TestCase):
    def test_summary_reports_quantiles_and_ordered_bootstrap_interval(self):
        frame = pd.DataFrame(
            {
                "run_id": ["r1"] * 5,
                "seed": [1] * 5,
                "batch_size": [16] * 5,
                "repetition": list(range(5)),
                "stage": ["merkle_build"] * 5,
                "duration_ns": [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000],
                "record_count": [16] * 5,
                "late_count": [0] * 5,
                "provenance": ["measured"] * 5,
            }
        )

        summary = summarize_observations(frame, bootstrap_reps=500, seed=7).iloc[0]

        self.assertEqual(summary["n"], 5)
        self.assertEqual(summary["median_ms"], 3.0)
        self.assertEqual(summary["q1_ms"], 2.0)
        self.assertEqual(summary["q3_ms"], 4.0)
        self.assertAlmostEqual(summary["p95_ms"], 4.8)
        self.assertLessEqual(summary["ci_low_ms"], summary["median_ms"])
        self.assertGreaterEqual(summary["ci_high_ms"], summary["median_ms"])

    def test_throughput_is_derived_from_batch_size_and_duration(self):
        frame = pd.DataFrame(
            {
                "run_id": ["r1", "r1"],
                "seed": [1, 1],
                "batch_size": [100, 100],
                "repetition": [0, 1],
                "stage": ["signature_admission_batch", "signature_admission_batch"],
                "duration_ns": [1_000_000_000, 2_000_000_000],
                "record_count": [100, 100],
                "late_count": [0, 0],
                "provenance": ["measured", "measured"],
            }
        )

        summary = summarize_observations(frame, bootstrap_reps=100, seed=9).iloc[0]

        self.assertEqual(summary["median_records_s"], 75.0)


if __name__ == "__main__":
    unittest.main()
