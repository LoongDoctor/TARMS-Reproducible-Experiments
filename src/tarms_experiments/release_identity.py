"""Deterministic identity and snapshot support for controlled release source."""

from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import struct
import tempfile
from typing import Iterable
import zipfile


FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)
_REGULAR_MODE = 0o100644
_EXECUTABLE_MODE = 0o100755
_FORBIDDEN_PARTS = frozenset(
    {
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "results",
        ".git",
    }
)
_REQUIRED_FILES = (
    "CITATION.cff",
    "Makefile",
    "README.md",
    "README_zh.md",
    "config/aamos00_derivation.yaml",
    "config/aamos_columns.example.yaml",
    "config/aamos_columns.fixture.yaml",
    "fabric/chaincode/index.js",
    "fabric/chaincode/package-lock.json",
    "fabric/chaincode/package.json",
    "fabric/client/package-lock.json",
    "fabric/client/package.json",
    "pyproject.toml",
    "requirements-lock.txt",
    "tests/aamos_submission_fixture.py",
    "tests/test_deterministic_figures.py",
    "tests/test_makefile_release_boundaries.py",
    "tests/test_plotting_gate.py",
    "tests/test_public_release_builder.py",
    "tests/test_public_release_manifest.py",
    "tests/test_r4_contracts.py",
)
_REQUIRED_PATTERNS = (
    "fabric/chaincode/lib/*.js",
    "fabric/chaincode/test/*.js",
    "fabric/client/src/*.js",
    "fabric/client/test/*.js",
    "fabric/network/*.sh",
    "scripts/*.py",
    "src/tarms_experiments/*.py",
    "tests/test_aamos*.py",
    "tests/test_release*.py",
)


def _validate_member_name(member: str) -> PurePosixPath:
    if not isinstance(member, str) or not member:
        raise ValueError("controlled-source member must be a nonempty string")
    path = PurePosixPath(member)
    if (
        path.is_absolute()
        or path.as_posix() != member
        or ".." in path.parts
        or "." in path.parts
        or "\\" in member
    ):
        raise ValueError(
            f"controlled-source member must be a safe relative path: {member}"
        )
    if _FORBIDDEN_PARTS.intersection(path.parts):
        raise ValueError(
            f"controlled-source member uses a forbidden path: {member}"
        )
    if path.name.startswith("."):
        raise ValueError(
            f"controlled-source member basename must not be hidden: {member}"
        )
    return path


def _matches_pattern(member: str, pattern: str) -> bool:
    member_parts = PurePosixPath(member).parts
    pattern_parts = PurePosixPath(pattern).parts
    return len(member_parts) == len(pattern_parts) and all(
        fnmatchcase(part, expected)
        for part, expected in zip(member_parts, pattern_parts, strict=True)
    )


def _is_allowlisted(member: str) -> bool:
    return member in _REQUIRED_FILES or any(
        _matches_pattern(member, pattern)
        for pattern in _REQUIRED_PATTERNS
    )


def _validate_contract_names(members: Iterable[str]) -> tuple[str, ...]:
    names = tuple(members)
    for member in names:
        _validate_member_name(member)
        if not _is_allowlisted(member):
            raise ValueError(
                f"unexpected controlled-source member: {member}"
            )
    if len(names) != len(set(names)):
        raise ValueError("controlled-source members contain duplicates")

    missing_files = [
        member for member in _REQUIRED_FILES if member not in names
    ]
    missing_patterns = [
        pattern
        for pattern in _REQUIRED_PATTERNS
        if not any(_matches_pattern(member, pattern) for member in names)
    ]
    if missing_files or missing_patterns:
        missing = sorted((*missing_files, *missing_patterns))
        raise ValueError(
            "controlled-source members do not satisfy the whitelist: "
            + ", ".join(missing)
        )
    return tuple(sorted(names))


def _controlled_path(project_root: Path, member: str) -> Path:
    root = Path(project_root).resolve()
    path = root / member
    if not path.is_file():
        raise FileNotFoundError(
            f"controlled-source member is missing: {member}"
        )
    try:
        path.resolve().relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"controlled-source member escapes the project root: {member}"
        ) from error
    return path


