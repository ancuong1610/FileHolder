import copy
import html as _html
import re as _re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import fitz
from crr_compare.redline_pdf import (
    WRAP_LABEL,
    change_flow_markup,
    render_pdf,
)
from crr_compare.diff_annotations import reliable_operation_ranges
from crr_compare.render_spans import build_diff_spans

_RED = "#b00020"
_AMBER = "#8a6d00"
_TAG_RE = _re.compile(r"<[^>]+>")


def _strip_tags(markup, *, drop_label=True):
    """Text content of a markup string with tags and the additive wrap label removed."""
    text = _TAG_RE.sub("", markup or "")
    if drop_label:
        text = text.replace(WRAP_LABEL, "")
    return _html.unescape(text)


def _norm(text):
    return " ".join((text or "").split())


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


def _insert_op(old_text, new_text, new_frag):
    new_start = new_text.index(new_frag)
    return {
        "operation": "insert",
        "old_char_start": len(old_text),
        "old_char_end": len(old_text),
        "new_char_start": new_start,
        "new_char_end": new_start + len(new_frag),
        "old_text": "",
        "new_text": new_frag,
        "old_tokens": [],
        "new_tokens": new_frag.split(),
    }

COMPARISON = {
    "changes": [{
        "change_id": "E-001",
        "change_type": "MODIFIED",
        "old": {"text": "old value"},
        "new": {"text": "new value"},
        "resolution": {"status": "accepted", "needs_review": False},
    }],
    "table_changes": [{
        "table_change_id": "T-001",
        "change_type": "MODIFIED",
        "classification": "cell_change",
        "old": {"logical_table_id": "T1", "row_count": 2, "column_count": 3},
        "new": {"logical_table_id": "T1", "row_count": 2, "column_count": 3},
        "cell_changes": [
            {"old": {"text": "1"}, "new": {"text": "2"}},
        ],
    }],
    "review_queue": [{"review_id": "E-001", "resolution": {"status": "review"}}],
}

class RedlinePdfTest(unittest.TestCase):
    def test_pdf_header(self):
        with TemporaryDirectory() as d:
            self.assertTrue(render_pdf(COMPARISON, Path(d) / "redline.pdf").read_bytes().startswith(b"%PDF-"))
    def test_ids_and_text_extractable(self):
        with TemporaryDirectory() as d:
            with fitz.open(render_pdf(COMPARISON, Path(d) / "redline.pdf")) as doc:
                text = "".join(page.get_text() for page in doc)
        for token in ("E-001", "T-001", "new value"):
            self.assertIn(token, text)
    def test_red_markup_drawings_present(self):
        with TemporaryDirectory() as d:
            with fitz.open(render_pdf(COMPARISON, Path(d) / "redline.pdf")) as doc:
                page = doc[0]
                texts = page.get_text("dict")["blocks"]
                colors = {span.get("color") for block in texts for line in block.get("lines", []) for span in line.get("spans", [])}
                drawings = page.get_drawings()
        # ReportLab Paragraph markup renders as red text spans; underline/strike may also appear as drawings.
        self.assertTrue(any(c is not None and c != 0 for c in colors), colors)
        self.assertTrue(len(drawings) > 0 or any(c is not None and c != 0 for c in colors))
    def test_empty_comparison_still_renders(self):
        with TemporaryDirectory() as d:
            self.assertTrue(render_pdf({}, Path(d) / "redline.pdf").read_bytes().startswith(b"%PDF-"))
    def test_long_text_spans_pages(self):
        comparison = {"changes": [{
            "change_id": f"E-{i:03d}",
            "change_type": "MODIFIED",
            "old": {"text": "x" * 400},
            "new": {"text": "y" * 400},
            "resolution": {"status": "accepted", "needs_review": False},
        } for i in range(1, 40)]}
        with TemporaryDirectory() as d:
            with fitz.open(render_pdf(comparison, Path(d) / "redline.pdf")) as doc:
                page_count = doc.page_count
        self.assertGreaterEqual(page_count, 2)

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


