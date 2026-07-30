#!/usr/bin/env python3
"""Build and inspect a deterministic controlled-source snapshot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.release_identity import (  # noqa: E402
    _paths_alias,
    _reject_controlled_destination,
    controlled_source_members,
    inspect_controlled_snapshot,
    write_controlled_snapshot,
)


def _preflight_destinations(
    output: Path,
    json_output: Path,
    members: tuple[str, ...],
) -> None:
    if _paths_alias(output, json_output):
        raise ValueError("ZIP and JSON destinations must be distinct")
    _reject_controlled_destination(PROJECT_ROOT, output, members)
    _reject_controlled_destination(PROJECT_ROOT, json_output, members)


def _temporary_path(destination: Path) -> Path:
    file_descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(file_descriptor)
    return Path(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()

    members = controlled_source_members(PROJECT_ROOT)
    _preflight_destinations(args.output, args.json_output, members)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    _preflight_destinations(args.output, args.json_output, members)
    staged_zip = _temporary_path(args.output)
    staged_json = _temporary_path(args.json_output)
    try:
        written = write_controlled_snapshot(
            PROJECT_ROOT,
            staged_zip,
            members,
        )
        inspected = inspect_controlled_snapshot(staged_zip)
        if written != inspected:
            raise RuntimeError(
                "controlled-source snapshot inspection disagrees "
                "with its build"
            )
        staged_json.write_text(
            json.dumps(inspected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _preflight_destinations(args.output, args.json_output, members)
        os.replace(staged_zip, args.output)
        if inspect_controlled_snapshot(args.output) != inspected:
            raise RuntimeError(
                "published controlled-source snapshot failed inspection"
            )
        os.replace(staged_json, args.json_output)
    finally:
        staged_zip.unlink(missing_ok=True)
        staged_json.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
