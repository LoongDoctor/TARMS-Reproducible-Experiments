import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tarms_experiments.plotting import (  # noqa: E402
    FABRIC_INSTALL_OPERATION,
    SIGNATURE_ADMISSION_TITLE,
    SIGNATURE_ADMISSION_YLABEL,
    model_ledger_bytes,
    model_window_tradeoff,
    render_component_conformance_figure,
    render_fabric_performance_figure,
    render_late_update_figure,
    render_python_benchmark_figure,
    render_window_tradeoff_figure,
)
from tarms_experiments.provenance import (  # noqa: E402
    EvidenceGateError,
    RunManifest,
    write_manifest,
)
from tarms_experiments.component_conformance import run_component_conformance  # noqa: E402
from tarms_experiments.stats import summarize_observations  # noqa: E402


class PlottingGateTests(unittest.TestCase):
    def test_fabric_figures_use_the_atomic_install_operation_name(self):
        self.assertEqual(FABRIC_INSTALL_OPERATION, "InstallAnchorCAS")

    def test_signature_admission_panel_uses_claim_calibrated_labels(self):
        self.assertEqual(SIGNATURE_ADMISSION_TITLE, "Signature + admission throughput")
        self.assertIn("Signature + admission", SIGNATURE_ADMISSION_YLABEL)

    def _observation_table(self):
        rows = []
        stages = [
            "sign_batch",
            "verify_batch",
            "merkle_build",
            "proof_verify",
            "signature_admission_batch",
            "late_rebuild",
        ]
        for batch in (16, 64):
            for repetition in range(4):
                for offset, stage in enumerate(stages, start=1):
                    rows.append(
                        {
                            "run_id": "plot-r1",
                            "seed": 1,
                            "batch_size": batch,
                            "repetition": repetition,
                            "stage": stage,
                            "duration_ns": batch * offset * 1_000 + repetition,
                            "record_count": batch,
                            "late_count": max(1, batch // 32),
                            "provenance": "measured",
                        }
                    )
        return pd.DataFrame(rows)

    def test_fixture_manifest_is_rejected_before_rendering_submission_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            write_manifest(
                RunManifest(
                    experiment="python",
                    provenance="fixture",
                    run_id="fixture-r1",
                    created_at="2026-07-22T00:00:00Z",
                    environment={"python": "3.12"},
                    artifacts={},
                ),
                manifest_path,
            )
            with self.assertRaisesRegex(EvidenceGateError, "fixture"):
                render_python_benchmark_figure(
                    root / "missing.csv",
                    root / "missing-summary.csv",
                    manifest_path,
                    root / "figures",
                    submission=True,
                )

    def test_measured_figure_exports_pdf_png_and_source_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self._observation_table()
            summary = summarize_observations(raw, bootstrap_reps=50, seed=1)
            raw_path = root / "raw.csv"
            summary_path = root / "summary.csv"
            manifest_path = root / "manifest.json"
            raw.to_csv(raw_path, index=False)
            summary.to_csv(summary_path, index=False)
            write_manifest(
                RunManifest(
                    experiment="python",
                    provenance="measured",
                    run_id="plot-r1",
                    created_at="2026-07-22T00:00:00Z",
                    environment={"python": "3.12"},
                    artifacts={},
                ),
                manifest_path,
            )

            outputs = render_python_benchmark_figure(
                raw_path, summary_path, manifest_path, root / "figures", submission=True
            )

            self.assertTrue(outputs["pdf"].is_file())
            self.assertTrue(outputs["png"].is_file())
            self.assertTrue(outputs["source_data"].is_file())
            self.assertGreater(outputs["png"].stat().st_size, 10_000)
            source = pd.read_csv(outputs["source_data"])
            self.assertIn("signature_admission_throughput", set(source["dataset"]))
            self.assertNotIn("accept_throughput", set(source["dataset"]))

    def test_modeled_ledger_bytes_are_explicit_and_monotone(self):
        modeled = model_ledger_bytes([16, 64, 4096])
        pivot = modeled.pivot(index="batch_size", columns="strategy", values="bytes")

        self.assertTrue(pivot["raw records"].is_monotonic_increasing)
        self.assertTrue(pivot["hash per record"].is_monotonic_increasing)
        self.assertGreater(pivot.loc[4096, "raw records"], pivot.loc[4096, "hash per record"])
        self.assertGreater(pivot.loc[4096, "hash per record"], pivot.loc[4096, "TARMS anchor"])
        tarms_rows = modeled.loc[modeled["strategy"] == "TARMS anchor"]
        self.assertEqual(set(tarms_rows["anchor_version"].dropna()), {1})
        anchor = {
            "aid": "a" * 64,
            "kappa": "patient-000001|2026-07-22T00:00Z",
            "version": 1,
            "root": "b" * 64,
            "prevAid": "d" * 64,
            "recordCount": 4096,
            "uriHash": "c" * 64,
            "createdAt": "2026-07-22T00:00:00Z",
        }
        latest = {
            "kappa": anchor["kappa"],
            "aid": anchor["aid"],
            "version": 1,
            "root": anchor["root"],
        }
        expected = len(json.dumps(anchor, separators=(",", ":")).encode()) + len(
            json.dumps(latest, separators=(",", ":")).encode()
        )
        self.assertEqual(pivot.loc[4096, "TARMS anchor"], expected)
        self.assertTrue(tarms_rows["assumption"].str.contains("uriHash").all())

    def test_window_tradeoff_model_is_monotone_and_exports_source_data(self):
        modeled = model_window_tradeoff([1, 5, 10, 30, 60], anchor_bytes=399)

        self.assertTrue(modeled["modeled_kib_day"].is_monotonic_decreasing)
        self.assertTrue(modeled["mean_batching_wait_s"].is_monotonic_increasing)
        self.assertEqual(modeled.loc[modeled["window_min"] == 60, "anchors_day"].iloc[0], 24)
        with tempfile.TemporaryDirectory() as directory:
            outputs = render_window_tradeoff_figure(Path(directory), anchor_bytes=399)
            self.assertTrue(outputs["pdf"].is_file())
            self.assertTrue(outputs["png"].is_file())
            self.assertTrue(outputs["source_data"].is_file())

    def test_generated_pdf_omits_wall_clock_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = render_window_tradeoff_figure(Path(directory), anchor_bytes=614)

            pdf_bytes = outputs["pdf"].read_bytes()

            self.assertNotIn(b"/CreationDate", pdf_bytes)
            self.assertNotIn(b"/ModDate", pdf_bytes)

    def test_component_conformance_figure_exports_matrix_and_source_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "components.csv"
            manifest_path = root / "manifest.json"
            run_component_conformance(repetitions=2, seed=3).to_csv(raw_path, index=False)
            write_manifest(
                RunManifest(
                    experiment="python",
                    provenance="measured",
                    run_id="components-r1",
                    created_at="2026-07-22T00:00:00Z",
                    environment={"repetitions_per_case": 2},
                    artifacts={},
                ),
                manifest_path,
            )

            outputs = render_component_conformance_figure(
                raw_path, manifest_path, root / "figures", submission=True
            )

            self.assertTrue(outputs["pdf"].is_file())
            self.assertTrue(outputs["png"].is_file())
            source = pd.read_csv(outputs["source_data"])
            self.assertEqual(set(source["proportion_matching"]), {1.0})

    def test_fabric_fixture_manifest_fails_before_data_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fabric_run = root / "fabric" / "run-1"
            fabric_run.mkdir(parents=True)
            write_manifest(
                RunManifest(
                    experiment="fabric",
                    provenance="fixture",
                    run_id="fabric-fixture",
                    created_at="2026-07-22T00:00:00Z",
                    environment={"concurrency": 1},
                    artifacts={},
                ),
                fabric_run / "run_manifest.json",
            )
            with self.assertRaisesRegex(EvidenceGateError, "fixture"):
                render_fabric_performance_figure(root / "fabric", root / "figures", submission=True)

            python_manifest = root / "python_manifest.json"
            write_manifest(
                RunManifest(
                    experiment="python",
                    provenance="measured",
                    run_id="python-measured",
                    created_at="2026-07-22T00:00:00Z",
                    environment={"python": "3.12"},
                    artifacts={},
                ),
                python_manifest,
            )
            with self.assertRaisesRegex(EvidenceGateError, "fixture"):
                render_late_update_figure(
                    root / "missing-python.csv",
                    python_manifest,
                    root / "fabric",
                    root / "figures",
                    submission=True,
                )

if __name__ == "__main__":
    unittest.main()
