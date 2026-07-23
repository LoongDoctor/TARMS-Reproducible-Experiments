#!/usr/bin/env python3
"""Run, validate, and summarize TARMS Python microbenchmarks."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import cryptography
import numpy
import pandas


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.benchmark import run_microbenchmark  # noqa: E402
from tarms_experiments.provenance import (  # noqa: E402
    RunManifest,
    sha256_file,
    write_manifest,
)
from tarms_experiments.schema import validate_raw_table  # noqa: E402
from tarms_experiments.stats import summarize_observations  # noqa: E402


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unreported"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("quick", "submission"), default="quick")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "results")
    args = parser.parse_args()

    if args.profile == "submission":
        sizes, warmups, repetitions = [16, 64, 256, 1024, 2048, 4096], 20, 200
    else:
        sizes, warmups, repetitions = [16, 64], 1, 3

    created = datetime.now(timezone.utc)
    run_id = created.strftime("python-%Y%m%dT%H%M%SZ")
    raw_dir = args.output_root / "raw" / "python" / run_id
    processed_dir = args.output_root / "processed" / "python" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "python_microbenchmark.csv"
    summary_path = processed_dir / "python_microbenchmark_summary.csv"

    frame = run_microbenchmark(
        batch_sizes=sizes,
        warmups=warmups,
        repetitions=repetitions,
        seed=20260722,
        run_id=run_id,
    )
    frame.to_csv(raw_path, index=False)
    report = validate_raw_table(raw_path, "python_raw")
    summary = summarize_observations(frame, bootstrap_reps=2_000, seed=20260722)
    summary.to_csv(summary_path, index=False)

    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu": _cpu_model(),
        "logical_cpus": os.cpu_count(),
        "cryptography": cryptography.__version__,
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "timer": "time.perf_counter_ns",
        "profile": args.profile,
        "warmups": warmups,
        "repetitions": repetitions,
        "batch_sizes": sizes,
    }
    environment_path = raw_dir / "environment.json"
    environment_path.write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = RunManifest(
        experiment="python",
        provenance="measured",
        run_id=run_id,
        created_at=created.isoformat(),
        environment=environment,
        artifacts={
            str(raw_path.relative_to(args.output_root)): sha256_file(raw_path),
            str(summary_path.relative_to(args.output_root)): sha256_file(summary_path),
            str(environment_path.relative_to(args.output_root)): sha256_file(environment_path),
        },
    )
    write_manifest(manifest, raw_dir / "run_manifest.json")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "rows": report.row_count,
                "raw": str(raw_path),
                "summary": str(summary_path),
                "manifest": str(raw_dir / "run_manifest.json"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
