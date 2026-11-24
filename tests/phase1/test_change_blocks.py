"""Packet 6 regression coverage using German comparison text."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from document_compare.phase1.matching.impact import classify_impact
from document_compare.phase1.reporting.report_model import build_report_model
from document_compare.phase1.stages import s07_modified_matching as s07
from document_compare.phase1.stages import s08_candidates, s14_canonical_result
from tests.phase1.test_automatic_reports import _element, _empty_tables, _routing


def _body(side, order, element_type="paragraph"):
    return _element(
        f"{side}-{order}", "Der Bericht muss vorliegen.", order=order,
        page=1 + order // 5, element_type=element_type,
    )


def _candidate(group, side, order, element_type="paragraph"):
    element = _body(side, order, element_type)
    element["extraction_validation"]["requires_extraction_review"] = True
    return {"candidate_id": f"{group}-{order}", side: element}


def _compare(deleted=(), added=(), unchanged=(), *, tables=None, structure_dir=None, modified=(),
             residual_groups=()):
    movement = {
        "residual_proposal_groups": list(residual_groups),
        "comparison_stage": s14_canonical_result.EXPECTED_STEP9_STAGE,
        "deleted": list(deleted),
        "added": list(added),
        "unchanged": list(unchanged),
        "modified": list(modified),
    }
    tables = _empty_tables() if tables is None else tables
    return s14_canonical_result.build_comparison_result(
        movement, tables, _routing(movement, tables), structure_dir=structure_dir,
    )


class ChangeBlockTests(unittest.TestCase):
    def test_deleted_section_keeps_all_members_and_queues_one_case(self):
        deleted = [_candidate("D", "old", i, "heading" if i == 2 else "paragraph")
                   for i in range(2, 8)]
        surviving = {"old": _body("old", 1, "heading"), "new": _body("new", 1, "heading")}
        surviving["old"]["text"] = "Geltungsbereich"
        result = _compare(deleted[::-1], unchanged=[surviving])
        self.assertEqual(len(result["changes"]), 6)
        self.assertEqual(result["statistics"]["changes_in_blocks"], 6)
        block, = result["change_blocks"]
        self.assertEqual(block["block_id"], "BLOCK-000001")
        self.assertEqual(block["side"], "old")
        self.assertEqual(block["anchor"], "Geltungsbereich")
        self.assertEqual(block["pages"], [1, 2])
        self.assertEqual(block["element_count"], 6)
        self.assertTrue(block["contains_heading"])
        self.assertEqual(block["impact"]["kinds"], ["section_deleted"])
        self.assertEqual(block["impact"]["priority"], "normal")
        self.assertIn("modal", block["impact"]["signals"])
        self.assertEqual(block["change_ids"], [c["change_id"] for c in reversed(result["changes"])])
        for change in result["changes"]:
            self.assertEqual(change["block_id"], block["block_id"])
            self.assertEqual(change["impact"]["priority"], "normal")
            self.assertIn("block_member", change["impact"]["kinds"])
            self.assertIn("modal", change["impact"]["signals"])
        review, = result["review_queue"]
        self.assertEqual(review["review_id"], "REVIEW-000001")
        self.assertEqual(review["source_kind"], "change_block")
        self.assertEqual(review["change_ids"], block["change_ids"])
        self.assertEqual(review["case_type"], result["changes"][0]["resolution"]["case_type"])
        self.assertEqual(result["statistics"]["result_state_counts"]["uncertain"], 1)
        report_entry, = build_report_model(result)["uncertain"]
        self.assertEqual(
            [source["change_id"] for source in report_entry["unpaired_sources"]["old"]],
            block["change_ids"],
        )

    def test_unchanged_body_splits_added_runs_and_small_runs_stay_individual(self):
        added = [_candidate("A", "new", i) for i in (1, 2, 3, 5, 6, 7, 8, 10, 11)]
        unchanged = [{"old": _body("old", i), "new": _body("new", i)} for i in (4, 9)]
        result = _compare(added=added, unchanged=unchanged)
        self.assertEqual([b["element_count"] for b in result["change_blocks"]], [3, 4])
        self.assertEqual([b["impact"]["kinds"] for b in result["change_blocks"]], [["block_added"]] * 2)
        self.assertEqual([c["impact"]["priority"] for c in result["changes"][-2:]], ["high", "high"])
        self.assertNotIn("block_id", result["changes"][-1])
        self.assertEqual([r["review_id"] for r in result["review_queue"]],
                         [f"REVIEW-{i:06d}" for i in range(1, 5)])

    def test_surviving_heading_breaks_run_and_table_is_not_a_member(self):
        deleted = [_candidate("D", "old", i) for i in (1, 2, 4, 5, 7, 8, 9)]
        heading = {"old": _body("old", 3, "heading"), "new": _body("new", 3, "heading")}
        heading["old"]["text"] = "Prüfverfahren"
        table = _body("old", 6, "table")
        tables = _empty_tables()
        tables["deleted_table_candidates"] = [{"logical_table_id": "OLD-T-6", "element_id": table["element_id"]}]
        elements = [c["old"] for c in deleted] + [heading["old"], table]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "old_document_structured.json").write_text(
                json.dumps({"document": {"elements": elements}}), encoding="utf-8",
            )
            result = _compare(deleted, unchanged=[heading], tables=tables, structure_dir=out)
        block, = result["change_blocks"]
        self.assertEqual(block["element_count"], 3)
        self.assertEqual(block["anchor"], "Prüfverfahren")
        table_change, = result["table_changes"]
        self.assertNotIn("block_id", table_change)
        self.assertNotIn(table_change["table_change_id"], block["change_ids"])

    def test_layout_is_skipped_and_residual_proposal_review_stays_separate(self):
        deleted = [_candidate("D", "old", i) for i in (1, 3, 4, 5)]
        deleted[-1]["residual_proposal_group_id"] = "GROUP-1"
        furniture = _body("old", 2, "heading")
        document = {
            "elements": [c["old"] for c in deleted] + [furniture],
            "structure_evidence": {"membership": {"by_element": {
                furniture["element_id"]: {"category": "layout"},
            }}},
        }
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "old_document_structured.json").write_text(
                json.dumps({"document": document}), encoding="utf-8",
            )
            # A residual group without counterpart proposals inside the block is
            # absorbed into the single block review entry.
            result = _compare(deleted, structure_dir=out)
            block, = result["change_blocks"]
            self.assertFalse(block["contains_heading"])
            self.assertEqual(block["element_count"], 4)
            grouped, = result["review_queue"]
            self.assertEqual(grouped["source_kind"], "change_block")
            self.assertEqual(sorted(grouped["change_ids"]), sorted(block["change_ids"]))
            self.assertEqual(grouped["proposal_group_ids"], ["GROUP-1"])
            # A group that carries a counterpart proposal keeps its own entry.
            result = _compare(deleted, structure_dir=out, residual_groups=[{
                "group_id": "GROUP-1", "proposals": [{"proposal_id": "PROPOSAL-1"}],
            }])
        block, = result["change_blocks"]
        grouped, proposal = result["review_queue"]
        self.assertEqual(grouped["change_ids"], block["change_ids"][:-1])
        self.assertEqual(proposal["source_kind"], "residual_proposal_group")
        self.assertEqual(proposal["evidence"]["change_ids"], block["change_ids"][-1:])
        self.assertEqual(proposal["evidence"]["proposal_ids"], ["PROPOSAL-1"])

    def test_one_sided_priority_keeps_signals_and_limits_high_to_short_normative_text(self):
        for count, priority in ((40, "high"), (41, "normal")):
            text = "Die Meldung muss vorliegen " + "fristgerecht " * (count - 4)
            impact = classify_impact(text, None)
            self.assertEqual(impact["priority"], priority)
            self.assertIn("modal", impact["signals"])
        self.assertEqual(classify_impact(None, "Die Frist beträgt 30 Tage.")["priority"], "high")
        impact = classify_impact(None, "Ausschließlich intern.")
        self.assertEqual(impact["priority"], "normal")
        self.assertIn("qualifier", impact["signals"])


class ClauseContainmentTests(unittest.TestCase):
    shorter = "Der Antrag ist bis zum 31. März einzureichen und wird von der Aufsicht geprüft."
    clause = "sofern keine Ausnahme nach § 5 vorliegt"
    extended_clause = (
        clause + " und sämtliche erforderlichen Angaben von der zuständigen Behörde "
        "noch nicht bestätigt worden sind"
    )

    @staticmethod
    def _stage7(old_texts, new_texts):
        def rows(side, texts):
            elements = [_element(f"{side}-{i}", text, order=i) for i, text in enumerate(texts)]
            for element in elements:
                element["provisional_anchor_context"] = {"recent_anchor_keys": ["antrag"]}
            return elements

        result = s07.build_result({
            "unmatched_old": rows("old", old_texts), "unmatched_new": rows("new", new_texts),
            "old_document": {}, "new_document": {},
        })
        s07.validate_result(result)
        return result

    @staticmethod
    def _longer(clause):
        return f"Der Antrag ist bis zum 31. März einzureichen, {clause}, und wird von der Aufsicht geprüft."

    def test_below_floor_unique_clause_pairs_and_reaches_canonical_high_impact(self):
        result = self._stage7([self._longer(self.extended_clause)], [self.shorter])
        match, = result["modified_matches"]
        self.assertEqual(match["match_type"], "modified_alignment_candidate")
        self.assertEqual(match["review_reasons"], ["clause_containment"])
        self.assertEqual(match["similarity_evidence"]["token_containment"], 1.0)
        self.assertLess(match["similarity"], s07.MIN_REVIEW_SCORE)
        self.assertEqual(result["unmatched_old"] + result["unmatched_new"], [])
        candidate = s08_candidates.paired_candidate(
            "MODIFIED-1", "MODIFIED", match, include_diff=True,
        )
        change, = _compare(modified=[candidate])["changes"]
        self.assertEqual(change["change_type"], "MODIFIED")
        self.assertIn("clause_containment", change["resolution"]["review_reasons"])
        self.assertEqual((change["impact"]["level"], change["impact"]["priority"]), ("content", "high"))
        self.assertIn("condition", change["impact"]["signals"])

    def test_short_unique_containment_pair_stays_in_review(self):
        shorter = "Er war da und hat es so getan."
        longer = "Er war da und, sofern die Zustimmung nicht fehlt, hat es so getan."
        result = self._stage7([longer], [shorter])
        pair, = result["review_pairs"]
        self.assertEqual(pair["match_type"], "modified_alignment_review")
        self.assertIn("clause_containment", pair["review_reasons"])
        self.assertIn("short_text_similarity_pair", pair["review_reasons"])

    def test_containment_ambiguity_in_either_direction_leaves_residuals(self):
        longer = self._longer(self.extended_clause)
        for old, new in (([longer], [self.shorter, self.shorter]), ([longer, longer], [self.shorter])):
            with self.subTest(old_count=len(old), new_count=len(new)):
                result = self._stage7(old, new)
                self.assertEqual(result["modified_matches"] + result["review_pairs"], [])
                self.assertEqual(len(result["unmatched_old"]), len(old))
                self.assertEqual(len(result["unmatched_new"]), len(new))

    def test_supplied_clause_above_floor_uses_existing_pairing(self):
        result = self._stage7([self._longer(self.clause)], [self.shorter])
        match, = result["modified_matches"] + result["review_pairs"]
        self.assertGreaterEqual(match["similarity"], s07.MIN_REVIEW_SCORE)
        self.assertNotIn("clause_containment", match["review_reasons"])
        impact = classify_impact(match["old"]["text"], match["new"]["text"])
        self.assertEqual((impact["level"], impact["priority"]), ("content", "high"))