def _mixed_wrap_insert_change():
    old = "Der Sachverhalt ist vorhan- den. Ende."
    new = "Der Sachverhalt ist vorhanden. Ende. Zusatz eins."
    # The insert covers the leading space so the unchanged source gaps stay
    # exactly equal on both sides (a complete, non-stale token diff).
    insert_start = new.index("Zusatz eins.") - 1
    insert_op = {
        "operation": "insert",
        "old_char_start": len(old),
        "old_char_end": len(old),
        "new_char_start": insert_start,
        "new_char_end": len(new),
        "old_text": "",
        "new_text": new[insert_start:],
        "old_tokens": [],
        "new_tokens": new[insert_start:].split(),
    }
    return {
        "change_id": "CHANGE-000151",
        "change_type": "MODIFIED",
        "old": {"text": old, "primary_page": 12},
        "new": {"text": new, "primary_page": 7},
        "token_diff": {"comparison_token_diff": {"changes": [
            _replace_op(old, new, "vorhan- den", "vorhanden"),
            insert_op,
        ]}},
        "resolution": {"status": "review", "needs_review": True},
    }


class DiffSpanTest(unittest.TestCase):
    def test_reliable_offsets_classify_wrap_and_content(self):
        change = _mixed_wrap_insert_change()
        old = change["old"]["text"]
        new = change["new"]["text"]
        spans = build_diff_spans(
            old, new, change["token_diff"], ["possible_wrap", "content"]
        )
        self.assertTrue(spans["reliable"])
        self.assertEqual(spans["wrap_count"], 1)
        self.assertEqual(spans["content_count"], 1)
        self.assertEqual([s["kind"] for s in spans["old_spans"]], ["wrap"])
        self.assertEqual([s["kind"] for s in spans["new_spans"]], ["wrap", "content"])
        self.assertIsNone(spans["warning"])

    def test_mismatched_offsets_fall_back_and_warn(self):
        old = "Der Sache ist re-sign."
        new = "Der Sache ist resign."
        bad = {
            "operation": "replace",
            "old_char_start": 0,
            "old_char_end": 7,
            "new_char_start": 0,
            "new_char_end": 7,
            "old_text": "re-sign",
            "new_text": "resign",
        }
        spans = build_diff_spans(old, new, {"changes": [bad]}, ["content"])
        self.assertFalse(spans["reliable"])
        self.assertEqual(spans["wrap_count"], 0)
        self.assertTrue(spans["content_count"] >= 1)
        self.assertIn("not verified", spans["warning"].lower())
        self.assertTrue(spans["old_spans"])
        self.assertTrue(spans["new_spans"])

    def test_repeated_words_fall_back_without_hiding(self):
        old = "Die Bank und die Bank zahlen."
        new = "Die Bank und die Bank zahlen spaeter."
        spans = build_diff_spans(old, new, None, None)
        self.assertFalse(spans["reliable"])
        self.assertTrue(spans["content_count"] >= 1)
        self.assertTrue(spans["new_spans"])
        result = change_flow_markup({
            "change_id": "X",
            "change_type": "MODIFIED",
            "old": {"text": old},
            "new": {"text": new},
        })
        self.assertEqual(_norm(_strip_tags(result["old"])), _norm(old))
        self.assertEqual(_norm(_strip_tags(result["new"])), _norm(new))


