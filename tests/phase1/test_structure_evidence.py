"""TDD coverage for the additive TOC structure evidence layer (T-03).

The fixtures are explicitly authored synthetics (``tests/structure_fixtures``)
covering text/table TOCs, multi-page continuation, split title/page, repeated
titles, printed/physical page mismatch, dotted levels, absent/partial TOCs and
numeric data-table negatives. The tests pin source immutability, unique
identities, resolvable joins and complete element accounting.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from document_compare.phase1.structure import reconstruction as structure
from document_compare.phase1.stages import s05_anchors
from tests.support import structure_fixtures as fixtures

ROOT = Path(__file__).resolve().parents[2]


def _entry_by_title(evidence: dict, title: str) -> dict:
    for entry in evidence["toc"]["entries"]:
        if entry["title"] == title:
            return entry
    raise AssertionError(f"no TOC entry titled {title!r}: {[e['title'] for e in evidence['toc']['entries']]}")


def _section_by_heading_title(evidence: dict, title: str) -> dict:
    for section in evidence["sections"]:
        if section["title"] == title:
            return section
    raise AssertionError(f"no section titled {title!r}: {[s['title'] for s in evidence['sections']]}")


class FurnitureTocRegressionTest(unittest.TestCase):
    def _header(self, eid, page, text="Contents", *, heading=False):
        return fixtures.paragraph(eid, text, order=0, page=page, top=0.05,
                                  role="sectionHeading" if heading else "pageHeader",
                                  element_type="heading" if heading else "header")

    def test_recurring_contents_header_is_not_a_toc_heading(self):
        document = fixtures.document("HEADER-ONLY", [
            self._header("H1", 1),
            fixtures.paragraph("B1", "Ordinary body text.", order=1),
            self._header("H2", 2, heading=True),
            self._header("H3", 3),
        ], 3)
        before = copy.deepcopy(document)
        evidence = structure.build_structure_evidence(document)
        self.assertFalse(evidence["toc"]["detected"])
        self.assertEqual(set(evidence["furniture"]["element_ids"]), {"H1", "H2", "H3"})
        for eid in ("H1", "H2", "H3"):
            self.assertEqual(evidence["membership"]["by_element"][eid]["category"], "layout")
        self.assertEqual(document, before)

    def test_mislabelled_header_does_not_stop_multipage_text_toc(self):
        document = fixtures.document("INTERLEAVED", [
            self._header("H1", 1, "Running document title"),
            fixtures.paragraph("C", "Contents", order=1, role="sectionHeading"),
            fixtures.paragraph("E1", "Introduction .... 4", order=2),
            self._header("H2", 2, "Running document title", heading=True),
            fixtures.paragraph("E2", "Methods .... 5", order=4, page=2),
            self._header("H3", 3, "Running document title"),
            fixtures.paragraph("B1", "Introduction", order=6, page=4, role="sectionHeading"),
            fixtures.paragraph("B2", "Methods", order=7, page=5, role="sectionHeading"),
        ], 5)
        evidence = structure.build_structure_evidence(document)
        self.assertEqual(evidence["toc"]["entry_count"], 2)
        self.assertEqual(evidence["toc"]["linked_count"], 2)
        self.assertEqual(evidence["membership"]["by_element"]["H2"]["category"], "layout")
        self.assertNotIn("H2", {s["heading_element_id"] for s in evidence["sections"]})

    def test_table_adjacency_skips_derived_furniture(self):
        document = fixtures.document("TABLE-INTERLEAVED", [
            self._header("H1", 1, "Running document title"),
            fixtures.paragraph("C", "Contents", order=1, role="sectionHeading"),
            self._header("H2", 2, "Running document title", heading=True),
            fixtures.table_element("T1", [["Introduction", "4"]], order=3, page=2),
            self._header("H3", 3, "Running document title"),
            fixtures.paragraph("B1", "Introduction", order=5, page=4, role="sectionHeading"),
        ], 4)
        evidence = structure.build_structure_evidence(document)
        self.assertEqual(evidence["toc"]["entry_count"], 1)
        self.assertEqual(evidence["toc"]["entries"][0]["source_table_id"], "T1")


class TocTextTest(unittest.TestCase):
    def setUp(self):
        self.document = fixtures.load("toc_text")
        self.evidence = structure.build_structure_evidence(self.document)

    def test_contents_heading_detected_and_entries_parsed(self):
        self.assertTrue(self.evidence["toc"]["detected"])
        self.assertTrue(self.evidence["toc"]["usable"])
        titles = [entry["title"] for entry in self.evidence["toc"]["entries"]]
        self.assertEqual(titles, ["Introduction", "Methods", "Data"])

    def test_every_entry_links_to_exactly_one_body_heading(self):
        for title, expected_id in (("Introduction", "P-00006"), ("Methods", "P-00008"), ("Data", "P-00010")):
            entry = _entry_by_title(self.evidence, title)
            self.assertEqual(entry["status"], "linked", title)
            self.assertEqual(entry["body_element_id"], expected_id, title)
            self.assertEqual(entry["link_basis"], "exact_title", title)

    def test_dotted_levels_and_parent_are_inferred_from_labels(self):
        methods = _section_by_heading_title(self.evidence, "2. Methods")
        data = _section_by_heading_title(self.evidence, "2.1 Data")
        self.assertTrue(methods["level_known"])
        self.assertEqual(methods["level"], 1)
        self.assertTrue(data["level_known"])
        self.assertEqual(data["level"], 2)
        self.assertTrue(data["parent_known"])
        self.assertEqual(data["parent_section_id"], methods["section_id"])

    def test_contents_heading_is_not_treated_as_a_body_section(self):
        self.assertNotIn("Contents", [section["title"] for section in self.evidence["sections"]])
        self.assertEqual(
            self.evidence["membership"]["by_element"]["P-00002"]["category"], "toc"
        )

    def test_membership_accounts_for_every_element_once(self):
        ids = {element["element_id"] for element in self.document["elements"]}
        self.assertEqual(set(self.evidence["membership"]["by_element"]), ids)
        counts = self.evidence["membership"]["counts"]
        self.assertEqual(sum(counts.values()), len(ids))
        self.assertGreaterEqual(counts["section"], 1)
        self.assertGreaterEqual(counts["preamble"], 1)

    def test_input_is_never_mutated(self):
        before = copy.deepcopy(self.document)
        structure.build_structure_evidence(self.document)
        self.assertEqual(self.document, before)


class SplitTitlePageTest(unittest.TestCase):
    def test_split_title_and_page_paragraphs_form_one_entry(self):
        evidence = structure.build_structure_evidence(fixtures.load("toc_split_title_page"))
        self.assertEqual(evidence["toc"]["entry_count"], 2)
        for entry in evidence["toc"]["entries"]:
            self.assertEqual(len(entry["source_element_ids"]), 2, entry)
            self.assertEqual(entry["status"], "linked", entry)
            self.assertTrue(entry["printed_page"].isdigit())


class TableTocTest(unittest.TestCase):
    def setUp(self):
        self.evidence = structure.build_structure_evidence(fixtures.load("toc_table"))

    def test_table_rows_become_entries_and_are_not_duplicated_by_paragraphs(self):
        entries = self.evidence["toc"]["entries"]
        self.assertEqual(len(entries), 2, entries)
        for entry in entries:
            self.assertEqual(entry["source_kind"], "table")
            self.assertEqual(entry["source_table_id"], "T-00001")
            self.assertNotIn("P-00002", entry["source_element_ids"])
            self.assertNotIn("P-00003", entry["source_element_ids"])
        self.assertEqual(
            sorted(entry["title"] for entry in entries), ["Introduction", "Methods"]
        )

    def test_overlapping_paragraph_cells_are_classified_toc_not_body(self):
        membership = self.evidence["membership"]["by_element"]
        self.assertEqual(membership["P-00002"]["category"], "toc")
        self.assertEqual(membership["P-00003"]["category"], "toc")


class MultiPageContinuationTest(unittest.TestCase):
    def test_toc_continues_across_a_page_break(self):
        evidence = structure.build_structure_evidence(fixtures.load("toc_multipage"))
        self.assertEqual(evidence["toc"]["entry_count"], 4)
        self.assertEqual(evidence["toc"]["linked_count"], 4)


class RepeatedTitleTest(unittest.TestCase):
    def test_duplicate_titles_stay_ambiguous_and_missing_stay_unmatched(self):
        evidence = structure.build_structure_evidence(fixtures.load("toc_repeated_titles"))
        overview = _entry_by_title(evidence, "Overview")
        self.assertEqual(overview["status"], "ambiguous")
        self.assertIsNone(overview["body_element_id"])
        self.assertEqual(len(overview["candidate_body_element_ids"]), 2)
        appendix = _entry_by_title(evidence, "Appendix")
        self.assertEqual(appendix["status"], "unmatched")
        self.assertIsNone(appendix["body_element_id"])
        self.assertIn(overview["entry_id"], evidence["toc"]["ambiguous_entry_ids"])
        self.assertIn(appendix["entry_id"], evidence["toc"]["unmatched_entry_ids"])


class PrintedPageMismatchTest(unittest.TestCase):
    def test_printed_page_is_separate_from_physical_page(self):
        evidence = structure.build_structure_evidence(fixtures.load("toc_printed_page_mismatch"))
        entry = _entry_by_title(evidence, "Introduction")
        self.assertEqual(entry["status"], "linked")
        self.assertEqual(entry["printed_page"], "5")
        link = evidence["links"][0]
        self.assertEqual(link["physical_page"], 2)
        self.assertTrue(link["printed_page_differs_from_physical"])


class NoTocFallbackTest(unittest.TestCase):
    def test_absent_toc_uses_flat_body_anchor_fallback(self):
        evidence = structure.build_structure_evidence(fixtures.load("no_toc"))
        self.assertFalse(evidence["toc"]["detected"])
        self.assertFalse(evidence["toc"]["usable"])
        self.assertTrue(evidence["sections"])
        for section in evidence["sections"]:
            self.assertEqual(section["basis"], "flat_fallback")
            self.assertFalse(section["level_known"])


class PartialTocTest(unittest.TestCase):
    def test_unlisted_body_headings_remain_visible_as_sections(self):
        evidence = structure.build_structure_evidence(fixtures.load("partial_toc"))
        self.assertEqual(evidence["toc"]["linked_count"], 1)
        methods = _section_by_heading_title(evidence, "Methods")
        self.assertEqual(methods["basis"], "body_heading_unlisted")


class DottedLevelsTest(unittest.TestCase):
    def test_levels_follow_dotted_labels_and_never_invent_root_parents(self):
        evidence = structure.build_structure_evidence(fixtures.load("dotted_levels"))
        levels = {section["title"]: section["level"] for section in evidence["sections"]}
        self.assertEqual(levels["1. Overview"], 1)
        self.assertEqual(levels["1.1 Scope"], 2)
        self.assertEqual(levels["1.1.1 Limits"], 3)
        self.assertEqual(levels["1.2 Terms"], 2)
        limits = _section_by_heading_title(evidence, "1.1.1 Limits")
        scope = _section_by_heading_title(evidence, "1.1 Scope")
        self.assertEqual(limits["parent_section_id"], scope["section_id"])
        orphan = _section_by_heading_title(evidence, "2.1 Orphan")
        self.assertFalse(orphan["parent_known"])
        self.assertIsNone(orphan["parent_section_id"])


class NumericDataTableNegativeTest(unittest.TestCase):
    def test_numeric_table_without_corroboration_is_not_a_toc(self):
        evidence = structure.build_structure_evidence(fixtures.load("numeric_data_table_negative"))
        self.assertFalse(evidence["toc"]["detected"])
        self.assertEqual(evidence["toc"]["entry_count"], 0)
        membership = evidence["membership"]["by_element"]
        self.assertNotEqual(membership["T-00001"]["category"], "toc")


class ChangedMissingHeadingTest(unittest.TestCase):
    def test_changed_title_is_unmatched_and_missing_entry_is_unlisted(self):
        evidence = structure.build_structure_evidence(fixtures.load("changed_missing_headings"))
        introduction = _entry_by_title(evidence, "Introduction")
        self.assertEqual(introduction["status"], "unmatched")
        methods = _section_by_heading_title(evidence, "Methods")
        self.assertEqual(methods["basis"], "body_heading_unlisted")


class TableParagraphNoDuplicateTest(unittest.TestCase):
    def test_no_duplicate_entries_from_table_linked_paragraphs(self):
        evidence = structure.build_structure_evidence(fixtures.load("table_paragraph_no_duplicate"))
        self.assertEqual(evidence["toc"]["entry_count"], 2)
        all_source_ids = [
            source_id
            for entry in evidence["toc"]["entries"]
            for source_id in entry["source_element_ids"]
        ]
        self.assertEqual(all_source_ids, [])


class TocTableHeadingDuplicateTest(unittest.TestCase):
    def test_overlapping_heading_paragraph_is_not_a_link_candidate(self):
        evidence = structure.build_structure_evidence(fixtures.load("toc_table_heading_duplicate"))
        entry = _entry_by_title(evidence, "Methods")
        self.assertEqual(entry["status"], "ambiguous")
        self.assertIsNone(entry["body_element_id"])
        self.assertNotIn("P-00002", entry["candidate_body_element_ids"])
        self.assertEqual(sorted(entry["candidate_body_element_ids"]), ["P-00003", "P-00004"])
        self.assertEqual(evidence["membership"]["by_element"]["P-00002"]["category"], "toc")


class NumericDataTableOneHeadingTest(unittest.TestCase):
    def test_single_accidental_heading_match_is_not_a_toc(self):
        evidence = structure.build_structure_evidence(fixtures.load("numeric_data_table_one_heading"))
        self.assertFalse(evidence["toc"]["detected"])
        self.assertEqual(evidence["toc"]["entry_count"], 0)


class DuplicateParentLabelTest(unittest.TestCase):
    def test_duplicate_parent_labels_stay_ambiguous_not_first_wins(self):
        evidence = structure.build_structure_evidence(fixtures.load("dotted_duplicate_parent"))
        beta = _section_by_heading_title(evidence, "1.1 Beta")
        self.assertFalse(beta["parent_known"])
        self.assertIsNone(beta["parent_section_id"])
        self.assertIn("parent_label_ambiguous", beta["uncertainty"])
        delta = _section_by_heading_title(evidence, "1.2 Delta")
        self.assertFalse(delta["parent_known"])
        self.assertIn("parent_label_ambiguous", delta["uncertainty"])


def _namespaced_document() -> dict:
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 1 Overview ......... 2", order=1),
        p("P-00003", "BT 1 Beta ......... 2", order=2),
        p("P-00004", "AT 1.1 NsScope ......... 2", order=3),
        p("P-00005", "1.1 BareScope ......... 3", order=4),
        p("P-00006", "AT 1 Overview", order=5, page=2, role="sectionHeading"),
        p("P-00007", "AT 1.1 NsScope", order=6, page=2, role="sectionHeading"),
        p("P-00008", "BT 1 Beta", order=7, page=3, role="sectionHeading"),
        p("P-00009", "1.1 BareScope", order=8, page=3, role="sectionHeading"),
        p("P-00010", "Body prose.", order=9, page=3),
    ]
    return fixtures.document("DOC-NAMESPACE", elements, 3)


def _namespaced_duplicate_parent_document() -> dict:
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 1 Alpha ......... 2", order=1),
        p("P-00003", "AT 1 Gamma ......... 2", order=2),
        p("P-00004", "AT 1.1 Beta ......... 3", order=3),
        p("P-00005", "AT 1 Alpha", order=4, page=2, role="sectionHeading"),
        p("P-00006", "AT 1 Gamma", order=5, page=3, role="sectionHeading"),
        p("P-00007", "AT 1.1 Beta", order=6, page=3, role="sectionHeading"),
    ]
    return fixtures.document("DOC-NAMESPACE-DUP", elements, 3)


def _furniture_document(
    *,
    header_pages: tuple[int, ...] = (1, 2, 3, 4, 5),
    title_pages: tuple[int, ...] = (2, 3, 4),
    with_geometry: bool = True,
) -> dict:
    p = fixtures.paragraph
    elements = [
        p(f"H-{page}", "ACME Confidential", order=index, page=page, top=0.6, element_type="header")
        for index, page in enumerate(header_pages)
    ]
    for index, page in enumerate(title_pages, start=len(elements)):
        elements.append(
            p(f"T-{page}", "ACME Confidential", order=index, page=page, top=0.6, role="title")
        )
    elements.append(
        p("B-00001", "Body prose.", order=len(elements), page=2, top=8.0)
    )
    document = fixtures.document("DOC-FURNITURE", elements, 5)
    if not with_geometry:
        for element in document["elements"]:
            if element["text"] == "ACME Confidential":
                element["primary_polygon"] = None
                element["bounding_regions"] = []
    return document


def _repeated_body_heading_document() -> dict:
    p = fixtures.paragraph
    elements = [
        p(f"S-{page}", "Overview", order=index, page=page, top=top, role="sectionHeading")
        for index, (page, top) in enumerate(((2, 4.0), (3, 5.5), (4, 7.0)))
    ]
    elements.append(p("B-00001", "Body prose.", order=len(elements), page=2, top=8.0))
    return fixtures.document("DOC-REPEAT-BODY", elements, 4)


class NamespacedHierarchyTest(unittest.TestCase):
    def test_namespaced_dotted_levels_and_namespace_scoped_parents(self):
        evidence = structure.build_structure_evidence(_namespaced_document())
        sections = {section["heading_element_id"]: section for section in evidence["sections"]}
        overview = sections["P-00006"]
        scope = sections["P-00007"]
        beta = sections["P-00008"]
        bare = sections["P-00009"]
        self.assertTrue(overview["level_known"])
        self.assertEqual(overview["level"], 1)
        self.assertTrue(scope["level_known"])
        self.assertEqual(scope["level"], 2)
        self.assertTrue(scope["parent_known"])
        self.assertEqual(scope["parent_section_id"], overview["section_id"])
        # A same-numbered label in another namespace is never the parent.
        self.assertNotEqual(scope["parent_section_id"], beta["section_id"])
        # A bare numeric label never crosses into a named namespace.
        self.assertFalse(bare["parent_known"])
        self.assertIsNone(bare["parent_section_id"])
        self.assertIn("parent_label_not_found", bare["uncertainty"])

    def test_duplicate_namespaced_parents_stay_ambiguous(self):
        evidence = structure.build_structure_evidence(_namespaced_duplicate_parent_document())
        sections = {section["heading_element_id"]: section for section in evidence["sections"]}
        beta = sections["P-00007"]
        self.assertFalse(beta["parent_known"])
        self.assertIsNone(beta["parent_section_id"])
        self.assertIn("parent_label_ambiguous", beta["uncertainty"])


def _qualified_label_document() -> dict:
    """One namespace, shared title text, two different numeric labels."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "BTR 2.1 Allgemeine Anforderungen ......... 3", order=1),
        p("P-00003", "BTR 3.1 Allgemeine Anforderungen ......... 4", order=2),
        p("P-00004", "BTR 2.1 Allgemeine Anforderungen", order=3, page=3, role="sectionHeading"),
        p("P-00005", "BTR 3.1 Allgemeine Anforderungen", order=4, page=4, role="sectionHeading"),
        p("P-00006", "Body prose.", order=5, page=4),
    ]
    return fixtures.document("DOC-QUALIFIED-SAME-TITLE", elements, 4)


