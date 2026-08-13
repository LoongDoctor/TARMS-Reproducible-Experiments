"""SciencePlots figures with explicit provenance gates and source data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd


EXPERIMENTS_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("MPLCONFIGDIR", str(EXPERIMENTS_ROOT / "tmp" / "matplotlib"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

from matplotlib.ticker import FuncFormatter
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from .aamos_experiment import (
    ATTACK_RATES,
    FIXED_SEEDS,
    METRIC_DEFINITION_VERSION,
    PIPELINES,
)
from .aamos_scenarios import BOUNDARY_SCENARIOS, REJECT_SCENARIOS
from .aamos_source import (
    FIXED_DERIVATION_CONFIG_BASENAME,
    FIXED_DERIVATION_CONFIG_CANONICAL_SHA256,
    FIXED_DERIVATION_CONFIG_FILE_SHA256,
    OFFICIAL_AAMOS_RELEASE,
    source_inventory_sha256,
)
from .deterministic_figures import (
    close_new_figures,
    configure_style,
    plt,
    prepare_figure_output,
    publish_figure_bundle,
    read_source_csv,
)
from .provenance import (
    assert_submission_eligible,
    load_manifest,
    sha256_file,
)
from .schema import validate_fabric_jsonl


BLUE = "#2D5F8B"
ORANGE = "#D47A1F"
OLIVE = "#667A46"
PINK = "#A65772"
INK = "#25313B"
GREY = "#7B8790"
LIGHT_GREY = "#D9DEE2"
FABRIC_INSTALL_OPERATION = "InstallAnchorCAS"
SIGNATURE_ADMISSION_TITLE = "Signature + admission throughput"
SIGNATURE_ADMISSION_YLABEL = "Signature + admission records s$^{-1}$"


STAGE_STYLE = {
    "sign_batch": ("Signing", BLUE, "o", "-"),
    "verify_batch": ("Verification", ORANGE, "s", "--"),
    "merkle_build": ("Merkle build", OLIVE, "^", "-."),
    "signature_admission_batch": ("Signature + admission", PINK, "D", ":"),
}


def _apply_reader_colors() -> None:
    plt.rcParams.update(
        {
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "text.color": INK,
            "grid.color": LIGHT_GREY,
        }
    )


def _configure_figure_style(*, submission: bool) -> None:
    configure_style("submission" if submission else "preview")
    _apply_reader_colors()


def _deterministic_source_columns(
    frame: pd.DataFrame,
    preferred: Iterable[str],
) -> tuple[str, ...]:
    leading = tuple(column for column in preferred if column in frame)
    remaining = tuple(sorted(set(frame.columns) - set(leading)))
    return (*leading, *remaining)


def model_ledger_bytes(batch_sizes: Iterable[int]) -> pd.DataFrame:
    rows = []
    for batch_size in map(int, batch_sizes):
        if batch_size <= 0:
            raise ValueError("batch sizes must be positive")
        anchor = {
            "aid": "a" * 64,
            "kappa": "patient-000001|2026-07-22T00:00Z",
            "version": 2,
            "root": "b" * 64,
            "prevAid": "d" * 64,
            "recordCount": batch_size,
            "uriHash": "c" * 64,
            "createdAt": "2026-07-22T00:00:00Z",
        }
        latest = {
            "kappa": anchor["kappa"],
            "aid": anchor["aid"],
            "version": 2,
            "root": anchor["root"],
        }
        anchor_bytes = len(json.dumps(anchor, separators=(",", ":")).encode())
        latest_bytes = len(json.dumps(latest, separators=(",", ":")).encode())
        rows.extend(
            [
                {
                    "batch_size": batch_size,
                    "strategy": "raw records",
                    "bytes": batch_size * 384,
                    "assumption": "384 B encoded event+signature per record",
                    "anchor_version": None,
                },
                {
                    "batch_size": batch_size,
                    "strategy": "hash per record",
                    "bytes": batch_size * 48,
                    "assumption": "32 B digest+16 B key/index per record",
                    "anchor_version": None,
                },
                {
                    "batch_size": batch_size,
                    "strategy": "TARMS anchor",
                    "bytes": anchor_bytes + latest_bytes,
                    "assumption": (
                        "UTF-8 version-2 successor anchor JSON+latest-state JSON; "
                        "includes nonempty prevAid, uriHash, and latest root"
                    ),
                    "anchor_version": 2,
                },
            ]
        )
    return pd.DataFrame(rows)


def model_window_tradeoff(
    window_minutes: Iterable[int], *, anchor_bytes: int
) -> pd.DataFrame:
    if anchor_bytes <= 0:
        raise ValueError("anchor_bytes must be positive")
    rows = []
    for window in map(int, window_minutes):
        if window <= 0:
            raise ValueError("window sizes must be positive")
        anchors_day = int(np.ceil(24 * 60 / window))
        rows.append(
            {
                "window_min": window,
                "anchors_day": anchors_day,
                "anchor_bytes": int(anchor_bytes),
                "modeled_bytes_day": anchors_day * int(anchor_bytes),
                "modeled_kib_day": anchors_day * int(anchor_bytes) / 1024.0,
                "mean_batching_wait_s": window * 30.0,
                "maximum_batching_wait_s": window * 60.0,
                "assumption": "uniform arrivals; one anchor+latest payload per window",
            }
        )
    return pd.DataFrame(rows).sort_values("window_min").reset_index(drop=True)


@close_new_figures
def render_window_tradeoff_figure(
    output_dir: str | Path,
    *,
    anchor_bytes: int | None = None,
    submission: bool = False,
) -> dict[str, Path]:
    stem = "fig_05_window_tradeoff"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )
    if anchor_bytes is None:
        reference = model_ledger_bytes([4096])
        anchor_bytes = int(
            reference.loc[reference["strategy"] == "TARMS anchor", "bytes"].iloc[0]
        )
    data = model_window_tradeoff([1, 5, 10, 15, 30, 60], anchor_bytes=anchor_bytes)

    _configure_figure_style(submission=submission)
    figure, (ax_a, ax_b) = plt.subplots(
        1, 2, figsize=(7.0, 2.75), constrained_layout=True
    )
    x = data["window_min"].to_numpy(dtype=float)
    ax_a.plot(
        x,
        data["modeled_kib_day"],
        color=BLUE,
        marker="o",
        label="Anchor+latest payload",
    )
    ax_a.set(xscale="log", yscale="log", xlabel="Anchoring window (min)", ylabel="Modeled payload (KiB day$^{-1}$)")
    ax_a.set_xticks(x, [str(int(value)) for value in x])
    ax_a.set_title("Daily ledger-payload model", loc="left")
    ax_a.grid(True, which="major")
    ax_a.legend(loc="upper right")

    ax_b.plot(
        x,
        data["mean_batching_wait_s"],
        color=ORANGE,
        marker="s",
        label="Mean wait",
    )
    ax_b.plot(
        x,
        data["maximum_batching_wait_s"],
        color=PINK,
        marker="D",
        linestyle="--",
        label="Maximum wait",
    )
    ax_b.set(xscale="log", xlabel="Anchoring window (min)", ylabel="Batching wait (s)")
    ax_b.set_xticks(x, [str(int(value)) for value in x])
    ax_b.set_ylim(bottom=0)
    ax_b.set_title("Visibility delay before ledger submission", loc="left")
    ax_b.grid(True)
    ax_b.legend(loc="upper left")
    _panel_label(ax_a, "a")
    _panel_label(ax_b, "b")
    return publish_figure_bundle(
        figure,
        data,
        output_paths,
        columns=(
            "window_min",
            "anchors_day",
            "anchor_bytes",
            "modeled_bytes_day",
            "modeled_kib_day",
            "mean_batching_wait_s",
            "maximum_batching_wait_s",
            "assumption",
        ),
        sort_by=("window_min",),
    )


@close_new_figures
def render_component_conformance_figure(
    raw_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    submission: bool,
) -> dict[str, Path]:
    manifest = load_manifest(manifest_path)
    if submission:
        assert_submission_eligible([manifest])
    raw = read_source_csv(raw_path)
    summary = (
        raw.groupby(
            ["component", "case", "expected_result", "observed_result"],
            as_index=False,
        )
        .agg(
            matching_executions=("matches_rule", "sum"),
            total_executions=("matches_rule", "size"),
        )
    )
    summary["proportion_matching"] = (
        summary["matching_executions"] / summary["total_executions"]
    )

    components = ["Signature", "AcceptOnce", "Merkle proof", "Latest CAS"]
    cases = [
        "valid_signature",
        "forged_signature",
        "modified_signed_payload",
        "first_admission",
        "idempotent_retransmission",
        "counter_conflict",
        "valid_merkle_proof",
        "modified_merkle_proof",
        "valid_cas",
        "stale_latest_pointer",
        "skipped_version",
    ]
    case_labels = [case.replace("_", " ") for case in cases]
    matrix = np.full((len(cases), len(components)), np.nan)
    annotations = {}
    for _, row in summary.iterrows():
        row_index = cases.index(row["case"])
        column_index = components.index(row["component"])
        code = 1 if row["observed_result"] == "accepted" else -1
        matrix[row_index, column_index] = code
        annotations[(row_index, column_index)] = (
            f"{'A' if code == 1 else 'R'}\n"
            f"{int(row['matching_executions'])}/{int(row['total_executions'])}"
        )

    stem = "fig_04_component_conformance"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )
    _configure_figure_style(submission=submission)
    figure, (ax_a, ax_b) = plt.subplots(
        1,
        2,
        figsize=(7.0, 4.25),
        gridspec_kw={"width_ratios": [1.55, 1.0]},
        constrained_layout=True,
    )
    cmap = ListedColormap(["#E6B4C4", "#F0F2F4", "#B9D2E5"])
    cmap.set_bad("#F3F4F5")
    norm = BoundaryNorm([-1.5, -0.5, 0.5, 1.5], cmap.N)
    ax_a.imshow(np.ma.masked_invalid(matrix), cmap=cmap, norm=norm, aspect="auto")
    ax_a.set_xticks(range(len(components)), components, rotation=25, ha="right")
    ax_a.set_yticks(range(len(cases)), case_labels)
    ax_a.set_title("Predicate outcome by constructed case", loc="left")
    for (row_index, column_index), annotation in annotations.items():
        ax_a.text(
            column_index,
            row_index,
            annotation,
            ha="center",
            va="center",
            fontsize=6.7,
            color=INK,
            fontweight="semibold",
        )
    ax_a.set_xticks(np.arange(-0.5, len(components), 1), minor=True)
    ax_a.set_yticks(np.arange(-0.5, len(cases), 1), minor=True)
    ax_a.grid(which="minor", color="white", linewidth=1.2)
    ax_a.tick_params(which="minor", bottom=False, left=False)
    ax_a.legend(
        handles=[
            Patch(facecolor="#B9D2E5", edgecolor=INK, label="A: accepted"),
            Patch(facecolor="#E6B4C4", edgecolor=INK, label="R: rejected"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.20),
        ncol=2,
    )

    ordered = summary.set_index("case").loc[cases].reset_index()
    y = np.arange(len(ordered))
    ax_b.hlines(y, 0, ordered["proportion_matching"], color=LIGHT_GREY, linewidth=1.2)
    colors = [BLUE if value == "accepted" else PINK for value in ordered["expected_result"]]
    ax_b.scatter(
        ordered["proportion_matching"],
        y,
        c=colors,
        s=28,
        edgecolor=INK,
        linewidth=0.55,
        zorder=3,
    )
    for row_index, row in ordered.iterrows():
        ax_b.text(
            0.97,
            row_index,
            f"{int(row['matching_executions'])}/{int(row['total_executions'])}",
            ha="right",
            va="center",
            fontsize=6.8,
            color=INK,
        )
    ax_b.set_yticks(y, [""] * len(y))
    ax_b.set_ylim(len(y) - 0.5, -0.5)
    ax_b.set_xlim(0, 1.03)
    ax_b.set_xlabel("Proportion matching prespecified rule")
    ax_b.set_title("Repeated conformance", loc="left")
    ax_b.grid(True, axis="x")
    _panel_label(ax_a, "a")
    _panel_label(ax_b, "b")
    if not submission:
        figure.text(
            0.5, 0.5, "FIXTURE — NOT FOR SUBMISSION", ha="center", va="center",
            fontsize=18, color=PINK, alpha=0.25, rotation=25, fontweight="bold",
        )
    return publish_figure_bundle(
        figure,
        summary,
        output_paths,
        columns=(
            "component",
            "case",
            "expected_result",
            "observed_result",
            "matching_executions",
            "total_executions",
            "proportion_matching",
        ),
        sort_by=(
            "component",
            "case",
            "expected_result",
            "observed_result",
        ),
    )


def _panel_label(axis, label: str) -> None:
    axis.text(
        -0.16,
        1.08,
        label,
        transform=axis.transAxes,
        fontsize=10.2,
        fontweight="bold",
        va="top",
    )


def _throughput_summary(raw: pd.DataFrame) -> pd.DataFrame:
    accept = raw.loc[raw["stage"] == "signature_admission_batch"].copy()
    accept["records_s"] = accept["record_count"] / (
        accept["duration_ns"] / 1_000_000_000.0
    )
    rows = []
    for batch_size, group in accept.groupby("batch_size", sort=True):
        values = group["records_s"].to_numpy(dtype=float)
        rows.append(
            {
                "batch_size": int(batch_size),
                "n": int(values.size),
                "median_records_s": float(np.median(values)),
                "q1_records_s": float(np.quantile(values, 0.25)),
                "q3_records_s": float(np.quantile(values, 0.75)),
            }
        )
    return pd.DataFrame(rows)


@close_new_figures
def render_python_benchmark_figure(
    raw_path: str | Path,
    summary_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    submission: bool,
) -> dict[str, Path]:
    manifest = load_manifest(manifest_path)
    if submission:
        assert_submission_eligible([manifest])
    raw = read_source_csv(raw_path)
    summary = read_source_csv(summary_path)
    stem = "fig_03_python_benchmarks"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )

    _configure_figure_style(submission=submission)
    figure, axes = plt.subplots(2, 2, figsize=(7.0, 5.45), constrained_layout=True)
    ax_a, ax_b, ax_c, ax_d = axes.flat

    panel_a = summary.loc[summary["stage"].isin(STAGE_STYLE)].copy()
    for stage, (label, color, marker, linestyle) in STAGE_STYLE.items():
        data = panel_a.loc[panel_a["stage"] == stage].sort_values("batch_size")
        x = data["batch_size"].to_numpy(dtype=float)
        median = data["median_ms"].to_numpy(dtype=float)
        ax_a.plot(x, median, label=label, color=color, marker=marker, linestyle=linestyle)
        ax_a.fill_between(
            x,
            data["ci_low_ms"].to_numpy(dtype=float),
            data["ci_high_ms"].to_numpy(dtype=float),
            color=color,
            alpha=0.11,
            linewidth=0,
        )
    ax_a.set(xscale="log", yscale="log", xlabel="Records per window", ylabel="Median latency (ms)")
    ax_a.set_title("Batch-stage latency", loc="left")
    ax_a.grid(True, which="major")
    ax_a.legend(ncol=2, loc="upper left", handlelength=2.2, columnspacing=0.9)

    largest = int(raw["batch_size"].max())
    ecdf_stages = ["sign_batch", "verify_batch", "signature_admission_batch"]
    ecdf_parts = []
    for stage in ecdf_stages:
        label, color, _, linestyle = STAGE_STYLE[stage]
        values = np.sort(
            raw.loc[(raw["batch_size"] == largest) & (raw["stage"] == stage), "duration_ns"].to_numpy(dtype=float)
            / 1_000_000.0
        )
        probability = np.arange(1, values.size + 1) / values.size
        ax_b.plot(values, probability, label=label, color=color, linestyle=linestyle)
        ecdf_parts.append(
            pd.DataFrame(
                {
                    "panel": "b",
                    "dataset": "latency_ecdf",
                    "stage": stage,
                    "batch_size": largest,
                    "latency_ms": values,
                    "ecdf": probability,
                    "n": values.size,
                }
            )
        )
    ax_b.set(xlabel="Batch latency (ms)", ylabel="Cumulative probability")
    ax_b.set_ylim(0, 1.02)
    ax_b.set_title(f"Distribution at {largest:,} records", loc="left")
    ax_b.grid(True)
    ax_b.legend(loc="lower right")

    throughput = _throughput_summary(raw)
    x = throughput["batch_size"].to_numpy(dtype=float)
    median = throughput["median_records_s"].to_numpy(dtype=float)
    ax_c.plot(x, median, color=ORANGE, marker="o", label="Median")
    ax_c.fill_between(
        x,
        throughput["q1_records_s"].to_numpy(dtype=float),
        throughput["q3_records_s"].to_numpy(dtype=float),
        color=ORANGE,
        alpha=0.18,
        linewidth=0,
        label="IQR",
    )
    ax_c.set(
        xscale="log",
        xlabel="Records per window",
        ylabel=SIGNATURE_ADMISSION_YLABEL,
    )
    ax_c.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value/1000:.2f}k"))
    ax_c.set_title(SIGNATURE_ADMISSION_TITLE, loc="left")
    ax_c.grid(True)
    ax_c.legend(loc="best")

    modeled = model_ledger_bytes(sorted(raw["batch_size"].unique()))
    model_styles = {
        "raw records": (PINK, "D", ":"),
        "hash per record": (BLUE, "s", "--"),
        "TARMS anchor": (OLIVE, "o", "-"),
    }
    for strategy, (color, marker, linestyle) in model_styles.items():
        data = modeled.loc[modeled["strategy"] == strategy]
        ax_d.plot(
            data["batch_size"], data["bytes"], label=strategy,
            color=color, marker=marker, linestyle=linestyle,
        )
    ax_d.set(xscale="log", yscale="log", xlabel="Records per window", ylabel="Modeled ledger bytes")
    ax_d.set_title("Application-payload model", loc="left")
    ax_d.grid(True, which="major")
    ax_d.legend(loc="upper left")

    for label, axis in zip("abcd", axes.flat, strict=True):
        _panel_label(axis, label)
    if not submission:
        figure.text(
            0.5,
            0.5,
            "FIXTURE — NOT FOR SUBMISSION",
            ha="center",
            va="center",
            fontsize=18,
            color=PINK,
            alpha=0.25,
            rotation=25,
            fontweight="bold",
        )
    source_parts = [
        panel_a.assign(panel="a", dataset="stage_summary"),
        *ecdf_parts,
        throughput.assign(panel="c", dataset="signature_admission_throughput"),
        modeled.assign(panel="d", dataset="ledger_model"),
    ]
    source = pd.concat(source_parts, ignore_index=True, sort=False)
    return publish_figure_bundle(
        figure,
        source,
        output_paths,
        columns=_deterministic_source_columns(
            source,
            (
                "run_id",
                "batch_size",
                "stage",
                "record_count",
                "late_count",
                "provenance",
                "n",
                "median_ms",
                "q1_ms",
                "q3_ms",
                "p95_ms",
                "ci_low_ms",
                "ci_high_ms",
                "median_records_s",
                "panel",
                "dataset",
                "latency_ms",
                "ecdf",
                "q1_records_s",
                "q3_records_s",
                "strategy",
                "bytes",
                "assumption",
                "anchor_version",
            ),
        ),
        sort_by=("panel", "dataset", "stage", "batch_size", "latency_ms"),
    )


def _load_fabric_observations(
    root: str | Path, *, submission: bool
) -> tuple[pd.DataFrame, list]:
    manifest_paths = sorted(Path(root).glob("**/run_manifest.json"))
    if not manifest_paths:
        raise ValueError(f"no Fabric run manifests found under {root}")
    manifests = [load_manifest(path) for path in manifest_paths]
    if submission:
        assert_submission_eligible(manifests)
    parts = []
    for path, manifest in zip(manifest_paths, manifests, strict=True):
        jsonl_candidates = sorted(path.parent.glob("*.jsonl"))
        if len(jsonl_candidates) != 1:
            raise ValueError(
                f"Fabric run {manifest.run_id} requires exactly one JSONL observation file"
            )
        jsonl = jsonl_candidates[0]
        validate_fabric_jsonl(jsonl, submission=submission)
        frame = pd.read_json(jsonl, lines=True)
        frame["run_id"] = manifest.run_id
        frame["concurrency"] = int(manifest.environment.get("concurrency", 0))
        frame["duration_seconds"] = int(
            manifest.environment.get("duration_seconds", 0)
        )
        frame["record_count"] = int(manifest.environment.get("record_count", 64))
        frame["latency_ms"] = frame["duration_ns"] / 1_000_000.0
        frame["successful"] = frame["error_class"].fillna("").eq("") & frame[
            "commit_status"
        ].isin(["VALID", "EVALUATED"])
        parts.append(frame)
    return pd.concat(parts, ignore_index=True), manifests


@close_new_figures
def render_fabric_performance_figure(
    fabric_root: str | Path,
    output_dir: str | Path,
    *,
    submission: bool,
) -> dict[str, Path]:
    raw, manifests = _load_fabric_observations(fabric_root, submission=submission)
    stem = "fig_04_fabric_performance"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )

    anchor = raw.loc[
        (raw["workload"] == "anchor_submit")
        & raw["successful"]
        & (raw["operation"] == FABRIC_INSTALL_OPERATION)
    ].copy()
    query = raw.loc[(raw["workload"] == "query") & raw["successful"]].copy()
    concurrent = raw.loc[
        (raw["workload"] == "concurrency") & raw["successful"]
    ].copy()
    if anchor.empty or query.empty or concurrent.empty:
        raise ValueError(
            "Fabric figure requires anchor_submit, query, and concurrency workloads"
        )

    _configure_figure_style(submission=submission)
    figure, axes = plt.subplots(2, 2, figsize=(7.0, 5.45), constrained_layout=True)
    ax_a, ax_b, ax_c, ax_d = axes.flat

    sizes = sorted(anchor["record_count"].unique())
    anchor_groups = [
        anchor.loc[anchor["record_count"] == size, "latency_ms"].to_numpy()
        for size in sizes
    ]
    boxes = ax_a.boxplot(
        anchor_groups,
        tick_labels=[f"{int(size):,}" for size in sizes],
        patch_artist=True,
        showfliers=False,
        widths=0.62,
    )
    for box in boxes["boxes"]:
        box.set(facecolor="#DDE9F2", edgecolor=BLUE, linewidth=1.1)
    for median in boxes["medians"]:
        median.set(color=INK, linewidth=1.4)
    ax_a.set(xlabel="Records represented by anchor", ylabel="Commit latency (ms)")
    ax_a.set_title("Anchor commit latency", loc="left")
    ax_a.grid(True, axis="y")

    operations = [operation for operation in ("ReadLatest", "ReadAnchor") if operation in set(query["operation"])]
    query_groups = [
        query.loc[query["operation"] == operation, "latency_ms"].to_numpy()
        for operation in operations
    ]
    violins = ax_b.violinplot(query_groups, showmedians=True, showextrema=False)
    for index, body in enumerate(violins["bodies"]):
        body.set_facecolor([BLUE, ORANGE][index % 2])
        body.set_edgecolor(INK)
        body.set_alpha(0.32)
    violins["cmedians"].set_color(INK)
    ax_b.set_xticks(range(1, len(operations) + 1), operations)
    ax_b.set(ylabel="Evaluate latency (ms)")
    ax_b.set_title("Gateway query distribution", loc="left")
    ax_b.grid(True, axis="y")

    run_level = (
        concurrent.groupby(["run_id", "concurrency", "duration_seconds"], as_index=False)
        .size()
        .rename(columns={"size": "successful_operations"})
    )
    run_level["throughput_tx_s"] = (
        run_level["successful_operations"] / run_level["duration_seconds"]
    )
    throughput = (
        run_level.groupby("concurrency")["throughput_tx_s"]
        .agg(median="median", q1=lambda x: x.quantile(0.25), q3=lambda x: x.quantile(0.75))
        .reset_index()
    )
    ax_c.plot(
        throughput["concurrency"], throughput["median"],
        color=ORANGE, marker="o", label="Median",
    )
    ax_c.fill_between(
        throughput["concurrency"].to_numpy(dtype=float),
        throughput["q1"].to_numpy(dtype=float),
        throughput["q3"].to_numpy(dtype=float),
        color=ORANGE, alpha=0.18, linewidth=0, label="Across-run IQR",
    )
    ax_c.set(xlabel="Concurrent clients", ylabel="Committed transactions s$^{-1}$")
    ax_c.set_title("Concurrency scaling", loc="left")
    ax_c.grid(True)
    ax_c.legend(loc="best")

    tail_rows = []
    for concurrency, group in concurrent.groupby("concurrency", sort=True):
        values = group["latency_ms"].to_numpy(dtype=float)
        for quantile, label in ((0.50, "P50"), (0.95, "P95"), (0.99, "P99")):
            tail_rows.append(
                {
                    "concurrency": int(concurrency),
                    "quantile": label,
                    "latency_ms": float(np.quantile(values, quantile)),
                    "n": int(values.size),
                }
            )
    tails = pd.DataFrame(tail_rows)
    tail_styles = {
        "P50": (BLUE, "o", "-"),
        "P95": (ORANGE, "s", "--"),
        "P99": (PINK, "D", ":"),
    }
    for quantile, (color, marker, linestyle) in tail_styles.items():
        data = tails.loc[tails["quantile"] == quantile]
        ax_d.plot(
            data["concurrency"], data["latency_ms"],
            color=color, marker=marker, linestyle=linestyle, label=quantile,
        )
    ax_d.set(xlabel="Concurrent clients", ylabel="Commit latency (ms)")
    ax_d.set_title("Tail latency under load", loc="left")
    ax_d.grid(True)
    ax_d.legend(loc="best")

    for label, axis in zip("abcd", axes.flat, strict=True):
        _panel_label(axis, label)
    if not submission:
        figure.text(
            0.5, 0.5, "FIXTURE — NOT FOR SUBMISSION", ha="center", va="center",
            fontsize=18, color=PINK, alpha=0.25, rotation=25, fontweight="bold",
        )
    source = pd.concat(
        [
            anchor.assign(panel="a", dataset="anchor_latency"),
            query.assign(panel="b", dataset="query_latency"),
            run_level.assign(panel="c", dataset="run_throughput"),
            tails.assign(panel="d", dataset="tail_latency"),
        ],
        ignore_index=True,
        sort=False,
    )
    return publish_figure_bundle(
        figure,
        source,
        output_paths,
        columns=_deterministic_source_columns(
            source,
            (
                "panel",
                "dataset",
                "run_id",
                "workload",
                "operation",
                "record_count",
                "concurrency",
                "duration_seconds",
                "latency_ms",
                "successful",
                "quantile",
            ),
        ),
        sort_by=(
            "panel",
            "dataset",
            "run_id",
            "workload",
            "operation",
            "record_count",
            "concurrency",
            "latency_ms",
        ),
    )


_AAMOS_PROBABILITY_METRICS = frozenset(
    {
        "attack_rejection",
        "control_rejection",
        "clean_false_rejection",
        "expected_stage_agreement",
        "coverage",
        "abstention",
        "covered_agreement",
        "upward_discordance",
        "priority_loss_discordance",
    }
)
_AAMOS_SIGNED_DIFFERENCE_METRICS = frozenset(
    {"pipeline_risk_difference"}
)
_AAMOS_BOOTSTRAP_METHOD = "crossed_seed_participant_multinomial"
_AAMOS_BOOTSTRAP_INTERVAL = "percentile_95"
_AAMOS_PAGE_SIZE_INCHES = (7.2, 6.25)
_AAMOS_METRIC_FIELD_CONTRACT = {
    "attack_rejection": (
        "attack",
        "attack_target",
        "attacked simulation evaluations",
    ),
    "control_rejection": (
        "boundary_control",
        "boundary_control",
        "boundary-control evaluations",
    ),
    "clean_false_rejection": (
        "clean_control",
        "clean_control",
        "clean simulation evaluations",
    ),
    "expected_stage_agreement": (
        "attack",
        "attack_target",
        "stage-applicable attacked simulation evaluations",
    ),
    "pipeline_risk_difference": (
        "attack",
        "paired_attack_pipelines",
        "paired attacked evaluations",
    ),
    "coverage": (
        "attack",
        "mixed_eligible_population",
        "eligible mixed simulation evaluations",
    ),
    "abstention": (
        "attack",
        "mixed_eligible_population",
        "eligible mixed simulation evaluations",
    ),
    "covered_agreement": (
        "attack",
        "mixed_eligible_population",
        "covered mixed simulation evaluations",
    ),
    "upward_discordance": (
        "attack",
        "mixed_eligible_population",
        "eligible mixed simulation evaluations",
    ),
    "priority_loss_discordance": (
        "attack",
        "mixed_eligible_population",
        "eligible mixed simulation evaluations",
    ),
}


def _required_manifest_text(
    mapping: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"AAMOS manifest lacks required {label}")
    return value


def _required_manifest_integer(
    value: object,
    *,
    label: str,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or float(value) % 1 != 0
    ):
        raise ValueError(
            f"AAMOS manifest {label} must be a finite integer"
        )
    integer = int(value)
    if integer < minimum:
        raise ValueError(
            f"AAMOS manifest {label} must be at least {minimum}"
        )
    return integer


def _required_manifest_integer_sequence(
    mapping: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> tuple[int, ...]:
    values = mapping.get(field)
    if not isinstance(values, list):
        raise ValueError(f"AAMOS manifest lacks required {label}")
    return tuple(
        _required_manifest_integer(
            value,
            label=f"{label} item",
        )
        for value in values
    )


def _source_required_exact(
    source: pd.DataFrame,
    column: str,
    expected: object,
    *,
    label: str,
) -> None:
    if column not in source or source[column].isna().any():
        raise ValueError(f"AAMOS source lacks required {label}")
    if not source[column].eq(expected).all():
        raise ValueError(f"AAMOS source has inconsistent {label}")


def _source_required_integer(
    source: pd.DataFrame,
    column: str,
    *,
    label: str,
    minimum: int = 0,
    expected: int | None = None,
) -> pd.Series:
    if column not in source or source[column].isna().any():
        raise ValueError(f"AAMOS source lacks required {label}")
    numeric = pd.to_numeric(source[column], errors="raise")
    if (
        not np.isfinite(numeric).all()
        or (numeric % 1 != 0).any()
        or (numeric < minimum).any()
    ):
        raise ValueError(
            f"AAMOS source {label} must contain finite integers"
        )
    if expected is not None and not numeric.eq(expected).all():
        raise ValueError(f"AAMOS source has inconsistent {label}")
    return numeric.astype("int64")


def _validate_aamos_derivation_and_dataset(
    payload: Mapping[str, object],
    design: Mapping[str, object],
    source: pd.DataFrame,
) -> None:
    derivation = payload.get("derivation")
    if not isinstance(derivation, Mapping):
        raise ValueError(
            "AAMOS submission manifest lacks derivation metadata"
        )
    expected_config = {
        "derivation_config_basename": (
            FIXED_DERIVATION_CONFIG_BASENAME
        ),
        "derivation_config_file_sha256": (
            FIXED_DERIVATION_CONFIG_FILE_SHA256
        ),
        "derivation_config_canonical_sha256": (
            FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
        ),
    }
    if (
        design.get("fixed_submission_config") is not True
        or derivation.get("fixed_submission_config") is not True
    ):
        raise ValueError(
            "AAMOS submission requires the fixed derivation config"
        )
    controlled = payload.get("controlled_source")
    if not isinstance(controlled, Mapping):
        raise ValueError(
            "AAMOS submission manifest lacks controlled-source metadata"
        )
    controlled_identity = _required_manifest_text(
        controlled,
        "identity_sha256",
        label="controlled-source identity",
    )
    controlled_snapshot = _required_manifest_text(
        controlled,
        "snapshot_sha256",
        label="controlled-source snapshot hash",
    )
    if (
        len(controlled_identity) != 64
        or len(controlled_snapshot) != 64
        or any(
            character not in "0123456789abcdef"
            for character in controlled_identity + controlled_snapshot
        )
    ):
        raise ValueError(
            "AAMOS controlled-source hashes must be lowercase SHA-256"
        )
    member_count = controlled.get("member_count")
    if (
        isinstance(member_count, bool)
        or not isinstance(member_count, int)
        or member_count <= 0
    ):
        raise ValueError(
            "AAMOS manifest controlled-source member count "
            "must be a positive integer"
        )
    fixed_member = f"config/{FIXED_DERIVATION_CONFIG_BASENAME}"
    controlled_member = _required_manifest_text(
        controlled,
        "derivation_config_member",
        label="controlled derivation config member",
    )
    design_member = _required_manifest_text(
        design,
        "derivation_config_member",
        label="design derivation config member",
    )
    derivation_member = _required_manifest_text(
        derivation,
        "derivation_config_member",
        label="derivation config member",
    )
    if (
        controlled_member != fixed_member
        or design_member != fixed_member
        or derivation_member != fixed_member
    ):
        raise ValueError(
            "AAMOS manifest derivation config members differ"
        )
    if design.get("code_archive_sha256") != controlled_identity:
        raise ValueError(
            "AAMOS code archive identity does not match controlled source"
        )
    if (
        derivation.get("config_canonical_sha256")
        != FIXED_DERIVATION_CONFIG_CANONICAL_SHA256
    ):
        raise ValueError(
            "AAMOS manifest legacy derivation config identity "
            "does not match"
        )
    for field, expected in expected_config.items():
        if (
            design.get(field) != expected
            or derivation.get(field) != expected
        ):
            raise ValueError(
                "AAMOS submission fixed derivation config identity "
                "does not match"
            )
        _source_required_exact(
            source,
            field,
            expected,
            label="derivation config identity",
        )

    dataset = payload.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ValueError("AAMOS manifest lacks the dataset contract")
    expected_inventory = [
        {"name": name, "sha256": digest}
        for name, digest in sorted(
            OFFICIAL_AAMOS_RELEASE[
                "selected_analysis_source_sha256"
            ].items()
        )
    ]
    fixed_inventory_hash = source_inventory_sha256(
        expected_inventory
    )
    source_files = dataset.get("source_files")
    if not isinstance(source_files, list):
        raise ValueError("AAMOS manifest lacks source inventory")
    observed_inventory_hash = source_inventory_sha256(source_files)
    if (
        dataset.get("name") != "AAMOS-00"
        or dataset.get("doi") != str(OFFICIAL_AAMOS_RELEASE["doi"])
        or observed_inventory_hash != fixed_inventory_hash
        or dataset.get("source_inventory_sha256")
        != fixed_inventory_hash
    ):
        raise ValueError(
            "AAMOS submission dataset contract does not match "
            "the fixed official release"
        )
    source_dataset = {
        "dataset_name": "AAMOS-00",
        "dataset_doi": str(OFFICIAL_AAMOS_RELEASE["doi"]),
        "dataset_source_inventory_sha256": fixed_inventory_hash,
    }
    for field, expected in source_dataset.items():
        _source_required_exact(
            source,
            field,
            expected,
            label="dataset contract",
        )


def _validate_aamos_bootstrap_design(
    design: Mapping[str, object],
    environment: Mapping[str, object],
    source: pd.DataFrame,
) -> tuple[int, int]:
    bootstrap = design.get("bootstrap")
    if not isinstance(bootstrap, Mapping):
        raise ValueError(
            "AAMOS submission manifest lacks design bootstrap metadata"
        )
    method = _required_manifest_text(
        bootstrap,
        "method",
        label="bootstrap method",
    )
    interval = _required_manifest_text(
        bootstrap,
        "interval_type",
        label="bootstrap interval type",
    )
    repetitions = _required_manifest_integer(
        bootstrap.get("repetitions"),
        label="design bootstrap repetitions",
        minimum=2_000,
    )
    master_seed = _required_manifest_integer(
        bootstrap.get("master_seed"),
        label="bootstrap master seed",
    )
    if (
        method != _AAMOS_BOOTSTRAP_METHOD
        or interval != _AAMOS_BOOTSTRAP_INTERVAL
    ):
        raise ValueError(
            "AAMOS design bootstrap does not match the fixed contract"
        )
    environment_repetitions = _required_manifest_integer(
        environment.get("bootstrap_repetitions"),
        label="environment bootstrap repetitions",
        minimum=2_000,
    )
    environment_seed = _required_manifest_integer(
        environment.get("bootstrap_master_seed"),
        label="environment bootstrap master seed",
    )
    if (
        environment_repetitions != repetitions
        or environment_seed != master_seed
    ):
        raise ValueError(
            "AAMOS environment and design bootstrap metadata differ"
        )
    design_seeds = _required_manifest_integer_sequence(
        design,
        "seeds",
        label="design injection seeds",
    )
    environment_seeds = _required_manifest_integer_sequence(
        environment,
        "injection_seeds",
        label="environment injection seeds",
    )
    if (
        design_seeds != FIXED_SEEDS
        or environment_seeds != FIXED_SEEDS
    ):
        raise ValueError(
            "AAMOS manifest does not use the fixed 20-seed design"
        )
    _source_required_exact(
        source,
        "bootstrap_method",
        method,
        label="bootstrap method",
    )
    _source_required_exact(
        source,
        "bootstrap_interval_type",
        interval,
        label="bootstrap interval type",
    )
    _source_required_integer(
        source,
        "bootstrap_repetitions_requested",
        label="bootstrap repetitions requested",
        minimum=2_000,
        expected=repetitions,
    )
    _source_required_integer(
        source,
        "bootstrap_master_seed",
        label="bootstrap master seed",
        expected=master_seed,
    )
    _source_required_integer(
        source,
        "seed_count",
        label="seed count",
        expected=len(FIXED_SEEDS),
    )
    _source_required_exact(
        source,
        "seed_scope",
        "pooled_fixed_seed_set",
        label="seed scope",
    )
    _source_required_exact(
        source,
        "execution_count_scope",
        "pooled_fixed_seed_set",
        label="execution count scope",
    )
    return repetitions, master_seed


def _validate_aamos_metric_required_fields(
    source: pd.DataFrame,
) -> None:
    required = {
        "metric_id",
        "scenario",
        "scenario_class",
        "evaluation_arm",
        "denominator_unit",
        "comparison_type",
        "comparator_pipeline",
        "both_reject_n",
        "attack_only_reject_n",
        "clean_only_reject_n",
        "neither_reject_n",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(
            "AAMOS source lacks metric-specific fields: "
            + ", ".join(missing)
        )
    if source["metric_id"].isna().any():
        raise ValueError(
            "AAMOS source lacks required metric identity"
        )
    observed_metrics = set(source["metric_id"].astype(str))
    if observed_metrics != set(_AAMOS_METRIC_FIELD_CONTRACT):
        raise ValueError(
            "AAMOS source required panels or metric set do not match "
            "the fixed submission contract"
        )
    for metric, (
        scenario_class,
        evaluation_arm,
        denominator_unit,
    ) in _AAMOS_METRIC_FIELD_CONTRACT.items():
        selection = source["metric_id"].eq(metric)
        for field, expected in (
            ("scenario_class", scenario_class),
            ("evaluation_arm", evaluation_arm),
            ("denominator_unit", denominator_unit),
        ):
            values = source.loc[selection, field]
            if values.isna().any() or not values.eq(expected).all():
                raise ValueError(
                    "AAMOS source metric-specific "
                    f"{field} contract is invalid"
                )

    clean = source["metric_id"].eq("clean_false_rejection")
    if (
        source.loc[clean, "scenario"].notna().any()
        or source.loc[~clean, "scenario"].isna().any()
    ):
        raise ValueError(
            "AAMOS source metric-specific scenario contract is invalid"
        )

    mechanism = source["metric_id"].eq(
        "pipeline_risk_difference"
    )
    if (
        source.loc[mechanism, "comparison_type"].isna().any()
        or not source.loc[
            mechanism, "comparison_type"
        ].eq("matched_pipeline").all()
        or source.loc[
            mechanism, "comparator_pipeline"
        ].isna().any()
    ):
        raise ValueError(
            "AAMOS panel b mechanism comparison contract is invalid"
        )
    for row in source.loc[mechanism].itertuples():
        stage = REJECT_SCENARIOS.get(str(row.scenario))
        expected_comparator = (
            "all_minus_freshness"
            if stage == "history"
            else f"all_minus_{stage}"
        )
        if str(row.comparator_pipeline) != expected_comparator:
            raise ValueError(
                "AAMOS panel b mechanism comparator is invalid"
            )
    non_mechanism = ~mechanism
    if (
        source.loc[
            non_mechanism, "comparison_type"
        ].notna().any()
        or source.loc[
            non_mechanism, "comparator_pipeline"
        ].notna().any()
    ):
        raise ValueError(
            "AAMOS source has unexpected comparison metadata"
        )


def _aamos_mark_key(row: object) -> tuple[object, ...]:
    panel = getattr(row, "panel_id")
    metric = getattr(row, "metric_id")
    scenario = getattr(row, "scenario")
    rate = getattr(row, "rate_requested")
    pipeline = getattr(row, "pipeline")
    comparator = getattr(row, "comparator_pipeline")
    for name, value in (
        ("panel", panel),
        ("metric", metric),
        ("rate", rate),
        ("pipeline", pipeline),
    ):
        if pd.isna(value) or (
            isinstance(value, str) and not value
        ):
            raise ValueError(
                f"AAMOS source mark has missing {name}"
            )
    metric = str(metric)
    if metric == "clean_false_rejection":
        if not pd.isna(scenario):
            raise ValueError(
                "AAMOS clean-control mark has a scenario"
            )
        scenario_key = ""
    else:
        if pd.isna(scenario) or not str(scenario):
            raise ValueError(
                "AAMOS source mark has missing scenario"
            )
        scenario_key = str(scenario)
    if metric == "pipeline_risk_difference":
        if pd.isna(comparator) or not str(comparator):
            raise ValueError(
                "AAMOS source mark has missing comparator"
            )
        comparator_key = str(comparator)
    else:
        if not pd.isna(comparator):
            raise ValueError(
                "AAMOS non-comparison mark has a comparator"
            )
        comparator_key = ""
    return (
        str(panel),
        metric,
        scenario_key,
        _rate_key(rate),
        str(pipeline),
        comparator_key,
    )


def _rate_key(value: object) -> float:
    numeric = float(value)
    if not np.isfinite(numeric):
        raise ValueError("AAMOS source mark rate must be finite")
    return round(numeric, 12)


def _aamos_expected_submission_marks() -> set[tuple[object, ...]]:
    expected: set[tuple[object, ...]] = set()
    pipelines = tuple(PIPELINES)
    primary_rate = _rate_key(0.10)
    for scenario in REJECT_SCENARIOS:
        for pipeline in pipelines:
            expected.add(
                (
                    "a",
                    "attack_rejection",
                    scenario,
                    primary_rate,
                    pipeline,
                    "",
                )
            )
    for scenario in BOUNDARY_SCENARIOS:
        expected.add(
            (
                "a",
                "control_rejection",
                scenario,
                primary_rate,
                "all_checks",
                "",
            )
        )
    for pipeline in pipelines:
        expected.add(
            (
                "a",
                "clean_false_rejection",
                "",
                0.0,
                pipeline,
                "",
            )
        )
    for scenario, stage in REJECT_SCENARIOS.items():
        comparator = (
            "all_minus_freshness"
            if stage == "history"
            else f"all_minus_{stage}"
        )
        expected.add(
            (
                "b",
                "expected_stage_agreement",
                scenario,
                primary_rate,
                "all_checks",
                "",
            )
        )
        expected.add(
            (
                "b",
                "pipeline_risk_difference",
                scenario,
                primary_rate,
                "all_checks",
                comparator,
            )
        )
    for rate in ATTACK_RATES:
        for metric in ("coverage", "abstention"):
            expected.add(
                (
                    "c",
                    metric,
                    "mixed_attack",
                    _rate_key(rate),
                    "all_checks",
                    "",
                )
            )
        for metric in (
            "covered_agreement",
            "upward_discordance",
            "priority_loss_discordance",
        ):
            expected.add(
                (
                    "d",
                    metric,
                    "mixed_attack",
                    _rate_key(rate),
                    "all_checks",
                    "",
                )
            )
    return expected


class _AamosRenderTracker:
    """Collect the source-row keys actually consumed by plotting operations."""

    def __init__(self) -> None:
        self.rendered_keys: set[tuple[object, ...]] = set()

    def register_row(self, row: object) -> None:
        key = _aamos_mark_key(row)
        if key in self.rendered_keys:
            raise ValueError("AAMOS render registered a mark more than once")
        self.rendered_keys.add(key)

    def require_complete(
        self,
        source: pd.DataFrame,
        *,
        submission: bool,
    ) -> None:
        source_keys = {
            _aamos_mark_key(row) for row in source.itertuples(index=False)
        }
        expected_keys = _aamos_expected_submission_marks()
        if self.rendered_keys != source_keys or (
            submission and source_keys != expected_keys
        ):
            missing = sorted(source_keys - self.rendered_keys)
            unexpected = sorted(self.rendered_keys - source_keys)
            raise ValueError(
                "rendered AAMOS mark keys do not exactly match source and "
                f"submission contract: rendered={len(self.rendered_keys)}, "
                f"source={len(source_keys)}, expected={len(expected_keys)}, "
                f"missing={missing[:1]}, unexpected={unexpected[:1]}"
            )


def _validate_aamos_mark_contract(
    design: Mapping[str, object],
    source: pd.DataFrame,
) -> None:
    observed_rates = tuple(float(value) for value in design.get("rates", []))
    if observed_rates != (0.0, *ATTACK_RATES):
        raise ValueError(
            "AAMOS manifest does not use the fixed submission design"
        )
    if tuple(design.get("attack_scenarios", [])) != tuple(
        REJECT_SCENARIOS
    ):
        raise ValueError(
            "AAMOS manifest does not use the fixed submission design"
        )
    if tuple(design.get("boundary_scenarios", [])) != tuple(
        BOUNDARY_SCENARIOS
    ):
        raise ValueError(
            "AAMOS manifest does not use the fixed submission design"
        )
    observed_pipelines = design.get("pipelines")
    if not isinstance(observed_pipelines, Mapping) or {
        str(name): tuple(checks)
        for name, checks in observed_pipelines.items()
    } != PIPELINES:
        raise ValueError(
            "AAMOS manifest does not use the fixed submission design"
        )
    panels = set(source["panel_id"].dropna().astype(str))
    if panels != {"a", "b", "c", "d"}:
        raise ValueError(
            "AAMOS source does not contain all required panels a-d"
        )
    mark_columns = {
        "panel_id",
        "metric_id",
        "scenario",
        "rate_requested",
        "pipeline",
        "comparator_pipeline",
    }
    missing = sorted(mark_columns - set(source.columns))
    if missing:
        raise ValueError(
            "AAMOS source lacks display mark columns: "
            + ", ".join(missing)
        )
    actual_marks = [_aamos_mark_key(row) for row in source.itertuples()]
    if len(actual_marks) != len(set(actual_marks)):
        raise ValueError(
            "AAMOS source contains duplicate display marks"
        )
    expected_marks = _aamos_expected_submission_marks()
    if set(actual_marks) != expected_marks:
        raise ValueError(
            "AAMOS source mark cardinality does not match "
            "the fixed submission design"
        )
    mechanism = source["metric_id"].eq("pipeline_risk_difference")
    if (
        "comparison_type" not in source
        or source.loc[
            mechanism, "comparison_type"
        ].isna().any()
        or not source.loc[
            mechanism, "comparison_type"
        ].eq("matched_pipeline").all()
    ):
        raise ValueError(
            "AAMOS panel b mechanism comparison contract is invalid"
        )
    covered = source["metric_id"].eq("covered_agreement")
    directional = source["metric_id"].isin(
        {"upward_discordance", "priority_loss_discordance"}
    )
    if (
        source.loc[covered, "denominator_unit"].isna().any()
        or not source.loc[
            covered, "denominator_unit"
        ].eq("covered mixed simulation evaluations").all()
        or source.loc[
            directional, "denominator_unit"
        ].isna().any()
        or not source.loc[
            directional, "denominator_unit"
        ].eq("eligible mixed simulation evaluations").all()
    ):
        raise ValueError(
            "AAMOS panel d facet denominator contract is invalid"
        )


def _validate_aamos_metric_domains(
    source: pd.DataFrame,
    *,
    numerator: pd.Series,
    denominator: pd.Series,
    estimate: pd.Series,
    ci_low: pd.Series,
    ci_high: pd.Series,
) -> None:
    if (
        not np.isfinite(numerator).all()
        or not np.isfinite(denominator).all()
        or (numerator % 1 != 0).any()
        or (denominator % 1 != 0).any()
    ):
        raise ValueError(
            "AAMOS source counts must be finite integers"
        )
    zero = denominator == 0
    if (
        (numerator.loc[zero] != 0).any()
        or estimate.loc[zero].notna().any()
        or ci_low.loc[zero].notna().any()
        or ci_high.loc[zero].notna().any()
    ):
        raise ValueError(
            "AAMOS zero denominator requires zero numerator "
            "and missing estimate/CI"
        )
    probability = source["metric_id"].isin(
        _AAMOS_PROBABILITY_METRICS
    )
    if (
        (numerator.loc[probability] < 0).any()
        or (
            numerator.loc[probability]
            > denominator.loc[probability]
        ).any()
        or (
            estimate.loc[probability & ~zero].lt(0)
            | estimate.loc[probability & ~zero].gt(1)
        ).any()
        or (
            ci_low.loc[probability & ~zero].dropna().lt(0).any()
        )
        or (
            ci_high.loc[probability & ~zero].dropna().gt(1).any()
        )
    ):
        raise ValueError(
            "AAMOS probability metric domain must remain within [0,1]"
        )
    signed = source["metric_id"].isin(
        _AAMOS_SIGNED_DIFFERENCE_METRICS
    )
    if (
        (numerator.loc[signed].abs() > denominator.loc[signed]).any()
        or (
            estimate.loc[signed & ~zero].lt(-1)
            | estimate.loc[signed & ~zero].gt(1)
        ).any()
        or ci_low.loc[signed & ~zero].dropna().lt(-1).any()
        or ci_high.loc[signed & ~zero].dropna().gt(1).any()
    ):
        raise ValueError(
            "AAMOS signed difference domain must remain within [-1,1]"
        )
    if signed.any():
        cell_columns = (
            "both_reject_n",
            "attack_only_reject_n",
            "clean_only_reject_n",
            "neither_reject_n",
        )
        if not set(cell_columns).issubset(source.columns):
            raise ValueError(
                "AAMOS signed difference lacks four-cell counts"
            )
        cells = source.loc[signed, cell_columns].apply(
            pd.to_numeric, errors="raise"
        )
        if (
            cells.isna().any().any()
            or (cells < 0).any().any()
            or (cells % 1 != 0).any().any()
            or not np.allclose(
                cells.sum(axis=1),
                denominator.loc[signed],
                rtol=0,
                atol=0,
            )
            or not np.allclose(
                (
                    cells["attack_only_reject_n"]
                    - cells["clean_only_reject_n"]
                ),
                numerator.loc[signed],
                rtol=0,
                atol=0,
            )
        ):
            raise ValueError(
                "AAMOS signed difference four-cell counts "
                "do not reconcile"
            )


def _validate_aamos_submission_source(
    source_data_path: str | Path,
    manifest_path: str | Path,
    source: pd.DataFrame,
) -> None:
    """Bind every submission mark to its exact artifact and run design."""

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    payload = json.loads(
        Path(manifest_path).read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("AAMOS manifest root must be an object")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise ValueError(
            "AAMOS submission manifest requires a non-empty artifact map"
        )
    source_path = Path(source_data_path)
    artifact_name = source_path.name
    if artifact_name not in artifacts:
        raise ValueError(
            "AAMOS source artifact basename is absent from the manifest"
        )
    observed_hash = sha256_file(source_path)
    if str(artifacts[artifact_name]) != observed_hash:
        raise ValueError(
            "AAMOS source artifact SHA-256 does not match the manifest"
        )
    if source.empty:
        raise ValueError("AAMOS submission source data cannot be empty")

    run_id = _required_manifest_text(
        payload,
        "run_id",
        label="run ID",
    )
    _source_required_exact(
        source,
        "run_id",
        run_id,
        label="run_id",
    )
    provenance = _required_manifest_text(
        payload,
        "provenance",
        label="provenance",
    )
    if provenance != "public_secondary":
        raise ValueError(
            "AAMOS submission manifest provenance is invalid"
        )
    _source_required_exact(
        source,
        "provenance",
        provenance,
        label="provenance",
    )
    design = payload.get("design")
    if not isinstance(design, Mapping):
        raise ValueError("AAMOS submission manifest lacks design metadata")
    code_hash = _required_manifest_text(
        design,
        "code_archive_sha256",
        label="code archive hash",
    )
    _source_required_exact(
        source,
        "code_commit_or_archive_hash",
        code_hash,
        label="code archive hash",
    )
    metric_version = _required_manifest_text(
        design,
        "metric_definition_version",
        label="metric definition",
    )
    if metric_version != METRIC_DEFINITION_VERSION:
        raise ValueError(
            "AAMOS submission manifest metric definition "
            "does not match the fixed design"
        )
    _source_required_exact(
        source,
        "metric_definition_version",
        metric_version,
        label="metric definition",
    )

    environment = payload.get("environment")
    if not isinstance(environment, Mapping):
        raise ValueError(
            "AAMOS submission manifest lacks environment metadata"
        )
    if environment.get("profile") != "submission":
        raise ValueError(
            "AAMOS submission manifest profile is not submission"
        )
    _validate_aamos_derivation_and_dataset(
        payload, design, source
    )
    manifest_repetitions, _ = _validate_aamos_bootstrap_design(
        design, environment, source
    )
    requested = _source_required_integer(
        source,
        "bootstrap_repetitions_requested",
        label="bootstrap repetitions requested",
        minimum=2_000,
        expected=manifest_repetitions,
    )
    valid = _source_required_integer(
        source,
        "bootstrap_repetitions_valid",
        label="bootstrap repetitions valid",
    )
    discarded = _source_required_integer(
        source,
        "bootstrap_repetitions_discarded",
        label="bootstrap repetitions discarded",
    )
    if not (requested == valid + discarded).all():
        raise ValueError(
            "AAMOS bootstrap valid and discarded counts do not reconcile"
        )
    _validate_aamos_metric_required_fields(source)

    numerator = pd.to_numeric(source["numerator_n"], errors="raise")
    denominator = pd.to_numeric(
        source["denominator_N"], errors="raise"
    )
    estimate = pd.to_numeric(source["estimate"], errors="coerce")
    if (denominator < 0).any():
        raise ValueError("AAMOS figure denominator cannot be negative")
    positive = denominator > 0
    expected = numerator.loc[positive] / denominator.loc[positive]
    if not np.allclose(
        estimate.loc[positive],
        expected,
        rtol=0,
        atol=1e-12,
        equal_nan=False,
    ):
        raise ValueError(
            "AAMOS source estimate does not reconcile with "
            "numerator/denominator"
        )
    if estimate.loc[~positive].notna().any():
        raise ValueError(
            "AAMOS zero-denominator estimate must be missing"
        )
    ci_low = pd.to_numeric(source["ci_low"], errors="coerce")
    ci_high = pd.to_numeric(source["ci_high"], errors="coerce")
    has_valid = valid > 0
    if (
        not bool(np.isfinite(ci_low.loc[has_valid]).all())
        or not bool(np.isfinite(ci_high.loc[has_valid]).all())
    ):
        raise ValueError(
            "AAMOS source CI must be finite when bootstrap replicates are valid"
        )
    if (ci_low.loc[has_valid] > ci_high.loc[has_valid]).any():
        raise ValueError("AAMOS source CI bounds are reversed")
    if (
        ci_low.loc[~has_valid].notna().any()
        or ci_high.loc[~has_valid].notna().any()
    ):
        raise ValueError(
            "AAMOS source CI must be missing with zero valid replicates"
        )
    _validate_aamos_metric_domains(
        source,
        numerator=numerator,
        denominator=denominator,
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
    )
    _validate_aamos_mark_contract(design, source)


def _aamos_panel_a_groups(
    panel_a: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Keep security attacks and capability controls in separate estimands."""

    return {
        "attacks": panel_a.loc[
            panel_a["metric_id"] == "attack_rejection"
        ].copy(),
        "controls": panel_a.loc[
            panel_a["metric_id"] == "control_rejection"
        ].copy(),
        "clean": panel_a.loc[
            panel_a["metric_id"] == "clean_false_rejection"
        ].copy(),
    }


