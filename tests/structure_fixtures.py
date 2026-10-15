"""Explicitly authored synthetic structure fixtures for the T-02/T-03 layer.

These are hand-written, generic documents (no CRR/legal vocabulary, no real
PDF) used to pin the TOC-evidence behavior: text and table TOCs, multi-page
continuation, split title/page paragraphs, repeated titles, printed/physical
page mismatch, dotted levels, absent/partial TOCs and numeric data-table
negatives. They are *documents* (the ``document`` mapping consumed by
``build_structure_evidence``), not full pipeline models.

The committed JSON under ``test_data/fixtures_structure/`` is produced by
``write_all_fixtures()``; the tests load the committed files so the fixtures
stay inspectable and reproducible. These fixtures are synthetic authored
expectations, never ground truth derived from predictions.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "test_data" / "fixtures_structure"

PAGE_W = 8.5
PAGE_H = 11.0


def _polygon(page: int, top: float) -> list[float]:
    return [0.8, top, 7.7, top, 7.7, top + 0.3, 0.8, top + 0.3]


def _span(order: int) -> list[dict[str, int]]:
    return [{"offset": order * 40, "length": 30}]


def _heading_evidence(role: str | None, heading_like: bool) -> dict:
    return {
        "di_role": role,
        "di_role_is_heading_like": heading_like,
        "di_role_is_non_content": False,
        "lexical_signal": None,
        "status": "di_heading_evidence" if heading_like else "no_heading_evidence",
        "hierarchy_level": None,
        "hierarchy_level_known": False,
        "authoritative_structure_created": False,
    }


def paragraph(
    eid: str,
    text: str,
    *,
    order: int,
    page: int = 1,
    top: float = 1.0,
    role: str | None = None,
    element_type: str | None = None,
    eligible: bool = True,
    exclusion_reasons: list[str] | None = None,
    represented_by_table_id: str | None = None,
) -> dict:
    heading_like = role in {"title", "sectionHeading"} or element_type in {"heading", "title"}
    etype = element_type or ("heading" if heading_like else "paragraph")
    if role is None and heading_like:
        role = "sectionHeading"
    return {
        "element_id": eid,
        "source_type": "paragraph",
        "source_reference": {"collection": "paragraphs", "index": order},
        "element_type": etype,
        "text": text,
        "normalized_text": text,
        "original_role": role,
        "heading_evidence": _heading_evidence(role, heading_like),
        "spans": _span(order),
        "bounding_regions": [{"page_number": page, "polygon": _polygon(page, top)}],
        "primary_page": page,
        "primary_polygon": _polygon(page, top),
        "source_order": order,
        "reading_order": order,
        "section_path": [],
        "structure_heading": None,
        "structure_status": "not_inferred_in_step_3",
        "inside_table": represented_by_table_id is not None,
        "represented_by_table_id": represented_by_table_id,
        "comparison_eligible": eligible and not exclusion_reasons,
        "exclusion_reasons": list(exclusion_reasons or []),
        "extraction_validation": {
            "requires_extraction_review": False,
            "extraction_warning_flags": [],
            "extraction_review_flags": [],
        },
        "confidence": {"mean_word_confidence": None, "minimum_word_confidence": None, "word_count_with_confidence": 0},
        "table": None,
    }


def table_element(
    eid: str,
    rows: list[list[str]],
    *,
    order: int,
    page: int = 1,
    top: float = 1.0,
) -> dict:
    cells = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            cells.append({
                "cell_id": f"{eid}-C{len(cells) + 1:04}",
                "row_index": row_index,
                "column_index": column_index,
                "row_span": 1,
                "column_span": 1,
                "kind": "columnHeader" if row_index == 0 else "content",
                "text": value,
                "normalized_text": value,
                "spans": _span(order),
                "bounding_regions": [{"page_number": page, "polygon": _polygon(page, top)}],
                "primary_page": page,
                "primary_polygon": _polygon(page, top),
            })
    return {
        "element_id": eid,
        "source_type": "table",
        "source_reference": {"collection": "tables", "index": order},
        "element_type": "table",
        "text": "\n".join("\t".join(row) for row in rows),
        "normalized_text": "\n".join("\t".join(row) for row in rows),
        "original_role": None,
        "heading_evidence": None,
        "spans": _span(order),
        "bounding_regions": [{"page_number": page, "polygon": _polygon(page, top)}],
        "primary_page": page,
        "primary_polygon": _polygon(page, top),
        "source_order": order,
        "reading_order": order,
        "section_path": [],
        "structure_heading": None,
        "structure_status": "not_inferred_in_step_3",
        "inside_table": False,
        "represented_by_table_id": None,
        "comparison_eligible": True,
        "exclusion_reasons": [],
        "extraction_validation": {
            "requires_extraction_review": False,
            "extraction_warning_flags": [],
            "extraction_review_flags": [],
        },
        "confidence": {"mean_word_confidence": None, "minimum_word_confidence": None, "word_count_with_confidence": 0},
        "table": {
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "cells": cells,
        },
    }


def document(document_id: str, elements: list[dict], pages: int) -> dict:
    for index, element in enumerate(elements):
        element["reading_order"] = index
    return {
        "document_id": document_id,
        "page_count": pages,
        "pages": [
            {"page_number": n, "width": PAGE_W, "height": PAGE_H, "unit": "inch", "angle": 0}
            for n in range(1, pages + 1)
        ],
        "elements": elements,
    }


def _toc_text() -> dict:
    elements = [
        paragraph("P-00000", "Preface note for reviewers.", order=0),
        paragraph("P-00001", "Annual Report (synthetic)", order=1, role="title"),
        paragraph("P-00002", "Contents", order=1, role="sectionHeading"),
        paragraph("P-00003", "1. Introduction ......... 2", order=2),
        paragraph("P-00004", "2. Methods ......... 3", order=3),
        paragraph("P-00005", "2.1 Data ......... 3", order=4),
        paragraph("P-00006", "1. Introduction", order=5, page=2, role="sectionHeading"),
        paragraph("P-00007", "Intro prose body.", order=6, page=2),
        paragraph("P-00008", "2. Methods", order=7, page=3, role="sectionHeading"),
        paragraph("P-00009", "Methods prose body.", order=8, page=3),
        paragraph("P-00010", "2.1 Data", order=9, page=3, role="sectionHeading"),
        paragraph("P-00011", "Data prose body.", order=10, page=3),
    ]
    return document("DOC-TOC-TEXT", elements, 3)


def _toc_split_title_page() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "Introduction", order=1),
        paragraph("P-00003", "2", order=2),
        paragraph("P-00004", "Methods", order=3),
        paragraph("P-00005", "3", order=4),
        paragraph("P-00006", "Introduction", order=5, page=2, role="sectionHeading"),
        paragraph("P-00007", "Intro body.", order=6, page=2),
        paragraph("P-00008", "Methods", order=7, page=3, role="sectionHeading"),
        paragraph("P-00009", "Methods body.", order=8, page=3),
    ]
    return document("DOC-TOC-SPLIT", elements, 3)


def _toc_table() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        table_element("T-00001", [["Section", "Page"], ["Introduction", "2"], ["Methods", "3"]], order=1),
        # Overlapping paragraph cells from DI: must not duplicate the table entries.
        paragraph("P-00002", "Introduction", order=2, represented_by_table_id="T-00001",
                  exclusion_reasons=["duplicate_table_representation"]),
        paragraph("P-00003", "2", order=3, represented_by_table_id="T-00001",
                  exclusion_reasons=["duplicate_table_representation"]),
        paragraph("P-00004", "Introduction", order=4, page=2, role="sectionHeading"),
        paragraph("P-00005", "Intro body.", order=5, page=2),
        paragraph("P-00006", "Methods", order=6, page=3, role="sectionHeading"),
        paragraph("P-00007", "Methods body.", order=7, page=3),
    ]
    return document("DOC-TOC-TABLE", elements, 3)


def _toc_multipage() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "1. Introduction ......... 3", order=1),
        paragraph("P-00003", "2. Methods ......... 3", order=2),
        paragraph("P-00004", "3. Results ......... 4", order=3, page=2),
        paragraph("P-00005", "4. Discussion ......... 4", order=4, page=2),
        paragraph("P-00006", "1. Introduction", order=5, page=3, role="sectionHeading"),
        paragraph("P-00007", "Intro body.", order=6, page=3),
        paragraph("P-00008", "2. Methods", order=7, page=3, role="sectionHeading"),
        paragraph("P-00009", "Methods body.", order=8, page=3),
        paragraph("P-00010", "3. Results", order=9, page=4, role="sectionHeading"),
        paragraph("P-00011", "Results body.", order=10, page=4),
        paragraph("P-00012", "4. Discussion", order=11, page=4, role="sectionHeading"),
        paragraph("P-00013", "Discussion body.", order=12, page=4),
    ]
    return document("DOC-TOC-MULTIPAGE", elements, 4)


def _toc_repeated_titles() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "Overview ......... 2", order=1),
        paragraph("P-00003", "Appendix ......... 9", order=2),
        paragraph("P-00004", "Overview", order=3, page=2, role="sectionHeading"),
        paragraph("P-00005", "Overview", order=4, page=2, role="sectionHeading"),
        paragraph("P-00006", "Body.", order=5, page=2),
    ]
    return document("DOC-TOC-REPEATED", elements, 2)


def _toc_printed_page_mismatch() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "Introduction ......... 5", order=1),
        paragraph("P-00003", "Introduction", order=2, page=2, role="sectionHeading"),
        paragraph("P-00004", "Intro body.", order=3, page=2),
    ]
    return document("DOC-TOC-PAGEMISMATCH", elements, 2)


def _no_toc() -> dict:
    elements = [
        paragraph("P-00001", "1. Introduction", order=0, role="sectionHeading"),
        paragraph("P-00002", "Intro body.", order=1),
        paragraph("P-00003", "2. Methods", order=2, page=2, role="sectionHeading"),
        paragraph("P-00004", "Methods body.", order=3, page=2),
    ]
    return document("DOC-NO-TOC", elements, 2)


def _partial_toc() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "Introduction ......... 2", order=1),
        paragraph("P-00003", "Introduction", order=2, page=2, role="sectionHeading"),
        paragraph("P-00004", "Intro body.", order=3, page=2),
        paragraph("P-00005", "Methods", order=4, page=3, role="sectionHeading"),
        paragraph("P-00006", "Methods body.", order=5, page=3),
    ]
    return document("DOC-PARTIAL-TOC", elements, 3)


def _dotted_levels() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "1. Overview ......... 2", order=1),
        paragraph("P-00003", "1.1 Scope ......... 2", order=2),
        paragraph("P-00004", "1.1.1 Limits ......... 2", order=3),
        paragraph("P-00005", "1.2 Terms ......... 3", order=4),
        paragraph("P-00006", "2.1 Orphan ......... 3", order=5),
        paragraph("P-00007", "1. Overview", order=6, page=2, role="sectionHeading"),
        paragraph("P-00008", "1.1 Scope", order=7, page=2, role="sectionHeading"),
        paragraph("P-00009", "1.1.1 Limits", order=8, page=2, role="sectionHeading"),
        paragraph("P-00010", "1.2 Terms", order=9, page=3, role="sectionHeading"),
        paragraph("P-00011", "2.1 Orphan", order=10, page=3, role="sectionHeading"),
        paragraph("P-00012", "Body prose.", order=11, page=3),
    ]
    return document("DOC-DOTTED", elements, 3)


def _numeric_data_table_negative() -> dict:
    elements = [
        paragraph("P-00001", "Quarterly figures", order=0, role="title"),
        table_element("T-00001", [["Region", "2020", "2021"], ["North", "10", "12"], ["South", "8", "9"]], order=1),
        paragraph("P-00002", "1. Introduction", order=2, role="sectionHeading"),
        paragraph("P-00003", "Intro body.", order=3),
    ]
    return document("DOC-NUMERIC-TABLE", elements, 2)


def _changed_missing_headings() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "Introduction ......... 2", order=1),
        paragraph("P-00003", "Introduction and Scope", order=2, page=2, role="sectionHeading"),
        paragraph("P-00004", "Body.", order=3, page=2),
        paragraph("P-00005", "Methods", order=4, page=3, role="sectionHeading"),
        paragraph("P-00006", "Methods body.", order=5, page=3),
    ]
    return document("DOC-CHANGED-HEADINGS", elements, 3)


def _table_paragraph_no_duplicate() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        table_element("T-00001", [["Section", "Page"], ["Introduction", "2"], ["Methods", "3"]], order=1),
        paragraph("P-00002", "Introduction", order=2, represented_by_table_id="T-00001",
                  exclusion_reasons=["duplicate_table_representation"]),
        paragraph("P-00003", "2", order=3, represented_by_table_id="T-00001",
                  exclusion_reasons=["duplicate_table_representation"]),
        paragraph("P-00004", "Introduction", order=4, page=2, role="sectionHeading"),
        paragraph("P-00005", "Methods", order=5, page=3, role="sectionHeading"),
    ]
    return document("DOC-TABLE-NODUP", elements, 3)


def _toc_table_heading_duplicate() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        table_element("T-00001", [["Section", "Page"], ["Methods", "3"]], order=1),
        paragraph("P-00002", "Methods", order=2, page=3, role="sectionHeading",
                  represented_by_table_id="T-00001",
                  exclusion_reasons=["duplicate_table_representation"]),
        paragraph("P-00003", "Methods", order=3, page=3, role="sectionHeading"),
        paragraph("P-00004", "Methods", order=4, page=4, role="sectionHeading"),
    ]
    return document("DOC-TOC-TABLE-DUP", elements, 3)


def _numeric_data_table_one_heading() -> dict:
    elements = [
        paragraph("P-00001", "Quarterly figures", order=0, role="title"),
        table_element(
            "T-00001",
            [["Section", "Page"], ["Introduction", "10"], ["North", "12"], ["South", "8"]],
            order=1,
        ),
        paragraph("P-00002", "1. Introduction", order=2, role="sectionHeading"),
        paragraph("P-00003", "Intro body.", order=3),
    ]
    return document("DOC-NUMERIC-ONE-HEADING", elements, 2)


def _dotted_duplicate_parent() -> dict:
    elements = [
        paragraph("P-00001", "Contents", order=0, role="sectionHeading"),
        paragraph("P-00002", "1. Alpha ......... 2", order=1),
        paragraph("P-00003", "1.1 Beta ......... 2", order=2),
        paragraph("P-00004", "1. Gamma ......... 3", order=3),
        paragraph("P-00005", "1.2 Delta ......... 3", order=4),
        paragraph("P-00006", "1. Alpha", order=5, page=2, role="sectionHeading"),
        paragraph("P-00007", "1.1 Beta", order=6, page=2, role="sectionHeading"),
        paragraph("P-00008", "1. Gamma", order=7, page=3, role="sectionHeading"),
        paragraph("P-00009", "1.2 Delta", order=8, page=3, role="sectionHeading"),
    ]
    return document("DOC-DUP-PARENT", elements, 3)


BUILDERS = {
    "toc_text": _toc_text,
    "toc_split_title_page": _toc_split_title_page,
    "toc_table": _toc_table,
    "toc_multipage": _toc_multipage,
    "toc_repeated_titles": _toc_repeated_titles,
    "toc_printed_page_mismatch": _toc_printed_page_mismatch,
    "no_toc": _no_toc,
    "partial_toc": _partial_toc,
    "dotted_levels": _dotted_levels,
    "numeric_data_table_negative": _numeric_data_table_negative,
    "changed_missing_headings": _changed_missing_headings,
    "table_paragraph_no_duplicate": _table_paragraph_no_duplicate,
    "toc_table_heading_duplicate": _toc_table_heading_duplicate,
    "numeric_data_table_one_heading": _numeric_data_table_one_heading,
    "dotted_duplicate_parent": _dotted_duplicate_parent,
}


def build(name: str) -> dict:
    return BUILDERS[name]()


def load(name: str) -> dict:
    path = FIXTURE_ROOT / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_all_fixtures() -> list[Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    written = []
    for name in BUILDERS:
        path = FIXTURE_ROOT / f"{name}.json"
        path.write_text(
            json.dumps(build(name), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_all_fixtures():
        print("wrote", written_path)
