"""Pure, additive TOC-guided structure evidence (T-02/T-03).

``build_structure_evidence(document)`` inspects a normalized/structured
document and returns a versioned, self-contained hypothesis layer:

* TOC candidates (explicit EN/DE contents headings, contiguous text material,
  table title/page rows and split title/page paragraphs),
* entry-to-body links by conservative exact title matching (duplicates stay
  ambiguous, missing titles stay unmatched, printed and physical pages stay
  separate),
* tentative section levels/parents only when dotted numeric labels support
  them (no invented root parents), plus a flat body-anchor fallback when the
  TOC is absent or unusable,
* a derived element-membership mapping that accounts for every element once.

The function never reads files, accesses providers, mutates its input or
claims authoritative hierarchy. DI roles are preserved separately from the
reconstructed hypotheses; TOC tables are *not* removed from comparison here.
Only the Python standard library and the project ``require`` helper are used.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from crr_compare.derived_structure import (
    DERIVED_FURNITURE_VERSION,
    classify_derived_furniture,
)
from crr_compare.validation import require

# 1.1.0: adds derived recurring-furniture classification and namespace-aware
# numeric label hierarchy. Both are additive evidence blocks; sections may now
# exclude derived furniture headings that were previously promoted from a
# single DI role.
# 1.1.1: links a repeated bare title by its full qualified label (namespace AND
# numeric) when that pair is unique, records why an entry stays ambiguous, and
# keeps the unlabeled exact-title fallback.
# 1.1.2: refuses to force a single title match whose parsed numeric label
# contradicts the entry's (different namespace or number); the entry stays
# unmatched with an explicit label_conflict reason.
STRUCTURE_EVIDENCE_VERSION = "1.1.2"

LAYOUT_ELEMENT_TYPES = {"header", "footer", "page_number"}
BODY_HEADING_ELEMENT_TYPES = {"heading", "title"}

# Generic English/German contents headings. No CRR/legal vocabulary and no
# fixed sample page numbers.
CONTENTS_MARKERS = {
    "contents",
    "table of contents",
    "inhalt",
    "inhaltsverzeichnis",
}

WHITESPACE = re.compile(r"\s+")
KEY_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
})

# A trailing page token separated by dot/ellipsis leaders, a tab, or whitespace.
TOC_PAGE_LINE = re.compile(
    r"^(?P<title>.+?)(?:\s*(?:[.\u00b7\u2026]{2,}|[ \t]{2,})\s*|\s+)"
    r"(?P<page>\d{1,4}|[ivxlcdmIVXLCDM]{1,6})\s*$"
)
PAGE_TOKEN = re.compile(r"^(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,6})$")
LEADING_LABEL = re.compile(
    r"^\s*(?P<label>"
    r"(?P<numeric>\d+(?:\.\d+)*)"
    r"|[IVXLCDM]{1,7}"
    r"|[A-Za-z]"
    r")(?P<tail>(?:[.)]\s*|\s+)(?P<rest>\S.*)?)?$"
)

# Generic namespaced numeric label, e.g. "AT 2", "AT 2.1", "BTO 1.3.1".
# The namespace is an uppercase alphabetic family token of bounded length; the
# trailing numeric label carries the dotted hierarchy. Only a label with
# following title text is accepted. No CRR/domain vocabulary is encoded.
NAMESPACED_LABEL = re.compile(
    r"^\s*(?P<namespace>[A-Z]{1,6})\s+(?P<numeric>\d+(?:\.\d+)*)"
    r"(?P<tail>(?:[.)]\s*|\s+)(?P<rest>\S.*)?)?$"
)


def canonical_key_text(text: str) -> str:
    value = unicodedata.normalize("NFC", str(text))
    value = value.translate(KEY_TRANSLATION)
    value = WHITESPACE.sub(" ", value)
    return value.strip().casefold()


def element_text(element: dict[str, Any]) -> str:
    value = element.get("normalized_text") or element.get("text") or ""
    return str(value).strip()


def element_id_of(element: dict[str, Any]) -> str:
    return str(element.get("element_id"))


def is_layout(element: dict[str, Any]) -> bool:
    return element.get("element_type") in LAYOUT_ELEMENT_TYPES


def is_contents_heading(element: dict[str, Any]) -> bool:
    if element.get("element_type") == "table":
        return False
    key = canonical_key_text(element_text(element)).rstrip(":")
    return key in CONTENTS_MARKERS


def _heading_evidence(element: dict[str, Any]) -> dict[str, Any]:
    evidence = element.get("heading_evidence")
    return evidence if isinstance(evidence, dict) else {}


def is_body_heading(
    element: dict[str, Any],
    furniture_ids: set[str] | None = None,
) -> bool:
    if element.get("element_type") == "table":
        return False
    if furniture_ids and element_id_of(element) in furniture_ids:
        # Derived recurring page furniture is not a body heading even when a
        # single DI role labels it as one.
        return False
    if is_layout(element):
        return False
    if is_contents_heading(element):
        return False
    if element.get("element_type") in BODY_HEADING_ELEMENT_TYPES:
        return True
    evidence = _heading_evidence(element)
    if evidence.get("di_role_is_heading_like"):
        return True
    if element.get("original_role") in {"title", "sectionHeading"}:
        return True
    return False


def _empty_label(title: str) -> dict[str, Any]:
    return {
        "label": None,
        "label_numeric": None,
        "label_kind": None,
        "label_namespace": None,
        "title": title.strip(),
    }


def split_leading_label(title: str) -> dict[str, Any]:
    """Return label metadata for a TOC entry title, or empty label info.

    Handles bare Arabic/dotted labels ("2", "2.1"), Roman/alphabetic labels and
    generic namespaced numeric labels ("AT 2", "BTO 1.3.1"). The namespace is
    kept separately so a parent is only ever resolved inside its own naming
    family. A label is only a label when title text follows (or the label is
    intrinsically dotted).
    """
    match = LEADING_LABEL.match(title)
    if match:
        label = match.group("label")
        numeric = match.group("numeric")
        tail = match.group("tail") or ""
        rest = (match.group("rest") or "").strip()
        separator = tail[:1] if tail.startswith((".", ")")) else ""
        if numeric is not None:
            kind = "dotted" if "." in numeric else "arabic"
            display = numeric + separator
        elif re.fullmatch(r"[IVXLCDM]{1,7}", label):
            kind, display = "roman", label + separator
        else:
            kind, display = "alphabetic", label + separator
        if not rest and kind != "dotted":
            return _empty_label(title)
        return {
            "label": display,
            "label_numeric": numeric,
            "label_kind": kind,
            "label_namespace": None,
            "title": rest or title.strip(),
        }

    namespaced = NAMESPACED_LABEL.match(title)
    if namespaced:
        namespace = namespaced.group("namespace")
        numeric = namespaced.group("numeric")
        tail = namespaced.group("tail") or ""
        rest = (namespaced.group("rest") or "").strip()
        separator = tail[:1] if tail.startswith((".", ")")) else ""
        # Only a label introducing title text is a heading label; a bare
        # namespace+number stays unparsed rather than inventing a level.
        if not rest:
            return _empty_label(title)
        return {
            "label": f"{namespace} {numeric}{separator}",
            "label_numeric": numeric,
            "label_kind": "namespaced",
            "label_namespace": namespace,
            "title": rest,
        }

    return _empty_label(title)


def _location(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_page": element.get("primary_page"),
        "spans": element.get("spans"),
        "polygon": element.get("primary_polygon"),
    }


def heading_key_variants(element: dict[str, Any]) -> set[str]:
    """Conservative canonical keys for a body heading (full text and label-stripped)."""
    text = element_text(element)
    variants = {canonical_key_text(text)}
    label_info = split_leading_label(text)
    if label_info["title"] and label_info["title"] != text:
        variants.add(canonical_key_text(label_info["title"]))
    return variants


def _parse_text_entry(raw_text: str) -> dict[str, Any] | None:
    match = TOC_PAGE_LINE.match(raw_text.strip())
    if not match:
        return None
    title = match.group("title").strip()
    if not any(character.isalpha() for character in title):
        return None
    page = match.group("page")
    return {"title": title, "printed_page": page}


def _polygon_overlap(subject: Any, container: Any) -> float:
    def bounds(polygon: Any):
        if not isinstance(polygon, list) or len(polygon) < 8 or len(polygon) % 2:
            return None
        xs, ys = polygon[0::2], polygon[1::2]
        if not all(isinstance(v, (int, float)) for v in xs + ys):
            return None
        return min(xs), min(ys), max(xs), max(ys)

    a, b = bounds(subject), bounds(container)
    if a is None or b is None:
        return 0.0
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    return (width * height / area) if area else 0.0


def _paragraph_overlaps_table(element: dict[str, Any], table: dict[str, Any]) -> bool:
    if element.get("represented_by_table_id") == table.get("element_id"):
        return True
    if element.get("primary_page") != table.get("primary_page"):
        return False
    return _polygon_overlap(element.get("primary_polygon"), table.get("primary_polygon")) >= 0.5


def _table_rows(table: dict[str, Any]) -> list[list[dict[str, Any]]]:
    cells = (table.get("table") or {}).get("cells") or []
    rows: dict[int, list[dict[str, Any]]] = {}
    for cell in cells:
        row_index = cell.get("row_index")
        if isinstance(row_index, int):
            rows.setdefault(row_index, []).append(cell)
    return [sorted(row, key=lambda c: (c.get("column_index") if isinstance(c.get("column_index"), int) else 0)) for _, row in sorted(rows.items())]


def _table_title_page_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in _table_rows(table):
        title_cell = None
        page_cell = None
        for cell in row:
            text = str(cell.get("normalized_text") or cell.get("text") or "").strip()
            if not text:
                continue
            if PAGE_TOKEN.fullmatch(text) and page_cell is None and title_cell is not None:
                page_cell = cell
            elif any(character.isalpha() for character in text) and title_cell is None:
                title_cell = cell
        if title_cell is not None and page_cell is not None:
            parsed.append({"title_cell": title_cell, "page_cell": page_cell})
    return parsed


def _is_toc_table(
    table: dict[str, Any],
    *,
    explicit_heading_adjacent: bool,
    body_heading_keys: set[str],
    body_heading_first_order: dict[str, int],
) -> bool:
    rows = _table_title_page_rows(table)
    if not rows:
        return False
    if explicit_heading_adjacent:
        return True
    # An unheaded candidate table needs strong, independent corroboration:
    # at least two *distinct* body headings and a majority of rows matching,
    # and it must precede the corroborated headings (local continuation). A
    # single accidental title/page row or a numeric data table must not pass.
    if len(rows) < 2:
        return False
    title_keys = [
        canonical_key_text(str(row["title_cell"].get("normalized_text") or row["title_cell"].get("text") or ""))
        for row in rows
    ]
    corroborated = [key for key in title_keys if key in body_heading_keys]
    distinct = set(corroborated)
    if len(distinct) < 2:
        return False
    if len(corroborated) * 2 < len(rows):
        return False
    table_order = table.get("reading_order")
    heading_orders = [body_heading_first_order.get(key) for key in distinct]
    heading_orders = [order for order in heading_orders if isinstance(order, int)]
    if not isinstance(table_order, int) or not heading_orders:
        return False
    if table_order >= min(heading_orders):
        return False
    return True


def _next_content_element(elements: list[dict[str, Any]], start: int) -> tuple[int, dict[str, Any]] | None:
    for index in range(start + 1, len(elements)):
        if not is_layout(elements[index]):
            return index, elements[index]
    return None


def build_structure_evidence(
    document: dict[str, Any],
    derived_furniture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the additive structure-evidence layer for ``document``.

    Pure: the input mapping is only read. All identities are derived from the
    document; no file, network or provider access happens here. ``derived_furniture``
    may be supplied by a caller (e.g. ``s05_anchors``) to reuse one identical
    classification instead of recomputing it.
    """
    require(isinstance(document, dict), "structure: document must be a mapping")
    elements = document.get("elements")
    require(isinstance(elements, list) and elements, "structure: document.elements must be a non-empty list")
    require(
        len({element_id_of(element) for element in elements}) == len(elements),
        "structure: duplicate element_id in document",
    )

    if derived_furniture is None:
        derived_furniture = classify_derived_furniture(document)
    require(
        isinstance(derived_furniture, dict)
        and isinstance(derived_furniture.get("by_element"), dict),
        "structure: derived furniture classification is malformed",
    )
    furniture_ids = set(derived_furniture["by_element"])

    ordered = sorted(elements, key=lambda element: element.get("reading_order") if isinstance(element.get("reading_order"), int) else 0)
    element_by_id = {element_id_of(element): element for element in elements}

    body_heading_elements = [
        element for element in ordered if is_body_heading(element, furniture_ids)
    ]
    body_heading_keys: set[str] = set()
    body_heading_first_order: dict[str, int] = {}
    for element in body_heading_elements:
        order = element.get("reading_order")
        for key in heading_key_variants(element):
            body_heading_keys.add(key)
            if isinstance(order, int):
                body_heading_first_order[key] = min(
                    body_heading_first_order.get(key, order), order
                )

    contents_positions = [index for index, element in enumerate(ordered) if is_contents_heading(element)]
    contents_heading_ids = {element_id_of(ordered[position]) for position in contents_positions}
    toc_table_ids: set[str] = set()
    toc_source_ids: set[str] = set(contents_heading_ids)
    entries: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}

    def make_entry_id(index: int) -> str:
        return f"{document.get('document_id')}-TOC{index:04d}"

    # ---- table TOCs (explicit heading adjacency or corroborated repetition) ----
    for position, element in enumerate(ordered):
        if element.get("element_type") != "table":
            continue
        explicit_adjacent = bool(
            contents_positions
            and _next_content_element(ordered, contents_positions[0]) is not None
            and _next_content_element(ordered, contents_positions[0])[1] is element
        )
        if not _is_toc_table(
            element,
            explicit_heading_adjacent=explicit_adjacent,
            body_heading_keys=body_heading_keys,
            body_heading_first_order=body_heading_first_order,
        ):
            continue
        toc_table_ids.add(element_id_of(element))
        for row in _table_title_page_rows(element):
            title_cell = row["title_cell"]
            page_cell = row["page_cell"]
            title = str(title_cell.get("normalized_text") or title_cell.get("text") or "").strip()
            label_info = split_leading_label(title)
            entries.append({
                "entry_id": make_entry_id(len(entries) + 1),
                "source_kind": "table",
                "source_element_ids": [],
                "source_table_id": element_id_of(element),
                "source_cell_ids": [str(title_cell.get("cell_id")), str(page_cell.get("cell_id"))],
                "raw_text": f"{title} {str(page_cell.get('normalized_text') or page_cell.get('text') or '').strip()}",
                "label": label_info["label"],
                "label_numeric": label_info["label_numeric"],
                "label_kind": label_info["label_kind"],
                "label_namespace": label_info["label_namespace"],
                "title": label_info["title"],
                "printed_page": str(page_cell.get("normalized_text") or page_cell.get("text") or "").strip(),
                "location": _location(title_cell),
                "body_element_id": None,
                "status": "unmatched",
                "candidate_body_element_ids": [],
                "link_basis": None,
                "ambiguity_reason": None,
                "printed_page_differs_from_physical": None,
            })

    # ---- text TOC material after the first explicit contents heading ----
    if contents_positions:
        start = contents_positions[0]
        pending: dict[str, Any] | None = None
        stopped_reason = None
        for index in range(start + 1, len(ordered)):
            element = ordered[index]
            if element_id_of(element) in toc_table_ids:
                stopped_reason = "toc_table_reached"
                break
            if is_layout(element):
                continue
            if is_body_heading(element):
                stopped_reason = "body_heading_reached"
                break
            if element.get("element_type") == "table":
                stopped_reason = "incompatible_table_reached"
                break
            text = element_text(element)
            parsed = _parse_text_entry(text)
            if parsed is not None:
                if pending is not None:
                    pending = None
                label_info = split_leading_label(parsed["title"])
                entries.append({
                    "entry_id": make_entry_id(len(entries) + 1),
                    "source_kind": "text",
                    "source_element_ids": [element_id_of(element)],
                    "source_table_id": None,
                    "source_cell_ids": [],
                    "raw_text": text,
                    "label": label_info["label"],
                    "label_numeric": label_info["label_numeric"],
                    "label_kind": label_info["label_kind"],
                    "label_namespace": label_info["label_namespace"],
                    "title": label_info["title"],
                    "printed_page": parsed["printed_page"],
                    "location": _location(element),
                    "body_element_id": None,
                    "status": "unmatched",
                    "candidate_body_element_ids": [],
                    "link_basis": None,
                    "ambiguity_reason": None,
                    "printed_page_differs_from_physical": None,
                })
                toc_source_ids.add(element_id_of(element))
                continue
            if PAGE_TOKEN.fullmatch(text):
                if pending is not None:
                    label_info = split_leading_label(pending["text"])
                    entries.append({
                        "entry_id": make_entry_id(len(entries) + 1),
                        "source_kind": "text",
                        "source_element_ids": [element_id_of(pending["element"]), element_id_of(element)],
                        "source_table_id": None,
                        "source_cell_ids": [],
                        "raw_text": f"{pending['text']} {text}",
                        "label": label_info["label"],
                        "label_numeric": label_info["label_numeric"],
                        "label_kind": label_info["label_kind"],
                        "label_namespace": label_info["label_namespace"],
                        "title": label_info["title"],
                        "printed_page": text,
                        "location": _location(pending["element"]),
                        "body_element_id": None,
                        "status": "unmatched",
                        "candidate_body_element_ids": [],
                        "link_basis": None,
                        "ambiguity_reason": None,
                        "printed_page_differs_from_physical": None,
                    })
                    toc_source_ids.add(element_id_of(pending["element"]))
                    toc_source_ids.add(element_id_of(element))
                    pending = None
                continue
            if any(character.isalpha() for character in text):
                pending = {"element": element, "text": text}
                continue
            stopped_reason = "incompatible_content_reached"
            break
        diagnostics["text_toc_stop_reason"] = stopped_reason

    # TOC tables must not be duplicated by overlapping paragraphs.
    for element in ordered:
        if element.get("element_type") == "table":
            continue
        for table_id in toc_table_ids:
            table = element_by_id[table_id]
            if _paragraph_overlaps_table(element, table):
                toc_source_ids.add(element_id_of(element))

    toc_source_ids -= toc_table_ids

    # ---- conservative exact-title linking ----
    # Link candidates exclude TOC source elements, contents headings and
    # elements represented by a table, so a TOC table cell or an overlapping
    # paragraph can never link to itself or to a duplicate table paragraph.
    link_candidates = [
        element
        for element in body_heading_elements
        if element_id_of(element) not in toc_source_ids
        and not element.get("inside_table")
        and element.get("represented_by_table_id") is None
    ]
    link_keys_by_element = {
        element_id_of(element): heading_key_variants(element)
        for element in link_candidates
    }
    link_label_info_by_element = {
        element_id_of(element): split_leading_label(element_text(element))
        for element in link_candidates
    }

    def _same_full_label(candidate_id: str, entry: dict[str, Any], title_key: str) -> bool:
        """True only when entry and body share namespace AND numeric label.

        Namespace alone or numeric alone is insufficient: the label family and
        the dotted number must both agree, and the residual title must match.
        """
        entry_numeric = entry.get("label_numeric")
        info = link_label_info_by_element[candidate_id]
        if not entry_numeric or not info.get("label_numeric"):
            return False
        return (
            info.get("label_namespace") == entry.get("label_namespace")
            and info.get("label_numeric") == entry_numeric
            and canonical_key_text(info["title"]) == title_key
        )

    def _label_conflict(candidate_id: str, entry: dict[str, Any]) -> bool:
        """True when both labels are numeric-parsed yet fully disagree.

        A TOC numeric label must never be applied to a body heading carrying a
        different namespace or number: that would forge a hierarchy the source
        does not support. Missing parsed numerics on either side stay eligible
        for the unlabeled exact-title fallback.
        """
        entry_numeric = entry.get("label_numeric")
        info = link_label_info_by_element[candidate_id]
        if not entry_numeric or not info.get("label_numeric"):
            return False
        return not (
            info.get("label_namespace") == entry.get("label_namespace")
            and info.get("label_numeric") == entry_numeric
        )

    def _apply_link(entry: dict[str, Any], body_element_id: str, basis: str) -> None:
        entry["body_element_id"] = body_element_id
        entry["status"] = "linked"
        entry["link_basis"] = basis
        physical_page = element_by_id[body_element_id].get("primary_page")
        printed_page = entry.get("printed_page")
        differs = None
        if isinstance(physical_page, int) and printed_page is not None:
            differs = str(physical_page) != str(printed_page)
        entry["printed_page_differs_from_physical"] = differs

    for entry in entries:
        title_key = canonical_key_text(entry["title"])
        candidates = [
            element_id_of(element)
            for element in link_candidates
            if title_key in link_keys_by_element[element_id_of(element)]
        ]
        entry["candidate_body_element_ids"] = candidates
        if len(candidates) == 1:
            candidate_id = candidates[0]
            if _label_conflict(candidate_id, entry):
                # The only title match contradicts the entry's qualified label;
                # linking would attach a label the body heading does not carry.
                entry["status"] = "unmatched"
                entry["ambiguity_reason"] = "label_conflict"
            else:
                _apply_link(entry, candidate_id, "exact_title")
        elif len(candidates) > 1:
            # Several headings share the bare title. Full qualification (the
            # namespace AND the numeric label) resolves a single exact match;
            # an exact unlabeled title pair is still a valid fallback, but two
            # identical qualified labels or a still-ambiguous title-only set
            # must remain ambiguous rather than pick arbitrarily.
            qualified = [
                candidate
                for candidate in candidates
                if _same_full_label(candidate, entry, title_key)
            ]
            if len(qualified) == 1:
                _apply_link(entry, qualified[0], "exact_qualified_title")
            elif len(qualified) > 1:
                entry["status"] = "ambiguous"
                entry["ambiguity_reason"] = "duplicate_exact_label"
            else:
                entry["status"] = "ambiguous"
                entry["ambiguity_reason"] = "title_only_fallback"
        else:
            entry["status"] = "unmatched"

    linked_entries = [entry for entry in entries if entry["status"] == "linked"]
    linked_heading_ids = {entry["body_element_id"] for entry in linked_entries}
    entry_by_linked_heading = {entry["body_element_id"]: entry for entry in linked_entries}
    toc_usable = bool(entries)

    # ---- section hypotheses ----
    sections: list[dict[str, Any]] = []
    section_by_heading_id: dict[str, str] = {}
    section_by_id: dict[str, dict[str, Any]] = {}
    for element in link_candidates:
        heading_id = element_id_of(element)
        label_numeric: str | None = None
        label_namespace: str | None = None
        label_kind: str | None = None
        if toc_usable and heading_id in linked_heading_ids:
            basis = "toc_entry_link"
            entry = entry_by_linked_heading[heading_id]
            level = None
            level_known = False
            label_numeric = entry.get("label_numeric")
            label_namespace = entry.get("label_namespace")
            label_kind = entry.get("label_kind")
            if label_kind in {"arabic", "dotted", "namespaced"} and label_numeric:
                # A namespace does not change the dotted depth; "AT 2" is a
                # root (level 1) and "AT 2.1" is one level below it.
                level, level_known = label_numeric.count(".") + 1, True
            uncertainty: list[str] = []
            if not level_known:
                uncertainty.append("level_and_parent_unknown")
            section = {
                "section_id": f"{document.get('document_id')}-S{len(sections) + 1:04d}",
                "heading_element_id": heading_id,
                "title": element_text(element),
                "level": level,
                "level_known": level_known,
                "parent_section_id": None,
                "parent_known": False,
                "basis": basis,
                "toc_entry_id": entry["entry_id"],
                "label_numeric": label_numeric,
                "label_namespace": label_namespace,
                "label_kind": label_kind,
                "uncertainty": uncertainty,
            }
        else:
            basis = "body_heading_unlisted" if toc_usable else "flat_fallback"
            level = None
            level_known = False
            uncertainty = ["level_and_parent_unknown"]
            # Body-label fallback: only when a TOC is usable (never a pure flat
            # fallback) and the heading itself carries a numeric label. This
            # keeps a legitimately unlisted "AT 2.1" heading in its own
            # namespace family without inventing hierarchy when no TOC exists.
            if toc_usable:
                label_info = split_leading_label(element_text(element))
                if (
                    label_info["label"] is not None
                    and label_info["label_kind"] in {"arabic", "dotted", "namespaced"}
                    and label_info["label_numeric"]
                ):
                    label_numeric = label_info["label_numeric"]
                    label_namespace = label_info["label_namespace"]
                    label_kind = label_info["label_kind"]
                    level = label_numeric.count(".") + 1
                    level_known = True
                    uncertainty = []
            section = {
                "section_id": f"{document.get('document_id')}-S{len(sections) + 1:04d}",
                "heading_element_id": heading_id,
                "title": element_text(element),
                "level": level,
                "level_known": level_known,
                "parent_section_id": None,
                "parent_known": False,
                "basis": basis,
                "toc_entry_id": None,
                "label_numeric": label_numeric,
                "label_namespace": label_namespace,
                "label_kind": label_kind,
                "uncertainty": uncertainty,
            }
        sections.append(section)
        section_by_heading_id[heading_id] = section["section_id"]
        section_by_id[section["section_id"]] = section

    # Numeric parent evidence only: never invent a root parent, never cross a
    # naming namespace, and never resolve a duplicate parent label first-wins.
    for section in sections:
        if not section["level_known"]:
            continue
        numeric = section.get("label_numeric") or ""
        if "." not in numeric:
            continue
        namespace = section.get("label_namespace")
        parent_numeric = numeric.rsplit(".", 1)[0]
        parent_matches = [
            candidate
            for candidate in sections
            if candidate["level_known"]
            and candidate.get("label_namespace") == namespace
            and candidate.get("label_numeric") == parent_numeric
        ]
        if len(parent_matches) == 1:
            section["parent_section_id"] = parent_matches[0]["section_id"]
            section["parent_known"] = True
        elif len(parent_matches) > 1:
            # A duplicate parent label must stay ambiguous, never first-wins.
            section["uncertainty"].append("parent_label_ambiguous")
        else:
            section["uncertainty"].append("parent_label_not_found")

    # ---- derived element membership: every element accounted for once ----
    membership_by_element: dict[str, dict[str, Any]] = {}
    counts = {"section": 0, "preamble": 0, "toc": 0, "layout": 0, "uncertain": 0}
    current_section_id: str | None = None
    for element in ordered:
        element_id = element_id_of(element)
        if element_id in toc_source_ids or element_id in toc_table_ids:
            category = "toc"
        elif is_layout(element):
            category = "layout"
        elif element_id in furniture_ids:
            # Derived recurring page furniture (even when a single DI role
            # called it a heading/title) is accounted for as layout.
            category = "layout"
        elif element_id in section_by_heading_id:
            current_section_id = section_by_heading_id[element_id]
            category = "section"
        elif element.get("element_type") == "table":
            category = "section" if current_section_id else "preamble"
        elif not element.get("comparison_eligible", True):
            category = "uncertain"
        else:
            category = "section" if current_section_id else "preamble"
        membership_by_element[element_id] = {
            "category": category,
            "section_id": current_section_id if category == "section" else None,
            "element_type": element.get("element_type"),
        }
        counts[category] += 1

    ambiguous = [entry["entry_id"] for entry in entries if entry["status"] == "ambiguous"]
    unmatched = [entry["entry_id"] for entry in entries if entry["status"] == "unmatched"]
    source_kinds = sorted({entry["source_kind"] for entry in entries})

    evidence = {
        "version": STRUCTURE_EVIDENCE_VERSION,
        "toc": {
            "detected": bool(entries),
            "usable": toc_usable,
            "heading_element_ids": [element_id_of(ordered[position]) for position in contents_positions],
            "source_kinds": source_kinds,
            "entry_count": len(entries),
            "linked_count": len(linked_entries),
            "ambiguous_count": len(ambiguous),
            "unmatched_count": len(unmatched),
            "entries": entries,
            "ambiguous_entry_ids": ambiguous,
            "unmatched_entry_ids": unmatched,
        },
        "links": [
            {
                "entry_id": entry["entry_id"],
                "body_element_id": entry["body_element_id"],
                "status": entry["status"],
                "candidate_body_element_ids": entry["candidate_body_element_ids"],
                "link_basis": entry["link_basis"],
                "ambiguity_reason": entry.get("ambiguity_reason"),
                "printed_page": entry["printed_page"],
                "physical_page": (
                    element_by_id[entry["body_element_id"]].get("primary_page")
                    if entry["body_element_id"] else None
                ),
                "printed_page_differs_from_physical": entry["printed_page_differs_from_physical"],
            }
            for entry in entries
        ],
        "sections": sections,
        "membership": {
            "by_element": membership_by_element,
            "counts": counts,
        },
        "furniture": {
            "version": derived_furniture.get("version"),
            "element_ids": list(derived_furniture.get("element_ids") or []),
            "by_element": derived_furniture.get("by_element") or {},
            "profile": derived_furniture.get("profile") or {},
            "limitations": list(derived_furniture.get("limitations") or []),
        },
        "diagnostics": {
            **diagnostics,
            "entry_count": len(entries),
            "linked_count": len(linked_entries),
            "ambiguous_count": len(ambiguous),
            "unmatched_count": len(unmatched),
            "section_count": len(sections),
            "membership_counts": counts,
            "toc_usable": toc_usable,
            "toc_table_ids": sorted(toc_table_ids),
            "furniture_version": derived_furniture.get("version"),
            "furniture_element_count": len(furniture_ids),
        },
        "limitations": [
            "toc_classification_is_a_hypothesis_not_authoritative_hierarchy",
            "printed_page_references_are_not_mapped_to_physical_pages",
            "levels_and_parents_are_unknown_without_dotted_numeric_labels",
            "duplicate_or_changed_titles_remain_ambiguous_or_unmatched",
            "repeated_titles_resolve_only_by_a_unique_full_qualified_label_namespace_and_numeric_or_an_unlabeled_exact_title",
            "duplicate_parent_labels_remain_ambiguous",
            "toc_tables_are_not_removed_from_comparison_in_this_layer",
            "membership_is_a_derived_mapping_not_a_content_rewrite",
            "derived_furniture_is_a_hypothesis_and_never_rewrites_raw_roles_text_types_ids_or_eligibility",
            "unheaded_toc_table_detection_requires_two_distinct_body_headings_and_a_local_preceding_position_no_universal_guarantee",
        ],
    }

    validate_structure_evidence(document, evidence)
    return evidence


