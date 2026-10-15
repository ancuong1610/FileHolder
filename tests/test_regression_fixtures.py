"""Task 4 regression coverage: important text/tables survive pipeline output.

TDD note: the tests were run RED before the fixture JSON existed (the runner
raises ``FileNotFoundError`` for the missing committed pair) and GREEN after
``write_all_fixtures()`` produced the committed fixtures. The narrative text
cases and every table case pass.

Task 4 discovery proved a concrete defect: a pure numeric/reference token
difference (``30``->``60``, a date, a percentage, ``Article 12``->``Article
15``) was classified ``possible_marker_or_version_marker_difference`` and
resolved ``structural_only``, so it was absent from ``changes``, the review
queue, the CSV and the redline. Task 4b's semantic-safety repair fixed the
routing; ``NumericValueSuppressionDefectTest`` is the regression that captured
that defect and ``SemanticSignalRoutingTest`` pins the route decision and the
preservation of a known letter-prefixed margin marker. See
``sdd/task-4b-report.md`` for the evidence.

The fixtures and the offline runner live in ``tests/regression_support.py``.
Every pipeline run is isolated in a temporary root with the mock evaluator and
the generic domain; no network, credentials, or stage imports are involved.
"""
from __future__ import annotations

import json
import unittest

from tests.regression_support import (
    FIXTURE_NAMES,
    FIXTURE_ROOT,
    TEXT_CASES,
    documents,
    run_fixture,
)

NUMERIC_CASES = {"retention_days", "effective_date", "percentage", "reference"}
WORDING_CASES = {"not_added", "not_removed", "shall_to_may"}


def _text_change(canonical: dict, old_text: str, new_text: str):
    for change in canonical.get("changes") or []:
        old = (change.get("old") or {}).get("text")
        new = (change.get("new") or {}).get("text")
        if old == old_text and new == new_text:
            return change
    return None


def _non_semantic_event(canonical: dict, old_text: str, new_text: str):
    for event in canonical.get("non_semantic_events") or []:
        old = (event.get("old") or {}).get("text")
        new = (event.get("new") or {}).get("text")
        if old == old_text and new == new_text:
            return event
    return None


def _table_changes_with_cell(canonical: dict, old_needle: str, new_needle: str | None = None):
    found = []
    for table_change in canonical.get("table_changes") or []:
        for cell in table_change.get("cell_changes") or []:
            old = (cell.get("old") or {}).get("text") or ""
            new = (cell.get("new") or {}).get("text") or ""
            if old_needle in old and (new_needle is None or new_needle in new):
                found.append((table_change, cell))
    return found


class FixtureReproducibilityTest(unittest.TestCase):
    """Committed fixtures must match the declarative builder exactly."""

    def test_committed_fixtures_match_builder(self):
        for kind in FIXTURE_NAMES:
            old, new = documents(kind)
            for side, expected in (("old", old), ("new", new)):
                path = FIXTURE_ROOT / kind / f"{side}_di_raw.json"
                self.assertTrue(path.is_file(), path)
                committed = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(committed, expected, f"{kind}/{side} drifted")


