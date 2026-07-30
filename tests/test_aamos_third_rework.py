import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from aamos_submission_fixture import submission_contract  # noqa: E402
from tarms_experiments import plotting  # noqa: E402
from tarms_experiments.aamos_experiment import (  # noqa: E402
    FIXED_SEEDS,
    run_standard_enhanced_experiment,
)
from tarms_experiments.aamos_statistics import (  # noqa: E402
    analyze_experiment,
    build_figure_source_data,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _small_frame() -> pd.DataFrame:
    rows = []
    for participant in range(4):
        for day in range(4):
            value = participant * 4 + day
            rows.append(
                {
                    "participant_id": f"P{participant}",
                    "relative_day": day,
                    "eligible": True,
                    "clean_priority": value % 4,
                    "payload_json": f'{{"row":{value}}}',
                }
            )
    return pd.DataFrame(rows)


class AamosThirdReworkGateTests(unittest.TestCase):
    def _write(
        self,
        source_path: Path,
        manifest_path: Path,
        source: pd.DataFrame,
        manifest: dict,
    ) -> None:
        source.to_csv(source_path, index=False)
        manifest["artifacts"][source_path.name] = _sha256(
            source_path
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _assert_rejected(self, mutate) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                submission_contract(root)
            )
            mutate(source, manifest)
            self._write(
                source_path, manifest_path, source, manifest
            )
            with self.assertRaises(ValueError):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_identity_and_design_columns_reject_one_row_na(self):
        cases = (
            ("run_id", "attack_rejection"),
            ("code_commit_or_archive_hash", "attack_rejection"),
            ("metric_definition_version", "attack_rejection"),
            ("derivation_config_basename", "attack_rejection"),
            ("derivation_config_file_sha256", "attack_rejection"),
            (
                "derivation_config_canonical_sha256",
                "attack_rejection",
            ),
            ("dataset_name", "attack_rejection"),
            ("dataset_doi", "attack_rejection"),
            (
                "dataset_source_inventory_sha256",
                "attack_rejection",
            ),
            ("bootstrap_method", "attack_rejection"),
            ("bootstrap_interval_type", "attack_rejection"),
            ("bootstrap_master_seed", "attack_rejection"),
            (
                "bootstrap_repetitions_requested",
                "attack_rejection",
            ),
            ("bootstrap_repetitions_valid", "attack_rejection"),
            (
                "bootstrap_repetitions_discarded",
                "attack_rejection",
            ),
            ("seed_count", "attack_rejection"),
            ("seed_scope", "attack_rejection"),
            ("execution_count_scope", "attack_rejection"),
            ("denominator_unit", "covered_agreement"),
        )
        for column, metric in cases:
            with self.subTest(column=column):
                def mutate(source, manifest, *, field=column, name=metric):
                    row = source.index[
                        source["metric_id"].eq(name)
                    ][0]
                    source.loc[row, field] = pd.NA

                self._assert_rejected(mutate)

    def test_metric_specific_fields_reject_missing_values(self):
        cases = (
            ("scenario", "attack_rejection"),
            ("comparison_type", "pipeline_risk_difference"),
            ("comparator_pipeline", "pipeline_risk_difference"),
            ("both_reject_n", "pipeline_risk_difference"),
            ("attack_only_reject_n", "pipeline_risk_difference"),
            ("clean_only_reject_n", "pipeline_risk_difference"),
            ("neither_reject_n", "pipeline_risk_difference"),
        )
        for column, metric in cases:
            with self.subTest(column=column):
                def mutate(source, manifest, *, field=column, name=metric):
                    row = source.index[
                        source["metric_id"].eq(name)
                    ][0]
                    source.loc[row, field] = pd.NA

                self._assert_rejected(mutate)

    def test_primary_mark_dimensions_reject_missing_values(self):
        for column in (
            "panel_id",
            "metric_id",
            "rate_requested",
            "pipeline",
        ):
            with self.subTest(column=column):
                def mutate(source, manifest, *, field=column):
                    source.loc[source.index[0], field] = pd.NA

                self._assert_rejected(mutate)

    def test_fractional_source_counts_are_rejected(self):
        cases = (
            (
                "fractional bootstrap partition",
                lambda source: (
                    source.__setitem__(
                        "bootstrap_repetitions_valid", 1_999.5
                    ),
                    source.__setitem__(
                        "bootstrap_repetitions_discarded", 0.5
                    ),
                ),
            ),
            (
                "fractional seed count",
                lambda source: source.__setitem__(
                    "seed_count", 19.5
                ),
            ),
        )
        for label, change in cases:
            with self.subTest(case=label):
                self._assert_rejected(
                    lambda source, manifest, update=change: update(
                        source
                    )
                )

    def test_fractional_manifest_seed_and_repetitions_are_rejected(self):
        def fractional_repetitions(source, manifest):
            manifest["design"]["bootstrap"][
                "repetitions"
            ] = 2_000.5
            manifest["environment"][
                "bootstrap_repetitions"
            ] = 2_000.5

        def fractional_master_seed(source, manifest):
            manifest["design"]["bootstrap"][
                "master_seed"
            ] = 20_260_722.5
            manifest["environment"][
                "bootstrap_master_seed"
            ] = 20_260_722.5

        def fractional_injection_seed(source, manifest):
            seeds = list(FIXED_SEEDS)
            seeds[-1] = float(seeds[-1]) + 0.5
            manifest["design"]["seeds"] = seeds
            manifest["environment"]["injection_seeds"] = seeds

        for label, mutate in (
            ("bootstrap repetitions", fractional_repetitions),
            ("bootstrap master seed", fractional_master_seed),
            ("injection seed", fractional_injection_seed),
        ):
            with self.subTest(case=label):
                self._assert_rejected(mutate)

    def test_fixed_twenty_seed_contract_is_required_everywhere(self):
        def missing_design_seeds(source, manifest):
            manifest["design"].pop("seeds")

        def wrong_design_seeds(source, manifest):
            manifest["design"]["seeds"] = list(FIXED_SEEDS[:-1])

        def missing_environment_seeds(source, manifest):
            manifest["environment"].pop("injection_seeds")

        def wrong_environment_seeds(source, manifest):
            manifest["environment"][
                "injection_seeds"
            ] = list(FIXED_SEEDS[:-1])

        def wrong_source_seed_count(source, manifest):
            source["seed_count"] = len(FIXED_SEEDS) - 1

        def wrong_seed_scope(source, manifest):
            source["seed_scope"] = "single_injection_seed"

        def wrong_execution_scope(source, manifest):
            source[
                "execution_count_scope"
            ] = "single_injection_seed"

        for label, mutate in (
            ("missing design seeds", missing_design_seeds),
            ("wrong design seeds", wrong_design_seeds),
            ("missing environment seeds", missing_environment_seeds),
            ("wrong environment seeds", wrong_environment_seeds),
            ("wrong source seed count", wrong_source_seed_count),
            ("wrong source seed scope", wrong_seed_scope),
            ("wrong source execution scope", wrong_execution_scope),
        ):
            with self.subTest(case=label):
                self._assert_rejected(mutate)

    def test_manifest_derivation_must_exist_and_match_design(self):
        def missing_derivation(source, manifest):
            manifest.pop("derivation")

        def contradictory_hashes(source, manifest):
            manifest["derivation"][
                "derivation_config_file_sha256"
            ] = "0" * 64
            manifest["derivation"][
                "derivation_config_canonical_sha256"
            ] = "0" * 64

        def contradictory_legacy_hash(source, manifest):
            manifest["derivation"][
                "config_canonical_sha256"
            ] = "0" * 64

        def contradictory_member(source, manifest):
            manifest["derivation"][
                "derivation_config_member"
            ] = "config/other.yaml"

        def false_derivation_flag(source, manifest):
            manifest["derivation"][
                "fixed_submission_config"
            ] = False

        for label, mutate in (
            ("missing derivation", missing_derivation),
            ("contradictory config hashes", contradictory_hashes),
            (
                "contradictory legacy canonical hash",
                contradictory_legacy_hash,
            ),
            ("contradictory config member", contradictory_member),
            ("false derivation fixed flag", false_derivation_flag),
        ):
            with self.subTest(case=label):
                self._assert_rejected(mutate)

    def test_controlled_member_count_requires_a_true_positive_integer(self):
        for value in (True, 85.0):
            with self.subTest(value=value):
                self._assert_rejected(
                    lambda source, manifest, count=value: manifest[
                        "controlled_source"
                    ].__setitem__("member_count", count)
                )

    def test_plotting_manifest_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, _ = submission_contract(root)
            raw = manifest_path.read_text(encoding="utf-8")
            raw = raw.replace(
                '  "run_id": "submission-run",',
                '  "note": "/home/alice/private",\n'
                '  "note": "safe",\n'
                '  "run_id": "submission-run",',
                1,
            )
            manifest_path.write_text(raw, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate|JSON"):
                plotting._validate_aamos_submission_source(
                    source_path, manifest_path, source
                )

    def test_valid_strict_fixture_renders_submission_figure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, _ = (
                submission_contract(root)
            )
            output = root / "figures"
            rendered = plotting.render_aamos_integrity_figure(
                source_path,
                manifest_path,
                output,
                submission=True,
            )
            self.assertEqual(len(source), 235)
            self.assertTrue(rendered["pdf"].is_file())
            self.assertTrue(rendered["png"].is_file())

    def test_composite_bypass_is_rejected_by_full_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, source, manifest = (
                submission_contract(root)
            )
            identity_row = source.index[
                source["metric_id"].eq("attack_rejection")
            ][0]
            for column in (
                "derivation_config_file_sha256",
                "dataset_doi",
                "code_commit_or_archive_hash",
                "metric_definition_version",
                "run_id",
                "bootstrap_method",
                "seed_count",
                "seed_scope",
                "execution_count_scope",
            ):
                source.loc[identity_row, column] = pd.NA
            source[
                "bootstrap_repetitions_valid"
            ] = 1_999.5
            source[
                "bootstrap_repetitions_discarded"
            ] = 0.5
            mechanism_row = source.index[
                source["metric_id"].eq(
                    "pipeline_risk_difference"
                )
            ][0]
            source.loc[
                mechanism_row, "comparison_type"
            ] = pd.NA
            covered_row = source.index[
                source["metric_id"].eq("covered_agreement")
            ][0]
            source.loc[covered_row, "denominator_unit"] = pd.NA
            manifest["derivation"][
                "derivation_config_file_sha256"
            ] = "0" * 64
            manifest["derivation"][
                "fixed_submission_config"
            ] = False
            manifest["design"].pop("seeds")
            manifest["environment"].pop("injection_seeds")
            self._write(
                source_path, manifest_path, source, manifest
            )
            with self.assertRaises(ValueError):
                plotting.render_aamos_integrity_figure(
                    source_path,
                    manifest_path,
                    root / "tampered-figures",
                    submission=True,
                )

    def test_generated_figure_source_preserves_comparison_type(self):
        tables = run_standard_enhanced_experiment(
            _small_frame(),
            attack_scenarios=("binding_mismatch",),
            boundary_scenarios=("canonical_reorder",),
            rates=(0.25,),
            seeds=(11, 12),
            boundary_rate=0.25,
        )
        analysis = analyze_experiment(
            tables,
            repetitions=20,
            master_seed=17,
            run_id="third-rework-source",
        )
        source = build_figure_source_data(
            analysis.summary,
            analysis.paired_contrasts,
            run_id="third-rework-source",
            created_utc="2026-07-23T00:00:00Z",
            code_commit_or_archive_hash="code-hash",
            bootstrap_master_seed=17,
            derivation_config_identity={
                "derivation_config_basename": "fixture.yaml",
                "derivation_config_file_sha256": "f" * 64,
                "derivation_config_canonical_sha256": "c" * 64,
            },
            dataset_identity={
                "dataset_name": "AAMOS-00",
                "dataset_doi": "10.7488/ds/3775",
                "dataset_source_inventory_sha256": "d" * 64,
            },
        )
        self.assertIn("comparison_type", source.columns)
        mechanism = source.loc[
            source["metric_id"].eq("pipeline_risk_difference")
        ]
        self.assertFalse(mechanism.empty)
        self.assertTrue(
            mechanism["comparison_type"].eq("matched_pipeline").all()
        )


if __name__ == "__main__":
    unittest.main()
