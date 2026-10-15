"""Generic-only runtime contract tests (constitution task T-01).

The production comparison path must be generic only:

- absent/blank/``auto``/``generic`` domain settings resolve to ``generic``
  without inspecting document content;
- an explicit retired ``crr3`` mode fails with an actionable error;
- CRR verification/evidence helpers are gone and no verifier is ever called;
- packet evidence keeps literal source words such as ``CRR3``/``authorization``
  (source vocabulary is content, not metadata to filter);
- legacy CRR enrichment on routing input cannot change the generic result;
- generic identity/uniqueness contracts still fail loudly;
- routing works with no CRR evidence directory and without importing the
  removed CRR-only utilities.

No network, credentials, DI submission, or model download is involved.
"""
from __future__ import annotations

import copy
import importlib
import json
import unittest
from unittest.mock import patch

from crr_compare import domain, redline
from crr_compare.stages import s11_routing, s12_packets, s14_canonical_result, s15_serve

ROUTING_STAGE = "deterministic_review_routing"

# Strong Regulation (EU) No 575/2013 evidence; document content must no longer
# be able to activate any CRR specialization.
CRR_DOCUMENT = {
    "document": {
        "document_id": "DOC-CRR",
        "elements": [
            {
                "element_id": "EL-1",
                "text": "Regulation (EU) No 575/2013 (CRR3) and 32013R0575",
            }
        ],
    }
}


def _movement(candidate_id="CAND-1"):
    old = {
        "element_id": "OLD-1",
        "element_type": "paragraph",
        "text": "old alpha value",
        "comparison_text": "old alpha value",
        "extraction_validation": {"requires_extraction_review": False},
    }
    new = {
        "element_id": "NEW-1",
        "element_type": "paragraph",
        "text": "new alpha value",
        "comparison_text": "new alpha value",
        "extraction_validation": {"requires_extraction_review": False},
    }
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
        "modified": [
            {
                "candidate_id": candidate_id,
                "old": old,
                "new": new,
                "similarity": 0.9,
                "review_reasons": [],
            }
        ],
        "modified_review": [],
        "duplicate_exact_review": [],
        "relocated": [],
        "deleted": [],
        "added": [],
        "movement_review": [],
        "structural_split_merge_review": [],
        "structural_boundary_review": [],
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


def _routing(movement, tables):
    """Routing artifact shape s14 expects, built through the generic path."""
    generic = s11_routing.build_generic_routing(movement, tables)
    routes = generic["routes"]
    records = s11_routing.build_domain_verification_records(
        routes, {"mode": "generic"}, None
    )
    return {
        "comparison_stage": ROUTING_STAGE,
        "routes": routes,
        "non_table_routes": generic["non_table_routes"],
        "table_routes": generic["table_routes"],
        "generic_review_list": generic["generic_review_list"],
        "domain_profile": {"mode": "generic"},
        "domain_verification": {
            "enabled": False,
            "records": records,
            "status_counts": {},
        },
        "domain_verification_review": [],
        "statistics": {},
    }


class DomainResolutionTest(unittest.TestCase):
    def test_default_and_legacy_auto_are_generic(self):
        for mode in (None, "", "   ", "auto", "AUTO", "generic", "Generic"):
            with self.subTest(mode=mode):
                profile = domain.resolve_domain_profile(
                    mode, CRR_DOCUMENT, CRR_DOCUMENT
                )
                self.assertEqual(profile["mode"], "generic")
                self.assertEqual(domain.parse_domain_mode(mode), "generic")

    def test_explicit_crr3_is_retired(self):
        for mode in ("crr3", "CRR3"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError) as caught:
                    domain.parse_domain_mode(mode)
                self.assertIn("generic", str(caught.exception).lower())
                with self.assertRaises(ValueError):
                    domain.resolve_domain_profile(mode, CRR_DOCUMENT, CRR_DOCUMENT)

    def test_unknown_mode_fails_actionably(self):
        with self.assertRaises(ValueError) as caught:
            domain.parse_domain_mode("legacy_special")
        self.assertIn("generic", str(caught.exception).lower())


class DomainVerificationRecordsTest(unittest.TestCase):
    def test_domain_record_builder_never_calls_verifier(self):
        calls = []

        def verifier(_change):
            calls.append(1)
            return {"status": "authorized"}

        routes = [
            {
                "route_id": "NT-000001",
                "candidate_id": "CAND-1",
                "route": "ACCEPT_DETERMINISTIC",
                "case_type": "substantive_modified_candidate",
                "review_reasons": [],
            }
        ]
        for profile in ({"mode": "generic"}, {"mode": "crr3"}):
            with self.subTest(profile=profile):
                records = s11_routing.build_domain_verification_records(
                    routes, profile, verifier
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["route_id"], "NT-000001")
                self.assertEqual(records[0]["candidate_id"], "CAND-1")
                self.assertEqual(
                    records[0]["domain_verification"]["status"], "not_applicable"
                )
        self.assertEqual(calls, [])

    def test_domain_review_list_is_empty(self):
        records = [
            {"route_id": "NT-000001", "domain_verification": {"status": "ambiguous"}}
        ]
        self.assertEqual(
            s11_routing.build_domain_verification_review(records), []
        )


