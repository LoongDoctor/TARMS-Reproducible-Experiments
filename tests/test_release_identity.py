from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

import yaml

from tarms_experiments import release_identity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)
FORBIDDEN_PARTS = {
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "results",
    ".git",
}


def _copy_project(destination: Path) -> None:
    shutil.copytree(
        PROJECT_ROOT,
        destination,
        ignore=shutil.ignore_patterns(*FORBIDDEN_PARTS),
    )


def _load_runner():
    path = PROJECT_ROOT / "scripts" / "run_aamos_standard_enhanced.py"
    spec = importlib.util.spec_from_file_location("aamos_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_builder(
    project_root: Path,
    output: Path,
    json_output: Path,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root / "src")
    return subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts/build_controlled_snapshot.py"),
            "--output",
            str(output),
            "--json-output",
            str(json_output),
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _zip_info(
    member: str,
    *,
    date_time: tuple[int, int, int, int, int, int] = FIXED_ZIP_DATETIME,
    create_system: int = 3,
    mode: int | None = None,
    compress_type: int = zipfile.ZIP_DEFLATED,
    extra: bytes = b"",
    comment: bytes = b"",
) -> zipfile.ZipInfo:
    if mode is None:
        mode = 0o100755 if member.endswith(".sh") else 0o100644
    info = zipfile.ZipInfo(member, date_time=date_time)
    info.create_system = create_system
    info.external_attr = mode << 16
    info.compress_type = compress_type
    info.extra = extra
    info.comment = comment
    return info


def _write_raw_archive(
    path: Path,
    entries: list[tuple[zipfile.ZipInfo, bytes]],
    *,
    archive_comment: bytes = b"",
    compresslevel: int = 9,
) -> None:
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=compresslevel,
    ) as archive:
        archive.comment = archive_comment
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for info, data in entries:
                archive.writestr(
                    info,
                    data,
                    compresslevel=compresslevel,
                )


