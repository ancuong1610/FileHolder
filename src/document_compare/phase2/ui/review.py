"""Section tree and old/new page viewer. Plain Streamlit widgets, API client only.

Result text is only ever shown through escaped plain markdown (``_plain``). The only
``unsafe_allow_html`` use is one fixed, static style block for the tree buttons;
no result text ever reaches it. Nothing here is logged.
"""
from __future__ import annotations

import inspect
import json
import re
import struct
import zlib
from collections.abc import Callable
from typing import Any

import streamlit as st

from document_compare.phase2.ui import texts
from document_compare.phase2.ui.api_client import ApiError

TYPE_OPTIONS = {"All": "all", "Added": "ADDED", "Deleted": "DELETED", "Modified": "MODIFIED", "Review": "REVIEW"}
KIND_OPTIONS = {"All": "all", "Change": "change", "Table": "table", "Move": "move"}
TYPE_COLORS = {"ADDED": "green", "DELETED": "red", "MODIFIED": "orange", "REVIEW": "violet", "MOVED": "orange"}
IMPACT_COLORS = {"content": "blue", "editorial": "gray", "moved": "orange"}
TREE_CSS = (
    "<style>"
    # one-line rows: the row is a keyed container holding [button | right-aligned pill]
    "[class*='st-key-row'] [data-testid='stHorizontalBlock']"
    "{flex-wrap:nowrap !important;gap:0.25rem !important;align-items:center !important;}"
    "[class*='st-key-row'] [data-testid='stColumn']{min-width:0 !important;}"
    "[class*='st-key-row'] [data-testid='stColumn']:first-child{flex:1 1 0 !important;width:auto !important;}"
    "[class*='st-key-row'] [data-testid='stColumn']:last-child{flex:0 0 auto !important;width:auto !important;"
    "text-align:right;}"
    "[class*='st-key-row'] [data-testid='stMarkdownContainer']{margin:0 !important;}"
    "[class*='st-key-row'] [data-testid='stMarkdownContainer'] p{margin:0 !important;font-size:0.8rem;"
    "line-height:1.2;white-space:nowrap;}"
    "[class*='st-key-pick'] button,[class*='st-key-tog'] button"
    "{justify-content:flex-start !important;text-align:left !important;width:100%;min-height:1.6rem;"
    "padding:2px 6px !important;overflow:hidden;border:none;background:transparent;font-size:0.9rem;}"
    "[class*='st-key-pick'] button > div,[class*='st-key-tog'] button > div"
    "{justify-content:flex-start !important;text-align:left !important;min-width:0;max-width:100%;}"
    "[class*='st-key-pick'] button p,[class*='st-key-tog'] button p"
    "{text-align:left !important;white-space:nowrap !important;overflow:hidden !important;"
    "text-overflow:ellipsis !important;display:block;margin:0;font-size:0.9rem;}"
    "[class*='st-key-pick'] button:hover,[class*='st-key-tog'] button:hover"
    "{background:rgba(128,128,128,0.12) !important;}"
    "[class*='st-key-ind']{margin-left:16px;padding-left:0;width:calc(100% - 16px) !important;"
    "border-left:1px solid rgba(128,128,128,0.45);}"
    "[class*='st-key-ind'],[class*='st-key-treebox']{gap:0.1rem !important;}"
    "[class*='st-key-pick'] button[kind='primary']{background:rgba(59,130,246,0.18) !important;"
    "border:1px solid rgb(59,130,246) !important;color:inherit !important;}"
    "[class*='st-key-hdrline'] p{font-size:1.1rem;margin-bottom:0.1rem;}"
    "</style>"
)
TREE_HEIGHT = 700
UNPLACED_ID = "__unplaced__"
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$&:])")
_DEFAULT_PAGE = (612, 792)
ORDER_OPTIONS = list(texts.ORDER_LABELS)
_LABEL_PREFIX = re.compile(r"^\s*(?:(?i:section|article|chapter|part|abschnitt|artikel|kapitel|teil)\s+|§\s*)?")
_SECTION_LABEL = re.compile(
    r"(?:(?P<namespace>[A-Z]+)\s+(?=\d))?"
    r"(\d+|[A-Z]+(?=[.)]|$)|[IVXLCDM]+|[A-Z])"
    r"((?:\.(?:\d+|[A-Z]+))*)(?=$|[\s.)\u2013\u2014:-])"
)
_ROMAN = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
_ROMAN_VALUES = dict(zip("IVXLCDM", (1, 5, 10, 50, 100, 500, 1000)))
_TABLE_LABEL = re.compile(r"Table(?: · p\.(\d+))?(?: · (\d+) cells? changed)?")
CHEVRON_CLOSED, CHEVRON_OPEN, NO_CHEVRON = "\u203a\u2002", "\u2304\u2002", "\u2003"