class TextValueRegressionTest(unittest.TestCase):
    """Wording changes (inserted/removed ``not``, ``shall``->``may``) survive."""

    @classmethod
    def setUpClass(cls):
        cls.artifacts = run_fixture("text_values")
        cls.canonical = cls.artifacts["canonical"]
        cls.redline = cls.artifacts["redline"]

    def _cases(self):
        return [case for case in TEXT_CASES if case[0] in WORDING_CASES]

    def test_every_wording_change_is_preserved_with_both_literals(self):
        missing = []
        for name, old_text, new_text in self._cases():
            if _text_change(self.canonical, old_text, new_text) is None:
                missing.append(name)
        self.assertEqual(missing, [], f"text changes not preserved: {missing}")

    def test_wording_change_types_are_honest_modified_records(self):
        for name, old_text, new_text in self._cases():
            change = _text_change(self.canonical, old_text, new_text)
            self.assertIsNotNone(change, name)
            self.assertEqual(change["change_type"], "MODIFIED", name)
            status = (change.get("resolution") or {}).get("status")
            self.assertIn(status, {"accepted", "review"}, name)

    def test_wording_literals_appear_in_redline(self):
        for name, old_text, new_text in self._cases():
            self.assertIn(old_text, self.redline, f"{name}: old literal missing")
            self.assertIn(new_text, self.redline, f"{name}: new literal missing")

    def test_manifest_and_csv_reflect_canonical_counts(self):
        manifest = self.artifacts["manifest"]
        self.assertEqual(manifest["changes"], len(self.canonical["changes"]))
        self.assertEqual(manifest["table_changes"], len(self.canonical["table_changes"]))
        self.assertEqual(len(self.artifacts["csv_rows"]), len(self.canonical["review_queue"]))

    def test_stage15_writes_both_pdfs_manifest_and_audit(self):
        import re

        from crr_compare.service import ARTIFACT_NAMES

        # run_fixture cleans its temporary root after loading the artifacts.
        # Inspect values captured from the actual files before cleanup.
        self.assertEqual(self.artifacts["redline_pdf_signature"], b"%PDF-")
        self.assertEqual(self.artifacts["inline_pdf_signature"], b"%PDF-")

        manifest = self.artifacts["manifest"]
        self.assertEqual(manifest["redline_pdf"], "comparison_redline.pdf")
        self.assertEqual(manifest["inline_pdf"], "comparison_inline.pdf")
        self.assertEqual(manifest["inline_pdf_audit"], "inline_pdf_audit.json")
        self.assertIn("comparison_inline.pdf", ARTIFACT_NAMES)
        self.assertIn("inline_pdf_audit.json", ARTIFACT_NAMES)

        audit = self.artifacts["inline_audit"]
        self.assertEqual(audit["totals"]["changes"], len(self.canonical["changes"]))
        self.assertEqual(audit["totals"]["review_items"], len(self.canonical["review_queue"]))

        # The inline PDF is a real one-flow render: it carries change ids and the
        # changed fragments of a known wording case (merged flow, so the full old
        # literal is intentionally not contiguous).
        inline_text = re.sub(r"\s+", " ", self.artifacts["inline_text"])
        self.assertIn("Inline Comparison Redline", inline_text)
        self.assertIn("CHANGE-", inline_text)
        self.assertIn("30", inline_text)
        self.assertIn("60", inline_text)


class NumericValueSuppressionDefectTest(unittest.TestCase):
    """Regression for the Task 4 numeric/reference semantic-suppression defect.

    Before Task 4b these four cases were routed marker-only and resolved
    ``structural_only``, so their literals never reached the canonical changes
    or the redline. The semantic-safety repair keeps them visible; this test
    stays as the concrete proof that the defect cannot silently return.
    """

    @classmethod
    def setUpClass(cls):
        cls.artifacts = run_fixture("text_values")
        cls.canonical = cls.artifacts["canonical"]
        cls.redline = cls.artifacts["redline"]

    def test_numeric_value_change_is_not_suppressed(self):
        failures = []
        for name, old_text, new_text in TEXT_CASES:
            if name not in NUMERIC_CASES:
                continue
            with self.subTest(case=name):
                change = _text_change(self.canonical, old_text, new_text)
                event = _non_semantic_event(self.canonical, old_text, new_text)
                if change is None:
                    detail = "no canonical change record"
                    if event is not None:
                        resolution = event.get("resolution") or {}
                        detail = (
                            f"suppressed as event_type={event.get('event_type')} "
                            f"status={resolution.get('status')} "
                            f"case_type={resolution.get('case_type')}"
                        )
                    failures.append(f"{name}: {detail}")
                if old_text not in self.redline or new_text not in self.redline:
                    failures.append(f"{name}: literal absent from redline")
                self.assertIsNotNone(change, f"{name}: change missing ({failures})")
                self.assertIn(old_text, self.redline, name)
                self.assertIn(new_text, self.redline, name)


