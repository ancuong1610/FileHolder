"""Resolve a saved finding to pages and normalized polygons per side.

Read-only: reads the canonical record handed in plus the fixed
``old_document.json`` / ``new_document.json`` artifacts of one output folder.
Polygons are never invented; every degraded case is labelled ``page_only`` or
``unavailable``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from document_compare.phase2.reader.result_reader import ID_FIELDS, ReaderError

SIDES = ("old", "new")
_CONTAINERS = {"change": "changes", "table": "table_changes", "review": "review_queue"}
_MAX_DOC_BYTES = 256 * 1024 * 1024


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _load_document(output_dir: Path, side: str) -> dict[str, Any] | None:
    base = Path(output_dir).resolve()
    path = base / f"{side}_document.json"
    try:
        resolved = path.resolve()
        if resolved.parent != base or not resolved.is_file() or resolved.stat().st_size > _MAX_DOC_BYTES:
            return None
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    document = data.get("document") if isinstance(data, dict) else None
    return document if isinstance(document, dict) else None


class _Doc:
    """Indexed view of one side's document JSON."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self.ok = document is not None
        self.pages: dict[int, tuple[float, float]] = {}
        self.known_pages: set[int] = set()
        self.elements: dict[str, dict[str, Any]] = {}
        self.page_count: int | None = None
        self.element_list: list[dict[str, Any]] = []
        if document is None:
            return
        pages = document.get("pages")
        pages = pages if isinstance(pages, list) else []
        count = document.get("page_count")
        self.page_count = count if isinstance(count, int) and not isinstance(count, bool) else len(pages)
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("page_number"), int):
                continue
            number = page["page_number"]
            self.known_pages.add(number)
            width, height = page.get("width"), page.get("height")
            if _is_number(width) and _is_number(height) and width > 0 and height > 0:
                self.pages[number] = (float(width), float(height))
        elements = document.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if isinstance(element, dict):
                self.element_list.append(element)
                eid = element.get("element_id")
                if isinstance(eid, str) and eid not in self.elements:
                    self.elements[eid] = element

    def region(self, page: Any, polygon: Any) -> dict[str, Any] | None:
        """Normalize one polygon, or None when it cannot be done safely."""
        if not isinstance(page, int) or isinstance(page, bool) or page not in self.pages:
            return None
        if not isinstance(polygon, list) or len(polygon) != 8 or not all(_is_number(v) for v in polygon):
            return None
        width, height = self.pages[page]
        values = [
            round(min(1.0, max(0.0, polygon[i] / (width if i % 2 == 0 else height))), 6)
            for i in range(8)
        ]
        return {"page_number": page, "polygon": values}

    def element_regions(self, element: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        regions = element.get("bounding_regions")
        for item in regions if isinstance(regions, list) else []:
            if isinstance(item, dict):
                normalized = self.region(item.get("page_number"), item.get("polygon"))
                if normalized is not None:
                    out.append(normalized)
        return out


def _side(
    counterpart: str,
    location: str,
    doc: _Doc,
    primary_page: int | None = None,
    regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "counterpart": counterpart,
        "location": location,
        "page_count": doc.page_count,
        "primary_page": primary_page,
        "regions": regions or [],
    }


def _dedupe(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for region in regions:
        key = (region["page_number"], tuple(region["polygon"]))
        if key not in seen:
            seen.add(key)
            out.append(region)
    return out


def _page_of(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _present(side: Any, id_key: str) -> bool:
    return isinstance(side, dict) and isinstance(side.get(id_key), str) and bool(side[id_key])


def _change_side(side: Any, doc: _Doc) -> dict[str, Any]:
    if not _present(side, "element_id"):
        return _side("none", "unavailable", doc)
    if not doc.ok:
        return _side("present", "unavailable", doc)
    element = doc.elements.get(side["element_id"])
    if element is None:
        return _side("present", "unavailable", doc)
    regions = _dedupe(doc.element_regions(element))
    page = _page_of(element.get("primary_page"))
    if page is None:
        page = _page_of(side.get("primary_page"))
    if regions:
        if page not in {r["page_number"] for r in regions}:
            page = regions[0]["page_number"]
        return _side("present", "polygon", doc, page, regions)
    if page is not None and page in doc.pages:
        return _side("present", "page_only", doc, page)
    return _side("present", "unavailable", doc)


def _cell_regions(cell: dict[str, Any], doc: _Doc) -> tuple[list[dict[str, Any]], int | None]:
    page = _page_of(cell.get("primary_page"))
    direct = doc.region(page, cell.get("primary_polygon"))
    if direct is not None:
        return [direct], page
    table_id = cell.get("source_table_id")
    found: list[dict[str, Any]] = []
    if isinstance(table_id, str) and table_id and page is not None:
        for element in doc.element_list:
            if element.get("represented_by_table_id") == table_id:
                found.extend(r for r in doc.element_regions(element) if r["page_number"] == page)
    return _dedupe(found), page


def _table_side(record: dict[str, Any], side: str, doc: _Doc) -> dict[str, Any]:
    if not _present(record.get(side), "logical_table_id"):
        return _side("none", "unavailable", doc)
    if not doc.ok:
        return _side("present", "unavailable", doc)
    cells = [c for c in record.get("cell_changes") or [] if isinstance(c, dict)]
    changed = [c for c in cells if c.get("classification") != "UNCHANGED"]
    regions: list[dict[str, Any]] = []
    pages: list[int] = []
    for change in changed or cells:
        cell = change.get(side)
        if not isinstance(cell, dict):
            continue
        found, page = _cell_regions(cell, doc)
        regions.extend(found)
        if page is not None and page in doc.pages:
            pages.append(page)
    regions = _dedupe(regions)
    if regions:
        return _side("present", "polygon", doc, regions[0]["page_number"], regions)
    if pages:
        return _side("present", "page_only", doc, pages[0])
    return _side("present", "unavailable", doc)


def resolve_locations(output_dir: Path, canonical: dict, kind: str, record_id: str) -> dict:
    """Return pages and normalized polygons (0-1) per side for one finding."""
    if kind not in ID_FIELDS:
        raise ReaderError("invalid_arguments", f"unknown kind: {kind!r}")
    id_field = ID_FIELDS[kind]
    rows = canonical.get(_CONTAINERS[kind]) if isinstance(canonical, dict) else None
    record = next(
        (r for r in rows or [] if isinstance(r, dict) and r.get(id_field) == record_id),
        None,
    )
    if record is None:
        raise ReaderError("not_found", f"{kind} record not found")

    docs = {side: _Doc(_load_document(output_dir, side)) for side in SIDES}
    result: dict[str, Any] = {"kind": kind, "record_id": record_id}
    if kind == "change":
        result["change_type"] = record.get("change_type")
        sides = {s: _change_side(record.get(s), docs[s]) for s in SIDES}
    elif kind == "table":
        result["change_type"] = record.get("change_type")
        sides = {s: _table_side(record, s, docs[s]) for s in SIDES}
    else:
        result["change_type"] = None
        sides = {s: _side("present", "unavailable", docs[s]) for s in SIDES}
    result["sides"] = sides
    return result
