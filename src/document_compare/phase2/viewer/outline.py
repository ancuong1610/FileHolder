"""Section outline of one saved comparison: where each finding sits in the documents.

Read-only. Combines the canonical result with the optional
``{old,new}_document_structured.json`` artifacts of one output folder. Section
structure is an evidence-based hypothesis from the extraction, not authority:
a record whose element has no section is listed under ``unplaced``, never guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from document_compare.phase2.reader.result_reader import ID_FIELDS, ReaderError
from document_compare.phase2.viewer.locations import (
    SIDES, _CONTAINERS, _load_document, linked_change_ids, needs_review_status, record_side_ref, review_reason,
)

LABEL_LIMIT = 90
_MAX_DOC_BYTES = 256 * 1024 * 1024
_BIG = 10**9


def _norm(title: Any) -> str:
    return re.sub(r"\s+", " ", str(title or "")).strip().casefold()


def _collapse(text: Any) -> str:
    if isinstance(text, (list, tuple)):
        text = " · ".join(_collapse(part) for part in text if _collapse(part))
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _label(text: Any) -> str:
    clean = _collapse(text)
    return clean if len(clean) <= LABEL_LIMIT else clean[:LABEL_LIMIT] + "…"


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _load_structured(output_dir: Path, side: str) -> dict[str, Any] | None:
    """Parsed ``document`` of one side's structured artifact; None when absent or unusable."""
    base = Path(output_dir).resolve()
    path = base / f"{side}_document_structured.json"
    try:
        resolved = path.resolve()
        if resolved.parent != base or not resolved.is_file() or resolved.stat().st_size > _MAX_DOC_BYTES:
            return None
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError as exc:
        raise ReaderError("invalid_artifact", f"{side}_document_structured.json is not valid JSON") from exc
    document = data.get("document") if isinstance(data, dict) else None
    return document if isinstance(document, dict) else None


class _Side:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self.sections: list[dict[str, Any]] = []
        self.by_element: dict[str, str] = {}
        self.elements: dict[str, dict[str, Any]] = {}
        self.table_element: dict[str, str] = {}
        self.page_count: int | None = None
        if document is None:
            return
        self.page_count = _int(document.get("page_count"))
        evidence = document.get("structure_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        sections = evidence.get("sections")
        self.sections = [s for s in sections if isinstance(s, dict) and isinstance(s.get("section_id"), str)
                         ] if isinstance(sections, list) else []
        membership = evidence.get("membership")
        by_element = membership.get("by_element") if isinstance(membership, dict) else None
        for eid, info in (by_element.items() if isinstance(by_element, dict) else []):
            if isinstance(info, dict) and isinstance(info.get("section_id"), str):
                self.by_element[eid] = info["section_id"]
        elements = document.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict) or not isinstance(element.get("element_id"), str):
                continue
            self.elements.setdefault(element["element_id"], element)
            table_id = element.get("represented_by_table_id")
            if isinstance(table_id, str) and table_id:
                self.table_element.setdefault(table_id, element["element_id"])

    def heading_page(self, section_id: str | None) -> int | None:
        """Page of the section's heading element (None when unknown)."""
        for section in self.sections:
            if section["section_id"] == section_id:
                element = self.elements.get(section.get("heading_element_id"))
                return _int(element.get("primary_page")) if element else None
        return None

    def section_span(self, section_id: str | None) -> tuple[int, int] | None:
        """First and last page of the section's member elements (None when unknown)."""
        pages = [p for eid, sid in self.by_element.items() if sid == section_id
                 for p in [_int((self.elements.get(eid) or {}).get("primary_page"))] if p is not None]
        if not pages:
            heading = self.heading_page(section_id)
            return (heading, heading) if heading is not None else None
        return min(pages), max(pages)

    def table_element_for(self, part: Any) -> str | None:
        """Element of a table record's side: by logical table ID, else by its source table IDs."""
        if not isinstance(part, dict):
            return None
        found = self.table_element.get(part.get("logical_table_id"))
        for sid in part.get("source_table_ids") or []:
            if found is None and sid in self.elements:
                found = sid
        return found

    def previous_section(self, element_id: str | None) -> str | None:
        """Section whose heading is the last one at or before the element in reading order."""
        element = self.elements.get(element_id or "")
        order = _int(element.get("reading_order")) if element else None
        if order is None:
            return None
        best: tuple[int, str] | None = None
        for section in self.sections:
            heading = self.elements.get(section.get("heading_element_id"))
            h_order = _int(heading.get("reading_order")) if heading else None
            if h_order is not None and h_order <= order and (best is None or h_order >= best[0]):
                best = (h_order, section["section_id"])
        return best[1] if best else None

    def element_for(self, kind: str, ref: str | None) -> str | None:
        if ref is None:
            return None
        return self.table_element.get(ref) if kind == "table" else ref

    def section_of(self, kind: str, ref: str | None) -> str | None:
        eid = self.element_for(kind, ref)
        return self.by_element.get(eid) if eid else None


