"""Regression: a PROVEN end-of-line hyphen-position-only diff must not surface as
a ``MODIFIED`` (review) semantic change, but an ambiguous hyphen/space difference
must stay in review.

Approved conservative policy: only line-wrap noise proven by the raw source DI
may disappear. A hyphen/space-only token difference on the same line, spelling
similarity, and case folding are NOT proof. The previous policy stripped all
hyphen variants and whitespace (and case-folded) and therefore silently
suppressed genuinely semantic differences (``re-sign``/``resign``,
``co-operating``/``cooperating``, ``now here``/``nowhere``, ``BaFin``/``Bafin``)
and the space-only ``ge- handhabt`` flattening. That was incorrect and is
replaced: those now route to HUMAN_REVIEW.

A true wrap artifact requires a hyphen directly before a real line break in the
raw (non-normalized) source ``text`` whose joined word equals the changed
content. Only then is the diff classified non-semantic. The original source
text is preserved (never rewritten).
"""
import unittest

from document_compare.phase1.stages import s11_routing, s14_canonical_result

ROUTING_STAGE = "deterministic_review_routing"
CANDIDATE_ID = "MODIFIED-CANDIDATE-000001"


def _element(element_id, text, order=1, *, normalized=None, comparison=None):
    return {
        "element_id": element_id,
        "element_type": "paragraph",
        "text": text,
        "normalized_text": normalized if normalized is not None else text,
        "comparison_text": comparison if comparison is not None else text,
        "reading_order": order,
        "primary_page": 1,
        "extraction_validation": {"requires_extraction_review": False},
    }


def _candidate(
    old_text,
    new_text,
    old_tokens,
    new_tokens,
    *,
    source=None,
    normalized=None,
    old_raw=None,
    new_raw=None,
    old_comparison=None,
    new_comparison=None,
):
    """Real s11/s14 candidate payload (comparison plus optional source diff).

    ``old_raw``/``new_raw`` set the raw (non-normalized) ``text`` used for
    line-break provenance, while ``old_text``/``new_text`` remain the default
    normalized/comparison text.
    """
    comparison = {"changes": [{"old_tokens": old_tokens, "new_tokens": new_tokens}]}
    source = source or comparison
    normalized = normalized or comparison
    return {
        "candidate_id": CANDIDATE_ID,
        "review_reasons": [],
        "similarity": 0.99,
        "old": _element(
            "OLD-1",
            old_raw if old_raw is not None else old_text,
            comparison=old_comparison if old_comparison is not None else old_text,
        ),
        "new": _element(
            "NEW-1",
            new_raw if new_raw is not None else new_text,
            comparison=new_comparison if new_comparison is not None else new_text,
        ),
        "token_diff": {
            "source_token_diff": source,
            "normalized_token_diff": normalized,
            "comparison_token_diff": comparison,
        },
    }


def _candidate_multi(old_text, new_text, changes, *, old_raw=None, new_raw=None):
    """Candidate with multiple explicit comparison change operations."""
    comparison = {"changes": changes}
    return {
        "candidate_id": CANDIDATE_ID,
        "review_reasons": [],
        "similarity": 0.99,
        "old": _element("OLD-1", old_raw if old_raw is not None else old_text),
        "new": _element("NEW-1", new_raw if new_raw is not None else new_text),
        "token_diff": {
            "source_token_diff": comparison,
            "normalized_token_diff": comparison,
            "comparison_token_diff": comparison,
        },
    }


def _empty_tables():
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP10_STAGE,
        "table_matches": [],
        "deleted_table_candidates": [],
        "added_table_candidates": [],
        "unresolved_table_groups": [],
    }


def _movement(candidate):
    return {
        "comparison_stage": s11_routing.EXPECTED_STEP9_STAGE,
        "modified": [candidate],
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


def _full_routing(movement, tables):
    """Wrap build_generic_routing into the routing artifact shape s14 expects."""
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
        "domain_verification": {"enabled": False, "records": records, "status_counts": {}},
        "domain_verification_review": [],
        "statistics": {},
    }


def _canonical(candidate):
    movement = _movement(candidate)
    tables = _empty_tables()
    routing = _full_routing(movement, tables)
    return s14_canonical_result.build_comparison_result(movement, tables, routing)


def _changes_for(canonical, element_id="OLD-1"):
    return [
        change
        for change in canonical["changes"]
        if (change.get("old") or {}).get("element_id") == element_id
    ]


