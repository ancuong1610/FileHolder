"""TDD contract tests for the controlled performance benchmark.

The benchmark must report TWO explicit streams and never blur them:

* stream A — asserted/accepted change correctness.  A true positive is an
  *accepted* prediction matching the exact declared ``(kind, old_id, new_id)``
  (or ``(classification, old_cell_id, new_cell_id)``).  Every other declared
  truth is a false negative, so ``tp + fn == truth_count`` always holds.  A
  review-only (unresolved) detection is NOT a true positive and remains a false
  negative for asserted-change recall.
* stream B — useful review routing.  ``surfaced_for_review`` counts declared
  truths a review-only prediction matched; ``never_surfaced`` counts truths no
  prediction touched at all.  A separate, clearly labelled
  ``detection_including_review`` may be shown, but it never resolves a case and
  never changes recall.

The tests also pin: duplicate predictions are not collapsed, wrong pairs/kinds
and unrelated table-cell changes are false positives, truth ids are validated
against the input identities, zero-truth categories are null (never a vacuous
perfect score), the noise condition is a proportion with a null denominator,
and the output bundle refuses to overwrite or nest inside the input artifacts.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from document_compare.phase1.evaluation import performance_benchmark as pb
from document_compare.phase1.evaluation.performance_benchmark import (
    benchmark_performance_command,
    render_html,
    score_case,
    validate_truth,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _canonical(changes=None, table_changes=None, review_queue=None):
    return {
        "changes": changes or [],
        "table_changes": table_changes or [],
        "review_queue": review_queue or [],
    }


def _change(change_type, old_id, new_id, *, accepted=True, old_text="alpha", new_text="beta"):
    resolution = {
        "status": "accepted" if accepted else "review",
        "needs_review": not accepted,
    }
    return {
        "change_type": change_type,
        "change_id": f"CHG-{change_type}-{old_id}-{new_id}",
        "old": None if old_id is None else {"element_id": old_id, "text": old_text},
        "new": None if new_id is None else {"element_id": new_id, "text": new_text},
        "resolution": resolution,
    }


def _cell(classification, old_cell, new_cell, *, needs_review=False, old_text="alpha", new_text="beta"):
    return {
        "classification": classification,
        "old": None if old_cell is None else {"cell_id": old_cell, "text": old_text},
        "new": None if new_cell is None else {"cell_id": new_cell, "text": new_text},
        "needs_review": needs_review,
    }


def _table_record(*cells, root_status="accepted", root_needs_review=False):
    return {
        "cell_changes": list(cells),
        "resolution": {"status": root_status, "needs_review": root_needs_review},
    }


def _truth(case_id, category, kind, *, old_id=None, new_id=None, table_element_id=None,
           old_cell_id=None, new_cell_id=None, before_text="alpha", after_text="beta"):
    return {
        "case_id": case_id,
        "category": category,
        "kind": kind,
        "old_id": old_id,
        "new_id": new_id,
        "table_element_id": table_element_id,
        "old_cell_id": old_cell_id,
        "new_cell_id": new_cell_id,
        "before_text": before_text,
        "after_text": after_text,
        "synthetic": True,
        "benchmark_only_provenance": True,
    }


def _modify_truth(case_id="word-0001", element_id="OLD-P1"):
    return _truth(case_id, "word", "modify", old_id=element_id, new_id=element_id)


def _delete_truth(case_id="delete-0001", element_id="OLD-P1"):
    return _truth(case_id, "delete", "delete", old_id=element_id, new_id=None)


def _insert_truth(case_id="insert-0001", element_id="BENCH-INS-0001"):
    return _truth(case_id, "insert", "insert", old_id=None, new_id=element_id)


def _noise_truth(case_id="noise-0001", element_id="OLD-P1"):
    return _truth(case_id, "noise", "noise", old_id=element_id, new_id=None)


def _cell_truth(case_id="table_cell-0001", cell_id="OLD-T1-C1", table_id="OLD-T1"):
    return _truth(
        case_id,
        "table_cell",
        "table_cell",
        table_element_id=table_id,
        old_cell_id=cell_id,
        new_cell_id=cell_id,
    )


# ---------------------------------------------------------------------------
# Stream A: asserted/accepted exact-pair correctness
# ---------------------------------------------------------------------------


class AssertedChangeContractTest(unittest.TestCase):
    def test_accepted_exact_pair_is_the_only_true_positive(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)
        self.assertEqual(block["fp"], 0)
        self.assertEqual(block["recall"], 1.0)
        self.assertEqual(block["tp"] + block["fn"], block["truth_count"])

    def test_wrong_pair_counts_false_positive_and_false_negative(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P9")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["tp"] + block["fn"], block["truth_count"])

    def test_wrong_kind_counts_false_positive_and_false_negative(self):
        block = score_case(
            _canonical([_change("DELETED", "OLD-P1", None)]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)

    def test_duplicate_predictions_are_not_collapsed(self):
        block = score_case(
            _canonical(
                [
                    _change("MODIFIED", "OLD-P1", "OLD-P1"),
                    _change("MODIFIED", "OLD-P1", "OLD-P1"),
                ]
            ),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 0)

    def test_unrelated_prediction_is_a_false_positive(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-X", "OLD-X")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["unmodified_false_positives"], 1)
        self.assertEqual(block["fn"], 1)

    def test_delete_truth_requires_matching_deleted_record(self):
        block = score_case(
            _canonical([_change("DELETED", "OLD-P1", None)]),
            [_delete_truth()],
            category="delete",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)

    def test_insert_truth_requires_matching_added_record(self):
        block = score_case(
            _canonical([_change("ADDED", None, "BENCH-INS-0001")]),
            [_insert_truth()],
            category="insert",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)

    def test_truth_support_conservation_across_element_categories(self):
        scenarios = [
            ("word", [_change("MODIFIED", "OLD-P1", "OLD-P1")], [_modify_truth()]),
            ("delete", [_change("DELETED", "OLD-P1", None)], [_delete_truth()]),
            ("insert", [_change("ADDED", None, "BENCH-INS-0001")], [_insert_truth()]),
        ]
        for category, changes, truth in scenarios:
            with self.subTest(category=category):
                block = score_case(
                    _canonical(changes), truth, category=category, elapsed_seconds=0.0
                )
                self.assertEqual(
                    block["tp"] + block["fn"], block["truth_count"], block
                )


# ---------------------------------------------------------------------------
# Raw canonical text payload: an exact pair is not enough on its own
# ---------------------------------------------------------------------------


class RawTextPayloadContractTest(unittest.TestCase):
    """A prediction matches a truth only when every declared side's raw text is exact.

    The pair (kind + ids) and the accepted state remain required, but a wrong raw
    text payload with the same ids is a false positive AND a false negative, never
    a true positive.  A side the truth does not declare (the absent ``new`` side of
    a deletion, the absent ``old`` side of an insertion) has no expected text and
    its unused prediction text is ignored.
    """

    def test_accepted_same_ids_wrong_actual_payload_is_fp_and_fn(self):
        truth = _truth(
            "numeric-0001", "numeric", "modify", old_id="P1", new_id="P1",
            before_text="Limit: 20", after_text="Limit: 21",
        )
        block = score_case(
            _canonical(
                [_change("MODIFIED", "P1", "P1", old_text="Limit: 20", new_text="Limit: 99")]
            ),
            [truth],
            category="numeric",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["f1"], 0.0)
        self.assertFalse(block["ok"])
        self.assertEqual(block["wrong_payload_mismatches"], 1)
        self.assertEqual(block["wrong_pair_mismatches"], 0)
        self.assertEqual(block["never_surfaced_touched_wrong"], 1)
        self.assertEqual(block["never_surfaced_truly_absent"], 0)
        example = block["wrong_payload_examples"][0]
        self.assertEqual(
            example["expected_payload"],
            {"before_text": "Limit: 20", "after_text": "Limit: 21"},
        )
        self.assertEqual(
            example["observed_payload"],
            {"old_text": "Limit: 20", "new_text": "Limit: 99"},
        )
        self.assertIn("payload", example["reason"])

    def test_accepted_same_ids_wrong_old_payload_is_fp_and_fn(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1", old_text="gamma")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["wrong_payload_mismatches"], 1)

    def test_tablecell_same_pair_wrong_payload_is_fp_and_fn(self):
        block = score_case(
            _canonical(
                table_changes=[
                    _table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1", new_text="WRONG"))
                ]
            ),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["wrong_payload_mismatches"], 1)
        self.assertEqual(block["unexpected_unmodified_cells"], 0)
        self.assertIn("payload", block["wrong_payload_examples"][0]["reason"])

    def test_delete_ignores_absent_new_side_text(self):
        block = score_case(
            _canonical([_change("DELETED", "OLD-P1", None)]),
            [_delete_truth()],
            category="delete",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)
        self.assertEqual(block["wrong_payload_mismatches"], 0)

    def test_delete_wrong_old_text_is_fp_and_fn(self):
        block = score_case(
            _canonical([_change("DELETED", "OLD-P1", None, old_text="WRONG")]),
            [_delete_truth()],
            category="delete",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["wrong_payload_mismatches"], 1)

    def test_insert_ignores_absent_old_side_text(self):
        block = score_case(
            _canonical([_change("ADDED", None, "BENCH-INS-0001")]),
            [_insert_truth()],
            category="insert",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)
        self.assertEqual(block["wrong_payload_mismatches"], 0)

    def test_insert_wrong_new_text_is_fp_and_fn(self):
        block = score_case(
            _canonical([_change("ADDED", None, "BENCH-INS-0001", new_text="WRONG")]),
            [_insert_truth()],
            category="insert",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["wrong_payload_mismatches"], 1)

    def test_review_only_wrong_payload_is_not_useful_review(self):
        block = score_case(
            _canonical(
                [_change("MODIFIED", "OLD-P1", "OLD-P1", accepted=False, new_text="WRONG")]
            ),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["surfaced_for_review"], 0)
        self.assertEqual(block["review_only_correct"], 0)
        self.assertEqual(block["never_surfaced"], 1)
        self.assertEqual(block["review_suggestions"], 1)
        self.assertEqual(block["fp"], 0)
        self.assertFalse(block["ok"])

    def test_wrong_pair_and_wrong_payload_are_separate_counters(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P9")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["wrong_pair_mismatches"], 1)
        self.assertEqual(block["wrong_payload_mismatches"], 0)
        self.assertEqual(block["never_surfaced_touched_wrong"], 1)


class EvaluationScopeTest(unittest.TestCase):
    def test_version_and_evaluation_scope_are_recorded(self):
        self.assertEqual(pb.BENCHMARK_VERSION, "1.1.0")
        report = pb.build_report(
            manifest={"path": "x", "sha256": "y"},
            seed=42,
            config_fingerprint="deadbeef",
            identity={"ok": True, "element_changes": 0, "table_changes": 0,
                      "false_alarms": 0, "elapsed_seconds": 0.0, "review_queue_composition": {}},
            categories={},
            elapsed_seconds=0.0,
            artifacts=Path("/tmp/artifacts"),
        )
        self.assertEqual(report["benchmark_version"], "1.1.0")
        self.assertEqual(report["evaluation_scope"], "canonical-pair-kind-raw-text")
        self.assertIn("NOT measured", report["evaluation_scope_note"])


# ---------------------------------------------------------------------------
# Stream B: review routing is separate and never inflates recall
# ---------------------------------------------------------------------------


class ReviewStreamContractTest(unittest.TestCase):
    def test_review_only_match_cannot_inflate_asserted_recall(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1", accepted=False)]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["recall"], 0.0)
        self.assertEqual(block["review_only_correct"], 1)
        self.assertEqual(block["surfaced_for_review"], 1)
        self.assertEqual(block["never_surfaced"], 0)
        self.assertEqual(block["tp"] + block["fn"], block["truth_count"])

    def test_never_surfaced_is_a_genuine_miss_not_review(self):
        block = score_case(
            _canonical([]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["surfaced_for_review"], 0)
        self.assertEqual(block["never_surfaced"], 1)

    def test_detection_including_review_is_separate_and_does_not_resolve(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1", accepted=False)]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["detection_including_review"], 1)
        self.assertEqual(block["recall"], 0.0)
        note = block["detection_including_review_note"].lower()
        self.assertIn("riêng", note)
        self.assertIn("không tự động giải quyết", note)

    def test_review_and_never_partition_the_false_negatives(self):
        truth = [
            _truth("word-0001", "word", "modify", old_id="OLD-P1", new_id="OLD-P1"),
            _truth("word-0002", "word", "modify", old_id="OLD-P2", new_id="OLD-P2"),
        ]
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1", accepted=False)]),
            truth,
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["surfaced_for_review"], 1)
        self.assertEqual(block["never_surfaced"], 1)
        self.assertEqual(block["surfaced_for_review"] + block["never_surfaced"], block["fn"])


# ---------------------------------------------------------------------------
# Table-cell contract
# ---------------------------------------------------------------------------


class TableCellContractTest(unittest.TestCase):
    def test_accepted_exact_cell_pair_is_a_true_positive(self):
        block = score_case(
            _canonical(table_changes=[_table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1"))]),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fn"], 0)
        self.assertEqual(block["tp"] + block["fn"], block["truth_count"])

    def test_unrelated_cell_change_is_a_false_positive(self):
        block = score_case(
            _canonical(table_changes=[_table_record(_cell("MODIFIED", "OLD-T9-C9", "OLD-T9-C9"))]),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["unexpected_unmodified_cells"], 1)

    def test_wrong_cell_classification_is_a_false_positive(self):
        block = score_case(
            _canonical(table_changes=[_table_record(_cell("DELETED", "OLD-T1-C1", None))]),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["fn"], 1)

    def test_review_only_cell_does_not_inflate_recall(self):
        block = score_case(
            _canonical(
                table_changes=[_table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1", needs_review=True))]
            ),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fn"], 1)
        self.assertEqual(block["recall"], 0.0)
        self.assertEqual(block["surfaced_for_review"], 1)


# ---------------------------------------------------------------------------
# Noise condition
# ---------------------------------------------------------------------------


class NoiseConditionTest(unittest.TestCase):
    def test_suppressed_noise_scores_proportion_with_null_precision(self):
        block = score_case(
            _canonical([]),
            [_noise_truth()],
            category="noise",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["truth_count"], 1)
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["fp"], 0)
        self.assertEqual(block["recall"], 1.0)
        self.assertIsNone(block["precision"])

    def test_leaked_noise_is_a_false_positive(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P1")]),
            [_noise_truth()],
            category="noise",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["fp"], 1)
        self.assertEqual(block["recall"], 0.0)
        self.assertEqual(block["changed_records_touching_noise"], 1)

    def test_zero_noise_truth_is_not_a_vacuous_perfect_score(self):
        block = score_case(_canonical([]), [], category="noise", elapsed_seconds=0.0)
        self.assertEqual(block["truth_count"], 0)
        self.assertIsNone(block["recall"])
        self.assertIsNone(block["precision"])
        self.assertIsNone(block["f1"])
        self.assertEqual(block["status"], "not_applicable")


# ---------------------------------------------------------------------------
# Zero-truth categories stay null
# ---------------------------------------------------------------------------


class ZeroTruthTest(unittest.TestCase):
    def test_zero_truth_with_no_predictions_is_not_applicable(self):
        block = pb._metric_block(tp=0, fp=0, fn=0, truth_count=0, prediction_count=0)
        self.assertIsNone(block["precision"])
        self.assertIsNone(block["recall"])
        self.assertIsNone(block["f1"])
        self.assertEqual(block["status"], "not_applicable")

    def test_zero_truth_with_false_positive_is_flagged(self):
        block = pb._metric_block(tp=0, fp=1, fn=0, truth_count=0, prediction_count=1)
        self.assertEqual(block["precision"], 0.0)
        self.assertIsNone(block["recall"])
        self.assertEqual(block["status"], "false_positive")


# ---------------------------------------------------------------------------
# Truth validation: duplicate ids, missing refs, input identities
# ---------------------------------------------------------------------------


class TruthValidationTest(unittest.TestCase):
    def setUp(self):
        self.base_ids = {"OLD-P1", "OLD-P2"}
        self.new_ids = {"OLD-P1", "OLD-P2", "BENCH-INS-0001"}
        self.base_cell_ids = {"OLD-T1-C1"}

    def _validate(self, truth):
        validate_truth(
            truth,
            base_ids=self.base_ids,
            new_ids=self.new_ids,
            base_cell_ids=self.base_cell_ids,
        )

    def test_valid_truth_passes(self):
        self._validate([_modify_truth(), _insert_truth(), _cell_truth()])

    def test_duplicate_case_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self._validate([_modify_truth("word-0001"), _modify_truth("word-0001", "OLD-P2")])

    def test_duplicate_tuple_is_rejected(self):
        with self.assertRaises(ValueError):
            self._validate(
                [
                    _truth("word-0001", "word", "modify", old_id="OLD-P1", new_id="OLD-P1"),
                    _truth("word-0002", "word", "modify", old_id="OLD-P1", new_id="OLD-P1"),
                ]
            )

    def test_missing_old_ref_is_rejected(self):
        with self.assertRaises(ValueError):
            self._validate([_modify_truth(element_id="OLD-MISSING")])

    def test_missing_new_ref_is_rejected(self):
        with self.assertRaises(ValueError):
            self._validate([_insert_truth(element_id="BENCH-MISSING")])

    def test_missing_cell_ref_is_rejected(self):
        with self.assertRaises(ValueError):
            self._validate([_cell_truth(cell_id="OLD-T9-C9")])


# ---------------------------------------------------------------------------
# Case construction: disjoint ids, real mutations, support accounting
# ---------------------------------------------------------------------------


def _tiny_base_model():
    paragraphs = [
        {
            "element_id": f"OLD-P{i:05d}",
            "element_type": "paragraph",
            "comparison_eligible": True,
            "text": (
                f"Absatz {i} beschreibt ein ausreichend langes Beispielwort "
                "mit Zahlen 2024 und weiteren Begriffen für den Vergleich."
            ),
            "reading_order": i,
        }
        for i in range(1, 6)
    ]
    table = {
        "element_id": "OLD-T0001",
        "element_type": "table",
        "comparison_eligible": True,
        "text": "tabelle",
        "reading_order": 6,
        "table": {
            "row_count": 2,
            "column_count": 1,
            "cells": [
                {"cell_id": "OLD-T0001-C0001", "row_index": 0, "column_index": 0, "text": "Kopf"},
                {
                    "cell_id": "OLD-T0001-C0002",
                    "row_index": 1,
                    "column_index": 0,
                    "text": "Ein ausreichend langer Zellentext mit Begriffen",
                },
            ],
        },
    }
    return {"document": {"document_id": "DOC-OLD", "elements": paragraphs + [table]}}


class CaseConstructionTest(unittest.TestCase):
    def test_word_case_mutates_data_and_records_support(self):
        model = _tiny_base_model()
        new_model, truth, support = pb.build_case(model, "word", seed=42, count=3)
        self.assertGreaterEqual(len(truth), 1)
        self.assertEqual(support["actual"], len(truth))
        old_by_id = {e["element_id"]: e for e in model["document"]["elements"]}
        for entry in truth:
            new_el = next(
                e for e in new_model["document"]["elements"] if e["element_id"] == entry["old_id"]
            )
            self.assertNotEqual(new_el["text"], old_by_id[entry["old_id"]]["text"])
        ids = [entry["old_id"] for entry in truth]
        self.assertEqual(len(ids), len(set(ids)))

    def test_noise_case_wraps_a_long_word(self):
        model = _tiny_base_model()
        new_model, truth, _support = pb.build_case(model, "noise", seed=42, count=2)
        for entry in truth:
            new_el = next(
                e for e in new_model["document"]["elements"] if e["element_id"] == entry["old_id"]
            )
            self.assertIn("-\n", new_el["text"])

    def test_truth_passes_validation_against_built_model(self):
        model = _tiny_base_model()
        for category in ("word", "numeric", "delete", "insert", "noise", "table_cell"):
            with self.subTest(category=category):
                new_model, truth, _support = pb.build_case(model, category, seed=42, count=3)
                base_ids = {e["element_id"] for e in model["document"]["elements"]}
                new_ids = {e["element_id"] for e in new_model["document"]["elements"]}
                base_cells = {
                    c["cell_id"]
                    for e in model["document"]["elements"]
                    for c in ((e.get("table") or {}).get("cells") or [])
                }
                validate_truth(
                    truth, base_ids=base_ids, new_ids=new_ids, base_cell_ids=base_cells
                )


# ---------------------------------------------------------------------------
# Output safety
# ---------------------------------------------------------------------------


class OutputSafetyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="perf-bench-safety-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "old_document.json").write_text(
            json.dumps(_tiny_base_model()), encoding="utf-8"
        )

    def test_output_nested_inside_artifacts_is_refused(self):
        with self.assertRaises(ValueError):
            benchmark_performance_command(
                artifacts=self.artifacts, out=self.artifacts / "nested", seed=42
            )

    def test_output_equal_to_artifacts_is_refused(self):
        with self.assertRaises(ValueError):
            benchmark_performance_command(
                artifacts=self.artifacts, out=self.artifacts, seed=42
            )

    def test_populated_output_directory_is_refused(self):
        out = self.base / "out"
        out.mkdir()
        (out / "existing.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            benchmark_performance_command(artifacts=self.artifacts, out=out, seed=42)
        self.assertTrue((out / "existing.txt").is_file())


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


def _minimal_report(payload: str) -> dict:
    block = {
        "category": "word",
        "truth_count": 1,
        "tp": 0,
        "fn": 1,
        "fp": 0,
        "review_only_correct": 0,
        "surfaced_for_review": 0,
        "never_surfaced": 1,
        "detection_including_review": 0,
        "precision": None,
        "recall": 0.0,
        "f1": None,
        "status": "evaluated",
        "elapsed_seconds": 0.1,
        "ok": False,
        "review_pending": False,
        "small_sample": False,
        "false_positive_examples": [],
        "miss_examples": [
            {
                "expected": {"kind": "modify", "old_id": payload},
                "old_text": payload,
                "new_text": payload,
                "reason": payload,
            }
        ],
    }
    return {
        "conclusion": {
            "ok": False,
            "supported_categories": ["word"],
            "unscored_categories": [],
            "failing_categories": ["word"],
            "small_sample_categories": [],
            "review_pending_categories": [],
            "identity_ok": True,
            "note": "x",
        },
        "categories": {"word": block},
        "identity": {
            "element_changes": 0,
            "table_changes": 0,
            "false_alarms": 0,
            "ok": True,
            "elapsed_seconds": 0.1,
            "review_queue_composition": {},
        },
        "source": {"path": payload, "sha256": payload},
        "seed": 42,
        "config_fingerprint": "deadbeef",
        "python_hash_seed": "0",
        "interpreter": "/usr/bin/python3",
        "python_version": "3.11.0",
        "elapsed_seconds": 0.1,
        "before_after": pb.BEFORE_AFTER_NOTE,
        "move_note": pb.MOVE_NOTE,
    }


class HtmlEscapingTest(unittest.TestCase):
    def test_document_text_is_escaped(self):
        payload = "<script>alert(1)</script>"
        html = render_html(_minimal_report(payload))
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class TextSummaryTest(unittest.TestCase):
    def test_text_summary_reports_both_streams_per_case(self):
        text = pb.render_text_summary(_minimal_report("x"))
        self.assertIn("LUỒNG A", text)
        self.assertIn("LUỒNG B", text)
        self.assertIn("bỏ sót(fn)", text)
        self.assertIn("không hề xuất hiện", text)
        self.assertIn("không tự giải quyết", text)
        self.assertIn(pb.BEFORE_AFTER_NOTE, text)


# ---------------------------------------------------------------------------
# Cross-stream false positives must never be silently ignored
# ---------------------------------------------------------------------------


class CrossStreamTest(unittest.TestCase):
    def test_element_case_counts_unexpected_table_change(self):
        block = score_case(
            _canonical(
                [_change("MODIFIED", "OLD-P1", "OLD-P1")],
                table_changes=[_table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1"))],
            ),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["scoped_false_positives"], 0)
        self.assertEqual(block["cross_stream_accepted_fp"], 1)
        self.assertEqual(block["fp"], 1)
        self.assertFalse(block["ok"])
        self.assertEqual(block["precision"], 0.5)

    def test_table_case_counts_unexpected_element_change(self):
        block = score_case(
            _canonical(
                [_change("MODIFIED", "OLD-X", "OLD-X")],
                table_changes=[_table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1"))],
            ),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 1)
        self.assertEqual(block["cross_stream_accepted_fp"], 1)
        self.assertEqual(block["fp"], 1)
        self.assertFalse(block["ok"])

    def test_cross_stream_review_suggestion_is_labelled_not_accepted_fp(self):
        block = score_case(
            _canonical(
                [_change("MODIFIED", "OLD-P1", "OLD-P1")],
                table_changes=[
                    _table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1", needs_review=True))
                ],
            ),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["fp"], 0)
        self.assertEqual(block["cross_stream_review_suggestions"], 1)
        self.assertEqual(block["review_suggestions"], 1)
        self.assertFalse(block["ok"])


# ---------------------------------------------------------------------------
# Noise verdict must fail on any unexpected change, not only noise touches
# ---------------------------------------------------------------------------


class NoiseUnrelatedAlarmTest(unittest.TestCase):
    def test_unrelated_element_change_fails_noise_verdict(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-Z", "OLD-Z")]),
            [_noise_truth()],
            category="noise",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["changed_records_touching_noise"], 0)
        self.assertEqual(block["unexpected_unmodified_element_changes"], 1)
        self.assertEqual(block["fp"], 1)
        self.assertFalse(block["ok"])

    def test_unexpected_table_change_fails_noise_verdict(self):
        block = score_case(
            _canonical(table_changes=[_table_record(_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1"))]),
            [_noise_truth()],
            category="noise",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["unexpected_table_changes"], 1)
        self.assertEqual(block["fp"], 1)
        self.assertFalse(block["ok"])


# ---------------------------------------------------------------------------
# Never-surfaced label distinguishes wrong-touch from truly absent
# ---------------------------------------------------------------------------


class WrongTouchLabelTest(unittest.TestCase):
    def test_wrong_pair_is_touched_wrong_not_truly_absent(self):
        block = score_case(
            _canonical([_change("MODIFIED", "OLD-P1", "OLD-P9")]),
            [_modify_truth()],
            category="word",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["never_surfaced"], 1)
        self.assertEqual(block["never_surfaced_touched_wrong"], 1)
        self.assertEqual(block["never_surfaced_truly_absent"], 0)
        self.assertIn("sai cặp", block["miss_examples"][0]["reason"])

    def test_no_prediction_at_all_is_truly_absent(self):
        block = score_case(_canonical([]), [_modify_truth()], category="word", elapsed_seconds=0.0)
        self.assertEqual(block["never_surfaced"], 1)
        self.assertEqual(block["never_surfaced_touched_wrong"], 0)
        self.assertEqual(block["never_surfaced_truly_absent"], 1)
        self.assertIn("không có dự đoán nào chạm tới", block["miss_examples"][0]["reason"])


# ---------------------------------------------------------------------------
# Table acceptance requires the ROOT record to be accepted
# ---------------------------------------------------------------------------


class RootTableReviewTest(unittest.TestCase):
    def test_root_review_with_clean_cell_is_not_accepted(self):
        block = score_case(
            _canonical(
                table_changes=[
                    _table_record(
                        _cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1", needs_review=False),
                        root_status="review",
                        root_needs_review=True,
                    )
                ]
            ),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["review_only_correct"], 1)
        self.assertEqual(block["surfaced_for_review"], 1)
        self.assertEqual(block["recall"], 0.0)
        self.assertEqual(block["tp"] + block["fn"], block["truth_count"])

    def test_absent_root_resolution_is_not_accepted(self):
        record = {"cell_changes": [_cell("MODIFIED", "OLD-T1-C1", "OLD-T1-C1")]}
        block = score_case(
            _canonical(table_changes=[record]),
            [_cell_truth()],
            category="table_cell",
            elapsed_seconds=0.0,
        )
        self.assertEqual(block["tp"], 0)
        self.assertEqual(block["review_only_correct"], 1)


# ---------------------------------------------------------------------------
# Report config: fingerprint is not a hash seed
# ---------------------------------------------------------------------------


class ReportConfigTest(unittest.TestCase):
    def test_report_records_config_fingerprint_and_real_python_hash_seed(self):
        report = pb.build_report(
            manifest={"path": "x", "sha256": "y"},
            seed=42,
            config_fingerprint="deadbeef",
            identity={"ok": True, "element_changes": 0, "table_changes": 0,
                      "false_alarms": 0, "elapsed_seconds": 0.0, "review_queue_composition": {}},
            categories={},
            elapsed_seconds=0.0,
            artifacts=Path("/tmp/artifacts"),
        )
        self.assertEqual(report["config_fingerprint"], "deadbeef")
        self.assertNotIn("hashseed", report)
        self.assertIn("python_hash_seed", report)
        self.assertIn("interpreter", report)
        self.assertIn("python_version", report)


# ---------------------------------------------------------------------------
# Plain-language headline derives from category blocks and excludes noise
# ---------------------------------------------------------------------------


def _cat_block(name, *, truth, tp, surfaced=0, never=0, fp=0, ok=True,
               review_pending=False, small_sample=False, supported=True):
    return {
        "category": name,
        "truth_count": truth,
        "tp": tp,
        "fn": truth - tp,
        "fp": fp,
        "review_only_correct": surfaced,
        "surfaced_for_review": surfaced,
        "never_surfaced": never,
        "detection_including_review": tp + surfaced,
        "precision": None,
        "recall": None,
        "f1": None,
        "status": "evaluated",
        "elapsed_seconds": 0.1,
        "ok": ok,
        "review_pending": review_pending,
        "small_sample": small_sample,
        "supported": supported,
        "false_positive_examples": [],
        "miss_examples": [],
        "review_examples": [],
        "review_suggestion_examples": [],
        "unexpected_cross_stream": 0,
        "review_suggestions": 0,
    }


def _headline_report(**overrides):
    blocks = {
        "word": _cat_block("word", truth=20, tp=20),
        "numeric": _cat_block("numeric", truth=10, tp=10),
        "delete": _cat_block("delete", truth=10, tp=0, surfaced=10, ok=False, review_pending=True),
        "insert": _cat_block("insert", truth=10, tp=0, surfaced=10, ok=False, review_pending=True),
        "table_cell": _cat_block("table_cell", truth=5, tp=5, small_sample=True),
        "noise": _cat_block("noise", truth=10, tp=10),
    }
    blocks.update(overrides)
    return {
        "conclusion": {
            "ok": False,
            "supported_categories": ["word", "numeric", "delete", "insert", "table_cell", "noise"],
            "unscored_categories": [],
            "failing_categories": [],
            "review_pending_categories": ["delete", "insert"],
            "small_sample_categories": ["table_cell"],
            "identity_ok": True,
            "note": "x",
        },
        "categories": blocks,
        "identity": {"ok": True, "element_changes": 0, "table_changes": 0,
                     "false_alarms": 0, "elapsed_seconds": 0.1, "review_queue_composition": {}},
        "source": {"path": "p", "sha256": "s"},
        "seed": 42,
        "config_fingerprint": "deadbeef",
        "python_hash_seed": "0",
        "interpreter": "/usr/bin/python3",
        "python_version": "3.11.0",
        "elapsed_seconds": 0.1,
        "before_after": pb.BEFORE_AFTER_NOTE,
        "move_note": pb.MOVE_NOTE,
    }


class HeadlineSummaryTest(unittest.TestCase):
    def test_totals_exclude_noise_and_cover_semantic_scope(self):
        headline = pb._headline_summary(_headline_report())
        self.assertEqual(headline["semantic_truth_count"], 55)
        self.assertEqual(headline["semantic_accepted"], 35)
        self.assertEqual(headline["semantic_review"], 20)
        self.assertEqual(headline["semantic_surfaced"], 55)
        self.assertEqual(headline["accepted_percent"], 63.6)
        self.assertEqual(headline["review_percent"], 36.4)
        self.assertEqual(headline["surfaced_percent"], 100.0)
        self.assertEqual(headline["noise_total"], 10)
        self.assertEqual(headline["noise_suppressed"], 10)

    def test_noise_truth_never_enters_semantic_totals(self):
        report = _headline_report(noise=_cat_block("noise", truth=999, tp=999))
        headline = pb._headline_summary(report)
        self.assertEqual(headline["semantic_truth_count"], 55)
        self.assertEqual(headline["semantic_accepted"], 35)

    def test_problems_are_surfaced_not_masked(self):
        report = _headline_report(word=_cat_block("word", truth=20, tp=19, fp=1, ok=False))
        headline = pb._headline_summary(report)
        self.assertTrue(any("báo nhầm" in problem for problem in headline["problems"]))

    def test_headline_text_states_scope_and_noise_wording(self):
        report = _headline_report()
        text = pb.render_text_summary(report)
        self.assertIn("55", text)
        self.assertIn("63.6%", text)
        self.assertIn("KHÔNG phải độ chính xác trên tài liệu thật", text)
        self.assertIn("xuống dòng được bỏ qua đúng", text)
        self.assertNotIn("true positives accepted", text)
        html = render_html(report)
        self.assertIn("xuống dòng được bỏ qua đúng", html)


if __name__ == "__main__":
    unittest.main()
