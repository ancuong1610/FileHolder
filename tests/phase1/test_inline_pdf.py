"""Report-only tests for the inline one-flow comparison PDF.

The inline PDF is an ADDITIONAL artifact: ``comparison_redline.pdf`` and
``document_compare.phase1.reporting.redline_pdf.render_pdf`` stay completely unchanged. This module
never touches the canonical result, raw DI, routing, matching, or any source
text; it only re-presents the canonical comparison with a safe,
presentation-only cleanup of GERMAN END-OF-LINE HYPHENATIONS that carry source
proof (a soft discretionary hyphen, or an explicit newline hyphen whose exact
joined word is corroborated by the opposite side).

Nothing here asserts semantic correctness, uses an LLM/dictionary, or downloads
anything. Unproven joins and missing alignment stay visible with an explicit
warning.
"""
import copy
import hashlib
import html as _html
import re as _re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from document_compare.phase1.reporting.inline_pdf import (
    DELETED_COLOR,
    INSERTED_COLOR,
    NO_DIFFERENCES_MESSAGE,
    SOFT_HYPHEN,
    build_audit,
    clean_verified_joins,
    inline_change_markup,
    render_pdf,
)
from document_compare.phase1.reporting.redline_pdf import (
    render_pdf as render_redline_pdf,
)
import document_compare.phase1.reporting.redline_pdf as redline_pdf_module

_TAG_RE = _re.compile(r"<[^>]+>")
_STRIKE_RE = _re.compile(r'<strike><font color="' + DELETED_COLOR + r'">(.*?)</font></strike>')
_INSERT_RE = _re.compile(r'<u><font color="' + INSERTED_COLOR + r'">(.*?)</font></u>')
_DELETED_INT = int(DELETED_COLOR.lstrip("#"), 16)
_INSERTED_INT = int(INSERTED_COLOR.lstrip("#"), 16)


def _strip_tags(markup):
    return _html.unescape(_TAG_RE.sub("", markup or ""))


def _struck(markup):
    return _html.unescape("".join(_STRIKE_RE.findall(markup or "")))


def _inserted(markup):
    return _html.unescape("".join(_INSERT_RE.findall(markup or "")))


def _norm(text):
    return " ".join((text or "").split())


def _old_from_markup(markup):
    """Reconstruct the old side: drop inserted segments, strip remaining tags."""
    return _html.unescape(_TAG_RE.sub("", _INSERT_RE.sub("", markup or "")))


def _new_from_markup(markup):
    """Reconstruct the new side: drop struck segments, strip remaining tags."""
    return _html.unescape(_TAG_RE.sub("", _STRIKE_RE.sub("", markup or "")))


def _layout_only_change(resolution_status="accepted"):
    """A change whose only difference is a corroborated end-of-line hyphenation."""
    old = "Die Kredit-\ninstitut Regel."
    new = "Die Kreditinstitut Regel."
    return _simple_change(
        old, new, change_id="C-LAYOUT", resolution_status=resolution_status,
        token_diff={"comparison_token_diff": {"changes": [
            _replace_op(old, new, "Kredit-\ninstitut", "Kreditinstitut"),
        ]}},
    )


def _replace_op(old_text, new_text, old_frag, new_frag):
    old_start = old_text.index(old_frag)
    new_start = new_text.index(new_frag)
    return {
        "operation": "replace",
        "old_char_start": old_start,
        "old_char_end": old_start + len(old_frag),
        "new_char_start": new_start,
        "new_char_end": new_start + len(new_frag),
        "old_text": old_frag,
        "new_text": new_frag,
        "old_tokens": old_frag.split(),
        "new_tokens": new_frag.split(),
    }


def _modal_change():
    old = "8 Zieht ein Institut Rettungserwerbe in Betracht, hat es eine Richtlinie zu entwickeln."
    new = "8 Zieht ein Institut Rettungserwerbe in Betracht, muss es eine Richtlinie zu entwickeln."
    return {
        "change_id": "CHANGE-000081",
        "change_type": "MODIFIED",
        "source_candidate_id": "MODIFIED-CANDIDATE-000081",
        "old": {"text": old, "primary_page": 46},
        "new": {"text": new, "primary_page": 33},
        "token_diff": {"comparison_token_diff": {"changes": [
            _replace_op(old, new, "hat", "muss"),
        ]}},
        "resolution": {"status": "accepted", "needs_review": False},
    }


