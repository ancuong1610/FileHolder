"""Offline regressions for extraction typing, correspondence and page furniture."""

import unittest

from document_compare.phase1.matching.reconciliation import reconcile_residuals
from document_compare.phase1.stages import s03_internal_schema as schema
from document_compare.phase1.stages import s06_exact_matching as exact
from document_compare.phase1.stages import s07_modified_matching as modified
from document_compare.phase1.stages.s08_candidates import build_comparison_candidates
from document_compare.phase1.stages.s09_movement import OUTPUT_STAGE, element_key
from document_compare.phase1.stages.s11_routing import build_generic_routing
from document_compare.phase1.stages.s14_canonical_result import build_comparison_result
from document_compare.phase1.structure.furniture import (
    classify_derived_furniture,
    confirm_furniture_exclusions,
)
from document_compare.phase1.structure.reconstruction import build_structure_evidence


def _element(element_id, text, element_type="paragraph", *, page=1, y=50, order=1):
    return {
        "element_id": element_id,
        "element_type": element_type,
        "original_role": {"heading": "sectionHeading", "title": "title"}.get(element_type),
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "comparison_eligible": True,
        "primary_page": page,
        # x exceeds page width to exercise rotated extraction coordinates.
        "primary_polygon": [110, y, 130, y, 130, y + 2, 110, y + 2],
        "reading_order": order,
        "heading_evidence": {},
        "extraction_validation": {"requires_extraction_review": False},
    }


def _document(elements, *, page_count=12):
    return {
        "page_count": page_count,
        "pages": [
            {"page_number": page, "width": 100, "height": 140}
            for page in range(1, page_count + 1)
        ],
        "elements": elements,
    }


def _match(olds, news):
    exact_result = exact.build_result({}, {}, olds, news, [], [], {}, {})
    exact.validate_result(exact_result, olds, news)
    modified_result = modified.build_result(exact_result)
    modified.validate_result(modified_result)
    return exact_result, modified_result


def _canonical(exact_result, modified_result):
    candidates = build_comparison_candidates(exact_result, modified_result)
    movement = {**candidates, "comparison_stage": OUTPUT_STAGE}
    tables = {"comparison_stage": "logical_table_comparison_with_provisional_context"}
    routing = build_generic_routing(movement, tables)
    routing["comparison_stage"] = "deterministic_review_routing"
    return candidates, build_comparison_result(movement, tables, routing)


class TextFamilyTest(unittest.TestCase):
    def test_exact_heading_to_paragraph_has_evidence_and_no_canonical_change(self):
        text = "Voraussetzungen für den Zugriff auf personenbezogene Daten"
        old = _element("OLD-1", text, "heading")
        new = _element("NEW-1", text)
        exact_result, modified_result = _match([old], [new])
        pair, = exact_result["matches"]
        type_change = {"old": "heading", "new": "paragraph"}
        self.assertEqual(pair["type_changed"], type_change)
        self.assertEqual(pair["old"]["element_type"], "heading")
        self.assertEqual(pair["new"]["element_type"], "paragraph")
        self.assertEqual(element_key(old), element_key(new))
        candidates, result = _canonical(exact_result, modified_result)
        self.assertEqual(candidates["unchanged"][0]["type_changed"], type_change)
        self.assertEqual(result["changes"], [])

    def test_modified_heading_to_paragraph_has_impact_kind(self):
        old = _element("OLD-1", "Für den Zugriff gelten weiterhin die folgenden Voraussetzungen.", "heading")
        new = _element("NEW-1", "Für den Zugriff gelten weiterhin die folgenden Bedingungen.")
        exact_result, modified_result = _match([old], [new])
        pairs = modified_result["modified_matches"] + modified_result["review_pairs"]
        pair, = pairs
        self.assertFalse(pair["similarity_evidence"]["same_element_type"])
        self.assertEqual(pair["type_changed"], {"old": "heading", "new": "paragraph"})
        _, result = _canonical(exact_result, modified_result)
        change, = result["changes"]
        self.assertEqual(change["change_type"], "MODIFIED")
        self.assertEqual(change["impact"]["level"], "content")
        self.assertIn("element_type_changed", change["impact"]["kinds"])
        self.assertEqual(change["type_changed"], pair["type_changed"])

    def test_table_and_layout_do_not_pair_with_body_text(self):
        for element_type in ("table", "header", "footer", "page_number"):
            with self.subTest(element_type=element_type):
                text = "Voraussetzungen für den Zugriff auf personenbezogene Daten"
                old = _element("OLD-1", text, element_type)
                for new_text in (text, text + " prüfen"):
                    new = _element("NEW-1", new_text)
                    exact_result, modified_result = _match([old], [new])
                    self.assertEqual(exact_result["matches"], [])
                    for group in ("modified_matches", "review_pairs", "duplicate_exact_review_pairs"):
                        self.assertEqual(modified_result[group], [])
                    self.assertNotEqual(element_key(old), element_key(new))
                    pairs, _, _ = reconcile_residuals([{"old": old}], [{"new": new}])
                    self.assertEqual(pairs, [])

    def test_duplicate_family_matches_still_use_shared_anchor_context(self):
        def with_context(element, key):
            return {**element, "provisional_anchor_context": {"recent_anchor_keys": [key]}}

        olds = [
            with_context(_element("OLD-1", "Voraussetzungen", "heading"), "heading:first"),
            with_context(_element("OLD-2", "Voraussetzungen", order=2), "heading:second"),
        ]
        news = [
            with_context(_element("NEW-1", "Voraussetzungen"), "heading:second"),
            with_context(_element("NEW-2", "Voraussetzungen", order=2), "heading:first"),
        ]
        exact_result, _ = _match(olds, news)
        self.assertEqual(
            [(p["old"]["element_id"], p["new"]["element_id"]) for p in exact_result["matches"]],
            [("OLD-1", "NEW-2"), ("OLD-2", "NEW-1")],
        )


