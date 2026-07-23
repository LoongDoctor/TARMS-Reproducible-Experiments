import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.conformance import CASES, run_late_update_conformance  # noqa: E402


class LateUpdateConformanceTests(unittest.TestCase):
    def test_each_case_matches_its_prespecified_outcome(self):
        rows = run_late_update_conformance(repetitions=3, seed=20260722)

        self.assertEqual(len(rows), len(CASES) * 3)
        self.assertTrue((rows["expected_result"] == rows["observed_result"]).all())
        self.assertEqual(set(rows["provenance"]), {"measured"})

    def test_reordering_is_accepted_but_content_changes_abort(self):
        rows = run_late_update_conformance(repetitions=1, seed=9).set_index("case")

        self.assertEqual(rows.loc["storage_reordering", "observed_result"], "accepted")
        for case in [
            "payload_modification",
            "record_deletion",
            "record_insertion",
            "counter_field_swap",
        ]:
            self.assertEqual(rows.loc[case, "observed_result"], "aborted")


if __name__ == "__main__":
    unittest.main()