def _simple_change(old, new, resolution_status="accepted", **extra):
    return {
        "change_id": extra.pop("change_id", "C-1"),
        "change_type": extra.pop("change_type", "MODIFIED"),
        "old": {"text": old, "primary_page": 1},
        "new": {"text": new, "primary_page": 2},
        "resolution": {"status": resolution_status, "needs_review": resolution_status == "review"},
        **extra,
    }


def _pdf_text(pdf_path):
    with fitz.open(pdf_path) as doc:
        extracted = "".join(page.get_text() for page in doc)
    return " ".join(extracted.split())


def _pdf_span_colors(pdf_path):
    colors = set()
    with fitz.open(pdf_path) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        colors.add(span.get("color"))
    return colors


class CleanVerifiedJoinsTest(unittest.TestCase):
    """Presentation-only hyphenation cleanup with hard source proof."""

    def test_soft_discretionary_hyphen_removed(self):
        old = "Kredit" + SOFT_HYPHEN + "institut"
        result = clean_verified_joins(old, "Kreditinstitut")
        self.assertEqual(result["old"], "Kreditinstitut")
        self.assertEqual(result["new"], "Kreditinstitut")
        self.assertEqual(result["uncertain"], [])
        self.assertTrue(
            any(join["proof"] == "soft_discretionary" for join in result["joins"]),
            result["joins"],
        )

    def test_soft_discretionary_hyphen_at_line_end_joins(self):
        result = clean_verified_joins(
            "Kredit" + SOFT_HYPHEN + "\ninstitut.", "Kreditinstitut."
        )
        self.assertEqual(result["old"], "Kreditinstitut.")
        self.assertEqual(result["new"], "Kreditinstitut.")

    def test_newline_hyphen_corroborated_by_opposite_side(self):
        old = "Die Kredit-\ninstitut Regel."
        new = "Die Kreditinstitut Regel."
        result = clean_verified_joins(old, new)
        self.assertEqual(result["old"], "Die Kreditinstitut Regel.")
        self.assertEqual(result["new"], new)
        self.assertEqual([join["proof"] for join in result["joins"]], ["newline_hyphen"])
        self.assertEqual(result["joins"][0]["joined"], "Kreditinstitut")
        self.assertEqual(result["uncertain"], [])

    def test_newline_hyphen_without_opposite_proof_stays_uncertain(self):
        old = "Die Kredit-\ninstitut Regel."
        new = "Die Kreditbank Regel."
        result = clean_verified_joins(old, new)
        self.assertEqual(result["old"], old)
        self.assertEqual(result["joins"], [])
        self.assertEqual(len(result["uncertain"]), 1)
        self.assertEqual(result["uncertain"][0]["joined"], "Kreditinstitut")

    def test_two_sided_german_compound_wrap_joins_both(self):
        old = "Die Positi-\nonen sind erfasst."
        new = "Die Positio-\nnen sind erfasst."
        result = clean_verified_joins(old, new)
        self.assertEqual(result["old"], "Die Positionen sind erfasst.")
        self.assertEqual(result["new"], "Die Positionen sind erfasst.")
        self.assertEqual(len(result["joins"]), 2)

    def test_permanent_same_line_hyphens_preserved(self):
        for text in ("Kosten-Nutzen", "EU-Verordnung", "re-sign"):
            with self.subTest(text=text):
                result = clean_verified_joins(text, text)
                self.assertEqual(result["old"], text)
                self.assertEqual(result["new"], text)
                self.assertEqual(result["joins"], [])
                self.assertEqual(result["uncertain"], [])

    def test_meaningful_word_boundary_preserved(self):
        result = clean_verified_joins("now here", "nowhere")
        self.assertEqual(result["old"], "now here")
        self.assertEqual(result["new"], "nowhere")
        self.assertEqual(result["joins"], [])

    def test_case_and_spacing_changes_not_cleaned(self):
        case = clean_verified_joins("BaFin", "Bafin")
        self.assertEqual((case["old"], case["new"]), ("BaFin", "Bafin"))
        self.assertEqual(case["joins"], [])
        spacing = clean_verified_joins("zwei Worte", "zweiWorte")
        self.assertEqual(spacing["joins"], [])

    def test_uppercase_newline_continuation_not_joined(self):
        # A permanent capitalized compound wrapped across a line is not a wrap.
        result = clean_verified_joins("Kosten-\nNutzen", "KostenNutzen")
        self.assertEqual(result["old"], "Kosten-\nNutzen")
        self.assertEqual(result["joins"], [])

    def test_soft_hyphen_between_digits_not_joined(self):
        # A numeric join (2<SHY>1 -> 21) is never a German word wrap.
        old = "2" + SOFT_HYPHEN + "1"
        result = clean_verified_joins(old, "21")
        self.assertEqual(result["old"], old)
        self.assertEqual(result["joins"], [])
        self.assertEqual(result["uncertain"], [])

    def test_soft_hyphen_letter_digit_mix_not_joined(self):
        old = "Ab" + SOFT_HYPHEN + "12"
        result = clean_verified_joins(old, "Ab12")
        self.assertEqual(result["old"], old)
        self.assertEqual(result["joins"], [])

    def test_newline_hyphen_between_digits_not_joined(self):
        result = clean_verified_joins("12-\n34", "1234")
        self.assertEqual(result["old"], "12-\n34")
        self.assertEqual(result["joins"], [])

    def test_nonbreaking_hyphen_at_newline_not_stripped(self):
        # U+2011 is a non-breaking hyphen, never a line-wrap hyphen.
        old = "Kredit\u2011\ninstitut Regel."
        result = clean_verified_joins(old, "Kreditinstitut Regel.")
        self.assertEqual(result["old"], old)
        self.assertEqual(result["joins"], [])