def _qualified_namespace_document() -> dict:
    """Shared title text and numeric label, two different namespaces."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 1.1 NsScope ......... 2", order=1),
        p("P-00003", "BT 1.1 NsScope ......... 3", order=2),
        p("P-00004", "AT 1.1 NsScope", order=3, page=2, role="sectionHeading"),
        p("P-00005", "BT 1.1 NsScope", order=4, page=3, role="sectionHeading"),
        p("P-00006", "Body prose.", order=5, page=3),
    ]
    return fixtures.document("DOC-QUALIFIED-NAMESPACE", elements, 3)


def _repeated_qualified_label_document() -> dict:
    """Two body headings carry one fully identical qualified label."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 1.1 NsScope ......... 2", order=1),
        p("P-00003", "AT 1.1 NsScope", order=2, page=2, role="sectionHeading"),
        p("P-00004", "AT 1.1 NsScope", order=3, page=3, role="sectionHeading"),
        p("P-00005", "Body prose.", order=4, page=3),
    ]
    return fixtures.document("DOC-QUALIFIED-REPEATED", elements, 3)


def _unlabeled_heading_document() -> dict:
    """A qualified TOC entry whose only title match is an unlabeled heading."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 1.1 NsScope ......... 2", order=1),
        p("P-00003", "NsScope", order=2, page=2, role="sectionHeading"),
        p("P-00004", "Body prose.", order=3, page=2),
    ]
    return fixtures.document("DOC-QUALIFIED-FALLBACK", elements, 2)


def _single_contradictory_numeric_document() -> dict:
    """The only title match carries a different numeric label."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 2 Kennwert ......... 5", order=1),
        p("P-00003", "AT 3 Kennwert", order=2, page=5, role="sectionHeading"),
        p("P-00004", "Body prose.", order=3, page=5),
    ]
    return fixtures.document("DOC-QUALIFIED-CONTRADICTION", elements, 5)