class _Node:
    def __init__(self, section_id: str, title: str, side: str, level: int | None) -> None:
        self.section_id, self.title, self.side, self.level = section_id, title, side, level
        self.children: list[_Node] = []
        self.items: list[tuple[tuple[int, int], dict[str, Any]]] = []
        self.path: list[str] = [title]
        self.parent: _Node | None = None
        self.section_ids: dict[str, str] = {side: section_id}  # per-side section id (merged by title)

    def count(self) -> int:
        return len(self.items) + sum(c.count() for c in self.children)

    def as_dict(self) -> dict[str, Any]:
        ordered = [item for _, item in sorted(self.items, key=lambda pair: pair[0])]
        return {
            "section_id": self.section_id, "title": self.title, "side": self.side, "level": self.level,
            "change_count": self.count(), "children": [c.as_dict() for c in self.children], "items": ordered,
        }


def _build_tree(sides: dict[str, _Side]) -> tuple[list[_Node], dict[tuple[str, str], _Node]]:
    roots: list[_Node] = []
    index: dict[tuple[str, str], _Node] = {}
    by_title: dict[str, _Node] = {}

    def attach(node: _Node, section: dict[str, Any], side: str) -> None:
        parent_id = section.get("parent_section_id")
        parent = index.get((side, parent_id)) if section.get("parent_known") is True \
            and isinstance(parent_id, str) else None
        ancestor = parent
        while ancestor is not None and ancestor is not node:  # a cycle must not hide the node
            ancestor = ancestor.parent
        if parent is None or ancestor is node:
            roots.append(node)
        else:
            node.parent = parent
            parent.children.append(node)

    for side in ("new", "old"):
        created: list[tuple[_Node, dict[str, Any]]] = []
        for section in sides[side].sections:  # first pass, so a parent listed late is still found
            sid = section["section_id"]
            title = str(section.get("title") or "")
            key = _norm(title)
            merged = by_title.get(key) if side == "old" else None
            if merged is not None:
                index[(side, sid)] = merged
                merged.section_ids.setdefault(side, sid)
                continue
            node = _Node(sid, title, side, _int(section.get("level")))
            index[(side, sid)] = node
            by_title.setdefault(key, node)
            created.append((node, section))
        for node, section in created:
            attach(node, section, side)

    def set_paths(node: _Node, prefix: list[str]) -> None:
        node.path = prefix + [node.title]
        for child in node.children:
            set_paths(child, node.path)

    for root in roots:
        set_paths(root, [])
    return roots, index


def _table_pages(record: dict[str, Any], side: str) -> int | None:
    cells = [c for c in record.get("cell_changes") or [] if isinstance(c, dict)]
    changed = [c for c in cells if c.get("classification") != "UNCHANGED"]
    for change in changed or cells:
        cell = change.get(side)
        page = _int(cell.get("primary_page")) if isinstance(cell, dict) else None
        if page is not None:
            return page
    return None


def _item(kind: str, record: dict[str, Any], sides: dict[str, _Side]) -> dict[str, Any]:
    ctype = record.get("change_type") if isinstance(record.get("change_type"), str) else None
    item: dict[str, Any] = {
        "kind": kind, "record_id": record.get(ID_FIELDS[kind]), "change_type": ctype,
        "label": "", "page_old": None, "page_new": None, "section_path": [],
    }
    if kind == "change":
        pref = ("old", "new") if ctype == "DELETED" else ("new", "old")
        text = ""
        for side in pref:
            part = record.get(side)
            if isinstance(part, dict) and _collapse(part.get("text")):
                text = part["text"]
                break
        item["label"] = _label(text)
        for side in SIDES:
            part = record.get(side)
            page = _int(part.get("primary_page")) if isinstance(part, dict) else None
            if page is None and isinstance(part, dict):
                element = sides[side].elements.get(part.get("element_id"))
                page = _int(element.get("primary_page")) if element else None
            item[f"page_{side}"] = page
    elif kind == "table":
        pref = ("old", "new") if ctype and "DELETED" in ctype else ("new", "old")
        for side in SIDES:
            item[f"page_{side}"] = _table_pages(record, side)
        page = next((item[f"page_{side}"] for side in pref if item[f"page_{side}"] is not None), None)
        cells = [c for c in record.get("cell_changes") or [] if isinstance(c, dict)
                 and c.get("classification") != "UNCHANGED"]
        label = "Table" + (f" \u00b7 p.{page}" if page is not None else "")
        if cells:
            label += f" \u00b7 {len(cells)} cell{'s' if len(cells) != 1 else ''} changed"
        item["label"] = label
    else:
        evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
        text = next((evidence.get(k) for k in ("new_text_preview", "old_text_preview") if _collapse(evidence.get(k))), "")
        case = record.get("case_type") or record.get("source_kind") or "review item"
        item["label"] = _label(text or review_reason(record) or str(case).replace("_", " "))
        item["change_type"] = None
    resolved = needs_review_status(record) or kind == "review"
    item["needs_review"] = resolved
    item["review_reason"] = review_reason(record) if resolved else None
    item["review_ids"] = [record[ID_FIELDS["review"]]] if kind == "review" else []
    item["llm_opinion"] = llm_opinion(record)
    return item


