import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FabricScaffoldTests(unittest.TestCase):
    def test_bootstrap_enrolls_a_role_authorized_anchor_writer(self):
        bootstrap = (PROJECT_ROOT / "fabric/network/bootstrap.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("tarms.role=anchor-writer:ecert", bootstrap)
        self.assertIn("TarmsWriter@org1.example.com", bootstrap)
        self.assertIn('export FABRIC_CA_CLIENT_HOME="${ORG1_ROOT}"', bootstrap)
        self.assertIn("-u https://localhost:7054", bootstrap)
        self.assertNotIn("ORG1_ADMIN_MSP", bootstrap)


if __name__ == "__main__":
    unittest.main()
