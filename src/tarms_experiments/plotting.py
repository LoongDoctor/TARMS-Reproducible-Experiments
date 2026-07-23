"""SciencePlots figures with explicit provenance gates and source data."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


EXPERIMENTS_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("MPLCONFIGDIR", str(EXPERIMENTS_ROOT / "tmp" / "matplotlib"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

try:
    import scienceplots  # noqa: F401
except ModuleNotFoundError:
    sys.path.append(str(EXPERIMENTS_ROOT / "vendor"))
    import scienceplots  # noqa: F401

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from .provenance import assert_submission_eligible, load_manifest
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


def _apply_style() -> None:
    plt.style.use(["science", "no-latex"])
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.2,
            "axes.titlesize": 9.0,
            "axes.labelsize": 8.5,
            "axes.titleweight": "semibold",
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "text.color": INK,
            "legend.fontsize": 7.1,
            "legend.frameon": False,
            "grid.color": LIGHT_GREY,
            "grid.linewidth": 0.55,
            "grid.alpha": 0.62,
            "lines.linewidth": 1.45,
            "lines.markersize": 4.0,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
        }
    )


def _save_figure(figure, pdf_path: Path, png_path: Path) -> None:
    """Write stable figure files without wall-clock PDF metadata."""
    figure.savefig(
        pdf_path,
        metadata={
            "CreationDate": None,
            "ModDate": None,
        },
    )
    figure.savefig(png_path, dpi=300)


def model_ledger_bytes(batch_sizes: Iterable[int]) -> pd.DataFrame:
    rows = []
    for batch_size in map(int, batch_sizes):
        if batch_size <= 0:
            raise ValueError("batch sizes must be positive")
        anchor = {
            "aid": "a" * 64,
            "kappa": "patient-000001|2026-07-22T00:00Z",
            "version": 1,
            "root": "b" * 64,
            "prevAid": "d" * 64,
            "recordCount": batch_size,
            "uriHash": "c" * 64,
            "createdAt": "2026-07-22T00:00:00Z",
        }
        latest = {
            "kappa": anchor["kappa"],
            "aid": anchor["aid"],
            "version": 1,
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
                        "UTF-8 steady-state anchor JSON+latest-pointer JSON; "
                        "includes nonempty prevAid, uriHash, and latest root"
                    ),
                    "anchor_version": 1,
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


def render_window_tradeoff_figure(
    output_dir: str | Path, *, anchor_bytes: int | None = None
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "fig_05_window_tradeoff"
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    source_path = output_dir / f"{stem}_source_data.csv"
    if anchor_bytes is None:
        reference = model_ledger_bytes([4096])
        anchor_bytes = int(
            reference.loc[reference["strategy"] == "TARMS anchor", "bytes"].iloc[0]
        )
    data = model_window_tradeoff([1, 5, 10, 15, 30, 60], anchor_bytes=anchor_bytes)

    _apply_style()
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
    _save_figure(figure, pdf_path, png_path)
    plt.close(figure)
    data.to_csv(source_path, index=False)
    return {"pdf": pdf_path, "png": png_path, "source_data": source_path}


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
    raw = pd.read_csv(raw_path)
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

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "fig_04_component_conformance"
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    source_path = output_dir / f"{stem}_source_data.csv"
    _apply_style()
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
    _save_figure(figure, pdf_path, png_path)
    plt.close(figure)
    summary.to_csv(source_path, index=False)
    return {"pdf": pdf_path, "png": png_path, "source_data": source_path}


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
    raw = pd.read_csv(raw_path)
    summary = pd.read_csv(summary_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "fig_03_python_benchmarks"
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    source_path = output_dir / f"{stem}_source_data.csv"

    _apply_style()
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
    _save_figure(figure, pdf_path, png_path)
    plt.close(figure)

    source_parts = [
        panel_a.assign(panel="a", dataset="stage_summary"),
        *ecdf_parts,
        throughput.assign(panel="c", dataset="signature_admission_throughput"),
        modeled.assign(panel="d", dataset="ledger_model"),
    ]
    pd.concat(source_parts, ignore_index=True, sort=False).to_csv(source_path, index=False)
    return {"pdf": pdf_path, "png": png_path, "source_data": source_path}


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


def render_fabric_performance_figure(
    fabric_root: str | Path,
    output_dir: str | Path,
    *,
    submission: bool,
) -> dict[str, Path]:
    raw, manifests = _load_fabric_observations(fabric_root, submission=submission)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "fig_04_fabric_performance"
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    source_path = output_dir / f"{stem}_source_data.csv"

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

    _apply_style()
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
    _save_figure(figure, pdf_path, png_path)
    plt.close(figure)

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
    source.to_csv(source_path, index=False)
    return {"pdf": pdf_path, "png": png_path, "source_data": source_path}


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
    python_raw = pd.read_csv(python_raw_path)
    local = python_raw.loc[python_raw["stage"] == "late_rebuild"].copy()
    cas = fabric.loc[
        (fabric["workload"] == "hot_key_cas")
        & (fabric["operation"] == FABRIC_INSTALL_OPERATION)
    ].copy()
    if local.empty or cas.empty:
        raise ValueError("late-update figure requires Python late_rebuild and Fabric hot_key_cas data")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "fig_05_late_update"
    pdf_path = output_dir / f"{stem}.pdf"
    png_path = output_dir / f"{stem}.png"
    source_path = output_dir / f"{stem}_source_data.csv"

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

    _apply_style()
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
    _save_figure(figure, pdf_path, png_path)
    plt.close(figure)

    pd.concat(
        [
            local_summary.assign(panel="a", dataset="local_rebuild"),
            run_cas.assign(panel="b-d", dataset="fabric_cas_runs"),
            success_cas.assign(panel="c", dataset="successful_cas_attempts"),
        ],
        ignore_index=True,
        sort=False,
    ).to_csv(source_path, index=False)
    return {"pdf": pdf_path, "png": png_path, "source_data": source_path}
