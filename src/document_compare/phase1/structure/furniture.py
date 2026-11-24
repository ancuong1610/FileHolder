"""Generic, derived document-furniture classification (T-03).

Repeated page furniture (running headers/footers) can be supported by a layout
role cohort and shared page-edge geometry. A second path uses short text with
widespread recurrence, at most once per physical page, and a dominant edge
across all occurrences. A heading role vetoes the second path. Both paths
preserve the original roles, types, text, spans, polygons and IDs.

The classifier is pure: it reads its input mapping only, uses the standard
library only, and is shared by the body-section producer
(``document_compare.phase1.structure.reconstruction``) and the provisional-anchor producer
(``document_compare.phase1.stages.s05_anchors``).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from math import isfinite
from typing import Any

from document_compare.phase1.contracts.validation import require

DERIVED_FURNITURE_VERSION = "1.0.0"

# Layout element types already owned by the neutral model.
LAYOUT_ELEMENT_TYPES = {"header", "footer", "page_number"}

# Generic thresholds justified by the supplied-evidence audit (recurring
# top-of-page furniture spans many pages with a dominant layout-role cohort at
# a stable page edge). They are lower bounds, not sample page numbers.
FURNITURE_MIN_OCCURRENCES = 3
FURNITURE_MIN_DISTINCT_PAGES = 3
FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES = 2
FURNITURE_MIN_SUPPORTING_ROLE_PAGES = 2
FURNITURE_SUPPORTING_ROLE_MIN_SHARE = 0.5
FURNITURE_EDGE_BAND_RATIO = 0.2
FURNITURE_EDGE_MIN_SHARE = 0.9
FURNITURE_GEOMETRY_MIN_SHARE = 0.5
RECURRENCE_EDGE_MIN_PAGES = 5
RECURRENCE_EDGE_MIN_PAGE_SHARE = 0.30
RECURRENCE_EDGE_MAX_CHARACTERS = 40
RECURRENCE_EDGE_MAX_WORDS = 5
HEADING_ROLES = {"title", "sectionHeading"}

WHITESPACE = re.compile(r"\s+")
KEY_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
})


def furniture_key_text(text: str) -> str:
    """Canonical normalized text used to group recurring occurrences."""
    value = unicodedata.normalize("NFC", str(text))
    value = value.translate(KEY_TRANSLATION)
    value = WHITESPACE.sub(" ", value)
    return value.strip().casefold()


def _element_text(element: dict[str, Any]) -> str:
    value = element.get("normalized_text") or element.get("text") or ""
    return str(value).strip()


def _element_id(element: dict[str, Any]) -> str:
    return str(element.get("element_id"))


def _polygon_bounds(polygon: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(polygon, list) or len(polygon) < 8 or len(polygon) % 2:
        return None
    xs, ys = polygon[0::2], polygon[1::2]
    if not all(isinstance(value, (int, float)) for value in xs + ys):
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _page_size_map(document: dict[str, Any]) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    for page in document.get("pages") or []:
        if not isinstance(page, dict):
            continue
        number = page.get("page_number")
        width = page.get("width")
        height = page.get("height")
        if isinstance(number, int) and isinstance(width, (int, float)) and isinstance(height, (int, float)):
            if width > 0 and height > 0:
                sizes[number] = (float(width), float(height))
    return sizes


def _edge_band(
    element: dict[str, Any],
    sizes: dict[int, tuple[float, float]],
    *,
    validate_frame: bool = False,
) -> str | None:
    """Return ``"top"``/``"bottom"`` when the element sits at a page edge.

    Both coordinate orientations are tolerated: an element is at the top edge
    when its smallest y is near 0, and at the bottom edge when its largest y is
    near the page height. The band is derived per occurrence, so a document
    whose origin is not top-left is still classified from consistent geometry.
    """
    bounds = _polygon_bounds(element.get("primary_polygon"))
    size = sizes.get(element.get("primary_page"))
    if bounds is None or size is None:
        return None
    width, height = size
    min_x, min_y, max_x, max_y = bounds
    if validate_frame:
        if not all(isfinite(value) for value in element["primary_polygon"]):
            return None
        if not (0 <= min_x <= max_x and 0 <= min_y <= max_y):
            return None
        if max_x > width or max_y > height:
            if max_x > height or max_y > width:
                return None
            height = width
    if min_y / height <= FURNITURE_EDGE_BAND_RATIO:
        return "top"
    if (height - max_y) / height <= FURNITURE_EDGE_BAND_RATIO:
        return "bottom"
    return None


def _record(
    element: dict[str, Any],
    *,
    key: str,
    edge: str,
    evidence_path: str,
    occurrence_count: int,
    page_count: int,
    layout_count: int,
    layout_page_count: int,
    geometry_count: int,
    dominant_count: int,
) -> dict[str, Any]:
    return {
        "classification": "furniture",
        "derived": True,
        "evidence_path": evidence_path,
        **({"exclusion_confirmed_by_other_document": False}
           if evidence_path == "recurrence_edge_only" else {}),
        "reason": (
            "recurring_text_with_supporting_layout_role_cohort_and_page_edge_geometry"
            if evidence_path == "role_supported"
            else "short_text_recurring_once_per_page_at_shared_page_edge"
        ),
        "group_key": key,
        "evidence": {
            "occurrence_count": occurrence_count,
            "distinct_page_count": page_count,
            "supporting_role_occurrence_count": layout_count,
            "supporting_role_page_count": layout_page_count,
            "supporting_role_share": round(layout_count / occurrence_count, 4),
            "geometry_occurrence_count": geometry_count,
            "edge": edge,
            "edge_share_of_geometry": round(dominant_count / geometry_count, 4),
            "edge_share_of_occurrences": round(dominant_count / occurrence_count, 4),
            "element_edge": edge,
            "element_role": element.get("original_role"),
            "element_type": element.get("element_type"),
        },
    }


def classify_derived_furniture(document: dict[str, Any]) -> dict[str, Any]:
    """Derive furniture element classifications from the neutral document.

    Pure: only reads ``document``. Returns a versioned, self-contained mapping
    with per-element ``reason``/``evidence`` for every derived furniture
    element. Elements without sufficient combined evidence are absent, i.e.
    left to the conservative body-heading producers.
    """
    require(isinstance(document, dict), "derived furniture: document must be a mapping")
    elements = document.get("elements")
    require(
        isinstance(elements, list) and elements,
        "derived furniture: document.elements must be a non-empty list",
    )

    sizes = _page_size_map(document)
    physical_pages = {
        page["page_number"] for page in document.get("pages") or []
        if isinstance(page, dict) and isinstance(page.get("page_number"), int)
    }

    groups: dict[str, list[dict[str, Any]]] = {}
    for element in elements:
        if element.get("element_type") == "table":
            continue
        text = _element_text(element)
        if not text:
            continue
        groups.setdefault(furniture_key_text(text), []).append(element)

    by_element: dict[str, dict[str, Any]] = {}
    furniture_group_count = 0

    for key in sorted(groups):
        occurrences = groups[key]
        occurrence_count = len(occurrences)
        pages = {
            element.get("primary_page")
            for element in occurrences
            if isinstance(element.get("primary_page"), int)
        }
        if (
            occurrence_count < FURNITURE_MIN_OCCURRENCES
            or len(pages) < FURNITURE_MIN_DISTINCT_PAGES
        ):
            continue

        layout_occurrences = [
            element
            for element in occurrences
            if element.get("element_type") in LAYOUT_ELEMENT_TYPES
        ]
        layout_pages = {
            element.get("primary_page")
            for element in layout_occurrences
            if isinstance(element.get("primary_page"), int)
        }
        geometry = [
            (element, band)
            for element in occurrences
            if (band := _edge_band(element, sizes)) is not None
        ]
        bands = Counter(band for _, band in geometry)
        dominant_band, dominant_count = next(iter(bands.most_common(1)), (None, 0))
        layout_edge = [
            element
            for element, band in geometry
            if band == dominant_band and element.get("element_type") in LAYOUT_ELEMENT_TYPES
        ]
        layout_edge_pages = {
            element.get("primary_page")
            for element in layout_edge
            if isinstance(element.get("primary_page"), int)
        }
        edge_share = dominant_count / len(geometry) if geometry else 0
        role_supported = (
            len(geometry) >= FURNITURE_MIN_OCCURRENCES
            and len(layout_occurrences) >= FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES
            and len(layout_pages) >= FURNITURE_MIN_SUPPORTING_ROLE_PAGES
            and len(layout_occurrences) / occurrence_count >= FURNITURE_SUPPORTING_ROLE_MIN_SHARE
            and len(geometry) / occurrence_count >= FURNITURE_GEOMETRY_MIN_SHARE
            and edge_share >= FURNITURE_EDGE_MIN_SHARE
            and len(layout_edge) >= FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES
            and len(layout_edge_pages) >= FURNITURE_MIN_SUPPORTING_ROLE_PAGES
        )
        if role_supported:
            evidence_path = "role_supported"
        elif (
            len(key) <= RECURRENCE_EDGE_MAX_CHARACTERS
            and len(key.split()) <= RECURRENCE_EDGE_MAX_WORDS
            and len(pages) >= max(RECURRENCE_EDGE_MIN_PAGES, RECURRENCE_EDGE_MIN_PAGE_SHARE * len(physical_pages))
            and len(pages) == occurrence_count
            and pages <= physical_pages
            and not any(element.get("original_role") in HEADING_ROLES for element in occurrences)
        ):
            geometry = [
                (element, band)
                for element in occurrences
                if (band := _edge_band(element, sizes, validate_frame=True)) is not None
            ]
            if not geometry:
                continue
            dominant_band, dominant_count = Counter(band for _, band in geometry).most_common(1)[0]
            if dominant_count / occurrence_count < FURNITURE_EDGE_MIN_SHARE:
                continue
            evidence_path = "recurrence_edge_only"
        else:
            continue

        furniture_group_count += 1
        for element, band in geometry:
            if band != dominant_band:
                # A same-text occurrence outside the shared edge band is not
                # furniture; it stays available to the body producers.
                continue
            by_element[_element_id(element)] = _record(
                element,
                key=key,
                edge=band,
                evidence_path=evidence_path,
                occurrence_count=occurrence_count,
                page_count=len(pages),
                layout_count=len(layout_occurrences),
                layout_page_count=len(layout_pages),
                geometry_count=len(geometry),
                dominant_count=dominant_count,
            )

    return {
        "version": DERIVED_FURNITURE_VERSION,
        "element_ids": sorted(by_element),
        "by_element": by_element,
        "profile": {
            "version": DERIVED_FURNITURE_VERSION,
            "group_count": len(groups),
            "furniture_group_count": furniture_group_count,
            "furniture_element_count": len(by_element),
            "min_occurrences": FURNITURE_MIN_OCCURRENCES,
            "min_distinct_pages": FURNITURE_MIN_DISTINCT_PAGES,
            "min_supporting_role_occurrences": FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES,
            "min_supporting_role_pages": FURNITURE_MIN_SUPPORTING_ROLE_PAGES,
            "supporting_role_min_share": FURNITURE_SUPPORTING_ROLE_MIN_SHARE,
            "edge_band_ratio": FURNITURE_EDGE_BAND_RATIO,
            "edge_min_share": FURNITURE_EDGE_MIN_SHARE,
            "recurrence_edge_min_pages": RECURRENCE_EDGE_MIN_PAGES,
            "recurrence_edge_min_page_share": RECURRENCE_EDGE_MIN_PAGE_SHARE,
            "recurrence_edge_max_characters": RECURRENCE_EDGE_MAX_CHARACTERS,
            "recurrence_edge_max_words": RECURRENCE_EDGE_MAX_WORDS,
        },
        "limitations": [
            "derived_furniture_is_a_hypothesis_not_an_authoritative_role",
            "requires_role_supported_or_recurrence_edge_only_evidence",
            "same_text_body_occurrences_outside_the_shared_edge_band_are_not_classified",
            "raw_roles_types_text_spans_and_ids_are_preserved_unchanged",
        ],
    }


def confirm_furniture_exclusions(
    furniture: dict[str, Any], opposite: dict[str, Any],
) -> dict[str, Any]:
    """Confirm recurrence-only exclusions against derived furniture on the other side."""
    opposite_keys = {
        record["group_key"] for record in (opposite.get("by_element") or {}).values()
        if record.get("group_key")
    }
    by_element = {
        element_id: (
            {**record, "exclusion_confirmed_by_other_document": record.get("group_key") in opposite_keys}
            if record.get("evidence_path") == "recurrence_edge_only" else record
        )
        for element_id, record in (furniture.get("by_element") or {}).items()
    }
    return {**furniture, "by_element": by_element}


def furniture_exclusion_ids(furniture: dict[str, Any]) -> set[str]:
    """Only confirmed recurrence-only evidence is eligible for layout exclusion."""
    records = furniture.get("by_element") or {}
    return {
        element_id for element_id in furniture.get("element_ids") or []
        if records.get(element_id, {}).get("evidence_path") != "recurrence_edge_only"
        or records[element_id].get("exclusion_confirmed_by_other_document") is True
    }
