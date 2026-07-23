import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.encoding import EncodingError, canonical_json_bytes  # noqa: E402


class CanonicalEncodingTests(unittest.TestCase):
    def test_key_order_does_not_change_encoding(self):
        left = {"did": "d-1", "counter": 7, "flags": [True, None]}
        right = {"flags": [True, None], "counter": 7, "did": "d-1"}

        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(
            canonical_json_bytes(left),
            b'{"counter":7,"did":"d-1","flags":[true,null]}',
        )

    def test_float_is_rejected_to_avoid_platform_specific_encoding(self):
        with self.assertRaisesRegex(EncodingError, "float"):
            canonical_json_bytes({"pef": 412.5})

    def test_normalized_key_collision_is_rejected(self):
        with self.assertRaisesRegex(EncodingError, "normalization collision"):
            canonical_json_bytes({"e\u0301": 1, "é": 2})

    def test_integer_outside_cross_language_safe_range_is_rejected(self):
        with self.assertRaisesRegex(EncodingError, "safe integer"):
            canonical_json_bytes({"counter": 2**53})


if __name__ == "__main__":
    unittest.main()
