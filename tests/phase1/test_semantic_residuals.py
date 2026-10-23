"""Bounded residual counterpart proposals: safety, budget and grouping.

Proposals are evidence only.  They never consume a residual, never become a
committed match and never by themselves justify an accepted deletion/addition.
The search is deterministic and bounded, and its examined/unsearched coverage
is explicit so budget exhaustion cannot masquerade as proof of absence.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from document_compare.phase1.matching import residual_proposals as rp
from document_compare.phase1.stages import s11_routing, s14_canonical_result

ROOT = Path(__file__).resolve().parents[2]
S09 = ROOT / "src" / "document_compare" / "phase1" / "stages" / "s09_movement.py"
PYTHON = sys.executable

INPUT_STAGE = "comparison_candidate_assembly_and_multi_representation_token_diff"


def _element(
    element_id: str,
    text: str,
    *,
    order: int | None = 1,
    element_type: str = "paragraph",
    context: list[str] | None = None,
) -> dict:
    element = {
        "element_id": element_id,
        "element_type": element_type,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "primary_page": 1,
        "extraction_validation": {"requires_extraction_review": False},
    }
    if order is not None:
        element["reading_order"] = order
    element["provisional_anchor_context"] = {
        "recent_anchor_keys": list(context or []),
    }
    return element


def _deleted(candidate_id: str, element: dict, reasons: list[str] | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "DELETED_CANDIDATE",
        "old": element,
        "new": None,
        "needs_review": False,
        "review_reasons": list(reasons or []),
        "token_diff": None,
    }


def _added(candidate_id: str, element: dict, reasons: list[str] | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "ADDED_CANDIDATE",
        "old": None,
        "new": element,
        "needs_review": False,
        "review_reasons": list(reasons or []),
        "token_diff": None,
    }


def _paired(candidate_id: str, old_element: dict, new_element: dict) -> dict:
    return {
        "candidate_id": candidate_id,
        "old": old_element,
        "new": new_element,
    }


class ProposalBudgetTest(unittest.TestCase):
    def test_at_most_five_stable_proposals_per_residual(self):
        old = _element(
            "OLD-1", "Alpha beta gamma delta epsilon review", order=5,
            context=["heading:policy"],
        )
        added = [
            _added(
                f"ADD-{index}",
                _element(
                    f"NEW-{index}",
                    f"Alpha beta gamma delta epsilon review {index}",
                    order=index,
                    context=["heading:policy"],
                ),
            )
            for index in range(1, 9)
        ]
        first = rp.build_residual_proposals([_deleted("DEL-1", old)], added)
        second = rp.build_residual_proposals([_deleted("DEL-1", old)], added)
        self.assertEqual(first["proposals"], second["proposals"])
        self.assertEqual(len(first["proposals"]), 5)
        self.assertEqual(
            [item["proposal_id"] for item in first["proposals"]],
            [item["proposal_id"] for item in second["proposals"]],
        )
        for proposal in first["proposals"]:
            self.assertIs(proposal["consumes_candidates"], False)
            self.assertNotEqual(proposal["deleted_candidate_id"], proposal["added_candidate_id"])

    def test_tie_break_is_reading_order_then_id(self):
        old = _element("OLD-1", "same repeated clause", order=2)
        added = [
            _added("ADD-B", _element("NEW-B", "same repeated clause", order=7)),
            _added("ADD-A", _element("NEW-A", "same repeated clause", order=3)),
        ]
        result = rp.build_residual_proposals([_deleted("DEL-1", old)], added)
        self.assertEqual(
            [item["added_candidate_id"] for item in result["proposals"]],
            ["ADD-A", "ADD-B"],
        )

    def test_budget_exhaustion_is_explicit_and_bounded(self):
        old = _element("OLD-1", "alpha beta gamma delta", order=1)
        added = [
            _added(
                f"ADD-{index}",
                _element(f"NEW-{index}", f"alpha beta gamma delta {index}", order=index),
            )
            for index in range(1, 8)
        ]
        result = rp.build_residual_proposals(
            [_deleted("DEL-1", old)], added, max_pair_checks=1
        )
        coverage = result["coverage"]
        self.assertLessEqual(coverage["pair_checks"], 1)
        self.assertTrue(coverage["budget_exhausted"])
        self.assertGreater(coverage["unsearched_residual_elements"], 0)
        deleted_coverage = result["candidate_coverage"]["DEL-1"]
        self.assertGreater(deleted_coverage["unsearched"], 0)

    def test_no_full_cross_product_for_unrelated_text(self):
        deleted = [
            _deleted(f"DEL-{i}", _element(f"OLD-{i}", f"Unique alpha {i}", order=i))
            for i in range(1, 4)
        ]
        added = [
            _added(f"ADD-{i}", _element(f"NEW-{i}", f"Separate omega {i}", order=i))
            for i in range(1, 4)
        ]
        result = rp.build_residual_proposals(deleted, added)
        self.assertEqual(result["proposals"], [])
        self.assertLess(result["coverage"]["pair_checks"], len(deleted) * len(added))

    def test_every_residual_has_explicit_coverage(self):
        deleted = [_deleted("DEL-1", _element("OLD-1", "alpha beta", order=1))]
        added = [_added("ADD-1", _element("NEW-1", "alpha beta", order=1))]
        result = rp.build_residual_proposals(deleted, added)
        self.assertIn("DEL-1", result["candidate_coverage"])
        self.assertIn("ADD-1", result["candidate_coverage"])
        for coverage in result["candidate_coverage"].values():
            self.assertIn(
                coverage["status"], {"complete", "partial", "no_opposite"}
            )


class ProposalSafetyTest(unittest.TestCase):
    def test_shared_context_finds_below_floor_counterpart_without_committing(self):
        old = _deleted(
            "DEL-1",
            _element(
                "OLD-1",
                "The device owner rotates the key.",
                order=5,
                context=["heading:key management"],
            ),
        )
        new = _added(
            "ADD-1",
            _element(
                "NEW-1",
                "The security administrator rotates the key.",
                order=5,
                context=["heading:key management"],
            ),
        )
        result = rp.build_residual_proposals([old], [new])
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["deleted_candidate_id"], "DEL-1")
        self.assertEqual(proposal["added_candidate_id"], "ADD-1")
        self.assertIs(proposal["consumes_candidates"], False)
        # Residual inputs are not mutated or consumed.
        self.assertIsNotNone(old["old"])
        self.assertIsNotNone(new["new"])

    def test_same_context_alone_is_not_a_committed_pair(self):
        old = _deleted(
            "DEL-1",
            _element("OLD-1", "Unrelated first clause.", order=5, context=["heading:x"]),
        )
        new = _added(
            "ADD-1",
            _element("NEW-1", "Completely different second text.", order=5, context=["heading:x"]),
        )
        result = rp.build_residual_proposals([old], [new])
        for proposal in result["proposals"]:
            self.assertIs(proposal["consumes_candidates"], False)
        self.assertEqual(result["groups"][0]["correspondence"] in {"proposed", "unresolved"}, True)

    def test_cross_section_candidate_stays_uncertain(self):
        old = _deleted(
            "DEL-1",
            _element("OLD-1", "Records shall be retained.", order=5, context=["heading:a"]),
        )
        new = _added(
            "ADD-1",
            _element("NEW-1", "Records shall be destroyed.", order=5, context=["heading:b"]),
        )
        result = rp.build_residual_proposals([old], [new])
        self.assertTrue(result["proposals"])
        self.assertEqual(
            result["proposals"][0]["classification"], "cross_section_conflict"
        )
        self.assertIs(result["proposals"][0]["consumes_candidates"], False)

    def test_duplicate_candidates_remain_ambiguous(self):
        old_a = _deleted("DEL-A", _element("OLD-A", "Repeated clause text.", order=4))
        old_b = _deleted("DEL-B", _element("OLD-B", "Repeated clause text.", order=5))
        new = _added("ADD-A", _element("NEW-A", "Repeated clause text.", order=5))
        result = rp.build_residual_proposals([old_a, old_b], [new])
        proposed_deleted = {item["deleted_candidate_id"] for item in result["proposals"]}
        self.assertEqual(proposed_deleted, {"DEL-A", "DEL-B"})
        self.assertTrue(all(
            item["classification"] in {"competing_counterparts", "plausible_counterpart", "shared_context"}
            for item in result["proposals"]
        ))

    def test_unique_residual_interval_proposes_without_shared_tokens(self):
        old_anchor = _element("OLD-A", "Anchor old", order=0)
        new_anchor = _element("NEW-A", "Anchor new", order=0)
        old_end = _element("OLD-Z", "End old", order=10)
        new_end = _element("NEW-Z", "End new", order=10)
        matched = [
            _paired("MATCH-A", old_anchor, new_anchor),
            _paired("MATCH-Z", old_end, new_end),
        ]
        deleted = [_deleted("DEL-1", _element("OLD-M", "Zebra crossing.", order=5))]
        added = [_added("ADD-1", _element("NEW-M", "Quokka tunnel.", order=5))]
        result = rp.build_residual_proposals(
            deleted, added, matched_pairs=matched
        )
        self.assertTrue(result["proposals"])
        self.assertEqual(
            result["proposals"][0]["classification"], "unique_residual_interval"
        )
        self.assertIs(result["proposals"][0]["consumes_candidates"], False)

    def test_absent_structure_falls_back_to_lexical_evidence(self):
        old = _deleted("DEL-1", _element("OLD-1", "alpha beta gamma", order=None))
        new = _added("ADD-1", _element("NEW-1", "alpha beta delta", order=None))
        result = rp.build_residual_proposals([old], [new])
        self.assertTrue(result["proposals"])
        self.assertEqual(result["candidate_coverage"]["DEL-1"]["status"], "complete")


class ResidualRoutingTest(unittest.TestCase):
    def _movement(self, deleted: list[dict], added: list[dict]) -> dict:
        return {
            "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
            "modified": [],
            "modified_review": [],
            "duplicate_exact_review": [],
            "relocated": [],
            "deleted": deleted,
            "added": added,
            "movement_review": [],
            "structural_split_merge_review": [],
            "structural_boundary_review": [],
            "resegmentation_review": [],
            "residual_proposal_groups": [],
        }

    def _tables(self) -> dict:
        return {
            "comparison_stage": s11_routing.EXPECTED_STEP10_STAGE,
            "table_matches": [],
            "deleted_table_candidates": [],
            "added_table_candidates": [],
            "unresolved_table_groups": [],
        }

    def test_plausible_proposal_prevents_independent_accept(self):
        old = _deleted(
            "DEL-1", _element("OLD-1", "The device owner rotates the key.", order=5)
        )
        new = _added(
            "ADD-1",
            _element("NEW-1", "The security administrator rotates the key.", order=5),
        )
        proposals = rp.build_residual_proposals([old], [new])
        for candidate in (old, new):
            candidate["residual_counterpart_proposals"] = proposals["proposals"]
            candidate["residual_proposal_coverage"] = proposals["candidate_coverage"][
                candidate["candidate_id"]
            ]
            candidate["residual_proposal_group_id"] = proposals["candidate_groups"][
                candidate["candidate_id"]
            ]
        movement = self._movement([old], [new])
        generic = s11_routing.build_generic_routing(movement, self._tables())
        for route in generic["routes"][:2]:
            self.assertEqual(route["route"], "UNCERTAIN_RESULT")
            self.assertEqual(route["case_type"], "unresolved_residual_counterpart")

    def test_complete_no_opposite_keeps_legitimate_one_sided_evidence(self):
        old = _deleted(
            "DEL-1",
            _element(
                "OLD-1",
                "A genuinely withdrawn clause with no counterpart.",
                order=5,
            ),
        )
        old["residual_proposal_coverage"] = {
            "status": "no_opposite",
            "examined": 0,
            "unsearched": 0,
            "opposite_residuals": 0,
            "budget_exhausted": False,
            "limitations": [],
        }
        movement = self._movement([old], [])
        generic = s11_routing.build_generic_routing(movement, self._tables())
        self.assertEqual(generic["routes"][0]["route"], "ACCEPT_DETERMINISTIC")

    def test_unsearched_coverage_blocks_absence_acceptance(self):
        old = _deleted(
            "DEL-1", _element("OLD-1", "Residual with no safe search.", order=5)
        )
        old["residual_proposal_coverage"] = {
            "status": "partial",
            "examined": 0,
            "unsearched": 1,
            "opposite_residuals": 1,
            "budget_exhausted": True,
            "limitations": ["budget_exhausted"],
        }
        motion = self._movement([old], [])
        generic = s11_routing.build_generic_routing(motion, self._tables())
        self.assertEqual(generic["routes"][0]["route"], "UNCERTAIN_RESULT")


class CanonicalGroupTest(unittest.TestCase):
    def _movement_and_routing(self):
        old = _deleted(
            "DEL-1",
            _element("OLD-1", "The device owner rotates the key.", order=5),
        )
        new = _added(
            "ADD-1",
            _element("NEW-1", "The security administrator rotates the key.", order=5),
        )
        proposals = rp.build_residual_proposals([old], [new])
        group_id = proposals["groups"][0]["group_id"]
        for candidate in (old, new):
            candidate["residual_counterpart_proposals"] = proposals["proposals"]
            candidate["residual_proposal_coverage"] = proposals["candidate_coverage"][
                candidate["candidate_id"]
            ]
            candidate["residual_proposal_group_id"] = group_id
        movement = {
            "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
            "modified": [],
            "modified_review": [],
            "duplicate_exact_review": [],
            "relocated": [],
            "deleted": [old],
            "added": [new],
            "movement_review": [],
            "structural_split_merge_review": [],
            "structural_boundary_review": [],
            "resegmentation_review": [],
            "residual_proposals": proposals["proposals"],
            "residual_proposal_groups": proposals["groups"],
        }
        tables = {
            "comparison_stage": s11_routing.EXPECTED_STEP10_STAGE,
            "table_matches": [],
            "deleted_table_candidates": [],
            "added_table_candidates": [],
            "unresolved_table_groups": [],
        }
        generic = s11_routing.build_generic_routing(movement, tables)
        routing = {
            "comparison_stage": "deterministic_review_routing",
            "routes": generic["routes"],
            "non_table_routes": generic["non_table_routes"],
            "table_routes": generic["table_routes"],
            "generic_review_list": generic["generic_review_list"],
            "statistics": {},
        }
        return movement, tables, routing

    def test_one_grouped_uncertainty_entry_preserves_both_candidates(self):
        movement, tables, routing = self._movement_and_routing()
        result = s14_canonical_result.build_comparison_result(
            movement, tables, routing
        )
        self.assertEqual(len(result["changes"]), 2)
        for change in result["changes"]:
            self.assertEqual(change["resolution"]["status"], "review")
            self.assertNotIn("accepted", change["resolution"]["status"])
            self.assertIn("proposal_group_id", change["resolution"])
        grouped = [
            entry for entry in result["review_queue"]
            if entry.get("source_kind") == "residual_proposal_group"
        ]
        self.assertEqual(len(grouped), 1)
        evidence = grouped[0]["evidence"]
        self.assertEqual(set(evidence["candidate_ids"]), {"DEL-1", "ADD-1"})
        self.assertEqual(set(evidence["old_element_ids"]), {"OLD-1"})
        self.assertEqual(set(evidence["new_element_ids"]), {"NEW-1"})
        self.assertIs(evidence["consumes_candidates"], False)
        # No fabricated paired before/after records or accepted one-sided claims.
        self.assertEqual(result["statistics"]["change_type_counts"], {"DELETED": 1, "ADDED": 1})

    def test_report_projection_has_one_uncertain_entry_and_no_invented_pair(self):
        from document_compare.phase1.reporting.report_model import build_report_model

        movement, tables, routing = self._movement_and_routing()
        result = s14_canonical_result.build_comparison_result(
            movement, tables, routing
        )
        report = build_report_model(result, old_document={}, new_document={})
        group_entries = [
            entry for entry in report["uncertain"]
            if entry.get("source_kind") == "residual_proposal_group"
        ]
        self.assertEqual(len(group_entries), 1)
        entry = group_entries[0]
        self.assertEqual(entry.get("before") or "", "")
        self.assertEqual(entry.get("after") or "", "")
        self.assertEqual(
            set((entry.get("evidence") or {}).get("candidate_ids") or []),
            {"DEL-1", "ADD-1"},
        )
        sources = entry.get("unpaired_sources") or {}
        self.assertIs(sources.get("paired"), False)
        self.assertEqual(
            [item["text"] for item in sources["old"]],
            ["The device owner rotates the key."],
        )
        self.assertEqual(
            [item["text"] for item in sources["new"]],
            ["The security administrator rotates the key."],
        )
        self.assertEqual(
            [item["element_id"] for item in sources["old"]], ["OLD-1"]
        )
        self.assertEqual(
            [item["element_id"] for item in sources["new"]], ["NEW-1"]
        )
        self.assertTrue(sources.get("notice"))
        from document_compare.phase1.reporting.html import _entry_card

        markup = _entry_card(entry)
        self.assertIn("The device owner rotates the key.", markup)
        self.assertIn("The security administrator rotates the key.", markup)
        self.assertIn("Old source passages (unpaired)", markup)
        self.assertIn("New source passages (unpaired)", markup)


class PresentationCompatibilityTest(unittest.TestCase):
    JOB = "abcdefabcdef"

    def _legacy_change(self) -> dict:
        return {
            "change_id": "CHANGE-000001",
            "change_type": "MODIFIED",
            "old": {
                "element_id": "OLD-1",
                "text": "Backups are retained for 30 days.",
                "primary_page": 2,
            },
            "new": {
                "element_id": "NEW-1",
                "text": "Backups are retained for 60 days.",
                "primary_page": 2,
            },
            "resolution": {
                "status": "accepted",
                "needs_review": False,
                "case_type": "substantive_modified_candidate",
                "result_state": "supported",
            },
        }

    def _canonical(self) -> dict:
        return {
            "schema_version": "3.0.0",
            "comparison_stage": "phase_1_final_comparison",
            "changes": [self._legacy_change()],
            "table_changes": [],
            "review_queue": [],
            "non_semantic_events": [],
        }

    def test_legacy_supported_record_reads_not_assessed_not_semantic_proof(self):
        from document_compare.phase1.reporting.report_model import build_report_model

        report = build_report_model(self._canonical())
        entry = report["supported"][0]
        self.assertEqual(entry["result_state"], "supported")
        self.assertEqual(entry["semantic_assessment"]["basis"], "not_assessed")
        self.assertEqual(entry["material_relation"], "undetermined")
        self.assertFalse(entry["material_proof"])
        self.assertIn("Source-supported text only", entry["assessment_notice"])

    def test_reader_legacy_projection_is_bounded_and_read_only(self):
        from document_compare.phase2.reader import result_reader as rr

        from document_compare.phase1.contracts.semantic_assessment import sha256_text

        canonical = self._canonical()
        canonical["changes"][0]["semantic_assessment"] = {
            "schema_version": "1.0.0",
            "policy_version": "gateA-1",
            "correspondence": "verified",
            "relation": "material_change",
            "basis": "model_advisory",
            "old_element_id": "OLD-1",
            "new_element_id": "NEW-1",
            "old_text_sha256": sha256_text(
                "Backups are retained for 30 days."
            ),
            "new_text_sha256": sha256_text(
                "Backups are retained for 60 days."
            ),
            "witness": None,
            "guards": {},
            "limitations": [f"limitation-{index}" for index in range(500)],
            "model": {"identity": "shadow", "mode": "advisory"},
        }
        before = copy.deepcopy(canonical)
        out = rr.details(
            self.JOB, canonical, kind="change", record_id="CHANGE-000001"
        )
        self.assertEqual(canonical, before)
        assessment = out["change"]["semantic_assessment"]
        self.assertEqual(assessment["basis"], "model_advisory")
        # Model advice is preserved as bounded metadata, never authority.
        self.assertEqual(assessment["correspondence"], "verified")
        self.assertIn("semantic_assessment", out["change"]["clipped"])
        self.assertLessEqual(
            len(assessment.get("limitations") or []), rr.MAX_METADATA_ITEMS
        )
        summary = rr.summarize(self.JOB, "done", canonical)
        self.assertEqual(
            summary["counts"]["semantic_basis_counts"]["model_advisory"], 1
        )
        legacy = self._canonical()
        legacy_out = rr.details(
            self.JOB, legacy, kind="change", record_id="CHANGE-000001"
        )
        self.assertEqual(
            legacy_out["change"]["semantic_assessment"]["basis"], "not_assessed"
        )
        legacy_summary = rr.summarize(self.JOB, "done", legacy)
        self.assertEqual(
            legacy_summary["counts"]["semantic_basis_counts"]["not_assessed"], 1
        )

    def test_reader_group_review_projection_keeps_typed_anchors_bounded(self):
        from document_compare.phase2.reader import result_reader as rr

        canonical = self._canonical()
        canonical["review_queue"] = [{
            "review_id": "REVIEW-000001",
            "source_kind": "residual_proposal_group",
            "case_type": "unresolved_residual_counterpart",
            "resolution": {
                "status": "review",
                "needs_review": True,
                "review_reasons": ["plausible_unresolved_counterpart"],
                "proposal_group_id": "RESIDUAL-GROUP-000001",
            },
            "evidence": {
                "proposal_group_id": "RESIDUAL-GROUP-000001",
                "change_ids": ["CHANGE-000001"],
                "candidate_ids": ["DEL-1", "ADD-1"],
                "old_element_ids": ["OLD-1"],
                "new_element_ids": ["NEW-1"],
                "correspondence": "proposed",
                "consumes_candidates": False,
            },
        }]
        out = rr.details(
            self.JOB, canonical, kind="review", record_id="REVIEW-000001"
        )
        self.assertEqual(out["review"]["source_correspondence"], "proposed")
        self.assertEqual(out["review"]["semantic_assessment"]["basis"], "not_assessed")
        self.assertEqual(
            out["review"]["evidence"]["proposal_group_id"],
            "RESIDUAL-GROUP-000001",
        )
        self.assertEqual(out["review"]["linked_refs"], [])
        self.assertIn("context", out)


class S09ConservationTest(unittest.TestCase):
    def test_real_stage09_attaches_proposals_without_consuming_residuals(self):
        case = {
            "schema_version": "0.3.0",
            "comparison_stage": INPUT_STAGE,
            "old_document": {"document_id": "OLD-DOC"},
            "new_document": {"document_id": "NEW-DOC"},
            "statistics": {},
            "unchanged": [],
            "modified": [],
            "modified_review": [],
            "duplicate_exact_review": [],
            "deleted": [
                _deleted(
                    "DEL-1",
                    _element(
                        "OLD-1",
                        "The device owner rotates the key.",
                        order=5,
                        context=["heading:key management"],
                    ),
                )
            ],
            "added": [
                _added(
                    "ADD-1",
                    _element(
                        "NEW-1",
                        "The security administrator rotates the key.",
                        order=5,
                        context=["heading:key management"],
                    ),
                )
            ],
        }
        directory = tempfile.mkdtemp(prefix="crr-s09-proposals-")
        root = Path(directory)
        output = root / "output"
        output.mkdir()
        (output / "comparison_candidates.json").write_text(
            json.dumps(case), encoding="utf-8"
        )
        script = f"import runpy\nrunpy.run_path({str(S09)!r}, run_name='__main__')\n"
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CRR_PROJECT_ROOT": str(root),
        }
        completed = subprocess.run(
            [PYTHON, "-c", script],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        try:
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(
                (output / "comparison_candidates_with_movement.json").read_text(
                    encoding="utf-8"
                )
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)

        self.assertEqual([c["candidate_id"] for c in result["deleted"]], ["DEL-1"])
        self.assertEqual([c["candidate_id"] for c in result["added"]], ["ADD-1"])
        self.assertEqual(result["statistics"]["remaining_deleted_candidates"], 1)
        self.assertEqual(result["statistics"]["remaining_added_candidates"], 1)
        self.assertTrue(result["residual_proposals"])
        self.assertEqual(len(result["residual_proposal_groups"]), 1)
        for candidate in result["deleted"] + result["added"]:
            self.assertTrue(candidate["residual_counterpart_proposals"])
            self.assertIn("residual_proposal_coverage", candidate)
            self.assertIn("residual_proposal_group_id", candidate)
            self.assertEqual(
                candidate.get("semantic_assessment", {}).get("basis"),
                "not_assessed",
            )


if __name__ == "__main__":
    unittest.main()
