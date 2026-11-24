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
_CONTAINERS = {"change": "changes", "table": "table_changes", "review": "review_queue", "move": "moves"}
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


def member_element_ids(record: dict[str, Any], side: str) -> list[str]:
    ids = record.get(f"{side}_element_ids")
    return [eid for eid in ids if isinstance(eid, str) and eid] if isinstance(ids, list) else []


def record_side_ref(kind: str, record: dict[str, Any], side: str) -> str | None:
    """The per-side element or logical table reference used for placement."""
    if kind == "move":
        return next(iter(member_element_ids(record, side)), None)
    part = record.get(side) if isinstance(record, dict) else None
    key = "logical_table_id" if kind == "table" else "element_id"
    if kind == "review" or not _present(part, key):
        return None
    return part[key]


_REASONS = {
    "unresolved_residual_counterpart": "Possible match on the other side not confirmed",
    "plausible_unresolved_counterpart": "Possible match on the other side not confirmed",
    "movement_block_review": "Block of content moved",
    "contiguous_structural_block_relative_order_relocation": "Block of content moved",
    "ambiguous_modified_pair": "Short or ambiguous change \u2014 LLM could not decide",
    "short_text_similarity_pair": "Short or ambiguous change \u2014 LLM could not decide",
}


def _humanize(text: Any) -> str:
    plain = str(text).replace("_", " ").strip()
    return plain[:1].upper() + plain[1:]


def review_reason(record: dict[str, Any]) -> str | None:
    """Plain-words reason a record needs review (None when the record names none)."""
    resolution = record.get("resolution") if isinstance(record.get("resolution"), dict) else {}
    cases = [c for c in (record.get("case_type"), resolution.get("case_type")) if isinstance(c, str) and c]
    reasons = [r for key in (resolution, record) for r in (key.get("review_reasons") or []) if isinstance(r, str) and r]
    for name in cases + reasons:
        if name in _REASONS:
            return _REASONS[name]
    named = (cases + reasons)[:1]
    return _humanize(named[0]) if named else None


def needs_review_status(record: dict[str, Any]) -> bool:
    resolution = record.get("resolution")
    return isinstance(resolution, dict) and resolution.get("status") == "review"


def linked_change_ids(review: dict[str, Any], changes: list[Any]) -> list[str]:
    """Existing change IDs a review record is about: named in its evidence, or sharing its candidate ID."""
    existing = {c["change_id"]: c for c in changes if isinstance(c, dict) and isinstance(c.get("change_id"), str)}
    evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    named = [i for i in evidence.get("change_ids") or [] if isinstance(i, str)] + [review.get("change_id")]
    out = [i for i in named if isinstance(i, str) and i in existing]
    candidate = review.get("source_candidate_id")
    if isinstance(candidate, str) and candidate:
        out += [cid for cid, c in existing.items() if c.get("source_candidate_id") == candidate]
    return list(dict.fromkeys(out))


def reviews_by_change(canonical: Any) -> dict[str, list[dict[str, Any]]]:
    """Review records grouped under the change IDs they are linked to."""
    canonical = canonical if isinstance(canonical, dict) else {}
    changes = canonical.get("changes") if isinstance(canonical.get("changes"), list) else []
    reviews = canonical.get("review_queue") if isinstance(canonical.get("review_queue"), list) else []
    out: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        if isinstance(review, dict) and isinstance(review.get("review_id"), str):
            for cid in linked_change_ids(review, changes):
                out.setdefault(cid, []).append(review)
    return out


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
    regions = [dict(r, role="element") for r in _dedupe(doc.element_regions(element))]
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