def validate_structure_evidence(document: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Independent integrity checks for a structure-evidence layer."""
    require(isinstance(evidence, dict), "structure: evidence must be a mapping")
    require(
        evidence.get("version") == STRUCTURE_EVIDENCE_VERSION,
        "structure: unexpected structure evidence version",
    )
    toc = evidence.get("toc")
    require(isinstance(toc, dict), "structure: toc block missing")
    entries = toc.get("entries")
    require(isinstance(entries, list), "structure: toc.entries must be a list")

    entry_ids = [entry.get("entry_id") for entry in entries]
    require(len(entry_ids) == len(set(entry_ids)), "structure: duplicate toc entry_id")
    require(
        len(entry_ids) == toc.get("entry_count"),
        "structure: entry_count does not match entries",
    )

    document_element_ids = {element_id_of(element) for element in document["elements"]}
    document_by_id = {element_id_of(element): element for element in document["elements"]}
    for entry in entries:
        require(isinstance(entry.get("entry_id"), str), "structure: toc entry missing entry_id")
        require(entry.get("source_kind") in {"text", "table"}, "structure: unknown toc entry source_kind")
        for source_id in entry.get("source_element_ids") or []:
            require(source_id in document_element_ids, f"structure: unknown toc source element {source_id!r}")
        table_id = entry.get("source_table_id")
        if table_id is not None:
            require(table_id in document_element_ids, f"structure: unknown toc source table {table_id!r}")
            valid_cells = {
                str(cell.get("cell_id"))
                for cell in ((document_by_id[table_id].get("table") or {}).get("cells") or [])
            }
            for cell_id in entry.get("source_cell_ids") or []:
                require(cell_id in valid_cells, f"structure: unknown toc source cell {cell_id!r}")
        body_id = entry.get("body_element_id")
        if body_id is not None:
            require(body_id in document_element_ids, f"structure: unknown linked body element {body_id!r}")
        for candidate in entry.get("candidate_body_element_ids") or []:
            require(candidate in document_element_ids, f"structure: unknown link candidate {candidate!r}")

    links = evidence.get("links")
    require(isinstance(links, list), "structure: links must be a list")
    require(len(links) == len(entries), "structure: links must match the toc entries")
    known_entries = set(entry_ids)
    for link in links:
        require(isinstance(link, dict), "structure: link must be a mapping")
        require(link.get("entry_id") in known_entries, "structure: link references an unknown toc entry")
        link_body_id = link.get("body_element_id")
        if link_body_id is not None:
            require(link_body_id in document_element_ids, f"structure: unknown link body element {link_body_id!r}")

    sections = evidence.get("sections")
    require(isinstance(sections, list), "structure: sections must be a list")
    section_ids = [section.get("section_id") for section in sections]
    require(len(section_ids) == len(set(section_ids)), "structure: duplicate section_id")
    known_sections = set(section_ids)
    for section in sections:
        require(section.get("heading_element_id") in document_element_ids, "structure: unknown section heading")
        parent = section.get("parent_section_id")
        if parent is not None:
            require(parent in known_sections, f"structure: unknown parent section {parent!r}")

    membership = evidence.get("membership")
    require(isinstance(membership, dict), "structure: membership block missing")
    by_element = membership.get("by_element")
    require(isinstance(by_element, dict), "structure: membership.by_element must be a mapping")
    require(
        set(by_element) == document_element_ids,
        "structure: membership must account for every element exactly once",
    )
    for element_id, record in by_element.items():
        require(isinstance(record, dict), f"structure: membership record for {element_id!r} must be a mapping")
        category = record.get("category")
        require(
            category in {"section", "preamble", "toc", "layout", "uncertain"},
            f"structure: unknown membership category {category!r}",
        )
        section_id = record.get("section_id")
        if section_id is not None:
            require(section_id in known_sections, f"structure: membership references unknown section {section_id!r}")

    declared_counts = membership.get("counts")
    require(isinstance(declared_counts, dict), "structure: membership.counts must be a mapping")
    recomputed_counts = {"section": 0, "preamble": 0, "toc": 0, "layout": 0, "uncertain": 0}
    for record in by_element.values():
        recomputed_counts[record["category"]] += 1
    require(
        declared_counts == recomputed_counts,
        "structure: membership counts do not match the membership records",
    )

    furniture = evidence.get("furniture")
    require(isinstance(furniture, dict), "structure: furniture block missing")
    require(
        furniture.get("version") == DERIVED_FURNITURE_VERSION,
        "structure: unexpected derived furniture version",
    )
    furniture_ids = furniture.get("element_ids")
    require(isinstance(furniture_ids, list), "structure: furniture.element_ids must be a list")
    require(
        len(furniture_ids) == len(set(furniture_ids)),
        "structure: duplicate furniture element_id",
    )
    furniture_by_element = furniture.get("by_element")
    require(
        isinstance(furniture_by_element, dict),
        "structure: furniture.by_element must be a mapping",
    )
    require(
        set(furniture_ids) == set(furniture_by_element),
        "structure: furniture element_ids must match furniture by_element",
    )
    heading_ids = {section["heading_element_id"] for section in sections}
    for element_id, record in furniture_by_element.items():
        require(
            element_id in document_element_ids,
            f"structure: unknown furniture element {element_id!r}",
        )
        require(
            isinstance(record, dict),
            f"structure: furniture record for {element_id!r} must be a mapping",
        )
        require(
            record.get("classification") == "furniture",
            f"structure: furniture record for {element_id!r} must be a furniture classification",
        )
        require(
            element_id not in heading_ids,
            f"structure: derived furniture {element_id!r} must not be a section heading",
        )
    for element_id in by_element:
        if element_id in furniture_by_element:
            require(
                by_element[element_id]["category"] == "layout",
                "structure: derived furniture membership must be accounted for as layout",
            )