class InlineChangeMarkupTest(unittest.TestCase):
    def test_reliable_modal_diff_marks_strike_and_insert_color(self):
        flow = inline_change_markup(_modal_change())
        self.assertIn(
            f'<strike><font color="{DELETED_COLOR}">hat</font></strike>', flow["inline"]
        )
        self.assertIn(
            f'<u><font color="{INSERTED_COLOR}">muss</font></u>', flow["inline"]
        )
        self.assertIn("Zieht ein Institut Rettungserwerbe", flow["inline"])
        self.assertIsNone(flow["warning"])
        self.assertTrue(flow["reliable"])
        self.assertEqual(flow["joins"], 0)
        self.assertIn("CHANGE-000081", flow["header"])
        self.assertIn("p.46", flow["header"])
        self.assertIn("p.33", flow["header"])
        self.assertIn("MODIFIED-CANDIDATE-000081", flow["header"])

    def test_numeric_sign_and_negation_differences_stay_visible(self):
        cases = (
            ("Der Wert betraegt 21 EUR.", "Der Wert betraegt 99 EUR.", "21", "99", None),
            ("Zins +10% p.a.", "Zins +12% p.a.", "10", "12", "+"),
            ("Wert \u226421 Tage.", "Wert \u226522 Tage.", "21", "22", "\u2264"),
            ("Saldo \u221221 Mio.", "Saldo \u221299 Mio.", "21", "99", "\u2212"),
            ("Es muss gelten.", "Es kann gelten.", "muss", "kann", None),
            ("Das ist nicht erlaubt.", "Das ist erlaubt.", "nicht", None, None),
            ("zwei Worte.", "zweiWorte.", "zwei Worte", "zweiWorte", None),
            ("BaFin", "Bafin", "BaFin", "Bafin", None),
        )
        for old, new, old_token, new_token, shared in cases:
            with self.subTest(old=old, new=new):
                flow = inline_change_markup(_simple_change(old, new))
                self.assertEqual(flow["joins"], 0, flow)
                self.assertIn(old_token, _struck(flow["inline"]), flow["inline"])
                if new_token is not None:
                    self.assertIn(new_token, _inserted(flow["inline"]), flow["inline"])
                if shared:
                    self.assertIn(shared, _strip_tags(flow["inline"]))
                self.assertNotIn("\ufffd", flow["inline"])

    def test_mixed_wrap_and_numeric_cleans_wrap_keeps_numeric(self):
        old = "Die Positi-\nonen sind 21 EUR."
        new = "Die Positio-\nnen sind 99 EUR."
        flow = inline_change_markup(_simple_change(old, new, resolution_status="review"))
        self.assertEqual(flow["joins"], 2)
        stripped = _strip_tags(flow["inline"]).replace("\n", " ")
        self.assertIn("Positionen", stripped)
        self.assertIn(
            f'<strike><font color="{DELETED_COLOR}">21</font></strike>', flow["inline"]
        )
        self.assertIn(
            f'<u><font color="{INSERTED_COLOR}">99</font></u>', flow["inline"]
        )
        self.assertIsNotNone(flow["warning"])
        self.assertIn("not verified", flow["warning"])

    def test_missing_offsets_warns_and_shows_all_text(self):
        old = "Der Wert betraegt 21 EUR."
        new = "Der Wert betraegt 99 EUR."
        change = _simple_change(old, new)
        change["token_diff"] = {"comparison_token_diff": {"changes": [{
            "operation": "replace",
            "old_char_start": 0,
            "old_char_end": 3,
            "new_char_start": 0,
            "new_char_end": 3,
        }]}}
        flow = inline_change_markup(change)
        self.assertFalse(flow["reliable"])
        self.assertIn("not verified", flow["warning"])
        stripped = _strip_tags(flow["inline"])
        self.assertIn("21", stripped)
        self.assertIn("99", stripped)

    def test_added_and_deleted_paragraphs_retain_all_text(self):
        added = inline_change_markup(
            _simple_change("", "Ein ganzer neuer Absatz.", change_type="ADDED")
        )
        deleted = inline_change_markup(
            _simple_change("Ein ganzer gestrichener Absatz.", "", change_type="DELETED")
        )
        self.assertIn("Ein ganzer neuer Absatz.", _strip_tags(added["inline"]))
        self.assertIn("Ein ganzer gestrichener Absatz.", _strip_tags(deleted["inline"]))

    def test_insertion_keeps_whitespace_gap_and_reconstructs_both_sides(self):
        old = "Die Pflicht gilt."
        new = "Die neue Pflicht gilt."
        flow = inline_change_markup(_simple_change(old, new))
        self.assertNotIn("neuePflicht", flow["inline"])
        self.assertEqual(_old_from_markup(flow["inline"]), old)
        self.assertEqual(_new_from_markup(flow["inline"]), new)

    def test_replacement_reconstructs_both_sides_exactly(self):
        old = "Der Wert betraegt 21 EUR."
        new = "Der Wert betraegt 99 EUR."
        flow = inline_change_markup(_simple_change(old, new))
        self.assertEqual(_old_from_markup(flow["inline"]), old)
        self.assertEqual(_new_from_markup(flow["inline"]), new)

    def test_whitespace_only_difference_reconstructs_both_sides(self):
        old = "zwei  Worte"
        new = "zwei Worte"
        flow = inline_change_markup(_simple_change(old, new))
        self.assertEqual(_old_from_markup(flow["inline"]), old)
        self.assertEqual(_new_from_markup(flow["inline"]), new)


