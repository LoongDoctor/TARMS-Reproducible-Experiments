#!/usr/bin/env python3
"""Run deterministic TARMS late-update conformance cases."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.conformance import run_late_update_conformance  # noqa: E402
from tarms_experiments.provenance import (  # noqa: E402
    RunManifest,
    sha256_file,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=200)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "results")
    args = parser.parse_args()
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")

    created = datetime.now(timezone.utc)
    run_id = created.strftime("conformance-%Y%m%dT%H%M%SZ")
    raw_dir = args.output_root / "raw/python_conformance" / run_id
    processed_dir = args.output_root / "processed/python_conformance" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "late_update_conformance.csv"
    summary_path = processed_dir / "late_update_conformance_summary.csv"

    rows = run_late_update_conformance(
        repetitions=args.repetitions, seed=20260722, run_id=run_id
    )
    rows.to_csv(raw_path, index=False)
    summary = (
        rows.groupby(["case", "expected_result", "observed_result"], as_index=False)
        .agg(matching_executions=("matches_rule", "sum"), total_executions=("matches_rule", "size"))
    )
    summary["proportion_matching"] = (
        summary["matching_executions"] / summary["total_executions"]
    )
    summary.to_csv(summary_path, index=False)

    environment = {
        "python": platform.python_version(),
        "repetitions_per_case": args.repetitions,
        "master_seed": 20260722,
        "canonical_order": "lexicographic canonical event bytes",
        "odd_leaf_rule": "duplicate last node",
    }
    manifest = RunManifest(
        experiment="python",
        provenance="measured",
        run_id=run_id,
        created_at=created.isoformat(),
        environment=environment,
        artifacts={
            str(raw_path.relative_to(args.output_root)): sha256_file(raw_path),
            str(summary_path.relative_to(args.output_root)): sha256_file(summary_path),
        },
    )
    manifest_path = raw_dir / "run_manifest.json"
    write_manifest(manifest, manifest_path)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "raw": str(raw_path),
                "summary": str(summary_path),
                "manifest": str(manifest_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
