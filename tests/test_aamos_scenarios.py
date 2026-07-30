import dataclasses
import inspect
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments import aamos  # noqa: E402
from tarms_experiments.aamos_protocol import (  # noqa: E402
    ALL_CHECKS,
    build_clean_history,
    build_history_state,
    verify_envelope,
)
from tarms_experiments.aamos_scenarios import (  # noqa: E402
    BOUNDARY_SCENARIOS,
    REJECT_SCENARIOS,
    apply_history_scenario,
    apply_scenario,
    mutate_envelope,
)
from tarms_experiments.protocol import verify_event_signature  # noqa: E402


LITERAL_REJECT_STAGES = {
    "payload_after_signing": "signature",
    "wrong_device": "device",
    "revoked_device": "device",
    "binding_mismatch": "binding",
    "counter_conflict": "admission",
    "tampered_merkle_leaf": "merkle",
    "tampered_merkle_path": "merkle",
    "tampered_merkle_root": "merkle",
    "stale_latest_pointer": "freshness",
    "authorization_substitution": "authorization",
    "historical_modification": "history",
    "historical_deletion": "history",
    "historical_insertion": "history",
    "mixed_attack": "signature",
}

LITERAL_BOUNDARIES = (
    "idempotent_retransmission",
    "pre_signing_false_payload",
    "permanent_omission",
    "clinical_measurement_error",
    "incorrect_priority_rule",
    "legitimate_late_arrival",
    "canonical_reorder",
)


def changed_fields(before, after):
    return {
        field.name
        for field in dataclasses.fields(before)
        if getattr(before, field.name) != getattr(after, field.name)
    }