class InlinePdfRenderTest(unittest.TestCase):
    def test_pdf_header_and_text_extractable(self):
        comparison = {"changes": [_modal_change()], "table_changes": [], "review_queue": []}
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            joined = _pdf_text(pdf)
        self.assertIn("CHANGE-000081", joined)
        self.assertIn("hat", joined)
        self.assertIn("muss", joined)

    def test_strike_and_insert_colors_present_in_pdf(self):
        comparison = {"changes": [_modal_change()], "table_changes": [], "review_queue": []}
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            colors = _pdf_span_colors(pdf)
        self.assertIn(_DELETED_INT, colors, colors)
        self.assertIn(_INSERTED_INT, colors, colors)

    def test_empty_comparison_reports_no_differences(self):
        with TemporaryDirectory() as d:
            pdf = render_pdf({}, Path(d) / "inline.pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            joined = _pdf_text(pdf)
        self.assertIn(NO_DIFFERENCES_MESSAGE, joined)

    def test_cover_reports_joins_and_limitations(self):
        old = "Die Kredit-\ninstitut Regel ist 21."
        new = "Die Kreditinstitut Regel ist 99."
        comparison = {
            "changes": [_simple_change(old, new, change_id="C-JOIN")],
            "table_changes": [],
            "review_queue": [],
        }
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertIn("Limitation", joined)
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["verified_joins"], 1)
        self.assertIn("C-JOIN", audit["totals"]["verified_joins_change_ids"])

    def test_unresolved_review_section_is_visible(self):
        comparison = {
            "changes": [],
            "table_changes": [],
            "review_queue": [
                {"review_id": "REVIEW-000001", "case_type": "hyphenation_wrap_artifact",
                 "change_id": "CHANGE-000001", "resolution": {"status": "review"}},
                {"review_id": "REVIEW-000002", "case_type": "movement_block_review",
                 "resolution": {"status": "review"}},
            ],
        }
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertIn("REVIEW-000001", joined)
        self.assertIn("hyphenation_wrap_artifact", joined)
        self.assertIn("review_queue_export.csv", joined)

    def test_unchanged_cell_text_is_omitted(self):
        table = {
            "table_change_id": "TABLE-CHANGE-00001",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"row_count": 1, "column_count": 2, "source_pages": [3]},
            "new": {"row_count": 1, "column_count": 2, "source_pages": [4]},
            "cell_changes": [
                {"classification": "UNCHANGED",
                 "old": {"cell_id": "OLD-T1-C0", "text": "UNCHANGED-CELL-TEXT", "primary_page": 3},
                 "new": {"cell_id": "NEW-T1-C0", "text": "UNCHANGED-CELL-TEXT", "primary_page": 4}},
                {"classification": "cell_change",
                 "old": {"cell_id": "OLD-T1-C1", "text": "100 percent", "primary_page": 3},
                 "new": {"cell_id": "NEW-T1-C1", "text": "150 percent", "primary_page": 4},
                 "token_diff": {"source_token_diff": {"changes": [
                     _replace_op("100 percent", "150 percent", "100", "150"),
                 ]}}},
            ],
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertNotIn("UNCHANGED-CELL-TEXT", joined)
        self.assertIn("100", joined)
        self.assertIn("150", joined)
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["unchanged_cells_omitted"], 1)

    def test_verified_layout_only_change_is_omitted_from_flow(self):
        comparison = {
            "changes": [_layout_only_change("accepted")],
            "table_changes": [], "review_queue": [],
        }
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["verified_joins"], 1)
        self.assertEqual(audit["totals"]["layout_only_differences_omitted"], 1)
        self.assertEqual(
            audit["totals"]["layout_only_differences_omitted_ids"], ["C-LAYOUT"])
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertNotIn("C-LAYOUT", joined)

    def test_layout_only_change_under_review_stays_visible(self):
        comparison = {
            "changes": [_layout_only_change("review")],
            "table_changes": [], "review_queue": [],
        }
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["layout_only_differences_omitted"], 0)
        self.assertEqual(audit["totals"]["layout_only_differences_omitted_ids"], [])
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertIn("C-LAYOUT", joined)

    def test_unchanged_cell_label_mismatch_stays_visible(self):
        table = {
            "table_change_id": "TABLE-CHANGE-00002",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"row_count": 1, "column_count": 1, "source_pages": [3]},
            "new": {"row_count": 1, "column_count": 1, "source_pages": [4]},
            "cell_changes": [
                {"classification": "UNCHANGED",
                 "old": {"cell_id": "OLD-T1-C0", "text": "OLD-TEXT", "primary_page": 3},
                 "new": {"cell_id": "NEW-T1-C0", "text": "NEW-TEXT", "primary_page": 4}},
            ],
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["unchanged_cells_omitted"], 0)
        self.assertEqual(audit["totals"]["unchanged_cell_mismatches"], 1)
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "inline.pdf")
            joined = _pdf_text(pdf)
        self.assertIn("OLD-TEXT", joined)
        self.assertIn("NEW-TEXT", joined)
        self.assertIn("WARNING", joined)

    def test_cell_joins_are_counted_in_audit(self):
        old = "Die Kredit-\ninstitut Regel ist 21."
        new = "Die Kreditinstitut Regel ist 99."
        table = {
            "table_change_id": "TABLE-CHANGE-00003",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"row_count": 1, "column_count": 1, "source_pages": [3]},
            "new": {"row_count": 1, "column_count": 1, "source_pages": [4]},
            "cell_changes": [
                {"classification": "cell_change",
                 "old": {"cell_id": "OLD-T1-C0", "text": old, "primary_page": 3},
                 "new": {"cell_id": "NEW-T1-C0", "text": new, "primary_page": 4},
                 "token_diff": {"source_token_diff": {"changes": [
                     _replace_op(old, new, "Kredit-\ninstitut", "Kreditinstitut")]}}},
            ],
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["verified_joins"], 1)
        self.assertEqual(audit["totals"]["cell_verified_joins"], 1)

    def test_render_does_not_mutate_input(self):
        comparison = {
            "changes": [_modal_change()],
            "table_changes": [],
            "review_queue": [{"review_id": "R", "resolution": {"status": "review"}}],
        }
        snapshot = copy.deepcopy(comparison)
        with TemporaryDirectory() as d:
            render_pdf(comparison, Path(d) / "inline.pdf")
            build_audit(comparison)
        self.assertEqual(comparison, snapshot)

    def test_redline_render_function_and_output_unchanged(self):
        # The default redline path must stay byte-for-byte the same source. The
        # redline PDF embeds a creation timestamp, so the guard is the render
        # function's source hash plus an isolation check that generating the
        # inline PDF cannot perturb the default redline output.
        redline_source = Path(redline_pdf_module.__file__)
        self.assertEqual(
            hashlib.sha256(redline_source.read_bytes()).hexdigest(),
            # This pin was updated intentionally by the approved report redesign
            # (complete supported/uncertain report). It still guards against
            # accidental later drift; it is not evidence that the renderer is
            # byte-identical to the earlier additive-only version. The
            # substantive invariants below (inline does not mutate the canonical
            # input and does not perturb redline output) are unchanged.
            "2831996c5ac11c8e27f89bdb41a1c819a0b07f0d8fc6870ba9e5f02a7de0081e",
        )
        self.assertEqual(render_redline_pdf.__module__, "document_compare.phase1.reporting.redline_pdf")
        self.assertIsNot(render_redline_pdf, render_pdf)

        comparison = {"changes": [_modal_change()], "table_changes": [], "review_queue": []}
        with TemporaryDirectory() as d:
            before_pdf = render_redline_pdf(comparison, Path(d) / "r1.pdf")
            before_text = _pdf_text(Path(before_pdf))
            render_pdf(comparison, Path(d) / "inline.pdf")
            after_pdf = render_redline_pdf(comparison, Path(d) / "r2.pdf")
            after_text = _pdf_text(Path(after_pdf))
        self.assertEqual(before_text, after_text)
        self.assertIn("old:", before_text)
        self.assertIn("new:", before_text)
        self.assertNotIn("inline:", before_text)


