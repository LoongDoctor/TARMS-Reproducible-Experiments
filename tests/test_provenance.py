import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.provenance import (  # noqa: E402
    EvidenceGateError,
    RunManifest,
    assert_submission_eligible,
    load_manifest,
    sha256_file,
)


class ProvenanceGateTests(unittest.TestCase):
    def test_fixture_cannot_generate_submission_artifact(self):
        manifest = RunManifest(
            experiment="fabric",
            provenance="fixture",
            run_id="r1",
            created_at="2026-07-22T00:00:00Z",
            environment={"node": "24.14.0"},
            artifacts={},
        )

        with self.assertRaisesRegex(EvidenceGateError, "fixture"):
            assert_submission_eligible([manifest])

    def test_experiment_requires_matching_submission_provenance(self):
        cases = [
            ("python", "measured"),
            ("fabric", "measured_fabric"),
        ]
        manifests = [
            RunManifest(
                experiment=experiment,
                provenance=provenance,
                run_id=f"{experiment}-r1",
                created_at="2026-07-22T00:00:00Z",
                environment={"python": "3.12"},
                artifacts={},
            )
            for experiment, provenance in cases
        ]

        assert_submission_eligible(manifests)

    def test_load_manifest_rejects_missing_environment(self):
        payload = {
            "schema_version": "1.0",
            "experiment": "python",
            "provenance": "measured",
            "run_id": "python-r1",
            "created_at": "2026-07-22T00:00:00Z",
            "artifacts": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run_manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "environment"):
                load_manifest(path)

    def test_sha256_file_matches_known_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )


if __name__ == "__main__":
    unittest.main()