class PacketSourceTextTest(unittest.TestCase):
    route = {
        "route_id": "NT-000001",
        "candidate_id": "CAND-1",
        "case_type": "substantive_modified_candidate",
        "review_reasons": ["authorization_evidence_required", "legal verification pending"],
        "old_text": "CRR3 authorization legal verification",
        "new_text": "CRR3 authorization legal verification updated",
        "source_kind": None,
    }

    def test_packets_preserve_domain_words_in_source(self):
        packet = s12_packets.payload_for_route(self.route, {"mode": "generic"})
        body = json.loads(packet["text"])
        self.assertIn("CRR3 authorization legal verification", packet["text"])
        self.assertEqual(body["old_text"], "CRR3 authorization legal verification")
        self.assertNotIn("domain_verification", body)
        self.assertEqual(
            body["review_reasons"],
            ["authorization_evidence_required", "legal verification pending"],
        )

    def test_review_reasons_are_never_vocabulary_filtered(self):
        for profile in (None, {"mode": "generic"}, {"mode": "crr3"}):
            with self.subTest(profile=profile):
                self.assertEqual(
                    s12_packets._review_reasons(self.route, profile),
                    ["authorization_evidence_required", "legal verification pending"],
                )


class LegacyEnrichmentTest(unittest.TestCase):
    def test_legacy_enrichment_does_not_change_generic_result(self):
        movement = _movement()
        movement["modified"][0]["review_reasons"] = ["generic_review_reason"]
        tables = _empty_tables()
        routing = _routing(movement, tables)

        clean = s14_canonical_result.build_comparison_result(movement, tables, routing)

        enriched_routing = copy.deepcopy(routing)
        for record in enriched_routing["domain_verification"]["records"]:
            record["domain_verification"] = {
                "status": "verified",
                "article": "99",
                "source_locations": ["legacy"],
            }
        enriched_routing["domain_verification_review"] = [
            {
                "route_id": routing["routes"][0]["route_id"],
                "domain_verification": {"status": "ambiguous"},
            }
        ]
        enriched = s14_canonical_result.build_comparison_result(
            movement, tables, enriched_routing
        )

        self.assertEqual(
            s14_canonical_result.generic_projection(clean),
            s14_canonical_result.generic_projection(enriched),
        )
        self.assertEqual(enriched["domain_verification_review"], [])
        for item in enriched["changes"] + enriched["table_changes"]:
            self.assertEqual(
                (item.get("domain_verification") or {}).get(
                    "status", "not_applicable"
                ),
                "not_applicable",
            )
        # Generic review reasons survive untouched.
        self.assertTrue(clean["changes"])
        self.assertTrue(clean["review_queue"])
        self.assertIn(
            "generic_review_reason",
            clean["review_queue"][0]["resolution"]["review_reasons"],
        )

    def test_duplicate_generic_identity_still_fails(self):
        movement = _movement()
        tables = _empty_tables()
        routing = _routing(movement, tables)
        first = routing["routes"][0]

        duplicate_candidate = copy.deepcopy(routing)
        duplicate_candidate["routes"] = routing["routes"] + [
            {**copy.deepcopy(first), "route_id": "NT-999999"}
        ]
        with self.assertRaises(ValueError):
            s14_canonical_result.build_comparison_result(
                movement, tables, duplicate_candidate
            )

        duplicate_route = copy.deepcopy(routing)
        duplicate_route["routes"] = routing["routes"] + [
            {**copy.deepcopy(first), "candidate_id": "CAND-2"}
        ]
        with self.assertRaises(ValueError):
            s14_canonical_result.build_comparison_result(
                movement, tables, duplicate_route
            )


class NoCrrEvidenceDependencyTest(unittest.TestCase):
    def test_no_crr_evidence_dependency(self):
        self.assertFalse(hasattr(domain, "detect_crr3"))
        self.assertFalse(hasattr(s11_routing, "evidence_dir"))
        self.assertFalse(hasattr(s11_routing, "crr3_verifier"))
        self.assertFalse(hasattr(s11_routing, "AUTHORIZATION_INDEX_PATH"))

        for module in ("crr_compare.authorization", "crr_compare.release_audit"):
            with self.subTest(module=module):
                with self.assertRaises(ImportError):
                    importlib.import_module(module)

        movement = _movement()
        tables = _empty_tables()
        with patch.dict(
            "os.environ", {"CRR_EVIDENCE_DIR": "/nonexistent/crr/evidence"}, clear=False
        ):
            generic = s11_routing.build_generic_routing(movement, tables)
            records = s11_routing.build_domain_verification_records(
                generic["routes"], {"mode": "generic"}, None
            )
        self.assertTrue(records)
        self.assertTrue(all(
            r["domain_verification"]["status"] == "not_applicable" for r in records
        ))


