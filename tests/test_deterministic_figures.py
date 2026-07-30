import datetime
import hashlib
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments import deterministic_figures, plotting  # noqa: E402


class DeterministicFigureUnitTests(unittest.TestCase):
    def test_submission_requires_exact_scienceplots_version(self):
        for dependency in ((None, None), (object(), "2.1.0")):
            with self.subTest(dependency=dependency):
                with mock.patch.object(
                    deterministic_figures,
                    "_scienceplots_dependency",
                    return_value=dependency,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "SciencePlots 2.1.1"
                    ):
                        plotting.configure_style("submission")

    def test_preview_uses_matplotlib_fallback_without_scienceplots(self):
        with mock.patch.object(
            deterministic_figures,
            "_scienceplots_dependency",
            return_value=(None, None),
        ):
            plotting.configure_style("preview")
        self.assertEqual(
            deterministic_figures.plt.rcParams["font.family"],
            ["DejaVu Sans"],
        )

    def test_fixed_metadata_contract(self):
        epoch = datetime.datetime(
            2000, 1, 1, tzinfo=datetime.timezone.utc
        )
        self.assertEqual(
            deterministic_figures.PDF_METADATA,
            {
                "Creator": "TARMS reproducible experiments",
                "Producer": "Matplotlib",
                "CreationDate": epoch,
                "ModDate": epoch,
            },
        )
        self.assertEqual(
            deterministic_figures.PNG_METADATA,
            {"Software": "TARMS reproducible experiments"},
        )

    def test_save_wrapper_closes_figure_when_save_fails(self):
        deterministic_figures.configure_style("preview")
        figure = deterministic_figures.plt.figure()
        number = figure.number
        with mock.patch.object(
            figure, "savefig", side_effect=OSError("write failed")
        ):
            with self.assertRaisesRegex(OSError, "write failed"):
                deterministic_figures.save_figure_pair(
                    figure,
                    Path("/unused/figure.pdf"),
                    Path("/unused/figure.png"),
                )
        self.assertFalse(deterministic_figures.plt.fignum_exists(number))

    def test_csv_writer_fixes_columns_rows_floats_and_newlines(self):
        frame = pd.DataFrame(
            {
                "label": ["b", "a"],
                "value": [2.0, 1.23456789012345],
                "ignored": [9, 8],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            deterministic_figures.write_source_csv(
                frame,
                path,
                columns=("label", "value"),
                sort_by=("label",),
            )
            payload = path.read_bytes()
        self.assertEqual(
            payload,
            b"label,value\na,1.23456789012345\nb,2\n",
        )
        self.assertNotIn(b"\r\n", payload)

    def test_csv_writer_round_trips_source_floats_exactly(self):
        expected = [
            0.12345678901234566,
            9_876_543.210987654,
            1.0000000000000002,
            math.nan,
        ]
        frame = pd.DataFrame(
            {
                "metric": ["ci", "throughput", "next_float", "not_defined"],
                "value": expected,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            deterministic_figures.write_source_csv(
                frame,
                path,
                columns=("metric", "value"),
                sort_by=("metric",),
            )
            restored = pd.read_csv(
                path,
                float_precision="round_trip",
            ).set_index("metric")["value"]

        for metric, original in zip(frame["metric"], expected, strict=True):
            observed = float(restored.loc[metric])
            if math.isnan(original):
                self.assertTrue(math.isnan(observed))
            else:
                self.assertEqual(observed, original)

    def test_round_trip_read_and_write_is_byte_idempotent(self):
        frame = pd.DataFrame(
            {
                "metric": ["ci", "throughput", "not_defined"],
                "value": [
                    0.12345678901234566,
                    9_876_543.210987654,
                    math.nan,
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "first.csv"
            second = root / "second.csv"
            deterministic_figures.write_source_csv(
                frame,
                first,
                columns=("metric", "value"),
                sort_by=("metric",),
            )
            restored = deterministic_figures.read_source_csv(first)
            deterministic_figures.write_source_csv(
                restored,
                second,
                columns=("metric", "value"),
                sort_by=("metric",),
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_bundle_failure_preserves_every_existing_target(self):
        for failure in ("second_save", "csv"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    output = root / "figures"
                    output.mkdir()
                    paths = deterministic_figures.prepare_figure_output(
                        output,
                        "atomic",
                        submission=False,
                    )
                    old_bytes = {
                        paths.pdf: b"old-pdf",
                        paths.png: b"old-png",
                        paths.source_data: b"old-csv",
                    }
                    for path, payload in old_bytes.items():
                        path.write_bytes(payload)
                    deterministic_figures.configure_style("preview")
                    figure = deterministic_figures.plt.figure()
                    frame = pd.DataFrame({"value": [1.0]})
                    if failure == "second_save":
                        patch = mock.patch.object(
                            figure,
                            "savefig",
                            side_effect=[None, OSError("png failed")],
                        )
                    else:
                        patch = mock.patch.object(
                            deterministic_figures,
                            "write_source_csv",
                            side_effect=OSError("csv failed"),
                        )
                    with patch:
                        with self.assertRaisesRegex(
                            OSError, "png failed|csv failed"
                        ):
                            deterministic_figures.publish_figure_bundle(
                                figure,
                                frame,
                                paths,
                                columns=("value",),
                                sort_by=("value",),
                            )
                    for path, payload in old_bytes.items():
                        self.assertEqual(path.read_bytes(), payload)
                    self.assertEqual(
                        list(output.glob(".tarms-figure-*")),
                        [],
                    )

    def test_destination_pdf_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "figures"
            output.mkdir()
            sealed_target = root / "sealed-target.pdf"
            sealed_target.write_bytes(b"sealed")
            destination = output / "fig_05_window_tradeoff.pdf"
            destination.symlink_to(sealed_target)

            with self.assertRaisesRegex(ValueError, "symlink"):
                plotting.render_window_tradeoff_figure(
                    output,
                    anchor_bytes=399,
                    submission=False,
                )

            self.assertTrue(destination.is_symlink())
            self.assertEqual(sealed_target.read_bytes(), b"sealed")

    def test_output_directory_symlink_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            output = alias / "figures"

            with self.assertRaisesRegex(ValueError, "symlink component"):
                plotting.render_window_tradeoff_figure(
                    output,
                    anchor_bytes=399,
                    submission=False,
                )

            self.assertFalse((real / "figures").exists())

    def test_replace_failure_rolls_back_every_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "figures"
            output.mkdir()
            paths = deterministic_figures.prepare_figure_output(
                output,
                "atomic",
                submission=False,
            )
            old_bytes = {
                paths.pdf: b"old-pdf",
                paths.png: b"old-png",
                paths.source_data: b"old-csv",
            }
            for path, payload in old_bytes.items():
                path.write_bytes(payload)
            deterministic_figures.configure_style("preview")
            figure = deterministic_figures.plt.figure()
            original_replace = deterministic_figures.os.replace
            failed = False

            def fail_png_publish(source, destination):
                nonlocal failed
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    not failed
                    and source_path.name == paths.png.name
                    and destination_path == paths.png
                ):
                    failed = True
                    raise OSError("replace failed")
                return original_replace(source, destination)

            with mock.patch.object(
                deterministic_figures.os,
                "replace",
                side_effect=fail_png_publish,
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    deterministic_figures.publish_figure_bundle(
                        figure,
                        pd.DataFrame({"value": [1.0]}),
                        paths,
                        columns=("value",),
                        sort_by=("value",),
                    )

            for path, payload in old_bytes.items():
                self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(list(output.glob(".tarms-figure-*")), [])

    def test_rollback_failures_retain_backups_and_attempt_every_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "figures"
            output.mkdir()
            paths = deterministic_figures.prepare_figure_output(
                output,
                "atomic",
                submission=False,
            )
            old_bytes = {
                paths.pdf: b"unique-old-pdf",
                paths.png: b"unique-old-png",
                paths.source_data: b"unique-old-csv",
            }
            for path, payload in old_bytes.items():
                path.write_bytes(payload)
            deterministic_figures.configure_style("preview")
            figure = deterministic_figures.plt.figure()
            original_replace = deterministic_figures.os.replace
            publish_failure = OSError("publish png failed")
            restore_failures = {
                paths.pdf: OSError("restore pdf failed"),
                paths.source_data: OSError("restore csv failed"),
            }
            restore_attempts = []

            def fail_publish_and_two_restores(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.name.startswith(".backup-")
                    and destination_path in old_bytes
                ):
                    restore_attempts.append(destination_path)
                    if destination_path in restore_failures:
                        raise restore_failures[destination_path]
                if (
                    source_path.name == paths.png.name
                    and destination_path == paths.png
                ):
                    raise publish_failure
                return original_replace(source, destination)

            caught_error = None
            with mock.patch.object(
                deterministic_figures.os,
                "replace",
                side_effect=fail_publish_and_two_restores,
            ):
                try:
                    deterministic_figures.publish_figure_bundle(
                        figure,
                        pd.DataFrame({"value": [1.0]}),
                        paths,
                        columns=("value",),
                        sort_by=("value",),
                    )
                except Exception as error:
                    caught_error = error
            self.assertIsNotNone(caught_error)
            self.assertIsInstance(caught_error, RuntimeError)
            self.assertIs(caught_error.__cause__, publish_failure)
            self.assertIs(caught_error.publish_error, publish_failure)
            self.assertEqual(
                restore_attempts,
                [paths.pdf, paths.png, paths.source_data],
            )
            self.assertEqual(
                caught_error.rollback_failures,
                restore_failures,
            )
            retained_directories = list(output.glob(".tarms-figure-*"))
            self.assertEqual(len(retained_directories), 1)
            recovery_directory = retained_directories[0]
            expected_recovery_paths = {
                paths.pdf: recovery_directory / ".backup-pdf",
                paths.source_data: recovery_directory
                / ".backup-source_data",
            }
            self.assertEqual(
                caught_error.recovery_paths,
                expected_recovery_paths,
            )
            recovery_directories = {
                path.parent
                for path in caught_error.recovery_paths.values()
            }
            self.assertEqual(len(recovery_directories), 1)
            self.assertEqual(recovery_directories.pop(), recovery_directory)
            self.assertTrue(recovery_directory.is_dir())
            self.assertEqual(paths.png.read_bytes(), old_bytes[paths.png])
            self.assertFalse(paths.pdf.exists())
            self.assertFalse(paths.source_data.exists())
            self.assertEqual(
                {path.name for path in recovery_directory.iterdir()},
                {".backup-pdf", ".backup-source_data"},
            )
            for target, payload in old_bytes.items():
                recovery_path = caught_error.recovery_paths.get(target)
                preserved_path = target if target.exists() else recovery_path
                self.assertIsNotNone(preserved_path)
                self.assertEqual(preserved_path.read_bytes(), payload)
            message = str(caught_error)
            self.assertIn("publish png failed", message)
            self.assertIn("restore pdf failed", message)
            self.assertIn("restore csv failed", message)
            for recovery_path in expected_recovery_paths.values():
                self.assertIn(str(recovery_path), message)

    def test_direct_submission_renderer_rejects_sealed_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sealed = root / "sealed"
            sealed.mkdir()
            (sealed / "MANIFEST.sha256").write_text(
                "sealed\n", encoding="utf-8"
            )
            output = sealed / "new" / "figures"

            with self.assertRaisesRegex(ValueError, "MANIFEST.sha256"):
                plotting.render_window_tradeoff_figure(
                    output,
                    anchor_bytes=399,
                    submission=True,
                )

            self.assertFalse(output.exists())

    def test_style_and_plot_failures_create_no_output_or_open_figures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            style_output = root / "style-failure"
            with mock.patch.object(
                deterministic_figures,
                "_scienceplots_dependency",
                return_value=(None, None),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "SciencePlots 2.1.1"
                ):
                    plotting.render_window_tradeoff_figure(
                        style_output,
                        anchor_bytes=399,
                        submission=True,
                    )
            self.assertFalse(style_output.exists())

            plot_output = root / "plot-failure"
            before = set(deterministic_figures.plt.get_fignums())
            with mock.patch.object(
                plotting,
                "_panel_label",
                side_effect=RuntimeError("plot failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "plot failed"):
                    plotting.render_window_tradeoff_figure(
                        plot_output,
                        anchor_bytes=399,
                        submission=False,
                    )
            self.assertFalse(plot_output.exists())
            self.assertEqual(
                set(deterministic_figures.plt.get_fignums()),
                before,
            )


class DeterministicFigureProcessTests(unittest.TestCase):
    maxDiff = None

    def _run_cli(
        self,
        figure: str,
        output: Path | None,
        *,
        mode: str = "submission",
        mplconfigdir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            "scripts/make_figures.py",
            "--figure",
            figure,
            "--mode",
            mode,
        ]
        if output is not None:
            command.extend(["--output", str(output)])
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        if mplconfigdir is not None:
            environment["MPLCONFIGDIR"] = str(mplconfigdir)
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _sealed_hashes() -> dict[str, str]:
        paths = [PROJECT_ROOT / "MANIFEST.sha256"]
        figure_root = PROJECT_ROOT / "results" / "figures" / "submission"
        paths.extend(
            path for path in figure_root.rglob("*") if path.is_file()
        )
        return {
            str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(paths)
        }

    def test_submission_requires_explicit_output(self):
        result = self._run_cli("window", None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--output", result.stderr)

    def test_submission_rejects_output_below_sealed_tree(self):
        output = PROJECT_ROOT / "tmp" / "must-not-write"
        result = self._run_cli("window", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MANIFEST.sha256", result.stderr)
        self.assertFalse(output.exists())

    def test_python_component_and_window_are_byte_identical_across_processes(self):
        expected_stems = {
            "python": "fig_03_python_benchmarks",
            "component": "fig_04_component_conformance",
            "window": "fig_05_window_tradeoff",
        }
        sealed_before = self._sealed_hashes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for figure, stem in expected_stems.items():
                with self.subTest(figure=figure):
                    left = root / f"{figure}-left"
                    right = root / f"{figure}-right"
                    left_cache = root / f"{figure}-left-cache"
                    right_cache = root / f"{figure}-right-cache"
                    first = self._run_cli(
                        figure,
                        left,
                        mplconfigdir=left_cache,
                    )
                    second = self._run_cli(
                        figure,
                        right,
                        mplconfigdir=right_cache,
                    )
                    self.assertEqual(
                        first.returncode,
                        0,
                        msg=f"stdout:\n{first.stdout}\nstderr:\n{first.stderr}",
                    )
                    self.assertEqual(
                        second.returncode,
                        0,
                        msg=f"stdout:\n{second.stdout}\nstderr:\n{second.stderr}",
                    )
                    for suffix in (".pdf", ".png", "_source_data.csv"):
                        left_path = left / f"{stem}{suffix}"
                        right_path = right / f"{stem}{suffix}"
                        self.assertTrue(left_path.is_file())
                        self.assertEqual(
                            hashlib.sha256(left_path.read_bytes()).digest(),
                            hashlib.sha256(right_path.read_bytes()).digest(),
                            msg=f"{figure} differs for {suffix}",
                        )
                        self.assertEqual(
                            left_path.read_bytes(),
                            right_path.read_bytes(),
                            msg=f"{figure} differs for {suffix}",
                        )
                    self.assertTrue(left_cache.is_dir())
                    self.assertTrue(right_cache.is_dir())
                    self.assertNotEqual(left_cache, right_cache)
        self.assertEqual(self._sealed_hashes(), sealed_before)


if __name__ == "__main__":
    unittest.main()
