#!/usr/bin/env python3
"""Verify the artifact hashes recorded by the bundled measured runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.provenance import (  # noqa: E402
    load_manifest,
    verify_manifest_artifacts,
)


MANIFESTS = (
    RESULTS_ROOT
    / "raw/python/python-20260723T020649Z/run_manifest.json",
    RESULTS_ROOT
    / "raw/python_conformance/conformance-20260723T020659Z/run_manifest.json",
    RESULTS_ROOT
    / "raw/python_components/components-20260723T020700Z/run_manifest.json",
)


def main() -> int:
    report = {}
    for manifest_path in MANIFESTS:
        manifest = load_manifest(manifest_path)
        checked = verify_manifest_artifacts(manifest_path, RESULTS_ROOT)
        report[manifest.run_id] = {
            "artifacts_verified": len(checked),
            "status": "ok",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
