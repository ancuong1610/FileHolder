"""Scoring-shape tests for the synthetic mutation benchmark.

The benchmark must never present a vacuous perfect score.  A category with no
ground truth is ``not_applicable`` (precision/recall/F1 null), and a category
with no predictions is not allowed to claim recall.  Empty-truth categories
that only produced false positives are a tested failure, not a success.  The
overall pass stays a meaningful gate over the *supported* conditions, and a
coverage report names every uncovered category explicitly.
"""
import unittest

from document_compare.phase1.evaluation.evaluate import _score_mutation_case

CATEGORIES = ("modify", "delete", "insert", "move_block", "table_cell", "noise_hyphenation")
SUPPORT_FIELDS = ("tp", "fp", "fn", "truth_count", "prediction_count", "status")


def _canonical(changes=None, table_changes=None, review_queue=None):
    return {
        "changes": changes or [],
        "table_changes": table_changes or [],
        "review_queue": review_queue or [],
    }


def _modified(old_id, new_id="NEW-1"):
    return {
        "change_type": "MODIFIED",
        "change_id": f"MOD-{old_id}",
        "old": {"element_id": old_id, "text": "alpha"},
        "new": {"element_id": new_id, "text": "beta"},
    }


def _deleted(old_id):
    return {
        "change_type": "DELETED",
        "change_id": f"DEL-{old_id}",
        "old": {"element_id": old_id, "text": "alpha"},
        "new": None,
    }


def _added(new_id):
    return {
        "change_type": "ADDED",
        "change_id": f"ADD-{new_id}",
        "old": None,
        "new": {"element_id": new_id, "text": "beta"},
    }


def _modify_truth(*element_ids):
    return [{"type": "modify", "element_id": eid} for eid in element_ids]


def _table_cell_truth(*cell_ids):
    return [{"type": "table_cell", "cell_id": cid} for cid in cell_ids]


def _cell(old_id, new_id=None, classification="MODIFIED"):
    return {
        "classification": classification,
        "old": None if old_id is None else {"cell_id": old_id, "text": "alpha"},
        "new": None if new_id is None else {"cell_id": new_id, "text": "beta"},
    }


def _table_record(*cells):
    return {"cell_changes": list(cells)}


class SupportFieldsTest(unittest.TestCase):
    def test_every_category_exposes_support_fields_and_status(self):
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        for category in CATEGORIES:
            with self.subTest(category=category):
                block = result["summary"][category]
                for field in SUPPORT_FIELDS:
                    self.assertIn(field, block, (category, field, block))
                self.assertIn(block["status"], {"evaluated", "not_applicable", "false_positive"})
                self.assertIn("precision", block)
                self.assertIn("recall", block)
                self.assertIn("f1", block)


class UndefinedMetricTest(unittest.TestCase):
    def test_missing_category_is_not_a_vacuous_perfect_score(self):
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        delete = result["summary"]["delete"]
        self.assertEqual(delete["truth_count"], 0)
        self.assertEqual(delete["prediction_count"], 0)
        self.assertIsNone(delete["precision"])
        self.assertIsNone(delete["recall"])
        self.assertIsNone(delete["f1"])
        self.assertEqual(delete["status"], "not_applicable")

    def test_precision_is_null_when_category_has_truth_but_no_predictions(self):
        result = _score_mutation_case(_canonical([]), _modify_truth("X"))
        modify = result["summary"]["modify"]
        self.assertEqual(modify["tp"], 0)
        self.assertEqual(modify["fp"], 0)
        self.assertEqual(modify["fn"], 1)
        self.assertEqual(modify["truth_count"], 1)
        self.assertEqual(modify["prediction_count"], 0)
        self.assertIsNone(modify["precision"])
        self.assertEqual(modify["recall"], 0.0)
        self.assertIsNone(modify["f1"])
        self.assertEqual(modify["status"], "evaluated")

    def test_supported_perfect_category_scores_one(self):
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        modify = result["summary"]["modify"]
        self.assertEqual(modify["tp"], 1)
        self.assertEqual(modify["fp"], 0)
        self.assertEqual(modify["fn"], 0)
        self.assertEqual(modify["precision"], 1.0)
        self.assertEqual(modify["recall"], 1.0)
        self.assertEqual(modify["f1"], 1.0)
        self.assertEqual(modify["status"], "evaluated")
        self.assertTrue(result["passed"])


class EmptyTruthFalsePositiveTest(unittest.TestCase):
    def test_false_positive_with_empty_truth_is_a_tested_failure(self):
        result = _score_mutation_case(
            _canonical([_modified("X"), _deleted("Z")]), _modify_truth("X")
        )
        delete = result["summary"]["delete"]
        self.assertEqual(delete["truth_count"], 0)
        self.assertEqual(delete["fp"], 1)
        self.assertEqual(delete["status"], "false_positive")
        self.assertEqual(delete["precision"], 0.0)
        self.assertIsNone(delete["recall"])
        self.assertIsNone(delete["f1"])
        self.assertFalse(result["passed"])

    def test_noise_leak_fails_even_though_noise_category_has_truth(self):
        truth = [{"type": "noise_hyphenation", "element_id": "N1"}]
        result = _score_mutation_case(_canonical([_modified("N1")]), truth)
        noise = result["summary"]["noise_hyphenation"]
        self.assertEqual(noise["truth_count"], 1)
        self.assertEqual(noise["leaked_into_changes"], 1)
        self.assertFalse(result["passed"])


