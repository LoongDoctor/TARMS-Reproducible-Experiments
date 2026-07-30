from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_VERIFY_COMMANDS = (
    "run_aamos",
    "run_python_benchmarks",
    "run_conformance",
    "run_component_conformance",
    "make_figures.py",
    "seal-release",
    "MANIFEST.sha256",
    "cp ",
    "mv ",
)
SHELL_MEMBERS = (
    "fabric/network/bootstrap.sh",
    "fabric/network/run_experiments.sh",
    "fabric/network/teardown.sh",
)


def _load_script(name: str):
    path = PROJECT_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        f"test_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _completed(
    command: list[str] | tuple[str, ...],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        list(command), exit_code, stdout=stdout, stderr=stderr
    )


class MakefileReleaseBoundaryTests(unittest.TestCase):
    def test_public_readme_verifies_integrity_before_installing(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.index("A clean public extraction")
        integrity = readme.index(
            "make verify-public verify-tree",
            section,
        )
        install = readme.index("make install", integrity)

        self.assertLess(integrity, install)
        self.assertNotIn(
            "`make verify` inside the sealed public package",
            readme,
        )

    def test_verify_dry_run_is_read_only_and_complete(self):
        result = _make("-n", "verify")

        self.assertEqual(0, result.returncode, result.stderr)
        output = result.stdout
        for forbidden in FORBIDDEN_VERIFY_COMMANDS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, output)
        self.assertIn("unittest discover", output)
        self.assertIn("PYTHONPATH=src", output)
        self.assertIn("npm --prefix fabric/chaincode test", output)
        self.assertIn("npm --prefix fabric/client test", output)
        for member in SHELL_MEMBERS:
            self.assertIn(f"bash -n {member}", output)
        self.assertIn("scripts/verify_public_release.py", output)
        self.assertIn("--manifest \"public_release_manifest.json\"", output)
        self.assertIn("--project-root \".\"", output)
        self.assertIn("scripts/verify_tree_manifest.py", output)

    def test_reproduce_figures_requires_and_forwards_explicit_output(self):
        missing = _make("reproduce-figures", "OUTPUT_DIR=")
        self.assertNotEqual(0, missing.returncode)

        result = _make(
            "-n", "reproduce-figures", "OUTPUT_DIR=/tmp/tarms-figures"
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(3, result.stdout.count("scripts/make_figures.py"))
        for figure in ("python", "component", "window"):
            self.assertIn(f"--figure {figure}", result.stdout)
        self.assertEqual(
            3,
            result.stdout.count('--output "/tmp/tarms-figures"'),
        )

    def test_release_report_requires_and_forwards_explicit_output(self):
        missing = _make("release-test-report", "REPORT=")
        self.assertNotEqual(0, missing.returncode)

        result = _make(
            "-n",
            "release-test-report",
            "REPORT=/tmp/tarms-test-report.json",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("scripts/run_release_tests.py", result.stdout)
        self.assertIn(
            '--output "/tmp/tarms-test-report.json"', result.stdout
        )

    def test_seal_release_is_explicit_and_reserved_for_builder(self):
        result = _make(
            "-n",
            "seal-release",
            "RELEASE_DIR=/tmp/release",
            "RUN_DIR=/tmp/run",
            "SNAPSHOT=/tmp/source.zip",
            "TEST_REPORT=/tmp/report.json",
            "FIGURE_DIR=/tmp/figures",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("scripts/build_public_release.py", result.stdout)
        self.assertIn('--release-dir "/tmp/release"', result.stdout)
        self.assertIn('--run-dir "/tmp/run"', result.stdout)
        self.assertIn('--snapshot "/tmp/source.zip"', result.stdout)
        self.assertIn('--test-report "/tmp/report.json"', result.stdout)
        self.assertIn('--figure-dir "/tmp/figures"', result.stdout)

    def test_release_python_commands_disable_bytecode_writes(self):
        invocations = (
            ("verify",),
            ("reproduce-figures", "OUTPUT_DIR=/tmp/tarms-figures"),
            (
                "release-test-report",
                "REPORT=/tmp/tarms-test-report.json",
            ),
            (
                "seal-release",
                "RELEASE_DIR=/tmp/release",
                "RUN_DIR=/tmp/run",
                "SNAPSHOT=/tmp/source.zip",
                "TEST_REPORT=/tmp/report.json",
                "FIGURE_DIR=/tmp/figures",
            ),
        )
        for invocation in invocations:
            with self.subTest(target=invocation[0]):
                result = _make("-n", *invocation, "PYTHON=python")
                self.assertEqual(0, result.returncode, result.stderr)
                python_lines = [
                    line
                    for line in result.stdout.splitlines()
                    if line.startswith("python ")
                    or " python " in line
                ]
                self.assertTrue(python_lines)
                for line in python_lines:
                    self.assertIn("PYTHONDONTWRITEBYTECODE=1", line)


class ReleaseTestReportTests(unittest.TestCase):
    @staticmethod
    def _runner(
        command: list[str] | tuple[str, ...],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        parts = tuple(command)
        if parts == ("node", "--version"):
            return _completed(parts, stdout="v20.19.4\n")
        if parts == ("npm", "--version"):
            return _completed(parts, stdout="10.8.2\n")
        if len(parts) >= 2 and parts[1] == "-c":
            return _completed(
                parts,
                stdout=json.dumps(
                    {
                        "implementation": "ProbePython",
                        "version": "9.8.7",
                        "version_exact": "9.8.7 exact probe build",
                    },
                    sort_keys=True,
                )
                + "\n",
            )
        if "-m" in parts and "unittest" in parts:
            return _completed(
                parts,
                stderr="Ran 4 tests in 0.100s\n\nOK\n",
            )
        if parts[:3] == ("npm", "--prefix", "fabric/chaincode"):
            return _completed(
                parts,
                stdout="# tests 6\n# pass 6\n# fail 0\n",
            )
        if parts[:3] == ("npm", "--prefix", "fabric/client"):
            return _completed(
                parts,
                stdout="# tests 9\n# pass 9\n# fail 0\n",
            )
        if parts[:2] == ("bash", "-n"):
            return _completed(parts)
        raise AssertionError(f"unexpected command: {parts}")

    def test_machine_report_records_portable_commands_counts_and_versions(self):
        module = _load_script("run_release_tests.py")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=self._runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(0, exit_code)
            raw = output.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            report = json.loads(raw)
            self.assertEqual("passed", report["status"])
            self.assertEqual(
                {
                    "python",
                    "chaincode",
                    "client",
                    "shell",
                },
                set(report["suites"]),
            )
            self.assertEqual(
                {
                    "observed_passed_count": 22,
                    "failed_count": 0,
                    "skipped_count": 0,
                },
                report["totals"],
            )
            self.assertEqual(
                "9.8.7", report["environment"]["python_version"]
            )
            self.assertEqual(
                "ProbePython",
                report["environment"]["python_implementation"],
            )
            self.assertEqual(
                "9.8.7 exact probe build",
                report["environment"]["python_version_exact"],
            )
            self.assertEqual(
                "v20.19.4", report["environment"]["node_version"]
            )
            self.assertEqual(
                "10.8.2", report["environment"]["npm_version"]
            )
            self.assertEqual(
                "python",
                report["suites"]["python"]["command"][0],
            )
            self.assertEqual(
                3,
                report["suites"]["shell"]["observed_passed_count"],
            )
            self.assertEqual(
                list(SHELL_MEMBERS),
                [
                    command[-1]
                    for command in report["suites"]["shell"]["commands"]
                ],
            )
            self.assertEqual(
                report["suites"]["shell"]["commands"],
                report["suites"]["shell"]["command"],
            )
            self.assertNotIn(
                str(PROJECT_ROOT), raw.decode("utf-8")
            )
            self.assertRegex(
                report["controlled_source_identity_sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_machine_report_injects_source_path_into_clean_environment(self):
        module = _load_script("run_release_tests.py")
        observed_python_paths: list[str] = []

        def inspect_environment(command, **kwargs):
            environment = kwargs.get("env")
            self.assertIsInstance(environment, dict)
            observed_python_paths.append(environment["PYTHONPATH"])
            self.assertEqual(
                "1",
                environment["PYTHONDONTWRITEBYTECODE"],
            )
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with mock.patch.dict(os.environ, {}, clear=True):
                exit_code = module.run_release_tests(
                    output,
                    project_root=PROJECT_ROOT,
                    runner=inspect_environment,
                    timestamp="2026-07-29T00:00:00Z",
                )

        self.assertEqual(0, exit_code)
        self.assertTrue(observed_python_paths)
        self.assertEqual(
            {str(PROJECT_ROOT / "src")},
            set(observed_python_paths),
        )

    def test_suite_failure_is_reported_and_returns_nonzero(self):
        module = _load_script("run_release_tests.py")

        def failing_runner(command, **kwargs):
            parts = tuple(command)
            if parts[:3] == ("npm", "--prefix", "fabric/chaincode"):
                return _completed(
                    parts,
                    exit_code=1,
                    stdout="# tests 6\n# pass 5\n# fail 1\n",
                    stderr="one failure\n",
                )
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=failing_runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(1, exit_code)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("failed", report["status"])
            self.assertEqual(1, report["totals"]["failed_count"])
            chaincode = report["suites"]["chaincode"]
            self.assertEqual(1, chaincode["exit_code"])
            self.assertEqual(5, chaincode["observed_passed_count"])
            self.assertEqual(1, chaincode["failed_count"])
            self.assertEqual("one failure\n", chaincode["stderr"])

    def test_missing_command_still_writes_a_failed_report(self):
        module = _load_script("run_release_tests.py")

        def missing_npm_runner(command, **kwargs):
            parts = tuple(command)
            if parts[:3] == ("npm", "--prefix", "fabric/chaincode"):
                raise FileNotFoundError(2, "No such file or directory", "npm")
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "missing-command.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=missing_npm_runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(1, exit_code)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("failed", report["status"])
            chaincode = report["suites"]["chaincode"]
            self.assertEqual(127, chaincode["exit_code"])
            self.assertEqual(1, chaincode["failed_count"])
            self.assertIn("npm", chaincode["stderr"])

    def test_python_execution_uses_selected_interpreter_but_report_is_portable(
        self,
    ):
        module = _load_script("run_release_tests.py")
        executed_python_commands = []

        def selected_python_runner(command, **kwargs):
            parts = tuple(command)
            if parts and parts[0] == "/chosen/python":
                executed_python_commands.append(list(parts))
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "selected-python.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=selected_python_runner,
                python_executable="/chosen/python",
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(0, exit_code)
            self.assertEqual(2, len(executed_python_commands))
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "python", report["suites"]["python"]["command"][0]
            )
            self.assertEqual(
                "python",
                report["environment"]["version_commands"]["python"][
                    "command"
                ][0],
            )

    def test_incomplete_counts_or_versions_fail_the_report(self):
        module = _load_script("run_release_tests.py")

        def incomplete_runner(command, **kwargs):
            parts = tuple(command)
            if parts[:3] == ("npm", "--prefix", "fabric/chaincode"):
                return _completed(
                    parts,
                    stdout="# tests 3\n# pass 2\n# fail 0\n",
                )
            if parts in (("node", "--version"), ("npm", "--version")):
                return _completed(parts, stdout="")
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "incomplete.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=incomplete_runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(1, exit_code)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("failed", report["status"])
            self.assertFalse(
                report["suites"]["chaincode"]["count_evidence_valid"]
            )
            self.assertFalse(
                report["environment"]["node_version_valid"]
            )
            self.assertFalse(
                report["environment"]["npm_version_valid"]
            )

    def test_skipped_unittests_are_not_counted_as_passed(self):
        module = _load_script("run_release_tests.py")

        def skipped_runner(command, **kwargs):
            parts = tuple(command)
            if "-m" in parts and "unittest" in parts:
                return _completed(
                    parts,
                    stderr=(
                        "Ran 5 tests in 0.100s\n\n"
                        "OK (skipped=2)\n"
                    ),
                )
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "skipped.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=skipped_runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(0, exit_code)
            report = json.loads(output.read_text(encoding="utf-8"))
            python_suite = report["suites"]["python"]
            self.assertEqual(3, python_suite["observed_passed_count"])
            self.assertEqual(0, python_suite["failed_count"])
            self.assertEqual(2, python_suite["skipped_count"])
            self.assertEqual(21, report["totals"]["observed_passed_count"])
            self.assertEqual(2, report["totals"]["skipped_count"])

    def test_expected_unittest_failure_is_not_a_failure(self):
        module = _load_script("run_release_tests.py")

        def expected_failure_runner(command, **kwargs):
            parts = tuple(command)
            if "-m" in parts and "unittest" in parts:
                return _completed(
                    parts,
                    stderr=(
                        "Ran 2 tests in 0.100s\n\n"
                        "OK (expected failures=1)\n"
                    ),
                )
            return self._runner(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "expected-failure.json"

            exit_code = module.run_release_tests(
                output,
                project_root=PROJECT_ROOT,
                runner=expected_failure_runner,
                timestamp="2026-07-29T00:00:00Z",
            )

            self.assertEqual(0, exit_code)
            report = json.loads(output.read_text(encoding="utf-8"))
            python_suite = report["suites"]["python"]
            self.assertEqual(1, python_suite["observed_passed_count"])
            self.assertEqual(0, python_suite["failed_count"])
            self.assertEqual(1, python_suite["skipped_count"])
            self.assertTrue(python_suite["count_evidence_valid"])

    def test_report_cannot_overwrite_or_alias_a_controlled_member(self):
        module = _load_script("run_release_tests.py")
        original = (PROJECT_ROOT / "Makefile").read_bytes()

        with self.assertRaisesRegex(ValueError, "controlled"):
            module.run_release_tests(
                PROJECT_ROOT / "Makefile",
                project_root=PROJECT_ROOT,
                runner=self._runner,
                timestamp="2026-07-29T00:00:00Z",
            )

        self.assertEqual(original, (PROJECT_ROOT / "Makefile").read_bytes())


class TreeManifestTests(unittest.TestCase):
    @staticmethod
    def _manifest_line(path: Path, member: str) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"{digest}  {member}\n"

    def test_tree_verification_is_read_only_and_rejects_extra_files(self):
        module = _load_script("verify_tree_manifest.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_bytes(b"artifact\n")
            manifest = root / "MANIFEST.sha256"
            manifest.write_text(
                self._manifest_line(artifact, "artifact.txt"),
                encoding="utf-8",
                newline="\n",
            )
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in (artifact, manifest)
            }

            report = module.verify_tree_manifest(root, manifest)

            self.assertEqual(
                {"artifacts_verified": 1, "status": "ok"}, report
            )
            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in (artifact, manifest)
            }
            self.assertEqual(before, after)
            self.assertEqual(
                report,
                module.verify_tree_manifest(
                    root,
                    Path("MANIFEST.sha256"),
                ),
            )

            (root / "extra.txt").write_text("extra\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extra"):
                module.verify_tree_manifest(root, manifest)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_tree_verification_rejects_undeclared_special_nodes(self):
        module = _load_script("verify_tree_manifest.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_bytes(b"artifact\n")
            manifest = root / "MANIFEST.sha256"
            manifest.write_text(
                self._manifest_line(artifact, "artifact.txt"),
                encoding="utf-8",
                newline="\n",
            )
            os.mkfifo(root / "undeclared.fifo")

            with self.assertRaisesRegex(ValueError, "unsupported"):
                module.verify_tree_manifest(root, manifest)

    def test_tree_verification_rejects_mismatch_traversal_and_symlink(self):
        module = _load_script("verify_tree_manifest.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_bytes(b"artifact\n")
            manifest = root / "MANIFEST.sha256"

            manifest.write_text(
                f"{'0' * 64}  artifact.txt\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "mismatch"):
                module.verify_tree_manifest(root, manifest)

            manifest.write_text(
                self._manifest_line(artifact, "../artifact.txt"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "safe relative"):
                module.verify_tree_manifest(root, manifest)

            link = root / "link.txt"
            link.symlink_to(artifact)
            manifest.write_text(
                self._manifest_line(artifact, "link.txt"),
                encoding="utf-8",
            )
            artifact.unlink()
            with self.assertRaisesRegex(ValueError, "symlink"):
                module.verify_tree_manifest(root, manifest)


class PublicVerifierCliTests(unittest.TestCase):
    def test_cli_routes_explicit_manifest_and_project_root(self):
        module = _load_script("verify_public_release.py")
        with mock.patch.object(
            module,
            "verify_public_release",
            return_value={"status": "ok"},
        ) as verify:
            exit_code = module.main(
                [
                    "--manifest",
                    "chosen-public-manifest.json",
                    "--project-root",
                    "chosen-project",
                ]
            )

        self.assertEqual(0, exit_code)
        verify.assert_called_once_with(
            Path("chosen-public-manifest.json"),
            Path("chosen-project"),
        )


if __name__ == "__main__":
    unittest.main()