def _elements_side(ids: list[str], doc: _Doc) -> dict[str, Any]:
    """One side of a multi-element record, using the regions of the named elements."""
    if not ids:
        return _side("none", "unavailable", doc)
    if not doc.ok:
        return _side("present", "unavailable", doc)
    regions: list[dict[str, Any]] = []
    pages: list[int] = []
    for eid in ids:
        element = doc.elements.get(eid)
        if element is None:
            continue
        found = doc.element_regions(element)
        regions.extend(dict(r, role="element") for r in found)
        page = _page_of(element.get("primary_page"))
        if page is not None and page in doc.pages:
            pages.append(page)
    regions = _dedupe(regions)
    if regions:
        return _side("present", "polygon", doc, regions[0]["page_number"], regions)
    if pages:
        return _side("present", "page_only", doc, pages[0])
    return _side("present", "unavailable", doc)


def _review_ids(record: dict[str, Any], canonical: dict[str, Any]) -> dict[str, list[str]]:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    ids = {s: [i for i in evidence.get(f"{s}_element_ids") or [] if isinstance(i, str)] for s in SIDES}
    if any(ids.values()):
        return ids
    changes = canonical.get("changes") if isinstance(canonical.get("changes"), list) else []
    wanted = set(linked_change_ids(record, changes))
    for change in changes:
        if isinstance(change, dict) and change.get("change_id") in wanted:
            for side in SIDES:
                members = member_element_ids(change, side)
                if members:
                    ids[side].extend(members)
                elif _present(change.get(side), "element_id"):
                    ids[side].append(change[side]["element_id"])
    return ids


def _table_elements(part: dict[str, Any], doc: _Doc) -> list[dict[str, Any]]:
    """Document elements of one side's logical table (by represented_by_table_id, else source table IDs)."""
    wanted = {part.get("logical_table_id")} | {i for i in part.get("source_table_ids") or [] if isinstance(i, str)}
    found = [e for e in doc.element_list if e.get("represented_by_table_id") in wanted]
    if not found:
        found = [doc.elements[i] for i in part.get("source_table_ids") or [] if i in doc.elements]
    return found


def _table_side(record: dict[str, Any], side: str, doc: _Doc) -> dict[str, Any]:
    if not _present(record.get(side), "logical_table_id"):
        return _side("none", "unavailable", doc)
    if not doc.ok:
        return _side("present", "unavailable", doc)
    cells = [c for c in record.get("cell_changes") or [] if isinstance(c, dict)]
    changed = [c for c in cells if c.get("classification") != "UNCHANGED"]
    table_regions: list[dict[str, Any]] = []
    for element in _table_elements(record[side], doc):
        table_regions.extend(dict(r, role="table") for r in doc.element_regions(element))
    cell_regions: list[dict[str, Any]] = []
    pages: list[int] = []
    for change in changed or cells:
        cell = change.get(side)
        if not isinstance(cell, dict):
            continue
        page = _page_of(cell.get("primary_page"))
        direct = doc.region(page, cell.get("primary_polygon"))
        if direct is not None and change in changed:
            cell_regions.append(dict(direct, role="cell"))
        if page is not None and page in doc.pages:
            pages.append(page)
    regions = _dedupe(table_regions) + _dedupe(cell_regions)
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
        sides = {}
        for side in SIDES:
            ids = member_element_ids(record, side)
            sides[side] = (
                _elements_side(ids, docs[side]) if len(ids) > 1
                else _change_side(record.get(side), docs[side])
            )
    elif kind == "table":
        result["change_type"] = record.get("change_type")
        sides = {s: _table_side(record, s, docs[s]) for s in SIDES}
    elif kind == "move":
        result["change_type"] = None
        sides = {s: _elements_side(member_element_ids(record, s), docs[s]) for s in SIDES}
    else:
        result["change_type"] = None
        ids = _review_ids(record, canonical)
        sides = {s: _elements_side(ids[s], docs[s]) for s in SIDES}
    result["sides"] = sides
    linked = []
    if kind == "change":
        linked = reviews_by_change(canonical).get(record_id, [])
    elif kind == "review":
        linked = [record]
    result["needs_review"] = kind == "review" or needs_review_status(record) or bool(linked)
    reason = review_reason(record) if kind == "review" or needs_review_status(record) else None
    for review in linked:
        reason = reason or review_reason(review)
    result["review_reason"] = reason if result["needs_review"] else None
    return result
