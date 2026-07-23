import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.protocol import (  # noqa: E402
    AdmissionResult,
    AcceptanceIndex,
    CompareAndSwapConflict,
    CounterConflictError,
    VersionContinuityError,
    VersionStore,
    generate_signing_key,
    sign_event,
    verify_event_signature,
)


class AcceptanceIndexTests(unittest.TestCase):
    def test_identical_digest_is_idempotent_but_conflict_is_rejected(self):
        index = AcceptanceIndex()
        first = index.accept_once("dev-1", 2, "boot-a", 3, "digest-original")
        repeat = index.accept_once("dev-1", 2, "boot-a", 3, "digest-original")

        with self.assertRaises(CounterConflictError):
            index.accept_once("dev-1", 2, "boot-a", 3, "digest-conflict")

        self.assertEqual(first, AdmissionResult.NEW)
        self.assertEqual(repeat, AdmissionResult.IDEMPOTENT)
        self.assertEqual(index.read("dev-1", 2, "boot-a", 3), "digest-original")

    def test_key_version_scopes_the_admission_slot(self):
        index = AcceptanceIndex()

        self.assertEqual(
            index.accept_once("dev-1", 1, "boot-a", 3, "digest-v1"),
            AdmissionResult.NEW,
        )
        self.assertEqual(
            index.accept_once("dev-1", 2, "boot-a", 3, "digest-v2"),
            AdmissionResult.NEW,
        )


class VersionStoreTests(unittest.TestCase):
    def test_cas_success_advances_exactly_one_version(self):
        store = VersionStore()
        store.initialize("patient-1|2026-07-22", "aid-v1")

        state = store.update_latest_cas(
            "patient-1|2026-07-22", "aid-v1", 1, "aid-v2", 2
        )

        self.assertEqual((state.aid, state.version), ("aid-v2", 2))

    def test_first_anchor_version_is_one(self):
        store = VersionStore()

        state = store.initialize("window", "aid-v1")

        self.assertEqual(state.version, 1)
        with self.assertRaisesRegex(VersionContinuityError, "initial version"):
            VersionStore().initialize("other", "aid-v0", 0)

    def test_stale_expected_aid_does_not_mutate_state(self):
        store = VersionStore()
        key = "patient-1|2026-07-22"
        store.initialize(key, "aid-v1")

        with self.assertRaises(CompareAndSwapConflict):
            store.update_latest_cas(key, "stale", 1, "aid-v2", 2)

        self.assertEqual((store.read_latest(key).aid, store.read_latest(key).version), ("aid-v1", 1))

    def test_skipped_version_does_not_mutate_state(self):
        store = VersionStore()
        key = "patient-1|2026-07-22"
        store.initialize(key, "aid-v1")

        with self.assertRaises(VersionContinuityError):
            store.update_latest_cas(key, "aid-v1", 1, "aid-v3", 3)

        self.assertEqual(store.read_latest(key).version, 1)


class SignatureTests(unittest.TestCase):
    def test_signature_is_bound_to_canonical_event(self):
        private_key = generate_signing_key()
        public_key = private_key.public_key()
        event = {"did": "dev-1", "counter": 9, "pef_l_min": 410}
        signature = sign_event(private_key, event)

        self.assertTrue(verify_event_signature(public_key, event, signature))
        self.assertFalse(
            verify_event_signature(
                public_key, {**event, "pef_l_min": 411}, signature
            )
        )


if __name__ == "__main__":
    unittest.main()
