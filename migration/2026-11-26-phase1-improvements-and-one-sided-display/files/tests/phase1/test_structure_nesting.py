"""Synthetic regression coverage for Stage 05 section nesting."""

import unittest

from document_compare.phase1.structure import reconstruction
from document_compare.phase2.viewer.outline import _Side, _build_tree
from tests.support.structure_fixtures import document, paragraph


def _german_document(*, with_toc=True):
    rows = [("Vorbemerkung", 1, "sectionHeading")]
    if with_toc:
        rows.extend([
            ("Inhaltsverzeichnis", 1, "sectionHeading"),
            ("1 Grundlagen .... 2", 1, None),
            ("1.1 Geltungsbereich .... 2", 1, None),
            ("2 Verfahren .... 3", 1, None),
        ])
    rows.extend([
        ("1 Grundlagen", 2, "sectionHeading"),
        ("1.1 Geltungsbereich", 2, "sectionHeading"),
        ("Erläuterungen", 2, "sectionHeading"),
        ("Beispiele", 2, "sectionHeading"),
        ("2 Verfahren", 3, "sectionHeading"),
        ("Hinweis", 3, "sectionHeading"),
    ])
    elements = [
        paragraph(f"P-{order:05d}", text, order=order, page=page, role=role)
        for order, (text, page, role) in enumerate(rows)
    ]
    result = document("GERMAN-NESTING", elements, 3)
    result["elements"].reverse()
    return result


class StructureNestingTest(unittest.TestCase):
    def setUp(self):
        self.document = _german_document()
        self.evidence = reconstruction.build_structure_evidence(self.document)
        self.sections = {section["title"]: section for section in self.evidence["sections"]}

    def assert_position_parent(self, title, parent_title, level):
        section = self.sections[title]
        self.assertEqual(section["basis"], "body_heading_unlisted")
        self.assertEqual(section["parent_section_id"], self.sections[parent_title]["section_id"])
        self.assertIs(section["parent_known"], True)
        self.assertEqual(section["level"], level)
        self.assertIs(section["level_known"], True)
        self.assertEqual(section["parent_basis"], "preceding_toc_section")
        self.assertIn("parent_inferred_from_position", section["uncertainty"])
        self.assertNotIn("level_and_parent_unknown", section["uncertainty"])

    def assert_unknown_hierarchy(self, section):
        self.assertIsNone(section["level"])
        self.assertIs(section["level_known"], False)
        self.assertIsNone(section["parent_section_id"])
        self.assertIs(section["parent_known"], False)
        self.assertIsNone(section.get("parent_basis"))
        self.assertEqual(section["uncertainty"], ["level_and_parent_unknown"])

    def test_unlisted_headings_share_nearest_preceding_toc_parent(self):
        self.assertEqual(self.evidence["toc"]["linked_count"], 3)
        self.assert_position_parent("Erläuterungen", "1.1 Geltungsbereich", 3)
        self.assert_position_parent("Beispiele", "1.1 Geltungsbereich", 3)
        self.assert_position_parent("Hinweis", "2 Verfahren", 2)

    def test_heading_before_first_linked_section_keeps_unknown_hierarchy(self):
        preface = self.sections["Vorbemerkung"]
        self.assertEqual(preface["basis"], "body_heading_unlisted")
        self.assert_unknown_hierarchy(preface)

    def test_no_usable_toc_keeps_flat_fallback(self):
        evidence = reconstruction.build_structure_evidence(_german_document(with_toc=False))
        self.assertFalse(evidence["toc"]["usable"])
        self.assertEqual(list(self.sections), [section["title"] for section in evidence["sections"]])
        for section in evidence["sections"]:
            self.assertEqual(section["basis"], "flat_fallback")
            self.assert_unknown_hierarchy(section)

    def test_numeric_parent_records_numeric_label_basis(self):
        scope = self.sections["1.1 Geltungsbereich"]
        self.assertEqual(scope["parent_section_id"], self.sections["1 Grundlagen"]["section_id"])
        self.assertIs(scope["parent_known"], True)
        self.assertEqual(scope["level"], 2)
        self.assertEqual(scope["parent_basis"], "numeric_label")

    def test_pdf_outline_parent_is_used_in_heading_reading_order(self):
        evidence = reconstruction.build_structure_evidence(self.document, pdf_outline={
            "status": "available",
            "entries": [{
                "entry_id": "OUTLINE-0001",
                "source_order": 0,
                "title": "2 Verfahren",
                "physical_page": 3,
                "level": 1,
            }],
        })
        self.sections = {section["title"]: section for section in evidence["sections"]}
        self.assertEqual(self.sections["2 Verfahren"]["basis"], "pdf_outline_link")
        self.assert_unknown_hierarchy(self.sections["Vorbemerkung"])
        self.assert_position_parent("Erläuterungen", "1.1 Geltungsbereich", 3)
        self.assert_position_parent("Hinweis", "2 Verfahren", 2)

    def test_phase_two_outline_nests_unlisted_headings(self):
        roots, index = _build_tree({
            "old": _Side(None),
            "new": _Side({**self.document, "structure_evidence": self.evidence}),
        })
        roots_by_title = {node.title for node in roots}
        self.assertEqual(roots_by_title, {"Vorbemerkung", "1 Grundlagen", "2 Verfahren"})
        for parent_title, children in (
            ("1.1 Geltungsbereich", ["Erläuterungen", "Beispiele"]),
            ("2 Verfahren", ["Hinweis"]),
        ):
            with self.subTest(parent=parent_title):
                parent = index["new", self.sections[parent_title]["section_id"]]
                self.assertEqual([child.title for child in parent.children], children)

    def test_validation_accepts_missing_or_null_parent_basis(self):
        self.sections["1.1 Geltungsbereich"].pop("parent_basis", None)
        self.sections["2 Verfahren"]["parent_basis"] = None
        reconstruction.validate_structure_evidence(self.document, self.evidence)

    def test_validation_rejects_unknown_parent_basis(self):
        self.sections["1.1 Geltungsbereich"]["parent_basis"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown parent_basis"):
            reconstruction.validate_structure_evidence(self.document, self.evidence)

    def test_validation_rejects_self_and_multi_section_parent_cycles(self):
        root = self.sections["1 Grundlagen"]
        for parent in (root, self.sections["1.1 Geltungsbereich"]):
            with self.subTest(parent=parent["title"]):
                root["parent_section_id"] = parent["section_id"]
                with self.assertRaisesRegex(ValueError, "parent cycle"):
                    reconstruction.validate_structure_evidence(self.document, self.evidence)