def _single_cross_namespace_document() -> dict:
    """The only title match shares the number in a different namespace."""
    p = fixtures.paragraph
    elements = [
        p("P-00001", "Contents", order=0, role="sectionHeading"),
        p("P-00002", "AT 2 Kennwert ......... 5", order=1),
        p("P-00003", "BT 2 Kennwert", order=2, page=5, role="sectionHeading"),
        p("P-00004", "Body prose.", order=3, page=5),
    ]
    return fixtures.document("DOC-QUALIFIED-CROSS-NAMESPACE", elements, 5)


class QualifiedLabelDisambiguationTest(unittest.TestCase):
    @staticmethod
    def _entry_by_label(evidence: dict, label: str) -> dict:
        for entry in evidence["toc"]["entries"]:
            if entry["label"] == label:
                return entry
        raise AssertionError(
            f"no entry labelled {label!r}: {[e['label'] for e in evidence['toc']['entries']]}"
        )

    def test_same_namespace_different_numeric_labels_link_to_their_own_heading(self):
        evidence = structure.build_structure_evidence(_qualified_label_document())
        lower = self._entry_by_label(evidence, "BTR 2.1")
        upper = self._entry_by_label(evidence, "BTR 3.1")
        self.assertEqual(lower["status"], "linked")
        self.assertEqual(lower["body_element_id"], "P-00004")
        self.assertEqual(lower["link_basis"], "exact_qualified_title")
        self.assertEqual(upper["status"], "linked")
        self.assertEqual(upper["body_element_id"], "P-00005")
        self.assertEqual(upper["link_basis"], "exact_qualified_title")

    def test_same_numeric_label_in_different_namespace_links_by_namespace(self):
        evidence = structure.build_structure_evidence(_qualified_namespace_document())
        at_entry = self._entry_by_label(evidence, "AT 1.1")
        bt_entry = self._entry_by_label(evidence, "BT 1.1")
        self.assertEqual(at_entry["status"], "linked")
        self.assertEqual(at_entry["body_element_id"], "P-00004")
        self.assertEqual(bt_entry["status"], "linked")
        self.assertEqual(bt_entry["body_element_id"], "P-00005")

    def test_repeated_exact_qualified_labels_stay_ambiguous(self):
        evidence = structure.build_structure_evidence(_repeated_qualified_label_document())
        entry = self._entry_by_label(evidence, "AT 1.1")
        self.assertEqual(entry["status"], "ambiguous")
        self.assertIsNone(entry["body_element_id"])
        self.assertEqual(entry["ambiguity_reason"], "duplicate_exact_label")
        self.assertEqual(sorted(entry["candidate_body_element_ids"]), ["P-00003", "P-00004"])

    def test_unlabeled_heading_fallback_survives_without_an_exact_label_pair(self):
        evidence = structure.build_structure_evidence(_unlabeled_heading_document())
        entry = self._entry_by_label(evidence, "AT 1.1")
        self.assertEqual(entry["status"], "linked")
        self.assertEqual(entry["body_element_id"], "P-00003")
        self.assertEqual(entry["link_basis"], "exact_title")

    def test_single_candidate_contradictory_numeric_is_unmatched(self):
        evidence = structure.build_structure_evidence(_single_contradictory_numeric_document())
        entry = self._entry_by_label(evidence, "AT 2")
        self.assertEqual(entry["status"], "unmatched")
        self.assertIsNone(entry["body_element_id"])
        self.assertEqual(entry["ambiguity_reason"], "label_conflict")

    def test_single_candidate_different_namespace_same_numeric_is_unmatched(self):
        evidence = structure.build_structure_evidence(_single_cross_namespace_document())
        entry = self._entry_by_label(evidence, "AT 2")
        self.assertEqual(entry["status"], "unmatched")
        self.assertIsNone(entry["body_element_id"])
        self.assertEqual(entry["ambiguity_reason"], "label_conflict")

    def test_single_candidate_missing_parsed_numeric_still_links(self):
        evidence = structure.build_structure_evidence(_unlabeled_heading_document())
        entry = self._entry_by_label(evidence, "AT 1.1")
        self.assertEqual(entry["status"], "linked")
        self.assertEqual(entry["body_element_id"], "P-00003")
        self.assertEqual(entry["link_basis"], "exact_title")
        self.assertIsNone(entry["ambiguity_reason"])

    def test_title_only_fallback_ambiguity_is_kept_and_distinguished(self):
        document = _unlabeled_heading_document()
        document["elements"].insert(
            3,
            fixtures.paragraph("P-00003B", "BT 2.2 NsScope", order=3, page=3, role="sectionHeading"),
        )
        document = fixtures.document("DOC-QUALIFIED-FALLBACK-AMBIG", document["elements"], 3)
        evidence = structure.build_structure_evidence(document)
        entry = self._entry_by_label(evidence, "AT 1.1")
        self.assertEqual(entry["status"], "ambiguous")
        self.assertIsNone(entry["body_element_id"])
        self.assertEqual(entry["ambiguity_reason"], "title_only_fallback")
        self.assertEqual(sorted(entry["candidate_body_element_ids"]), ["P-00003", "P-00003B"])


