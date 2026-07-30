#!/usr/bin/env python3
"""Verify a complete SHA-256 tree manifest without changing the tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


_LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  (.+)$")
_DEFAULT_MANIFEST_NAME = "MANIFEST.sha256"


def _safe_member(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise ValueError(
            f"tree manifest member is not a safe relative path: {value}"
        )
    member = PurePosixPath(value)
    if (
        member.is_absolute()
        or member.as_posix() != value
        or "." in member.parts
        or ".." in member.parts
    ):
        raise ValueError(
            f"tree manifest member is not a safe relative path: {value}"
        )
    return member


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_regular_file(root: Path, member: PurePosixPath) -> Path:
    cursor = root
    for part in member.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(
                f"tree manifest member contains a symlink: {member}"
            )
    try:
        candidate = cursor.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(
            f"tree manifest member is missing: {member}"
        ) from error
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"tree manifest member escapes the project root: {member}"
        ) from error
    if not candidate.is_file():
        raise ValueError(
            f"tree manifest member is not a regular file: {member}"
        )
    return candidate


def _parse_manifest(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("tree manifest is not UTF-8") from error
    if text and not text.endswith("\n"):
        raise ValueError("tree manifest must end with LF")
    if "\r" in text:
        raise ValueError("tree manifest must use LF line endings")

    entries: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _LINE_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(
                f"invalid tree manifest line {line_number}"
            )
        digest, raw_member = match.groups()
        member = _safe_member(raw_member).as_posix()
        if member in entries:
            raise ValueError(
                f"duplicate tree manifest member: {member}"
            )
        entries[member] = digest
    if not entries:
        raise ValueError("tree manifest is empty")
    return entries


def verify_tree_manifest(
    project_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, object]:
    """Verify hashes and reject files absent from the supplied manifest."""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    if manifest_path is None:
        raw_manifest = root / _DEFAULT_MANIFEST_NAME
    else:
        specified_manifest = Path(manifest_path)
        raw_manifest = (
            specified_manifest
            if specified_manifest.is_absolute()
            else root / specified_manifest
        )
    if raw_manifest.is_symlink():
        raise ValueError("tree manifest must not be a symlink")
    manifest = raw_manifest.resolve(strict=True)
    try:
        manifest.relative_to(root)
    except ValueError as error:
        raise ValueError(
            "tree manifest must be contained by the project root"
        ) from error
    if not manifest.is_file():
        raise ValueError("tree manifest must be a regular file")

    entries = _parse_manifest(manifest.read_bytes())
    manifest_member = manifest.relative_to(root).as_posix()
    if manifest_member in entries:
        raise ValueError("tree manifest must not list itself")

    declared: set[str] = set()
    for raw_member, expected in entries.items():
        member = _safe_member(raw_member)
        path = _resolve_regular_file(root, member)
        observed = _sha256(path)
        if observed != expected:
            raise ValueError(
                f"tree manifest SHA-256 mismatch: {raw_member}"
            )
        declared.add(member.as_posix())

    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                "project tree contains a symlink: "
                + path.relative_to(root).as_posix()
            )
        if path.is_dir():
            continue
        if path.is_file() and path != manifest:
            actual.add(path.relative_to(root).as_posix())
            continue
        if path != manifest:
            raise ValueError(
                "project tree contains an unsupported special node: "
                + path.relative_to(root).as_posix()
            )
    missing = sorted(declared.difference(actual))
    extra = sorted(actual.difference(declared))
    if missing:
        raise ValueError(
            "tree manifest members are missing: " + ", ".join(missing)
        )
    if extra:
        raise ValueError(
            "project tree contains extra files: " + ", ".join(extra)
        )
    return {"artifacts_verified": len(entries), "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a complete TARMS SHA-256 tree manifest."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument("--manifest", type=Path)
    arguments = parser.parse_args(argv)
    report = verify_tree_manifest(
        arguments.project_root,
        arguments.manifest,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