def _aamos_panel_a_control_pipelines(
    controls: pd.DataFrame,
) -> list[str]:
    """Display boundary controls only for the all-checks configuration."""

    return (
        ["all_checks"]
        if "all_checks" in set(controls["pipeline"])
        else []
    )


def _aamos_panel_a_control_labels(
    scenarios: list[str],
) -> list[str]:
    """Keep the boundary-control column legible beside the attack matrix."""

    labels = {
        "canonical_reorder": "Reorder",
        "clinical_measurement_error": "Measurement error",
        "idempotent_retransmission": "Idempotent retransmission",
        "incorrect_priority_rule": "Symptom-count rule error",
        "legitimate_late_arrival": "Late arrival",
        "permanent_omission": "Permanent omission",
        "pre_signing_false_payload": "False payload",
    }
    return [
        labels.get(value, value.replace("_", " "))
        for value in scenarios
    ]


def _aamos_pipeline_display_labels(
    pipelines: list[str],
) -> list[str]:
    """Use compact reader-facing names for experimental configurations."""

    labels = {
        "unverified": "unverified",
        "signature_only": "signature only",
        "signature_admission": "signature + admission",
        "signature_binding_admission": (
            "signature + binding + admission"
        ),
        "all_checks": "all checks",
        "all_minus_signature": "all checks − signature",
        "all_minus_device": "all checks − device",
        "all_minus_binding": "all checks − binding",
        "all_minus_admission": "all checks − admission",
        "all_minus_merkle": "all checks − Merkle",
        "all_minus_freshness": "all checks − freshness",
        "all_minus_authorization": "all checks − authorization",
    }
    return [
        labels.get(value, value.replace("_", " "))
        for value in pipelines
    ]


