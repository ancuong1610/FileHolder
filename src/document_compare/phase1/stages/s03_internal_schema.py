"""Stage 03: build the neutral internal document model for both sides.

Input  : ``<root>/output/old_di_raw.json`` and ``new_di_raw.json`` (Step-1
         DI results) plus ``old_extraction_validation.json`` and
         ``new_extraction_validation.json`` (Step-2 validation reports;
         required unless ``REQUIRE_VALIDATION_REPORTS`` is disabled).

Work   : Copies each paragraph/table into a neutral element model with a
         stable ``element_id``, preserves raw text, spans, bounding regions,
         confidence, and Step-2 extraction validation evidence, and records
         heading/role evidence without inferring any hierarchy.

Output : ``<root>/output/old_document.json`` and ``new_document.json`` plus
         console summaries.

Review : No review gate; Step-2 ``requires_review`` evidence is copied into
         ``extraction_validation`` per element/page and consumed by later
         stages. Duplicate IDs, page-count mismatch, and drifted source
         evidence are rejected by :func:`validate_internal_document`.

The module is import-safe: importing it only defines the pure schema helpers
and never resolves a workspace, reads files, prints, or mutates the
environment. File orchestration lives in :func:`main`, which the
``__name__ == "__main__"`` guard runs for the CLI stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.contracts.validation import require

# ---- imports and configuration constants ----

import json
import re
from bisect import bisect_left
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

SCHEMA_VERSION = "0.2.0"
STEP3_VERSION = "3.0.0"
REQUIRE_VALIDATION_REPORTS = True
TABLE_PARAGRAPH_OVERLAP_THRESHOLD = 0.50


# ---- workspace configuration ----
def document_config(project_root: Path) -> dict[str, dict[str, Any]]:
    """Per-document identity and input/output paths for one workspace root."""
    output_dir = project_root / "output"
    return {
        "old": {
            "document_id": "DOC-OLD",
            "prefix": "OLD",
            "raw": output_dir / "old_di_raw.json",
            "validation": output_dir / "old_extraction_validation.json",
            "result": output_dir / "old_document.json",
        },
        "new": {
            "document_id": "DOC-NEW",
            "prefix": "NEW",
            "raw": output_dir / "new_di_raw.json",
            "validation": output_dir / "new_extraction_validation.json",
            "result": output_dir / "new_document.json",
        },
    }


# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    temp_path.replace(path)


# ---- source field copying helpers ----
def value_of(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def copy_spans(item: dict[str, Any]) -> list[dict[str, int]]:
    copied: list[dict[str, int]] = []
    for span in value_of(item, "spans", default=[]) or []:
        offset = value_of(span, "offset")
        length = value_of(span, "length")
        if offset is not None and length is not None:
            copied.append({"offset": int(offset), "length": int(length)})
    return copied


def copy_bounding_regions(item: dict[str, Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for region in value_of(item, "boundingRegions", "bounding_regions", default=[]) or []:
        regions.append({
            "page_number": value_of(region, "pageNumber", "page_number"),
            "polygon": list(value_of(region, "polygon", default=[]) or []),
        })
    return regions


def primary_page(regions: list[dict[str, Any]]) -> int | None:
    page = regions[0].get("page_number") if regions else None
    return int(page) if isinstance(page, (int, float)) else None


def primary_polygon(regions: list[dict[str, Any]]) -> list[float] | None:
    return regions[0].get("polygon") if regions else None


def element_page_numbers(regions: list[dict[str, Any]]) -> list[int]:
    return sorted({int(region["page_number"]) for region in regions if region.get("page_number") is not None})


def first_span_offset(spans: list[dict[str, int]]) -> int | None:
    return min((span["offset"] for span in spans), default=None)


def polygon_bounds(polygon: list[float] | None) -> tuple[float, float, float, float] | None:
    if not polygon or len(polygon) < 8 or len(polygon) % 2:
        return None
    xs, ys = polygon[0::2], polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def overlap_ratio(subject: list[float] | None, container: list[float] | None) -> float:
    subject_bounds, container_bounds = polygon_bounds(subject), polygon_bounds(container)
    if subject_bounds is None or container_bounds is None:
        return 0.0
    ax1, ay1, ax2, ay2 = subject_bounds
    bx1, by1, bx2, by2 = container_bounds
    width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    height = max(0.0, min(ay2, by2) - max(ay1, by1))
    subject_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    return (width * height / subject_area) if subject_area else 0.0


# ---- confidence and extraction-validation evidence ----
def build_page_validation_map(report: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not report:
        return {}
    native = report.get("native_text_validation") or {}
    result: dict[int, dict[str, Any]] = {}
    for page in native.get("pages") or []:
        page_number = value_of(page, "page", "page_number", "pageNumber")
        if page_number is None:
            continue
        result[int(page_number)] = {
            "warning_flags": list(page.get("warning_flags") or []),
            "review_flags": list(page.get("review_flags") or []),
            "requires_review": bool(page.get("requires_review")),
            "native_text_coverage": page.get("native_text_coverage"),
            "di_text_coverage": page.get("di_text_coverage"),
            "di_token_deficit_ratio": page.get("di_token_deficit_ratio"),
        }
    return result


def validation_for_pages(page_numbers: Iterable[int], page_validation: dict[int, dict[str, Any]]) -> dict[str, Any]:
    records = [page_validation[page] for page in page_numbers if page in page_validation]
    return {
        "requires_extraction_review": any(record["requires_review"] for record in records),
        "extraction_warning_flags": sorted({flag for record in records for flag in record["warning_flags"]}),
        "extraction_review_flags": sorted({flag for record in records for flag in record["review_flags"]}),
    }


def build_word_index(raw: dict[str, Any]) -> tuple[list[int], list[dict[str, Any]]]:
    indexed: list[dict[str, Any]] = []
    for page in raw.get("pages") or []:
        page_number = value_of(page, "pageNumber", "page_number")
        for source_index, word in enumerate(page.get("words") or []):
            span = word.get("span") or {}
            offset, length = span.get("offset"), span.get("length")
            if offset is None or length is None:
                continue
            indexed.append({
                "offset": int(offset),
                "length": int(length),
                "confidence": word.get("confidence"),
                "page_number": page_number,
                "source_index": source_index,
            })
    indexed.sort(key=lambda word: (word["offset"], word["length"]))
    return [word["offset"] for word in indexed], indexed


def confidence_for_spans(spans: list[dict[str, int]], word_offsets: list[int], words: list[dict[str, Any]]) -> dict[str, Any]:
    matched: dict[tuple[int, int], float] = {}
    for span in spans:
        start, end = span["offset"], span["offset"] + span["length"]
        index = bisect_left(word_offsets, start)
        while index < len(words) and words[index]["offset"] < end:
            word = words[index]
            word_end = word["offset"] + word["length"]
            if word["offset"] >= start and word_end <= end and word["confidence"] is not None:
                matched[(word["offset"], word["length"])] = float(word["confidence"])
            index += 1
    values = list(matched.values())
    return {
        "mean_word_confidence": mean(values) if values else None,
        "minimum_word_confidence": min(values) if values else None,
        "word_count_with_confidence": len(values),
    }


# ---- heading and paragraph-type evidence ----
PAGE_NUMBER_PATTERN = re.compile(r"^\s*(?:page|seite)\s+\d+\s*$", re.IGNORECASE)
LIST_ITEM_PATTERN = re.compile(
    r"^\s*(?:[•‣▪◦-]|\(\d+[a-z]?\)|\([a-z]\)|\d+(?:\.\d+)*[.)]|[a-z][.)])\s+",
    re.IGNORECASE,
)
HEADING_LIKE_ROLES = {"title", "sectionHeading"}
NON_CONTENT_ROLES = {"pageHeader", "pageFooter", "pageNumber", "footnote"}

# This is intentionally generic. It records a possible numbered label but does
# not claim its semantic kind, hierarchy level, or parent. Examples include
# "Article 4", "Kapitel 2", "Section 3.1", and any future document language.
STANDALONE_NUMBERED_LABEL = re.compile(
    r"^\s*(?P<prefix>[^\d\W_][^\d]*?)\s+"
    r"(?P<identifier>(?:\d+[A-Za-z]*|[IVXLCDM]+)(?:[.\-]\d+[A-Za-z]*)*)\s*$",
    re.IGNORECASE,
)


def compact_text(text: str) -> str:
    return " ".join(text.split())


def lexical_heading_signal(text: str) -> dict[str, Any] | None:
    compact = compact_text(text)
    match = STANDALONE_NUMBERED_LABEL.fullmatch(compact)
    if not match:
        return None
    return {
        "signal_type": "standalone_numbered_label",
        "prefix": match.group("prefix").strip(),
        "identifier": match.group("identifier"),
    }


def heading_evidence(text: str, role: str | None) -> dict[str, Any]:
    lexical_signal = lexical_heading_signal(text)
    role_is_heading_like = role in HEADING_LIKE_ROLES
    role_is_non_content = role in NON_CONTENT_ROLES

    if role_is_non_content and lexical_signal is not None:
        status = "role_lexical_conflict"
    elif role_is_non_content:
        status = "non_content"
    elif role_is_heading_like and lexical_signal is not None:
        status = "di_and_lexical_heading_evidence"
    elif role_is_heading_like:
        status = "di_heading_evidence"
    elif lexical_signal is not None:
        status = "lexical_heading_candidate"
    else:
        status = "no_heading_evidence"

    return {
        "di_role": role,
        "di_role_is_heading_like": role_is_heading_like,
        "di_role_is_non_content": role_is_non_content,
        "lexical_signal": lexical_signal,
        "status": status,
        "hierarchy_level": None,
        "hierarchy_level_known": False,
        "authoritative_structure_created": False,
    }


def derive_paragraph_type(text: str, role: str | None, evidence: dict[str, Any]) -> str:
    if role == "pageHeader":
        return "header"
    if role == "pageFooter":
        return "footer"
    if role == "pageNumber" or PAGE_NUMBER_PATTERN.fullmatch(text.strip()):
        return "page_number"
    if role == "footnote":
        return "footnote"
    if evidence["di_role_is_heading_like"]:
        return "heading"
    if LIST_ITEM_PATTERN.match(text):
        return "list_item"
    return "paragraph"


# ---- element builders ----
def table_text(table: dict[str, Any]) -> str:
    row_count = int(value_of(table, "rowCount", "row_count", default=0) or 0)
    column_count = int(value_of(table, "columnCount", "column_count", default=0) or 0)
    grid = [["" for _ in range(column_count)] for _ in range(row_count)]
    for cell in table.get("cells") or []:
        row = value_of(cell, "rowIndex", "row_index")
        column = value_of(cell, "columnIndex", "column_index")
        if isinstance(row, int) and isinstance(column, int) and 0 <= row < row_count and 0 <= column < column_count:
            grid[row][column] = value_of(cell, "content", "text", default="") or ""
    return "\n".join("\t".join(value for value in row) for row in grid)


def build_table_element(table: dict[str, Any], *, table_index: int, prefix: str, page_validation: dict[int, dict[str, Any]], word_offsets: list[int], words: list[dict[str, Any]]) -> dict[str, Any]:
    element_id = f"{prefix}-T{table_index + 1:04}"
    spans, regions = copy_spans(table), copy_bounding_regions(table)
    cells = []
    for cell_index, cell in enumerate(table.get("cells") or []):
        cell_regions = copy_bounding_regions(cell)
        cells.append({
            "cell_id": f"{element_id}-C{cell_index + 1:04}",
            "row_index": value_of(cell, "rowIndex", "row_index"),
            "column_index": value_of(cell, "columnIndex", "column_index"),
            "row_span": value_of(cell, "rowSpan", "row_span", default=1),
            "column_span": value_of(cell, "columnSpan", "column_span", default=1),
            "kind": cell.get("kind"),
            "text": value_of(cell, "content", "text", default="") or "",
            "spans": copy_spans(cell),
            "bounding_regions": cell_regions,
            "primary_page": primary_page(cell_regions),
            "primary_polygon": primary_polygon(cell_regions),
        })
    return {
        "element_id": element_id,
        "source_type": "table",
        "source_reference": {"collection": "tables", "index": table_index},
        "element_type": "table",
        "text": table_text(table),
        "original_role": None,
        "heading_evidence": None,
        "spans": spans,
        "bounding_regions": regions,
        "primary_page": primary_page(regions),
        "primary_polygon": primary_polygon(regions),
        "source_order": table_index,
        "reading_order": None,
        "section_path": [],
        "structure_heading": None,
        "structure_status": "not_inferred_in_step_3",
        "inside_table": False,
        "represented_by_table_id": None,
        "comparison_eligible": True,
        "exclusion_reasons": [],
        "extraction_validation": validation_for_pages(element_page_numbers(regions), page_validation),
        "confidence": confidence_for_spans(spans, word_offsets, words),
        "table": {
            "row_count": value_of(table, "rowCount", "row_count"),
            "column_count": value_of(table, "columnCount", "column_count"),
            "cells": cells,
        },
    }


def find_overlapping_table_id(paragraph_regions: list[dict[str, Any]], table_elements: list[dict[str, Any]]) -> str | None:
    best_id, best_ratio = None, 0.0
    for paragraph_region in paragraph_regions:
        for table in table_elements:
            for table_region in table["bounding_regions"]:
                if table_region.get("page_number") != paragraph_region.get("page_number"):
                    continue
                ratio = overlap_ratio(paragraph_region.get("polygon"), table_region.get("polygon"))
                if ratio > best_ratio:
                    best_id, best_ratio = table["element_id"], ratio
    return best_id if best_ratio >= TABLE_PARAGRAPH_OVERLAP_THRESHOLD else None


def build_paragraph_element(paragraph: dict[str, Any], *, paragraph_index: int, prefix: str, table_elements: list[dict[str, Any]], page_validation: dict[int, dict[str, Any]], word_offsets: list[int], words: list[dict[str, Any]]) -> dict[str, Any]:
    spans, regions = copy_spans(paragraph), copy_bounding_regions(paragraph)
    text = value_of(paragraph, "content", "text", default="") or ""
    role = paragraph.get("role")
    evidence = heading_evidence(text, role)
    element_type = derive_paragraph_type(text, role, evidence)
    represented_by_table_id = find_overlapping_table_id(regions, table_elements)

    exclusion_reasons: list[str] = []
    if element_type == "header":
        exclusion_reasons.append("page_header")
    elif element_type == "footer":
        exclusion_reasons.append("page_footer")
    elif element_type == "page_number":
        exclusion_reasons.append("page_number")
    if represented_by_table_id:
        exclusion_reasons.append("duplicate_table_representation")

    return {
        "element_id": f"{prefix}-P{paragraph_index + 1:05}",
        "source_type": "paragraph",
        "source_reference": {"collection": "paragraphs", "index": paragraph_index},
        "element_type": element_type,
        "text": text,
        "original_role": role,
        "heading_evidence": evidence,
        "spans": spans,
        "bounding_regions": regions,
        "primary_page": primary_page(regions),
        "primary_polygon": primary_polygon(regions),
        "source_order": paragraph_index,
        "reading_order": None,
        "section_path": [],
        "structure_heading": None,
        "structure_status": "not_inferred_in_step_3",
        "inside_table": represented_by_table_id is not None,
        "represented_by_table_id": represented_by_table_id,
        "comparison_eligible": not exclusion_reasons,
        "exclusion_reasons": exclusion_reasons,
        "extraction_validation": validation_for_pages(element_page_numbers(regions), page_validation),
        "confidence": confidence_for_spans(spans, word_offsets, words),
        "table": None,
    }


def order_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_priority = {"table": 0, "paragraph": 1}
    elements.sort(key=lambda element: (
        first_span_offset(element["spans"]) if first_span_offset(element["spans"]) is not None else float("inf"),
        source_priority[element["source_type"]],
        element["source_order"],
    ))
    for reading_order, element in enumerate(elements):
        element["reading_order"] = reading_order
    return elements


def build_pages(raw: dict[str, Any], report: dict[str, Any] | None, page_validation: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    confidence_metrics = {
        int(item["page"]): item
        for item in ((report or {}).get("confidence_and_scale", {}).get("page_metrics") or [])
        if item.get("page") is not None
    }
    pages = []
    for page in raw.get("pages") or []:
        page_number = value_of(page, "pageNumber", "page_number")
        if page_number is None:
            raise ValueError("DI page is missing page_number.")
        page_number = int(page_number)
        pages.append({
            "page_number": page_number,
            "width": page.get("width"),
            "height": page.get("height"),
            "unit": page.get("unit"),
            "angle": page.get("angle"),
            "extraction_validation": page_validation.get(page_number, {
                "warning_flags": [], "review_flags": [], "requires_review": False,
                "native_text_coverage": None, "di_text_coverage": None, "di_token_deficit_ratio": None,
            }),
            "confidence_metrics": confidence_metrics.get(page_number, {}),
        })
    return pages


def compact_validation_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"available": False}
    native = report.get("native_text_validation") or {}
    review_pages = [int(page["page"]) for page in native.get("pages") or [] if page.get("page") is not None and page.get("requires_review")]
    integrity = report.get("page_integrity") or {}
    return {
        "available": True,
        "source_raw_file": report.get("source_raw_file"),
        "source_pdf_file": report.get("source_pdf_file"),
        "requires_review": bool(report.get("requires_review")),
        "document_flags": list(report.get("document_flags") or []),
        "thresholds": report.get("thresholds") or {},
        "pages_requiring_review_count": len(review_pages),
        "pages_requiring_review": review_pages,
        "page_integrity": {
            "sequential_from_one": integrity.get("sequential_from_one"),
            "duplicates": list(integrity.get("duplicates") or []),
            "missing": list(integrity.get("missing") or []),
        },
        "span_integrity": report.get("span_integrity") or {},
        "polygon_integrity": report.get("polygon_integrity") or {},
        "table_integrity": report.get("table_integrity") or {},
    }


def build_internal_document(raw: dict[str, Any], report: dict[str, Any] | None, *, document_id: str, prefix: str) -> dict[str, Any]:
    page_validation = build_page_validation_map(report)
    word_offsets, words = build_word_index(raw)
    table_elements = [
        build_table_element(table, table_index=index, prefix=prefix, page_validation=page_validation, word_offsets=word_offsets, words=words)
        for index, table in enumerate(raw.get("tables") or [])
    ]
    paragraph_elements = [
        build_paragraph_element(paragraph, paragraph_index=index, prefix=prefix, table_elements=table_elements, page_validation=page_validation, word_offsets=word_offsets, words=words)
        for index, paragraph in enumerate(raw.get("paragraphs") or [])
    ]
    elements = order_elements(table_elements + paragraph_elements)
    pages = build_pages(raw, report, page_validation)
    paragraph_evidence = [element["heading_evidence"] for element in paragraph_elements]

    statistics = {
        "pages": len(pages),
        "elements": len(elements),
        "paragraph_source_elements": len(paragraph_elements),
        "tables": len(table_elements),
        "table_cells": sum(len(element["table"]["cells"]) for element in table_elements),
        "element_types": dict(Counter(element["element_type"] for element in elements)),
        "comparison_eligible_elements": sum(element["comparison_eligible"] for element in elements),
        "comparison_excluded_elements": sum(not element["comparison_eligible"] for element in elements),
        "inside_table_paragraphs": sum(element["inside_table"] for element in paragraph_elements),
        "elements_requiring_extraction_review": sum(element["extraction_validation"]["requires_extraction_review"] for element in elements),
        "di_paragraph_roles": dict(Counter(element.get("original_role") or "unlabeled" for element in paragraph_elements)),
        "heading_evidence_statuses": dict(Counter(evidence["status"] for evidence in paragraph_evidence)),
        "heading_evidence_with_lexical_signal": sum(evidence["lexical_signal"] is not None for evidence in paragraph_evidence),
        "heading_role_lexical_conflicts": sum(evidence["status"] == "role_lexical_conflict" for evidence in paragraph_evidence),
        "authoritative_structure_headings": 0,
        "elements_with_provisional_section_path": 0,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "processing": {
            "internal_schema": {
                "version": STEP3_VERSION,
                "strategy": "neutral_di_evidence_model",
                "di_roles_preserved": True,
                "raw_text_preserved": True,
                "authoritative_hierarchy_created": False,
                "section_path_created": False,
                "lexical_signals_are_non_authoritative": True,
                "next_required_stage": "rework_step_5_provisional_structure_reconstruction",
            }
        },
        "document": {
            "document_id": document_id,
            "source": {
                "provider": "azure_document_intelligence",
                "api_version": value_of(raw, "apiVersion", "api_version"),
                "model_id": value_of(raw, "modelId", "model_id"),
                "string_index_type": value_of(raw, "stringIndexType", "string_index_type"),
            },
            "validation_summary": compact_validation_summary(report),
            "page_count": len(pages),
            "pages": pages,
            "elements": elements,
            "statistics": statistics,
        },
    }


# ---- internal-document validation ----
def validate_internal_document(model: dict[str, Any], raw: dict[str, Any], report: dict[str, Any] | None) -> None:
    document = model["document"]
    elements, pages = document["elements"], document["pages"]

    require(
        model["schema_version"] == SCHEMA_VERSION,
        "s03: internal schema_version mismatch",
    )
    require(
        document["page_count"] == len(raw.get("pages") or []) == len(pages),
        "s03: page_count does not match the raw and materialized pages",
    )
    require(
        len(elements) == len(raw.get("paragraphs") or []) + len(raw.get("tables") or []),
        "s03: element count does not match raw paragraphs + tables",
    )
    require(
        len({element["element_id"] for element in elements}) == len(elements),
        "s03: duplicate element_id in the internal document",
    )
    require(
        [element["reading_order"] for element in elements] == list(range(len(elements))),
        "s03: reading_order is not the complete ordered range",
    )
    require(
        all(element["structure_heading"] is None for element in elements),
        "s03: step 3 must not infer structure headings",
    )
    require(
        all(element["section_path"] == [] for element in elements),
        "s03: step 3 must not infer section paths",
    )
    require(
        all(element["structure_status"] == "not_inferred_in_step_3" for element in elements),
        "s03: unexpected structure_status value",
    )
    require(
        all(element["comparison_eligible"] == (not element["exclusion_reasons"]) for element in elements),
        "s03: comparison_eligible disagrees with exclusion_reasons",
    )
    require(
        all(not element["comparison_eligible"] for element in elements if element["inside_table"]),
        "s03: inside-table elements must not be comparison_eligible",
    )

    paragraph_elements = {element["source_reference"]["index"]: element for element in elements if element["source_type"] == "paragraph"}
    for index, paragraph in enumerate(raw.get("paragraphs") or []):
        element = paragraph_elements[index]
        if element["text"] != (value_of(paragraph, "content", "text", default="") or ""):
            raise ValueError(f"s03: paragraph {index} text drifted from raw source")
        if element["spans"] != copy_spans(paragraph):
            raise ValueError(f"s03: paragraph {index} spans drifted from raw source")
        if element["bounding_regions"] != copy_bounding_regions(paragraph):
            raise ValueError(f"s03: paragraph {index} bounding_regions drifted from raw source")
        evidence = element["heading_evidence"]
        if not isinstance(evidence, dict):
            raise ValueError(f"s03: paragraph {index} heading_evidence is not a mapping")
        if evidence["di_role_is_non_content"] and element["structure_heading"] is not None:
            raise ValueError(f"s03: paragraph {index} non-content role inferred a structure heading")

    expected_review_pages = {
        int(page["page"])
        for page in (((report or {}).get("native_text_validation") or {}).get("pages") or [])
        if page.get("page") is not None and page.get("requires_review")
    }
    actual_review_pages = {page["page_number"] for page in pages if page["extraction_validation"]["requires_review"]}
    if actual_review_pages != expected_review_pages:
        raise ValueError(
            "s03: page review flags do not match the raw validation evidence "
            f"(actual={sorted(actual_review_pages)}, expected={sorted(expected_review_pages)})"
        )


# ---- console summary ----
def print_document_summary(model: dict[str, Any]) -> None:
    document = model["document"]
    statistics = document["statistics"]
    print(f"Document ID: {document['document_id']}")
    print(f"Schema:      {model['schema_version']}")
    print(f"Step 3:      {model['processing']['internal_schema']['version']}")
    for key, value in statistics.items():
        print(f"{key:42}: {value}")

    print("\nFirst 20 heading-like evidence records:")
    shown = 0
    for element in document["elements"]:
        evidence = element.get("heading_evidence") or {}
        if evidence.get("status") not in {"di_heading_evidence", "di_and_lexical_heading_evidence", "lexical_heading_candidate", "role_lexical_conflict"}:
            continue
        print({
            "id": element["element_id"],
            "page": element["primary_page"],
            "element_type": element["element_type"],
            "role": element["original_role"],
            "evidence": evidence,
            "text": element["text"][:160],
        })
        shown += 1
        if shown == 20:
            break


# ---- stage entry point ----
def main() -> None:
    """Run Stage 03 against the workspace and save both internal documents."""
    project_root = find_project_root()
    documents = document_config(project_root)

    for label, paths in documents.items():
        if not paths["raw"].exists():
            raise FileNotFoundError(f"Missing Step-1 output for {label}: {paths['raw']}")
        if REQUIRE_VALIDATION_REPORTS and not paths["validation"].exists():
            raise FileNotFoundError(f"Missing Step-2 report for {label}: {paths['validation']}")

    print(f"Project root: {project_root}")
    for label, paths in documents.items():
        print(f"{label.upper()}: {paths['raw'].name} + {paths['validation'].name}")

    raw_documents = {label: load_json(paths["raw"]) for label, paths in documents.items()}
    validation_reports = {
        label: load_json(paths["validation"]) if paths["validation"].exists() else None
        for label, paths in documents.items()
    }

    print("Loaded Step-1 raw JSON and Step-2 validation metadata.")

    internal_documents = {
        label: build_internal_document(raw_documents[label], validation_reports[label], document_id=paths["document_id"], prefix=paths["prefix"])
        for label, paths in documents.items()
    }

    print("Built both neutral internal document models.")

    for label, model in internal_documents.items():
        validate_internal_document(model, raw_documents[label], validation_reports[label])
        print(f"{label.upper()}: neutral-schema acceptance checks passed.")

    for label, model in internal_documents.items():
        print("\n" + "=" * 80)
        print(label.upper())
        print_document_summary(model)

    for label, model in internal_documents.items():
        result_path = documents[label]["result"]
        save_json(model, result_path)
        if not result_path.exists():
            raise ValueError(f"s03: expected saved document at {result_path}")
        print(f"Saved: {result_path}")

    print("\nStep 3 v3 complete: neutral internal schema saved for both documents.")


if __name__ == "__main__":
    main()