class ReliableRangeAccountingTest(unittest.TestCase):
    """A stored diff may only be trusted when it accounts for ALL source text."""

    def test_empty_operations_with_different_text_falls_back(self):
        spans = build_diff_spans("pay 5", "pay 6", {"changes": []}, None)
        self.assertFalse(spans["reliable"])
        self.assertEqual(spans["wrap_count"], 0)
        self.assertTrue(any(
            span["start"] == 4 and span["end"] == 5 for span in spans["old_spans"]
        ))
        self.assertTrue(any(
            span["start"] == 4 and span["end"] == 5 for span in spans["new_spans"]
        ))
        self.assertIn("not verified", spans["warning"].lower())

    def test_empty_operations_with_equal_text_is_accepted(self):
        spans = build_diff_spans("pay 5", "pay 5", {"changes": []}, None)
        self.assertTrue(spans["reliable"])
        self.assertEqual(spans["content_count"], 0)

    def test_partial_stale_wrap_diff_falls_back_and_highlights_omitted(self):
        old = "Der Wert ist vorhan- den 5."
        new = "Der Wert ist vorhanden 6."
        wrap_only = _replace_op(old, new, "vorhan- den", "vorhanden")
        spans = build_diff_spans(
            old, new, {"changes": [wrap_only]}, ["possible_wrap"]
        )
        self.assertFalse(spans["reliable"])
        self.assertEqual(spans["wrap_count"], 0)
        # The omitted numeric difference must still be highlighted red.
        new_pos = new.index("6")
        self.assertTrue(any(
            span["start"] <= new_pos < span["end"] for span in spans["new_spans"]
        ))

    def test_intervening_omitted_difference_forces_fallback(self):
        old = "5 x 6 z 7"
        new = "4 x 9 z 8"
        first = _replace_op(old, new, "5", "4")
        last = _replace_op(old, new, "7", "8")
        spans = build_diff_spans(
            old, new, {"changes": [first, last]}, ["content", "content"]
        )
        self.assertFalse(spans["reliable"])
        new_pos = new.index("9")
        self.assertTrue(any(
            span["start"] <= new_pos < span["end"] for span in spans["new_spans"]
        ))

    def test_non_string_input_is_rejected(self):
        self.assertIsNone(reliable_operation_ranges(None, "x", {"changes": []}))
        self.assertIsNone(reliable_operation_ranges("x", None, {"changes": []}))


class ChangeFlowMarkupTest(unittest.TestCase):
    def test_common_context_is_plain_not_whole_paragraph_struck(self):
        result = change_flow_markup(_modal_change())
        struck = _re.findall(
            r'<strike><font color="' + _RED + r'">(.*?)</font></strike>',
            result["old"],
        )
        self.assertEqual(struck, ["hat"])
        self.assertNotIn("<strike><font", result["old"].split("Zieht")[0] + "Zieht")
        self.assertIn("Zieht ein Institut Rettungserwerbe", result["old"])

    def test_changed_modal_is_red_and_context_plain(self):
        result = change_flow_markup(_modal_change())
        inserted = _re.findall(
            r'<u><font color="' + _RED + r'">(.*?)</font></u>',
            result["new"],
        )
        self.assertEqual(inserted, ["muss"])
        self.assertIn(" es eine Richtlinie", result["new"])
        self.assertIn("Content differences: 1", result["summary"])
        self.assertIn("Possible line-wrap differences: 0", result["summary"])
        self.assertIn("not verified", result["summary"])
        self.assertNotIn("Operation grouping", result["summary"])

    def test_mixed_possible_wrap_and_insert_are_differentiated(self):
        result = change_flow_markup(_mixed_wrap_insert_change())
        amber_old = _re.findall(
            r'<font color="' + _AMBER + r'">(.*?)</font>', result["old"]
        )
        amber_new = _re.findall(
            r'<font color="' + _AMBER + r'">(.*?)</font>', result["new"]
        )
        red_new = _re.findall(
            r'<u><font color="' + _RED + r'">(.*?)</font></u>', result["new"]
        )
        self.assertEqual(amber_old, ["vorhan- den"])
        self.assertEqual(amber_new, ["vorhanden"])
        self.assertEqual([span.strip() for span in red_new], ["Zusatz eins."])
        self.assertIn(WRAP_LABEL, result["new"])
        self.assertIn("Content differences: 1", result["summary"])
        self.assertIn("Possible line-wrap differences: 1", result["summary"])

    def test_raw_text_equivalent_after_tag_strip_with_xml_escapes(self):
        old = "Risiko & Chance <hoch> gilt für 4 Modelle."
        new = "Risiko & Chance <niedrig> gilt für 5 Modelle."
        change = {
            "change_id": "C",
            "change_type": "MODIFIED",
            "old": {"text": old},
            "new": {"text": new},
            "token_diff": {"comparison_token_diff": {"changes": [
                _replace_op(old, new, "<hoch>", "<niedrig>"),
                _replace_op(old, new, "4", "5"),
            ]}},
        }
        result = change_flow_markup(change)
        self.assertEqual(_norm(_strip_tags(result["old"])), _norm(old))
        self.assertEqual(_norm(_strip_tags(result["new"])), _norm(new))

    def test_added_and_deleted_text_is_localized(self):
        deleted = {
            "change_id": "D",
            "change_type": "DELETED",
            "old": {"text": "Ein ganzer gestrichener Absatz."},
            "new": {"text": ""},
        }
        added = {
            "change_id": "A",
            "change_type": "ADDED",
            "old": {"text": ""},
            "new": {"text": "Ein ganzer neuer Absatz."},
        }
        deleted_result = change_flow_markup(deleted)
        added_result = change_flow_markup(added)
        self.assertIn("Ein ganzer gestrichener Absatz.", deleted_result["old"])
        self.assertIn("<u><font", added_result["new"])
        self.assertIn("Ein ganzer neuer Absatz.", added_result["new"])

    def test_header_shows_pages_and_candidate_id(self):
        result = change_flow_markup(_modal_change())
        self.assertIn("CHANGE-000081", result["header"])
        self.assertIn("p.46", result["header"])
        self.assertIn("p.33", result["header"])
        self.assertIn("MODIFIED-CANDIDATE-000081", result["header"])