class InlineAuditTest(unittest.TestCase):
    def test_audit_reports_omissions_joins_and_limitations(self):
        old = "Die Kredit-\ninstitut Regel ist 21."
        new = "Die Kreditinstitut Regel ist 99."
        comparison = {
            "changes": [_simple_change(old, new, change_id="C-JOIN", resolution_status="review")],
            "table_changes": [],
            "review_queue": [{"review_id": "REVIEW-000001", "case_type": "x",
                              "resolution": {"status": "review"}}],
        }
        audit = build_audit(comparison)
        self.assertEqual(audit["totals"]["changes"], 1)
        self.assertEqual(audit["totals"]["verified_joins"], 1)
        # The change still carries a real 21 -> 99 difference, so it is NOT a
        # whole layout-only omission; the join is recorded but the change stays
        # visible. The old assertion pinned this to the join count (1), which was
        # the bug: omissions must count changes actually omitted.
        self.assertEqual(audit["totals"]["layout_only_differences_omitted"], 0)
        self.assertEqual(audit["totals"]["layout_only_differences_omitted_ids"], [])
        self.assertEqual(audit["totals"]["review_items"], 1)
        self.assertTrue(audit["limitations"])
        self.assertIn("review_queue_export.csv", audit["unresolved_review"]["csv"])

    def test_safe_join_with_numeric_difference_is_not_omitted(self):
        old = "Die Kredit-\ninstitut Regel ist 21."
        new = "Die Kreditinstitut Regel ist 99."
        change = _simple_change(old, new, change_id="C-MIX", resolution_status="accepted")
        audit = build_audit({"changes": [change], "table_changes": [], "review_queue": []})
        self.assertEqual(audit["totals"]["verified_joins"], 1)
        self.assertEqual(audit["totals"]["layout_only_differences_omitted"], 0)

    def test_audit_does_not_mutate_input(self):
        comparison = {"changes": [_modal_change()], "table_changes": [], "review_queue": []}
        snapshot = copy.deepcopy(comparison)
        build_audit(comparison)
        self.assertEqual(comparison, snapshot)

    def test_audit_empty_comparison(self):
        audit = build_audit({})
        self.assertEqual(audit["totals"]["changes"], 0)
        self.assertEqual(audit["totals"]["verified_joins"], 0)


if __name__ == "__main__":
    unittest.main()
