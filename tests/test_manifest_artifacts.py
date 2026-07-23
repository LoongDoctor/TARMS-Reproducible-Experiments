import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments import provenance  # noqa: E402


class ManifestArtifactTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> Path:
        artifact = root / "raw" / "sample.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("measured\n", encoding="utf-8")
        manifest_path = root / "raw" / "run_manifest.json"
        provenance.write_manifest(
            provenance.RunManifest(
                experiment="python",
                provenance="measured",
                run_id="python-test",
                created_at="2026-07-23T00:00:00Z",
                environment={"python": "3.12"},
                artifacts={"raw/sample.txt": provenance.sha256_file(artifact)},
            ),
            manifest_path,
        )
        return manifest_path

    def test_matching_manifest_artifacts_are_verified(self):
        verify = getattr(provenance, "verify_manifest_artifacts", None)
        self.assertIsNotNone(verify, "verify_manifest_artifacts is not implemented")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self._write_fixture(root)

            checked = verify(manifest_path, root)

            self.assertEqual(set(checked), {"raw/sample.txt"})

    def test_changed_artifact_is_rejected(self):
        verify = getattr(provenance, "verify_manifest_artifacts", None)
        self.assertIsNotNone(verify, "verify_manifest_artifacts is not implemented")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self._write_fixture(root)
            (root / "raw" / "sample.txt").write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify(manifest_path, root)


if __name__ == "__main__":
    unittest.main()