class PdfClarityTest(unittest.TestCase):
    def test_pdf_has_plain_context_and_inline_red_change(self):
        comparison = {"changes": [_modal_change()], "table_changes": [], "review_queue": []}
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "redline.pdf")
            with fitz.open(pdf) as doc:
                text = " ".join(page.get_text() for page in doc).split()
        joined = " ".join(text)
        self.assertIn("Zieht ein Institut Rettungserwerbe", joined)
        self.assertIn("muss es eine Richtlinie", joined)
        self.assertNotIn("Operation grouping", joined)
        self.assertIn("Content differences: 1", joined)
        self.assertIn("Possible line-wrap differences: 0", joined)

    def test_pdf_shows_amber_wrap_label_and_keeps_status(self):
        comparison = {
            "changes": [_mixed_wrap_insert_change()],
            "table_changes": [],
            "review_queue": [],
        }
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "redline.pdf")
            with fitz.open(pdf) as doc:
                text = " ".join(page.get_text() for page in doc).split()
        joined = " ".join(text)
        self.assertIn("vorhan- den", joined)
        self.assertIn("Zusatz eins.", joined)
        self.assertIn("Possible line wrap", joined)
        self.assertIn("not verified", joined)
        self.assertIn("review", joined)

    def test_table_structural_warning_stays_separate(self):
        table = {
            "table_change_id": "TABLE-CHANGE-00001",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"row_count": 1, "column_count": 1, "source_pages": [11]},
            "new": {"row_count": 1, "column_count": 1, "source_pages": [12]},
            "cell_changes": [{
                "classification": "STRUCTURE_CHANGED_REVIEW",
                "review_reasons": ["merged_cell_or_header_kind_changed"],
                "old": {"cell_id": "OLD-T0004-C0002", "text": "Massgebliche Positi- onen sind erfasst.", "primary_page": 11},
                "new": {"cell_id": "NEW-T0006-C0002", "text": "Massgebliche Positio- nen sind erfasst.", "primary_page": 12},
                "token_diff": {"source_token_diff": {"changes": [
                    _replace_op(
                        "Massgebliche Positi- onen sind erfasst.",
                        "Massgebliche Positio- nen sind erfasst.",
                        "Positi-",
                        "Positio-",
                    ),
                ]}},
            }],
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        with TemporaryDirectory() as d:
            pdf = render_pdf(comparison, Path(d) / "redline.pdf")
            with fitz.open(pdf) as doc:
                lines = [ln for page in doc for ln in page.get_text().splitlines()]
        joined = " ".join(lines)
        self.assertIn("STRUCTURE_CHANGED_REVIEW", joined)
        self.assertIn("merged_cell_or_header_kind_changed", joined)
        self.assertIn("Possible line wrap", joined)
        warning_lines = [ln for ln in lines if "STRUCTURE_CHANGED_REVIEW" in ln]
        summary_lines = [ln for ln in lines if "Possible line-wrap differences" in ln]
        self.assertTrue(warning_lines)
        self.assertTrue(summary_lines)
        self.assertTrue(all("Possible line-wrap differences" not in ln for ln in warning_lines))

    def test_render_does_not_mutate_input(self):
        comparison = {
            "changes": [_mixed_wrap_insert_change()],
            "table_changes": [],
            "review_queue": [{"review_id": "R", "resolution": {"status": "review"}}],
        }
        snapshot = copy.deepcopy(comparison)
        with TemporaryDirectory() as d:
            render_pdf(comparison, Path(d) / "redline.pdf")
        self.assertEqual(comparison, snapshot)