class FurnitureTest(unittest.TestCase):
    @staticmethod
    def _repeat(text="BaFin", *, count=10, y=3):
        return [_element(f"P-{page}", text, page=page, y=y, order=page) for page in range(1, count + 1)]

    def test_role_free_header_confirmed_by_other_document_is_excluded(self):
        elements = self._repeat() + [_element("P-11", "Body text", page=11, order=11)]
        document = _document(elements)
        derived = classify_derived_furniture(document)
        opposite_elements = self._repeat()
        for element in opposite_elements:
            element.update(element_type="header", original_role="pageHeader")
        opposite = classify_derived_furniture(_document(opposite_elements))
        evidence = build_structure_evidence(
            document, derived_furniture=confirm_furniture_exclusions(derived, opposite),
        )
        furniture = evidence["furniture"]
        self.assertTrue(furniture["by_element"]["P-1"]["exclusion_confirmed_by_other_document"])
        self.assertEqual(len(furniture["element_ids"]), 10)
        self.assertEqual(furniture["by_element"]["P-1"]["evidence_path"], "recurrence_edge_only")
        self.assertEqual(evidence["membership"]["by_element"]["P-1"]["category"], "layout")
        self.assertTrue(elements[0]["comparison_eligible"])
        self.assertEqual(elements[0]["element_type"], "paragraph")
        metadata, candidates, ignored, profile = exact.extract_candidates({
            "document": {**document, "structure_evidence": evidence},
        })
        self.assertEqual([item["element_id"] for item in candidates], ["P-11"])
        result = exact.build_result(metadata, {}, candidates, [], ignored, [], profile, {})
        self.assertEqual(result["statistics"]["ignored_old_derived_furniture"], 10)
        self.assertEqual(result["ignored_old_nonsemantic_margin_markers"], [])
        self.assertEqual(
            {item["element_id"] for item in result["ignored_old_derived_furniture"]},
            set(furniture["element_ids"]),
        )

    def test_changed_running_instructions_and_invalid_geometry_remain_comparable(self):
        texts = ("Keine Freigabe erforderlich", "Freigabe erforderlich")
        documents = [_document(self._repeat(text, count=5)) for text in texts]
        derived = [classify_derived_furniture(document) for document in documents]
        for index, document in enumerate(documents):
            furniture = confirm_furniture_exclusions(derived[index], derived[1 - index])
            evidence = build_structure_evidence(document, derived_furniture=furniture)
            self.assertFalse(furniture["by_element"]["P-1"]["exclusion_confirmed_by_other_document"])
            self.assertEqual(evidence["membership"]["by_element"]["P-1"]["category"], "preamble")
            _, candidates, ignored, _ = exact.extract_candidates({
                "document": {**document, "structure_evidence": evidence},
            })
            self.assertEqual(len(candidates), 5)
            self.assertEqual(ignored, [])

        for polygon in (
            [141, 3, 145, 3, 145, 5, 141, 5],
            [10, 143, 20, 143, 20, 145, 10, 145],
        ):
            invalid = self._repeat("Freigabe erforderlich", count=5)
            invalid[0]["primary_polygon"] = polygon
            self.assertEqual(classify_derived_furniture(_document(invalid))["element_ids"], [])

    def test_layout_role_cohort_can_still_qualify_on_three_pages(self):
        elements = self._repeat(count=3)
        for element in elements[:2]:
            element.update(element_type="header", original_role="pageHeader")
        furniture = classify_derived_furniture(_document(elements))
        self.assertEqual(
            {key: record["evidence_path"] for key, record in furniture["by_element"].items()},
            {f"P-{page}": "role_supported" for page in range(1, 4)},
        )

    def test_recurrence_requires_enough_distinct_physical_pages(self):
        self.assertEqual(classify_derived_furniture(_document(self._repeat(count=3)))["element_ids"], [])
        self.assertEqual(
            classify_derived_furniture(_document(self._repeat(count=5), page_count=20))["element_ids"], [],
        )

    def test_role_free_group_requires_one_occurrence_per_page(self):
        elements = self._repeat() + [_element("P-duplicate", "BaFin", page=2, y=4)]
        self.assertEqual(classify_derived_furniture(_document(elements))["element_ids"], [])

    def test_recurrence_requires_short_text_at_the_same_edge(self):
        groups = (
            self._repeat("Same body sentence.", y=50),
            self._repeat("This running footer is far too long to qualify.", y=95),
            self._repeat("One two three four five six", y=95),
            self._repeat(count=8) + [
                _element("P-9", "BaFin", page=9, y=95),
                _element("P-10", "BaFin", page=10, y=50),
            ],
        )
        for elements in groups:
            with self.subTest(text=elements[0]["text"], y=elements[0]["primary_polygon"][1]):
                self.assertEqual(classify_derived_furniture(_document(elements))["element_ids"], [])

    def test_footer_requires_ninety_percent_at_edge_and_leaves_body_occurrence(self):
        elements = self._repeat(count=9, y=95) + [_element("P-10", "BaFin", page=10)]
        furniture = classify_derived_furniture(_document(elements))
        self.assertEqual(set(furniture["element_ids"]), {f"P-{page}" for page in range(1, 10)})
        self.assertEqual(furniture["by_element"]["P-1"]["evidence"]["edge"], "bottom")

    def test_heading_role_on_any_occurrence_blocks_role_free_path(self):
        elements = self._repeat(count=9) + [_element("P-10", "BaFin", "heading", page=10)]
        self.assertEqual(classify_derived_furniture(_document(elements))["element_ids"], [])


class ListItemTypingTest(unittest.TestCase):
    def test_german_abbreviations_remain_paragraphs(self):
        for prefix in ("z. B.", "d. h.", "u. a.", "u. ä.", "s. o.", "s. u.", "i. S. d.",
                       "i. d. R.", "v. a.", "o. ä.", "z. T.", "m. E.", "ggf.", "bzw.", "vgl.", "usw."):
            for variant in (prefix, prefix.upper(), prefix.upper().replace(" ", "")):
                with self.subTest(prefix=variant):
                    self.assertEqual(self._kind(variant + " gelten diese Regeln."), "paragraph")

    def test_legitimate_or_ambiguous_labels_remain_list_items(self):
        for prefix in ("(i)", "i)", "a)", "a.", "b.", "A.", "1.", "1.2)", "-"):
            with self.subTest(prefix=prefix):
                self.assertEqual(self._kind(prefix + " Voraussetzungen"), "list_item")
        for text in ("i. weitere Regeln", "i. Voraussetzungen", "v. Vertrag nach Genehmigung"):
            self.assertEqual(self._kind(text), "list_item")

    @staticmethod
    def _kind(text):
        return schema.derive_paragraph_type(text, None, {"di_role_is_heading_like": False})
