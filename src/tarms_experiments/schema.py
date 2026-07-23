"""Schema validation for experiment observations."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path


class SchemaValidationError(ValueError):
    """Raised when an experiment artifact violates its declared schema."""


PYTHON_RAW_COLUMNS = (
    "run_id",
    "seed",
    "batch_size",
    "repetition",
    "stage",
    "duration_ns",
    "record_count",
    "late_count",
    "provenance",
)

FABRIC_RAW_COLUMNS = (
    "run_id",
    "workload",
    "client_id",
    "operation",
    "start_ns",
    "end_ns",
    "duration_ns",
    "txid",
    "commit_status",
    "block_number",
    "attempt",
    "error_class",
    "payload_bytes",
    "provenance",
)


@dataclass(frozen=True)
class ValidationReport:
    schema_name: str
    row_count: int
    provenance_values: frozenset[str]


def _parse_nonnegative_int(value: str, field: str, row_number: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(
            f"row {row_number}: {field} must be an integer"
        ) from exc
    if parsed < 0:
        raise SchemaValidationError(f"row {row_number}: {field} must be nonnegative")
    return parsed


def validate_raw_table(path: str | Path, schema_name: str) -> ValidationReport:
    if schema_name != "python_raw":
        raise SchemaValidationError(f"unknown schema {schema_name!r}")
    source = Path(path)
    with source.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = tuple(reader.fieldnames or ())
        missing = [column for column in PYTHON_RAW_COLUMNS if column not in fields]
        if missing:
            raise SchemaValidationError(
                f"{source.name} missing required columns: {', '.join(missing)}"
            )
        row_count = 0
        provenance_values: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            for field in ("seed", "batch_size", "repetition", "record_count", "late_count"):
                _parse_nonnegative_int(row[field], field, row_number)
            duration = _parse_nonnegative_int(row["duration_ns"], "duration_ns", row_number)
            if duration <= 0:
                raise SchemaValidationError(
                    f"row {row_number}: duration_ns must be positive"
                )
            if not row["run_id"].strip() or not row["stage"].strip():
                raise SchemaValidationError(
                    f"row {row_number}: run_id and stage must be non-empty"
                )
            provenance = row["provenance"].strip()
            if not provenance:
                raise SchemaValidationError(
                    f"row {row_number}: provenance must be non-empty"
                )
            provenance_values.add(provenance)
    if row_count == 0:
        raise SchemaValidationError(f"{source.name} contains no observations")
    return ValidationReport(
        schema_name=schema_name,
        row_count=row_count,
        provenance_values=frozenset(provenance_values),
    )


def validate_fabric_jsonl(
    path: str | Path, *, submission: bool = False
) -> ValidationReport:
    source = Path(path)
    row_count = 0
    provenance_values: set[str] = set()
    with source.open(encoding="utf-8") as stream:
        for row_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaValidationError(
                    f"line {row_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SchemaValidationError(f"line {row_number}: row must be an object")
            missing = [field for field in FABRIC_RAW_COLUMNS if field not in row]
            if missing:
                raise SchemaValidationError(
                    f"line {row_number}: missing required fields: {', '.join(missing)}"
                )
            start = _parse_nonnegative_int(str(row["start_ns"]), "start_ns", row_number)
            end = _parse_nonnegative_int(str(row["end_ns"]), "end_ns", row_number)
            duration = _parse_nonnegative_int(
                str(row["duration_ns"]), "duration_ns", row_number
            )
            if end <= start or duration != end - start:
                raise SchemaValidationError(
                    f"line {row_number}: duration_ns must be positive and equal end_ns-start_ns"
                )
            attempt = _parse_nonnegative_int(str(row["attempt"]), "attempt", row_number)
            if attempt < 1:
                raise SchemaValidationError(f"line {row_number}: attempt must be positive")
            _parse_nonnegative_int(
                str(row["payload_bytes"]), "payload_bytes", row_number
            )
            try:
                block_number = int(row["block_number"])
            except (TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"line {row_number}: block_number must be an integer"
                ) from exc
            if block_number < -1:
                raise SchemaValidationError(
                    f"line {row_number}: block_number must be >= -1"
                )
            for field in ("run_id", "workload", "client_id", "operation", "commit_status"):
                if not str(row[field]).strip():
                    raise SchemaValidationError(
                        f"line {row_number}: {field} must be non-empty"
                    )
            provenance = str(row["provenance"])
            if submission and provenance != "measured_fabric":
                raise SchemaValidationError(
                    f"line {row_number}: provenance {provenance!r} is not submission eligible"
                )
            provenance_values.add(provenance)
            row_count += 1
    if row_count == 0:
        raise SchemaValidationError(f"{source.name} contains no observations")
    return ValidationReport(
        schema_name="fabric_jsonl",
        row_count=row_count,
        provenance_values=frozenset(provenance_values),
    )
