"""Automatic uncertainty-result and human-readable report regressions.

Pins the approved slice: new routing never emits a human-action route, legacy
``HUMAN_REVIEW``/``review`` artifacts stay readable as uncertainty, and the
primary HTML/PDF reports show supported and uncertain differences with exact
old/new values instead of an accepted-only or no-differences claim.
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

from document_compare.phase1.reporting import html as report_html
from document_compare.phase1.reporting import redline_pdf, report_model
from document_compare.phase1.contracts import result_state
from document_compare.phase1.stages import s11_routing, s14_canonical_result
from document_compare.phase1.structure import reconstruction
from document_compare.phase2.reader import result_reader as reader
from tests.support import structure_fixtures

ROUTING_STAGE = "deterministic_review_routing"


def _element(element_id, text, *, page=1, order=1, element_type="paragraph"):
    return {
        "element_id": element_id,
        "element_type": element_type,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "reading_order": order,
        "primary_page": page,
        "extraction_validation": {"requires_extraction_review": False},
    }


def _empty_tables():
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP10_STAGE,
        "table_matches": [],
        "deleted_table_candidates": [],
        "added_table_candidates": [],
        "unresolved_table_groups": [],
    }


def _routing(movement, tables):
    generic = s11_routing.build_generic_routing(movement, tables)
    records = s11_routing.build_domain_verification_records(
        generic["routes"], {"mode": "generic"}, None
    )
    return {
        "comparison_stage": ROUTING_STAGE,
        "schema_version": "1.0.0",
        "routes": generic["routes"],
        "non_table_routes": generic["non_table_routes"],
        "table_routes": generic["table_routes"],
        "generic_review_list": generic["generic_review_list"],
        "domain_profile": {"mode": "generic"},
        "domain_verification": {"enabled": False, "records": records,
                                "status_counts": {}},
        "domain_verification_review": [],
        "statistics": {},
    }


def _movement():
    accepted = {
        "candidate_id": "MODIFIED-1",
        "old": _element("OLD-1", "The limit is 21 units."),
        "new": _element("NEW-1", "The limit is 99 units."),
        "similarity": 0.95,
    }
    duplicate = {
        "candidate_id": "DUPLICATE-1",
        "old": _element("OLD-D", "Duplicate candidate."),
        "new": _element("NEW-D", "Duplicate candidate."),
        "similarity": 1.0,
        "review_reasons": ["duplicate_exact_assignment_ambiguous"],
    }
    movement_review = {
        "review_id": "MOVEMENT-BLOCK-REVIEW-1",
        "candidate_id": "MOVEMENT-BLOCK-REVIEW-1",
        "classification": "MOVEMENT_BLOCK_REVIEW",
        "needs_review": True,
        "review_reasons": ["contiguous_block_relative_order_relocation"],
        "old_element_ids": ["OLD-2"],
        "new_element_ids": ["NEW-2"],
    }
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
        "modified": [accepted],
        "modified_review": [],
        "duplicate_exact_review": [duplicate],
        "relocated": [],
        "deleted": [],
        "added": [],
        "movement_review": [movement_review],
        "structural_split_merge_review": [],
        "structural_boundary_review": [],
        "resegmentation_review": [],
    }


class RoutingSemanticsTest(unittest.TestCase):
    def test_new_producer_never_emits_human_review(self):
        routing = _routing(_movement(), _empty_tables())
        routes = {route["route"] for route in routing["routes"]}
        self.assertNotIn("HUMAN_REVIEW", routes)
        self.assertIn("UNCERTAIN_RESULT", routes)
        self.assertTrue(routing["generic_review_list"])
        for route in routing["generic_review_list"]:
            self.assertIn(route["route"], {"UNCERTAIN_RESULT", "SPECIAL_CASE_LLM_OPTIONAL"})

    def test_canonical_resolution_has_normalized_result_state(self):
        movement = _movement()
        tables = _empty_tables()
        result = s14_canonical_result.build_comparison_result(
            movement, tables, _routing(movement, tables)
        )
        for item in result["changes"] + result["table_changes"]:
            self.assertIn(
                item["resolution"]["result_state"],
                {"supported", "uncertain", "non_semantic"},
            )
            # The explicit normalized state must agree with the legacy fields
            # the canonical validation compares against.
            self.assertEqual(
                item["resolution"]["result_state"],
                result_state.derive_result_state(
                    item["resolution"]["status"],
                    item["resolution"]["needs_review"],
                ),
            )
        supported = next(item for item in result["changes"]
                         if item["change_id"] == "CHANGE-000001")
        self.assertEqual(supported["resolution"]["result_state"], "supported")
        # Legacy machine fields stay readable.
        self.assertEqual(supported["resolution"]["status"], "accepted")
        self.assertFalse(supported["resolution"]["needs_review"])
        self.assertTrue(result["review_queue"])
        for item in result["review_queue"]:
            self.assertEqual(item["resolution"]["result_state"], "uncertain")
            self.assertEqual(item["resolution"]["status"], "review")
            self.assertTrue(item["resolution"]["needs_review"])
        self.assertEqual(result["statistics"]["result_state_counts"].get("uncertain"),
                         len(result["review_queue"]))

    def test_legacy_human_review_and_new_uncertain_routes_both_stay_uncertain(self):
        legacy = s14_canonical_result._resolve(
            "C-1",
            {"C-1": {"route_id": "R-1", "route": "HUMAN_REVIEW",
                     "case_type": "movement_block_review",
                     "review_reasons": ["legacy"]}},
            {},
        )
        new = s14_canonical_result._resolve(
            "C-2",
            {"C-2": {"route_id": "R-2", "route": "UNCERTAIN_RESULT",
                     "case_type": "movement_block_review",
                     "review_reasons": ["new"]}},
            {},
        )
        non_semantic = s14_canonical_result._resolve(
            "C-3",
            {"C-3": {"route_id": "R-3", "route": "NON_SEMANTIC",
                     "case_type": "table_non_semantic",
                     "review_reasons": []}},
            {},
        )
        for resolution in (legacy, new):
            self.assertEqual(resolution["status"], "review")
            self.assertTrue(resolution["needs_review"])
            self.assertEqual(resolution["result_state"], "uncertain")
        self.assertFalse(non_semantic["needs_review"])
        self.assertNotEqual(non_semantic["status"], "accepted")
        self.assertEqual(non_semantic["result_state"], "non_semantic")

    def test_llm_advice_cannot_accept_or_erase_uncertainty(self):
        legacy = s14_canonical_result._resolve(
            "C-1",
            {"C-1": {"route_id": "R-1", "route": "UNCERTAIN_RESULT",
                     "case_type": "ambiguous_modified_pair",
                     "review_reasons": ["ambiguous"]}},
            {"R-1": {"route_id": "R-1", "decision": "REAL_CHANGE"}},
        )
        self.assertEqual(legacy["status"], "review")
        self.assertEqual(legacy["result_state"], "uncertain")
        self.assertEqual(legacy["method"], "llm_advisory")
        self.assertIn("packet_decision", legacy)


def _saved_documents():
    old = structure_fixtures.load("toc_text")
    new = structure_fixtures.load("toc_text")
    old["document_id"] = "DOC-OLD"
    new["document_id"] = "DOC-NEW"
    for element in new["elements"]:
        if element["element_id"] == "P-00007":
            element["text"] = "Intro prose body with 99 units."
            element["normalized_text"] = element["text"]
            element["comparison_text"] = element["text"]
    old_evidence = reconstruction.build_structure_evidence(old)
    new_evidence = reconstruction.build_structure_evidence(new)
    old["structure_evidence"] = old_evidence
    new["structure_evidence"] = new_evidence
    return {"document": old}, {"document": new}


def _comparison(*, uncertain_only=False):
    old_doc, new_doc = _saved_documents()
    supported = {
        "change_id": "CHANGE-000001",
        "change_type": "MODIFIED",
        "source_candidate_id": "MODIFIED-1",
        "old": {"element_id": "P-00007", "text": "Intro prose body with 21 units.",
                "primary_page": 2, "reading_order": 7},
        "new": {"element_id": "P-00007", "text": "Intro prose body with 99 units.",
                "primary_page": 2, "reading_order": 7},
        "similarity": 0.9,
        "resolution": {"status": "accepted", "needs_review": False,
                       "result_state": "supported", "review_reasons": []},
    }
    uncertain = {
        "change_id": "CHANGE-000002",
        "change_type": "MODIFIED",
        "source_candidate_id": "MODIFIED-2",
        "old": {"element_id": "P-00009", "text": "Methods prose body 10.",
                "primary_page": 3, "reading_order": 9},
        "new": {"element_id": "P-00009", "text": "Methods prose body 12.",
                "primary_page": 3, "reading_order": 9},
        "resolution": {"status": "review", "needs_review": True,
                       "result_state": "uncertain",
                       "review_reasons": ["ambiguous_modified_pair"]},
    }
    review = {
        "review_id": "REVIEW-000001",
        "source_kind": "non_table_change",
        "change_id": "CHANGE-000002",
        "source_candidate_id": "MODIFIED-2",
        "case_type": "ambiguous_modified_pair",
        "resolution": {"status": "review", "needs_review": True,
                       "result_state": "uncertain",
                       "review_reasons": ["ambiguous_modified_pair"]},
    }
    evidence_only = {
        "review_id": "REVIEW-000002",
        "source_kind": "movement_block",
        "source_candidate_id": "MOVEMENT-BLOCK-REVIEW-1",
        "case_type": "movement_block_review",
        "resolution": {"status": "review", "needs_review": True,
                       "result_state": "uncertain",
                       "review_reasons": ["contiguous_block_relative_order_relocation"]},
        "evidence": {
            "old_element_ids": ["P-00011"],
            "new_element_ids": ["P-00011"],
            "review_reasons": ["contiguous_block_relative_order_relocation"],
        },
    }
    table = {
        "table_change_id": "TABLE-CHANGE-00001",
        "change_type": "TABLE_MODIFIED",
        "classification": "MODIFIED",
        "old": {"row_count": 2, "column_count": 2, "source_pages": [3]},
        "new": {"row_count": 2, "column_count": 2, "source_pages": [4]},
        "cell_changes": [
            {"classification": "UNCHANGED",
             "old": {"text": "Region", "primary_page": 3},
             "new": {"text": "Region", "primary_page": 4}},
            {"classification": "cell_change", "old_row_index": 1,
             "new_row_index": 1, "column_index": 1,
             "old": {"text": "100", "primary_page": 3},
             "new": {"text": "150", "primary_page": 4}},
        ],
        "resolution": {"status": "accepted", "needs_review": False,
                       "result_state": "supported"},
    }
    changes = [] if uncertain_only else [supported, uncertain]
    review_queue = [review] if uncertain_only else [review, evidence_only]
    if uncertain_only:
        review_queue = [{**review, "change_id": None, "source_kind": "movement_block",
                         "source_candidate_id": "MOVEMENT-BLOCK-REVIEW-1",
                         "case_type": "movement_block_review",
                         "resolution": {"status": "review", "needs_review": True,
                                        "result_state": "uncertain",
                                        "review_reasons": ["contiguous_block_relative_order_relocation"]},
                         "evidence": {"old_element_ids": ["P-00011"],
                                      "new_element_ids": ["P-00011"]}}]
    return {
        "schema_version": "3.0.0",
        "comparison_stage": "phase_1_final_comparison",
        "old_document": {"document_id": "DOC-OLD", "page_count": 3,
                         "source_element_count": 27},
        "new_document": {"document_id": "DOC-NEW", "page_count": 3,
                         "source_element_count": 27},
        "changes": changes,
        "table_changes": [] if uncertain_only else [table],
        "review_queue": review_queue,
        "non_semantic_events": [] if uncertain_only else [
            {"event_type": "RELOCATED_UNCHANGED", "source_candidate_id": "REL-1"}
        ],
        "unresolved_table_reviews": [],
        "statistics": {},
    }, old_doc, new_doc


def _write_workspace(root: Path, comparison, old_doc, new_doc):
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison_result.json").write_text(
        json.dumps(comparison, ensure_ascii=False), encoding="utf-8"
    )
    (out / "old_document_structured.json").write_text(
        json.dumps(old_doc, ensure_ascii=False), encoding="utf-8"
    )
    (out / "new_document_structured.json").write_text(
        json.dumps(new_doc, ensure_ascii=False), encoding="utf-8"
    )
    return out


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return " ".join(
            "".join(page.get_text() for page in document).split()
        )


def _section_document(document_id: str, sections: dict[str, str]) -> dict:
    """Minimal structured document with one section per local element id."""
    section_records = []
    membership = {}
    for index, (element_id, title) in enumerate(sections.items()):
        section_id = f"S-{index}"
        section_records.append({"section_id": section_id, "title": title})
        membership[element_id] = {"section_id": section_id}
    return {
        "document": {
            "document_id": document_id,
            "elements": [],
            "structure_evidence": {
                "sections": section_records,
                "membership": {"by_element": membership},
            },
        }
    }


def _supported_change(change_id, change_type, old, new):
    return {
        "change_id": change_id,
        "change_type": change_type,
        "old": old,
        "new": new,
        "resolution": {"status": "accepted", "needs_review": False,
                       "result_state": "supported"},
    }


class SectionOrientationTest(unittest.TestCase):
    """Element/table ids are document-local; lookup must respect orientation."""

    def _documents(self):
        old_doc = _section_document(
            "DOC-OLD",
            {"P-00001": "Old introduction", "T-00001": "Old table section"},
        )
        new_doc = _section_document(
            "DOC-NEW",
            {"P-00001": "New appendix", "T-00001": "New table section"},
        )
        return old_doc, new_doc

    def test_added_deleted_and_paired_same_local_ids_use_owning_side(self):
        old_doc, new_doc = self._documents()
        added = _supported_change(
            "CHANGE-ADDED", "ADDED", {},
            {"element_id": "P-00001", "text": "Added body", "primary_page": 9},
        )
        deleted = _supported_change(
            "CHANGE-DELETED", "DELETED",
            {"element_id": "P-00001", "text": "Deleted body", "primary_page": 2},
            {},
        )
        paired = _supported_change(
            "CHANGE-PAIRED", "MODIFIED",
            {"element_id": "P-00001", "text": "Old body", "primary_page": 2},
            {"element_id": "P-00001", "text": "New body", "primary_page": 3},
        )
        comparison = {"changes": [added, deleted, paired],
                      "table_changes": [], "review_queue": []}
        model = report_model.build_report_model(
            comparison, old_document=old_doc, new_document=new_doc
        )
        by_id = {entry["record_id"]: entry for entry in model["supported"]}
        self.assertEqual(by_id["CHANGE-ADDED"]["group_title"], "New appendix")
        self.assertEqual(by_id["CHANGE-DELETED"]["group_title"], "Old introduction")
        self.assertEqual(by_id["CHANGE-PAIRED"]["group_title"], "Old introduction")

    def test_evidence_refs_use_their_owning_side(self):
        old_doc, new_doc = self._documents()
        comparison = {
            "changes": [], "table_changes": [],
            "review_queue": [
                {
                    "review_id": "REVIEW-OLD",
                    "case_type": "movement_block_review",
                    "resolution": {"status": "review", "needs_review": True,
                                   "result_state": "uncertain"},
                    "evidence": {"old_element_ids": ["P-00001"]},
                },
                {
                    "review_id": "REVIEW-NEW",
                    "case_type": "movement_block_review",
                    "resolution": {"status": "review", "needs_review": True,
                                   "result_state": "uncertain"},
                    "evidence": {"new_element_ids": ["P-00001"]},
                },
            ],
        }
        model = report_model.build_report_model(
            comparison, old_document=old_doc, new_document=new_doc
        )
        by_id = {entry["review_id"]: entry for entry in model["uncertain"]}
        self.assertEqual(by_id["REVIEW-OLD"]["group_title"], "Old introduction")
        self.assertEqual(by_id["REVIEW-NEW"]["group_title"], "New appendix")

    def test_table_source_ids_use_their_owning_side(self):
        old_doc, new_doc = self._documents()
        paired = {
            "table_change_id": "TABLE-PAIRED",
            "change_type": "TABLE_MODIFIED",
            "old": {"source_table_ids": ["T-00001"], "source_pages": [3],
                    "row_count": 1, "column_count": 1},
            "new": {"source_table_ids": ["T-00001"], "source_pages": [7],
                    "row_count": 1, "column_count": 1},
            "resolution": {"status": "accepted", "needs_review": False,
                           "result_state": "supported"},
        }
        added = {
            "table_change_id": "TABLE-ADDED",
            "change_type": "TABLE_ADDED",
            "old": None,
            "new": {"source_table_ids": ["T-00001"], "source_pages": [8],
                    "row_count": 1, "column_count": 1},
            "resolution": {"status": "accepted", "needs_review": False,
                           "result_state": "supported"},
        }
        comparison = {"changes": [], "table_changes": [paired, added],
                      "review_queue": []}
        model = report_model.build_report_model(
            comparison, old_document=old_doc, new_document=new_doc
        )
        by_id = {entry["record_id"]: entry for entry in model["supported_tables"]}
        self.assertEqual(by_id["TABLE-PAIRED"]["group_title"], "Old table section")
        self.assertEqual(by_id["TABLE-ADDED"]["group_title"], "New table section")


class TablePageOrientationTest(unittest.TestCase):
    def test_modified_table_keeps_side_specific_primary_pages(self):
        table = {
            "table_change_id": "TABLE-CHANGE-00001",
            "change_type": "TABLE_MODIFIED",
            "old": {"source_pages": [3], "row_count": 1, "column_count": 1},
            "new": {"source_pages": [7], "row_count": 1, "column_count": 1},
            "resolution": {"status": "accepted", "needs_review": False,
                           "result_state": "supported"},
        }
        model = report_model.build_report_model(
            {"changes": [], "table_changes": [table], "review_queue": []}
        )
        entry = model["supported_tables"][0]
        self.assertEqual(entry["old_page"], 3)
        self.assertEqual(entry["new_page"], 7)

    def test_one_sided_tables_do_not_borrow_the_other_side_page(self):
        deleted = {
            "table_change_id": "TABLE-DELETED",
            "change_type": "TABLE_DELETED",
            "old": {"source_pages": [3], "row_count": 1, "column_count": 1},
            "new": None,
            "resolution": {"status": "accepted", "needs_review": False,
                           "result_state": "supported"},
        }
        added = {
            "table_change_id": "TABLE-ADDED",
            "change_type": "TABLE_ADDED",
            "old": None,
            "new": {"source_pages": [7], "row_count": 1, "column_count": 1},
            "resolution": {"status": "accepted", "needs_review": False,
                           "result_state": "supported"},
        }
        model = report_model.build_report_model(
            {"changes": [], "table_changes": [deleted, added], "review_queue": []}
        )
        by_id = {entry["record_id"]: entry for entry in model["supported_tables"]}
        self.assertEqual(by_id["TABLE-DELETED"]["old_page"], 3)
        self.assertIsNone(by_id["TABLE-DELETED"]["new_page"])
        self.assertIsNone(by_id["TABLE-ADDED"]["old_page"])
        self.assertEqual(by_id["TABLE-ADDED"]["new_page"], 7)


class ConservativeResultStateTest(unittest.TestCase):
    def test_explicit_supported_cannot_override_review_or_missing_status(self):
        contradictions = [
            {"status": "review", "needs_review": True, "result_state": "supported"},
            {"status": "accepted", "needs_review": True, "result_state": "supported"},
            {"result_state": "supported"},
            {"status": "unknown", "result_state": "supported"},
            {"status": "review", "needs_review": False, "result_state": "supported"},
        ]
        for resolution in contradictions:
            self.assertEqual(
                result_state.resolution_result_state(resolution), "uncertain",
                resolution,
            )

    def test_consistent_explicit_state_and_legacy_derivation_stay_compatible(self):
        cases = [
            ({"status": "accepted", "needs_review": False,
              "result_state": "supported"}, "supported"),
            ({"status": "review", "needs_review": True,
              "result_state": "uncertain"}, "uncertain"),
            ({"status": "structural_only", "needs_review": False,
              "result_state": "non_semantic"}, "non_semantic"),
            ({"status": "accepted", "needs_review": False}, "supported"),
            ({"status": "review", "needs_review": True}, "uncertain"),
        ]
        for resolution, expected in cases:
            self.assertEqual(
                result_state.resolution_result_state(resolution), expected
            )

    def test_reader_and_report_do_not_promote_contradictory_state(self):
        comparison, _, _ = _comparison()
        comparison["changes"][0]["resolution"] = {
            "status": "review", "needs_review": True,
            "result_state": "supported",
            "review_reasons": ["ambiguous_modified_pair"],
        }
        summary = reader.summarize("c" * 12, "done", comparison)
        self.assertEqual(summary["counts"]["result_state_counts"]["supported"], 1)
        self.assertEqual(summary["counts"]["result_state_counts"]["uncertain"], 3)
        search = reader.search("c" * 12, comparison, limit=50)
        by_id = {item["record_id"]: item for item in search["returned"]}
        self.assertEqual(by_id["CHANGE-000001"]["result_state"], "uncertain")

        model = report_model.build_report_model(comparison)
        supported_ids = {entry["record_id"] for entry in model["supported"]}
        uncertain_ids = {entry["record_id"] for entry in model["uncertain"]}
        self.assertNotIn("CHANGE-000001", supported_ids)
        self.assertIn("CHANGE-000001", uncertain_ids)


class UncertainTableHtmlTest(unittest.TestCase):
    """Uncertain tables are projected as review entries but must keep cells."""

    def _table(self):
        return {
            "table_change_id": "T1",
            "change_type": "TABLE_MODIFIED",
            "old": {"source_pages": [2], "row_count": 1, "column_count": 1},
            "new": {"source_pages": [3], "row_count": 1, "column_count": 1},
            "review_reasons": ["merged_cell_or_header_kind_changed"],
            "resolution": {"status": "review", "needs_review": True,
                           "result_state": "uncertain"},
            "cell_changes": [{
                "classification": "MODIFIED",
                "old": {"text": "100"},
                "new": {"text": "150"},
            }],
        }

    def _assert_table_card(self, card, *, record_id, review_id):
        self.assertIn("report-table", card)
        self.assertIn("100", card)
        self.assertIn("150", card)
        self.assertIn("Table cell structure or header type changed", card)
        self.assertIn(f"data-record-id='{record_id}'", card)
        self.assertIn(f"data-review-id='{review_id}'", card)
        self.assertNotIn("Before", card)
        self.assertNotIn("After", card)

    def test_standalone_uncertain_table_keeps_cells_and_reason(self):
        comparison = {
            "changes": [],
            "table_changes": [self._table()],
            "review_queue": [],
        }
        model = report_model.build_report_model(comparison)
        self.assertEqual(len(model["uncertain"]), 1)
        card = report_html._entry_card(model["uncertain"][0])
        self._assert_table_card(card, record_id="T1", review_id="")

    def test_linked_uncertain_table_keeps_cells_typed_link_and_reason(self):
        comparison = {
            "changes": [],
            "table_changes": [self._table()],
            "review_queue": [{
                "review_id": "R1",
                "table_change_id": "T1",
                "resolution": {"status": "review", "needs_review": True,
                               "result_state": "uncertain",
                               "review_reasons": [
                                   "merged_cell_or_header_kind_changed"
                               ]},
            }],
        }
        model = report_model.build_report_model(comparison)
        self.assertEqual(len(model["uncertain"]), 1)
        card = report_html._entry_card(model["uncertain"][0])
        self._assert_table_card(card, record_id="R1", review_id="R1")
        self.assertIn("table:T1", card)


class HumanReportTest(unittest.TestCase):
    def test_html_is_a_readable_complete_report(self):
        comparison, old_doc, new_doc = _comparison()
        comparison["changes"][0]["old"]["text"] = (
            "Intro prose body with 21 units <script>alert(1)</script> & risk."
        )
        comparison["changes"][0]["new"]["text"] = (
            "Intro prose body with 99 units <script>alert(1)</script> & risk."
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root, comparison, old_doc, new_doc)
            target = report_html.render(root)
            body = target.read_text(encoding="utf-8")
        self.assertIn("Uncertain differences", body)
        self.assertIn("Supported changes", body)
        self.assertIn("Before", body)
        self.assertIn("After", body)
        self.assertIn("21", body)
        self.assertIn("99", body)
        self.assertIn("100", body)
        self.assertIn("150", body)
        self.assertIn("1. Introduction", body)
        self.assertIn("<details", body)
        self.assertIn("2 uncertain", body.replace("&nbsp;", " "))
        self.assertIn("Why this remains uncertain", body)
        self.assertIn("The old and new passages resemble each other", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("No differences were found", body)

    def test_html_before_after_columns_keep_their_own_source_text(self):
        comparison, old_doc, new_doc = _comparison()
        change = comparison["changes"][0]
        change["old"]["text"] = "Limit 100"
        change["new"]["text"] = "Limit 150"
        change["token_diff"] = {"comparison_token_diff": {"changes": [{
            "operation": "replace",
            "old_char_start": 6,
            "old_char_end": 9,
            "new_char_start": 6,
            "new_char_end": 9,
        }]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root, comparison, old_doc, new_doc)
            body = report_html.render(root).read_text(encoding="utf-8")
        article = body.split("data-record-id='CHANGE-000001'", 1)[1]
        article = article.split("</article>", 1)[0]
        block = article.split("<div class='beforeafter'>", 1)[1]
        block = block.split("</div></div>", 1)[0]
        before_column, after_column = block.split("</div><div class='side'>", 1)
        self.assertIn("Limit 100", before_column)
        self.assertNotIn("150", before_column)
        self.assertNotIn("<del>", before_column)
        self.assertNotIn("<ins>", before_column)
        self.assertIn("Limit 150", after_column)
        self.assertNotIn("100", after_column)

    def test_html_uncertainty_only_is_not_a_no_differences_claim(self):
        comparison, old_doc, new_doc = _comparison(uncertain_only=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root, comparison, old_doc, new_doc)
            body = report_html.render(root).read_text(encoding="utf-8")
        self.assertNotIn("No differences were found", body)
        self.assertIn("uncertain", body.lower())
        self.assertIn("No supported differences", body)

    def test_pdf_contains_summary_before_after_uncertainty_and_appendix(self):
        comparison, old_doc, new_doc = _comparison()
        with tempfile.TemporaryDirectory() as directory:
            target = redline_pdf.render_pdf(
                comparison, Path(directory) / "report.pdf",
                old_document=old_doc, new_document=new_doc,
            )
            joined = _pdf_text(target)
        for token in ("Supported changes", "Uncertain differences", "Before",
                      "After", "21", "99", "100", "150", "1. Introduction",
                      "Technical appendix"):
            self.assertIn(token, joined)
        # Exact before/after lines stay compatible with the existing redline.
        self.assertIn("old: Intro prose body with 21 units.", joined)
        self.assertIn("new: Intro prose body with 99 units.", joined)

    def test_pdf_uncertainty_only_does_not_claim_no_differences(self):
        comparison, old_doc, new_doc = _comparison(uncertain_only=True)
        with tempfile.TemporaryDirectory() as directory:
            target = redline_pdf.render_pdf(
                comparison, Path(directory) / "report.pdf",
                old_document=old_doc, new_document=new_doc,
            )
            joined = _pdf_text(target)
        self.assertNotIn("No differences were found", joined)
        self.assertIn("No supported differences", joined)
        self.assertIn("Uncertain differences", joined)

    def test_empty_report_keeps_the_truthful_no_differences_message(self):
        with tempfile.TemporaryDirectory() as directory:
            target = redline_pdf.render_pdf({}, Path(directory) / "empty.pdf")
            joined = _pdf_text(target)
        self.assertIn("No differences were found between the two documents.", joined)

    def test_evidence_only_uncertainty_does_not_invent_a_pair(self):
        comparison, old_doc, new_doc = _comparison(uncertain_only=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root, comparison, old_doc, new_doc)
            body = report_html.render(root).read_text(encoding="utf-8")
        evidence_section = body.split("Uncertain differences", 1)[1]
        # The evidence-only item names its refs/reason but has no Before/After.
        self.assertIn("MOVEMENT-BLOCK-REVIEW-1", evidence_section)
        article = evidence_section.split("data-record-id='REVIEW-000001'", 1)[1]
        item_block = article.split("</article>", 1)[0]
        self.assertIn("A contiguous block appears to have moved", item_block)
        self.assertNotIn("Before", item_block)
        self.assertNotIn("After", item_block)


class ReaderResultStateTest(unittest.TestCase):
    def _canonical(self):
        comparison, _, _ = _comparison()
        return comparison

    def test_summary_search_and_details_expose_result_state(self):
        canonical = self._canonical()
        summary = reader.summarize("a" * 12, "done", canonical)
        self.assertEqual(summary["counts"]["review_open"], 2)
        self.assertEqual(summary["counts"]["result_state_counts"]["supported"], 2)
        self.assertEqual(summary["counts"]["result_state_counts"]["uncertain"], 2)

        search = reader.search("a" * 12, canonical, limit=50)
        by_id = {item["record_id"]: item for item in search["returned"]}
        self.assertEqual(by_id["CHANGE-000001"]["result_state"], "supported")
        self.assertEqual(by_id["CHANGE-000002"]["result_state"], "uncertain")
        self.assertEqual(by_id["REVIEW-000001"]["result_state"], "uncertain")
        # Existing filters and typed identities stay intact.
        accepted = reader.search("a" * 12, canonical, status="accepted")
        self.assertEqual([item["record_id"] for item in accepted["returned"]],
                         ["CHANGE-000001", "TABLE-CHANGE-00001"])
        reviews = reader.search("a" * 12, canonical, status="review")
        self.assertTrue(all(item["result_state"] == "uncertain"
                            for item in reviews["returned"]))

        details = reader.details("a" * 12, canonical, kind="change",
                                 record_id="CHANGE-000002")
        self.assertEqual(details["change"]["resolution"]["result_state"], "uncertain")
        review = reader.details("a" * 12, canonical, kind="review",
                                record_id="REVIEW-000001")
        self.assertEqual(review["review"]["result_state"], "uncertain")
        self.assertFalse(review["review"]["confirmed"])

    def test_details_nested_resolution_uses_normalized_state_without_mutation(self):
        contradictory = {
            "status": "review",
            "needs_review": True,
            "result_state": "supported",
        }
        canonical = {
            "changes": [{
                "change_id": "CHANGE-1",
                "change_type": "MODIFIED",
                "old": {"text": "100"},
                "new": {"text": "150"},
                "resolution": dict(contradictory),
            }],
            "table_changes": [{
                "table_change_id": "TABLE-1",
                "change_type": "TABLE_MODIFIED",
                "old": {"row_count": 1, "column_count": 1},
                "new": {"row_count": 1, "column_count": 1},
                "resolution": dict(contradictory),
                "cell_changes": [],
            }],
            "review_queue": [{
                "review_id": "REVIEW-1",
                "case_type": "ambiguous_modified_pair",
                "resolution": dict(contradictory),
            }],
            "statistics": {},
        }
        snapshot = copy.deepcopy(canonical)
        change = reader.details("d" * 12, canonical, kind="change",
                                record_id="CHANGE-1")
        self.assertEqual(change["change"]["result_state"], "uncertain")
        self.assertEqual(
            change["change"]["resolution"]["result_state"], "uncertain"
        )
        table = reader.details("d" * 12, canonical, kind="table",
                               record_id="TABLE-1")
        self.assertEqual(table["table"]["result_state"], "uncertain")
        self.assertEqual(
            table["table"]["resolution"]["result_state"], "uncertain"
        )
        review = reader.details("d" * 12, canonical, kind="review",
                                record_id="REVIEW-1")
        self.assertEqual(review["review"]["result_state"], "uncertain")
        self.assertEqual(
            review["review"]["resolution"]["result_state"], "uncertain"
        )
        self.assertEqual(canonical, snapshot)

    def test_legacy_snapshot_derives_state_without_rewriting_it(self):
        canonical = {
            "changes": [{
                "change_id": "CHANGE-000001",
                "change_type": "MODIFIED",
                "old": {"text": "21"},
                "new": {"text": "99"},
                "resolution": {"status": "accepted", "needs_review": False},
            }],
            "table_changes": [],
            "review_queue": [{
                "review_id": "REVIEW-000001",
                "case_type": "legacy_review",
                "resolution": {"route": "HUMAN_REVIEW", "status": "review",
                               "needs_review": True},
            }],
            "statistics": {},
        }
        snapshot = copy.deepcopy(canonical)
        summary = reader.summarize("b" * 12, "done", canonical)
        self.assertEqual(summary["counts"]["result_state_counts"]["supported"], 1)
        self.assertEqual(summary["counts"]["result_state_counts"]["uncertain"], 1)
        search = reader.search("b" * 12, canonical, limit=50)
        self.assertEqual({item["record_id"]: item["result_state"]
                          for item in search["returned"]}["REVIEW-000001"], "uncertain")
        self.assertEqual(canonical, snapshot)


if __name__ == "__main__":
    unittest.main()
