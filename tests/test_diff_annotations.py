"""Reporting-only annotation of token-diff operations for the redline.

These tests pin the conservative rules approved for the redline annotation
packet:

* A *possible* wrap difference is an explicit hyphen with adjacent whitespace
  between lowercase word fragments. It is NEVER a verified formatting claim
  without source line-break proof, and it is NEVER global removal of arbitrary
  hyphens/whitespace or case folding.
* ``re-sign``/``resign``, ``co-operating``/``cooperating``,
  ``now here``/``nowhere`` and case-only changes can never be possible wrap.
* A mixed diff keeps its inserted sentences as content differences.
* Other differences stay "content (uncertain)"; the annotation never asserts
  that every remaining operation is meaningful and never infers a DI/extraction
  error.
* Ambiguous parsing leaves the original differences unchanged.
* The helper never mutates its inputs.
* Both render paths expose the grouping summary, the complete original old/new
  text, and a separate structural/cell-kind warning.

All texts here are synthetic; no company sample text is copied.
"""
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from crr_compare import diff_annotations as da
from crr_compare import redline, redline_pdf


def _op(operation, old_text="", new_text="", **extra):
    op = {"operation": operation, "old_text": old_text, "new_text": new_text}
    op.update(extra)
    return op


class LimitedWrapTransformTest(unittest.TestCase):
    def test_hyphen_with_space_between_lowercase_fragments_is_removed(self):
        self.assertEqual(da.limited_wrap_transform("vorhan- den"), "vorhanden")

    def test_space_hyphen_space_between_lowercase_fragments_is_removed(self):
        self.assertEqual(da.limited_wrap_transform("vorhan - den"), "vorhanden")

    def test_hyphen_with_no_whitespace_is_not_a_possible_wrap(self):
        self.assertIsNone(da.limited_wrap_transform("re-sign"))

    def test_uppercase_fragment_is_not_a_possible_wrap(self):
        self.assertIsNone(da.limited_wrap_transform("ge- Handhabt"))

    def test_plain_text_has_no_wrap_transform(self):
        self.assertIsNone(da.limited_wrap_transform("handhabt"))

    def test_non_string_input_is_not_transformed(self):
        self.assertIsNone(da.limited_wrap_transform(None))


class PossibleWrapEquivalentTest(unittest.TestCase):
    def test_joined_lowercase_fragment_is_possible_wrap(self):
        self.assertTrue(da.possible_wrap_equivalent("vorhan- den", "vorhanden"))
        self.assertTrue(da.possible_wrap_equivalent("ge- handhabt", "gehandhabt"))

    def test_hyphen_replacements_are_not_possible_wrap(self):
        self.assertFalse(da.possible_wrap_equivalent("re-sign", "resign"))
        self.assertFalse(da.possible_wrap_equivalent("co-operating", "cooperating"))

    def test_word_boundary_and_case_changes_are_not_possible_wrap(self):
        self.assertFalse(da.possible_wrap_equivalent("now here", "nowhere"))
        self.assertFalse(da.possible_wrap_equivalent("BaFin", "Bafin"))

    def test_numeric_change_is_not_possible_wrap(self):
        self.assertFalse(da.possible_wrap_equivalent("4", "5"))

    def test_genuine_hyphen_word_change_is_not_possible_wrap(self):
        self.assertFalse(da.possible_wrap_equivalent("Kredit-Risiko", "Kredit-Bank"))

    def test_identical_text_is_not_reported_as_wrap(self):
        self.assertFalse(da.possible_wrap_equivalent("gehandhabt", "gehandhabt"))


