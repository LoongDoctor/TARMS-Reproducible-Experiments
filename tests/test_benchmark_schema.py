import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.benchmark import (  # noqa: E402
    RAW_COLUMNS,
    STAGES,
    run_microbenchmark,
    signature_admission_loop,
)
from tarms_experiments.protocol import (  # noqa: E402
    CounterConflictError,
    generate_signing_key,
    sign_event,
)


class MicrobenchmarkSchemaTests(unittest.TestCase):
    def test_signature_admission_loop_verifies_signatures_and_detects_conflicts(self):
        private_key = generate_signing_key()
        public_key = private_key.public_key()
        first = {
            "did": "dev-1",
            "keyver": 1,
            "boot": "boot-a",
            "counter": 7,
            "value": 10,
        }
        second = {**first, "value": 11}

        self.assertEqual(
            signature_admission_loop(
                [first], [sign_event(private_key, first)], public_key
            ),
            1,
        )

        tampered_signature = bytearray(sign_event(private_key, first))
        tampered_signature[-1] ^= 1
        self.assertFalse(
            signature_admission_loop([first], [bytes(tampered_signature)], public_key)
        )

        with self.assertRaises(CounterConflictError):
            signature_admission_loop(
                [first, second],
                [sign_event(private_key, first), sign_event(private_key, second)],
                public_key,
            )

    def test_small_profile_emits_one_positive_row_per_stage_and_repetition(self):
        rows = run_microbenchmark(
            batch_sizes=[4], warmups=0, repetitions=2, seed=20260722
        )

        self.assertEqual(len(rows), len(STAGES) * 2)
        self.assertEqual(set(rows.columns), set(RAW_COLUMNS))
        self.assertEqual(set(rows["stage"]), set(STAGES))
        self.assertIn("signature_admission_batch", set(rows["stage"]))
        self.assertNotIn("accept_batch", set(rows["stage"]))
        self.assertTrue((rows["duration_ns"] > 0).all())
        self.assertEqual(set(rows["provenance"]), {"measured"})
        self.assertEqual(set(rows["record_count"]), {4})
        self.assertEqual(set(rows["late_count"]), {1})

    def test_seed_structure_is_stable_across_runs(self):
        left = run_microbenchmark(
            batch_sizes=[2, 4], warmups=0, repetitions=1, seed=20260722
        )
        right = run_microbenchmark(
            batch_sizes=[2, 4], warmups=0, repetitions=1, seed=20260722
        )

        stable = ["seed", "batch_size", "repetition", "stage", "record_count", "late_count"]
        self.assertEqual(left[stable].to_dict("records"), right[stable].to_dict("records"))


if __name__ == "__main__":
    unittest.main()
