import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from aamos_submission_fixture import submission_contract  # noqa: E402
from tarms_experiments import aamos_experiment  # noqa: E402
from tarms_experiments import aamos_protocol  # noqa: E402
from tarms_experiments import aamos_scenarios  # noqa: E402
from tarms_experiments import aamos_statistics  # noqa: E402
from tarms_experiments import plotting  # noqa: E402


class R4AdmissionContractTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            {
                "participant_id": ["P01", "P01"],
                "relative_day": [1, 2],
                "eligible": [True, True],
                "clean_priority": [0, 1],
                "payload_json": ['{"x":0}', '{"x":1}'],
            }
        )

    def test_same_digest_is_idempotent_but_different_digest_conflicts(self):
        current, profile = aamos_protocol.build_clean_history(
            self.frame, seed=7
        )[1]
        admitted = aamos_protocol.profile_after_acceptance(profile, current)

        idempotent = aamos_protocol.verify_envelope(
            current, admitted, aamos_protocol.ALL_CHECKS
        )
        self.assertTrue(idempotent.accepted)

        submitted, conflict_profile = aamos_scenarios.apply_scenario(
            current, profile, scenario="counter_conflict"
        )
        conflict = aamos_protocol.verify_envelope(
            submitted, conflict_profile, aamos_protocol.ALL_CHECKS
        )
        self.assertFalse(conflict.accepted)
        self.assertEqual(conflict.failure_stage, "admission")
        self.assertEqual(conflict.failure_reason, "counter_conflict")

    def test_r4_scenario_and_pipeline_identifiers_are_truthful(self):
        self.assertIn("counter_conflict", aamos_scenarios.REJECT_SCENARIOS)
        self.assertNotIn("replay_counter", aamos_scenarios.REJECT_SCENARIOS)
        self.assertIn(
            "idempotent_retransmission",
            aamos_scenarios.BOUNDARY_SCENARIOS,
        )
        self.assertIn("admission", aamos_protocol.ALL_CHECKS)
        self.assertNotIn("replay", aamos_protocol.ALL_CHECKS)
        self.assertIn("all_checks", aamos_experiment.PIPELINES)
        self.assertNotIn("full_tarms", aamos_experiment.PIPELINES)
        self.assertIn("all_minus_admission", aamos_experiment.PIPELINES)


class R4CrossedBootstrapContractTests(unittest.TestCase):
    def test_crossed_draw_shares_participant_multiplicities_across_seeds(self):
        self.assertEqual(
            aamos_statistics.BOOTSTRAP_METHOD,
            "crossed_seed_participant_multinomial",
        )
        seed_draws = [[0, 1]]
        participant_draws = [[0, 0]]
        weights = (
            aamos_statistics._multiplicities_from_crossed_occurrence_draws(
                seed_draws,
                participant_draws,
                seed_count=2,
                participant_count=2,
            )
        )
        self.assertEqual(weights.tolist(), [[[2, 0], [2, 0]]])


class R4FigureAndDatasetContractTests(unittest.TestCase):
    def test_expected_source_marks_equal_the_rendered_r4_contract(self):
        marks = plotting._aamos_expected_submission_marks()
        self.assertEqual(len(marks), 235)
        boundary_marks = {
            mark
            for mark in marks
            if mark[1] == "control_rejection"
        }
        self.assertEqual(len(boundary_marks), 7)
        self.assertEqual(
            {mark[4] for mark in boundary_marks},
            {"all_checks"},
        )

    def test_renderer_selections_consume_every_source_mark(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, source, _ = submission_contract(Path(directory))
        panel_a = plotting._aamos_panel_a_groups(
            source.loc[source["panel_id"] == "a"]
        )
        control_pipelines = plotting._aamos_panel_a_control_pipelines(
            panel_a["controls"]
        )
        consumed = len(panel_a["attacks"])
        consumed += len(
            panel_a["controls"].loc[
                panel_a["controls"]["pipeline"].isin(control_pipelines)
            ]
        )
        consumed += len(panel_a["clean"])
        panel_b = plotting._aamos_panel_b_layout(
            source.loc[source["panel_id"] == "b"]
        )
        consumed += len(panel_b["stage"])
        consumed += len(panel_b["matched_pipeline"])
        consumed += len(source.loc[source["panel_id"] == "c"])
        consumed += len(source.loc[source["panel_id"] == "d"])
        self.assertEqual(consumed, len(source))
        self.assertEqual(consumed, 235)

    def test_render_tracker_rejects_replaced_key_at_same_cardinality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, manifest_path, _, _ = submission_contract(root)
            original_register = plotting._AamosRenderTracker.register_row

            def replace_first_key(tracker, row):
                if not tracker.rendered_keys:
                    key = plotting._aamos_mark_key(row)
                    tracker.rendered_keys.add(("replaced", *key[1:]))
                else:
                    original_register(tracker, row)

            before = set(plotting.plt.get_fignums())
            with mock.patch.object(
                plotting._AamosRenderTracker,
                "register_row",
                new=replace_first_key,
            ):
                with self.assertRaisesRegex(
                    ValueError, "rendered AAMOS mark keys"
                ) as raised:
                    plotting.render_aamos_integrity_figure(
                        source_path,
                        manifest_path,
                        root / "figures",
                        submission=True,
                    )
            self.assertIn(
                "rendered=235, source=235",
                str(raised.exception),
            )
            self.assertEqual(set(plotting.plt.get_fignums()), before)

    def test_official_release_and_analysis_flow_are_distinct(self):
        config = yaml.safe_load(
            (
                PROJECT_ROOT / "config" / "aamos00_derivation.yaml"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            config["official_release"]["any_modality_participant_days"],
            2054,
        )
        expected = config["analysis_derivation_expectations"]
        self.assertEqual(expected["daily_questionnaire_participant_days"], 1583)
        self.assertEqual(expected["eligible_three_item_days"], 1582)
        self.assertNotIn("participant_days", config["official_release"])

    def test_reader_facing_labels_do_not_call_the_output_clinical_priority(self):
        self.assertEqual(
            plotting._aamos_panel_a_control_labels(
                ["incorrect_priority_rule"]
            ),
            ["Symptom-count rule error"],
        )
        self.assertEqual(
            plotting._aamos_pipeline_display_labels(
                ["signature_admission", "all_minus_signature"]
            ),
            ["signature + admission", "all checks − signature"],
        )


if __name__ == "__main__":
    unittest.main()
