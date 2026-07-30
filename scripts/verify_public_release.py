#!/usr/bin/env python3
"""Verify every publishable artifact and its controlled-source binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.release_identity import (  # noqa: E402
    controlled_source_members,
    inspect_controlled_snapshot,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "public_release_manifest.json"
FIXED_DERIVATION_CONFIG_MEMBER = "config/aamos00_derivation.yaml"
CONTROLLED_SNAPSHOT_MEMBER = "controlled-source.zip"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_LOCAL_PATH_FRAGMENTS = (
    "/workspace/",
    "/users/",
    "/home/",
    "\\users\\",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("public artifact must use a safe canonical relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("public artifact must use a safe canonical relative path")
    return path


def _canonical_artifact_name(value: object, *, label: str) -> str:
    path = _safe_relative_path(value)
    if len(path.parts) != 1 or path.name.startswith("."):
        raise ValueError(f"{label} must be a safe canonical artifact name")
    return path.name


def _manifest_strings(value: object):
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                yield key
            yield from _manifest_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _manifest_strings(nested)
    elif isinstance(value, str):
        yield value


def _reject_path_leakage(value: object, *, label: str) -> None:
    for text in _manifest_strings(value):
        folded = text.casefold()
        if (
            text.startswith(("/", "\\"))
            or _WINDOWS_ABSOLUTE_PATTERN.match(text)
            or any(fragment in folded for fragment in _LOCAL_PATH_FRAGMENTS)
        ):
            raise ValueError(
                f"{label} contains an absolute or local path; "
                "manifest strings must use relative member names"
            )


def _strict_json_bytes(data: bytes, *, label: str) -> object:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from error


def _required_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a 64-character lowercase SHA-256")
    return value


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _controlled_source_contract(
    manifest: Mapping[str, object],
    *,
    label: str,
) -> dict[str, object]:
    controlled = manifest.get("controlled_source")
    if not isinstance(controlled, Mapping):
        raise ValueError(f"{label} lacks controlled_source")
    identity = _required_sha256(
        controlled.get("identity_sha256"),
        label=f"{label} controlled-source identity",
    )
    snapshot = _required_sha256(
        controlled.get("snapshot_sha256"),
        label=f"{label} controlled-source snapshot",
    )
    count = controlled.get("member_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError(
            f"{label} controlled-source member count must be a positive integer"
        )
    member_value = controlled.get("derivation_config_member")
    member = _safe_relative_path(member_value).as_posix()
    if member != FIXED_DERIVATION_CONFIG_MEMBER:
        raise ValueError(
            f"{label} controlled-source derivation member must be "
            f"{FIXED_DERIVATION_CONFIG_MEMBER}"
        )
    return {
        "identity_sha256": identity,
        "snapshot_sha256": snapshot,
        "member_count": count,
        "derivation_config_member": member,
    }


def _require_contract_match(
    observed: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    for field in (
        "identity_sha256",
        "snapshot_sha256",
        "member_count",
        "derivation_config_member",
    ):
        if observed.get(field) != expected.get(field):
            raise ValueError(
                f"{label} controlled-source {field} does not match "
                "the current source and distributed snapshot"
            )


def _resolve_declared_artifact(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(
                f"public artifact path contains a symlink: {relative}"
            )
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"public artifact is missing: {relative}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"public artifact is not contained by the project root: {relative}"
        ) from error
    if not resolved.is_file():
        raise ValueError(f"public artifact is not a regular file: {relative}")
    return resolved


def _formal_artifact_contract(
    run_manifest: Mapping[str, object],
) -> dict[str, str]:
    raw = run_manifest.get("artifacts")
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(
            "formal run manifest requires a nonempty artifacts object"
        )
    result: dict[str, str] = {}
    for raw_name, raw_hash in raw.items():
        name = _canonical_artifact_name(
            raw_name, label="formal artifact name"
        )
        result[name] = _required_sha256(
            raw_hash, label=f"formal artifact {name} hash"
        )
    return result


def _omission_contract(
    manifest: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    raw = manifest.get("locally_regenerated_artifacts", {})
    if not isinstance(raw, Mapping):
        raise ValueError("locally_regenerated_artifacts must be an object")
    result: dict[str, dict[str, object]] = {}
    for raw_name, raw_details in raw.items():
        name = _canonical_artifact_name(
            raw_name, label="regenerated omission name"
        )
        if not isinstance(raw_details, Mapping):
            raise ValueError(
                f"regenerated omission declaration is incomplete: {name}"
            )
        if raw_details.get("published") is not False:
            raise ValueError(
                f"regenerated omission must set published false: {name}"
            )
        canonical_hash = _required_sha256(
            raw_details.get("canonical_sha256"),
            label=f"regenerated omission {name} hash",
        )
        reason = raw_details.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "regenerated omission declaration is incomplete; "
                f"reason must be nonempty: {name}"
            )
        result[name] = {
            "published": False,
            "canonical_sha256": canonical_hash,
            "reason": reason,
        }
    return result


def _reconcile_formal_artifacts(
    formal: Mapping[str, str],
    published: Mapping[str, str],
    omissions: Mapping[str, Mapping[str, object]],
) -> None:
    extra_omissions = sorted(set(omissions).difference(formal))
    if extra_omissions:
        raise ValueError(
            "regenerated omissions are not formal artifacts: "
            + ", ".join(extra_omissions)
        )

    published_by_basename: dict[str, list[str]] = {}
    for member in published:
        published_by_basename.setdefault(
            PurePosixPath(member).name, []
        ).append(member)

    for name, formal_hash in formal.items():
        published_members = published_by_basename.get(name, [])
        omitted = name in omissions
        if len(published_members) > 1:
            raise ValueError(
                f"formal artifact has ambiguous published basenames: {name}"
            )
        if bool(published_members) == omitted:
            raise ValueError(
                "formal artifact must have exactly one published or omitted "
                f"representation: {name}"
            )
        if published_members:
            member = published_members[0]
            if published[member] != formal_hash:
                raise ValueError(
                    f"formal artifact hash does not match public artifact: {name}"
                )
        elif omissions[name]["canonical_sha256"] != formal_hash:
            raise ValueError(
                f"formal artifact hash does not match omission: {name}"
            )


def _inspect_snapshot_bytes(data: bytes) -> dict[str, object]:
    with tempfile.TemporaryDirectory(
        prefix="tarms-controlled-snapshot-"
    ) as directory:
        private_copy = Path(directory) / CONTROLLED_SNAPSHOT_MEMBER
        private_copy.write_bytes(data)
        return inspect_controlled_snapshot(private_copy)


def _cached_controlled_source_identity(
    members: tuple[str, ...],
    payloads: Mapping[str, bytes],
) -> str:
    digest = hashlib.sha256()
    for member in sorted(members):
        digest.update(member.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payloads[member])
        digest.update(b"\0")
    return digest.hexdigest()


def verify_public_release(
    manifest_path: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Recompute artifacts and bind source, snapshot, run, and public records."""

    manifest_bytes = Path(manifest_path).read_bytes()
    manifest = _strict_json_bytes(
        manifest_bytes, label="public release manifest"
    )
    if not isinstance(manifest, Mapping):
        raise ValueError("public release manifest root must be an object")
    _reject_path_leakage(manifest, label="public release manifest")
    public_controlled = _controlled_source_contract(
        manifest, label="public manifest"
    )
    public_run_id = _required_text(
        manifest.get("run_id"), label="public manifest run ID"
    )
    formal_manifest_sha256 = _required_sha256(
        manifest.get("formal_run_manifest_sha256"),
        label="formal run manifest hash",
    )

    artifacts = manifest.get("public_artifacts")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise ValueError("public release manifest lacks public_artifacts")

    root = Path(project_root).resolve(strict=True)
    current_members = controlled_source_members(root)
    controlled_member_set = set(current_members)
    checked: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
    resolved_members: set[Path] = set()
    for relative, expected_value in sorted(artifacts.items()):
        relative_path = _safe_relative_path(relative)
        canonical = relative_path.as_posix()
        expected = _required_sha256(
            expected_value,
            label=f"public artifact {canonical} hash",
        )
        path = _resolve_declared_artifact(root, relative_path)
        if path in resolved_members:
            raise ValueError(
                f"public artifact path is a duplicate alias: {canonical}"
            )
        resolved_members.add(path)
        data = path.read_bytes()
        observed = _sha256_bytes(data)
        if observed != expected:
            raise ValueError(
                f"SHA-256 mismatch for public artifact: {canonical}"
            )
        checked[canonical] = observed
        if (
            canonical in controlled_member_set
            or relative_path.name == "run_manifest.json"
            or canonical == CONTROLLED_SNAPSHOT_MEMBER
        ):
            payloads[canonical] = data

    missing_controlled = [
        member for member in current_members if member not in payloads
    ]
    if missing_controlled:
        raise ValueError(
            "controlled-source members must be declared as public artifacts: "
            + ", ".join(missing_controlled)
        )

    declared_run_member = manifest.get("formal_run_manifest_member")
    if declared_run_member is None:
        run_members = [
            relative
            for relative in checked
            if PurePosixPath(relative).name == "run_manifest.json"
        ]
        if len(run_members) != 1:
            raise ValueError(
                "public release must declare exactly one run manifest "
                "or an explicit formal_run_manifest_member"
            )
        run_member = run_members[0]
    else:
        run_member = _safe_relative_path(
            declared_run_member
        ).as_posix()
        if PurePosixPath(run_member).name != "run_manifest.json":
            raise ValueError(
                "formal_run_manifest_member must name run_manifest.json"
            )
        if run_member not in checked:
            raise ValueError(
                "formal_run_manifest_member is not a public artifact"
            )
    if checked[run_member] != formal_manifest_sha256:
        raise ValueError(
            "declared run manifest does not match "
            "formal_run_manifest_sha256"
        )
    run_manifest = _strict_json_bytes(
        payloads[run_member], label="formal run manifest"
    )
    if not isinstance(run_manifest, Mapping):
        raise ValueError("formal run manifest root must be an object")
    _reject_path_leakage(run_manifest, label="formal run manifest")
    formal_run_id = _required_text(
        run_manifest.get("run_id"), label="formal manifest run ID"
    )
    if public_run_id != formal_run_id:
        raise ValueError("public and formal manifest run IDs do not match")
    run_controlled = _controlled_source_contract(
        run_manifest, label="run manifest"
    )

    formal_artifacts = _formal_artifact_contract(run_manifest)
    omissions = _omission_contract(manifest)
    _reconcile_formal_artifacts(formal_artifacts, checked, omissions)

    snapshot_hash = checked.get(CONTROLLED_SNAPSHOT_MEMBER)
    if snapshot_hash is None:
        raise ValueError(
            "public release must declare controlled-source.zip"
        )
    if snapshot_hash != public_controlled["snapshot_sha256"]:
        raise ValueError(
            "controlled-source.zip hash does not match the public "
            "controlled-source snapshot SHA-256"
        )
    snapshot_record = _inspect_snapshot_bytes(
        payloads[CONTROLLED_SNAPSHOT_MEMBER]
    )

    current_identity = _cached_controlled_source_identity(
        current_members, payloads
    )
    snapshot_members = tuple(snapshot_record["members"])
    if snapshot_members != current_members:
        raise ValueError(
            "distributed controlled-source snapshot members do not match "
            "the current controlled source"
        )
    if snapshot_record["identity_sha256"] != current_identity:
        raise ValueError(
            "distributed controlled-source snapshot identity does not match "
            "the current controlled source identity"
        )
    if FIXED_DERIVATION_CONFIG_MEMBER not in current_members:
        raise ValueError(
            "current controlled source lacks the fixed derivation member"
        )

    expected_controlled = {
        "identity_sha256": current_identity,
        "snapshot_sha256": snapshot_record["snapshot_sha256"],
        "member_count": len(current_members),
        "derivation_config_member": FIXED_DERIVATION_CONFIG_MEMBER,
    }
    _require_contract_match(
        run_controlled, expected_controlled, label="run manifest"
    )
    _require_contract_match(
        public_controlled, expected_controlled, label="public manifest"
    )

    design = run_manifest.get("design")
    if isinstance(design, Mapping) and "code_archive_sha256" in design:
        if design["code_archive_sha256"] != current_identity:
            raise ValueError(
                "run manifest code archive identity does not match "
                "controlled source"
            )

    return {
        "artifacts_verified": len(checked),
        "controlled_source_identity_sha256": current_identity,
        "controlled_source_member_count": len(current_members),
        "controlled_source_snapshot_sha256": snapshot_record[
            "snapshot_sha256"
        ],
        "regenerated_artifacts_declared": len(omissions),
        "run_id": formal_run_id,
        "status": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a TARMS public release without modifying it."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="public release manifest to verify",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="root containing the declared public artifacts",
    )
    arguments = parser.parse_args(argv)
    report = verify_public_release(
        arguments.manifest,
        arguments.project_root,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
