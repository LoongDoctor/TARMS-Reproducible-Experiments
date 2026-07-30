"""Tests for the self-verifying public release manifest gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tarms_experiments import release_identity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXED_DERIVATION_MEMBER = "config/aamos00_derivation.yaml"
SPEC = importlib.util.spec_from_file_location(
    "verify_public_release",
    PROJECT_ROOT / "scripts" / "verify_public_release.py",
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_controlled_project(destination: Path) -> None:
    for member in release_identity.controlled_source_members(PROJECT_ROOT):
        source = PROJECT_ROOT / member
        target = destination / member
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _controlled_contract(record: dict[str, object]) -> dict[str, object]:
    return {
        "identity_sha256": record["identity_sha256"],
        "snapshot_sha256": record["snapshot_sha256"],
        "member_count": record["member_count"],
        "derivation_config_member": FIXED_DERIVATION_MEMBER,
    }


def _valid_release(root: Path) -> dict[str, object]:
    _copy_controlled_project(root)
    snapshot_path = root / "controlled-source.zip"
    snapshot_record = release_identity.write_controlled_snapshot(
        root, snapshot_path
    )
    controlled = _controlled_contract(snapshot_record)
    controlled_artifacts = {
        member: VERIFY._sha256(root / member)
        for member in snapshot_record["members"]
    }
    metric_path = root / "formal" / "metric_summary.csv"
    metric_path.parent.mkdir(parents=True, exist_ok=True)
    metric_path.write_bytes(b"metric,value\ncoverage,1.0\n")
    metric_hash = VERIFY._sha256(metric_path)
    omitted_hash = hashlib.sha256(
        b"participant-linked private rows\n"
    ).hexdigest()
    run_path = root / "formal" / "run_manifest.json"
    run_manifest = {
        "schema_version": "1.0",
        "run_id": "formal-v6",
        "controlled_source": dict(controlled),
        "artifacts": {
            "metric_summary.csv": metric_hash,
            "patient_days.csv": omitted_hash,
        },
        "design": {
            "code_archive_sha256": controlled["identity_sha256"],
        },
    }
    _write_json(run_path, run_manifest)
    run_hash = VERIFY._sha256(run_path)
    public_manifest = {
        "schema_version": "1.0",
        "run_id": "formal-v6",
        "formal_run_manifest_sha256": run_hash,
        "controlled_source": dict(controlled),
        "public_artifacts": {
            **controlled_artifacts,
            "controlled-source.zip": snapshot_record["snapshot_sha256"],
            "formal/run_manifest.json": run_hash,
            "formal/metric_summary.csv": metric_hash,
        },
        "locally_regenerated_artifacts": {
            "patient_days.csv": {
                "published": False,
                "canonical_sha256": omitted_hash,
                "reason": "Participant-linked table is regenerated locally.",
            }
        },
    }
    manifest_path = root / "public_release_manifest.json"
    _write_json(manifest_path, public_manifest)
    return {
        "manifest_path": manifest_path,
        "public_manifest": public_manifest,
        "run_path": run_path,
        "run_manifest": run_manifest,
        "metric_path": metric_path,
        "snapshot_path": snapshot_path,
        "snapshot_record": snapshot_record,
    }


def _rewrite_public(release: dict[str, object]) -> None:
    _write_json(
        release["manifest_path"],
        release["public_manifest"],
    )


def _rewrite_run_and_rebind(release: dict[str, object]) -> None:
    _write_json(release["run_path"], release["run_manifest"])
    run_hash = VERIFY._sha256(release["run_path"])
    public = release["public_manifest"]
    public["formal_run_manifest_sha256"] = run_hash
    public["public_artifacts"]["formal/run_manifest.json"] = run_hash
    _rewrite_public(release)


def _write_run_bytes_and_rebind(
    release: dict[str, object],
    payload: bytes,
) -> None:
    release["run_path"].write_bytes(payload)
    run_hash = hashlib.sha256(payload).hexdigest()
    public = release["public_manifest"]
    public["formal_run_manifest_sha256"] = run_hash
    public["public_artifacts"]["formal/run_manifest.json"] = run_hash
    _rewrite_public(release)


class PublicReleaseManifestTests(unittest.TestCase):
    def test_valid_release_binds_current_snapshot_run_and_public_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)

            report = VERIFY.verify_public_release(
                release["manifest_path"], root
            )

            self.assertEqual("ok", report["status"])
            self.assertEqual(
                release["snapshot_record"]["member_count"] + 3,
                report["artifacts_verified"],
            )
            self.assertEqual(1, report["regenerated_artifacts_declared"])
            self.assertEqual("formal-v6", report["run_id"])
            self.assertEqual(
                release["snapshot_record"]["identity_sha256"],
                report["controlled_source_identity_sha256"],
            )
            self.assertEqual(
                release["snapshot_record"]["member_count"],
                report["controlled_source_member_count"],
            )

    def test_formal_artifact_hash_must_match_public_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            release["run_manifest"]["artifacts"][
                "metric_summary.csv"
            ] = "0" * 64
            _rewrite_run_and_rebind(release)

            with self.assertRaisesRegex(
                ValueError, "formal artifact|metric_summary"
            ):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_each_formal_artifact_has_exactly_one_public_or_omitted_form(self):
        for case in (
            "published-gap",
            "omitted-gap",
            "overlap",
            "ambiguous-published-basename",
        ):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    public = release["public_manifest"]
                    if case == "published-gap":
                        public["public_artifacts"].pop(
                            "formal/metric_summary.csv"
                        )
                    elif case == "omitted-gap":
                        public["locally_regenerated_artifacts"].pop(
                            "patient_days.csv"
                        )
                    elif case == "overlap":
                        public["locally_regenerated_artifacts"][
                            "metric_summary.csv"
                        ] = {
                            "published": False,
                            "canonical_sha256": (
                                release["run_manifest"]["artifacts"][
                                    "metric_summary.csv"
                                ]
                            ),
                            "reason": "Invalid overlapping representation.",
                        }
                    else:
                        duplicate = root / "extra" / "metric_summary.csv"
                        duplicate.parent.mkdir(parents=True)
                        shutil.copy2(release["metric_path"], duplicate)
                        public["public_artifacts"][
                            "extra/metric_summary.csv"
                        ] = VERIFY._sha256(duplicate)
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError,
                        "formal artifact|representation|ambiguous|overlap",
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_formal_artifact_contract_rejects_empty_unsafe_or_invalid_entries(self):
        for case in ("empty", "not-object", "unsafe", "nested", "hash"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    artifacts = release["run_manifest"]["artifacts"]
                    if case == "empty":
                        release["run_manifest"]["artifacts"] = {}
                    elif case == "not-object":
                        release["run_manifest"]["artifacts"] = []
                    elif case == "unsafe":
                        digest = artifacts.pop("metric_summary.csv")
                        artifacts["../metric_summary.csv"] = digest
                    elif case == "nested":
                        digest = artifacts.pop("metric_summary.csv")
                        artifacts["formal/metric_summary.csv"] = digest
                    else:
                        artifacts["metric_summary.csv"] = "A" * 64
                    _rewrite_run_and_rebind(release)

                    with self.assertRaisesRegex(
                        ValueError, "formal|artifact|relative|SHA-256"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_omission_contract_rejects_invalid_or_extra_declarations(self):
        for case in (
            "unsafe-name",
            "not-object",
            "published",
            "hash-format",
            "hash-mismatch",
            "empty-reason",
            "nontext-reason",
            "extra",
        ):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    omissions = release["public_manifest"][
                        "locally_regenerated_artifacts"
                    ]
                    if case == "unsafe-name":
                        details = omissions.pop("patient_days.csv")
                        omissions["../patient_days.csv"] = details
                    elif case == "not-object":
                        omissions["patient_days.csv"] = []
                    elif case == "published":
                        omissions["patient_days.csv"]["published"] = True
                    elif case == "hash-format":
                        omissions["patient_days.csv"][
                            "canonical_sha256"
                        ] = "A" * 64
                    elif case == "hash-mismatch":
                        omissions["patient_days.csv"][
                            "canonical_sha256"
                        ] = "0" * 64
                    elif case == "empty-reason":
                        omissions["patient_days.csv"]["reason"] = ""
                    elif case == "nontext-reason":
                        omissions["patient_days.csv"]["reason"] = 7
                    else:
                        omissions["extra.csv"] = {
                            "published": False,
                            "canonical_sha256": "0" * 64,
                            "reason": "Not a formal artifact.",
                        }
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "omission|regenerated|formal|relative"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_public_artifact_paths_must_be_canonical_and_unique(self):
        for case in ("repeated-separator", "dot-segment", "alias"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    artifacts = release["public_manifest"][
                        "public_artifacts"
                    ]
                    digest = artifacts["formal/metric_summary.csv"]
                    if case == "repeated-separator":
                        artifacts.pop("formal/metric_summary.csv")
                        artifacts["formal//metric_summary.csv"] = digest
                    elif case == "dot-segment":
                        artifacts.pop("formal/metric_summary.csv")
                        artifacts["formal/./metric_summary.csv"] = digest
                    else:
                        artifacts["formal//metric_summary.csv"] = digest
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "canonical|relative|alias|duplicate"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_external_symlink_artifacts_and_symlink_parents_are_rejected(self):
        for case in ("run-file", "snapshot-file", "run-parent"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    root = base / "project"
                    outside = base / "outside"
                    outside.mkdir()
                    release = _valid_release(root)
                    if case == "run-file":
                        target = outside / "run_manifest.json"
                        shutil.move(release["run_path"], target)
                        release["run_path"].symlink_to(target)
                    elif case == "snapshot-file":
                        target = outside / "controlled-source.zip"
                        shutil.move(release["snapshot_path"], target)
                        release["snapshot_path"].symlink_to(target)
                    else:
                        target = outside / "formal"
                        shutil.move(root / "formal", target)
                        (root / "formal").symlink_to(
                            target, target_is_directory=True
                        )

                    with self.assertRaisesRegex(
                        ValueError, "symlink|contain"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_public_manifest_and_declared_artifacts_are_each_read_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            targets = {
                path.resolve(): 0
                for path in (
                    release["manifest_path"],
                    release["run_path"],
                    release["snapshot_path"],
                    release["metric_path"],
                )
            }
            original_open = Path.open

            def counted_open(path, *args, **kwargs):
                resolved = path.resolve(strict=False)
                if resolved in targets:
                    targets[resolved] += 1
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", new=counted_open):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

            self.assertEqual(
                {path: 1 for path in targets},
                targets,
            )

    def test_controlled_source_identity_uses_each_verified_file_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            controlled_paths = {
                (root / member).resolve(): 0
                for member in release["snapshot_record"]["members"]
            }
            swapped = {
                (root / "README.md").resolve(),
                (root / "pyproject.toml").resolve(),
            }
            original_open = Path.open

            def counted_open(path, *args, **kwargs):
                resolved = path.resolve(strict=False)
                handle = original_open(path, *args, **kwargs)
                if resolved in controlled_paths:
                    controlled_paths[resolved] += 1
                    if resolved in swapped and controlled_paths[resolved] == 1:
                        staged = path.with_name(f".{path.name}.swap")
                        with original_open(staged, "wb") as replacement:
                            replacement.write(b"swapped after verified read\n")
                        staged.replace(path)
                return handle

            with mock.patch.object(Path, "open", new=counted_open):
                report = VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

            self.assertEqual("ok", report["status"])
            self.assertEqual(
                {path: 1 for path in controlled_paths},
                controlled_paths,
            )

    def test_every_controlled_source_member_must_be_publicly_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            release["public_manifest"]["public_artifacts"].pop("README.md")
            _rewrite_public(release)

            with self.assertRaisesRegex(
                ValueError, "controlled-source|README|declared"
            ):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_duplicate_keys_are_rejected_in_public_and_run_manifests(self):
        for case in ("public", "run"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    if case == "public":
                        path = release["manifest_path"]
                        raw = path.read_text(encoding="utf-8")
                        raw = raw.replace(
                            '  "run_id": "formal-v6",',
                            '  "note": "/home/alice/private",\n'
                            '  "note": "safe",\n'
                            '  "run_id": "formal-v6",',
                            1,
                        )
                        path.write_text(raw, encoding="utf-8")
                    else:
                        raw = release["run_path"].read_text(
                            encoding="utf-8"
                        )
                        raw = raw.replace(
                            '  "run_id": "formal-v6",',
                            '  "note": "/home/alice/private",\n'
                            '  "note": "safe",\n'
                            '  "run_id": "formal-v6",',
                            1,
                        )
                        _write_run_bytes_and_rebind(
                            release, raw.encode("utf-8")
                        )

                    with self.assertRaisesRegex(
                        ValueError, "duplicate|JSON"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_recursive_scan_rejects_windows_unc_device_and_rooted_paths(self):
        leaked_values = (
            r"\\server\share\secret.txt",
            r"\\?\C:\secret.txt",
            r"\\.\C:\secret.txt",
            r"\rooted\secret.txt",
            r"folder\uSeRs\alice\secret.txt",
        )
        for leaked in leaked_values:
            with self.subTest(leaked=leaked):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    release["public_manifest"]["unrelated"] = {
                        "value": leaked
                    }
                    _rewrite_public(release)

                    with self.assertRaisesRegex(ValueError, "path"):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_public_and_formal_run_ids_must_be_nonempty_and_equal(self):
        for case in (
            "mismatch",
            "empty-public",
            "nontext-public",
            "empty-run",
            "nontext-run",
        ):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    if case == "mismatch":
                        release["run_manifest"]["run_id"] = "other-run"
                    elif case == "empty-public":
                        release["public_manifest"]["run_id"] = ""
                    elif case == "nontext-public":
                        release["public_manifest"]["run_id"] = 7
                    elif case == "empty-run":
                        release["run_manifest"]["run_id"] = ""
                    else:
                        release["run_manifest"]["run_id"] = 7
                    if case.endswith("run") or case == "mismatch":
                        _rewrite_run_and_rebind(release)
                    else:
                        _rewrite_public(release)

                    with self.assertRaisesRegex(ValueError, "run.?id|run ID"):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_controlled_member_rejects_posix_macos_and_windows_leaks(self):
        leaked_values = (
            "/workspace/project/config.yaml",
            "/Users/alice/project/config.yaml",
            r"C:\Users\alice\project\config.yaml",
        )
        for leaked in leaked_values:
            with self.subTest(leaked=leaked):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    release["public_manifest"]["controlled_source"][
                        "derivation_config_member"
                    ] = leaked
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "relative|path"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_recursive_public_manifest_scan_rejects_nested_values_and_keys(self):
        mutations = (
            lambda manifest: manifest.update(
                {"unrelated": {"note": "/home/alice/private.txt"}}
            ),
            lambda manifest: manifest.update(
                {"unrelated": {"/workspace/private-key": "value"}}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    mutate(release["public_manifest"])
                    _rewrite_public(release)

                    with self.assertRaisesRegex(ValueError, "path"):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_recursive_run_manifest_scan_rejects_nested_values_and_keys(self):
        mutations = (
            lambda manifest: manifest.update(
                {"unrelated": [{"note": "/Users/alice/private.txt"}]}
            ),
            lambda manifest: manifest.update(
                {"unrelated": {r"C:\Users\alice\private": "value"}}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    mutate(release["run_manifest"])
                    _rewrite_run_and_rebind(release)

                    with self.assertRaisesRegex(ValueError, "path"):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_current_source_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            readme = root / "README.md"
            readme.write_bytes(readme.read_bytes() + b"\n")

            with self.assertRaisesRegex(
                ValueError, "README|SHA-256|current|identity"
            ):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_snapshot_content_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            readme = root / "README.md"
            original = readme.read_bytes()
            readme.write_bytes(original + b"\n")
            changed_snapshot = root / "changed-source.zip"
            changed_record = release_identity.write_controlled_snapshot(
                root, changed_snapshot
            )
            readme.write_bytes(original)
            shutil.copyfile(changed_snapshot, release["snapshot_path"])
            public = release["public_manifest"]
            public["public_artifacts"][
                "controlled-source.zip"
            ] = changed_record["snapshot_sha256"]
            public["controlled_source"][
                "snapshot_sha256"
            ] = changed_record["snapshot_sha256"]
            _rewrite_public(release)

            with self.assertRaisesRegex(ValueError, "snapshot|identity"):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_run_manifest_controlled_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            release["run_manifest"]["controlled_source"][
                "identity_sha256"
            ] = "0" * 64
            _rewrite_run_and_rebind(release)

            with self.assertRaisesRegex(ValueError, "run manifest|identity"):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_public_manifest_controlled_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            release["public_manifest"]["controlled_source"][
                "identity_sha256"
            ] = "0" * 64
            _rewrite_public(release)

            with self.assertRaisesRegex(
                ValueError, "public manifest|identity"
            ):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_missing_or_ambiguous_run_manifest_is_rejected(self):
        for case in ("missing", "ambiguous"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    public = release["public_manifest"]
                    if case == "missing":
                        public["public_artifacts"].pop(
                            "formal/run_manifest.json"
                        )
                    else:
                        second = root / "second" / "run_manifest.json"
                        second.parent.mkdir(parents=True)
                        second.write_text(
                            '{"run_id":"stale-run"}\n',
                            encoding="utf-8",
                        )
                        public["public_artifacts"][
                            "second/run_manifest.json"
                        ] = VERIFY._sha256(second)
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "exactly one|run manifest"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_controlled_snapshot_must_be_declared_with_matching_hash(self):
        for case in ("undeclared", "artifact-hash", "contract-hash"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    public = release["public_manifest"]
                    if case == "undeclared":
                        public["public_artifacts"].pop(
                            "controlled-source.zip"
                        )
                    elif case == "artifact-hash":
                        public["public_artifacts"][
                            "controlled-source.zip"
                        ] = "0" * 64
                    else:
                        public["controlled_source"][
                            "snapshot_sha256"
                        ] = "0" * 64
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "controlled-source|snapshot|SHA-256"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_invalid_controlled_hash_count_and_member_are_rejected(self):
        mutations = (
            ("identity_sha256", "A" * 64),
            ("snapshot_sha256", "short"),
            ("member_count", 0),
            ("derivation_config_member", "../config.yaml"),
            ("derivation_config_member", "config/other.yaml"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    release = _valid_release(root)
                    release["public_manifest"]["controlled_source"][
                        field
                    ] = value
                    _rewrite_public(release)

                    with self.assertRaisesRegex(
                        ValueError, "controlled|relative|SHA-256|count"
                    ):
                        VERIFY.verify_public_release(
                            release["manifest_path"], root
                        )

    def test_manifest_requires_declared_omission_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = _valid_release(root)
            release["public_manifest"][
                "locally_regenerated_artifacts"
            ] = {
                "omitted.csv": {
                    "published": False,
                    "canonical_sha256": "0" * 64,
                }
            }
            _rewrite_public(release)

            with self.assertRaisesRegex(ValueError, "incomplete"):
                VERIFY.verify_public_release(
                    release["manifest_path"], root
                )

    def test_legacy_manifest_without_controlled_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_text("ok\n", encoding="utf-8")
            manifest = {
                "public_artifacts": {
                    "artifact.txt": VERIFY._sha256(artifact),
                },
                "locally_regenerated_artifacts": {},
            }
            path = root / "manifest.json"
            _write_json(path, manifest)

            with self.assertRaisesRegex(ValueError, "controlled"):
                VERIFY.verify_public_release(path, root)


if __name__ == "__main__":
    unittest.main()