class AamosScenarioTests(unittest.TestCase):
    def setUp(self):
        self.seed = 9
        self.frame = pd.DataFrame(
            {
                "participant_id": ["P01", "P01", "P01"],
                "relative_day": [1, 5, 8],
                "eligible": [True, True, True],
                "clean_priority": [0, 1, 3],
                "payload_json": ['{"x":0}', '{"x":1}', '{"x":3}'],
            }
        )
        self.snapshot, self.profile = build_history_state(
            self.frame, seed=self.seed, version=4
        )
        self.envelope = self.snapshot.envelopes[1]

    def assert_valid_signature(self, envelope, profile=None):
        trusted = profile or self.profile
        self.assertTrue(
            verify_event_signature(
                trusted.public_key, envelope.signed_event(), envelope.signature
            )
        )

    def test_catalogues_match_independent_prespecified_literal_expectations(self):
        self.assertEqual(REJECT_SCENARIOS, LITERAL_REJECT_STAGES)
        self.assertEqual(BOUNDARY_SCENARIOS, LITERAL_BOUNDARIES)

    def test_scalar_attacks_mutate_only_literal_untrusted_record_fields(self):
        expected_deltas = {
            "payload_after_signing": {"payload_json"},
            "wrong_device": {"presented_device_id"},
            "binding_mismatch": {"bound_participant"},
            "tampered_merkle_leaf": {"merkle_leaf"},
            "tampered_merkle_path": {"merkle_proof"},
            "tampered_merkle_root": {"merkle_root"},
            "stale_latest_pointer": {"anchor_version"},
            "authorization_substitution": {"authorized_requester"},
        }
        for scenario, expected in expected_deltas.items():
            with self.subTest(scenario=scenario):
                mutated, returned_profile = apply_scenario(
                    self.envelope, self.profile, scenario=scenario
                )
                self.assertIs(returned_profile, self.profile)
                self.assertEqual(changed_fields(self.envelope, mutated), expected)
                self.assertFalse(
                    {"accepted", "failure_stage", "failure_reason"}
                    & {field.name for field in dataclasses.fields(mutated)}
                )

    def test_scalar_attacks_hit_independent_literal_first_stage(self):
        expectations = {
            "payload_after_signing": "signature",
            "wrong_device": "device",
            "binding_mismatch": "binding",
            "tampered_merkle_leaf": "merkle",
            "tampered_merkle_path": "merkle",
            "tampered_merkle_root": "merkle",
            "stale_latest_pointer": "freshness",
            "authorization_substitution": "authorization",
        }
        for scenario, expected_stage in expectations.items():
            with self.subTest(scenario=scenario):
                submitted, returned_profile = apply_scenario(
                    self.envelope, self.profile, scenario=scenario
                )
                decision = verify_envelope(
                    submitted, returned_profile, ALL_CHECKS
                )
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.failure_stage, expected_stage)

    def test_revoked_device_uses_new_authoritative_registry_snapshot(self):
        self.assert_valid_signature(self.envelope)
        self.assertIn(
            self.envelope.device_id, self.profile.device_registry.active_device_ids
        )
        original_registry = self.profile.device_registry

        submitted, revoked_profile = apply_scenario(
            self.envelope, self.profile, scenario="revoked_device"
        )

        self.assertIs(submitted, self.envelope)
        self.assertIs(self.profile.device_registry, original_registry)
        self.assertIsNot(revoked_profile, self.profile)
        self.assertEqual(revoked_profile.device_registry.version, 2)
        self.assertNotIn(
            self.envelope.device_id,
            revoked_profile.device_registry.active_device_ids,
        )
        self.assertIn(
            self.envelope.device_id,
            revoked_profile.device_registry.revoked_device_ids,
        )
        decision = verify_envelope(submitted, revoked_profile, ALL_CHECKS)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failure_stage, "device")

    def test_counter_conflict_changes_admission_state_not_envelope(self):
        history = build_clean_history(self.frame.iloc[:2], seed=self.seed)
        current, current_profile = history[1]
        submitted, returned_profile = apply_scenario(
            current, current_profile, scenario="counter_conflict"
        )
        self.assertIsNot(returned_profile, current_profile)
        self.assertIs(submitted, current)
        self.assertIn(submitted.counter, returned_profile.accepted_counters)
        decision = verify_envelope(submitted, returned_profile, ALL_CHECKS)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.failure_stage, "admission")
        self.assertEqual(decision.failure_reason, "counter_conflict")

    def test_idempotent_retransmission_is_a_valid_boundary_control(self):
        history = build_clean_history(self.frame.iloc[:2], seed=self.seed)
        current, current_profile = history[1]
        submitted, returned_profile = apply_scenario(
            current,
            current_profile,
            scenario="idempotent_retransmission",
        )
        decision = verify_envelope(submitted, returned_profile, ALL_CHECKS)
        self.assertTrue(decision.accepted)

    def test_presigning_false_measurement_and_priority_are_changed_resigned_and_accepted(self):
        scenarios = (
            "pre_signing_false_payload",
            "clinical_measurement_error",
            "incorrect_priority_rule",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result = apply_history_scenario(
                    self.snapshot,
                    self.profile,
                    scenario=scenario,
                    seed=self.seed,
                )
                self.assertEqual(result.operation, scenario)
                self.assertEqual(result.before_keys, result.after_keys)
                self.assertEqual(result.before_version, result.after_version)
                self.assertNotEqual(result.before_root, result.after_root)
                before = result.before.envelope_for(result.affected_key)
                after = result.after.envelope_for(result.affected_key)
                self.assertNotEqual(before.signed_event(), after.signed_event())
                self.assertNotEqual(before.signature, after.signature)
                self.assert_valid_signature(after, result.verifier_profile)
                self.assertIsNotNone(result.decision)
                self.assertTrue(result.decision.accepted)
                self.assertEqual(result.decision.failure_stage, "none")

    def test_permanent_omission_removes_record_and_has_no_omitted_decision(self):
        requested_key = ("P01", 1)
        result = apply_history_scenario(
            self.snapshot,
            self.profile,
            scenario="permanent_omission",
            seed=self.seed,
            affected_key=requested_key,
        )
        self.assertEqual(result.operation, "permanent_omission")
        self.assertEqual(result.requested_key, requested_key)
        self.assertEqual(result.affected_key, requested_key)
        self.assertEqual(len(result.before_keys), 3)
        self.assertEqual(len(result.after_keys), 2)
        self.assertIn(result.affected_key, result.before_keys)
        self.assertNotIn(result.affected_key, result.after_keys)
        self.assertNotEqual(result.before_root, result.after_root)
        self.assertEqual(result.before_version, result.after_version)
        self.assertIsNone(result.decision)
        surviving = dict(result.record_decisions)
        self.assertEqual(tuple(surviving), result.after_keys)
        self.assertNotIn(result.affected_key, surviving)
        self.assertTrue(all(decision.accepted for decision in surviving.values()))

    def test_legitimate_late_arrival_executes_valid_successor_transition(self):
        result = apply_history_scenario(
            self.snapshot,
            self.profile,
            scenario="legitimate_late_arrival",
            seed=self.seed,
        )
        self.assertEqual(set(result.after_keys) - set(result.before_keys), {result.affected_key})
        self.assertEqual(result.after_version, result.before_version + 1)
        self.assertNotEqual(result.before_root, result.after_root)
        self.assertEqual(result.after.input_order[-1], result.affected_key)
        self.assertNotEqual(result.after.input_order, result.after.canonical_order)
        self.assertEqual(result.after_keys, tuple(sorted(result.after_keys)))
        added = result.after.envelope_for(result.affected_key)
        self.assert_valid_signature(added, result.verifier_profile)
        self.assertTrue(result.decision.accepted)
        self.assertEqual(result.decision.failure_stage, "none")

    def test_canonical_reorder_changes_input_order_but_not_effective_set_or_root(self):
        result = apply_history_scenario(
            self.snapshot,
            self.profile,
            scenario="canonical_reorder",
            seed=self.seed,
        )
        self.assertEqual(result.before_keys, result.after_keys)
        self.assertNotEqual(result.before.input_order, result.after.input_order)
        self.assertEqual(result.before.canonical_order, result.after.canonical_order)
        self.assertEqual(result.before_root, result.after_root)
        self.assertEqual(result.before_version, result.after_version)
        self.assertTrue(result.decision.accepted)

    def test_historical_set_attacks_are_real_successors_rejected_by_history_validator(self):
        expectations = {
            "historical_modification": (
                set(),
                set(),
                "historical_record_modified",
            ),
            "historical_deletion": (
                set(),
                {("P01", 5)},
                "historical_record_deleted",
            ),
            "historical_insertion": (
                {("P01", 9)},
                set(),
                "historical_record_inserted_without_authorization",
            ),
        }
        for scenario, (added, removed, reason) in expectations.items():
            with self.subTest(scenario=scenario):
                result = apply_history_scenario(
                    self.snapshot,
                    self.profile,
                    scenario=scenario,
                    seed=self.seed,
                )
                self.assertEqual(
                    set(result.after_keys) - set(result.before_keys), added
                )
                self.assertEqual(
                    set(result.before_keys) - set(result.after_keys), removed
                )
                self.assertEqual(result.after_version, result.before_version + 1)
                self.assertNotEqual(result.before_root, result.after_root)
                self.assertFalse(result.decision.accepted)
                self.assertEqual(result.decision.failure_stage, "history")
                self.assertEqual(result.decision.failure_reason, reason)
                for envelope in result.after.envelopes:
                    self.assert_valid_signature(envelope, result.verifier_profile)

    def test_contiguous_two_day_history_uses_real_withheld_late_record(self):
        frame = pd.DataFrame(
            {
                "participant_id": ["217", "217"],
                "relative_day": [0, 1],
                "eligible": [True, True],
                "clean_priority": [1, 2],
                "payload_json": ['{"observed":"day0"}', '{"observed":"day1"}'],
            }
        )
        full, profile = build_history_state(frame, seed=41, version=7)
        real_key = ("217", 0)
        real_record = full.envelope_for(real_key)

        result = apply_history_scenario(
            full,
            profile,
            scenario="legitimate_late_arrival",
            seed=41,
            affected_key=real_key,
        )

        self.assertEqual(result.before_keys, (("217", 1),))
        self.assertEqual(result.after_keys, (("217", 0), ("217", 1)))
        self.assertEqual(result.requested_key, real_key)
        self.assertEqual(result.affected_key, real_key)
        self.assertEqual(result.after_version, result.before_version + 1)
        self.assertEqual(result.after.input_order, (("217", 1), ("217", 0)))
        restored = result.after.envelope_for(real_key)
        self.assertEqual(restored.signed_event(), real_record.signed_event())
        self.assertEqual(restored.signature, real_record.signature)
        self.assertEqual(result.after_root, full.merkle_root)
        self.assertTrue(result.decision.accepted)

    def test_contiguous_two_day_history_allows_unauthorized_insertion_fixture(self):
        frame = pd.DataFrame(
            {
                "participant_id": ["217", "217"],
                "relative_day": [0, 1],
                "eligible": [True, True],
                "clean_priority": [1, 2],
                "payload_json": ['{"observed":"day0"}', '{"observed":"day1"}'],
            }
        )
        before, profile = build_history_state(frame, seed=43, version=7)
        source = before.envelope_for(("217", 1))

        result = apply_history_scenario(
            before,
            profile,
            scenario="historical_insertion",
            seed=43,
            affected_key=("217", 1),
        )

        self.assertEqual(set(result.after_keys) - set(result.before_keys), {("217", 2)})
        inserted = result.after.envelope_for(("217", 2))
        self.assertEqual(inserted.payload_json, source.payload_json)
        self.assertEqual(inserted.clean_priority, source.clean_priority)
        self.assert_valid_signature(inserted, result.verifier_profile)
        self.assertFalse(result.decision.accepted)
        self.assertEqual(result.decision.failure_stage, "history")
        self.assertEqual(
            result.decision.failure_reason,
            "historical_record_inserted_without_authorization",
        )

    def test_history_result_is_immutable_and_scalar_api_rejects_collection_names(self):
        result = apply_history_scenario(
            self.snapshot,
            self.profile,
            scenario="canonical_reorder",
            seed=self.seed,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.operation = "changed"
        for scenario in (
            "historical_modification",
            "historical_deletion",
            "historical_insertion",
            *LITERAL_BOUNDARIES[1:],
        ):
            with self.subTest(scenario=scenario):
                with self.assertRaisesRegex(ValueError, "collection-level"):
                    apply_scenario(self.envelope, self.profile, scenario=scenario)

    def test_mutate_envelope_public_api_never_returns_a_profile_or_decision(self):
        mutated = mutate_envelope(
            self.envelope,
            "payload_after_signing",
            np.random.default_rng(17),
            profile=self.profile,
        )
        self.assertEqual(changed_fields(self.envelope, mutated), {"payload_json"})
        self.assertFalse(hasattr(mutated, "accepted"))

    def test_legacy_injector_is_explicitly_retired(self):
        fixture = pd.DataFrame(
            {
                "participant_id": ["P01"],
                "date": [pd.Timestamp("2026-01-01")],
                "eligible": [True],
                "clean_priority": [1],
            }
        )
        with self.assertRaisesRegex(RuntimeError, "retired.*unified verifier"):
            aamos.inject_integrity_violations(fixture, seed=1, rate=1.0)
        runner_source = (
            PROJECT_ROOT / "scripts" / "run_aamos_analysis.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("inject_integrity_violations", runner_source)
        self.assertNotIn('"accepted"', inspect.getsource(aamos.inject_integrity_violations))
        self.assertNotIn(
            '"verified_priority"', inspect.getsource(aamos.inject_integrity_violations)
        )


if __name__ == "__main__":
    unittest.main()