def _aamos_panel_b_layout(
    panel_b: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Align stage agreement and mechanism RD on one scenario index."""

    stage = panel_b.loc[
        panel_b["metric_id"] == "expected_stage_agreement"
    ]
    mechanism = panel_b.loc[
        panel_b["metric_id"] == "pipeline_risk_difference"
    ]
    scenarios = sorted(
        set(stage["scenario"].dropna().astype(str))
        | set(mechanism["scenario"].dropna().astype(str))
    )

    def aligned(frame: pd.DataFrame) -> pd.DataFrame:
        if frame["scenario"].duplicated().any():
            raise ValueError(
                "AAMOS panel b requires one mark per scenario and metric"
            )
        indexed = frame.assign(
            scenario=frame["scenario"].astype(str)
        ).set_index("scenario")
        result = indexed.reindex(scenarios).reset_index()
        result["plot_y"] = np.arange(len(scenarios), dtype=float)
        return result

    return {
        "stage": aligned(stage),
        "matched_pipeline": aligned(mechanism),
    }


def _aamos_rate_interval_marks(data: pd.DataFrame) -> pd.DataFrame:
    """Return rate, estimate, and asymmetric CI lengths for line marks."""

    marks = data.sort_values("rate_requested").copy()
    marks["x_percent"] = (
        pd.to_numeric(marks["rate_requested"], errors="raise") * 100
    )
    marks["estimate"] = pd.to_numeric(
        marks["estimate"], errors="coerce"
    )
    marks["ci_low"] = pd.to_numeric(
        marks["ci_low"], errors="coerce"
    )
    marks["ci_high"] = pd.to_numeric(
        marks["ci_high"], errors="coerce"
    )
    marks["xerr_low"] = marks["estimate"] - marks["ci_low"]
    marks["xerr_high"] = marks["ci_high"] - marks["estimate"]
    return marks


def _aamos_panel_d_facets(
    panel_d: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Split conditional covered outputs from all-eligible-day outcomes."""

    return {
        "conditional": panel_d.loc[
            panel_d["metric_id"] == "covered_agreement"
        ].copy(),
        "all_day": panel_d.loc[
            panel_d["metric_id"].isin(
                {
                    "upward_discordance",
                    "priority_loss_discordance",
                }
            )
        ].copy(),
    }


def _aamos_panel_d_axis_labels() -> tuple[str, str]:
    """Use compact labels that remain distinct in the stacked inset axes."""

    return "Covered outputs", "All eligible days"


def _plot_aamos_rate_interval(
    axis,
    data: pd.DataFrame,
    *,
    label: str,
    color: str,
    marker: str,
    linestyle: str,
    tracker: _AamosRenderTracker | None = None,
) -> None:
    marks = _aamos_rate_interval_marks(data)
    if tracker is not None:
        for row in marks.itertuples(index=False):
            tracker.register_row(row)
    valid = (
        np.isfinite(marks["estimate"])
        & np.isfinite(marks["ci_low"])
        & np.isfinite(marks["ci_high"])
    )
    axis.plot(
        marks.loc[valid, "x_percent"],
        marks.loc[valid, "estimate"],
        label=label,
        color=color,
        marker=marker,
        linestyle=linestyle,
    )
    axis.vlines(
        marks.loc[valid, "x_percent"],
        marks.loc[valid, "ci_low"],
        marks.loc[valid, "ci_high"],
        color=color,
        linewidth=0.8,
        alpha=0.8,
    )


def _create_aamos_figure_layout():
    """Create collision-resistant axes for the four-panel AAMOS figure."""

    figure = plt.figure(
        figsize=_AAMOS_PAGE_SIZE_INCHES,
        constrained_layout=False,
    )
    outer = figure.add_gridspec(
        2,
        2,
        left=0.14,
        right=0.98,
        bottom=0.12,
        top=0.92,
        wspace=0.62,
        hspace=0.56,
    )

    panel_a = outer[0, 0].subgridspec(
        2,
        4,
        width_ratios=(9.0, 0.7, 0.8, 4.6),
        height_ratios=(1.0, 10.0),
        wspace=0.28,
        hspace=0.06,
    )
    a_clean = figure.add_subplot(panel_a[0, 0])
    a_colorbar = figure.add_subplot(panel_a[1, 1])
    a_control = figure.add_subplot(panel_a[1, 2])
    a_control_labels = figure.add_subplot(
        panel_a[1, 3],
        sharey=a_control,
    )
    a_control_header = figure.add_subplot(panel_a[0, 2:])
    a_attack = figure.add_subplot(panel_a[1, 0])

    panel_b = outer[0, 1].subgridspec(
        1,
        2,
        wspace=0.28,
    )
    b_stage = figure.add_subplot(panel_b[0, 0])
    b_mechanism = figure.add_subplot(
        panel_b[0, 1],
        sharey=b_stage,
    )
    b_stage.set_xlim(0, 1.02)
    b_stage.set_xticks([0.0, 0.5, 1.0])
    b_mechanism.set_xlim(-1.02, 1.02)
    b_mechanism.set_xticks([-1.0, 0.0, 1.0])

    panel_c = figure.add_subplot(outer[1, 0])
    panel_c.set_ylabel("Eligible evaluation proportion")

    panel_d = figure.add_subplot(outer[1, 1])
    panel_d.set_axis_off()
    d_conditional = panel_d.inset_axes([0.0, 0.57, 1.0, 0.36])
    d_all_day = panel_d.inset_axes([0.0, 0.08, 1.0, 0.36])

    return figure, {
        "a_attack": a_attack,
        "a_colorbar": a_colorbar,
        "a_control": a_control,
        "a_control_labels": a_control_labels,
        "a_clean": a_clean,
        "a_control_header": a_control_header,
        "b_stage": b_stage,
        "b_mechanism": b_mechanism,
        "c": panel_c,
        "d_frame": panel_d,
        "d_conditional": d_conditional,
        "d_all_day": d_all_day,
    }


@close_new_figures
def render_aamos_integrity_figure(
    source_data_path: str | Path,
    manifest_or_intervals_path: str | Path,
    output_or_injection_path: str | Path,
    legacy_manifest_path: str | Path | None = None,
    legacy_output_dir: str | Path | None = None,
    *,
    submission: bool,
) -> dict[str, Path]:
    """Render the prespecified 2×2 AAMOS protocol-integrity figure.

    The three-argument form is ``source_data, manifest, output_dir``.  The old
    five-path signature is recognized only so an ineligible legacy manifest is
    rejected before any data loading; eligible legacy inputs must be regenerated
    with the new one-row-per-mark source-data contract.
    """

    legacy_call = legacy_manifest_path is not None
    if legacy_call:
        manifest_path = Path(legacy_manifest_path)
        output_dir = Path(legacy_output_dir) if legacy_output_dir else None
    else:
        manifest_path = Path(manifest_or_intervals_path)
        output_dir = Path(output_or_injection_path)
    manifest = load_manifest(manifest_path)
    if submission:
        assert_submission_eligible([manifest])
    if legacy_call:
        raise ValueError(
            "legacy AAMOS figure inputs are retired; regenerate the "
            "fig_aamos_protocol_integrity_source_data.csv contract"
        )
    if output_dir is None:  # pragma: no cover - defensive type guard
        raise ValueError("AAMOS output directory is required")
    if submission:
        if manifest.environment.get("profile") != "submission":
            raise ValueError(
                "AAMOS submission figure requires profile='submission'"
            )
        repetitions = int(
            manifest.environment.get("bootstrap_repetitions", 0)
        )
        if repetitions < 2_000:
            raise ValueError(
                "AAMOS submission figure requires at least 2000 "
                "bootstrap repetitions"
            )
    source = read_source_csv(source_data_path)
    required = {
        "panel_id",
        "metric_id",
        "scenario",
        "pipeline",
        "rate_requested",
        "numerator_n",
        "denominator_N",
        "estimate",
        "ci_low",
        "ci_high",
        "provenance",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(
            "AAMOS figure source missing columns: " + ", ".join(missing)
        )
    if submission:
        _validate_aamos_submission_source(
            source_data_path, manifest_path, source
        )
    if submission and set(source["provenance"]) != {"public_secondary"}:
        raise ValueError(
            "AAMOS submission source data must be public_secondary"
        )
    if submission:
        source_repetitions = pd.to_numeric(
            source["bootstrap_repetitions_requested"], errors="raise"
        )
        if source_repetitions.min() < 2_000:
            raise ValueError(
                "AAMOS submission source data require at least 2000 "
                "bootstrap repetitions"
            )
    mark_key = [
        "panel_id",
        "scenario",
        "rate_requested",
        "pipeline",
        "comparator_pipeline",
        "metric_id",
    ]
    if source.duplicated(mark_key).any():
        raise ValueError("AAMOS figure source contains duplicate plotted marks")
    if (pd.to_numeric(source["denominator_N"], errors="coerce") < 0).any():
        raise ValueError("AAMOS figure denominator cannot be negative")

    stem = "fig_06_aamos_protocol_integrity"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )

    _configure_figure_style(submission=submission)
    figure, layout = _create_aamos_figure_layout()
    render_tracker = _AamosRenderTracker()
    attack_axis = layout["a_attack"]
    colorbar_axis = layout["a_colorbar"]
    control_axis = layout["a_control"]
    control_label_axis = layout["a_control_labels"]
    clean_axis = layout["a_clean"]
    control_header_axis = layout["a_control_header"]
    left_axis = layout["b_stage"]
    right_axis = layout["b_mechanism"]
    ax_c = layout["c"]
    ax_d = layout["d_frame"]
    conditional_axis = layout["d_conditional"]
    all_day_axis = layout["d_all_day"]

    panel_a = source.loc[source["panel_id"] == "a"].copy()
    panel_a_groups = _aamos_panel_a_groups(panel_a)
    attack = panel_a_groups["attacks"]
    controls = panel_a_groups["controls"]
    clean = panel_a_groups["clean"]
    pipelines = [
        value
        for value in (
            "unverified",
            "signature_only",
            "signature_admission",
            "signature_binding_admission",
            "all_checks",
            "all_minus_signature",
            "all_minus_device",
            "all_minus_binding",
            "all_minus_admission",
            "all_minus_merkle",
            "all_minus_freshness",
            "all_minus_authorization",
        )
        if value in set(
            pd.concat(
                [attack["pipeline"], controls["pipeline"]],
                ignore_index=True,
            )
        )
    ]
    def rejection_matrix(
        data: pd.DataFrame,
        selected_pipelines: list[str],
    ):
        scenarios = sorted(data["scenario"].dropna().astype(str).unique())
        matrix = np.full(
            (len(scenarios), len(selected_pipelines)), np.nan
        )
        for row_index, scenario in enumerate(scenarios):
            for column_index, pipeline in enumerate(
                selected_pipelines
            ):
                selected = data.loc[
                    (data["scenario"].astype(str) == scenario)
                    & (data["pipeline"] == pipeline)
                ]
                if len(selected):
                    render_tracker.register_row(
                        next(selected.itertuples(index=False))
                    )
                    matrix[row_index, column_index] = float(
                        selected["estimate"].iloc[0]
                    )
        return scenarios, matrix

    attack_scenarios, attack_matrix = rejection_matrix(
        attack, pipelines
    )
    attack_image = attack_axis.imshow(
        np.ma.masked_invalid(attack_matrix),
        vmin=0,
        vmax=1,
        cmap="Blues",
        aspect="auto",
    )
    attack_axis.set_xticks(
        range(len(pipelines)),
        _aamos_pipeline_display_labels(pipelines),
        rotation=48,
        ha="right",
        fontsize=5.5,
    )
    attack_axis.set_yticks(
        range(len(attack_scenarios)),
        [value.replace("_", " ") for value in attack_scenarios],
        fontsize=5.5,
    )
    colorbar = figure.colorbar(
        attack_image,
        cax=colorbar_axis,
    )
    colorbar.set_ticks(
        [0.0, 0.5, 1.0],
        labels=["0", ".5", "1"],
    )
    colorbar.ax.set_title(
        "Reject.",
        fontsize=5.4,
        pad=3,
    )
    colorbar.ax.yaxis.set_ticks_position("left")
    colorbar.ax.tick_params(
        labelleft=True,
        labelright=False,
        labelsize=5.0,
        pad=1,
    )

    if controls.empty:
        control_axis.set_axis_off()
        control_label_axis.set_axis_off()
        control_header_axis.set_axis_off()
    else:
        control_pipelines = _aamos_panel_a_control_pipelines(controls)
        control_scenarios, control_matrix = rejection_matrix(
            controls, control_pipelines
        )
        control_cmap = plt.get_cmap("Oranges").copy()
        control_cmap.set_bad("#d9d9d9")
        control_image = control_axis.imshow(
            np.ma.masked_invalid(control_matrix),
            vmin=0,
            vmax=1,
            cmap=control_cmap,
            aspect="auto",
        )
        control_axis.set_xticks(
            []
        )
        control_axis.set_yticks([])
        for row_index, value in enumerate(control_matrix[:, 0]):
            if not np.isfinite(value):
                control_axis.text(
                    0,
                    row_index,
                    "ND",
                    ha="center",
                    va="center",
                    fontsize=4.8,
                    color=INK,
                )
        control_labels = _aamos_panel_a_control_labels(
            control_scenarios
        )
        control_label_axis.set_xlim(0, 1)
        control_label_axis.set_ylim(
            len(control_scenarios) - 0.5,
            -0.5,
        )
        control_label_axis.set_axis_off()
        for row_index, label in enumerate(control_labels):
            control_label_axis.text(
                0.02,
                row_index,
                label,
                transform=control_label_axis.get_yaxis_transform(),
                ha="left",
                va="center",
                fontsize=4.9,
            )
        control_header_axis.set_axis_off()
        control_header_axis.text(
            0.0,
            0.5,
            "Boundary controls\nAll-check rejection (0–1)",
            transform=control_header_axis.transAxes,
            ha="left",
            va="center",
            fontsize=5.8,
        )
    if pipelines and not clean.empty:
        clean_values_list = []
        for pipeline in pipelines:
            selected = clean.loc[clean["pipeline"] == pipeline]
            if selected.empty:
                clean_values_list.append(np.nan)
            else:
                render_tracker.register_row(
                    next(selected.itertuples(index=False))
                )
                clean_values_list.append(
                    float(selected["estimate"].iloc[0])
                )
        clean_values = np.array(clean_values_list)[None, :]
        clean_axis.imshow(
            np.ma.masked_invalid(clean_values),
            vmin=0,
            vmax=1,
            cmap="Greys",
            aspect="auto",
        )
        clean_axis.set_xticks([])
        clean_axis.set_yticks([0], ["clean FR"], fontsize=5.8)
        clean_axis.tick_params(axis="y", pad=2)
    else:
        clean_axis.set_axis_off()
    clean_axis.set_title(
        "Protocol decisions by scenario and configuration",
        loc="left",
        pad=8,
    )

    panel_b = source.loc[source["panel_id"] == "b"].copy()
    panel_b_layout = _aamos_panel_b_layout(panel_b)
    stage = panel_b_layout["stage"]
    mechanism = panel_b_layout["matched_pipeline"]

    def interval_plot(axis, data, *, color, xlabel, xlim, zero=False):
        for row in data.itertuples(index=False):
            render_tracker.register_row(row)
        labels = [
            str(value).replace("_", " ") for value in data["scenario"]
        ]
        y = pd.to_numeric(data["plot_y"], errors="raise").to_numpy()
        estimates = pd.to_numeric(data["estimate"], errors="coerce").to_numpy()
        low = pd.to_numeric(data["ci_low"], errors="coerce").to_numpy()
        high = pd.to_numeric(data["ci_high"], errors="coerce").to_numpy()
        valid = np.isfinite(estimates) & np.isfinite(low) & np.isfinite(high)
        axis.plot(
            estimates[valid],
            y[valid],
            "o",
            color=color,
            markersize=3.8,
        )
        axis.hlines(
            y[valid],
            low[valid],
            high[valid],
            color=GREY,
            linewidth=0.9,
        )
        nd_x = xlim[0] + 0.04 * (xlim[1] - xlim[0])
        for nd_y in y[~valid]:
            axis.text(
                nd_x,
                nd_y,
                "ND",
                ha="left",
                va="center",
                fontsize=5.2,
                color=INK,
            )
        if zero:
            axis.axvline(0, color=INK, linewidth=0.7)
        axis.set_yticks(y, labels, fontsize=5.7)
        axis.set_ylim(len(data) - 0.5, -0.5)
        axis.set_xlim(*xlim)
        axis.set_xlabel(xlabel, fontsize=6.5)
        axis.grid(True, axis="x")

    interval_plot(
        left_axis,
        stage,
        color=BLUE,
        xlabel="Stage agreement",
        xlim=(0, 1.02),
    )
    interval_plot(
        right_axis,
        mechanism,
        color=ORANGE,
        xlabel="Matched-config. rejection RD",
        xlim=(-1.02, 1.02),
        zero=True,
    )
    right_axis.tick_params(
        axis="y",
        left=False,
        labelleft=False,
    )
    left_axis.set_title(
        "Stage agreement and matched-configuration contrast",
        loc="left",
        pad=8,
    )

    panel_c = source.loc[source["panel_id"] == "c"].copy()
    line_styles = {
        "coverage": ("Coverage", BLUE, "o", "-"),
        "abstention": ("Abstention", ORANGE, "s", "--"),
    }
    for metric, (label, color, marker, linestyle) in line_styles.items():
        data = panel_c.loc[panel_c["metric_id"] == metric].sort_values(
            "rate_requested"
        )
        _plot_aamos_rate_interval(
            ax_c,
            data,
            label=label,
            color=color,
            marker=marker,
            linestyle=linestyle,
            tracker=render_tracker,
        )
    ax_c.set(
        xlabel="Requested injection rate (%)",
        ylabel="Eligible participant-day proportion",
        ylim=(0, 1.02),
    )
    ax_c.xaxis.labelpad = 3
    ax_c.yaxis.labelpad = 4
    ax_c.set_title(
        "Availability under mixed violations",
        loc="left",
    )
    ax_c.grid(True)
    ax_c.legend(loc="best")

    panel_d = source.loc[source["panel_id"] == "d"].copy()
    panel_d_facets = _aamos_panel_d_facets(panel_d)
    conditional_styles = {
        "covered_agreement": ("Covered agreement", BLUE, "o", "-"),
    }
    directional_styles = {
        "upward_discordance": ("Upward", OLIVE, "^", "--"),
        "priority_loss_discordance": (
            "Symptom-count loss",
            PINK,
            "D",
            ":",
        ),
    }
    for metric, (
        label,
        color,
        marker,
        linestyle,
    ) in conditional_styles.items():
        data = panel_d_facets["conditional"].loc[
            panel_d_facets["conditional"]["metric_id"] == metric
        ]
        _plot_aamos_rate_interval(
            conditional_axis,
            data,
            label=label,
            color=color,
            marker=marker,
            linestyle=linestyle,
            tracker=render_tracker,
        )
    for metric, (
        label,
        color,
        marker,
        linestyle,
    ) in directional_styles.items():
        data = panel_d_facets["all_day"].loc[
            panel_d_facets["all_day"]["metric_id"] == metric
        ]
        _plot_aamos_rate_interval(
            all_day_axis,
            data,
            label=label,
            color=color,
            marker=marker,
            linestyle=linestyle,
            tracker=render_tracker,
        )
    conditional_label, all_day_label = _aamos_panel_d_axis_labels()
    conditional_axis.set(
        ylabel=conditional_label,
        ylim=(0, 1.02),
    )
    all_day_axis.set(
        xlabel="Requested injection rate (%)",
        ylabel=all_day_label,
        ylim=(0, 1.02),
    )
    conditional_axis.set_xticklabels([])
    conditional_axis.set_title(
        "Conditional symptom-count agreement",
        loc="left",
        fontsize=7.2,
    )
    all_day_axis.set_title(
        "Directional discordance",
        loc="left",
        fontsize=7.2,
    )
    for inset in (conditional_axis, all_day_axis):
        inset.grid(True)
        inset.legend(loc="best", fontsize=5.8)
    ax_d.set_title(
        "Symptom-count behavior under mixed violations",
        loc="left",
    )

    panel_axes = (
        left_axis,
        ax_c,
        ax_d,
    )
    for label, axis in zip("bcd", panel_axes, strict=True):
        _panel_label(axis, label)
    figure.text(
        0.055,
        0.94,
        "a",
        fontsize=10.2,
        fontweight="bold",
        va="top",
    )
    if not submission:
        figure.text(
            0.5,
            0.5,
            "FIXTURE — NOT FOR SUBMISSION",
            ha="center",
            va="center",
            fontsize=18,
            color=PINK,
            alpha=0.25,
            rotation=25,
            fontweight="bold",
        )
    render_tracker.require_complete(source, submission=submission)
    return publish_figure_bundle(
        figure,
        source,
        output_paths,
        columns=_deterministic_source_columns(
            source,
            (
                "run_id",
                "created_utc",
                "provenance",
                "dataset_name",
                "dataset_doi",
                "dataset_source_inventory_sha256",
                "derivation_version",
                "derivation_config_basename",
                "derivation_config_file_sha256",
                "derivation_config_canonical_sha256",
                "metric_definition_version",
                "code_commit_or_archive_hash",
                "scenario",
                "scenario_class",
                "expected_outcome",
                "expected_first_stage",
                "rate_requested",
                "rate_realized",
                "pipeline",
                "comparator_pipeline",
                "comparison_type",
                "enabled_checks",
                "comparator_enabled_checks",
                "comparator_definition",
                "seed_count",
                "seed_scope",
                "execution_count_scope",
                "bootstrap_master_seed",
                "bootstrap_method",
                "bootstrap_interval_type",
                "bootstrap_repetitions_requested",
                "bootstrap_repetitions_valid",
                "bootstrap_repetitions_discarded",
                "unique_participants",
                "unique_eligible_participant_days",
                "unique_attacked_participant_days",
                "simulation_evaluations",
                "attempted_N",
                "mutated_N",
                "evaluated_N",
                "mixed_metric_applicable",
                "estimand",
                "evaluation_arm",
                "metric_id",
                "denominator_unit",
                "numerator_n",
                "denominator_N",
                "estimate",
                "ci_low",
                "ci_high",
                "panel_id",
                "display_order",
                "attack_rejected_n",
                "attacked_N",
                "clean_rejected_n",
                "clean_N",
                "stage_match_n",
                "stage_applicable_N",
                "covered_n",
                "eligible_N",
                "covered_agreement_n",
                "covered_N",
                "upward_n",
                "priority_loss_n",
                "paired_N",
                "both_reject_n",
                "attack_only_reject_n",
                "clean_only_reject_n",
                "neither_reject_n",
                "risk_difference",
                "risk_difference_ci_low",
                "risk_difference_ci_high",
            ),
        ),
        sort_by=tuple(
            column
            for column in (
                "panel_id",
                "display_order",
                "metric_id",
                "scenario",
                "rate_requested",
                "pipeline",
                "comparator_pipeline",
            )
            if column in source
        ),
    )


@close_new_figures
def render_late_update_figure(
    python_raw_path: str | Path,
    python_manifest_path: str | Path,
    fabric_root: str | Path,
    output_dir: str | Path,
    *,
    submission: bool,
) -> dict[str, Path]:
    python_manifest = load_manifest(python_manifest_path)
    if submission:
        assert_submission_eligible([python_manifest])
    fabric, _ = _load_fabric_observations(fabric_root, submission=submission)
    python_raw = read_source_csv(python_raw_path)
    local = python_raw.loc[python_raw["stage"] == "late_rebuild"].copy()
    cas = fabric.loc[
        (fabric["workload"] == "hot_key_cas")
        & (fabric["operation"] == FABRIC_INSTALL_OPERATION)
    ].copy()
    if local.empty or cas.empty:
        raise ValueError("late-update figure requires Python late_rebuild and Fabric hot_key_cas data")

    stem = "fig_05_late_update"
    output_paths = prepare_figure_output(
        output_dir,
        stem,
        submission=submission,
    )

    local_rows = []
    for batch_size, group in local.groupby("batch_size", sort=True):
        values = group["duration_ns"].to_numpy(dtype=float) / 1_000_000.0
        local_rows.append(
            {
                "batch_size": int(batch_size),
                "late_count": int(group["late_count"].iloc[0]),
                "n": int(values.size),
                "median_ms": float(np.median(values)),
                "q1_ms": float(np.quantile(values, 0.25)),
                "q3_ms": float(np.quantile(values, 0.75)),
                "p95_ms": float(np.quantile(values, 0.95)),
            }
        )
    local_summary = pd.DataFrame(local_rows)

    cas["conflict"] = cas["error_class"].isin(["CAS_CONFLICT", "MVCC_READ_CONFLICT"])
    run_cas = (
        cas.groupby(["run_id", "concurrency", "duration_seconds"], as_index=False)
        .agg(
            attempts=("attempt", "size"),
            conflicts=("conflict", "sum"),
            successes=("successful", "sum"),
        )
    )
    run_cas["conflict_rate"] = run_cas["conflicts"] / run_cas["attempts"]
    run_cas["successful_updates_s"] = run_cas["successes"] / run_cas["duration_seconds"]

    _configure_figure_style(submission=submission)
    figure, axes = plt.subplots(2, 2, figsize=(7.0, 5.45), constrained_layout=True)
    ax_a, ax_b, ax_c, ax_d = axes.flat

    x = local_summary["batch_size"].to_numpy(dtype=float)
    ax_a.plot(
        x, local_summary["median_ms"], color=BLUE, marker="o", label="Median"
    )
    ax_a.plot(
        x, local_summary["p95_ms"], color=ORANGE, marker="s", linestyle="--", label="P95"
    )
    ax_a.fill_between(
        x,
        local_summary["q1_ms"].to_numpy(dtype=float),
        local_summary["q3_ms"].to_numpy(dtype=float),
        color=BLUE,
        alpha=0.14,
        linewidth=0,
        label="IQR",
    )
    ax_a.set(xscale="log", yscale="log", xlabel="Previous records", ylabel="Local rebuild latency (ms)")
    ax_a.set_title("Late-record version rebuild", loc="left")
    ax_a.grid(True, which="major")
    ax_a.legend(loc="best")

    conflict_summary = (
        run_cas.groupby("concurrency")["conflict_rate"]
        .agg(median="median", q1=lambda x: x.quantile(0.25), q3=lambda x: x.quantile(0.75))
        .reset_index()
    )
    ax_b.plot(
        conflict_summary["concurrency"], conflict_summary["median"],
        color=PINK, marker="D", label="Median",
    )
    ax_b.fill_between(
        conflict_summary["concurrency"].to_numpy(dtype=float),
        conflict_summary["q1"].to_numpy(dtype=float),
        conflict_summary["q3"].to_numpy(dtype=float),
        color=PINK, alpha=0.16, linewidth=0, label="Across-run IQR",
    )
    ax_b.set(xlabel="Concurrent writers", ylabel="CAS/MVCC conflict proportion")
    ax_b.set_ylim(0, 1.02)
    ax_b.set_title("Hot-key contention", loc="left")
    ax_b.grid(True)
    ax_b.legend(loc="best")

    success_cas = cas.loc[cas["successful"]].copy()
    writers = sorted(success_cas["concurrency"].unique())
    retry_groups = [
        success_cas.loc[success_cas["concurrency"] == writer, "attempt"].to_numpy()
        for writer in writers
    ]
    boxes = ax_c.boxplot(
        retry_groups,
        tick_labels=[str(int(writer)) for writer in writers],
        patch_artist=True,
        showfliers=False,
        widths=0.58,
    )
    for box in boxes["boxes"]:
        box.set(facecolor="#F4E3D2", edgecolor=ORANGE, linewidth=1.0)
    for median in boxes["medians"]:
        median.set(color=INK, linewidth=1.4)
    ax_c.set(xlabel="Concurrent writers", ylabel="Attempt of successful CAS")
    ax_c.set_title("Retry cost", loc="left")
    ax_c.grid(True, axis="y")

    success_summary = (
        run_cas.groupby("concurrency")["successful_updates_s"]
        .agg(median="median", q1=lambda x: x.quantile(0.25), q3=lambda x: x.quantile(0.75))
        .reset_index()
    )
    ax_d.plot(
        success_summary["concurrency"], success_summary["median"],
        color=OLIVE, marker="o", label="Median",
    )
    ax_d.fill_between(
        success_summary["concurrency"].to_numpy(dtype=float),
        success_summary["q1"].to_numpy(dtype=float),
        success_summary["q3"].to_numpy(dtype=float),
        color=OLIVE, alpha=0.17, linewidth=0, label="Across-run IQR",
    )
    ax_d.set(xlabel="Concurrent writers", ylabel="Successful updates s$^{-1}$")
    ax_d.set_title("Linearized update throughput", loc="left")
    ax_d.grid(True)
    ax_d.legend(loc="best")

    for label, axis in zip("abcd", axes.flat, strict=True):
        _panel_label(axis, label)
    if not submission:
        figure.text(
            0.5, 0.5, "FIXTURE — NOT FOR SUBMISSION", ha="center", va="center",
            fontsize=18, color=PINK, alpha=0.25, rotation=25, fontweight="bold",
        )
    source = pd.concat(
        [
            local_summary.assign(panel="a", dataset="local_rebuild"),
            run_cas.assign(panel="b-d", dataset="fabric_cas_runs"),
            success_cas.assign(panel="c", dataset="successful_cas_attempts"),
        ],
        ignore_index=True,
        sort=False,
    )
    return publish_figure_bundle(
        figure,
        source,
        output_paths,
        columns=_deterministic_source_columns(
            source,
            (
                "panel",
                "dataset",
                "batch_size",
                "late_count",
                "n",
                "median_ms",
                "q1_ms",
                "q3_ms",
                "p95_ms",
                "run_id",
                "concurrency",
                "duration_seconds",
                "attempt",
            ),
        ),
        sort_by=(
            "panel",
            "dataset",
            "batch_size",
            "run_id",
            "concurrency",
            "attempt",
        ),
    )
