"""Residual resegmentation reconciler: quantitative-evidence guard.

The reconciler (``crr_compare.reconcile``) pairs leftover DELETED/ADDED
candidates that are near-twins under a ``[^\\W_]{3,}`` token score, so a
paragraph split/merge, list-marker prefix or hyphenation artifact surfaces as a
single review-only ``RESEGMENTATION_REVIEW`` item instead of noisy ADD+DELETE.

That score is blind to short/signed/decimal numbers and to the percent and
``<=``/``>=`` markers, because TOKEN_RE keeps only runs of >=3 plain word
characters.  A real numeric edit therefore scores a perfect near-twin and is
swallowed as cosmetic noise, vanishing from the normal change path:

    21 -> 99        Required capital limit 21 percent -> ... 99 percent
    -21 -> 21       ... -21 percent -> ... 21 percent
    12,50 -> 12,75  ... 12,50 percent -> ... 12,75 percent
    1% -> 1         ... 1% applies -> ... 1 applies

The guard added to ``reconcile_residuals`` compares an ordered signature of the
RAW old/new text (``text`` before any normalized/lossy ``comparison_text``)
before any near-twin pairing, so distinct digit/sign/decimal/percent/relational
evidence can never become a cosmetic twin.  Reordering and repetition are kept
(an ordered tuple, not a set).  Truly value-identical line-wrap/hyphenation/
list-marker resegmentation still pairs when unambiguously safe; anything
ambiguous is left as a visible, reviewable DELETE/ADD pair.

These tests exercise the real ``reconcile_residuals`` (no mocks) and, in the
integration classes, the real ``s09_movement`` stage plus the canonical/redline
route, so the guard is proven end to end and not just at the unit boundary.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crr_compare.reconcile import reconcile_residuals

ROOT = Path(__file__).resolve().parents[1]
S09 = ROOT / "crr_compare" / "stages" / "s09_movement.py"
PYTHON = sys.executable

INPUT_STAGE = "comparison_candidate_assembly_and_multi_representation_token_diff"
OUTPUT_STAGE = "movement_and_structural_reconciliation_evidence"


# ---------------------------------------------------------------------------
# Unit fixtures: one deleted + one added residual candidate
# ---------------------------------------------------------------------------
def _element(
    element_id: str,
    text: str,
    *,
    comparison_text: str | None = None,
    element_type: str = "paragraph",
    reading_order: int = 0,
) -> dict:
    return {
        "element_id": element_id,
        "element_type": element_type,
        # ``text`` is the RAW source text; ``comparison_text`` is the
        # marker-stripped/lossy-normalized representation the token score reads.
        "text": text,
        "normalized_text": text,
        "comparison_text": text if comparison_text is None else comparison_text,
        "reading_order": reading_order,
        "primary_page": 1,
    }


def _deleted(candidate_id: str, element: dict) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "DELETED_CANDIDATE",
        "old": element,
        "new": None,
        "needs_review": False,
        "review_reasons": ["upstream_delete_reason"],
        "movement_classification": None,
        "token_diff": None,
    }


def _added(candidate_id: str, element: dict) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "ADDED_CANDIDATE",
        "old": None,
        "new": element,
        "needs_review": False,
        "review_reasons": ["upstream_add_reason"],
        "movement_classification": None,
        "token_diff": None,
    }


def _pair(old_text: str, new_text: str, **element_kwargs):
    old_element = _element("OLD-1", old_text, **element_kwargs)
    new_element = _element("NEW-1", new_text, **element_kwargs)
    return (
        reconcile_residuals(
            [_deleted("DEL-1", old_element)], [_added("ADD-1", new_element)]
        ),
        old_element,
        new_element,
    )


class DistinctQuantitativeEvidenceIsNotACosmeticTwinTest(unittest.TestCase):
    """A raw numeric/sign/unit change must stay a visible DELETE/ADD residual."""

    # (name, old raw text, new raw text) -- each scores >=0.8 on tokens alone.
    REJECTED = (
        ("percent_21_to_99", "Required capital limit 21 percent",
         "Required capital limit 99 percent"),
        ("short_1_to_2", "Required capital limit 1 unit",
         "Required capital limit 2 unit"),
        ("three_digit", "Required capital limit 100 points",
         "Required capital limit 999 points"),
        ("decimal_comma", "Required capital limit 12,50 percent",
         "Required capital limit 12,75 percent"),
        ("decimal_dot", "Required capital limit 12.50 percent",
         "Required capital limit 12.75 percent"),
        ("ascii_negative", "Required capital limit -21 percent",
         "Required capital limit 21 percent"),
        ("unicode_negative", "Required capital limit \u221221 percent",
         "Required capital limit 21 percent"),
        ("plus_sign", "Required capital surplus +21 points",
         "Required capital surplus 21 points"),
        ("percent_unit", "Required capital limit 1% applies",
         "Required capital limit 1 applies"),
        ("less_vs_greater", "Required capital limit \u226421 days",
         "Required capital limit \u226522 days"),
        ("reordered", "Required capital 12 34 percent",
         "Required capital 34 12 percent"),
        ("repeated", "Required capital 1 1 percent",
         "Required capital 1 percent"),
        # --- broader symbol / sign / punctuation / unit / scale context ---
        ("relational_symbol", "Required capital limit < 21 percent",
         "Required capital limit > 21 percent"),
        ("ascii_relational", "Required capital limit <= 21 days",
         "Required capital limit >= 21 days"),
        ("parentheses", "Required capital (21) units",
         "Required capital 21 units"),
        ("spaced_ascii_minus", "Required capital - 21 units",
         "Required capital 21 units"),
        ("spaced_unicode_minus", "Required capital \u2212 21 units",
         "Required capital 21 units"),
        ("fraction_separator", "Required capital 1/2 units",
         "Required capital 1 2 units"),
        ("equals_operator", "Required capital = 21 units",
         "Required capital 21 units"),
        ("short_unit",
         "The minimum operating reserve requirement is 21kg for all covered entities in scope",
         "The minimum operating reserve requirement is 21g for all covered entities in scope"),
        ("scale_word",
         "The minimum operating reserve requirement is 21 million for all covered entities in scope",
         "The minimum operating reserve requirement is 21 billion for all covered entities in scope"),
    )

    def test_distinct_quantitative_evidence_never_pairs(self):
        for name, old_text, new_text in self.REJECTED:
            with self.subTest(case=name):
                (pairs, leftover_deleted, leftover_added), old_el, new_el = _pair(
                    old_text, new_text
                )
                self.assertEqual(pairs, [], f"{name}: swallowed as a cosmetic twin")
                # Nothing is consumed: the edit stays visible and reviewable.
                self.assertEqual([c["old"] for c in leftover_deleted], [old_el])
                self.assertEqual([c["new"] for c in leftover_added], [new_el])

    def test_raw_text_governs_over_lossy_comparison_text(self):
        """The guard reads RAW ``text``, not a lossy ``comparison_text``.

        When a normalization strips the number from ``comparison_text`` the two
        token representations are identical, and only the raw text still proves
        the 21 -> 99 change. The raw evidence must win.
        """
        (pairs, leftover_deleted, leftover_added), _, _ = _pair(
            "Required capital limit 21 percent",
            "Required capital limit 99 percent",
            comparison_text="Required capital limit percent",
        )
        self.assertEqual(pairs, [])
        self.assertEqual(len(leftover_deleted), 1)
        self.assertEqual(len(leftover_added), 1)

    def test_same_value_wrap_still_pairs(self):
        """A value-identical line-wrap resegmentation still pairs."""
        (pairs, leftover_deleted, leftover_added), old_el, new_el = _pair(
            "Required capital limit 21\npercent applies to all institutions",
            "Required capital limit 21 percent applies to all institutions",
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(leftover_deleted, [])
        self.assertEqual(leftover_added, [])
        self.assertEqual(pairs[0]["candidate_type"], "RESEGMENTATION_REVIEW")
        # Raw element objects and source candidate ids are carried, not rewritten.
        self.assertIs(pairs[0]["old"], old_el)
        self.assertIs(pairs[0]["new"], new_el)
        self.assertEqual(pairs[0]["source_deleted_candidate_id"], "DEL-1")
        self.assertEqual(pairs[0]["source_added_candidate_id"], "ADD-1")

    def test_same_value_percent_spacing_still_pairs(self):
        """``12,5 %`` vs a line-wrapped ``12,5``/``%`` is the same evidence."""
        (pairs, _, _), _, _ = _pair(
            "Required capital limit 12,5\n% applies to all institutions",
            "Required capital limit 12,5 % applies to all institutions",
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["source_deleted_candidate_id"], "DEL-1")

    def test_same_value_percent_spacing_without_wrap_still_pairs(self):
        """``12,5 %`` and ``12,5%`` differ only by layout spacing."""
        (pairs, _, _), _, _ = _pair(
            "Required capital limit 12,5 % applies to all institutions",
            "Required capital limit 12,5% applies to all institutions",
        )
        self.assertEqual(len(pairs), 1)

    def test_same_value_number_hyphen_wrap_still_pairs(self):
        """A line-break hyphenation with an unchanged number still pairs."""
        (pairs, _, _), _, _ = _pair(
            "Required capital limit 21 per-\ncent applies to all institutions",
            "Required capital limit 21 percent applies to all institutions",
        )
        self.assertEqual(len(pairs), 1)

    def test_non_numeric_near_twin_still_pairs(self):
        """Ordinary wording near-twins are unaffected by the guard."""
        (pairs, _, _), _, _ = _pair(
            "The quarterly revenue recognition policy applies",
            "The quarterly revenue recognition policy applies here",
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(
            pairs[0]["review_reasons"],
            ["resegmentation_marker_or_hyphenation_artifact"],
        )


# ---------------------------------------------------------------------------
# Integration: real s09 -> canonical -> redline PDF
# ---------------------------------------------------------------------------
def _env(root: Path | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "CRR_RUN_LLM": "mock",
        "CRR_DOMAIN_MODE": "generic",
        "PYTHONPATH": str(ROOT),
    }
    if root is not None:
        env["CRR_PROJECT_ROOT"] = str(root)
    return env


def _run_s09(deleted: list[dict], added: list[dict]) -> dict:
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
        "deleted": deleted,
        "added": added,
    }
    directory = tempfile.mkdtemp(prefix="crr-reconcile-s09-")
    root = Path(directory)
    output = root / "output"
    output.mkdir()
    (output / "comparison_candidates.json").write_text(
        json.dumps(case), encoding="utf-8"
    )
    script = f"import runpy\nrunpy.run_path({str(S09)!r}, run_name='__main__')\n"
    completed = subprocess.run(
        [PYTHON, "-c", script],
        cwd=str(ROOT),
        env=_env(root),
        capture_output=True,
        text=True,
    )
    try:
        if completed.returncode != 0:
            raise AssertionError(completed.stderr + completed.stdout)
        result_path = output / "comparison_candidates_with_movement.json"
        return json.loads(result_path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class MovementNumericReseguardIntegrationTest(unittest.TestCase):
    """The real s09 stage must keep a paragraph numeric edit out of the
    cosmetic resegmentation bucket, and the canonical/redline route must still
    show both literals as a genuine change."""

    NUMERIC_PAIRS = (
        ("21", "Minimum reserve ratio is 21 percent", "Minimum reserve ratio is 99 percent"),
        ("neg", "Counterparty exposure limit is -21 percent", "Counterparty exposure limit is 21 percent"),
        ("decimal", "Liquidity buffer is 12,50 percent", "Liquidity buffer is 12,75 percent"),
        ("relop", "Minimum reserve floor is < 21 percent", "Minimum reserve floor is > 21 percent"),
    )

    def _movement(self) -> dict:
        deleted = [
            _deleted(f"DEL-{name}", _element(f"OLD-{name}", old_text, reading_order=i))
            for i, (name, old_text, _) in enumerate(self.NUMERIC_PAIRS)
        ]
        added = [
            _added(f"ADD-{name}", _element(f"NEW-{name}", new_text, reading_order=50 + i))
            for i, (name, _, new_text) in enumerate(self.NUMERIC_PAIRS)
        ]
        return _run_s09(deleted, added)

    def test_s09_keeps_numeric_edits_as_visible_delete_add(self):
        movement = self._movement()
        self.assertEqual(movement["comparison_stage"], OUTPUT_STAGE)
        self.assertEqual(movement["resegmentation_review"], [])
        self.assertEqual(movement["statistics"]["resegmentation_review_pairs"], 0)
        self.assertEqual(
            sorted(record["old"]["element_id"] for record in movement["deleted"]),
            ["OLD-21", "OLD-decimal", "OLD-neg", "OLD-relop"],
        )
        self.assertEqual(
            sorted(record["new"]["element_id"] for record in movement["added"]),
            ["NEW-21", "NEW-decimal", "NEW-neg", "NEW-relop"],
        )
        # Upstream review reasons survive (no classification migration).
        for record in movement["deleted"]:
            self.assertIn("upstream_delete_reason", record["review_reasons"])
        for record in movement["added"]:
            self.assertIn("upstream_add_reason", record["review_reasons"])

    def test_canonical_and_pdf_show_both_numeric_literals(self):
        movement = self._movement()
        from crr_compare.stages import s11_routing, s14_canonical_result
        from crr_compare.redline_pdf import render_pdf

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
            "domain_profile": {"mode": "generic"},
            "domain_verification": {"enabled": False, "records": [], "status_counts": {}},
            "domain_verification_review": [],
            "statistics": {},
        }
        canonical = s14_canonical_result.build_comparison_result(movement, tables, routing)

        # The numeric edit is not hidden inside a resegmentation-only record.
        reseg_reviews = [
            item for item in canonical["review_queue"]
            if item.get("source_kind") == "resegmentation_pair"
        ]
        self.assertEqual(reseg_reviews, [])

        literals = [
            "Minimum reserve ratio is 21 percent",
            "Minimum reserve ratio is 99 percent",
            "Counterparty exposure limit is -21 percent",
            "Counterparty exposure limit is 21 percent",
            "Liquidity buffer is 12,50 percent",
            "Liquidity buffer is 12,75 percent",
            "Minimum reserve floor is < 21 percent",
            "Minimum reserve floor is > 21 percent",
        ]
        for literal in literals:
            self.assertTrue(
                _canonical_has_literal(canonical, literal),
                f"canonical lost numeric literal {literal!r}",
            )

        with tempfile.TemporaryDirectory(prefix="crr-reconcile-pdf-") as directory:
            pdf = render_pdf(canonical, Path(directory) / "redline.pdf")
            import fitz

            with fitz.open(pdf) as document:
                text = " ".join(
                    "".join(page.get_text() for page in document).split()
                )
        for literal in literals:
            self.assertIn(literal, text)


def _canonical_has_literal(canonical: dict, literal: str) -> bool:
    for change in canonical.get("changes") or []:
        for side in ("old", "new"):
            if (change.get(side) or {}).get("text") == literal:
                return True
    for record in canonical.get("deleted") or []:
        if (record.get("old") or {}).get("text") == literal:
            return True
    for record in canonical.get("added") or []:
        if (record.get("new") or {}).get("text") == literal:
            return True
    return False


if __name__ == "__main__":
    unittest.main()