def controlled_source_members(project_root: Path) -> tuple[str, ...]:
    """Return the complete sorted controlled-source whitelist for a project."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project root is not a directory: {root}")

    members: list[str] = []
    for member in _REQUIRED_FILES:
        _controlled_path(root, member)
        members.append(member)
    for pattern in _REQUIRED_PATTERNS:
        matches = sorted(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file()
        )
        if not matches:
            raise FileNotFoundError(
                f"controlled-source pattern has no matches: {pattern}"
            )
        for member in matches:
            _controlled_path(root, member)
        members.extend(matches)
    return _validate_contract_names(members)


def _validated_project_members(
    project_root: Path,
    members: Iterable[str] | None,
) -> tuple[str, ...]:
    expected = controlled_source_members(project_root)
    if members is None:
        return expected
    observed = _validate_contract_names(tuple(members))
    if observed != expected:
        missing = sorted(set(expected).difference(observed))
        unexpected = sorted(set(observed).difference(expected))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError(
            "supplied controlled-source members differ from the project: "
            + "; ".join(details)
        )
    return observed


def _identity_from_entries(entries: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for member, data in entries:
        digest.update(member.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def controlled_source_identity(
    project_root: Path,
    members: Iterable[str] | None = None,
) -> str:
    """Return the canonical SHA-256 identity for controlled project source."""

    root = Path(project_root)
    controlled = _validated_project_members(root, members)
    return _identity_from_entries(
        (member, _controlled_path(root, member).read_bytes())
        for member in controlled
    )


def _zip_mode(member: str) -> int:
    return (
        _EXECUTABLE_MODE
        if PurePosixPath(member).suffix == ".sh"
        else _REGULAR_MODE
    )


def _snapshot_record(
    *,
    identity_sha256: str,
    snapshot_sha256: str,
    members: tuple[str, ...],
) -> dict[str, object]:
    return {
        "identity_sha256": identity_sha256,
        "snapshot_sha256": snapshot_sha256,
        "member_count": len(members),
        "members": list(members),
    }


def _canonical_snapshot_bytes(
    entries: Iterable[tuple[str, bytes]],
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.comment = b""
        for member, data in entries:
            info = zipfile.ZipInfo(
                filename=member,
                date_time=FIXED_ZIP_DATETIME,
            )
            info.create_system = 3
            info.external_attr = _zip_mode(member) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.extra = b""
            info.comment = b""
            archive.writestr(info, data, compresslevel=9)
    return output.getvalue()


def _paths_alias(left: Path, right: Path) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    if left_path.resolve(strict=False) == right_path.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left_path, right_path)
    except (FileNotFoundError, OSError):
        return False


def _reject_controlled_destination(
    project_root: Path,
    destination: Path,
    members: Iterable[str] | None = None,
) -> None:
    root = Path(project_root).resolve()
    controlled = (
        controlled_source_members(root)
        if members is None
        else tuple(members)
    )
    destination_path = Path(destination)
    candidate_paths = {
        Path(os.path.abspath(destination_path)),
        destination_path.resolve(strict=False),
    }
    for candidate in candidate_paths:
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            pass
        else:
            if _is_allowlisted(relative):
                raise ValueError(
                    "snapshot destination collides with controlled source: "
                    f"{destination_path}"
                )
    for member in controlled:
        controlled_path = _controlled_path(root, member)
        if _paths_alias(destination_path, controlled_path):
            raise ValueError(
                "snapshot destination aliases controlled source: "
                f"{destination_path}"
            )


def write_controlled_snapshot(
    project_root: Path,
    output_zip: Path,
    members: Iterable[str] | None = None,
) -> dict[str, object]:
    """Write a deterministic ZIP of controlled source and return its record."""

    root = Path(project_root)
    controlled = _validated_project_members(root, members)
    output = Path(output_zip)
    _reject_controlled_destination(root, output, controlled)
    entries = tuple(
        (member, _controlled_path(root, member).read_bytes())
        for member in controlled
    )
    identity_sha256 = _identity_from_entries(entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    _reject_controlled_destination(root, output, controlled)
    snapshot_bytes = _canonical_snapshot_bytes(entries)
    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    expected = _snapshot_record(
        identity_sha256=identity_sha256,
        snapshot_sha256=snapshot_sha256,
        members=controlled,
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    os.close(file_descriptor)
    staged = Path(temporary_name)
    try:
        staged.write_bytes(snapshot_bytes)
        if inspect_controlled_snapshot(staged) != expected:
            raise RuntimeError(
                "staged controlled-source snapshot failed inspection"
            )
        _reject_controlled_destination(root, output, controlled)
        os.replace(staged, output)
        if inspect_controlled_snapshot(output) != expected:
            raise RuntimeError(
                "published controlled-source snapshot failed inspection"
            )
    finally:
        staged.unlink(missing_ok=True)
    return expected


def _validate_local_header(
    archive_path: Path,
    info: zipfile.ZipInfo,
) -> None:
    with archive_path.open("rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30:
            raise ValueError(
                f"truncated local ZIP header for member: {info.filename}"
            )
        fields = struct.unpack("<4s5H3I2H", header)
        signature = fields[0]
        filename_length = fields[-2]
        extra_length = fields[-1]
        if signature != b"PK\x03\x04":
            raise ValueError(
                f"invalid local ZIP header for member: {info.filename}"
            )
        raw_name = stream.read(filename_length)
        if raw_name != info.filename.encode("utf-8"):
            raise ValueError(
                f"ZIP member name is not canonical UTF-8: {info.filename}"
            )
        if extra_length:
            raise ValueError(
                f"ZIP member has a local extra field: {info.filename}"
            )


def inspect_controlled_snapshot(
    snapshot_zip: Path,
) -> dict[str, object]:
    """Inspect a snapshot without extraction and recompute both SHA-256 values."""

    snapshot = Path(snapshot_zip)
    snapshot_bytes = snapshot.read_bytes()
    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    try:
        with zipfile.ZipFile(snapshot, "r") as archive:
            if archive.comment:
                raise ValueError("controlled-source ZIP has an archive comment")
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if len(names) != len(set(names)):
                raise ValueError(
                    "controlled-source ZIP contains duplicate member names"
                )
            if names != tuple(sorted(names)):
                raise ValueError(
                    "controlled-source ZIP members are not sorted"
                )
            controlled = _validate_contract_names(names)
            entries: list[tuple[str, bytes]] = []
            for info in infos:
                _validate_local_header(snapshot, info)
                expected_mode = _zip_mode(info.filename)
                observed_mode = info.external_attr >> 16
                if info.date_time != FIXED_ZIP_DATETIME:
                    raise ValueError(
                        "controlled-source ZIP member has a non-fixed "
                        f"timestamp: {info.filename}"
                    )
                if info.create_system != 3:
                    raise ValueError(
                        "controlled-source ZIP member is not Unix-authored: "
                        f"{info.filename}"
                    )
                if observed_mode != expected_mode:
                    raise ValueError(
                        "controlled-source ZIP member has a noncanonical mode: "
                        f"{info.filename}"
                    )
                if info.compress_type != zipfile.ZIP_DEFLATED:
                    raise ValueError(
                        "controlled-source ZIP member has wrong compression: "
                        f"{info.filename}"
                    )
                if info.extra:
                    raise ValueError(
                        "controlled-source ZIP member has an extra field: "
                        f"{info.filename}"
                    )
                if info.comment:
                    raise ValueError(
                        "controlled-source ZIP member has a comment: "
                        f"{info.filename}"
                    )
                entries.append((info.filename, archive.read(info)))
    except zipfile.BadZipFile as error:
        raise ValueError("invalid controlled-source ZIP") from error

    if snapshot_bytes != _canonical_snapshot_bytes(entries):
        raise ValueError(
            "controlled-source ZIP bytes are not canonical"
        )
    identity_sha256 = _identity_from_entries(entries)
    return _snapshot_record(
        identity_sha256=identity_sha256,
        snapshot_sha256=snapshot_sha256,
        members=controlled,
    )
