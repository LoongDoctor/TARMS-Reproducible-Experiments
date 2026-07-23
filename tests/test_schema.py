import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.schema import (  # noqa: E402
    SchemaValidationError,
    validate_fabric_jsonl,
    validate_raw_table,
)


class RawSchemaTests(unittest.TestCase):
    def _write_csv(self, fieldnames, rows):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "raw.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return directory, path

    def test_python_raw_requires_duration_and_provenance(self):
        directory, path = self._write_csv(
            ["run_id", "seed", "batch_size", "repetition", "stage"],
            [
                {
                    "run_id": "r1",
                    "seed": 1,
                    "batch_size": 16,
                    "repetition": 0,
                    "stage": "merkle_build",
                }
            ],
        )
        self.addCleanup(directory.cleanup)

        with self.assertRaisesRegex(SchemaValidationError, "duration_ns"):
            validate_raw_table(path, "python_raw")

    def test_python_raw_accepts_valid_positive_observation(self):
        columns = [
            "run_id",
            "seed",
            "batch_size",
            "repetition",
            "stage",
            "duration_ns",
            "record_count",
            "late_count",
            "provenance",
        ]
        directory, path = self._write_csv(
            columns,
            [
                {
                    "run_id": "r1",
                    "seed": 1,
                    "batch_size": 16,
                    "repetition": 0,
                    "stage": "merkle_build",
                    "duration_ns": 1234,
                    "record_count": 16,
                    "late_count": 0,
                    "provenance": "measured",
                }
            ],
        )
        self.addCleanup(directory.cleanup)

        report = validate_raw_table(path, "python_raw")

        self.assertEqual(report.row_count, 1)
        self.assertEqual(report.provenance_values, frozenset({"measured"}))

    def test_python_raw_rejects_nonpositive_duration(self):
        columns = [
            "run_id",
            "seed",
            "batch_size",
            "repetition",
            "stage",
            "duration_ns",
            "record_count",
            "late_count",
            "provenance",
        ]
        directory, path = self._write_csv(
            columns,
            [
                {
                    "run_id": "r1",
                    "seed": 1,
                    "batch_size": 16,
                    "repetition": 0,
                    "stage": "merkle_build",
                    "duration_ns": 0,
                    "record_count": 16,
                    "late_count": 0,
                    "provenance": "measured",
                }
            ],
        )
        self.addCleanup(directory.cleanup)

        with self.assertRaisesRegex(SchemaValidationError, "positive"):
            validate_raw_table(path, "python_raw")

    def test_fabric_jsonl_validates_shared_fields_and_submission_provenance(self):
        row = {
            "run_id": "r1",
            "workload": "query",
            "client_id": "c1",
            "operation": "ReadLatest",
            "start_ns": 1,
            "end_ns": 3,
            "duration_ns": 2,
            "txid": "",
            "commit_status": "EVALUATED",
            "block_number": -1,
            "attempt": 1,
            "error_class": "",
            "payload_bytes": 10,
            "provenance": "measured_fabric",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fabric.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = validate_fabric_jsonl(path, submission=True)

        self.assertEqual(report.row_count, 1)
        self.assertEqual(report.provenance_values, frozenset({"measured_fabric"}))

    def test_fabric_fixture_is_rejected_for_submission(self):
        row = {
            "run_id": "r1", "workload": "query", "client_id": "c1",
            "operation": "ReadLatest", "start_ns": 1, "end_ns": 3,
            "duration_ns": 2, "txid": "", "commit_status": "EVALUATED",
            "block_number": -1, "attempt": 1, "error_class": "",
            "payload_bytes": 10, "provenance": "fixture",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fabric.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SchemaValidationError, "fixture"):
                validate_fabric_jsonl(path, submission=True)


if __name__ == "__main__":
    unittest.main()