class AnnotateChangeTest(unittest.TestCase):
    def test_mixed_add_and_wrap_keeps_insert_as_content(self):
        old = "Der Sachverhalt ist vorhan- den. Ende."
        new = "Der Sachverhalt ist vorhanden. Ende. Zusatz eins."
        token_diff = {"changes": [
            _op("replace", "vorhan- den", "vorhanden"),
            _op("insert", "", "Zusatz eins."),
        ]}
        annotation = da.annotate_change(old, new, token_diff)
        self.assertFalse(annotation["whole_text_possible_wrap"])
        self.assertEqual(annotation["possible_wrap_count"], 1)
        self.assertEqual(annotation["content_count"], 1)
        self.assertEqual(annotation["operations"][0]["classification"], "possible_wrap")
        self.assertEqual(annotation["operations"][1]["classification"], "content")
        self.assertIn("possible wrap", annotation["summary"])
        self.assertIn("content", annotation["summary"])

    def test_whole_text_wrap_position_groups_multi_operation_change(self):
        old = "Die massgeblichen Positi- onen sind erfasst."
        new = "Die massgeblichen Positio- nen sind erfasst."
        token_diff = {"changes": [
            {"operation": "replace", "old_tokens": ["Positi"], "new_tokens": ["Positio"]},
            {"operation": "replace", "old_tokens": ["onen"], "new_tokens": ["nen"]},
        ]}
        annotation = da.annotate_change(old, new, token_diff)
        self.assertTrue(annotation["whole_text_possible_wrap"])
        self.assertEqual(annotation["possible_wrap_count"], 2)
        self.assertEqual(annotation["content_count"], 0)
        self.assertTrue(all(
            op["classification"] == "possible_wrap"
            for op in annotation["operations"]
        ))

    def test_numeric_removed_inserted_stays_content_without_error_claim(self):
        old = "Es gilt Absatz 4 weiter."
        new = "Es gilt Absatz 5 weiter."
        token_diff = {"changes": [
            _op("delete", "4", ""),
            _op("insert", "", "5"),
        ]}
        annotation = da.annotate_change(old, new, token_diff)
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 2)
        summary = annotation["summary"].lower()
        self.assertNotIn("extraction", summary)
        self.assertNotIn("error", summary)
        self.assertNotIn("di ", summary)

    def test_ambiguous_token_diff_leaves_differences_unchanged(self):
        annotation = da.annotate_change("a", "b", {"changes": "not-a-list"})
        self.assertTrue(annotation["ambiguous"])
        self.assertEqual(annotation["operations"], [])
        self.assertIn("unavailable", annotation["summary"])

    def test_missing_token_diff_is_ambiguous(self):
        annotation = da.annotate_change("a", "b", None)
        self.assertTrue(annotation["ambiguous"])

    def test_malformed_operation_missing_sides_is_ambiguous(self):
        annotation = da.annotate_change(
            "a", "b", {"changes": [{"operation": "replace"}]}
        )
        self.assertTrue(annotation["ambiguous"])
        self.assertEqual(annotation["operations"], [])
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 0)

    def test_char_offsets_are_preferred_when_reliable(self):
        old = "Der vorhan- den Rest alt."
        new = "Der vorhanden Rest neu."
        old_fragment = "vorhan- den"
        new_fragment = "vorhanden"
        old_start = old.index(old_fragment)
        new_start = new.index(new_fragment)
        token_diff = {"changes": [
            {
                "operation": "replace",
                "old_char_start": old_start,
                "old_char_end": old_start + len(old_fragment),
                "new_char_start": new_start,
                "new_char_end": new_start + len(new_fragment),
                "old_tokens": ["vorhan", "-", "den"],
                "new_tokens": ["vorhanden"],
            },
            {
                "operation": "replace",
                "old_tokens": ["alt"],
                "new_tokens": ["neu"],
            },
        ]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertFalse(annotation["whole_text_possible_wrap"])
        self.assertEqual(annotation["operations"][0]["classification"], "possible_wrap")
        self.assertEqual(annotation["operations"][1]["classification"], "content")
        self.assertEqual(annotation["possible_wrap_count"], 1)
        self.assertEqual(annotation["content_count"], 1)

    def test_inputs_are_not_mutated(self):
        old = "Der Sachverhalt ist vorhan- den."
        new = "Der Sachverhalt ist vorhanden."
        token_diff = {"changes": [_op("replace", "vorhan- den", "vorhanden")]}
        old_before = copy.deepcopy(old)
        new_before = copy.deepcopy(new)
        diff_before = copy.deepcopy(token_diff)
        da.annotate_change(old, new, token_diff)
        self.assertEqual(old, old_before)
        self.assertEqual(new, new_before)
        self.assertEqual(token_diff, diff_before)

    def test_source_fields_are_optional_and_flaggable(self):
        old = "vorhan-\nden"
        new = "vorhanden"
        token_diff = {"changes": [_op("replace", "vorhan- den", "vorhanden")]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 1)
        self.assertTrue(
            annotation["operations"][0]["source_linebreak_consistent"]
        )

    def test_wrap_is_never_labelled_verified_formatting(self):
        old = "vorhan- den"
        new = "vorhanden"
        annotation = da.annotate_change(
            old, new, {"changes": [_op("replace", old, new)]}
        )
        self.assertIn("not verified", annotation["summary"].lower())


class TokenOnlyOperationTest(unittest.TestCase):
    """Operations without ``*_text``/offsets must not invent whitespace proof."""

    def test_token_only_mixed_ops_with_raw_source_wrap_and_content(self):
        old = "Der Sachverhalt ist vorhan- den. Ende."
        new = "Der Sachverhalt ist vorhanden. Ende. Zusatz eins."
        token_diff = {"changes": [
            {
                "operation": "replace",
                "old_tokens": ["vorhan", "-", "den"],
                "new_tokens": ["vorhanden"],
            },
            {
                "operation": "insert",
                "old_tokens": [],
                "new_tokens": ["Zusatz", "eins"],
            },
        ]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 1)
        self.assertEqual(annotation["content_count"], 1)
        self.assertEqual(annotation["operations"][0]["classification"], "possible_wrap")
        self.assertEqual(annotation["operations"][1]["classification"], "content")

    def test_token_only_re_sign_without_raw_whitespace_stays_content(self):
        old = "Please re-sign the contract."
        new = "Please resign the contract."
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["re", "-", "sign"],
            "new_tokens": ["resign"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 1)

    def test_token_only_word_boundary_stays_content(self):
        old = "now here"
        new = "nowhere"
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["now", "here"],
            "new_tokens": ["nowhere"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 1)

    def test_composite_token_only_bare_hyphen_plus_wrap_stays_content(self):
        # One operation spanning both a bare ``re-sign`` (semantic) and a real
        # ``vorhan- den`` wrap must NOT be aggregated into possible wrap.
        old = "re-sign vorhan- den"
        new = "resign vorhanden"
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["re", "-", "sign", "vorhan", "-", "den"],
            "new_tokens": ["resign", "vorhanden"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 1)

    def test_composite_token_only_word_boundary_plus_wrap_stays_content(self):
        old = "now here vorhan- den"
        new = "nowhere vorhanden"
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["now", "here", "vorhan", "-", "den"],
            "new_tokens": ["nowhere", "vorhanden"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 1)

    def test_token_only_multiple_occurrences_is_content(self):
        # The same token sequence occurs twice in the old raw source: the
        # pairing is ambiguous, so prefer content over broad grouping.
        old = "vorhan- den und vorhan- den"
        new = "vorhanden"
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["vorhan", "-", "den"],
            "new_tokens": ["vorhanden"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        self.assertEqual(annotation["possible_wrap_count"], 0)
        self.assertEqual(annotation["content_count"], 1)

    def test_display_join_is_not_used_as_wrap_proof(self):
        old = "re-sign"
        new = "resign"
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["re", "-", "sign"],
            "new_tokens": ["resign"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        # ``" ".join(tokens)`` would look like "re - sign" but that invented
        # whitespace must never become a possible-wrap classification.
        self.assertEqual(annotation["operations"][0]["classification"], "content")
        self.assertNotIn("re - sign", annotation["summary"])

    def test_display_text_is_human_readable_for_token_only_ops(self):
        old = "Der vorhan- den Rest."
        new = "Der vorhanden Rest."
        token_diff = {"changes": [{
            "operation": "replace",
            "old_tokens": ["vorhan", "-", "den"],
            "new_tokens": ["vorhanden"],
        }]}
        annotation = da.annotate_change(
            old, new, token_diff, source_old=old, source_new=new
        )
        operation = annotation["operations"][0]
        self.assertEqual(operation["old_text"], "vorhan - den")
        self.assertEqual(operation["new_text"], "vorhanden")


def _mixed_change():
    old = "Der Sachverhalt ist vorhan- den. Ende."
    new = "Der Sachverhalt ist vorhanden. Ende. Zusatz eins."
    return {
        "change_id": "CHANGE-000001",
        "change_type": "MODIFIED",
        "old": {"text": old, "comparison_text": old},
        "new": {"text": new, "comparison_text": new},
        "token_diff": {"comparison_token_diff": {"changes": [
            _op("replace", "vorhan- den", "vorhanden"),
            _op("insert", "", "Zusatz eins."),
        ]}},
        "resolution": {"status": "accepted", "needs_review": False},
    }


def _structural_table_change():
    old_cell = "Massgebliche Positi- onen sind erfasst."
    new_cell = "Massgebliche Positio- nen sind erfasst."
    return {
        "table_change_id": "TABLE-CHANGE-00001",
        "change_type": "TABLE_MODIFIED",
        "classification": "MODIFIED",
        "old": {"row_count": 1, "column_count": 1},
        "new": {"row_count": 1, "column_count": 1},
        "needs_review": True,
        "review_reasons": ["merged_cell_or_header_kind_changed"],
        "resolution": {"status": "review", "needs_review": True},
        "cell_changes": [{
            "classification": "STRUCTURE_CHANGED_REVIEW",
            "old": {"text": old_cell},
            "new": {"text": new_cell},
            "review_reasons": ["merged_cell_or_header_kind_changed"],
            "token_diff": {"source_token_diff": {"changes": [
                {"operation": "replace", "old_tokens": ["Positi"], "new_tokens": ["Positio"]},
                {"operation": "replace", "old_tokens": ["onen"], "new_tokens": ["nen"]},
            ]}},
        }],
    }


class HtmlRenderAnnotationTest(unittest.TestCase):
    def test_change_block_shows_summary_and_full_text_and_ops(self):
        html = redline.render_change_block(_mixed_change(), "")
        self.assertIn("possible wrap", html)
        self.assertIn("content", html)
        self.assertIn("vorhan- den", html)
        self.assertIn("Zusatz eins.", html)

    def test_table_block_keeps_structural_warning_and_shows_summary(self):
        html = redline.render_table_change(_structural_table_change())
        self.assertIn("STRUCTURE_CHANGED_REVIEW", html)
        self.assertIn("merged_cell_or_header_kind_changed", html)
        self.assertIn("possible wrap", html)
        self.assertIn("Positi- onen", html)
        self.assertIn("Positio- nen", html)


class PdfRenderAnnotationTest(unittest.TestCase):
    def test_pdf_shows_summary_full_text_and_structural_warning(self):
        comparison = {
            "changes": [_mixed_change()],
            "table_changes": [_structural_table_change()],
            "review_queue": [],
        }
        with TemporaryDirectory() as d:
            pdf = redline_pdf.render_pdf(comparison, Path(d) / "redline.pdf")
            with fitz.open(pdf) as doc:
                raw = "".join(page.get_text() for page in doc)
        text = " ".join(raw.split())
        self.assertIn("possible wrap", text)
        self.assertIn("vorhan- den", text)
        self.assertIn("Zusatz eins.", text)
        self.assertIn("STRUCTURE_CHANGED_REVIEW", text)
        self.assertIn("merged_cell_or_header_kind_changed", text)
        self.assertIn("Positi- onen", text)


if __name__ == "__main__":
    unittest.main()
