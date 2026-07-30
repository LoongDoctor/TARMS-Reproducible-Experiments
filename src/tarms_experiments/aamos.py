"""Controlled integrity analysis for confirmed AAMOS-derived patient days."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "participant_id",
    "date",
    "eligible",
    "clean_priority",
}

def _read_table(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(source)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(source)
    raise ValueError(f"unsupported patient-day file type: {suffix}")


def _as_boolean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0).astype(int).ne(0)
    accepted = {"1", "true", "yes", "y", "eligible"}
    return series.fillna("").astype(str).str.strip().str.lower().isin(accepted)


def prepare_patient_days(
    path: str | Path, column_mapping: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, object]]:
    required_mappings = {"participant_id", "date", "clean_priority"}
    missing_mappings = sorted(required_mappings - set(column_mapping))
    if missing_mappings:
        raise ValueError(f"column mapping missing: {', '.join(missing_mappings)}")
    source = _read_table(path)
    missing_source = sorted(
        source_name
        for source_name in column_mapping.values()
        if source_name not in source.columns
    )
    if missing_source:
        raise ValueError(f"source table missing mapped columns: {', '.join(missing_source)}")
    selected = source[
        [column_mapping[name] for name in column_mapping]
    ].rename(columns={source_name: target for target, source_name in column_mapping.items()})
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce")
    selected["clean_priority"] = pd.to_numeric(
        selected["clean_priority"], errors="coerce"
    )
    participant = selected["participant_id"].astype("string").str.strip()
    missing_required = (
        participant.isna()
        | participant.eq("")
        | selected["date"].isna()
        | selected["clean_priority"].isna()
    )
    excluded_missing = int(missing_required.sum())
    selected = selected.loc[~missing_required].copy()
    selected["participant_id"] = participant.loc[~missing_required].astype(str)
    selected["clean_priority"] = selected["clean_priority"].astype(int)
    if "eligible" in selected.columns:
        selected["eligible"] = _as_boolean(selected["eligible"])
    else:
        selected["eligible"] = True
    if selected.duplicated(["participant_id", "date"]).any():
        duplicates = int(selected.duplicated(["participant_id", "date"], keep=False).sum())
        raise ValueError(
            f"prepared input contains {duplicates} rows with duplicate participant/date keys"
        )
    selected = selected.sort_values(["participant_id", "date"]).reset_index(drop=True)
    flow = {
        "source_rows": int(len(source)),
        "excluded_missing_required": excluded_missing,
        "included_patient_days": int(len(selected)),
        "participants": int(selected["participant_id"].nunique()),
        "eligible_patient_days": int(selected["eligible"].sum()),
        "priority_counts": {
            str(int(key)): int(value)
            for key, value in selected["clean_priority"].value_counts().sort_index().items()
        },
        "date_min": selected["date"].min().date().isoformat() if len(selected) else None,
        "date_max": selected["date"].max().date().isoformat() if len(selected) else None,
    }
    _check_source(selected)
    return selected, flow


def _check_source(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"patient-day table missing columns: {', '.join(missing)}")
    if frame.duplicated(["participant_id", "date"]).any():
        raise ValueError("patient-day table contains duplicate participant/date keys")


def compute_integrity_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    required = REQUIRED_COLUMNS | {"attacked_priority", "verified_priority", "accepted"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"analysis table missing columns: {', '.join(missing)}")
    eligible = frame["eligible"].astype(bool)
    covered = eligible & frame["accepted"].astype(bool) & frame["verified_priority"].notna()
    definitions = [
        ("coverage", covered, eligible),
        ("abstention", eligible & ~covered, eligible),
        (
            "covered_agreement",
            covered & (frame["verified_priority"] == frame["clean_priority"]),
            covered,
        ),
        (
            "upward_discordance",
            eligible & (frame["attacked_priority"] > frame["clean_priority"]),
            eligible,
        ),
        (
            "priority_loss_discordance",
            eligible & (frame["attacked_priority"] < frame["clean_priority"]),
            eligible,
        ),
    ]
    rows = []
    for metric, numerator_mask, denominator_mask in definitions:
        numerator = int(numerator_mask.sum())
        denominator = int(denominator_mask.sum())
        rows.append(
            {
                "metric": metric,
                "n": numerator,
                "N": denominator,
                "value": numerator / denominator if denominator else np.nan,
            }
        )
    return pd.DataFrame(rows)


def inject_integrity_violations(
    frame: pd.DataFrame,
    *,
    seed: int,
    rate: float,
    provenance: str = "fixture",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    del frame, seed, rate, provenance
    raise RuntimeError(
        "legacy AAMOS injector retired; use the standard enhanced workflow "
        "and unified verifier"
    )


def resample_participant_clusters(
    frame: pd.DataFrame, sampled_participants: Sequence[str]
) -> pd.DataFrame:
    parts = []
    for draw, participant in enumerate(sampled_participants):
        cluster = frame.loc[frame["participant_id"] == participant].copy()
        if cluster.empty:
            raise ValueError(f"unknown sampled participant {participant!r}")
        cluster["bootstrap_cluster"] = f"{draw:04d}:{participant}"
        parts.append(cluster)
    if not parts:
        raise ValueError("at least one participant must be sampled")
    return pd.concat(parts, ignore_index=True)


def cluster_bootstrap_metrics(
    frame: pd.DataFrame, *, repetitions: int = 2_000, seed: int = 20260722
) -> pd.DataFrame:
    if repetitions <= 0:
        raise ValueError("bootstrap repetitions must be positive")
    participants = np.array(sorted(frame["participant_id"].astype(str).unique()))
    if participants.size < 2:
        raise ValueError("cluster bootstrap requires at least two participants")
    estimates = compute_integrity_metrics(frame).set_index("metric")
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {metric: [] for metric in estimates.index}
    for _ in range(repetitions):
        sampled = rng.choice(participants, size=participants.size, replace=True)
        replicate = resample_participant_clusters(frame, sampled)
        metrics = compute_integrity_metrics(replicate).set_index("metric")
        for metric in values:
            value = float(metrics.loc[metric, "value"])
            if np.isfinite(value):
                values[metric].append(value)
    rows = []
    for metric, samples in values.items():
        if not samples:
            low = high = np.nan
        else:
            low, high = np.quantile(samples, [0.025, 0.975])
        rows.append(
            {
                "metric": metric,
                "estimate": float(estimates.loc[metric, "value"]),
                "n": int(estimates.loc[metric, "n"]),
                "N": int(estimates.loc[metric, "N"]),
                "ci_low": float(low),
                "ci_high": float(high),
                "bootstrap_repetitions": repetitions,
                "bootstrap_unit": "participant",
            }
        )
    return pd.DataFrame(rows)
