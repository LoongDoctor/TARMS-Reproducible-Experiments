import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_source import build_patient_days  # noqa: E402


def derivation_config(*, peakflow_required=False, inhaler_required=False):
    return {
        "version": 1,
        "source": {
            "participant_column": "user_key",
            "relative_day_column": "date",
            "daily_questionnaire": {
                "filename": "daily.csv",
                "required": True,
            },
            "modalities": {
                "peakflow": {
                    "filename": "peak.csv",
                    "required": peakflow_required,
                    "count_field": "peakflow_readings",
                },
                "inhaler": {
                    "filename": "inhaler.csv",
                    "required": inhaler_required,
                    "count_field": "inhaler_events",
                },
            },
        },
        "priority": {
            "rule": "sum_boolean_true",
            "symptom_columns": ["night", "day", "limited"],
            "missing_policy": "retain_ineligible",
        },
        "payload": {
            "daily_columns": ["night", "day", "limited", "reliever"],
            "include_modality_counts": True,
        },
        "flow": {"exclude_negative_modality_days": True},
        "data_contract": {
            "observed": ["payload.daily_columns", "modality event counts"],
            "derived": ["eligible", "clean_priority"],
            "synthetic": ["all TARMS protocol metadata"],
        },
    }


def write_daily(root: Path, rows=None):
    if rows is None:
        rows = [
            {
                "user_key": 101,
                "date": 0,
                "night": False,
                "day": False,
                "limited": False,
                "reliever": 0,
            },
            {
                "user_key": 101,
                "date": 1,
                "night": True,
                "day": False,
                "limited": True,
                "reliever": 2,
            },
            {
                "user_key": 202,
                "date": 4,
                "night": True,
                "day": True,
                "limited": True,
                "reliever": 3,
            },
        ]
    pd.DataFrame(rows).to_csv(root / "daily.csv", index=False)


