"""Config-driven derivation of auditable AAMOS-00 participant-days."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd


FIXED_DERIVATION_CONFIG_BASENAME = "aamos00_derivation.yaml"
FIXED_DERIVATION_CONFIG_CANONICAL_SHA256 = (
    "37665ed58cb0e42242624a310351f7d3920e4815a7fe55f79848ec4f6557e1fe"
)
FIXED_DERIVATION_CONFIG_FILE_SHA256 = (
    "afdad4cef23c79307379e21011b1638ac91638f04b457dc21dc0be7888174baf"
)
OFFICIAL_AAMOS_RELEASE = {
    "doi": "10.7488/ds/3775",
    "participants": 22,
    "any_modality_participant_days": 2_054,
    "selected_analysis_source_sha256": {
        "aamos00_data_dictionary.xlsx": (
            "0d50002843b80b75db2a764ffd0e0a8139f881c1cf84b2ca6f8956ab5884bcbc"
        ),
        "anonym_aamos00_dailyquestionnaire.csv": (
            "8133aeba38c2bb5db0027731e64c09f7c2436e55fa25d845665786db88820f24"
        ),
        "anonym_aamos00_peakflow.csv": (
            "0b211e61d4aaa4613d25e95777af03ff535767b744ef79f38ef2722d2374ba83"
        ),
        "anonym_aamos00_smartinhaler.csv": (
            "925ea383539d14cefb0f92d52c1a254c1316271185f04f07de4c08281414dd9a"
        ),
        "anonym_aamos00_weeklyquestionnaire.csv": (
            "697e6d21f3d61145fec881345d9a6682ac9e95f27d4da2ee0a2dc63ec55f0eba"
        ),
    },
}
AAMOS_ANALYSIS_EXPECTATIONS = {
    "participants": 22,
    "daily_questionnaire_participant_days": 1_583,
    "eligible_three_item_days": 1_582,
    "priority_counts": {
        "0": 346,
        "1": 531,
        "2": 491,
        "3": 214,
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_config_sha256(config: Mapping[str, Any]) -> str:
    return canonical_json_sha256(config)


def source_inventory_sha256(
    source_files: Any,
) -> str:
    normalized = sorted(
        (
            {
                "name": str(item["name"]),
                "sha256": str(item["sha256"]),
            }
            for item in source_files
        ),
        key=lambda item: item["name"],
    )
    return canonical_json_sha256(normalized)


_canonical_config_sha256 = canonical_config_sha256


def _participant_ids(series: pd.Series, source_name: str) -> pd.Series:
    missing = series.isna() | series.astype("string").str.strip().eq("")
    if missing.any():
        raise ValueError(
            f"{source_name} participant ID must not be null or blank"
        )
    return series.astype(str).str.strip()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"AAMOS derivation config {name} must be a mapping")
    return value


def _validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ValueError("AAMOS derivation config must be a mapping")
    if config.get("version") != 1:
        raise ValueError("AAMOS derivation config version must be 1")
    source = _mapping(config.get("source"), "source")
    daily = _mapping(source.get("daily_questionnaire"), "source.daily_questionnaire")
    modalities = _mapping(source.get("modalities"), "source.modalities")
    priority = _mapping(config.get("priority"), "priority")
    payload = _mapping(config.get("payload"), "payload")
    flow = _mapping(config.get("flow"), "flow")
    data_contract = _mapping(config.get("data_contract"), "data_contract")
    if priority.get("rule") != "sum_boolean_true":
        raise ValueError("unsupported AAMOS priority rule")
    if priority.get("missing_policy") != "retain_ineligible":
        raise ValueError("unsupported AAMOS priority missing policy")
    symptom_columns = priority.get("symptom_columns")
    daily_columns = payload.get("daily_columns")
    if not isinstance(symptom_columns, list) or not symptom_columns:
        raise ValueError("priority.symptom_columns must be a non-empty list")
    if not isinstance(daily_columns, list) or not daily_columns:
        raise ValueError("payload.daily_columns must be a non-empty list")
    if not set(symptom_columns).issubset(daily_columns):
        raise ValueError("priority symptom columns must be included in payload")
    if payload.get("include_modality_counts") is not True:
        raise ValueError("payload.include_modality_counts must be true")
    if flow.get("exclude_negative_modality_days") is not True:
        raise ValueError("flow.exclude_negative_modality_days must be true")
    participant_column = source.get("participant_column")
    relative_day_column = source.get("relative_day_column")
    if not isinstance(participant_column, str) or not participant_column:
        raise ValueError("source.participant_column must be a non-empty string")
    if not isinstance(relative_day_column, str) or not relative_day_column:
        raise ValueError("source.relative_day_column must be a non-empty string")
    if not isinstance(daily.get("filename"), str) or not daily["filename"]:
        raise ValueError("daily questionnaire filename must be configured")
    if daily.get("required") is not True:
        raise ValueError("daily questionnaire source must be required")
    normalized_modalities: dict[str, dict[str, Any]] = {}
    for name, raw_spec in modalities.items():
        if not isinstance(name, str) or not name:
            raise ValueError("modality names must be non-empty strings")
        spec = _mapping(raw_spec, f"source.modalities.{name}")
        filename = spec.get("filename")
        count_field = spec.get("count_field")
        required = spec.get("required")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"modality {name} filename must be configured")
        if not isinstance(count_field, str) or not count_field:
            raise ValueError(f"modality {name} count_field must be configured")
        if not isinstance(required, bool):
            raise ValueError(f"modality {name} required must be boolean")
        normalized_modalities[name] = {
            "filename": filename,
            "count_field": count_field,
            "required": required,
        }
    if not normalized_modalities:
        raise ValueError("at least one modality must be configured")
    return {
        "participant_column": participant_column,
        "relative_day_column": relative_day_column,
        "daily_filename": daily["filename"],
        "modalities": normalized_modalities,
        "symptom_columns": list(symptom_columns),
        "payload_columns": list(daily_columns),
        "priority_rule": priority["rule"],
        "missing_policy": priority["missing_policy"],
        "data_contract": dict(data_contract),
    }


def _strict_integer_days(series: pd.Series, *, allow_negative: bool) -> pd.Series:
    numeric = pd.to_numeric(series, errors="raise")
    if numeric.isna().any() or not (numeric == numeric.astype("int64")).all():
        raise ValueError("AAMOS relative day must be an integer")
    days = numeric.astype("int64")
    if not allow_negative and (days < 0).any():
        raise ValueError("AAMOS relative day must be non-negative")
    return days


def _strict_boolean(series: pd.Series, name: str) -> pd.Series:
    def parse(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        raise ValueError(f"AAMOS symptom column {name} must contain boolean values")

    return series.map(parse).astype("boolean")


def _read_modality_counts(
    root: Path,
    *,
    name: str,
    spec: Mapping[str, Any],
    participant_column: str,
    relative_day_column: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    path = root / str(spec["filename"])
    count_field = str(spec["count_field"])
    if not path.exists():
        if spec["required"]:
            raise FileNotFoundError(f"required AAMOS source is missing: {path.name}")
        return (
            pd.DataFrame(
                {
                    "participant_id": pd.Series(dtype="object"),
                    "relative_day": pd.Series(dtype="int64"),
                    count_field: pd.Series(dtype="int64"),
                }
            ),
            {
                "source_present": False,
                "source_rows": 0,
                "excluded_negative_day_rows": 0,
                "included_rows": 0,
                "participant_days_with_records": 0,
            },
        )
    frame = pd.read_csv(path)
    required = {participant_column, relative_day_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} missing columns: {', '.join(missing)}")
    frame["participant_id"] = _participant_ids(
        frame[participant_column], path.name
    )
    source_rows = len(frame)
    if source_rows:
        all_days = _strict_integer_days(
            frame[relative_day_column], allow_negative=True
        )
        negative = all_days < 0
        frame = frame.loc[~negative].copy()
        frame["relative_day"] = all_days.loc[~negative].astype("int64")
    else:
        negative = pd.Series([], dtype=bool)
        frame = frame.copy()
        frame["relative_day"] = pd.Series(dtype="int64")
    counts = (
        frame.groupby(["participant_id", "relative_day"], as_index=False)
        .size()
        .rename(columns={"size": count_field})
    )
    counts[count_field] = counts[count_field].astype("int64")
    return counts, {
        "source_present": True,
        "source_rows": int(source_rows),
        "excluded_negative_day_rows": int(negative.sum()),
        "included_rows": int(len(frame)),
        "participant_days_with_records": int(len(counts)),
    }


def _json_scalar(value: object) -> object:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        return value.item()
    return value


def build_patient_days(
    source_dir: str | Path, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Build one configured questionnaire-row participant-day per source key."""

    resolved = _validate_config(config)
    config_sha256 = canonical_config_sha256(config)
    root = Path(source_dir)
    daily_path = root / resolved["daily_filename"]
    if not daily_path.exists():
        raise FileNotFoundError(
            f"required AAMOS source is missing: {daily_path.name}"
        )
    daily = pd.read_csv(daily_path)
    required = {
        resolved["participant_column"],
        resolved["relative_day_column"],
        *resolved["payload_columns"],
    }
    missing = sorted(required - set(daily.columns))
    if missing:
        raise ValueError(f"{daily_path.name} missing columns: {', '.join(missing)}")
    daily["participant_id"] = _participant_ids(
        daily[resolved["participant_column"]], daily_path.name
    )
    daily["relative_day"] = _strict_integer_days(
        daily[resolved["relative_day_column"]], allow_negative=False
    )
    if daily.duplicated(["participant_id", "relative_day"]).any():
        raise ValueError("duplicate participant/day in daily questionnaire")

    symptom_values = pd.DataFrame(
        {
            column: _strict_boolean(daily[column], column)
            for column in resolved["symptom_columns"]
        }
    )
    complete_symptoms = symptom_values.notna().all(axis=1)
    daily["clean_priority"] = (
        symptom_values.sum(axis=1).where(complete_symptoms).astype("Int64")
    )
    daily["eligible"] = complete_symptoms.astype(bool)

    modality_flow: dict[str, dict[str, object]] = {}
    modality_fields: list[str] = []
    output = daily
    for name, spec in resolved["modalities"].items():
        counts, counts_flow = _read_modality_counts(
            root,
            name=name,
            spec=spec,
            participant_column=resolved["participant_column"],
            relative_day_column=resolved["relative_day_column"],
        )
        count_field = str(spec["count_field"])
        source_flag = f"{name}_source_present"
        day_flag = f"{name}_day_present"
        output = output.merge(
            counts, how="left", on=["participant_id", "relative_day"]
        )
        output[source_flag] = bool(counts_flow["source_present"])
        output[day_flag] = output[count_field].notna()
        counts_flow["questionnaire_days_with_records"] = int(output[day_flag].sum())
        if counts_flow["source_present"]:
            output[count_field] = output[count_field].fillna(0).astype("int64")
        else:
            output[count_field] = pd.Series(pd.NA, index=output.index, dtype="Int64")
        modality_fields.extend([count_field, source_flag, day_flag])
        modality_flow[name] = counts_flow

    payload_fields = [*resolved["payload_columns"]]
    payload_modality_fields = [
        str(spec["count_field"]) for spec in resolved["modalities"].values()
    ]
    output["payload_json"] = output[
        [*payload_fields, *payload_modality_fields]
    ].apply(
        lambda row: json.dumps(
            {key: _json_scalar(value) for key, value in row.items()},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        axis=1,
    )
    output = output.sort_values(
        ["participant_id", "relative_day"], kind="stable"
    ).reset_index(drop=True)

    source_files = sorted(path for path in root.iterdir() if path.is_file())
    flow = {
        "participants": int(output["participant_id"].nunique()),
        "participant_days": int(len(output)),
        "eligible_participant_days": int(output["eligible"].sum()),
        "priority_counts": {
            str(int(key)): int(value)
            for key, value in output["clean_priority"]
            .dropna()
            .value_counts()
            .sort_index()
            .items()
        },
        "relative_day_min": int(output["relative_day"].min()),
        "relative_day_max": int(output["relative_day"].max()),
        "modalities": modality_flow,
        "derivation": {
            "config_canonical_sha256": config_sha256,
            "priority_rule": resolved["priority_rule"],
            "priority_columns": resolved["symptom_columns"],
            "missing_policy": resolved["missing_policy"],
        },
        "data_contract": resolved["data_contract"],
        "source_files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in source_files
        ],
    }
    columns = [
        "participant_id",
        "relative_day",
        "eligible",
        "clean_priority",
        "payload_json",
        *modality_fields,
    ]
    return output[columns], flow