def _plain(value: Any) -> str:
    """Escape markdown so result text renders literally."""
    return _MD_SPECIAL.sub(r"\\\1", str(value if value is not None else "")).replace("\n", " ")


def _key(client) -> str:
    return str(getattr(client, "cache_key", "default"))


@st.cache_data(show_spinner=False, max_entries=64)
def _outline(_client, cache_key: str, job_id: str) -> dict:
    return _client.outline(job_id)


@st.cache_data(show_spinner=False, max_entries=256)
def _details(_client, cache_key: str, job_id: str, kind: str, record_id: str) -> dict:
    return _client.details(job_id, kind, record_id)


@st.cache_data(show_spinner=False, max_entries=256)
def _locations(_client, cache_key: str, job_id: str, kind: str, record_id: str) -> dict:
    return _client.locations(job_id, kind, record_id)


@st.cache_data(show_spinner=False, max_entries=64)
def _page_png(_client, cache_key: str, job_id: str, side: str, page: int, kind: str | None,
              record_id: str | None, uncertain: bool) -> bytes | None:
    return _client.page_png(job_id, side, page, kind=kind, record_id=record_id, uncertain=uncertain)


def _show_image(data: bytes) -> None:
    """Stretch to the column; old Streamlit takes use_container_width, new takes width."""
    try:
        try:
            st.image(data, width="stretch")
        except TypeError:
            st.image(data, use_container_width=True)
        except st.errors.StreamlitAPIException:
            st.image(data, use_container_width=True)
    except Exception:  # undecodable bytes: degrade, never crash or log them
        st.warning(texts.PAGE_DISPLAY_FAILED)


_BUTTON_FILL = ({"width": "stretch"} if "width" in inspect.signature(st.button).parameters
                else {"use_container_width": True})


def target_page(side_info: dict, other_side: dict) -> int | None:
    """This side's primary page, else the other side's page clamped to this side's page count."""
    own = (side_info or {}).get("primary_page")
    if isinstance(own, int) and not isinstance(own, bool):
        return own
    other = (other_side or {}).get("primary_page")
    count = (side_info or {}).get("page_count")
    if isinstance(other, int) and isinstance(count, int) and count >= 1:
        return max(1, min(other, count))
    return None


# --------------------------------------------------------------------------- outline helpers (pure)

def bucket(item: dict) -> str:
    """Change type for change/table items and the display type for review/move items."""
    if item.get("kind") == "review":
        return "REVIEW"
    if item.get("kind") == "move":
        return "MOVED"
    ctype = str(item.get("change_type") or "").upper()
    if "ADDED" in ctype:
        return "ADDED"
    if "DELETED" in ctype or "REMOVED" in ctype:
        return "DELETED"
    return "MODIFIED"