class FloorGateTest(unittest.TestCase):
    def test_modify_recall_floor_still_gates_supported_category(self):
        result = _score_mutation_case(
            _canonical([_modified("A"), _modified("B")]),
            _modify_truth("A", "B", "C", "D"),
        )
        self.assertEqual(result["summary"]["modify"]["recall"], 0.5)
        self.assertFalse(result["passed"])

    def test_uncovered_category_does_not_silently_count_as_a_failure(self):
        # The fixture genuinely has no delete truth; that is a coverage gap, not
        # a detection failure, and it must be named as such.
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        self.assertTrue(result["passed"])
        self.assertIn("delete", result["coverage"]["uncovered"])
        self.assertFalse(result["coverage"]["fully_covered"])


class CoverageReportTest(unittest.TestCase):
    def test_coverage_names_supported_and_uncovered_categories(self):
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        coverage = result["coverage"]
        self.assertIn("modify", coverage["supported"])
        for category in ("delete", "insert", "move_block", "table_cell", "noise_hyphenation"):
            with self.subTest(category=category):
                self.assertIn(category, coverage["uncovered"])
                entry = coverage["categories"][category]
                self.assertFalse(entry["covered"])
                self.assertEqual(entry["status"], "not_applicable")

    def test_coverage_flags_empty_truth_false_positive(self):
        result = _score_mutation_case(
            _canonical([_modified("X"), _deleted("Z")]), _modify_truth("X")
        )
        coverage = result["coverage"]
        self.assertIn("delete", coverage["false_positive"])
        self.assertIn("delete", coverage["uncovered"])


class TableCellMetricTest(unittest.TestCase):
    """The table condition is scored per unique ground-truth cell.

    Every injected cell that surfaces as a non-UNCHANGED cell change is one
    true positive; repeated records and a shared old/new cell id must not
    inflate the count, and unrelated or UNCHANGED cells earn no credit. The
    legacy any-hit ``hit`` bool stays, but the pass gate requires every
    supported cell to be detected.
    """

    def test_partial_cell_detection_counts_each_unique_ground_truth_cell(self):
        # 2 of 5 injected cells surfaced: tp=2, fn=3, recall=0.4, gate fails.
        table_changes = [
            _table_record(
                _cell("C1"),
                _cell("C2"),
                _cell("C3", classification="UNCHANGED"),
                _cell("C4", classification="UNCHANGED"),
                _cell("C5", classification="UNCHANGED"),
            )
        ]
        result = _score_mutation_case(
            _canonical(table_changes=table_changes),
            _table_cell_truth("C1", "C2", "C3", "C4", "C5"),
        )
        table = result["summary"]["table_cell"]
        self.assertEqual(table["tp"], 2)
        self.assertEqual(table["fn"], 3)
        self.assertEqual(table["truth_count"], 5)
        self.assertEqual(table["recall"], 0.4)
        # legacy any-hit compatibility: some cell surfaced...
        self.assertTrue(table["hit"])
        # ...but the all-cell gate must still fail.
        self.assertFalse(result["passed"])

    def test_all_cells_detected_scores_perfect_and_passes(self):
        table_changes = [_table_record(*[_cell(f"C{i}") for i in range(1, 6)])]
        result = _score_mutation_case(
            _canonical(table_changes=table_changes),
            _table_cell_truth("C1", "C2", "C3", "C4", "C5"),
        )
        table = result["summary"]["table_cell"]
        self.assertEqual(table["tp"], 5)
        self.assertEqual(table["fn"], 0)
        self.assertEqual(table["recall"], 1.0)
        self.assertTrue(table["hit"])
        self.assertTrue(result["passed"])

    def test_repeated_records_and_shared_old_new_ids_deduplicate(self):
        table_changes = [
            _table_record(_cell("C1")),
            _table_record(_cell("C1")),  # repeated record
            _table_record(_cell("C1", "C1")),  # old and new share one id
            _table_record(_cell("C2")),
        ]
        result = _score_mutation_case(
            _canonical(table_changes=table_changes),
            _table_cell_truth("C1", "C2"),
        )
        table = result["summary"]["table_cell"]
        self.assertEqual(table["tp"], 2)
        self.assertEqual(table["fn"], 0)
        self.assertEqual(table["recall"], 1.0)
        self.assertTrue(result["passed"])

    def test_unrelated_and_unchanged_cells_get_no_credit(self):
        table_changes = [
            _table_record(
                _cell("C1", classification="UNCHANGED"),
                _cell("C9"),  # not injected ground truth
            )
        ]
        result = _score_mutation_case(
            _canonical(table_changes=table_changes),
            _table_cell_truth("C1"),
        )
        table = result["summary"]["table_cell"]
        self.assertEqual(table["tp"], 0)
        self.assertEqual(table["fn"], 1)
        self.assertEqual(table["recall"], 0.0)
        self.assertFalse(table["hit"])
        self.assertFalse(result["passed"])

    def test_empty_table_support_stays_not_applicable_and_not_a_failure(self):
        result = _score_mutation_case(_canonical([_modified("X")]), _modify_truth("X"))
        table = result["summary"]["table_cell"]
        self.assertEqual(table["truth_count"], 0)
        self.assertEqual(table["status"], "not_applicable")
        self.assertFalse(table["hit"])
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
