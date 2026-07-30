import dataclasses
import sys
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_protocol import (  # noqa: E402
    ALL_CHECKS,
    build_clean_envelope,
    build_clean_envelopes,
    build_clean_history,
    verify_envelope,
)
from tarms_experiments import aamos_protocol  # noqa: E402
from tarms_experiments.encoding import canonical_json_bytes  # noqa: E402
from tarms_experiments.merkle import MerkleTree, ProofStep  # noqa: E402


class AamosProtocolTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "participant_id": ["P01", "P02", "P01", "P01"],
                "relative_day": [7, 1, 3, 5],
                "eligible": [True, True, True, True],
                "clean_priority": [2, 0, 1, 3],
                "payload_json": [
                    '{"row":7}',
                    '{"row":1}',
                    '{"row":3}',
                    '{"row":5}',
                ],
            }
        )

    def test_clean_envelope_is_accepted(self):
        envelope, profile = build_clean_envelope(
            participant_id="P01",
            relative_day=7,
            clean_priority=2,
            payload_json='{"symptom":true}',
            seed=11,
        )
        decision = verify_envelope(envelope, profile, ALL_CHECKS)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.failure_stage, "none")
        self.assertEqual(decision.failure_reason, "accepted")

    def test_batch_builder_is_canonical_and_uses_real_participant_merkle_proofs(self):
        envelopes = build_clean_envelopes(self.frame, seed=19)
        shuffled = build_clean_envelopes(
            self.frame.sample(frac=1, random_state=4), seed=19
        )
        keys = [(item.participant_id, item.relative_day) for item in envelopes]
        self.assertEqual(keys, [("P01", 3), ("P01", 5), ("P01", 7), ("P02", 1)])
        self.assertEqual(envelopes, shuffled)

        p01 = envelopes[:3]
        self.assertEqual([item.counter for item in p01], [1, 2, 3])
        self.assertEqual([item.merkle_index for item in p01], [0, 1, 2])
        self.assertEqual([item.merkle_count for item in p01], [3, 3, 3])
        self.assertEqual(
            [[step.side for step in item.merkle_proof] for item in p01],
            [["right", "right"], ["left", "right"], ["right", "left"]],
        )
        self.assertEqual(len({item.merkle_root for item in p01}), 1)
        expected_tree = MerkleTree(
            [canonical_json_bytes(item.signed_event()) for item in p01]
        )
        self.assertEqual(p01[0].merkle_root, expected_tree.root)
        self.assertEqual(
            [item.merkle_leaf for item in p01],
            [canonical_json_bytes(item.signed_event()) for item in p01],
        )
        self.assertNotEqual(p01[0].merkle_root, envelopes[3].merkle_root)

    def test_history_profiles_are_immutable_and_hold_only_prior_acceptances(self):
        history = build_clean_history(self.frame, seed=23)
        p01 = [(envelope, profile) for envelope, profile in history if envelope.participant_id == "P01"]
        self.assertEqual(
            [profile.accepted_counters for _, profile in p01],
            [frozenset(), frozenset({1}), frozenset({1, 2})],
        )
        self.assertEqual(
            [len(profile.prior_envelopes) for _, profile in p01], [0, 1, 2]
        )
        for envelope, profile in history:
            self.assertEqual(profile.trusted_merkle_root, envelope.merkle_root)
            self.assertIn(envelope.device_id, profile.active_device_ids)
            self.assertTrue(verify_envelope(envelope, profile, ALL_CHECKS).accepted)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                profile.latest_version = 99
            with self.assertRaises(AttributeError):
                profile.accepted_counters.add(999)

    def test_compound_invalid_record_proves_ordered_first_failure(self):
        history = build_clean_history(self.frame, seed=29)
        p01 = [(envelope, profile) for envelope, profile in history if envelope.participant_id == "P01"]
        _, current_profile = p01[1]
        replayed = current_profile.prior_envelopes[-1]
        bad_step = ProofStep(
            replayed.merkle_proof[0].side,
            bytes([replayed.merkle_proof[0].sibling[0] ^ 1])
            + replayed.merkle_proof[0].sibling[1:],
        )
        compound = dataclasses.replace(
            replayed,
            payload_json=replayed.payload_json + " ",
            device_active=False,
            bound_participant="P99",
            merkle_proof=(bad_step, *replayed.merkle_proof[1:]),
            anchor_version=replayed.anchor_version - 1,
            authorized_requester="requester-elsewhere",
        )
        cases = [
            (ALL_CHECKS, "signature", "signature_invalid"),
            (ALL_CHECKS[1:], "device", "device_inactive_or_unknown"),
            (ALL_CHECKS[2:], "binding", "patient_device_binding_mismatch"),
            (ALL_CHECKS[3:], "admission", "counter_conflict"),
            (ALL_CHECKS[4:], "merkle", "leaf_event_mismatch"),
            (ALL_CHECKS[5:], "freshness", "latest_pointer_mismatch"),
            (ALL_CHECKS[6:], "authorization", "request_context_unauthorized"),
        ]
        for checks, expected_stage, expected_reason in cases:
            with self.subTest(checks=checks):
                decision = verify_envelope(compound, current_profile, checks)
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.failure_stage, expected_stage)
                self.assertEqual(decision.failure_reason, expected_reason)

    def test_merkle_root_is_checked_against_immutable_trusted_root(self):
        history = build_clean_history(self.frame, seed=31)
        envelope, profile = history[0]
        other_root = bytes([envelope.merkle_root[0] ^ 1]) + envelope.merkle_root[1:]
        mutated = dataclasses.replace(envelope, merkle_root=other_root)
        decision = verify_envelope(mutated, profile, ("merkle",))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failure_stage, "merkle")
        self.assertEqual(decision.failure_reason, "root_not_trusted")

    def test_profile_after_acceptance_returns_idempotent_admission_state(self):
        envelope, profile = build_clean_envelope(
            participant_id="P01",
            relative_day=2,
            clean_priority=1,
            payload_json='{"x":1}',
            seed=37,
        )
        advanced = aamos_protocol.profile_after_acceptance(profile, envelope)
        self.assertIsNot(advanced, profile)
        self.assertEqual(profile.accepted_counters, frozenset())
        self.assertEqual(advanced.accepted_counters, frozenset({1}))
        self.assertEqual(advanced.prior_envelopes, (envelope,))
        decision = verify_envelope(envelope, advanced, ALL_CHECKS)
        self.assertTrue(decision.accepted)
        repeated = aamos_protocol.profile_after_acceptance(
            advanced, envelope
        )
        self.assertIs(repeated, advanced)


if __name__ == "__main__":
    unittest.main()
