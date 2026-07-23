#!/usr/bin/env python3
"""Generate provenance-gated TARMS figures and source-data CSV files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.plotting import (  # noqa: E402
    render_component_conformance_figure,
    render_fabric_performance_figure,
    render_late_update_figure,
    render_python_benchmark_figure,
    render_window_tradeoff_figure,
)
from tarms_experiments.provenance import load_manifest  # noqa: E402


def latest_python_run(results: Path) -> Path:
    candidates = sorted((results / "raw/python").glob("python-*"), reverse=True)
    for candidate in candidates:
        manifest = load_manifest(candidate / "run_manifest.json")
        if manifest.environment.get("profile") == "submission":
            return candidate
    raise ValueError("no submission-profile Python benchmark run found")


def latest_component_run(results: Path) -> Path:
    candidates = sorted(
        (results / "raw/python_components").glob("components-*"), reverse=True
    )
    if not candidates:
        raise ValueError("no component-conformance run found")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--figure",
        choices=("python", "fabric", "late", "component", "window"),
        required=True,
    )
    parser.add_argument("--mode", choices=("submission", "preview"), default="submission")
    parser.add_argument("--results", type=Path, default=PROJECT_ROOT / "results")
    parser.add_argument("--python-run", type=Path)
    parser.add_argument("--fabric-root", type=Path)
    parser.add_argument("--component-run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    submission = args.mode == "submission"
    output = args.output or args.results / "figures" / args.mode
    if args.figure == "python":
        run = args.python_run or latest_python_run(args.results)
        outputs = render_python_benchmark_figure(
            run / "python_microbenchmark.csv",
            args.results / "processed/python" / run.name / "python_microbenchmark_summary.csv",
            run / "run_manifest.json",
            output,
            submission=submission,
        )
    elif args.figure == "fabric":
        fabric_root = args.fabric_root or args.results / "raw/fabric"
        outputs = render_fabric_performance_figure(
            fabric_root, output, submission=submission
        )
    elif args.figure == "late":
        run = args.python_run or latest_python_run(args.results)
        fabric_root = args.fabric_root or args.results / "raw/fabric"
        outputs = render_late_update_figure(
            run / "python_microbenchmark.csv",
            run / "run_manifest.json",
            fabric_root,
            output,
            submission=submission,
        )
    elif args.figure == "component":
        run = args.component_run or latest_component_run(args.results)
        outputs = render_component_conformance_figure(
            run / "component_conformance.csv",
            run / "run_manifest.json",
            output,
            submission=submission,
        )
    else:
        outputs = render_window_tradeoff_figure(output)
    print(json.dumps({name: str(path) for name, path in outputs.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
