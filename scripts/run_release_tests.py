#!/usr/bin/env python3
"""Run the release gates and atomically write one machine-readable report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.release_identity import (  # noqa: E402
    controlled_source_identity,
    controlled_source_members,
)


SHELL_MEMBERS = (
    "fabric/network/bootstrap.sh",
    "fabric/network/run_experiments.sh",
    "fabric/network/teardown.sh",
)
Runner = Callable[..., subprocess.CompletedProcess[str]]
_UNITTEST_RAN = re.compile(r"\bRan\s+(\d+)\s+tests?\b")
_UNITTEST_FAILURES = re.compile(
    r"(?<!expected )\bfailures=(\d+)\b"
)
_UNITTEST_ERRORS = re.compile(r"\berrors=(\d+)\b")
_UNITTEST_SKIPPED = re.compile(r"\bskipped=(\d+)\b")
_UNITTEST_EXPECTED_FAILURES = re.compile(
    r"\bexpected failures=(\d+)\b"
)
_UNITTEST_UNEXPECTED_SUCCESSES = re.compile(
    r"\bunexpected successes=(\d+)\b"
)
_TAP_TESTS = re.compile(r"(?m)^# tests\s+(\d+)\s*$")
_TAP_PASS = re.compile(r"(?m)^# pass\s+(\d+)\s*$")
_TAP_FAIL = re.compile(r"(?m)^# fail\s+(\d+)\s*$")
_TAP_SKIPPED = re.compile(r"(?m)^# skipped\s+(\d+)\s*$")
_TAP_CANCELLED = re.compile(r"(?m)^# cancelled\s+(\d+)\s*$")
_TAP_TODO = re.compile(r"(?m)^# todo\s+(\d+)\s*$")
_VERSION_PATTERN = re.compile(
    r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"
)
_PYTHON_ENVIRONMENT_PROBE = (
    "import json,platform,sys;"
    "print(json.dumps({"
    "'implementation':platform.python_implementation(),"
    "'version':platform.python_version(),"
    "'version_exact':sys.version"
    "},sort_keys=True))"
)


def _default_runner(
    command: Sequence[str],
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


def _run(
    runner: Runner,
    command: list[str],
    *,
    project_root: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_path = str(project_root / "src")
    inherited_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not inherited_python_path
        else source_path + os.pathsep + inherited_python_path
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        return runner(
            command,
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return subprocess.CompletedProcess(
            command,
            127,
            stdout="",
            stderr=f"{type(error).__name__}: {error}\n",
        )


def _integer_match(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _unittest_counts(
    result: subprocess.CompletedProcess[str],
) -> tuple[int, int, int, bool]:
    output = result.stdout + "\n" + result.stderr
    total = _integer_match(_UNITTEST_RAN, output)
    failed = (
        _integer_match(_UNITTEST_FAILURES, output)
        + _integer_match(_UNITTEST_ERRORS, output)
        + _integer_match(_UNITTEST_UNEXPECTED_SUCCESSES, output)
    )
    skipped = (
        _integer_match(_UNITTEST_SKIPPED, output)
        + _integer_match(_UNITTEST_EXPECTED_FAILURES, output)
    )
    if result.returncode and failed == 0:
        failed = max(1, total)
    passed = max(0, total - failed - skipped)
    evidence_valid = (
        total > 0 and passed + failed + skipped == total
    )
    if total == 0 and result.returncode:
        failed = max(1, failed)
    return passed, failed, skipped, evidence_valid


def _tap_counts(
    result: subprocess.CompletedProcess[str],
) -> tuple[int, int, int, bool]:
    output = result.stdout + "\n" + result.stderr
    total = _integer_match(_TAP_TESTS, output)
    passed = _integer_match(_TAP_PASS, output)
    failed = _integer_match(_TAP_FAIL, output)
    skipped = (
        _integer_match(_TAP_SKIPPED, output)
        + _integer_match(_TAP_CANCELLED, output)
        + _integer_match(_TAP_TODO, output)
    )
    if result.returncode and failed == 0:
        failed = max(1, total - passed)
    evidence_valid = (
        total > 0 and passed + failed + skipped == total
    )
    if total == 0 and result.returncode:
        failed = max(1, failed)
    return passed, failed, skipped, evidence_valid


def _suite_record(
    command: list[str],
    result: subprocess.CompletedProcess[str],
    counts: tuple[int, int, int, bool],
) -> dict[str, object]:
    passed, failed, skipped, evidence_valid = counts
    return {
        "command": command,
        "count_evidence_valid": evidence_valid,
        "exit_code": result.returncode,
        "failed_count": failed,
        "observed_passed_count": passed,
        "skipped_count": skipped,
        "stderr": result.stderr,
        "stdout": result.stdout,
    }


def _python_environment(
    result: subprocess.CompletedProcess[str],
) -> tuple[dict[str, str], bool]:
    if result.returncode:
        return {
            "python_implementation": "",
            "python_version": "",
            "python_version_exact": "",
        }, False
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "python_implementation": "",
            "python_version": "",
            "python_version_exact": "",
        }, False
    if not isinstance(value, dict):
        return {
            "python_implementation": "",
            "python_version": "",
            "python_version_exact": "",
        }, False
    fields = {
        "python_implementation": value.get("implementation"),
        "python_version": value.get("version"),
        "python_version_exact": value.get("version_exact"),
    }
    valid = all(
        isinstance(item, str) and bool(item.strip())
        for item in fields.values()
    )
    return (
        {
            key: item if isinstance(item, str) else ""
            for key, item in fields.items()
        },
        valid,
    )


def _command_observation(
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    return {
        "command": command,
        "exit_code": result.returncode,
        "stderr": result.stderr,
        "stdout": result.stdout,
    }


def _reject_report_target(output: Path, project_root: Path) -> Path:
    root = project_root.resolve(strict=True)
    target = Path(output)
    if target.exists() and target.is_symlink():
        raise ValueError("test report target must not be a symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = target.parent.resolve(strict=True)
    resolved_target = parent / target.name

    for member in controlled_source_members(root):
        controlled = (root / member).resolve(strict=True)
        if resolved_target == controlled:
            raise ValueError(
                "test report target aliases a controlled-source member"
            )
        if target.exists():
            try:
                if os.path.samefile(target, controlled):
                    raise ValueError(
                        "test report target aliases a controlled-source member"
                    )
            except FileNotFoundError:
                pass
    if target.exists() and not target.is_file():
        raise ValueError("test report target must be a regular file")
    return resolved_target


def _write_atomic(output: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_release_tests(
    output: str | Path,
    *,
    project_root: str | Path = PROJECT_ROOT,
    python_executable: str | Path | None = None,
    runner: Runner = _default_runner,
    timestamp: str | None = None,
) -> int:
    """Run all four release suites and publish their complete observations."""

    root = Path(project_root).resolve(strict=True)
    report_target = _reject_report_target(Path(output), root)
    before_identity = controlled_source_identity(root)

    portable_python_command = [
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
        "-v",
    ]
    selected_python = str(
        python_executable
        if python_executable is not None
        else sys.executable
    )
    if not selected_python:
        raise ValueError("Python executable must be nonempty")
    python_execution_command = [
        selected_python,
        *portable_python_command[1:],
    ]
    chaincode_command = ["npm", "--prefix", "fabric/chaincode", "test"]
    client_command = ["npm", "--prefix", "fabric/client", "test"]

    python_result = _run(
        runner, python_execution_command, project_root=root
    )
    chaincode_result = _run(
        runner, chaincode_command, project_root=root
    )
    client_result = _run(
        runner, client_command, project_root=root
    )

    shell_commands = [
        ["bash", "-n", member] for member in SHELL_MEMBERS
    ]
    shell_results = [
        _run(runner, command, project_root=root)
        for command in shell_commands
    ]
    shell_failed = sum(result.returncode != 0 for result in shell_results)
    shell_record = {
        "command": shell_commands,
        "commands": shell_commands,
        "count_evidence_valid": True,
        "exit_code": next(
            (
                result.returncode
                for result in shell_results
                if result.returncode
            ),
            0,
        ),
        "failed_count": shell_failed,
        "observed_passed_count": len(shell_results) - shell_failed,
        "skipped_count": 0,
        "stderr": "".join(result.stderr for result in shell_results),
        "stdout": "".join(result.stdout for result in shell_results),
    }

    suites = {
        "python": _suite_record(
            portable_python_command,
            python_result,
            _unittest_counts(python_result),
        ),
        "chaincode": _suite_record(
            chaincode_command,
            chaincode_result,
            _tap_counts(chaincode_result),
        ),
        "client": _suite_record(
            client_command,
            client_result,
            _tap_counts(client_result),
        ),
        "shell": shell_record,
    }
    portable_python_probe_command = [
        "python",
        "-c",
        _PYTHON_ENVIRONMENT_PROBE,
    ]
    python_probe_execution_command = [
        selected_python,
        *portable_python_probe_command[1:],
    ]
    python_probe_result = _run(
        runner, python_probe_execution_command, project_root=root
    )
    node_result = _run(runner, ["node", "--version"], project_root=root)
    npm_result = _run(runner, ["npm", "--version"], project_root=root)
    python_environment, python_environment_valid = _python_environment(
        python_probe_result
    )
    node_version = (
        node_result.stdout.strip() or node_result.stderr.strip()
    )
    npm_version = npm_result.stdout.strip() or npm_result.stderr.strip()
    node_version_valid = bool(
        node_result.returncode == 0
        and _VERSION_PATTERN.fullmatch(node_version)
    )
    npm_version_valid = bool(
        npm_result.returncode == 0
        and _VERSION_PATTERN.fullmatch(npm_version)
    )
    after_identity = controlled_source_identity(root)
    source_stable = before_identity == after_identity
    passed_total = sum(
        int(record["observed_passed_count"])
        for record in suites.values()
    )
    failed_total = sum(
        int(record["failed_count"]) for record in suites.values()
    )
    skipped_total = sum(
        int(record["skipped_count"]) for record in suites.values()
    )
    suites_passed = all(
        int(record["exit_code"]) == 0
        and int(record["failed_count"]) == 0
        and bool(record["count_evidence_valid"])
        for record in suites.values()
    )
    versions_passed = (
        python_environment_valid
        and node_version_valid
        and npm_version_valid
    )
    status = (
        "passed"
        if suites_passed and versions_passed and source_stable
        else "failed"
    )
    report = {
        "controlled_source_identity_sha256": before_identity,
        "controlled_source_identity_stable": source_stable,
        "environment": {
            **python_environment,
            "node_version": node_version,
            "node_version_valid": node_version_valid,
            "npm_version": npm_version,
            "npm_version_valid": npm_version_valid,
            "platform": platform.platform(),
            "version_commands": {
                "node": _command_observation(
                    ["node", "--version"], node_result
                ),
                "npm": _command_observation(
                    ["npm", "--version"], npm_result
                ),
                "python": _command_observation(
                    portable_python_probe_command,
                    python_probe_result,
                ),
            },
        },
        "generated_at_utc": timestamp
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "suites": suites,
        "totals": {
            "failed_count": failed_total,
            "observed_passed_count": passed_total,
            "skipped_count": skipped_total,
        },
    }
    payload = (
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    _write_atomic(report_target, payload)
    return 0 if status == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run TARMS release gates and write one JSON report."
    )
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    return run_release_tests(arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
