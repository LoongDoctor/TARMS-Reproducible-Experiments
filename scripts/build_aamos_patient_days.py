#!/usr/bin/env python3
"""Build the configured AAMOS-00 participant-day table and source-flow record."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.aamos_source import build_patient_days  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "aamos00_derivation.yaml",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flow-output", type=Path, required=True)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    patient_days, flow = build_patient_days(args.source_dir, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.flow_output.parent.mkdir(parents=True, exist_ok=True)
    patient_days.to_csv(args.output, index=False)
    args.flow_output.write_text(
        json.dumps(flow, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "participants": flow["participants"],
                "participant_days": flow["participant_days"],
                "eligible_participant_days": flow["eligible_participant_days"],
                "config_canonical_sha256": flow["derivation"][
                    "config_canonical_sha256"
                ],
                "output": str(args.output),
                "flow_output": str(args.flow_output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