def _numeric_change(change_id, old, new, old_frag, new_frag):
    """One MODIFIED change whose only difference is ``old_frag`` -> ``new_frag``."""
    return {
        "change_id": change_id,
        "change_type": "MODIFIED",
        "old": {"text": old},
        "new": {"text": new},
        "token_diff": {"comparison_token_diff": {"changes": [
            _replace_op(old, new, old_frag, new_frag),
        ]}},
        "resolution": {"status": "accepted", "needs_review": False},
    }


def _pdf_joined_text(comparison):
    """Render through the real engine and return whitespace-normalized PDF text."""
    with TemporaryDirectory() as d:
        pdf = render_pdf(comparison, Path(d) / "redline.pdf")
        with fitz.open(pdf) as doc:
            extracted = "".join(page.get_text() for page in doc)
    return " ".join(extracted.split())


class NumericFidelityTest(unittest.TestCase):
    """Numbers and Unicode math signs must survive the rendered PDF intact.

    These are fidelity controls over the real renderer, not markup helpers: the
    values are read back out of the produced PDF with PyMuPDF and checked in
    their old/new orientation. ReportLab maps U+2212/U+2264/U+2265 through the
    base-14 Symbol font; none of this may be silently dropped or substituted.
    """

    def _assert_orientations(self, comparison, expected):
        joined = _pdf_joined_text(comparison)
        for old, new in expected:
            self.assertIn(f"old: {old}", joined)
            self.assertIn(f"new: {new}", joined)
        return joined

    def test_ascii_numeric_values_preserved(self):
        comparison = {"changes": [
            _numeric_change("C-21", "Der Wert betraegt 21 EUR.", "Der Wert betraegt 99 EUR.", "21", "99"),
            _numeric_change("C-0", "Anteil 0 Punkte.", "Anteil 1 Punkte.", "0", "1"),
            _numeric_change("C-neg", "Temperatur -12.5 Grad.", "Temperatur -13.5 Grad.", "-12.5", "-13.5"),
            _numeric_change("C-plus", "Zuwachs 12 Prozent.", "Zuwachs 13 Prozent.", "12", "13"),
        ], "table_changes": [], "review_queue": []}
        joined = self._assert_orientations(comparison, [
            ("Der Wert betraegt 21 EUR.", "Der Wert betraegt 99 EUR."),
            ("Anteil 0 Punkte.", "Anteil 1 Punkte."),
            ("Temperatur -12.5 Grad.", "Temperatur -13.5 Grad."),
            ("Zuwachs 12 Prozent.", "Zuwachs 13 Prozent."),
        ])
        # The changed values must be present, not merely the sentence shells.
        for token in ("21", "99", "0", "1", "-12.5", "-13.5", "12", "13"):
            self.assertIn(token, joined)

    def test_decimal_comma_thousands_and_percent_preserved(self):
        comparison = {"changes": [
            _numeric_change("C-comma", "Quote 50,5 Basispunkte.", "Quote 50,6 Basispunkte.", "50,5", "50,6"),
            _numeric_change("C-thousands", "Betrag 1.234,56 EUR.", "Betrag 1.234,57 EUR.", "1.234,56", "1.234,57"),
            _numeric_change("C-percent", "Zins 12,5 % p.a.", "Zins 13,5 % p.a.", "12,5 %", "13,5 %"),
        ], "table_changes": [], "review_queue": []}
        joined = self._assert_orientations(comparison, [
            ("Quote 50,5 Basispunkte.", "Quote 50,6 Basispunkte."),
            ("Betrag 1.234,56 EUR.", "Betrag 1.234,57 EUR."),
            ("Zins 12,5 % p.a.", "Zins 13,5 % p.a."),
        ])
        for token in ("50,5", "50,6", "1.234,56", "1.234,57", "12,5 %", "13,5 %"):
            self.assertIn(token, joined)

    def test_unicode_minus_and_relational_operators_preserved(self):
        """U+2212 minus, U+2264 <=, U+2265 >= must keep their exact code points."""
        comparison = {"changes": [
            _numeric_change("C-min", "Saldo \u221221 Mio.", "Saldo \u221299 Mio.",
                            "\u221221", "\u221299"),
            _numeric_change("C-le", "Wert \u226421 Tage.", "Wert \u226522 Tage.",
                            "\u226421", "\u226522"),
            _numeric_change("C-ge", "Grenze \u226522 Einheiten.", "Grenze \u226421 Einheiten.",
                            "\u226522", "\u226421"),
        ], "table_changes": [], "review_queue": []}
        joined = _pdf_joined_text(comparison)
        self.assertIn("old: Saldo \u221221 Mio.", joined)
        self.assertIn("new: Saldo \u221299 Mio.", joined)
        self.assertIn("old: Wert \u226421 Tage.", joined)
        self.assertIn("new: Wert \u226522 Tage.", joined)
        self.assertIn("old: Grenze \u226522 Einheiten.", joined)
        self.assertIn("new: Grenze \u226421 Einheiten.", joined)
        # Exact code points only: no ASCII "-"/"<="/">=" or U+FFFD substitution.
        for exact in ("\u221221", "\u221299", "\u226421", "\u226522"):
            self.assertIn(exact, joined)
        self.assertNotIn("\ufffd", joined)
        self.assertNotIn("-21 Mio.", joined)
        self.assertNotIn("<=21", joined)
        self.assertNotIn(">=22", joined)

    def test_long_wrapped_table_cell_keeps_values(self):
        old_cell = (
            "Die massgebliche Obergrenze fuer das Gesamtengagement betraegt "
            "1.234,56 Millionen Euro und darf den Schwellenwert von \u226421 "
            "Basispunkten sowie den Saldo von \u221212,50 Prozent keinesfalls "
            "ueberschreiten, sofern die zustaendige Stelle nichts anderes anordnet."
        )
        new_cell = (
            "Die massgebliche Obergrenze fuer das Gesamtengagement betraegt "
            "9.876,54 Millionen Euro und darf den Schwellenwert von \u226522 "
            "Basispunkten sowie den Saldo von \u221299,50 Prozent keinesfalls "
            "ueberschreiten, sofern die zustaendige Stelle nichts anderes anordnet."
        )
        table = {
            "table_change_id": "TABLE-CHANGE-00042",
            "change_type": "TABLE_MODIFIED",
            "classification": "MODIFIED",
            "old": {"row_count": 2, "column_count": 2, "source_pages": [3]},
            "new": {"row_count": 2, "column_count": 2, "source_pages": [4]},
            "cell_changes": [{
                "classification": "cell_change",
                "old": {"cell_id": "OLD-T1-C1", "text": old_cell, "primary_page": 3},
                "new": {"cell_id": "NEW-T1-C1", "text": new_cell, "primary_page": 4},
                "token_diff": {"source_token_diff": {"changes": [
                    _replace_op(old_cell, new_cell, "1.234,56", "9.876,54"),
                    _replace_op(old_cell, new_cell, "\u226421", "\u226522"),
                    _replace_op(old_cell, new_cell, "\u221212,50", "\u221299,50"),
                ]}},
            }],
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        joined = _pdf_joined_text(comparison)
        self.assertIn(f"old cell: {old_cell}", joined)
        self.assertIn(f"new cell: {new_cell}", joined)
        for token in ("1.234,56", "9.876,54", "\u226421", "\u226522",
                      "\u221212,50", "\u221299,50"):
            self.assertIn(token, joined)
        self.assertIn("Content differences: 3", joined)


def _one_sided_deleted_table(*, status, cell_changes=None, include_cell_changes=True):
    table = {
        "table_change_id": "TABLE-CHANGE-00042",
        "change_type": "TABLE_DELETED",
        "old": {
            "logical_table_id": "OLD-LT0009",
            "row_count": 2,
            "column_count": 2,
            "source_pages": [8],
        },
        "new": None,
    }
    if status is not None:
        table["cell_content_status"] = status
    if include_cell_changes:
        table["cell_changes"] = cell_changes if cell_changes is not None else [
            {
                "classification": "DELETED",
                "change_id": "TABLE-DELETED-OLD-LT0009-R00001-C001",
                "old": {
                    "cell_id": "OLD-T0009-C0004",
                    "text": "41",
                    "source_table_id": "OLD-T0009",
                    "primary_page": 8,
                },
                "new": None,
            },
        ]
    return table


def _one_sided_added_table(*, status, include_cell_changes=True):
    table = {
        "table_change_id": "TABLE-CHANGE-00043",
        "change_type": "TABLE_ADDED",
        "old": None,
        "new": {
            "logical_table_id": "NEW-LT0009",
            "row_count": 2,
            "column_count": 2,
            "source_pages": [8],
        },
    }
    if status is not None:
        table["cell_content_status"] = status
    if include_cell_changes:
        table["cell_changes"] = [
            {
                "classification": "ADDED",
                "change_id": "TABLE-ADDED-NEW-LT0009-R00000-C001",
                "old": None,
                "new": {
                    "cell_id": "NEW-T0010-C0002",
                    "text": "17",
                    "source_table_id": "NEW-T0010",
                    "primary_page": 8,
                },
            },
        ]
    return table


class OneSidedTableContentTest(unittest.TestCase):
    """A deleted/added table shows its real cell text, not metadata only."""

    def test_deleted_table_renders_cell_text_and_source_id(self):
        comparison = {
            "changes": [],
            "table_changes": [_one_sided_deleted_table(status="available")],
            "review_queue": [],
        }
        joined = _pdf_joined_text(comparison)
        self.assertIn("old cell: 41", joined)
        self.assertIn("OLD-T0009-C0004", joined)
        self.assertNotIn("Table cell content unavailable", joined)

    def test_added_table_renders_cell_text_and_source_id(self):
        comparison = {
            "changes": [],
            "table_changes": [_one_sided_added_table(status="available")],
            "review_queue": [],
        }
        joined = _pdf_joined_text(comparison)
        self.assertIn("new cell: 17", joined)
        self.assertIn("NEW-T0010-C0002", joined)
        self.assertNotIn("Table cell content unavailable", joined)

    def test_legacy_one_sided_table_without_cells_warns(self):
        """A legacy artifact with no cell content is named, not silently empty."""
        comparison = {
            "changes": [],
            "table_changes": [
                _one_sided_deleted_table(status=None, include_cell_changes=False),
                _one_sided_added_table(status=None, include_cell_changes=False),
            ],
            "review_queue": [],
        }
        joined = _pdf_joined_text(comparison)
        self.assertIn(
            "Table cell content unavailable (legacy artifact); inspect source table.",
            joined,
        )

    def test_explicit_unavailable_status_warns_even_with_no_cells(self):
        comparison = {
            "changes": [],
            "table_changes": [
                _one_sided_deleted_table(status="unavailable", include_cell_changes=False),
            ],
            "review_queue": [],
        }
        joined = _pdf_joined_text(comparison)
        self.assertIn(
            "Table cell content unavailable (legacy artifact); inspect source table.",
            joined,
        )

    def test_zero_cell_one_sided_table_tagged_available_does_not_warn(self):
        table = {
            "table_change_id": "TABLE-CHANGE-00044",
            "change_type": "TABLE_DELETED",
            "old": {
                "logical_table_id": "OLD-LT0009",
                "row_count": 0,
                "column_count": 0,
                "source_pages": [8],
            },
            "new": None,
            "cell_changes": [],
            "cell_content_status": "available",
        }
        comparison = {"changes": [], "table_changes": [table], "review_queue": []}
        joined = _pdf_joined_text(comparison)
        self.assertNotIn("Table cell content unavailable", joined)


if __name__ == "__main__":
    unittest.main()