class DerivedFurnitureTest(unittest.TestCase):
    def test_recurring_title_with_supporting_layout_role_and_geometry_is_furniture(self):
        evidence = structure.build_structure_evidence(_furniture_document())
        for page in (2, 3, 4):
            self.assertIn(f"T-{page}", evidence["furniture"]["element_ids"])
        self.assertNotIn(
            "ACME Confidential", [section["title"] for section in evidence["sections"]]
        )
        self.assertEqual(
            evidence["membership"]["by_element"]["T-2"]["category"], "layout"
        )
        record = evidence["furniture"]["by_element"]["T-2"]
        self.assertEqual(record["classification"], "furniture")
        self.assertTrue(record["reason"])
        self.assertIn("occurrence_count", record["evidence"])

    def test_legitimate_repeated_body_heading_survives_not_furniture(self):
        evidence = structure.build_structure_evidence(_repeated_body_heading_document())
        self.assertEqual(evidence["furniture"]["element_ids"], [])
        titles = [section["title"] for section in evidence["sections"]]
        self.assertEqual(titles.count("Overview"), 3)

    def test_missing_geometry_stays_conservative(self):
        evidence = structure.build_structure_evidence(_furniture_document(with_geometry=False))
        self.assertEqual(evidence["furniture"]["element_ids"], [])
        self.assertIn(
            "ACME Confidential", [section["title"] for section in evidence["sections"]]
        )

    def test_recurrence_and_geometry_without_supporting_role_stay_conservative(self):
        evidence = structure.build_structure_evidence(
            _furniture_document(header_pages=(), title_pages=(2, 3, 4, 5))
        )
        self.assertEqual(evidence["furniture"]["element_ids"], [])
        self.assertIn(
            "ACME Confidential", [section["title"] for section in evidence["sections"]]
        )


