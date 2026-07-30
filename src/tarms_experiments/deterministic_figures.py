"""Strict, byte-stable figure and source-data output helpers."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from functools import wraps
import importlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterable, Sequence

import pandas as pd

import matplotlib.pyplot as plt


REQUIRED_SCIENCEPLOTS_VERSION = "2.1.1"
SOURCE_FLOAT_FORMAT = "%.17g"
SAVE_DPI = 300
SAVE_BBOX = "tight"
SAVE_PAD_INCHES = 0.04
_FIXED_PDF_TIME = datetime.datetime(
    2000, 1, 1, tzinfo=datetime.timezone.utc
)
PDF_METADATA = {
    "Creator": "TARMS reproducible experiments",
    "Producer": "Matplotlib",
    "CreationDate": _FIXED_PDF_TIME,
    "ModDate": _FIXED_PDF_TIME,
}
PNG_METADATA = {"Software": "TARMS reproducible experiments"}


@dataclass(frozen=True)
class FigureOutputPaths:
    directory: Path
    pdf: Path
    png: Path
    source_data: Path
    submission: bool

    def as_dict(self) -> dict[str, Path]:
        return {
            "pdf": self.pdf,
            "png": self.png,
            "source_data": self.source_data,
        }


class FigureBundleRollbackError(RuntimeError):
    """Report an incomplete rollback and the exact retained backup paths."""

    def __init__(
        self,
        publish_error: Exception,
        rollback_failures: dict[Path, Exception],
        recovery_paths: dict[Path, Path],
    ) -> None:
        self.publish_error = publish_error
        self.rollback_failures = dict(rollback_failures)
        self.recovery_paths = dict(recovery_paths)
        details = "; ".join(
            (
                f"target={target}, backup={self.recovery_paths.get(target)}, "
                f"error={type(error).__name__}: {error}"
            )
            for target, error in self.rollback_failures.items()
        )
        super().__init__(
            "figure bundle publication failed "
            f"({type(publish_error).__name__}: {publish_error}); "
            f"rollback incomplete; retained recovery backups: {details}"
        )


def _allowed_system_alias(path: Path) -> bool:
    aliases = {
        Path("/tmp"): Path("/private/tmp"),
        Path("/var"): Path("/private/var"),
    }
    expected = aliases.get(path)
    return (
        expected is not None
        and path.is_symlink()
        and path.resolve() == expected
    )


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink() and not _allowed_system_alias(current):
            raise ValueError(
                f"figure output path contains a symlink component: {current}"
            )


def validate_output_directory(
    output_dir: str | Path,
    *,
    submission: bool,
) -> Path:
    """Resolve an output directory without following untrusted symlinks."""

    supplied = Path(output_dir).expanduser()
    if ".." in supplied.parts:
        raise ValueError("figure output directory cannot contain '..'")
    lexical = Path(os.path.abspath(supplied))
    _reject_symlink_components(lexical)
    resolved = lexical.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("figure output directory must be a directory")
    if submission:
        for candidate in (resolved, *resolved.parents):
            manifest = candidate / "MANIFEST.sha256"
            if manifest.is_file():
                raise ValueError(
                    "submission output is inside a tree sealed by "
                    f"{manifest}"
                )
    return resolved


def _validate_destination(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(
            f"figure destination cannot be a symlink: {path}"
        )
    if path.exists() and not path.is_file():
        raise ValueError(
            f"figure destination must be a regular file: {path}"
        )


def prepare_figure_output(
    output_dir: str | Path,
    stem: str,
    *,
    submission: bool,
) -> FigureOutputPaths:
    """Validate output safety without creating directories or files."""

    if not stem or Path(stem).name != stem:
        raise ValueError("figure output stem must be a basename")
    directory = validate_output_directory(
        output_dir,
        submission=submission,
    )
    paths = FigureOutputPaths(
        directory=directory,
        pdf=directory / f"{stem}.pdf",
        png=directory / f"{stem}.png",
        source_data=directory / f"{stem}_source_data.csv",
        submission=submission,
    )
    for path in paths.as_dict().values():
        _validate_destination(path)
    return paths


def close_new_figures(function):
    """Close every figure opened by a renderer, on success or failure."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        before = set(plt.get_fignums())
        try:
            return function(*args, **kwargs)
        finally:
            for number in set(plt.get_fignums()) - before:
                plt.close(number)

    return wrapped