class StaleMetadataLeakTest(unittest.TestCase):
    """Stale CRR verification metadata must never reach fresh output."""

    def test_stale_crr3_profile_and_verification_are_ignored(self):
        movement = _movement()
        movement["modified"][0]["review_reasons"] = ["generic_review_reason"]
        tables = _empty_tables()
        clean_routing = _routing(movement, tables)
        clean = s14_canonical_result.build_comparison_result(
            movement, tables, clean_routing
        )

        result = None
        for status in ("authorized", "verified", "ambiguous", "unsupported"):
            with self.subTest(status=status):
                stale = copy.deepcopy(clean_routing)
                stale["domain_profile"] = {
                    "mode": "crr3",
                    "source": "explicit",
                    "confidence": "confident",
                }
                stale["domain_verification"]["enabled"] = True
                for record in stale["domain_verification"]["records"]:
                    record["domain_verification"] = {
                        "status": status,
                        "article": "99",
                        "source_locations": ["legacy"],
                    }
                stale["domain_verification_review"] = [
                    {
                        "route_id": clean_routing["routes"][0]["route_id"],
                        "domain_verification": {"status": status},
                    }
                ]
                result = s14_canonical_result.build_comparison_result(
                    movement, tables, stale
                )
                self.assertEqual(result["domain_profile"]["mode"], "generic")
                self.assertEqual(result["domain_verification_review"], [])
                for item in result["changes"] + result["table_changes"]:
                    self.assertNotIn("domain_verification", item)
                self.assertEqual(
                    s14_canonical_result.generic_projection(result),
                    s14_canonical_result.generic_projection(clean),
                )

        # Source text and generic review reasons survive unchanged.
        self.assertEqual(result["changes"][0]["old"]["text"], "old alpha value")
        self.assertIn(
            "generic_review_reason",
            result["review_queue"][0]["resolution"]["review_reasons"],
        )

    def test_serving_index_never_copies_legacy_verification(self):
        comparison = {
            "schema_version": "3.0.0",
            "changes": [
                {
                    "change_id": "CHANGE-000001",
                    "change_type": "MODIFIED",
                    "resolution": {"status": "accepted", "needs_review": False},
                    "old": {
                        "element_id": "OLD-1",
                        "primary_page": 1,
                        "text": "CRR3 authorization legal verification",
                    },
                    "new": {
                        "element_id": "NEW-1",
                        "primary_page": 2,
                        "text": "CRR3 authorization legal verification updated",
                    },
                    "domain_verification": {"status": "verified", "article": "99"},
                }
            ],
            "table_changes": [],
            "non_semantic_events": [],
            "review_queue": [],
        }
        document = {"document": {"elements": [], "provisional_anchors": []}}
        index = s15_serve.build_phase2_index(comparison, document, document)
        self.assertEqual(len(index["change_units"]), 1)
        unit = index["change_units"][0]
        self.assertNotIn("domain_verification", unit)
        self.assertEqual(unit["old"]["text"], "CRR3 authorization legal verification")
        self.assertEqual(
            unit["new"]["text"], "CRR3 authorization legal verification updated"
        )

    def test_redline_never_renders_legacy_domain_status(self):
        item = {
            "change_id": "CHANGE-000001",
            "change_type": "MODIFIED",
            "review_reasons": ["authorization_evidence_required"],
            "resolution": {"status": "review", "needs_review": True},
            "domain_verification": {"status": "verified", "article": "99"},
        }
        rendered = redline.comment_rows(item)
        self.assertNotIn("Domain status", rendered)
        self.assertNotIn("verified", rendered)
        self.assertIn("authorization_evidence_required", rendered)

        record = {
            "table_change_id": "TABLE-CHANGE-00001",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"source_pages": [1]},
            "new": {"source_pages": [2]},
            "cell_changes": [],
            "review_reasons": ["cell_or_row_boundary_changed"],
            "resolution": {"status": "review", "needs_review": True},
            "domain_verification": {"status": "ambiguous"},
        }
        table_rendered = redline.render_table_change(record)
        self.assertNotIn("Domain status", table_rendered)
        self.assertNotIn("ambiguous", table_rendered)
        self.assertIn("cell_or_row_boundary_changed", table_rendered)


if __name__ == "__main__":
    unittest.main()