class AnchorFurnitureSuppressionTest(unittest.TestCase):
    def test_derived_furniture_title_is_not_promoted_to_an_anchor(self):
        element = fixtures.paragraph(
            "T-2", "ACME Confidential", order=0, page=2, top=0.6, role="title"
        )
        evidence, reason = s05_anchors.anchor_evidence(element, set())
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["anchor_type"], "di_heading")
        suppressed, suppressed_reason = s05_anchors.anchor_evidence(element, set(), {"T-2"})
        self.assertIsNone(suppressed)
        self.assertEqual(suppressed_reason, "derived_recurring_furniture_needs_review")

    def test_body_heading_is_still_promoted_with_unrelated_furniture(self):
        element = fixtures.paragraph(
            "S-1", "Overview", order=0, page=2, top=5.0, role="sectionHeading"
        )
        evidence, _ = s05_anchors.anchor_evidence(element, set(), {"OTHER"})
        self.assertIsNotNone(evidence)


@unittest.skipUnless(
    (ROOT / "output_sample" / "old_document.json").is_file(),
    "private output_sample/old_document.json fixture is unavailable in this checkout",
)
class SampleStructureReplayConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(
            (ROOT / "output_sample" / "old_document.json").read_text(encoding="utf-8")
        )["document"]
        cls.evidence = structure.build_structure_evidence(cls.document)
        cls.rebuilt = structure.build_structure_evidence(cls.document)

    def test_replay_is_deterministic(self):
        self.assertEqual(self.evidence, self.rebuilt)

    def test_qualified_labels_resolve_repeated_titles_not_ambiguous(self):
        entries = {entry["label"]: entry for entry in self.evidence["toc"]["entries"]}
        expectations = {
            "BTO 2.1": "OLD-P01400",
            "BTR 2": "OLD-P01618",
            "BTR 2.1": "OLD-P01619",
            "BTR 3.1": "OLD-P01681",
            "AT 4.3.3": "OLD-P00475",
        }
        for label, body_id in expectations.items():
            entry = entries[label]
            self.assertEqual(entry["status"], "linked", label)
            self.assertEqual(entry["body_element_id"], body_id, label)
            self.assertEqual(entry["link_basis"], "exact_qualified_title", label)
        self.assertEqual(self.evidence["toc"]["linked_count"], 76)
        self.assertEqual(self.evidence["toc"]["ambiguous_count"], 0)

    def test_recurring_title_header_is_derived_furniture(self):
        membership = self.evidence["membership"]["by_element"]
        for element_id in ("OLD-P00076", "OLD-P00149", "OLD-P00190"):
            self.assertEqual(membership[element_id]["category"], "layout")
            self.assertIn(element_id, self.evidence["furniture"]["element_ids"])

    def test_namespaced_family_levels_and_parents(self):
        sections = {
            section["heading_element_id"]: section for section in self.evidence["sections"]
        }
        scope = sections["OLD-P00217"]
        self.assertTrue(scope["level_known"])
        self.assertEqual(scope["level"], 2)
        self.assertTrue(scope["parent_known"])
        self.assertEqual(
            scope["parent_section_id"], sections["OLD-P00210"]["section_id"]
        )
        for heading_id in ("OLD-P00231", "OLD-P00252"):
            self.assertEqual(
                sections[heading_id]["parent_section_id"],
                sections["OLD-P00210"]["section_id"],
            )

    def test_every_element_is_accounted_for_once(self):
        ids = {element["element_id"] for element in self.document["elements"]}
        self.assertEqual(set(self.evidence["membership"]["by_element"]), ids)


