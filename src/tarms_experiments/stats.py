"""Predefined summaries for repeated TARMS measurements."""

from __future__ import annotations

import numpy as np
import pandas as pd


GROUP_COLUMNS = [
    "run_id",
    "batch_size",
    "stage",
    "record_count",
    "late_count",
    "provenance",
]


def _bootstrap_median_interval(
    values: np.ndarray, *, repetitions: int, rng: np.random.Generator
) -> tuple[float, float]:
    samples = rng.choice(values, size=(repetitions, values.size), replace=True)
    medians = np.median(samples, axis=1)
    low, high = np.quantile(medians, [0.025, 0.975])
    return float(low), float(high)


def summarize_observations(
    frame: pd.DataFrame, *, bootstrap_reps: int = 2_000, seed: int = 20260722
) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("cannot summarize an empty observation table")
    if bootstrap_reps <= 0:
        raise ValueError("bootstrap_reps must be positive")
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for keys, group in frame.groupby(GROUP_COLUMNS, sort=True, dropna=False):
        duration_ms = group["duration_ns"].to_numpy(dtype=float) / 1_000_000.0
        throughput = (
            group["record_count"].to_numpy(dtype=float)
            / (group["duration_ns"].to_numpy(dtype=float) / 1_000_000_000.0)
        )
        ci_low, ci_high = _bootstrap_median_interval(
            duration_ms, repetitions=bootstrap_reps, rng=rng
        )
        row = dict(zip(GROUP_COLUMNS, keys, strict=True))
        row.update(
            {
                "n": int(duration_ms.size),
                "median_ms": float(np.median(duration_ms)),
                "q1_ms": float(np.quantile(duration_ms, 0.25)),
                "q3_ms": float(np.quantile(duration_ms, 0.75)),
                "p95_ms": float(np.quantile(duration_ms, 0.95)),
                "ci_low_ms": ci_low,
                "ci_high_ms": ci_high,
                "median_records_s": float(np.median(throughput)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