def _scienceplots_dependency() -> tuple[object | None, str | None]:
    """Load SciencePlots and its installed distribution version dynamically."""

    try:
        module = importlib.import_module("scienceplots")
    except ModuleNotFoundError as error:
        if error.name != "scienceplots":
            raise
        return None, None
    try:
        version = importlib.metadata.version("SciencePlots")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return module, version


def configure_style(mode: str = "submission") -> None:
    """Configure deterministic plotting style for submission or preview."""

    if mode not in {"submission", "preview"}:
        raise ValueError("figure mode must be 'submission' or 'preview'")
    scienceplots, version = _scienceplots_dependency()
    if mode == "submission" and (
        scienceplots is None or version != REQUIRED_SCIENCEPLOTS_VERSION
    ):
        raise RuntimeError(
            "Submission rendering requires importable "
            f"SciencePlots {REQUIRED_SCIENCEPLOTS_VERSION}; "
            f"observed {version or 'missing'}"
        )
    if scienceplots is not None:
        plt.style.use(["science", "no-latex"])
    else:
        plt.style.use("default")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.sans-serif": ["DejaVu Sans"],
            "font.size": 8.2,
            "mathtext.fontset": "dejavusans",
            "axes.titlesize": 9.0,
            "axes.labelsize": 8.5,
            "axes.titleweight": "semibold",
            "axes.formatter.use_locale": False,
            "legend.fontsize": 7.1,
            "legend.frameon": False,
            "grid.linewidth": 0.55,
            "grid.alpha": 0.62,
            "lines.linewidth": 1.45,
            "lines.markersize": 4.0,
            "path.simplify": True,
            "path.simplify_threshold": 0.111111111111,
            "agg.path.chunksize": 0,
            "text.hinting": "force_autohint",
            "text.hinting_factor": 8,
            "text.kerning_factor": 0,
            "pdf.compression": 9,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.dpi": 100,
            "savefig.dpi": SAVE_DPI,
            "savefig.bbox": SAVE_BBOX,
            "savefig.pad_inches": SAVE_PAD_INCHES,
        }
    )


def save_figure_pair(
    figure,
    pdf_path: str | Path,
    png_path: str | Path,
) -> None:
    """Save deterministic PDF and PNG bytes and always close the figure."""

    try:
        figure.savefig(
            Path(pdf_path),
            format="pdf",
            dpi=SAVE_DPI,
            bbox_inches=SAVE_BBOX,
            pad_inches=SAVE_PAD_INCHES,
            metadata=PDF_METADATA,
        )
        figure.savefig(
            Path(png_path),
            format="png",
            dpi=SAVE_DPI,
            bbox_inches=SAVE_BBOX,
            pad_inches=SAVE_PAD_INCHES,
            metadata=PNG_METADATA,
        )
    finally:
        plt.close(figure)


def write_source_csv(
    frame: pd.DataFrame,
    output_path: str | Path,
    *,
    columns: Sequence[str],
    sort_by: Iterable[str],
) -> None:
    """Write source data with an explicit schema and stable row ordering."""

    ordered_columns = tuple(columns)
    if len(ordered_columns) != len(set(ordered_columns)):
        raise ValueError("source CSV columns must be unique")
    missing = [column for column in ordered_columns if column not in frame]
    if missing:
        raise ValueError(
            "source CSV missing columns: " + ", ".join(missing)
        )
    sort_columns = tuple(sort_by)
    missing_sort = [column for column in sort_columns if column not in frame]
    if missing_sort:
        raise ValueError(
            "source CSV missing sort columns: " + ", ".join(missing_sort)
        )
    ordered = frame.loc[:, ordered_columns]
    if sort_columns:
        ordered = ordered.sort_values(
            list(sort_columns),
            kind="mergesort",
            na_position="last",
        )
    ordered.to_csv(
        Path(output_path),
        index=False,
        lineterminator="\n",
        float_format=SOURCE_FLOAT_FORMAT,
    )


