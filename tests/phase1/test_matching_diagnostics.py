"""Task 5 tests: candidate-retrieval vs ranking vs selection diagnostics.

These tests exercise three independent layers:

1. ``score_pair_trace`` is a pure funnel scorer.  Synthetic trace records are
   fed directly so each failure label (retrieval, shortlist, scoring,
   selection) is distinguishable and every denominator is explicit.
2. The synthetic labeled fixture is run through the real s07 algorithm via the
   existing import-safe helper (no workspace seeding, no import side effects on
   the test process, no mocked matcher core).  Truth pair IDs were authored
   before execution.
3. Diagnostics off/on must return byte-identical canonical results because the
   collector is opt-in and must not change matching behaviour.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from document_compare.phase1.evaluation.matching_diagnostics import (
    default_experiment_matrix,
    load_fixture,
    run_harness,
    run_traced_build,
    score_pair_trace,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "regression" / "matching_pairs.json"


def _event(stage, old, new, **fields):
    record = {"stage": stage, "old_element_id": old, "new_element_id": new}
    record.update(fields)
    return record


def _truth(old, new, disposition=None):
    return {
        "old_element_id": old,
        "new_element_id": new,
        "expected_disposition": disposition,
    }


def _correct_trace(old, new, disposition=None):
    trace = [
        _event("retrieved", old, new, retrieval_method="shared_provisional_context", candidate_phase="primary"),
        _event("shortlisted", old, new, retrieval_method="shared_provisional_context", candidate_phase="primary"),
        _event("scored", old, new, score=0.9, passed_review_floor=True),
        _event("selected", old, new, score=0.9, minimum_best_margin=0.2),
    ]
    if disposition is not None:
        trace.append(_event("classified", old, new, disposition=disposition, needs_review=False))
    return trace


class ScorePairTraceStageTest(unittest.TestCase):
    """Each funnel stage must be distinguishable from the next."""

    def test_lost_retrieval(self):
        result = score_pair_trace([], [_truth("O1", "N1")])
        retrieval = result["stages"]["retrieval"]
        self.assertEqual(retrieval["numerator"], 0)
        self.assertEqual(retrieval["denominator"], 1)
        self.assertEqual(retrieval["recall"], 0.0)
        self.assertEqual(retrieval["status"], "evaluated")
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "lost_retrieval")

    def test_lost_shortlist(self):
        trace = [_event("retrieved", "O1", "N1", retrieval_method="shared_provisional_context", candidate_phase="primary")]
        result = score_pair_trace(trace, [_truth("O1", "N1")])
        self.assertEqual(result["stages"]["retrieval"]["numerator"], 1)
        self.assertEqual(result["stages"]["shortlist"]["numerator"], 0)
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "lost_shortlist")

    def test_lost_scoring(self):
        trace = [
            _event("retrieved", "O1", "N1", retrieval_method="rare_token_fallback", candidate_phase="primary"),
            _event("shortlisted", "O1", "N1", retrieval_method="rare_token_fallback", candidate_phase="primary"),
            _event("scored", "O1", "N1", score=0.4, passed_review_floor=False),
        ]
        result = score_pair_trace(trace, [_truth("O1", "N1")])
        self.assertEqual(result["stages"]["shortlist"]["numerator"], 1)
        self.assertEqual(result["stages"]["scoring"]["numerator"], 0)
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "lost_scoring")

    def test_lost_selection(self):
        trace = [
            _event("retrieved", "O1", "N1"),
            _event("shortlisted", "O1", "N1"),
            _event("scored", "O1", "N1", score=0.9, passed_review_floor=True),
        ]
        result = score_pair_trace(trace, [_truth("O1", "N1")])
        self.assertEqual(result["stages"]["scoring"]["numerator"], 1)
        self.assertEqual(result["stages"]["selection"]["numerator"], 0)
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "lost_selection")

    def test_correct_pairing_reaches_every_stage(self):
        result = score_pair_trace(
            _correct_trace("O1", "N1", "modified_alignment_candidate"),
            [_truth("O1", "N1", "modified_alignment_candidate")],
        )
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "correct_pairing")
        for stage in ("retrieval", "shortlist", "scoring", "selection"):
            with self.subTest(stage=stage):
                self.assertEqual(result["stages"][stage]["numerator"], 1)
                self.assertEqual(result["stages"][stage]["recall"], 1.0)
        classification = result["stages"]["classification"]
        self.assertEqual(classification["numerator"], 1)
        self.assertEqual(classification["denominator"], 1)

    def test_empty_truth_is_not_applicable(self):
        result = score_pair_trace(_correct_trace("O1", "N1"), [])
        for stage in ("retrieval", "shortlist", "scoring", "selection", "classification"):
            with self.subTest(stage=stage):
                block = result["stages"][stage]
                self.assertEqual(block["status"], "not_applicable")
                self.assertIsNone(block["recall"])
        self.assertEqual(result["pair_precision_recall"]["status"], "not_applicable")

    def test_classification_not_applicable_without_expected_disposition(self):
        result = score_pair_trace(
            _correct_trace("O1", "N1", "modified_alignment_candidate"),
            [_truth("O1", "N1")],
        )
        classification = result["stages"]["classification"]
        self.assertEqual(classification["status"], "not_applicable")
        self.assertIsNone(classification["recall"])
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "correct_pairing")

    def test_classification_mismatch_is_counted(self):
        result = score_pair_trace(
            _correct_trace("O1", "N1", "modified_alignment_candidate"),
            [_truth("O1", "N1", "modified_alignment_review")],
        )
        classification = result["stages"]["classification"]
        self.assertEqual(classification["numerator"], 0)
        self.assertEqual(classification["denominator"], 1)
        self.assertEqual(classification["recall"], 0.0)

    def test_missing_counterpart_is_not_reported_as_success(self):
        result = score_pair_trace(_correct_trace("O1", "N1"), [_truth("O2", "N2")])
        self.assertEqual(result["stages"]["retrieval"]["numerator"], 0)
        self.assertEqual(result["pair_outcomes"][0]["outcome"], "lost_retrieval")

    def test_pair_precision_penalises_extra_selected_pairs(self):
        trace = _correct_trace("O1", "N1") + _correct_trace("O2", "N2")
        result = score_pair_trace(trace, [_truth("O1", "N1")])
        pair = result["pair_precision_recall"]
        self.assertEqual(pair["selected_count"], 2)
        self.assertEqual(pair["matched_count"], 1)
        self.assertEqual(pair["precision"], 0.5)
        self.assertEqual(pair["recall"], 1.0)


class FixtureTraceIntegrationTest(unittest.TestCase):
    """Run the real s07 algorithm against the authored synthetic fixture."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture(FIXTURE)
        cls.result, cls.trace = run_traced_build(cls.fixture["exact"])
        cls.scored = score_pair_trace(cls.trace, cls.fixture["truth_pairs"])
        cls.outcomes = {
            (item["old_element_id"], item["new_element_id"]): item["outcome"]
            for item in cls.scored["pair_outcomes"]
        }

    def test_authored_truth_outcomes(self):
        expected = {
            ("OLD-PARA-001", "NEW-PARA-001"): "correct_pairing",
            ("OLD-HEAD-001", "NEW-HEAD-001"): "correct_pairing",
            ("OLD-LOST-001", "NEW-LOST-001"): "lost_retrieval",
            ("OLD-SCORE-001", "NEW-SCORE-001"): "lost_scoring",
            ("OLD-AMB-001", "NEW-AMB-001"): "correct_pairing",
            ("OLD-CAP-001", "NEW-CAP-TRUE-001"): "lost_shortlist",
            ("OLD-RFLOOR-001", "NEW-RFLOOR-001"): "correct_pairing",
            ("OLD-AUTO-001", "NEW-AUTO-001"): "correct_pairing",
            ("OLD-MAR-001", "NEW-MAR-A"): "correct_pairing",
            ("OLD-FALL-001", "NEW-FALL-TOKEN"): "correct_pairing",
        }
        for pair, outcome in expected.items():
            with self.subTest(pair=pair):
                self.assertEqual(self.outcomes[pair], outcome)

    def test_default_stage_counts(self):
        stages = self.scored["stages"]
        self.assertEqual((stages["retrieval"]["numerator"], stages["retrieval"]["denominator"]), (9, 10))
        self.assertEqual((stages["shortlist"]["numerator"], stages["shortlist"]["denominator"]), (8, 10))
        self.assertEqual((stages["scoring"]["numerator"], stages["scoring"]["denominator"]), (7, 10))
        self.assertEqual((stages["selection"]["numerator"], stages["selection"]["denominator"]), (7, 10))
        self.assertEqual((stages["classification"]["numerator"], stages["classification"]["denominator"]), (7, 7))

    def test_default_pair_precision_recall(self):
        pair = self.scored["pair_precision_recall"]
        self.assertEqual(pair["selected_count"], 8)
        self.assertEqual(pair["matched_count"], 7)
        self.assertAlmostEqual(pair["precision"], 7 / 8)
        self.assertAlmostEqual(pair["recall"], 7 / 10)

    def test_traces_have_ids_and_stages_but_no_text(self):
        forbidden = {"text", "normalized_text", "comparison_text", "old", "new", "similarity_evidence"}
        for record in self.trace:
            for key in forbidden:
                self.assertNotIn(key, record, record)
            self.assertIn("stage", record)
            self.assertIn("old_element_id", record)

    def test_below_floor_scoring_is_recorded(self):
        below = [
            record
            for record in self.trace
            if record["stage"] == "scored" and record["new_element_id"] == "NEW-SCORE-001"
        ]
        self.assertTrue(below)
        self.assertFalse(below[0]["passed_review_floor"])

    def test_true_cap_counterpart_is_retrieved_but_not_shortlisted(self):
        retrieved = {
            record["new_element_id"]
            for record in self.trace
            if record["stage"] == "retrieved" and record["old_element_id"] == "OLD-CAP-001"
        }
        shortlisted = {
            record["new_element_id"]
            for record in self.trace
            if record["stage"] == "shortlisted" and record["old_element_id"] == "OLD-CAP-001"
        }
        self.assertIn("NEW-CAP-TRUE-001", retrieved)
        self.assertNotIn("NEW-CAP-TRUE-001", shortlisted)

    def test_context_then_token_fallback_is_recorded(self):
        fallback = [
            record
            for record in self.trace
            if record["stage"] == "retrieved"
            and record.get("candidate_phase") == "fallback"
            and record["new_element_id"] == "NEW-FALL-TOKEN"
        ]
        self.assertTrue(fallback)
        self.assertEqual(fallback[0]["retrieval_method"], "rare_token_fallback")

    def test_primary_token_fallback_method_is_recorded(self):
        head = [
            record
            for record in self.trace
            if record["stage"] == "retrieved" and record["new_element_id"] == "NEW-HEAD-001"
        ]
        self.assertTrue(head)
        self.assertEqual(head[0]["retrieval_method"], "rare_token_fallback")

    def test_diagnostics_off_and_on_are_semantically_equal(self):
        off_result, _ = run_traced_build(self.fixture["exact"], collect=False)
        self.assertEqual(off_result, self.result)

    def test_classified_dispositions_are_recorded(self):
        dispositions = {
            (record["old_element_id"], record["new_element_id"]): record["disposition"]
            for record in self.trace
            if record["stage"] == "classified"
        }
        self.assertEqual(dispositions[("OLD-PARA-001", "NEW-PARA-001")], "modified_alignment_candidate")
        self.assertEqual(dispositions[("OLD-AMB-001", "NEW-AMB-001")], "modified_alignment_review")
        self.assertEqual(dispositions[("OLD-CAP-001", "NEW-CAP-001")], "duplicate_exact_review")


