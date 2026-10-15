"""Generic, derived document-furniture classification (T-03).

Repeated page furniture (running headers/footers) is recognised from three
independent signals that already exist in the neutral model, so production
never trusts a single DI role:

* recurrence of the same normalized text on several physical pages,
* a supporting cohort of layout-role occurrences (``header``/``footer``/
  ``page_number``) on several pages, and
* page-edge geometry shared by those occurrences.

Only the combined evidence derives a furniture classification. Original roles,
types, text, spans, polygons and IDs are never modified; insufficient evidence
leaves an element unclassified so a legitimate repeated body heading survives.
No CRR/legal vocabulary, sample page numbers or document-specific rules are
used.

The classifier is pure: it reads its input mapping only, uses the standard
library only, and is shared by the body-section producer
(``crr_compare.structure``) and the provisional-anchor producer
(``crr_compare.stages.s05_anchors``).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from crr_compare.validation import require

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
    _, height = size
    _, min_y, _, max_y = bounds
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
    occurrence_count: int,
    page_count: int,
    layout_count: int,
    layout_page_count: int,
    geometry_count: int,
    edge_share: float,
) -> dict[str, Any]:
    return {
        "classification": "furniture",
        "derived": True,
        "reason": "recurring_text_with_supporting_layout_role_cohort_and_page_edge_geometry",
        "group_key": key,
        "evidence": {
            "occurrence_count": occurrence_count,
            "distinct_page_count": page_count,
            "supporting_role_occurrence_count": layout_count,
            "supporting_role_page_count": layout_page_count,
            "supporting_role_share": round(layout_count / occurrence_count, 4),
            "geometry_occurrence_count": geometry_count,
            "edge": edge,
            "edge_share_of_geometry": round(edge_share, 4),
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
        if (
            len(layout_occurrences) < FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES
            or len(layout_pages) < FURNITURE_MIN_SUPPORTING_ROLE_PAGES
        ):
            continue
        if (
            len(layout_occurrences) / occurrence_count
            < FURNITURE_SUPPORTING_ROLE_MIN_SHARE
        ):
            continue

        geometry = [
            (element, band)
            for element in occurrences
            if (band := _edge_band(element, sizes)) is not None
        ]
        if len(geometry) < FURNITURE_MIN_OCCURRENCES:
            continue
        if len(geometry) / occurrence_count < FURNITURE_GEOMETRY_MIN_SHARE:
            continue

        bands = Counter(band for _, band in geometry)
        dominant_band, dominant_count = bands.most_common(1)[0]
        if dominant_count / len(geometry) < FURNITURE_EDGE_MIN_SHARE:
            continue

        layout_edge = [
            element
            for element in layout_occurrences
            if _edge_band(element, sizes) == dominant_band
        ]
        layout_edge_pages = {
            element.get("primary_page")
            for element in layout_edge
            if isinstance(element.get("primary_page"), int)
        }
        if (
            len(layout_edge) < FURNITURE_MIN_SUPPORTING_ROLE_OCCURRENCES
            or len(layout_edge_pages) < FURNITURE_MIN_SUPPORTING_ROLE_PAGES
        ):
            continue

        furniture_group_count += 1
        edge_share = dominant_count / len(geometry)
        for element, band in geometry:
            if band != dominant_band:
                # A same-text occurrence outside the shared edge band is not
                # furniture; it stays available to the body producers.
                continue
            by_element[_element_id(element)] = _record(
                element,
                key=key,
                edge=band,
                occurrence_count=occurrence_count,
                page_count=len(pages),
                layout_count=len(layout_occurrences),
                layout_page_count=len(layout_pages),
                geometry_count=len(geometry),
                edge_share=edge_share,
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
        },
        "limitations": [
            "derived_furniture_is_a_hypothesis_not_an_authoritative_role",
            "requires_recurrence_supporting_layout_role_and_page_edge_geometry_together",
            "same_text_body_occurrences_outside_the_shared_edge_band_are_not_classified",
            "raw_roles_types_text_spans_and_ids_are_preserved_unchanged",
        ],
    }