class ControlledSourceIdentityTests(unittest.TestCase):
    def test_public_release_builder_gate_is_controlled(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)

        self.assertIn("tests/test_public_release_builder.py", members)

    def test_makefile_release_boundary_gate_is_controlled(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)

        self.assertIn("tests/test_makefile_release_boundaries.py", members)

    def test_deterministic_figure_gate_is_controlled(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)

        self.assertIn("tests/test_deterministic_figures.py", members)

    def test_members_are_sorted_unique_relative_and_environment_free(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)

        self.assertEqual(tuple(sorted(members)), members)
        self.assertEqual(len(members), len(set(members)))
        self.assertIn("config/aamos00_derivation.yaml", members)
        self.assertIn("fabric/chaincode/index.js", members)
        self.assertIn("scripts/run_aamos_standard_enhanced.py", members)
        self.assertIn("src/tarms_experiments/encoding.py", members)
        self.assertIn("tests/test_release_identity.py", members)
        for member in members:
            path = PurePosixPath(member)
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            self.assertTrue(FORBIDDEN_PARTS.isdisjoint(path.parts))
            self.assertFalse(path.name.startswith("."))

    def test_missing_literal_or_wildcard_contract_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            _copy_project(project)
            (project / "CITATION.cff").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "CITATION.cff"):
                release_identity.controlled_source_members(project)

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            _copy_project(project)
            for path in (project / "fabric/client/test").glob("*.js"):
                path.unlink()
            with self.assertRaisesRegex(
                FileNotFoundError, r"fabric/client/test/\*\.js"
            ):
                release_identity.controlled_source_members(project)

    def test_two_fresh_snapshots_are_byte_identical_and_records_equal(self):
        with (
            tempfile.TemporaryDirectory() as left,
            tempfile.TemporaryDirectory() as right,
        ):
            left_zip = Path(left) / "source.zip"
            right_zip = Path(right) / "source.zip"

            first = release_identity.write_controlled_snapshot(
                PROJECT_ROOT, left_zip
            )
            second = release_identity.write_controlled_snapshot(
                PROJECT_ROOT, right_zip
            )

            self.assertEqual(left_zip.read_bytes(), right_zip.read_bytes())
            self.assertEqual(first, second)
            self.assertEqual(
                first,
                release_identity.inspect_controlled_snapshot(left_zip),
            )
            self.assertEqual(
                {
                    "identity_sha256",
                    "snapshot_sha256",
                    "member_count",
                    "members",
                },
                set(first),
            )
            self.assertEqual(
                tuple(sorted(first["members"])),
                tuple(first["members"]),
            )
            self.assertEqual(64, len(first["identity_sha256"]))
            self.assertEqual(64, len(first["snapshot_sha256"]))

    def test_one_byte_controlled_change_changes_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            _copy_project(project)
            before = release_identity.controlled_source_identity(project)
            target = project / "src/tarms_experiments/encoding.py"
            original = target.read_bytes()
            target.write_bytes(original + b"\n")

            after = release_identity.controlled_source_identity(project)

            self.assertNotEqual(before, after)

    def test_nested_node_modules_changes_neither_members_nor_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            _copy_project(project)
            before_members = release_identity.controlled_source_members(project)
            before_identity = release_identity.controlled_source_identity(
                project, before_members
            )
            ignored = (
                project
                / "fabric/chaincode/node_modules/ignored/index.js"
            )
            ignored.parent.mkdir(parents=True)
            ignored.write_text("ignored\n", encoding="utf-8")

            after_members = release_identity.controlled_source_members(project)
            after_identity = release_identity.controlled_source_identity(
                project, after_members
            )

            self.assertEqual(before_members, after_members)
            self.assertEqual(before_identity, after_identity)

    def test_supplied_members_must_be_complete_safe_and_allowlisted(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)
        cases = (
            members[:-1],
            (*members, members[0]),
            (*members, "../outside.py"),
            (*members, "/absolute.py"),
            (*members, "scripts/unlisted.txt"),
        )
        for case in cases:
            with self.subTest(member=case[-1]):
                with self.assertRaises(ValueError):
                    release_identity.controlled_source_identity(
                        PROJECT_ROOT, case
                    )

    def test_snapshot_inspection_rejects_unsafe_order_and_duplicates(self):
        members = list(
            release_identity.controlled_source_members(PROJECT_ROOT)
        )
        valid_entries = [
            (
                _zip_info(member),
                (PROJECT_ROOT / member).read_bytes(),
            )
            for member in members
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = {
                "unsorted": list(reversed(valid_entries)),
                "duplicate": [valid_entries[0], *valid_entries],
                "absolute": [
                    (_zip_info("/absolute.py"), b"bad"),
                    *valid_entries,
                ],
                "parent": [
                    (_zip_info("../outside.py"), b"bad"),
                    *valid_entries,
                ],
                "unexpected": sorted(
                    [
                        *valid_entries,
                        (_zip_info("scripts/unlisted.txt"), b"bad"),
                    ],
                    key=lambda entry: entry[0].filename,
                ),
                "missing": valid_entries[:-1],
            }
            for name, entries in cases.items():
                with self.subTest(name=name):
                    archive_path = root / f"{name}.zip"
                    _write_raw_archive(archive_path, entries)
                    with self.assertRaises(ValueError):
                        release_identity.inspect_controlled_snapshot(
                            archive_path
                        )

    def test_snapshot_inspection_rejects_noncanonical_metadata(self):
        members = list(
            release_identity.controlled_source_members(PROJECT_ROOT)
        )
        data = {
            member: (PROJECT_ROOT / member).read_bytes()
            for member in members
        }
        first = members[0]
        metadata_cases = {
            "timestamp": _zip_info(
                first, date_time=(2000, 1, 1, 0, 0, 0)
            ),
            "creator": _zip_info(first, create_system=0),
            "mode": _zip_info(first, mode=0o100600),
            "compression": _zip_info(
                first, compress_type=zipfile.ZIP_STORED
            ),
            "extra": _zip_info(first, extra=b"\xfe\xca\x00\x00"),
            "member-comment": _zip_info(first, comment=b"comment"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, changed in metadata_cases.items():
                with self.subTest(name=name):
                    entries = [
                        (
                            changed if member == first else _zip_info(member),
                            data[member],
                        )
                        for member in members
                    ]
                    archive_path = root / f"{name}.zip"
                    _write_raw_archive(archive_path, entries)
                    with self.assertRaises(ValueError):
                        release_identity.inspect_controlled_snapshot(
                            archive_path
                        )

            archive_path = root / "archive-comment.zip"
            _write_raw_archive(
                archive_path,
                [
                    (_zip_info(member), data[member])
                    for member in members
                ],
                archive_comment=b"comment",
            )
            with self.assertRaises(ValueError):
                release_identity.inspect_controlled_snapshot(archive_path)

    def test_snapshot_inspection_rejects_level_one_deflate(self):
        members = release_identity.controlled_source_members(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "level-one.zip"
            _write_raw_archive(
                archive_path,
                [
                    (
                        _zip_info(member),
                        (PROJECT_ROOT / member).read_bytes(),
                    )
                    for member in members
                ],
                compresslevel=1,
            )

            with self.assertRaisesRegex(ValueError, "canonical"):
                release_identity.inspect_controlled_snapshot(archive_path)

    def test_snapshot_inspection_rejects_local_header_timestamp_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "local-time.zip"
            release_identity.write_controlled_snapshot(
                PROJECT_ROOT,
                archive_path,
            )
            with zipfile.ZipFile(archive_path, "r") as archive:
                header_offset = archive.infolist()[0].header_offset
            mutated = bytearray(archive_path.read_bytes())
            mutated[header_offset + 10 : header_offset + 14] = struct.pack(
                "<HH",
                1,
                33,
            )
            archive_path.write_bytes(mutated)

            with self.assertRaisesRegex(ValueError, "canonical"):
                release_identity.inspect_controlled_snapshot(archive_path)

    def test_builder_cli_writes_sorted_lf_terminated_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "controlled-source.zip"
            json_output = root / "controlled-source.json"

            completed = _run_builder(
                PROJECT_ROOT,
                snapshot,
                json_output,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            record = release_identity.inspect_controlled_snapshot(snapshot)
            expected = json.dumps(
                record,
                indent=2,
                sort_keys=True,
            ) + "\n"
            self.assertEqual(expected, json_output.read_text(encoding="utf-8"))
            self.assertNotIn("\r", json_output.read_text(encoding="utf-8"))

    def test_builder_cli_rejects_identical_destinations_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "snapshot"

            completed = _run_builder(
                PROJECT_ROOT,
                destination,
                destination,
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("distinct", completed.stderr)
            self.assertFalse(destination.exists())

    def test_builder_cli_rejects_symlink_and_hardlink_destination_aliases(self):
        for alias_kind in ("symlink", "hardlink"):
            with self.subTest(alias_kind=alias_kind):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    output = root / "controlled-source.zip"
                    json_output = root / "controlled-source.json"
                    original = b"original destination bytes\n"
                    output.write_bytes(original)
                    if alias_kind == "symlink":
                        json_output.symlink_to(output)
                    else:
                        os.link(output, json_output)

                    completed = _run_builder(
                        PROJECT_ROOT,
                        output,
                        json_output,
                    )

                    self.assertNotEqual(0, completed.returncode)
                    self.assertIn("distinct", completed.stderr)
                    self.assertEqual(original, output.read_bytes())
                    self.assertEqual(original, json_output.read_bytes())

    def test_snapshot_writer_rejects_controlled_destination_aliases(self):
        for alias_kind in ("exact", "symlink", "hardlink"):
            with self.subTest(alias_kind=alias_kind):
                with tempfile.TemporaryDirectory() as directory:
                    project = Path(directory) / "project"
                    _copy_project(project)
                    controlled = project / "README.md"
                    original = controlled.read_bytes()
                    if alias_kind == "exact":
                        destination = controlled
                    else:
                        destination = Path(directory) / "snapshot.zip"
                        if alias_kind == "symlink":
                            destination.symlink_to(controlled)
                        else:
                            os.link(controlled, destination)

                    with self.assertRaisesRegex(ValueError, "controlled"):
                        release_identity.write_controlled_snapshot(
                            project,
                            destination,
                        )

                    self.assertEqual(original, controlled.read_bytes())

    def test_builder_cli_rejects_json_controlled_destination_aliases(self):
        for alias_kind in ("exact", "symlink", "hardlink"):
            with self.subTest(alias_kind=alias_kind):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    project = root / "project"
                    _copy_project(project)
                    controlled = project / "README.md"
                    original = controlled.read_bytes()
                    output = root / "controlled-source.zip"
                    if alias_kind == "exact":
                        json_output = controlled
                    else:
                        json_output = root / "controlled-source.json"
                        if alias_kind == "symlink":
                            json_output.symlink_to(controlled)
                        else:
                            os.link(controlled, json_output)

                    completed = _run_builder(
                        project,
                        output,
                        json_output,
                    )

                    self.assertNotEqual(0, completed.returncode)
                    self.assertIn("controlled", completed.stderr)
                    self.assertEqual(original, controlled.read_bytes())
                    self.assertFalse(output.exists())

    def test_fixed_runner_identity_delegates_to_controlled_source(self):
        runner = _load_runner()
        members = release_identity.controlled_source_members(PROJECT_ROOT)

        self.assertEqual(
            members,
            tuple(
                runner._archive_member_name(path)
                for path in runner._code_archive_paths()
            ),
        )
        self.assertEqual(
            release_identity.controlled_source_identity(
                PROJECT_ROOT, members
            ),
            runner._code_archive_hash(),
        )

    def test_runner_derivation_identity_uses_only_relative_member_names(self):
        runner = _load_runner()
        config_path = PROJECT_ROOT / "config/aamos00_derivation.yaml"
        config = yaml.safe_load(
            config_path.read_text(encoding="utf-8")
        )

        identity = runner._derivation_config_identity(config_path, config)

        self.assertNotIn("derivation_config_path", identity)
        self.assertEqual(
            "config/aamos00_derivation.yaml",
            identity["derivation_config_member"],
        )
        self.assertTrue(identity["fixed_submission_config"])
        self.assertEqual(64, len(identity["derivation_config_file_sha256"]))
        self.assertEqual(
            64, len(identity["derivation_config_canonical_sha256"])
        )

    def test_submission_snapshot_mismatch_precedes_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed_project = root / "changed-project"
            _copy_project(changed_project)
            readme = changed_project / "README.md"
            readme.write_bytes(readme.read_bytes() + b"\n")
            explicit_snapshot = root / "explicit-source.zip"
            release_identity.write_controlled_snapshot(
                changed_project, explicit_snapshot
            )

            for case in ("explicit", "default"):
                with self.subTest(case=case):
                    output_root = root / case / "reproduced_results"
                    if case == "default":
                        snapshot = output_root.parent / "controlled-source.zip"
                        snapshot.parent.mkdir(parents=True)
                        shutil.copy2(explicit_snapshot, snapshot)
                    else:
                        snapshot = explicit_snapshot
                    command = [
                        sys.executable,
                        str(
                            PROJECT_ROOT
                            / "scripts/run_aamos_standard_enhanced.py"
                        ),
                        "--source-dir",
                        str(root / "missing-source"),
                        "--output-root",
                        str(output_root),
                        "--profile",
                        "submission",
                        "--bootstrap-reps",
                        "2000",
                        "--run-id",
                        f"{case}-snapshot-mismatch",
                    ]
                    if case == "explicit":
                        command.extend(
                            ["--controlled-snapshot", str(snapshot)]
                        )

                    completed = subprocess.run(
                        command,
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertNotEqual(0, completed.returncode)
                    self.assertIn("controlled-source", completed.stderr)
                    self.assertIn("identity", completed.stderr)
                    self.assertNotIn(
                        "required AAMOS source is missing",
                        completed.stderr,
                    )

    def test_preview_custom_config_uses_environment_free_member_name(self):
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as directory:
            custom = Path(directory) / "custom.yaml"
            custom.write_bytes(
                (
                    PROJECT_ROOT / "config/aamos00_derivation.yaml"
                ).read_bytes()
                + b"\n"
            )

            names = tuple(
                runner._archive_member_name(path)
                for path in runner._code_archive_paths(custom)
            )
            custom_hash = runner._code_archive_hash(
                derivation_config_path=custom
            )

            self.assertIn("runtime-config/custom.yaml", names)
            self.assertNotIn(str(custom), names)
            self.assertNotEqual(custom_hash, runner._code_archive_hash())
            self.assertEqual(64, len(custom_hash))

    def test_preview_in_tree_custom_config_uses_unique_runtime_alias(self):
        runner = _load_runner()
        custom = PROJECT_ROOT / "config/aamos_columns.fixture.yaml"

        paths = runner._code_archive_paths(custom)
        names = tuple(
            runner._archive_member_name(path)
            for path in paths
        )
        first = runner._code_archive_hash(
            derivation_config_path=custom
        )
        second = runner._code_archive_hash(
            derivation_config_path=custom
        )

        self.assertIn(
            "runtime-config/aamos_columns.fixture.yaml",
            names,
        )
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn(str(PROJECT_ROOT), "\n".join(names))
        self.assertEqual(first, second)
        self.assertEqual(64, len(first))


if __name__ == "__main__":
    unittest.main()