class SemanticSignalRoutingTest(unittest.TestCase):
    """Direct route-level coverage for semantic signals vs layout markers.

    These tests build synthetic candidate payloads (no pipeline run) so the
    routing decision is asserted in isolation. Numeric value differences must
    not be marker/list-label/single-glyph artifacts; a known letter-prefixed
    margin marker must stay deterministically structural-only.
    """

    @staticmethod
    def _candidate(old_tokens, new_tokens, old_text, new_text):
        def element(text):
            return {
                "element_type": "paragraph",
                "text": text,
                "comparison_text": text,
                "extraction_validation": {"requires_extraction_review": False},
            }

        return {
            "review_reasons": [],
            "similarity": 0.99,
            "old": element(old_text),
            "new": element(new_text),
            "token_diff": {
                "comparison_token_diff": {
                    "changes": [
                        {"old_tokens": old_tokens, "new_tokens": new_tokens}
                    ]
                }
            },
        }

    @staticmethod
    def _equivalent_candidate(old_tokens, new_tokens, old_text, new_text):
        """Candidate whose comparison representation is token-equivalent.

        The ``comparison_token_diff`` has zero operations (e.g. a detected
        margin-marker prefix or a wrap hyphen was normalized away), while the
        source/normalized diffs still carry the real changed tokens.
        """

        def element(text):
            return {
                "element_type": "paragraph",
                "text": text,
                "comparison_text": text,
                "extraction_validation": {"requires_extraction_review": False},
            }

        def representation(tokens_old, tokens_new):
            changes = (
                [{"old_tokens": tokens_old, "new_tokens": tokens_new}]
                if tokens_old or tokens_new
                else []
            )
            return {"change_operation_count": len(changes), "changes": changes}

        return {
            "review_reasons": [],
            "similarity": 0.99,
            "comparison_token_equivalent": True,
            "old": element(old_text),
            "new": element(new_text),
            "token_diff": {
                "source_token_diff": representation(old_tokens, new_tokens),
                "normalized_token_diff": representation(old_tokens, new_tokens),
                "comparison_token_diff": representation([], []),
            },
        }

    def test_numeric_value_signals_are_accepted_not_artifact(self):
        from crr_compare.stages import s11_routing

        for name, old_tokens, new_tokens in (
            ("retention_days", ["30"], ["60"]),
            ("effective_date", ["2026"], ["2027"]),
            ("percentage", ["20"], ["35"]),
            ("reference", ["12"], ["15"]),
        ):
            with self.subTest(case=name):
                candidate = self._candidate(
                    old_tokens, new_tokens, "old value " + name, "new value " + name
                )
                self.assertFalse(s11_routing.marker_only_like(candidate), name)
                self.assertFalse(s11_routing.list_label_only_like(candidate), name)
                self.assertFalse(s11_routing.single_glyph_like(candidate), name)
                route, case_type, reasons = s11_routing.route_non_table(
                    "modified", candidate
                )
                self.assertEqual(
                    route, "ACCEPT_DETERMINISTIC", (name, route, case_type, reasons)
                )

    def test_negation_and_modal_signals_fall_through_to_modified(self):
        from crr_compare.stages import s11_routing

        for name, old_tokens, new_tokens in (
            ("not_added", [], ["not"]),
            ("not_removed", ["not"], []),
            ("shall_to_may", ["shall"], ["may"]),
        ):
            with self.subTest(case=name):
                candidate = self._candidate(
                    old_tokens, new_tokens, "old wording " + name, "new wording " + name
                )
                route, case_type, reasons = s11_routing.route_non_table(
                    "modified", candidate
                )
                self.assertEqual(
                    route, "ACCEPT_DETERMINISTIC", (name, route, case_type, reasons)
                )

    def test_known_margin_marker_stays_structural_only(self):
        from crr_compare.stages import s11_routing
        from crr_compare.stages import s14_canonical_result

        candidate = self._candidate(["M5"], ["C12"], "M5", "C12")
        self.assertTrue(s11_routing.marker_only_like(candidate))
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "SPECIAL_CASE_RULE", (route, case_type, reasons))
        self.assertEqual(case_type, "possible_marker_or_version_marker_difference")
        resolution = s14_canonical_result._resolve(
            "MODIFIED-CANDIDATE-MARKER",
            {
                "MODIFIED-CANDIDATE-MARKER": {
                    "route_id": "NT-000001",
                    "route": route,
                    "case_type": case_type,
                    "review_reasons": reasons,
                }
            },
            {},
        )
        self.assertEqual(resolution["status"], "structural_only")
        self.assertFalse(resolution["needs_review"])

    def test_comparison_token_equivalent_with_semantic_signal_is_not_suppressed(self):
        """A token-equivalent comparison must not hide a value/reference change.

        ``comparison_token_equivalent`` only proves the comparison
        representation is identical. The source/normalized diff can still
        carry a numeric/date/percentage/reference change (e.g. a detected
        margin-marker prefix such as ``12a``->``15b`` is stripped from the
        comparison text). Such a signal must route to review, never to
        ``tokenization_neutral_difference`` suppression.
        """
        from crr_compare.stages import s11_routing
        from crr_compare.stages import s14_canonical_result

        for name, old_tokens, new_tokens in (
            ("reference", ["12a"], ["15b"]),
            ("retention_days", ["30"], ["60"]),
            ("effective_date", ["2026"], ["2027"]),
            ("percentage", ["20"], ["35"]),
        ):
            with self.subTest(case=name):
                candidate = self._equivalent_candidate(
                    old_tokens, new_tokens, "old " + name, "new " + name
                )
                self.assertTrue(candidate["comparison_token_equivalent"], name)
                route, case_type, reasons = s11_routing.route_non_table(
                    "modified", candidate
                )
                self.assertNotEqual(
                    (route, case_type),
                    ("SPECIAL_CASE_RULE", "tokenization_neutral_difference"),
                    (name, route, case_type, reasons),
                )
                self.assertEqual(
                    route, "HUMAN_REVIEW", (name, route, case_type, reasons)
                )
                resolution = s14_canonical_result._resolve(
                    "MODIFIED-CANDIDATE-TOKEN-EQUIV",
                    {
                        "MODIFIED-CANDIDATE-TOKEN-EQUIV": {
                            "route_id": "NT-000001",
                            "route": route,
                            "case_type": case_type,
                            "review_reasons": reasons,
                        }
                    },
                    {},
                )
                self.assertEqual(resolution["status"], "review", (name, resolution))
                self.assertTrue(resolution["needs_review"], name)

    def test_true_tokenization_neutral_equivalent_stays_suppressed(self):
        """A hyphen/wrap-only equivalent difference is still an artifact."""
        from crr_compare.stages import s11_routing
        from crr_compare.stages import s14_canonical_result

        candidate = self._equivalent_candidate(
            ["Kredit", "-", "institut"],
            ["Kreditinstitut"],
            "Kredit-\ninstitut",
            "Kreditinstitut",
        )
        route, case_type, reasons = s11_routing.route_non_table("modified", candidate)
        self.assertEqual(route, "SPECIAL_CASE_RULE", (route, case_type, reasons))
        self.assertEqual(
            case_type, "tokenization_neutral_difference", (route, case_type, reasons)
        )
        resolution = s14_canonical_result._resolve(
            "MODIFIED-CANDIDATE-TOKEN-EQUIV",
            {
                "MODIFIED-CANDIDATE-TOKEN-EQUIV": {
                    "route_id": "NT-000001",
                    "route": route,
                    "case_type": case_type,
                    "review_reasons": reasons,
                }
            },
            {},
        )
        self.assertEqual(resolution["status"], "suppressed_artifact")
        self.assertFalse(resolution["needs_review"])

    def test_s14_does_not_suppress_stale_semantic_tokenization_route(self):
        """Defense in depth: a stale suppression route with a semantic signal
        reason still surfaces as review."""
        from crr_compare.stages import s14_canonical_result

        resolution = s14_canonical_result._resolve(
            "MODIFIED-CANDIDATE-STALE",
            {
                "MODIFIED-CANDIDATE-STALE": {
                    "route_id": "NT-000001",
                    "route": "SPECIAL_CASE_RULE",
                    "case_type": "tokenization_neutral_difference",
                    "review_reasons": [
                        "comparison_token_equivalent_with_semantic_signal"
                    ],
                }
            },
            {},
        )
        self.assertEqual(resolution["status"], "review", resolution)
        self.assertTrue(resolution["needs_review"])


class TableValueRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = run_fixture("table_values")
        cls.canonical = cls.artifacts["canonical"]
        cls.tables = cls.artifacts["tables"]
        cls.redline = cls.artifacts["redline"]

    def test_changed_cell_preserved_with_provenance(self):
        found = _table_changes_with_cell(self.canonical, "100 percent", "150 percent")
        self.assertTrue(found, "changed cell not reported")
        table_change, cell = found[0]
        self.assertEqual(table_change["classification"], "MODIFIED")
        self.assertIn(cell["classification"], {"MODIFIED", "LAYOUT_EQUIVALENT"})
        self.assertIsNotNone(table_change.get("source_table_match_id"))
        self.assertTrue((table_change.get("old") or {}).get("source_table_ids"))
        self.assertIsNotNone(cell.get("change_id"))
        self.assertIsNotNone(cell.get("old_row_index"))
        self.assertIsNotNone(cell.get("new_row_index"))
        self.assertIsNotNone(cell.get("column_index"))
        self.assertIn("100 percent", self.redline)
        self.assertIn("150 percent", self.redline)

    def _stage10_match(self, table_change):
        match_id = table_change["source_table_match_id"]
        for match in self.tables.get("table_matches") or []:
            if match["table_match_id"] == match_id:
                return match
        return None

    def test_inserted_row_is_added_and_reviewable(self):
        found = _table_changes_with_cell(self.canonical, "", "SME")
        self.assertTrue(found, "inserted row not reported")
        table_change, cell = found[0]
        self.assertEqual(cell["classification"], "ADDED")
        self.assertTrue((table_change.get("resolution") or {})["needs_review"])
        self.assertTrue(self._stage10_match(table_change)["statistics"]["added_rows"] >= 1)
        self.assertIn("SME", self.redline)

    def test_deleted_row_is_deleted_and_reviewable(self):
        found = _table_changes_with_cell(self.canonical, "Silver")
        self.assertTrue(found, "deleted row not reported")
        table_change, cell = found[0]
        self.assertEqual(cell["classification"], "DELETED")
        self.assertTrue((table_change.get("resolution") or {})["needs_review"])
        self.assertTrue(self._stage10_match(table_change)["statistics"]["deleted_rows"] >= 1)
        self.assertIn("Silver", self.redline)

    def test_reordered_row_remains_reviewable(self):
        reorder_literals = ("Publish", "Intake", "Review")
        matching = [
            table_change
            for table_change in self.canonical["table_changes"]
            if any(
                literal in ((cell.get("old") or {}).get("text") or "")
                or literal in ((cell.get("new") or {}).get("text") or "")
                for cell in table_change.get("cell_changes") or []
                for literal in reorder_literals
            )
        ]
        self.assertTrue(matching, "reordered table not reported")
        for table_change in matching:
            self.assertTrue((table_change.get("resolution") or {})["needs_review"])
            self.assertIn("cell_or_row_boundary_changed", table_change["review_reasons"])

    def test_changed_column_remains_reviewable(self):
        matching = [
            table_change
            for table_change in self.canonical["table_changes"]
            if (table_change.get("old") or {}).get("column_count") == 3
            and (table_change.get("new") or {}).get("column_count") == 4
        ]
        self.assertTrue(matching, "changed column not reported")
        self.assertTrue((matching[0].get("resolution") or {})["needs_review"])
        self.assertIn("column_count_changed", matching[0]["review_reasons"])
        self.assertIn("EUR", self.redline)

    def test_repeated_header_split_does_not_invent_changes(self):
        region_changes = [
            table_change
            for table_change in self.canonical["table_changes"]
            if "region" in " ".join((table_change.get("old") or {}).get("header_signature") or [])
            or "region" in " ".join((table_change.get("new") or {}).get("header_signature") or [])
        ]
        self.assertEqual(region_changes, [], "identical repeated-header split invented a change")

        joined = [
            match
            for match in self.tables.get("table_matches") or []
            if len((match.get("new") or {}).get("source_table_ids") or []) == 2
        ]
        self.assertTrue(joined, "split table was not joined")
        self.assertEqual(joined[0]["classification"], "UNCHANGED")
        self.assertGreaterEqual(
            (joined[0].get("new") or {}).get("omitted_repeated_header_rows") or 0, 1
        )

    def test_merged_cells_remain_reviewable(self):
        matching = [
            table_change
            for table_change in self.canonical["table_changes"]
            if "merged_cell_or_header_kind_changed" in table_change["review_reasons"]
        ]
        self.assertTrue(matching, "merged-cell structure change not reviewable")
        self.assertTrue((matching[0].get("resolution") or {})["needs_review"])
        self.assertIn("Merged", self.redline)

    def test_extraction_uncertain_cell_remains_reviewable(self):
        found = _table_changes_with_cell(self.canonical, "Co-operating", "Cooperating")
        self.assertTrue(found, "extraction-uncertain cell not reported")
        table_change, cell = found[0]
        self.assertEqual(cell["classification"], "EXTRACTION_ARTIFACT_REVIEW")
        self.assertTrue((table_change.get("resolution") or {})["needs_review"])
        self.assertIn(
            "cell_difference_only_matches_aggressive_artifact_normalization",
            table_change["review_reasons"],
        )
        self.assertIn("Co-operating", self.redline)
        self.assertIn("Cooperating", self.redline)

    def test_table_review_entries_reach_csv(self):
        csv_kinds = {row["source_kind"] for row in self.artifacts["csv_rows"]}
        self.assertIn("table_change", csv_kinds)
        self.assertEqual(
            len(self.artifacts["csv_rows"]), len(self.canonical["review_queue"])
        )

    def test_whole_added_deleted_tables_reach_pdf_with_cell_provenance(self):
        pdf_text = " ".join(self.redline.split())
        for change_type, side, opposite, count, literals in (
            ("TABLE_DELETED", "old", "new", 6, ("Withdrawn backup", "41", "42")),
            ("TABLE_ADDED", "new", "old", 9, ("Flood exposure", "17", "18")),
        ):
            with self.subTest(change_type=change_type):
                changes = [
                    item for item in self.canonical["table_changes"]
                    if item["change_type"] == change_type
                ]
                self.assertEqual(len(changes), 1)
                change = changes[0]
                self.assertEqual(change["cell_content_status"], "available")
                self.assertEqual(len(change["cell_changes"]), count)
                texts = []
                ids = []
                for cell in change["cell_changes"]:
                    self.assertIsNone(cell[opposite])
                    source = cell[side]
                    ids.append(source["cell_id"])
                    self.assertIn(
                        source["source_table_id"], change[side]["source_table_ids"]
                    )
                    self.assertIsNotNone(source["source_row_index"])
                    text = " ".join(source["text"].split())
                    texts.append(text)
                    self.assertIn(f"{side} cell: {text}", pdf_text)
                self.assertEqual(len(set(ids)), count)
                for literal in literals:
                    self.assertIn(literal, texts)
        self.assertNotIn("Table cell content unavailable", self.redline)


if __name__ == "__main__":
    unittest.main()