def read_source_csv(path: str | Path, **kwargs) -> pd.DataFrame:
    """Read controlled floating source data with exact binary64 recovery."""

    if "float_precision" in kwargs:
        raise ValueError("source CSV float precision is fixed")
    return pd.read_csv(
        Path(path),
        float_precision="round_trip",
        **kwargs,
    )


def _require_regular_stage(path: Path) -> None:
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"staged figure output is not regular: {path}")


def _replace_bundle(
    staged: dict[str, Path],
    targets: dict[str, Path],
) -> None:
    """Replace a bundle with caught-exception rollback.

    Every backup restoration is attempted independently. Unrestored backups
    are retained for manual recovery. This does not provide crash atomicity or
    concurrent-reader bundle atomicity.
    """

    staging_dir = next(iter(staged.values())).parent
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for target in targets.values():
            _validate_destination(target)
        for name, target in targets.items():
            if target.exists():
                backup = staging_dir / f".backup-{name}"
                os.replace(target, backup)
                backups[target] = backup
        for name, target in targets.items():
            _validate_destination(target)
            os.replace(staged[name], target)
            published.append(target)
    except Exception as publish_error:
        for target in reversed(published):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        rollback_failures: dict[Path, Exception] = {}
        for target, backup in backups.items():
            if backup.exists():
                try:
                    os.replace(backup, target)
                except Exception as rollback_error:
                    rollback_failures[target] = rollback_error
        for staged_path in staged.values():
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
        if rollback_failures:
            recovery_paths = {
                target: backup
                for target, backup in backups.items()
                if backup.exists()
            }
            rollback_error = FigureBundleRollbackError(
                publish_error,
                rollback_failures,
                recovery_paths,
            )
            raise rollback_error from publish_error
        raise


def publish_figure_bundle(
    figure,
    frame: pd.DataFrame,
    paths: FigureOutputPaths,
    *,
    columns: Sequence[str],
    sort_by: Iterable[str],
) -> dict[str, Path]:
    """Stage and publish a PDF/PNG/CSV set with caught-exception recovery.

    All artifacts are staged before publication. A caught publication error
    attempts rollback and retains any unrestored backup for recovery. This
    does not provide crash atomicity or concurrent-reader bundle atomicity.
    """

    staging_dir: Path | None = None
    retain_staging = False
    try:
        checked = prepare_figure_output(
            paths.directory,
            paths.pdf.stem,
            submission=paths.submission,
        )
        if checked != paths:
            raise ValueError("figure output paths changed after validation")
        paths.directory.mkdir(parents=True, exist_ok=True)
        if (
            validate_output_directory(
                paths.directory,
                submission=paths.submission,
            )
            != paths.directory
        ):
            raise ValueError("figure output directory changed during creation")
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=".tarms-figure-",
                dir=paths.directory,
            )
        )
        staged = {
            "pdf": staging_dir / paths.pdf.name,
            "png": staging_dir / paths.png.name,
            "source_data": staging_dir / paths.source_data.name,
        }
        save_figure_pair(figure, staged["pdf"], staged["png"])
        write_source_csv(
            frame,
            staged["source_data"],
            columns=columns,
            sort_by=sort_by,
        )
        for path in staged.values():
            _require_regular_stage(path)
        try:
            _replace_bundle(staged, paths.as_dict())
        except FigureBundleRollbackError:
            retain_staging = True
            raise
        return paths.as_dict()
    finally:
        plt.close(figure)
        if staging_dir is not None and not retain_staging:
            shutil.rmtree(staging_dir, ignore_errors=True)
