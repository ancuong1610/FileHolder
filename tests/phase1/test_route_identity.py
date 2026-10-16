"""Regression tests for structural-evidence route identity (Phase 1, Task 1).

The exact screenshot defect: an original comparison candidate and a structural
boundary evidence record that refers to it shared the same ``candidate_id``,
so Stage 11 produced two routes with one identity and Stage 14's duplicate
identity validation stopped the pipeline. Structural evidence must carry its
own identity (its ``review_id`` when present) while preserving the link back
to the source candidate, and ordinary/table identities must not change.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_compare.phase1.stages import s11_routing, s12_packets, s14_canonical_result

SOURCE_CANDIDATE = "MODIFIED-CANDIDATE-000083"
EVIDENCE_REVIEW = "STRUCTURAL-BOUNDARY-000001"
ROUTING_STAGE = "deterministic_review_routing"


def _element(element_id, text, element_type="paragraph", order=1):
    return {
        "element_id": element_id,
        "element_type": element_type,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "reading_order": order,
        "primary_page": 1,
        "extraction_validation": {"requires_extraction_review": False},
    }


def _boundary_evidence(review_id=EVIDENCE_REVIEW, candidate_id=SOURCE_CANDIDATE):
    return {
        "review_id": review_id,
        "candidate_id": candidate_id,
        "classification": "STRUCTURAL_BOUNDARY_REVIEW",
        "needs_review": True,
        "review_reasons": ["possible_structural_split_merge_boundary"],
        "old_element_id": "OLD-1",
        "new_element_id": "NEW-1",
        "evidence": {"evidence_type": "short_exact_structural_boundary_fragment"},
    }


def _movement_with_boundary_evidence(boundary_reviews=None):
    modified = {
        "candidate_id": SOURCE_CANDIDATE,
        "old": _element("OLD-1", "The data protection officer shall review this policy annually."),
        "new": _element("NEW-1", "The data protection officer shall review this policy biannually."),
        "similarity": 0.959,
    }
    if boundary_reviews is None:
        boundary_reviews = [_boundary_evidence()]
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
        "modified": [modified],
        "modified_review": [],
        "duplicate_exact_review": [],
        "relocated": [],
        "deleted": [],
        "added": [],
        "movement_review": [],
        "structural_split_merge_review": [],
        "structural_boundary_review": boundary_reviews,
        "resegmentation_review": [],
    }


def _empty_tables():
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP10_STAGE,
        "table_matches": [],
        "deleted_table_candidates": [],
        "added_table_candidates": [],
        "unresolved_table_groups": [],
    }


def _full_routing(movement, tables):
    """Wrap build_generic_routing into the routing artifact shape s14 expects."""
    generic = s11_routing.build_generic_routing(movement, tables)
    routes = generic["routes"]
    records = s11_routing.build_domain_verification_records(
        routes, {"mode": "generic"}, None
    )
    return {
        "comparison_stage": ROUTING_STAGE,
        "schema_version": "1.0.0",
        "routes": routes,
        "non_table_routes": generic["non_table_routes"],
        "table_routes": generic["table_routes"],
        "generic_review_list": generic["generic_review_list"],
        "domain_profile": {"mode": "generic"},
        "domain_verification": {"enabled": False, "records": records, "status_counts": {}},
        "domain_verification_review": [],
        "statistics": {},
    }


class ScreenshotReproductionTest(unittest.TestCase):
    def test_boundary_evidence_gets_own_identity_and_keeps_source_link(self):
        movement = _movement_with_boundary_evidence()
        tables = _empty_tables()
        routing = _full_routing(movement, tables)
        routes = routing["routes"]

        candidate_ids = [route["candidate_id"] for route in routes]
        self.assertEqual(
            len(candidate_ids), len(set(candidate_ids)),
            f"structural evidence collided with its source candidate: {candidate_ids}",
        )
        # Stage 14 duplicate identity validation must no longer fire.
        s14_canonical_result._validate_candidate_uniqueness(routes)

        boundary_route = next(
            route for route in routes
            if route["source_group"] == "structural_boundary_review"
        )
        self.assertEqual(boundary_route["candidate_id"], EVIDENCE_REVIEW)
        self.assertEqual(boundary_route["source_candidate_id"], SOURCE_CANDIDATE)

        result = s14_canonical_result.build_comparison_result(movement, tables, routing)

        # The original change and the structural review both survive.
        change_sources = {c["source_candidate_id"] for c in result["changes"]}
        self.assertIn(SOURCE_CANDIDATE, change_sources)
        self.assertEqual(len(result["changes"]), 1)

        boundary_review = next(
            entry for entry in result["review_queue"]
            if entry.get("source_evidence_id") == EVIDENCE_REVIEW
        )
        self.assertEqual(boundary_review["source_candidate_id"], SOURCE_CANDIDATE)
        self.assertEqual(boundary_review["resolution"]["case_type"], "structural_boundary")
        self.assertTrue(boundary_review["resolution"]["needs_review"])
        # The evidence resolution belongs to the evidence route, not the
        # accepted modified candidate route.
        self.assertEqual(boundary_review["resolution"]["route"], "SPECIAL_CASE_RULE")


class MultipleEvidenceSameSourceTest(unittest.TestCase):
    def test_two_evidence_items_may_share_one_source_candidate(self):
        second = _boundary_evidence(
            review_id="STRUCTURAL-BOUNDARY-000002", candidate_id=SOURCE_CANDIDATE
        )
        movement = _movement_with_boundary_evidence(
            [_boundary_evidence(), second]
        )
        tables = _empty_tables()
        routing = _full_routing(movement, tables)

        candidate_ids = [route["candidate_id"] for route in routing["routes"]]
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)), candidate_ids)
        boundary_routes = [
            route for route in routing["routes"]
            if route["source_group"] == "structural_boundary_review"
        ]
        self.assertEqual(len(boundary_routes), 2)
        for route in boundary_routes:
            self.assertEqual(route["source_candidate_id"], SOURCE_CANDIDATE)

        result = s14_canonical_result.build_comparison_result(movement, tables, routing)
        evidence_ids = {entry.get("source_evidence_id") for entry in result["review_queue"]}
        self.assertIn(EVIDENCE_REVIEW, evidence_ids)
        self.assertIn("STRUCTURAL-BOUNDARY-000002", evidence_ids)
        for entry in result["review_queue"]:
            self.assertEqual(entry.get("source_candidate_id"), SOURCE_CANDIDATE)


class LegacyAndMissingIdentityTest(unittest.TestCase):
    def test_legacy_evidence_without_review_id_uses_candidate_id(self):
        from document_compare.phase1.contracts.review_identity import structural_route_identity

        identity = structural_route_identity({"candidate_id": "LEGACY-STRUCT-EVIDENCE-1"})
        self.assertEqual(identity["candidate_id"], "LEGACY-STRUCT-EVIDENCE-1")
        self.assertIsNone(identity["source_candidate_id"])

        movement = _movement_with_boundary_evidence([
            {
                "candidate_id": "LEGACY-STRUCT-EVIDENCE-1",
                "classification": "STRUCTURAL_BOUNDARY_REVIEW",
                "review_reasons": ["legacy_evidence"],
            }
        ])
        routing = _full_routing(movement, _empty_tables())
        route = next(
            route for route in routing["routes"]
            if route["source_group"] == "structural_boundary_review"
        )
        self.assertEqual(route["candidate_id"], "LEGACY-STRUCT-EVIDENCE-1")
        self.assertNotIn("source_candidate_id", route)

    def test_missing_identity_fields_raise_value_error(self):
        from document_compare.phase1.contracts.review_identity import structural_route_identity

        with self.assertRaises(ValueError):
            structural_route_identity({})

        movement = _movement_with_boundary_evidence([
            {"review_reasons": ["no_identity_at_all"]}
        ])
        with self.assertRaises(ValueError):
            s11_routing.build_generic_routing(movement, _empty_tables())

        # s14 consumes the same identity contract and must fail clearly too.
        with self.assertRaises(ValueError):
            s14_canonical_result._consumed_candidate_ids(movement, _empty_tables())


class GenuineDuplicateStillRaisesTest(unittest.TestCase):
    def test_duplicate_ordinary_candidate_routes_still_raise(self):
        movement = _movement_with_boundary_evidence()
        movement["modified"].append(copy.deepcopy(movement["modified"][0]))
        routing = _full_routing(movement, _empty_tables())
        with self.assertRaises(ValueError):
            s14_canonical_result._validate_candidate_uniqueness(routing["routes"])


class PacketPayloadTest(unittest.TestCase):
    def test_payload_resolves_structural_evidence_by_review_identity(self):
        movement = _movement_with_boundary_evidence()
        tables = _empty_tables()
        routing = _full_routing(movement, tables)
        route = next(
            route for route in routing["routes"]
            if route["source_group"] == "structural_boundary_review"
        )
        self.assertEqual(route["candidate_id"], EVIDENCE_REVIEW)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "output"
            out.mkdir(parents=True)
            (out / "comparison_candidates_with_movement.json").write_text(
                json.dumps(movement), encoding="utf-8"
            )
            (out / "table_comparison_result.json").write_text(
                json.dumps(tables), encoding="utf-8"
            )
            s12_packets._INDEXES.clear()
            try:
                with patch.object(s12_packets, "find_project_root", return_value=root):
                    packet = s12_packets.payload_for_route(route)
            finally:
                s12_packets._INDEXES.clear()

        # The envelope carries the evidence identity; the review body is the
        # canonical JSON serialized in ``text`` (the documented s12 contract).
        self.assertEqual(packet["candidate_id"], EVIDENCE_REVIEW)
        body = json.loads(packet["text"])
        self.assertEqual(body["source_group"], "structural_boundary_review")
        self.assertEqual(body["evidence"]["candidate_id"], SOURCE_CANDIDATE)
        self.assertEqual(body["evidence"]["review_id"], EVIDENCE_REVIEW)
        self.assertEqual(body["source_candidate_id"], SOURCE_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
