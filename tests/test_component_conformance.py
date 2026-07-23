import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.component_conformance import (  # noqa: E402
    COMPONENT_CASES,
    run_component_conformance,
)


class ComponentConformanceTests(unittest.TestCase):
    def test_all_constructed_cases_match_their_prespecified_outcomes(self):
        rows = run_component_conformance(repetitions=3, seed=20260722)

        self.assertEqual(len(rows), len(COMPONENT_CASES) * 3)
        self.assertTrue(rows["matches_rule"].all())
        self.assertEqual(set(rows["provenance"]), {"measured"})

    def test_violation_cases_are_rejected_and_valid_controls_are_accepted(self):
        rows = run_component_conformance(repetitions=1, seed=8).set_index("case")

        for case in [
            "valid_signature",
            "first_admission",
            "idempotent_retransmission",
            "valid_merkle_proof",
            "valid_cas",
        ]:
            self.assertEqual(rows.loc[case, "observed_result"], "accepted")
        for case in [
            "forged_signature",
            "modified_signed_payload",
            "counter_conflict",
            "modified_merkle_proof",
            "stale_latest_pointer",
            "skipped_version",
        ]:
            self.assertEqual(rows.loc[case, "observed_result"], "rejected")


if __name__ == "__main__":
    unittest.main()