class ProvenWrapArtifactTest(unittest.TestCase):
    def test_true_lowercase_newline_wrap_is_proven_and_suppressed(self):
        # Raw old source proves a real line break ("ge-\nhandhabt"); the
        # comparison representation was normalized to "gehandhabt" while the
        # new side carries the flattened "ge- handhabt".
        candidate = _candidate(
            "gehandhabt",
            "ge- handhabt",
            ["gehandhabt"],
            ["ge", "-", "handhabt"],
            old_raw="ge-\nhandhabt",
            new_raw="ge- handhabt",
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertTrue(s11_routing.source_proven_line_wrap(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "SPECIAL_CASE_RULE", (route, case_type, reasons))
        self.assertEqual(case_type, "hyphenation_wrap_artifact", reasons)
        self.assertIn("hyphenation_wrap_source_line_break_proven", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(_changes_for(canonical), [], canonical["changes"])
        artifacts = [
            event
            for event in canonical["non_semantic_events"]
            if (event.get("resolution") or {}).get("case_type") == "hyphenation_wrap_artifact"
        ]
        self.assertEqual(len(artifacts), 1, canonical["non_semantic_events"])
        self.assertFalse(artifacts[0]["resolution"]["needs_review"])
        # The original raw source is preserved, not rewritten.
        self.assertEqual(artifacts[0]["old"]["text"], "ge-\nhandhabt")


class AmbiguousHyphenStaysReviewTest(unittest.TestCase):
    def test_space_only_ge_handhabt_is_not_proven_and_stays_review(self):
        # Exact prior-policy sample: source DI has only a space, not a newline.
        # Per the corrected policy this is ambiguous and must stay review.
        candidate = _candidate(
            "gehandhabt", "ge- handhabt", ["gehandhabt"], ["ge", "-", "handhabt"]
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.source_proven_line_wrap(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertEqual(case_type, "hyphenation_wrap_artifact", reasons)
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertEqual(changes[0]["change_type"], "MODIFIED")
        self.assertTrue(changes[0]["resolution"]["needs_review"])

    def test_re_sign_vs_resign_stays_review(self):
        candidate = _candidate(
            "Please re-sign the contract.",
            "Please resign the contract.",
            ["re-sign"],
            ["resign"],
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertEqual(case_type, "hyphenation_wrap_artifact", reasons)
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertTrue(changes[0]["resolution"]["needs_review"])

    def test_co_operating_vs_cooperating_stays_review(self):
        candidate = _candidate(
            "The co-operating banks report.",
            "The cooperating banks report.",
            ["co-operating"],
            ["cooperating"],
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        self.assertTrue(_changes_for(canonical)[0]["resolution"]["needs_review"])

    def test_whole_word_boundary_now_here_vs_nowhere_stays_review(self):
        candidate = _candidate("now here", "nowhere", ["now", "here"], ["nowhere"])
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        self.assertTrue(_changes_for(canonical)[0]["resolution"]["needs_review"])

    def test_case_only_difference_is_not_masked_as_an_artifact(self):
        candidate = _candidate("BaFin", "Bafin", ["BaFin"], ["Bafin"])
        self.assertFalse(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.word_split_like(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertNotEqual(
            (route, case_type), ("SPECIAL_CASE_RULE", "hyphenation_wrap_artifact"), reasons
        )
        self.assertNotEqual(
            (route, case_type),
            ("SPECIAL_CASE_RULE", "possible_extraction_word_split"),
            reasons,
        )

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertTrue(changes[0]["resolution"]["needs_review"])


class MixedBoundaryProvenanceTest(unittest.TestCase):
    """Suppression requires ALL differing boundaries to be source-proven.

    A single matching source line break must never prove an aggregate diff
    that also contains an ambiguous same-line hyphen or word boundary.
    """

    def test_mixed_proven_and_unproven_operations_stays_review(self):
        # Operation 1 is a real line-wrap (raw old "ge-\nhandhabt").
        # Operation 2 is a semantic hyphen only (re-sign -> resign) with no
        # source line break. One proof must not hide the other.
        changes = [
            {"old_tokens": ["gehandhabt"], "new_tokens": ["ge", "-", "handhabt"]},
            {"old_tokens": ["re-sign"], "new_tokens": ["resign"]},
        ]
        candidate = _candidate_multi(
            "gehandhabt re-sign",
            "ge- handhabt resign",
            changes,
            old_raw="ge-\nhandhabt re-sign",
            new_raw="ge- handhabt resign",
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.source_proven_line_wrap(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        self.assertTrue(_changes_for(canonical)[0]["resolution"]["needs_review"])

    def test_unrelated_source_wrap_substring_does_not_prove(self):
        # The only source line break ("ge-\no") joins to "geo", which is a
        # substring of the aggregate changed content "geotherm" but is NOT the
        # changed boundary. Substring similarity is not proof.
        changes = [
            {"old_tokens": ["geotherm"], "new_tokens": ["ge", "-", "otherm"]}
        ]
        candidate = _candidate_multi(
            "geotherm",
            "ge- otherm",
            changes,
            old_raw="prefix ge-\no suffix",
            new_raw="ge- otherm",
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.source_proven_line_wrap(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertIn("hyphenation_wrap_unproven_source", reasons)

    def test_uppercase_continuation_line_break_is_not_proven(self):
        # Wrap-like (case preserved), but the line-break continuation is
        # uppercase: the approved rule joins only lowercase continuations.
        candidate = _candidate(
            "geHandhabt",
            "ge- Handhabt",
            ["geHandhabt"],
            ["ge", "-", "Handhabt"],
            old_raw="ge-\nHandhabt",
            new_raw="ge- Handhabt",
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.source_proven_line_wrap(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))

    def test_digit_continuation_line_break_is_not_proven(self):
        candidate = _candidate(
            "ge123",
            "ge- 123",
            ["ge123"],
            ["ge", "-", "123"],
            old_raw="ge-\n123",
            new_raw="ge- 123",
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))
        self.assertFalse(s11_routing.source_proven_line_wrap(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))


class GenuineHyphenChangeTest(unittest.TestCase):
    def test_genuine_change_inside_hyphenated_word_stays_modified(self):
        candidate = _candidate(
            "Das Risiko wird im Kredit-Risiko erfasst.",
            "Das Risiko wird im Kredit-Bank erfasst.",
            ["Kredit-Risiko"],
            ["Kredit-Bank"],
        )
        self.assertFalse(s11_routing.wrap_artifact_like(candidate))

        canonical = _canonical(candidate)
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertEqual(changes[0]["change_type"], "MODIFIED")
        self.assertEqual(changes[0]["resolution"]["status"], "accepted")
        self.assertEqual(canonical["non_semantic_events"], [])

    def test_extra_word_change_alongside_hyphen_stays_modified(self):
        candidate = _candidate(
            "sachgerecht gehandhabt werden können",
            "sachgerecht ge- handhabt wurden können",
            ["gehandhabt", "werden"],
            ["ge", "-", "handhabt", "wurden"],
        )
        self.assertFalse(s11_routing.wrap_artifact_like(candidate))

        canonical = _canonical(candidate)
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertEqual(changes[0]["change_type"], "MODIFIED")
        self.assertEqual(canonical["non_semantic_events"], [])

    def test_semantic_signal_hidden_in_source_representation_is_not_suppressed(self):
        comparison = {
            "changes": [{"old_tokens": ["gehandhabt"], "new_tokens": ["ge", "-", "handhabt"]}]
        }
        source = {
            "changes": [
                {
                    "old_tokens": ["12a", "gehandhabt"],
                    "new_tokens": ["15b", "ge", "-", "handhabt"],
                }
            ]
        }
        candidate = _candidate(
            "12a gehandhabt",
            "15b ge- handhabt",
            ["gehandhabt"],
            ["ge", "-", "handhabt"],
            source=source,
            normalized=source,
        )
        self.assertTrue(s11_routing.wrap_artifact_like(candidate))

        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "UNCERTAIN_RESULT", (route, case_type, reasons))
        self.assertIn("hyphenation_wrap_with_semantic_signal", reasons)

        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        changes = _changes_for(canonical)
        self.assertEqual(len(changes), 1, canonical["changes"])
        self.assertTrue(changes[0]["resolution"]["needs_review"])


class StaleRouteDefenseTest(unittest.TestCase):
    def test_s14_does_not_suppress_wrap_without_provenance(self):
        """A stale/unsafe wrap route without source provenance stays review."""
        resolution = s14_canonical_result._resolve(
            "MODIFIED-CANDIDATE-STALE-WRAP",
            {
                "MODIFIED-CANDIDATE-STALE-WRAP": {
                    "route_id": "NT-000001",
                    "route": "SPECIAL_CASE_RULE",
                    "case_type": "hyphenation_wrap_artifact",
                    "review_reasons": ["hyphenation_wrap_position_only"],
                }
            },
            {},
        )
        self.assertEqual(resolution["status"], "review", resolution)
        self.assertTrue(resolution["needs_review"])

    def test_s14_suppresses_wrap_with_provenance(self):
        resolution = s14_canonical_result._resolve(
            "MODIFIED-CANDIDATE-PROVEN-WRAP",
            {
                "MODIFIED-CANDIDATE-PROVEN-WRAP": {
                    "route_id": "NT-000001",
                    "route": "SPECIAL_CASE_RULE",
                    "case_type": "hyphenation_wrap_artifact",
                    "review_reasons": [
                        "hyphenation_wrap_position_only",
                        "hyphenation_wrap_source_line_break_proven",
                    ],
                }
            },
            {},
        )
        self.assertEqual(resolution["status"], "suppressed_artifact", resolution)
        self.assertFalse(resolution["needs_review"])


if __name__ == "__main__":
    unittest.main()