class ThresholdOverrideTest(unittest.TestCase):
    """In-memory overrides must actually reach the algorithm."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture(FIXTURE)

    def _score(self, overrides):
        _, trace = run_traced_build(self.fixture["exact"], overrides=overrides)
        return score_pair_trace(trace, self.fixture["truth_pairs"])

    def test_override_is_restored_after_each_call(self):
        default_before, _ = run_traced_build(self.fixture["exact"])
        self._score({"MIN_REVIEW_SCORE": 0.74})
        default_after, _ = run_traced_build(self.fixture["exact"])
        self.assertEqual(default_before, default_after)

    def test_review_floor_override_changes_scoring_stage(self):
        default = self._score({})
        raised = self._score({"MIN_REVIEW_SCORE": 0.74})
        self.assertEqual(default["stages"]["scoring"]["numerator"], 7)
        self.assertEqual(raised["stages"]["scoring"]["numerator"], 6)
        self.assertEqual(default["stages"]["classification"]["numerator"], 7)
        self.assertEqual(raised["stages"]["classification"]["numerator"], 6)

    def test_automatic_threshold_override_changes_classification(self):
        raised = self._score({"MIN_AUTOMATIC_SCORE": 0.84})
        self.assertEqual(raised["stages"]["selection"]["numerator"], 7)
        self.assertEqual(raised["stages"]["classification"]["numerator"], 6)

    def test_margin_override_changes_classification(self):
        raised = self._score({"MIN_AUTOMATIC_MARGIN": 0.12})
        self.assertEqual(raised["stages"]["selection"]["numerator"], 7)
        self.assertEqual(raised["stages"]["classification"]["numerator"], 6)


class ExperimentMatrixTest(unittest.TestCase):
    def test_matrix_is_one_factor_at_a_time(self):
        matrix = default_experiment_matrix()
        review_values = {item["overrides"].get("MIN_REVIEW_SCORE") for item in matrix if item["factor"] == "review"}
        automatic_values = {
            item["overrides"].get("MIN_AUTOMATIC_SCORE") for item in matrix if item["factor"] == "automatic"
        }
        margin_values = {item["overrides"].get("MIN_AUTOMATIC_MARGIN") for item in matrix if item["factor"] == "margin"}
        self.assertEqual(review_values, {0.70, 0.72, 0.74})
        self.assertEqual(automatic_values, {0.84, 0.86, 0.88})
        self.assertEqual(margin_values, {0.08, 0.10, 0.12})
        for item in matrix:
            self.assertLessEqual(len(item["overrides"]), 1)


class HarnessTest(unittest.TestCase):
    def test_harness_writes_report_and_refuses_overwrite(self):
        import tempfile

        experiments = default_experiment_matrix()[:1]
        with tempfile.TemporaryDirectory(prefix="crr-diag-harness-") as directory:
            root = Path(directory) / "root"
            report_path = run_harness(root, FIXTURE, experiments=experiments)
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["synthetic"])
            self.assertEqual(len(report["experiments"]), 1)
            with self.assertRaises(SystemExit):
                run_harness(root, FIXTURE, experiments=experiments)

    def test_harness_does_not_modify_production_defaults(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="crr-diag-defaults-") as directory:
            root = Path(directory) / "root"
            run_harness(root, FIXTURE, experiments=default_experiment_matrix()[:1])
        source = (ROOT / "src" / "document_compare" / "phase1" / "stages" / "s07_modified_matching.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MIN_REVIEW_SCORE = 0.72", source)
        self.assertIn("MIN_AUTOMATIC_SCORE = 0.86", source)
        self.assertIn("MIN_AUTOMATIC_MARGIN = 0.10", source)


if __name__ == "__main__":
    unittest.main()