class ValidationTest(unittest.TestCase):
    def test_validate_accepts_built_evidence(self):
        document = fixtures.load("toc_text")
        evidence = structure.build_structure_evidence(document)
        structure.validate_structure_evidence(document, evidence)

    def test_duplicate_entry_id_is_rejected(self):
        document = fixtures.load("toc_text")
        evidence = structure.build_structure_evidence(document)
        evidence["toc"]["entries"][1]["entry_id"] = evidence["toc"]["entries"][0]["entry_id"]
        with self.assertRaises(ValueError):
            structure.validate_structure_evidence(document, evidence)

    def test_membership_must_cover_every_element(self):
        document = fixtures.load("toc_text")
        evidence = structure.build_structure_evidence(document)
        del evidence["membership"]["by_element"]["P-00001"]
        with self.assertRaises(ValueError):
            structure.validate_structure_evidence(document, evidence)

    def test_membership_count_mismatch_is_rejected(self):
        document = fixtures.load("toc_text")
        evidence = structure.build_structure_evidence(document)
        evidence["membership"]["counts"]["toc"] += 1
        with self.assertRaises(ValueError):
            structure.validate_structure_evidence(document, evidence)

    def test_unknown_source_cell_is_rejected(self):
        document = fixtures.load("toc_table")
        evidence = structure.build_structure_evidence(document)
        evidence["toc"]["entries"][0]["source_cell_ids"] = ["NOPE"]
        with self.assertRaises(ValueError):
            structure.validate_structure_evidence(document, evidence)

    def test_unknown_parent_section_is_rejected(self):
        document = fixtures.load("toc_text")
        evidence = structure.build_structure_evidence(document)
        evidence["sections"][1]["parent_section_id"] = "NOT-A-SECTION"
        with self.assertRaises(ValueError):
            structure.validate_structure_evidence(document, evidence)


class FixtureReproducibilityTest(unittest.TestCase):
    def test_committed_fixtures_match_the_builder(self):
        for name in fixtures.BUILDERS:
            with self.subTest(name=name):
                self.assertEqual(fixtures.load(name), fixtures.build(name))


if __name__ == "__main__":
    unittest.main()