class AamosSourceTests(unittest.TestCase):
    def test_config_drives_priority_presence_missingness_and_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_daily(root)
            pd.DataFrame(
                {
                    "user_key": [101, 101, 101, 202],
                    "date": [-2, 0, 1, 1],
                }
            ).to_csv(root / "peak.csv", index=False)
            expected_daily_sha256 = hashlib.sha256(
                (root / "daily.csv").read_bytes()
            ).hexdigest()

            frame, flow = build_patient_days(root, derivation_config())

        self.assertEqual(frame["relative_day"].tolist(), [0, 1, 4])
        self.assertTrue(pd.api.types.is_integer_dtype(frame["relative_day"]))
        self.assertFalse(frame.duplicated(["participant_id", "relative_day"]).any())
        self.assertEqual(frame["clean_priority"].tolist(), [0, 2, 3])
        self.assertEqual(frame["peakflow_source_present"].tolist(), [True, True, True])
        self.assertEqual(frame["peakflow_day_present"].tolist(), [True, True, False])
        self.assertEqual(frame["peakflow_readings"].tolist(), [1, 1, 0])
        self.assertEqual(frame["inhaler_source_present"].tolist(), [False, False, False])
        self.assertEqual(frame["inhaler_day_present"].tolist(), [False, False, False])
        self.assertTrue(frame["inhaler_events"].isna().all())
        payloads = frame["payload_json"].map(json.loads).tolist()
        self.assertEqual(payloads[0]["peakflow_readings"], 1)
        self.assertEqual(payloads[2]["peakflow_readings"], 0)
        self.assertIsNone(payloads[0]["inhaler_events"])
        self.assertEqual(
            flow["modalities"]["peakflow"],
            {
                "source_present": True,
                "source_rows": 4,
                "excluded_negative_day_rows": 1,
                "included_rows": 3,
                "participant_days_with_records": 3,
                "questionnaire_days_with_records": 2,
            },
        )
        self.assertEqual(
            flow["modalities"]["inhaler"],
            {
                "source_present": False,
                "source_rows": 0,
                "excluded_negative_day_rows": 0,
                "included_rows": 0,
                "participant_days_with_records": 0,
                "questionnaire_days_with_records": 0,
            },
        )
        self.assertEqual(flow["participants"], 2)
        self.assertEqual(flow["participant_days"], 3)
        inventory = {item["name"]: item for item in flow["source_files"]}
        self.assertEqual(
            inventory["daily.csv"]["sha256"],
            expected_daily_sha256,
        )
        self.assertEqual(flow["derivation"]["priority_rule"], "sum_boolean_true")
        self.assertEqual(
            flow["data_contract"]["synthetic"], ["all TARMS protocol metadata"]
        )

    def test_present_modality_with_no_day_is_zero_not_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_daily(root)
            pd.DataFrame(columns=["user_key", "date"]).to_csv(
                root / "inhaler.csv", index=False
            )

            frame, _ = build_patient_days(root, derivation_config())

        self.assertEqual(frame["inhaler_source_present"].tolist(), [True, True, True])
        self.assertEqual(frame["inhaler_day_present"].tolist(), [False, False, False])
        self.assertEqual(frame["inhaler_events"].tolist(), [0, 0, 0])

    def test_required_modality_source_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_daily(root)
            with self.assertRaisesRegex(FileNotFoundError, "peak.csv"):
                build_patient_days(root, derivation_config(peakflow_required=True))

    def test_rejects_duplicate_daily_questionnaire_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {
                "user_key": 101,
                "date": 0,
                "night": False,
                "day": False,
                "limited": False,
                "reliever": 0,
            }
            write_daily(root, [row, row])
            with self.assertRaisesRegex(ValueError, "duplicate participant/day"):
                build_patient_days(root, derivation_config())

    def test_rejects_daily_key_collision_after_participant_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {
                "user_key": "P01",
                "date": 0,
                "night": False,
                "day": False,
                "limited": False,
                "reliever": 0,
            }
            second = {**first, "user_key": " P01 "}
            write_daily(root, [first, second])
            with self.assertRaisesRegex(ValueError, "duplicate participant/day"):
                build_patient_days(root, derivation_config())

    def test_rejects_null_or_blank_daily_participant_ids(self):
        for participant_id in (None, "", "   "):
            with self.subTest(participant_id=participant_id):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_daily(
                        root,
                        [
                            {
                                "user_key": participant_id,
                                "date": 0,
                                "night": False,
                                "day": False,
                                "limited": False,
                                "reliever": 0,
                            }
                        ],
                    )
                    with self.assertRaisesRegex(
                        ValueError, "daily.csv.*participant.*null or blank"
                    ):
                        build_patient_days(root, derivation_config())

    def test_rejects_null_or_blank_modality_participant_ids(self):
        for participant_id in (None, "", "   "):
            with self.subTest(participant_id=participant_id):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_daily(root)
                    pd.DataFrame(
                        {"user_key": [participant_id], "date": [0]}
                    ).to_csv(root / "peak.csv", index=False)
                    with self.assertRaisesRegex(
                        ValueError, "peak.csv.*participant.*null or blank"
                    ):
                        build_patient_days(root, derivation_config())

    def test_rejects_non_boolean_symptom_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_daily(
                root,
                [
                    {
                        "user_key": 101,
                        "date": 0,
                        "night": "maybe",
                        "day": False,
                        "limited": False,
                        "reliever": 0,
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "night.*boolean"):
                build_patient_days(root, derivation_config())

    def test_incomplete_symptom_rows_are_retained_but_ineligible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "user_key": 101,
                    "date": 0,
                    "night": False,
                    "day": False,
                    "limited": False,
                    "reliever": 0,
                },
                {
                    "user_key": 101,
                    "date": 1,
                    "night": True,
                    "day": False,
                    "limited": None,
                    "reliever": 1,
                },
            ]
            write_daily(root, rows)
            frame, flow = build_patient_days(root, derivation_config())

        self.assertEqual(frame["eligible"].tolist(), [True, False])
        self.assertEqual(frame.loc[0, "clean_priority"], 0)
        self.assertTrue(pd.isna(frame.loc[1, "clean_priority"]))
        self.assertEqual(flow["eligible_participant_days"], 1)

    def test_build_script_writes_table_and_flow_from_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_daily(root)
            config_path = root / "derivation.yaml"
            config = derivation_config()
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            expected_config_hash = hashlib.sha256(
                json.dumps(
                    config,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            output_path = root / "patient_days.csv"
            flow_path = root / "flow.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "build_aamos_patient_days.py"),
                    "--source-dir",
                    str(root),
                    "--config",
                    str(config_path),
                    "--output",
                    str(output_path),
                    "--flow-output",
                    str(flow_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"participant_days": 3', completed.stdout)
            self.assertIn(expected_config_hash, completed.stdout)
            self.assertEqual(len(pd.read_csv(output_path)), 3)
            flow = json.loads(flow_path.read_text())
            self.assertEqual(flow["participants"], 2)
            self.assertEqual(
                flow["derivation"]["config_canonical_sha256"],
                expected_config_hash,
            )

    def test_standard_runner_exposes_derivation_config_and_boundary_catalogue(self):
        runner = PROJECT_ROOT / "scripts" / "run_aamos_standard_enhanced.py"
        completed = subprocess.run(
            [sys.executable, str(runner), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--derivation-config", completed.stdout)
        source = runner.read_text(encoding="utf-8")
        self.assertIn("BOUNDARY_SCENARIOS", source)


if __name__ == "__main__":
    unittest.main()
