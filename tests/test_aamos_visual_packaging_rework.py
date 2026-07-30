import importlib.util
import inspect
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

import matplotlib.pyplot as plt
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from aamos_submission_fixture import submission_contract  # noqa: E402
from tarms_experiments import plotting  # noqa: E402


EXPECTED_ARTIFACT_FILENAMES = (
    "patient_days.csv",
    "clean_decisions.csv",
    "injection_manifest.csv",
    "attack_decisions.csv",
    "boundary_manifest.csv",
    "boundary_decisions.csv",
    "per_seed_metrics.csv",
    "metric_summary.csv",
    "attack_stage_matrix.csv",
    "paired_contrasts.csv",
    "fig_aamos_protocol_integrity_source_data.csv",
    "participant_day_flow.json",
)


def _load_runner():
    path = (
        PROJECT_ROOT
        / "scripts"
        / "run_aamos_standard_enhanced.py"
    )
    spec = importlib.util.spec_from_file_location(
        "aamos_visual_packaging_runner", path
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("cannot load AAMOS runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AamosVisualLayoutTests(unittest.TestCase):
    def test_panels_use_separated_grid_cells_and_outer_ticks(self):
        factory = getattr(
            plotting, "_create_aamos_figure_layout", None
        )
        self.assertTrue(
            callable(factory),
            "AAMOS renderer requires an explicit GridSpec layout",
        )
        figure, axes = factory()
        try:
            required = {
                "a_attack",
                "a_colorbar",
                "a_control",
                "a_control_labels",
                "a_clean",
                "a_control_header",
                "b_stage",
                "b_mechanism",
                "c",
                "d_frame",
            }
            self.assertTrue(required.issubset(axes))
            figure.canvas.draw()

            panel_a = [
                axes["a_attack"].get_position(),
                axes["a_colorbar"].get_position(),
                axes["a_control"].get_position(),
                axes["a_control_labels"].get_position(),
            ]
            for left, right in pairwise(panel_a):
                self.assertLess(
                    left.x1,
                    right.x0,
                    "Panel a GridSpec columns must not overlap",
                )

            stage = axes["b_stage"].get_position()
            mechanism = axes["b_mechanism"].get_position()
            self.assertGreaterEqual(
                mechanism.x0 - stage.x1,
                0.02,
                "Panel b needs a visible inter-axis gutter",
            )
            self.assertEqual(
                list(axes["b_stage"].get_xticks()),
                [0.0, 0.5, 1.0],
            )
            self.assertEqual(
                list(axes["b_mechanism"].get_xticks()),
                [-1.0, 0.0, 1.0],
            )
            self.assertEqual(
                axes["c"].get_ylabel(),
                "Eligible evaluation proportion",
            )
        finally:
            plt.close(figure)

    def test_strict_fixture_render_keeps_printable_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, _, _ = (
                submission_contract(root)
            )
            rendered = plotting.render_aamos_integrity_figure(
                source_path,
                manifest_path,
                root / "figures",
                submission=True,
            )
            with Image.open(rendered["png"]) as image:
                width, height = image.size
            self.assertGreaterEqual(width, 2_200)
            self.assertLessEqual(width, 2_900)
            self.assertGreaterEqual(height, 1_550)
            self.assertLessEqual(height, 2_100)
            self.assertGreaterEqual(
                width / height,
                1.40,
            )
            self.assertLessEqual(
                width / height,
                1.60,
            )
            self.assertTrue(
                rendered["pdf"].read_bytes().startswith(b"%PDF")
            )


class AamosCanonicalArtifactTests(unittest.TestCase):
    def test_hidden_partial_files_cannot_enter_artifact_contract(self):
        runner = _load_runner()
        self.assertEqual(
            getattr(runner, "CANONICAL_ARTIFACT_FILENAMES", None),
            EXPECTED_ARTIFACT_FILENAMES,
        )
        resolver = getattr(
            runner, "_canonical_artifact_paths", None
        )
        self.assertTrue(
            callable(resolver),
            "runner requires a canonical artifact path resolver",
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in EXPECTED_ARTIFACT_FILENAMES:
                (root / name).write_text(name, encoding="utf-8")
            (root / ".attack_decisions.csv.partial").write_text(
                "partial", encoding="utf-8"
            )
            (root / "workspace-sync").mkdir()

            paths = resolver(root)
            self.assertEqual(
                tuple(path.name for path in paths),
                EXPECTED_ARTIFACT_FILENAMES,
            )
            self.assertTrue(all(path.is_file() for path in paths))

    def test_main_does_not_enumerate_the_staging_directory(self):
        runner = _load_runner()
        source = inspect.getsource(runner.main)
        self.assertIn("_canonical_artifact_paths(output)", source)
        self.assertNotIn("output.iterdir()", source)


if __name__ == "__main__":
    unittest.main()
