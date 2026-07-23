import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.merkle import MerkleTree, ProofStep, verify_proof  # noqa: E402


class MerkleTreeTests(unittest.TestCase):
    def test_all_odd_leaf_proofs_verify(self):
        payloads = [f"record-{index}".encode() for index in range(5)]
        tree = MerkleTree(payloads)

        self.assertEqual(len(tree.root), 32)
        for index, payload in enumerate(payloads):
            self.assertTrue(
                verify_proof(
                    payload, index, len(payloads), tree.proof(index), tree.root
                )
            )

    def test_mutated_payload_and_sibling_are_rejected(self):
        payloads = [b"a", b"b", b"c"]
        tree = MerkleTree(payloads)
        proof = tree.proof(1)

        self.assertFalse(verify_proof(b"B", 1, len(payloads), proof, tree.root))
        proof[0] = proof[0].__class__(proof[0].side, b"\x00" * 32)
        self.assertFalse(
            verify_proof(payloads[1], 1, len(payloads), proof, tree.root)
        )

    def test_empty_tree_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            MerkleTree([])

    def test_index_alias_and_wrong_leaf_count_are_rejected(self):
        payloads = [f"record-{index}".encode() for index in range(8)]
        tree = MerkleTree(payloads)
        proof = tree.proof(0)

        self.assertFalse(
            verify_proof(
                payloads[0],
                leaf_index=8,
                leaf_count=8,
                proof=proof,
                expected_root=tree.root,
            )
        )
        self.assertFalse(
            verify_proof(
                payloads[0],
                leaf_index=0,
                leaf_count=9,
                proof=proof,
                expected_root=tree.root,
            )
        )

        three_payloads = [b"a", b"b", b"c"]
        three_tree = MerkleTree(three_payloads)
        self.assertFalse(
            verify_proof(
                three_payloads[2],
                leaf_index=2,
                leaf_count=4,
                proof=three_tree.proof(2),
                expected_root=three_tree.root,
            )
        )

        five_payloads = [f"odd-{index}".encode() for index in range(5)]
        five_tree = MerkleTree(five_payloads)
        self.assertFalse(
            verify_proof(
                five_payloads[4],
                leaf_index=4,
                leaf_count=6,
                proof=five_tree.proof(4),
                expected_root=five_tree.root,
            )
        )

    def test_root_commits_total_leaf_count(self):
        self.assertNotEqual(
            MerkleTree([b"a", b"b", b"c"]).root,
            MerkleTree([b"a", b"b", b"c", b"c"]).root,
        )

    def test_malformed_root_and_sibling_are_rejected(self):
        tree = MerkleTree([b"a", b"b"])

        self.assertFalse(verify_proof(b"a", 0, 2, tree.proof(0), None))
        self.assertFalse(
            verify_proof(
                b"a",
                0,
                2,
                [ProofStep("right", None)],
                tree.root,
            )
        )
        self.assertFalse(
            verify_proof(
                b"a",
                0,
                2**64,
                [ProofStep("right", b"\x00" * 32)] * 64,
                b"\x00" * 32,
            )
        )
    def test_proof_depth_is_bound_to_declared_tree_shape(self):
        payloads = [f"record-{index}".encode() for index in range(5)]
        tree = MerkleTree(payloads)
        proof = tree.proof(4)

        self.assertFalse(
            verify_proof(
                payloads[4],
                leaf_index=4,
                leaf_count=5,
                proof=proof[:-1],
                expected_root=tree.root,
            )
        )


if __name__ == "__main__":
    unittest.main()