def llm_opinion(record: dict[str, Any]) -> dict[str, Any] | None:
    """The advisory LLM decision stored on a record (Stage 13), or None.

    Read from ``resolution.packet_decision``. Only outcome, reason, evaluator and the
    element IDs it looked at are passed on; the model's confidence number is left out
    on purpose because it is not calibrated.
    """
    resolution = record.get("resolution") if isinstance(record.get("resolution"), dict) else {}
    decision = resolution.get("packet_decision")
    if not isinstance(decision, dict) or not isinstance(decision.get("outcome"), str):
        return None
    refs = decision.get("evidence_refs")
    return {
        "outcome": decision["outcome"],
        "reason": _collapse(decision.get("reason")) or None,
        "evaluator": decision.get("evaluator") if isinstance(decision.get("evaluator"), str) else None,
        "evidence_refs": [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else [],
    }


def _preorder(nodes: list[_Node]) -> list[_Node]:
    out: list[_Node] = []
    for node in nodes:
        out.append(node)
        out.extend(_preorder(node.children))
    return out


def _fill_context_pages(roots: list[_Node], placed: list[tuple[_Node, dict[str, Any]]],
                        unplaced: list[dict[str, Any]], sides: dict[str, _Side],
                        counts: dict[str, int | None]) -> None:
    """Give one-sided items the page where the same section lives on the missing side.

    Order of preference: the section's own page span on that side; the span of the nearest
    previous section (tree order) that has one; the other side's page clamped to the
    document's page count. Within a span, the page closest to the other side's page is
    used, so a section running over pages 1-3 shows page 3 for a change on page 3.
    ``context_<side>`` is true when the page was filled so.
    """
    last: dict[str, tuple[int, int] | None] = {side: None for side in SIDES}
    context: dict[int, dict[str, tuple[int, int] | None]] = {}
    for node in _preorder(roots):
        per_side: dict[str, tuple[int, int] | None] = {}
        for side in SIDES:
            own = sides[side].section_span(node.section_ids.get(side)) if side in node.section_ids else None
            if own is not None:
                last[side] = own
            per_side[side] = own if own is not None else last[side]
        context[id(node)] = per_side

    def fill(item: dict[str, Any], node: _Node | None) -> None:
        for side in SIDES:
            other = "new" if side == "old" else "old"
            item[f"context_{side}"] = False
            if item.get(f"page_{side}") is not None:
                continue
            span = context[id(node)][side] if node is not None else None
            page = None
            if span is not None:
                target = item.get(f"page_{other}")
                page = span[0] if target is None else max(span[0], min(target, span[1]))
            if page is None:
                page = item.get(f"page_{other}")
                count = counts[side]
                page = max(1, min(page, count)) if page is not None and count and count >= 1 else None
            if page is not None:
                item[f"page_{side}"], item[f"context_{side}"] = page, True

    for node, item in placed:
        fill(item, node)
    for item in unplaced:
        fill(item, None)


def _review_element(record: dict[str, Any], side: str) -> str | None:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    ids = [i for i in evidence.get(f"{side}_element_ids") or [] if isinstance(i, str)]
    return ids[0] if ids else None


def _place(kind: str, record: dict[str, Any], sides: dict[str, _Side], index: dict[tuple[str, str], _Node],
           pref: tuple[str, ...]) -> _Node | None:
    """Section node of a record: its own section on the preferred side, else (tables) the nearest
    previous section in reading order."""
    for fallback in ((False, True) if kind == "table" else (False,)):
        for side in pref:
            if kind == "review":
                eid = _review_element(record, side)
            elif kind == "table":
                eid = sides[side].table_element_for(record.get(side))
            else:
                eid = sides[side].element_for(kind, record_side_ref(kind, record, side))
            if not eid:
                continue
            sid = sides[side].previous_section(eid) if fallback else sides[side].by_element.get(eid)
            node = index.get((side, sid)) if sid else None
            if node is not None:
                return node
    return None


def _order_of(kind: str, record: dict[str, Any], sides: dict[str, _Side], pref: tuple[str, ...],
              position: int) -> tuple[int, int]:
    for side in pref:
        if kind == "review":
            eid = _review_element(record, side)
        elif kind == "table":
            eid = sides[side].table_element_for(record.get(side))
        else:
            eid = sides[side].element_for(kind, record_side_ref(kind, record, side))
        element = sides[side].elements.get(eid or "")
        order = _int(element.get("reading_order")) if element else None
        if order is None and kind == "change" and isinstance(record.get(side), dict):
            order = _int(record[side].get("reading_order"))
        if order is not None:
            return (order, position)
    return (_BIG, position)


def _review_pages(item: dict[str, Any], record: dict[str, Any], sides: dict[str, _Side]) -> None:
    for side in SIDES:
        element = sides[side].elements.get(_review_element(record, side) or "")
        item[f"page_{side}"] = _int(element.get("primary_page")) if element else None


def build_outline(output_dir: Path, canonical: dict) -> dict:
    """Section tree (NEW document first, OLD-only sections appended) with findings placed in it."""
    sides = {side: _Side(_load_structured(output_dir, side)) for side in SIDES}
    roots, index = _build_tree(sides)
    unplaced: list[dict[str, Any]] = []
    placed_items: list[tuple[_Node, dict[str, Any]]] = []
    review: list[dict[str, Any]] = []
    canonical = canonical if isinstance(canonical, dict) else {}
    by_id: dict[str, dict[str, Any]] = {}

    def put(kind: str, record: dict[str, Any], item: dict[str, Any], position: int) -> None:
        ctype = record.get("change_type") if isinstance(record.get("change_type"), str) else ""
        pref = ("old", "new") if "DELETED" in ctype else ("new", "old")
        node = _place(kind, record, sides, index, pref)
        if node is None:
            unplaced.append(item)
        else:
            item["section_path"] = list(node.path)
            node.items.append((_order_of(kind, record, sides, pref, position), item))
            placed_items.append((node, item))

    for kind in ("change", "table"):
        rows = canonical.get(_CONTAINERS[kind])
        for position, record in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(record, dict) or not isinstance(record.get(ID_FIELDS[kind]), str):
                continue
            item = _item(kind, record, sides)
            by_id[record[ID_FIELDS[kind]]] = item
            put(kind, record, item, position)

    changes = canonical.get("changes") if isinstance(canonical.get("changes"), list) else []
    rows = canonical.get(_CONTAINERS["review"])
    for position, record in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(record, dict) or not isinstance(record.get(ID_FIELDS["review"]), str):
            continue
        rid = record[ID_FIELDS["review"]]
        linked = [cid for cid in linked_change_ids(record, changes) if cid in by_id]
        entry = _item("review", record, sides)
        entry["context_old"] = entry["context_new"] = False
        entry["linked_to"] = list(linked)
        review.append(entry)
        if linked:
            for cid in linked:
                target = by_id[cid]
                target["needs_review"] = True
                if rid not in target["review_ids"]:
                    target["review_ids"].append(rid)
                target["review_reason"] = target["review_reason"] or entry["review_reason"]
                target["llm_opinion"] = target.get("llm_opinion") or entry.get("llm_opinion")
            continue
        item = dict(entry, review_ids=list(entry["review_ids"]))
        item.pop("linked_to")
        item["change_type"] = "REVIEW"
        _review_pages(item, record, sides)
        put("review", record, item, position)

    for item in [i for _, i in placed_items] + unplaced:
        if item["needs_review"] and not item["review_reason"]:
            item["review_reason"] = "Needs review"
    for entry in review:
        entry["review_reason"] = entry["review_reason"] or "Needs review"

    documents = {}
    for side in SIDES:
        loaded = _load_document(output_dir, side)
        count = _int(loaded.get("page_count")) if loaded else None
        if count is None and loaded and isinstance(loaded.get("pages"), list):
            count = len(loaded["pages"])
        documents[side] = {"page_count": count if count is not None else sides[side].page_count}

    _fill_context_pages(roots, placed_items, unplaced, sides, {s: documents[s]["page_count"] for s in SIDES})
    sections = [n.as_dict() for n in roots]
    placed = sum(n["change_count"] for n in sections)
    return {"documents": documents, "sections": sections, "unplaced": unplaced, "review": review,
            "total": placed + len(unplaced)}