def _fold(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _matches(item: dict, query: str) -> bool:
    """Query hits the item's record ID (e.g. CHANGE-000001 quoted by the chat), its label or any
    title on its section path. Punctuation a chat answer wraps around an ID is ignored."""
    record_id = _fold(item.get("record_id"))
    bare = query.strip(" `'\"()[].,;:")
    if record_id and bare and bare in record_id:
        return True
    label = _move_label(item) if item.get("kind") == "move" else item.get("label")
    return query in _fold(label) or any(query in _fold(t) for t in item.get("section_path") or [])


def _is_flagged(item: dict) -> bool:
    return bool(item.get("needs_review")) or item.get("kind") == "review"


def _impact(item: dict) -> dict:
    impact = item.get("impact")
    return impact if isinstance(impact, dict) else {}


def _filter_node(node: dict, passes: Callable[[dict], bool], query: str, inherited: bool = False) -> dict | None:
    """Filtered copy of a section; None when a search leaves nothing of it. A section whose own
    title matches the query stays with all its Type/Kind-matching items and descendants."""
    hit = inherited or (bool(query) and query in _fold(node.get("title")))
    items = [i for i in node.get("items") or [] if (not query or hit or _matches(i, query)) and passes(i)]
    kids = (_filter_node(c, passes, query, hit) for c in node.get("children") or [])
    children = [c for c in kids if c]
    if query and not hit and not items and not children:
        return None
    return dict(node, items=items, children=children,
                change_count=len(items) + sum(c["change_count"] for c in children))


def filter_outline(outline: dict, kind: str = "all", ctype: str = "all", query: str = "",
                   flagged_only: bool = False, impact: str = "all", show_local_reorders: bool = False) -> dict:
    """Filter the loaded outline and recompute counts for the visible findings.

    An item matches a query through its label or any title on its section path. Standalone review
    items live in the tree and unplaced list; the compatibility ``review`` list is never shown."""
    query = _fold(query)
    excluded_unknown_impact = 0

    def passes(item: dict) -> bool:
        nonlocal excluded_unknown_impact
        item_kind = item.get("kind")
        item_impact = _impact(item)
        if item_kind not in ("change", "table", "review", "move"):
            return False
        if kind != "all" and item_kind != kind:
            return False
        if flagged_only and not _is_flagged(item):
            return False
        if ctype != "all" and bucket(item) != ctype:
            return False
        if item_kind == "move" and not show_local_reorders and "local_reorder" in (item_impact.get("kinds") or []):
            return False
        if impact != "all" and item_impact.get("level") != impact:
            if not item_impact.get("level"):
                excluded_unknown_impact += 1
            return False
        return True

    sections = [n for n in (_filter_node(n, passes, query) for n in outline.get("sections") or []) if n]
    unplaced = [i for i in outline.get("unplaced") or []
                if (not query or _matches(i, query)) and passes(i)]
    result = {"sections": sections, "unplaced": unplaced}
    if impact != "all":
        result["excluded_unknown_impact"] = excluded_unknown_impact
    return result


def _section_label(node: dict) -> tuple[str | None, list[str]]:
    title = _LABEL_PREFIX.sub("", str(node.get("title") or ""), count=1)
    match = _SECTION_LABEL.match(title)
    if match is None:
        return None, []
    namespace, head, tail = match.groups()
    return namespace, [head, *tail.split(".")[1:]]


def _label_token(token: str, roman_family: bool) -> tuple[int, int | str]:
    if token.isdecimal():
        return 0, int(token)
    if (roman_family or len(token) > 1) and _ROMAN.fullmatch(token):
        values = [_ROMAN_VALUES[letter] for letter in token]
        return 0, values[-1] + sum(-a if a < b else a for a, b in zip(values, values[1:]))
    return 1, token.casefold()


def _section_keys(sections: list[dict]) -> list[tuple]:
    labels = [_section_label(node) for node in sections]
    # Single letters such as C can also be Roman numerals. Resolve them as Roman
    # when the alphabetic heads of this sibling group all fit that convention.
    heads = [label[0] for namespace, label in labels if not namespace and label and label[0].isalpha()]
    roman_family = bool(heads) and all(_ROMAN.fullmatch(head) for head in heads)
    keys = []
    for node, (namespace, label) in zip(sections, labels):
        rank = node.get("toc_rank")
        if isinstance(rank, int) and not isinstance(rank, bool) and rank >= 0:
            keys.append((0, rank))
        elif label:
            tokens = tuple(_label_token(token, roman_family) for token in label)
            if namespace:
                tokens = ((1, namespace.casefold()), *tokens)
            keys.append((1, tokens))
        else:
            keys.append((2,))
    return keys


def with_title_root(outline: dict) -> dict:
    """Display inference for a flat outline: an unnumbered first section is the document title,
    so it becomes the root and every other top-level section moves under it. Real hierarchy
    (any node with children) is left alone. The API payload itself is never changed."""
    sections = outline.get("sections") or []
    if len(sections) < 2 or any(n.get("children") for n in sections):
        return outline
    first = sections[0]
    if first.get("toc_rank") is not None or _section_label(first)[1]:
        return outline
    rest = sections[1:]
    root = dict(first, children=list(rest),
                change_count=first.get("change_count", 0) + sum(n.get("change_count", 0) for n in rest))
    return dict(outline, sections=[root])


def order_sections(sections: list[dict], mode: str) -> list[dict]:
    """Sort siblings by TOC rank, then natural label, then stable reading order."""
    out = [dict(n, children=order_sections(n.get("children") or [], mode)) for n in sections]
    if mode == "Section number":
        out = [node for _, node in sorted(zip(_section_keys(out), out), key=lambda pair: pair[0])]
    return out


def numbers_ascending(sections: list[dict]) -> bool:
    """True when every sibling group already follows the section ordering key."""
    keys = _section_keys(sections)
    return keys == sorted(keys) and all(numbers_ascending(n.get("children") or []) for n in sections)


def _flatten_node(node: dict) -> list[dict]:
    out = list(node.get("items") or [])
    for child in node.get("children") or []:
        out.extend(_flatten_node(child))
    return out


def flatten(outline: dict) -> list[dict]:
    """Every shown item in tree order: sections (own items before children), then unplaced."""
    out: list[dict] = []
    for node in outline.get("sections") or []:
        out.extend(_flatten_node(node))
    out.extend(outline.get("unplaced") or [])
    return out


def chip_counts(outline: dict) -> dict[str, int]:
    visible = flatten(outline)
    items = [i for i in visible if i.get("kind") in ("change", "table")]
    return {
        "Changes": sum(1 for i in items if i["kind"] == "change"),
        "Tables": sum(1 for i in items if i["kind"] == "table"),
        "Added": sum(1 for i in items if bucket(i) == "ADDED"),
        "Deleted": sum(1 for i in items if bucket(i) == "DELETED"),
        "Modified": sum(1 for i in items if bucket(i) == "MODIFIED"),
        "Needs review": sum(1 for i in visible if _is_flagged(i)),
        **{level: sum(1 for i in visible if _impact(i).get("level") == level) for level in IMPACT_COLORS},
    }


def _item_page(item: dict) -> int | None:
    order = ("page_old", "page_new") if bucket(item) == "DELETED" else ("page_new", "page_old")
    for name in order:
        page = item.get(name)
        if isinstance(page, int) and not isinstance(page, bool):
            return page
    return None


def _png_size(data: bytes | None) -> tuple[int, int]:
    if data and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if 0 < width <= 8192 and 0 < height <= 8192:
            return width, height
    return _DEFAULT_PAGE


def _placeholder_png(width: int, height: int) -> bytes:
    """Flat light-grey PNG of the given size (stdlib only), used when a side has no page."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    row = b"\x00" + b"\xf2" * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height, 1)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- selection

def _select(item: dict) -> None:
    st.session_state["selected"] = {"kind": item["kind"], "record_id": item["record_id"]}


def _same(item: dict, selected: dict | None) -> bool:
    return bool(selected) and (item["kind"], item["record_id"]) == (selected.get("kind"), selected.get("record_id"))


# --------------------------------------------------------------------------- header and filters

def _header(status: dict, job_id: str, outline: dict) -> None:
    docs = outline.get("documents") or {}

    def line(label: str, name: str, side: str) -> str:
        count = (docs.get(side) or {}).get("page_count")
        template = texts.PAGE_COUNT_ONE if count == 1 else texts.PAGE_COUNT_MANY
        pages = template.format(count=count) if isinstance(count, int) else ""
        return f"{label}: **{_plain(name)}**{pages}"

    old = line(texts.SIDE_LABELS["old"], str(status.get("old_name") or texts.OLD_PDF_NAME), "old")
    new = line(texts.SIDE_LABELS["new"], str(status.get("new_name") or texts.NEW_PDF_NAME), "new")
    with st.container(key="hdrline"):
        st.markdown(f"{old}   |   {new}")
    st.caption(texts.KEEP_JOB_ID)
    st.code(job_id, language=None)  # copy button: reopen or fetch this result later


def _chips(counts: dict[str, int]) -> None:
    colors = {"Changes": "blue", "Tables": "gray", "Added": "green", "Deleted": "red", "Modified": "orange",
              "Needs review": "violet", **IMPACT_COLORS}
    labels = {**texts.CHIP_LABELS, **texts.IMPACT_LABELS}
    st.markdown(" ".join(f":{colors[k]}-badge[{labels[k]} {v}]" for k, v in counts.items()))


def _filters() -> tuple[str, str, str, bool, str, bool]:
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
    ctype = c1.selectbox(texts.TYPE_FILTER, list(TYPE_OPTIONS), key="f_type",
                         format_func=lambda option: texts.TYPE_LABELS[TYPE_OPTIONS[option]])
    kind = c2.selectbox(texts.KIND_FILTER, list(KIND_OPTIONS), key="f_kind",
                        format_func=lambda option: texts.KIND_LABELS[KIND_OPTIONS[option]])
    impact = c3.selectbox(texts.IMPACT_FILTER, list(texts.IMPACT_LABELS), key="f_impact",
                          format_func=texts.IMPACT_LABELS.__getitem__)
    query = c4.text_input(texts.SEARCH, key="f_search", placeholder=texts.SEARCH_HINT)
    c5, c6 = st.columns(2)
    flagged = c5.checkbox(texts.FLAGGED_ONLY, key="f_needs_review", value=False)
    local = c6.checkbox(texts.LOCAL_REORDERS, key="f_local_reorders", value=False)
    return KIND_OPTIONS[kind], TYPE_OPTIONS[ctype], query, flagged, impact, local


# --------------------------------------------------------------------------- tree

def _line(row_key: str):
    """One tree row: a keyed container holding [button | right-aligned pill]."""
    box = st.container(key=f"row::{row_key}")
    with box:
        return st.columns([1, 0.001], gap="small", vertical_alignment="center")


def _impact_badges(item: dict) -> str:
    impact = _impact(item)
    level = impact.get("level")
    if level not in IMPACT_COLORS:
        return ""
    badge = f":{IMPACT_COLORS[level]}-badge[{texts.IMPACT_LABELS[level]}]"
    if level == "content" and impact.get("priority") == "high":
        badge += f" :red-badge[{texts.IMPORTANT}]"
    return badge


def _move_label(item: dict) -> str:
    return texts.MOVE_LABEL.format(
        text=item.get("label") or item["record_id"],
        source=item.get("from_anchor") or texts.UNKNOWN_ANCHOR,
        destination=item.get("to_anchor") or texts.UNKNOWN_ANCHOR,
    )


def _row(item: dict, selected: dict | None) -> None:
    page = _item_page(item)
    label = item.get("label") or item.get("record_id")
    if item.get("kind") == "review":
        label = texts.REVIEW_REASONS.get(label, label)
    elif item.get("kind") == "table" and (match := _TABLE_LABEL.fullmatch(label)):
        table_page, cells = match.groups()
        parts = [texts.KIND_LABELS["table"]]
        if table_page:
            parts.append(texts.PAGE_SHORT.format(page=table_page))
        if cells:
            cell_text = texts.TABLE_CELLS_ONE if cells == "1" else texts.TABLE_CELLS_MANY
            parts.append(cell_text.format(count=cells))
        label = " · ".join(parts)
    left, right = _line(f"{item['kind']}::{item['record_id']}")
    tag = f" :violet[{texts.TYPE_LABELS['REVIEW']}]" if _is_flagged(item) else ""
    badges = _impact_badges(item)
    if item.get("kind") == "move":
        label = _move_label(item)
        button_label = f"{badges or texts.TYPE_LABELS['MOVED']}\\: {_plain(label)}"
    else:
        button_label = f":{TYPE_COLORS[bucket(item)]}[●]{tag} {badges} {_plain(label)}"
    left.button(button_label,
                key=f"pick::{item['kind']}::{item['record_id']}",
                on_click=_select, args=(item,), help=str(label),
                type="primary" if _same(item, selected) else "secondary", **_BUTTON_FILL)
    if page is not None:
        right.markdown(f":gray[{texts.PAGE_SHORT.format(page=page)}]")


def _open_set() -> set:
    opened = st.session_state.get("tree_open")
    if not isinstance(opened, set):
        opened = st.session_state["tree_open"] = set()
    return opened


def _toggle(node_id: str) -> None:
    opened = _open_set()
    opened.symmetric_difference_update({node_id})


def path_ids(filtered: dict, selected: dict | None) -> list[str]:
    """Section ids from a root down to the section holding the selected item ([] when none)."""
    def walk(node: dict, trail: list[str]) -> list[str] | None:
        here = trail + [node["section_id"]]
        if any(_same(i, selected) for i in node.get("items") or []):
            return here
        for child in node.get("children") or []:
            found = walk(child, here)
            if found:
                return found
        return None

    for node in filtered.get("sections") or []:
        found = walk(node, [])
        if found:
            return found
    if any(_same(i, selected) for i in filtered.get("unplaced") or []):
        return [UNPLACED_ID]
    return []


def _caret_row(node_id: str, title: str, count: int, expandable: bool, opened: bool) -> None:
    """Confluence-style row: fixed-width chevron slot, one-line title, muted count pill at the right."""
    caret = (CHEVRON_OPEN if opened else CHEVRON_CLOSED) if expandable else NO_CHEVRON
    shown = _plain(title)
    left, right = _line(f"tog::{node_id}")
    left.button(f"{caret}{shown}" if count else f"{caret}:gray[{shown}]", key=f"tog::{node_id}", on_click=_toggle,
                args=(node_id,), help=str(title), type="tertiary", disabled=not expandable, **_BUTTON_FILL)
    right.markdown(f":gray-badge[{count}]" if count else ":gray[0]")


def _tree_node(node: dict, selected: dict | None, opened: set) -> None:
    items, children = node.get("items") or [], node.get("children") or []
    expandable = bool(items or children)
    is_open = node["section_id"] in opened
    _caret_row(node["section_id"], node.get("title") or texts.UNTITLED, node["change_count"], expandable, is_open)
    if expandable and is_open:
        with st.container(key=f"ind::{node['section_id']}"):
            for item in items:
                _row(item, selected)
            for child in children:
                _tree_node(child, selected, opened)


def _order_control() -> str:
    return st.radio(texts.ORDER, ORDER_OPTIONS, key="tree_order", horizontal=True,
                    label_visibility="collapsed", format_func=texts.ORDER_LABELS.__getitem__)


def _tree(filtered: dict, selected: dict | None, has_sections: bool, mode: str) -> None:
    st.markdown(f"**{texts.STRUCTURE}**")
    if mode == ORDER_OPTIONS[0] and not numbers_ascending(filtered["sections"]):
        st.caption(texts.ORDER_NOTE)
    opened = _open_set()
    with st.container(height=TREE_HEIGHT):
        with st.container(key="treebox"):
            if not filtered["sections"] and not filtered["unplaced"]:
                st.caption(texts.NO_MATCHING_SECTIONS if has_sections
                           else texts.NO_SECTION_STRUCTURE)
            for node in filtered["sections"]:
                _tree_node(node, selected, opened)
            if filtered["unplaced"]:
                _caret_row(UNPLACED_ID, texts.UNPLACED, len(filtered["unplaced"]), True, UNPLACED_ID in opened)
                if UNPLACED_ID in opened:
                    with st.container(key=f"ind::{UNPLACED_ID}"):
                        for item in filtered["unplaced"]:
                            _row(item, selected)


# --------------------------------------------------------------------------- viewer

def _breadcrumb(item: dict) -> str:
    path = item.get("section_path") or []
    where = " › ".join(_plain(p) for p in path) if path else texts.UNPLACED
    ctype = bucket(item)
    badges = _impact_badges(item)
    if ctype == "MOVED" and badges:
        return f"{where}   {badges}"
    return f"{where}   :{TYPE_COLORS[ctype]}[{texts.TYPE_LABELS[ctype]}] {badges}"


def _llm_opinion(opinion: Any) -> None:
    """Compact, advisory LLM opinion box (variant A); nothing when the LLM did not look at it."""
    if not isinstance(opinion, dict) or not opinion.get("outcome"):
        return
    outcome = texts.LLM_OUTCOMES.get(opinion["outcome"], texts.UNKNOWN_LLM_OUTCOME)
    with st.container(border=True, key="llm_opinion"):
        st.caption(f":material/auto_awesome: {texts.LLM_OPINION}")
        reason = _plain(opinion.get("reason") or texts.NO_LLM_REASON)
        st.markdown(f":violet-badge[{_plain(outcome)}] {reason}")
        meta = [opinion.get("evaluator") or texts.NO_EVALUATOR]
        refs = [r for r in opinion.get("evidence_refs") or [] if isinstance(r, str)]
        if refs:
            meta.append(texts.EVIDENCE_REFS.format(refs=", ".join(refs[:6]) + (" …" if len(refs) > 6 else "")))
        st.caption(_plain(" · ".join(meta)))


def _viewer(client, job_id: str, item: dict, flat: list[dict]) -> None:
    kind, record_id, ck = item["kind"], item["record_id"], _key(client)
    st.markdown(_breadcrumb(item))
    if kind == "move":
        st.caption(_plain(_move_label(item)))
    impact = _impact(item)
    if impact.get("level") == "content" and impact.get("priority") == "high":
        signals = [texts.IMPACT_SIGNALS[s] for s in impact.get("signals") or [] if s in texts.IMPACT_SIGNALS]
        if signals:
            st.caption(" · ".join(signals))
    if _is_flagged(item):
        reason = texts.REVIEW_REASONS.get(item.get("review_reason"), texts.NO_REVIEW_REASON)
        st.markdown(f":violet[{texts.REVIEW_NOTE.format(reason=_plain(reason))}]")
    _llm_opinion(item.get("llm_opinion"))
    st.caption(texts.LEGEND)
    try:
        details = _details(client, ck, job_id, kind, record_id)
    except ApiError as exc:
        st.error(texts.DETAILS_FAILED.format(error=texts.api_error(exc.code, exc.status)))
        return
    payload = details.get(kind) or {}
    try:
        located = _locations(client, ck, job_id, kind, record_id)
    except ApiError as exc:
        st.warning(texts.NO_LOCATIONS.format(error=texts.api_error(exc.code, exc.status)))
        located = {}
    sides = (located or {}).get("sides") or {}
    old_side, new_side = sides.get("old") or {}, sides.get("new") or {}
    state = payload.get("result_state") or (payload.get("resolution") or {}).get("result_state")
    uncertain = state == "uncertain" or _is_flagged(item)
    infos = {"old": old_side, "new": new_side}
    other_of = {"old": new_side, "new": old_side}
    targets: dict[str, int | None] = {}
    context: dict[str, bool] = {}
    for name in ("old", "new"):
        # a side with no element of this finding shows its section's page, never a highlight
        context[name] = bool(item.get(f"context_{name}")) or infos[name].get("counterpart") == "none"
        page = item.get(f"page_{name}")
        if context[name] and isinstance(page, int) and not isinstance(page, bool):
            targets[name] = page
        else:
            targets[name] = target_page(infos[name], other_of[name])

    images: dict[str, bytes | None] = {}
    source_missing = False
    for side, page in targets.items():
        images[side] = None
        if page is None:
            continue
        try:
            if context[side]:
                images[side] = _page_png(client, ck, job_id, side, page, None, None, False)
            else:
                images[side] = _page_png(client, ck, job_id, side, page, kind, record_id, uncertain)
        except ApiError as exc:
            st.warning(texts.RENDER_FAILED.format(side=texts.SIDE_LABELS[side], error=texts.api_error(exc.code, exc.status)))
            continue
        source_missing = source_missing or images[side] is None

    shown = [data for data in images.values() if data]
    size = _png_size(shown[0]) if shown else _DEFAULT_PAGE
    cols = st.columns(2)
    for col, side in zip(cols, ("old", "new")):
        with col:
            page, data = targets[side], images[side]
            name = texts.SIDE_LABELS[side]
            if data:
                _show_image(data)
                st.caption(texts.PAGE.format(side=name, page=page) + (texts.SECTION_CONTEXT if context[side] else ""))
            else:
                _show_image(_placeholder_png(*size))
                if page is None:
                    st.caption(texts.NO_PAGE_LOCATION.format(side=name))
                else:
                    st.caption(texts.PAGE_IMAGE_UNAVAILABLE.format(side=name, page=page))
    if source_missing:
        st.info(texts.SOURCE_PDF_UNAVAILABLE)

    ids = [(i["kind"], i["record_id"]) for i in flat]
    index = ids.index((kind, record_id)) if (kind, record_id) in ids else 0
    prev_col, next_col = st.columns(2, gap="small", width=260)
    prev_col.button(texts.PREVIOUS, key="prev_finding", disabled=index <= 0, on_click=_select,
                    args=(flat[max(index - 1, 0)],), **_BUTTON_FILL)
    next_col.button(texts.NEXT, key="next_finding", disabled=index >= len(flat) - 1, on_click=_select,
                    args=(flat[min(index + 1, len(flat) - 1)],), **_BUTTON_FILL)
    st.caption(texts.CHANGE_ID.format(record_id=record_id))


SPLIT_MIN_PX = 260
SPLIT_MAX_SHARE = 0.65
SPLITTER_JS = """
<script>
(function () {
  var host = window.parent, doc = host.document;
  if (host.__dcSplitInstalled) return;
  host.__dcSplitInstalled = true;
  var MIN = %(min)d, MAX_SHARE = %(max)s, KEY = 'dc_tree_width';
  function saved() { try { return parseInt(host.localStorage.getItem(KEY), 10) || null; } catch (e) { return null; } }
  function store(w) { try { if (w) host.localStorage.setItem(KEY, String(w)); else host.localStorage.removeItem(KEY); } catch (e) {} }
  function parts() {
    var block = doc.querySelector('.st-key-split [data-testid="stHorizontalBlock"]');
    if (!block || block.children.length < 2) return null;
    return { block: block, left: block.children[0], right: block.children[1] };
  }
  function stacked(p) { return host.getComputedStyle(p.block).flexDirection.indexOf('column') === 0 || p.block.clientWidth < 700; }
  function clamp(p, w) { return Math.max(MIN, Math.min(Math.round(w), Math.round(p.block.clientWidth * MAX_SHARE))); }
  function apply(p, w) {
    if (!w || stacked(p)) { p.left.style.flex = ''; p.left.style.maxWidth = ''; p.right.style.flex = ''; return; }
    w = clamp(p, w);
    p.left.style.flex = '0 0 ' + w + 'px'; p.left.style.maxWidth = w + 'px'; p.right.style.flex = '1 1 0';
  }
  function place(p) {
    var h = p.block.querySelector(':scope > .dc-split-handle');
    if (stacked(p)) { if (h) h.style.display = 'none'; return; }
    if (!h) {
      h = doc.createElement('div'); h.className = 'dc-split-handle';
      h.setAttribute('role', 'separator'); h.setAttribute('aria-orientation', 'vertical');
      h.setAttribute('aria-label', %(resize_structure)s); h.tabIndex = 0;
      h.title = %(resize_hint)s;
      h.addEventListener('mousedown', function (e) { start(e); });
      h.addEventListener('dblclick', function () { store(null); var q = parts(); if (q) { apply(q, null); place(q); } });
      h.addEventListener('keydown', function (e) {
        var q = parts(); if (!q || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
        var w = clamp(q, q.left.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? -24 : 24));
        store(w); apply(q, w); place(q); e.preventDefault();
      });
      p.block.style.position = 'relative'; p.block.appendChild(h);
    }
    h.style.display = '';
    var lr = p.left.getBoundingClientRect(), rr = p.right.getBoundingClientRect(), br = p.block.getBoundingClientRect();
    h.style.left = ((lr.right + rr.left) / 2 - br.left - 5) + 'px';
  }
  function start(e) {
    var p = parts(); if (!p) return; e.preventDefault();
    doc.body.style.userSelect = 'none'; doc.body.style.cursor = 'col-resize';
    function move(ev) { var q = parts(); if (!q) return; var w = clamp(q, ev.clientX - q.block.getBoundingClientRect().left); apply(q, w); place(q); store(w); }
    function up() { doc.removeEventListener('mousemove', move); doc.removeEventListener('mouseup', up); doc.body.style.userSelect = ''; doc.body.style.cursor = ''; }
    doc.addEventListener('mousemove', move); doc.addEventListener('mouseup', up);
  }
  var style = doc.createElement('style');
  style.textContent = '.dc-split-handle{position:absolute;top:0;bottom:0;width:10px;cursor:col-resize;z-index:5;}'
    + '.dc-split-handle::after{content:"";position:absolute;left:4px;top:0;bottom:0;width:2px;border-radius:1px;background:rgba(128,128,128,.25);}'
    + '.dc-split-handle:hover::after,.dc-split-handle:focus::after{background:rgba(59,130,246,.8);}'
    + '.dc-split-handle:focus{outline:none;}';
  doc.head.appendChild(style);
  var pending = false;
  function sync() { pending = false; var p = parts(); if (!p) return; apply(p, saved()); place(p); }
  new host.MutationObserver(function () { if (!pending) { pending = true; host.requestAnimationFrame(sync); } })
    .observe(doc.body, { childList: true, subtree: true });
  host.addEventListener('resize', function () { host.requestAnimationFrame(sync); });
  sync();
})();
</script>
""" % {
    "min": SPLIT_MIN_PX, "max": SPLIT_MAX_SHARE,
    "resize_structure": json.dumps(texts.RESIZE_STRUCTURE),
    "resize_hint": json.dumps(texts.RESIZE_HINT),
}


def _splitter() -> None:
    """Draggable divider between the structure panel and the viewer (width kept per browser)."""
    import streamlit.components.v1 as components
    components.html(SPLITTER_JS, height=0)


def render(client, job_id: str, status: dict | None = None) -> None:
    try:
        outline = _outline(client, _key(client), job_id)
    except ApiError as exc:
        st.error(texts.RESULT_FAILED.format(job_id=job_id, error=texts.api_error(exc.code, exc.status)))
        return
    st.markdown(TREE_CSS, unsafe_allow_html=True)
    _header(status or {}, job_id, outline)
    filters = _filters()
    shown = with_title_root(outline)
    root = shown["sections"][0]["section_id"] if shown is not outline else None
    if root and st.session_state.get("root_open") != (job_id, root):
        _open_set().add(root)  # the title root starts expanded, once per job
        st.session_state["root_open"] = (job_id, root)
    filtered = filter_outline(shown, *filters)
    excluded = filtered.get("excluded_unknown_impact", 0)
    if excluded:
        st.caption(texts.EXCLUDED_UNKNOWN_IMPACT.format(count=excluded))
    _chips(chip_counts(filtered))
    _splitter()
    left, right = st.container(key="split").columns([2, 3])
    with left:
        mode = _order_control()
    filtered["sections"] = order_sections(filtered["sections"], mode)
    flat = flatten(filtered)

    selected = st.session_state.get("selected")
    if flat and not any(_same(i, selected) for i in flat):
        _select(flat[0])  # default and fallback: first item in tree order
        selected = st.session_state["selected"]
    elif not flat:
        st.session_state.pop("selected", None)
        selected = None
    synced = (job_id, selected and selected.get("kind"), selected and selected.get("record_id"))
    if selected and st.session_state.get("tree_synced") != synced:
        _open_set().update(path_ids(filtered, selected))  # reveal the selected item
        st.session_state["tree_synced"] = synced

    with left:
        _tree(filtered, selected, bool(outline.get("sections")), mode)
    with right:
        current = next((i for i in flat if _same(i, selected)), None)
        if current is None:
            st.info(texts.NO_MATCHING_FINDINGS if flatten(outline) else texts.NO_FINDINGS)
        else:
            _viewer(client, job_id, current, flat)
