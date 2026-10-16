"""Focused tests for German scan end-of-line hyphenation handling (s04).

Approved rule: a hyphen directly before a line break is joined only when the
continuation begins with a lowercase letter and is not a suspended-hyphen
function word. Uppercase continuations, digits, same-line hyphens, and genuine
compounds keep the visible hyphen so the difference stays reviewable.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

STAGE_MODULE = "document_compare.phase1.stages.s04_normalize"


def _load_normalizer():
    """Import the real s04 module against a throwaway Step-3 root.

    The stage file is notebook-converted and loads its inputs at import time,
    so a minimal valid old/new document pair is seeded in a temp project root.
    """
    tmp = tempfile.mkdtemp(prefix="crr-s04-test-")
    out = Path(tmp) / "output"
    out.mkdir(parents=True)
    document = {
        "document": {
            "elements": [
                {
                    "element_id": "e1",
                    "element_type": "paragraph",
                    "text": "seed",
                    "reading_order": 0,
                }
            ]
        }
    }
    for name in ("old_document.json", "new_document.json"):
        (out / name).write_text(json.dumps(document), encoding="utf-8")

    previous = os.environ.get("CRR_PROJECT_ROOT")
    os.environ["CRR_PROJECT_ROOT"] = tmp
    try:
        sys.modules.pop(STAGE_MODULE, None)
        module = importlib.import_module(STAGE_MODULE)
    finally:
        if previous is None:
            os.environ.pop("CRR_PROJECT_ROOT", None)
        else:
            os.environ["CRR_PROJECT_ROOT"] = previous
    return module


s04 = _load_normalizer()
normalize = s04.normalize_text
normalize_with_flags = s04.normalize_text_with_flags


class LowercaseWrapJoinTest(unittest.TestCase):
    def test_lowercase_continuation_joins(self):
        self.assertEqual(normalize("Kredit-\ninstitut"), "Kreditinstitut")
        self.assertEqual(normalize("Kredit-\ninstitut"), normalize("Kreditinstitut"))

    def test_join_flag_recorded(self):
        _, flags = normalize_with_flags("Kredit-\ninstitut")
        self.assertIn("line_break_hyphenation_joined", flags)

    def test_lowercase_with_punctuation_joins(self):
        self.assertEqual(normalize("Kredit-\ninstitut."), "Kreditinstitut.")

    def test_english_lowercase_wrap_joins(self):
        self.assertEqual(normalize("risk-\nbased"), "riskbased")


class UppercaseContinuationPreservedTest(unittest.TestCase):
    def test_uppercase_continuation_not_joined(self):
        self.assertEqual(normalize("Kredit-\nInstitut"), "Kredit- Institut")
        self.assertNotEqual(normalize("Kredit-\nInstitut"), normalize("KreditInstitut"))

    def test_uppercase_compound_example_not_joined(self):
        self.assertEqual(normalize("E-\nMail"), "E- Mail")

    def test_uppercase_no_join_flag(self):
        _, flags = normalize_with_flags("Kredit-\nInstitut")
        self.assertNotIn("line_break_hyphenation_joined", flags)

    def test_uppercase_german_compound_not_joined(self):
        self.assertEqual(normalize("Kredit-\nRisiko"), "Kredit- Risiko")


class DigitContinuationPreservedTest(unittest.TestCase):
    def test_digit_continuation_not_joined(self):
        self.assertEqual(normalize("Kredit-\n123"), "Kredit- 123")
        self.assertNotEqual(normalize("Kredit-\n123"), normalize("Kredit123"))


class SameLineCompoundPreservedTest(unittest.TestCase):
    def test_visible_hyphen_compounds_unchanged(self):
        for text in ("E-Mail", "EU-Recht", "Kredit-Risiko", "Kredit-institut"):
            with self.subTest(text=text):
                self.assertEqual(normalize(text), text)

    def test_same_line_hyphen_does_not_join(self):
        self.assertNotEqual(normalize("Kredit-institut"), normalize("Kreditinstitut"))


class StopwordWrapPreservedTest(unittest.TestCase):
    def test_suspended_stopword_keeps_hyphen_and_space(self):
        self.assertEqual(normalize("Risiko-\nund Ertrag"), "Risiko- und Ertrag")
        _, flags = normalize_with_flags("Risiko-\nund Ertrag")
        self.assertNotIn("line_break_hyphenation_joined", flags)


class ChangedWrappedWordVisibleTest(unittest.TestCase):
    def test_changed_lowercase_wrapped_word_is_a_change(self):
        self.assertNotEqual(normalize("Kredit-\ninstitut"), normalize("Kredit-\nbank"))

    def test_changed_uppercase_wrapped_word_is_a_change(self):
        self.assertNotEqual(normalize("Kredit-\nInstitut"), normalize("Kredit-\nBank"))


class DocumentNormalizationTest(unittest.TestCase):
    def test_document_level_wrap_forms(self):
        model = {
            "document": {
                "elements": [
                    {
                        "element_id": "e1",
                        "element_type": "paragraph",
                        "text": "Kredit-\ninstitut",
                        "reading_order": 0,
                    },
                    {
                        "element_id": "e2",
                        "element_type": "paragraph",
                        "text": "Kredit-\nInstitut",
                        "reading_order": 1,
                    },
                ]
            }
        }
        normalized = s04.normalize_document(model)
        texts = [e["normalized_text"] for e in normalized["document"]["elements"]]
        self.assertEqual(texts[0], "Kreditinstitut")
        self.assertEqual(texts[1], "Kredit- Institut")


class IdempotenceTest(unittest.TestCase):
    def test_uppercase_preserved_output_is_stable(self):
        once = normalize("Kredit-\nInstitut")
        self.assertEqual(normalize(once), once)

    def test_lowercase_joined_output_is_stable(self):
        once = normalize("Kredit-\ninstitut")
        self.assertEqual(normalize(once), once)


class WrapProvenanceBoundaryTest(unittest.TestCase):
    """s04 leaves the evidence s11 needs to prove (or reject) a line-wrap.

    A real lowercase newline wrap is joined; the identical characters with only
    a space (same line) are NOT joined, so the ambiguous boundary survives into
    comparison/review instead of being silently flattened. The raw ``text`` is
    never rewritten, so the newline provenance stays inspectable downstream.
    """

    def test_same_line_hyphen_space_is_not_joined(self):
        self.assertEqual(normalize("ge- handhabt"), "ge- handhabt")
        self.assertNotEqual(normalize("ge- handhabt"), normalize("gehandhabt"))

    def test_lowercase_newline_wrap_equals_plain_join(self):
        self.assertEqual(normalize("ge-\nhandhabt"), "gehandhabt")
        self.assertEqual(normalize("ge-\nhandhabt"), normalize("gehandhabt"))

    def test_raw_source_text_preserved_for_provenance(self):
        model = {
            "document": {
                "elements": [
                    {
                        "element_id": "e1",
                        "element_type": "paragraph",
                        "text": "ge-\nhandhabt",
                        "reading_order": 0,
                    }
                ]
            }
        }
        normalized = s04.normalize_document(model)
        element = normalized["document"]["elements"][0]
        self.assertEqual(element["text"], "ge-\nhandhabt")
        self.assertEqual(element["normalized_text"], "gehandhabt")


if __name__ == "__main__":
    unittest.main()
