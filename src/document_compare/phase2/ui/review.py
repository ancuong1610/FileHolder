"""Section tree and old/new page viewer. Plain Streamlit widgets, API client only.

Result text is only ever shown through escaped plain markdown (``_plain``). The only
``unsafe_allow_html`` use is one fixed, static style block for the tree buttons;
no result text ever reaches it. Nothing here is logged.
"""
from __future__ import annotations

import inspect
import re
import struct
import zlib
from typing import Any

import streamlit as st

from document_compare.phase2.ui.api_client import ApiError

TYPE_OPTIONS = {"All": "all", "Added": "ADDED", "Deleted": "DELETED", "Modified": "MODIFIED", "Review": "REVIEW"}
KIND_OPTIONS = {"All": "all", "Change": "change", "Table": "table"}
TAGS = {"ADDED": ":green[ADDED]", "DELETED": ":red[DELETED]", "MODIFIED": ":orange[MODIFIED]",
        "REVIEW": ":violet[REVIEW]"}
LEGEND = ("Red = deleted, green = added, amber = modified, purple dashed = needs review "
          "(dashed outline = uncertain match).")
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
ORDER_OPTIONS = ["Document order", "Section number"]
ORDER_NOTE = "Sections appear in the order they occur in the new document."
_NUMBERED = re.compile(r"^\s*(section|article|chapter|part|\u00a7)?\s*\d", re.IGNORECASE)
_FIRST_INT = re.compile(r"\d+")
TAG_DOTS = {"ADDED": ":green[\u25cf]", "DELETED": ":red[\u25cf]", "MODIFIED": ":orange[\u25cf]", "REVIEW": ":violet[\u25cf]"}
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
        st.warning("This page image could not be displayed.")


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
    """ADDED / DELETED / MODIFIED for change and table items; REVIEW for standalone review items."""
    if item.get("kind") == "review":
        return "REVIEW"
    ctype = str(item.get("change_type") or "").upper()
    if "ADDED" in ctype:
        return "ADDED"
    if "DELETED" in ctype or "REMOVED" in ctype:
        return "DELETED"
    return "MODIFIED"


def _fold(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _matches(item: dict, query: str) -> bool:
    """Query hits the item's label or any title on its section path."""
    return query in _fold(item.get("label")) or any(query in _fold(t) for t in item.get("section_path") or [])


def _is_flagged(item: dict) -> bool:
    return bool(item.get("needs_review")) or item.get("kind") == "review"


def _passes(item: dict, kind: str, ctype: str, flagged_only: bool = False) -> bool:
    if item.get("kind") not in ("change", "table", "review"):
        return False
    if kind != "all" and item.get("kind") != kind:
        return False
    if flagged_only and not _is_flagged(item):
        return False
    return ctype == "all" or bucket(item) == ctype


def _filter_node(node: dict, kind: str, ctype: str, query: str, flagged: bool = False,
                 inherited: bool = False) -> dict | None:
    """Filtered copy of a section; None when a search leaves nothing of it. A section whose own
    title matches the query stays with all its Type/Kind-matching items and descendants."""
    hit = inherited or (bool(query) and query in _fold(node.get("title")))
    items = [i for i in node.get("items") or [] if _passes(i, kind, ctype, flagged) and (not query or hit or _matches(i, query))]
    kids = (_filter_node(c, kind, ctype, query, flagged, hit) for c in node.get("children") or [])
    children = [c for c in kids if c]
    if query and not hit and not items and not children:
        return None
    return dict(node, items=items, children=children,
                change_count=len(items) + sum(c["change_count"] for c in children))


def filter_outline(outline: dict, kind: str = "all", ctype: str = "all", query: str = "",
                   flagged_only: bool = False) -> dict:
    """Client-side Type/Kind/Search/Needs-review filter; counts are recomputed so they match what is shown.

    An item matches a query through its label or any title on its section path. Standalone review
    items live in the tree and unplaced list; the compatibility ``review`` list is never shown."""
    query = _fold(query)
    sections = [n for n in (_filter_node(n, kind, ctype, query, flagged_only) for n in outline.get("sections") or [])
                if n]
    unplaced = [i for i in outline.get("unplaced") or []
                if _passes(i, kind, ctype, flagged_only) and (not query or _matches(i, query))]
    return {"sections": sections, "unplaced": unplaced}


def _section_number(node: dict) -> int | None:
    found = _FIRST_INT.search(str(node.get("title") or ""))
    return int(found.group()) if found else None


def with_title_root(outline: dict) -> dict:
    """Display inference for a flat outline: an unnumbered first section is the document title,
    so it becomes the root and every other top-level section moves under it. Real hierarchy
    (any node with children) is left alone. The API payload itself is never changed."""
    sections = outline.get("sections") or []
    if len(sections) < 2 or any(n.get("children") for n in sections):
        return outline
    first = sections[0]
    if _NUMBERED.match(str(first.get("title") or "")):
        return outline
    rest = sections[1:]
    root = dict(first, children=list(rest),
                change_count=first.get("change_count", 0) + sum(n.get("change_count", 0) for n in rest))
    return dict(outline, sections=[root])


def order_sections(sections: list[dict], mode: str) -> list[dict]:
    """Sibling order at every level. 'Section number' sorts by the first integer in the title
    (natural: 2 < 10); siblings without a number keep their relative order, after numbered ones."""
    out = [dict(n, children=order_sections(n.get("children") or [], mode)) for n in sections]
    if mode == "Section number":
        out.sort(key=lambda n: (_section_number(n) is None, _section_number(n) or 0))
    return out


def numbers_ascending(sections: list[dict]) -> bool:
    """True when, among every group of siblings, the section numbers never go down."""
    nums = [n for n in map(_section_number, sections) if n is not None]
    return nums == sorted(nums) and all(numbers_ascending(n.get("children") or []) for n in sections)


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
    items = [i for i in flatten(outline) if i.get("kind") in ("change", "table")]
    return {
        "Changes": sum(1 for i in items if i["kind"] == "change"),
        "Tables": sum(1 for i in items if i["kind"] == "table"),
        "Added": sum(1 for i in items if bucket(i) == "ADDED"),
        "Deleted": sum(1 for i in items if bucket(i) == "DELETED"),
        "Modified": sum(1 for i in items if bucket(i) == "MODIFIED"),
        "Needs review": sum(1 for i in flatten(outline) if _is_flagged(i)),
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
        pages = f" · {count} page{'s' if count != 1 else ''}" if isinstance(count, int) else ""
        return f"{label}: **{_plain(name)}**{pages}"

    old = str(status.get("old_name") or "old.pdf")
    new = str(status.get("new_name") or "new.pdf")
    with st.container(key="hdrline"):
        st.markdown(f"{line('Old', old, 'old')}   |   {line('New', new, 'new')}")
    st.caption(f"Job ID: {job_id}")


def _chips(counts: dict[str, int]) -> None:
    colors = {"Changes": "blue", "Tables": "gray", "Added": "green", "Deleted": "red", "Modified": "orange",
              "Needs review": "violet"}
    st.markdown(" ".join(f":{colors[k]}-badge[{k} {v}]" for k, v in counts.items()))


def _filters() -> tuple[str, str, str, bool]:
    c1, c2, c3, c4 = st.columns([1, 1, 2, 1], vertical_alignment="bottom")
    ctype = c1.selectbox("Type", list(TYPE_OPTIONS), key="f_type")
    kind = c2.selectbox("Kind", list(KIND_OPTIONS), key="f_kind")
    query = c3.text_input("Search", key="f_search", placeholder="Search changes or sections")
    flagged = c4.checkbox("Needs review only", key="f_needs_review", value=False)
    return KIND_OPTIONS[kind], TYPE_OPTIONS[ctype], query, flagged


# --------------------------------------------------------------------------- tree

def _line(row_key: str):
    """One tree row: a keyed container holding [button | right-aligned pill]."""
    box = st.container(key=f"row::{row_key}")
    with box:
        return st.columns([1, 0.001], gap="small", vertical_alignment="center")


def _row(item: dict, selected: dict | None) -> None:
    page = _item_page(item)
    text = _plain(item.get("label") or item.get("record_id"))
    left, right = _line(f"{item['kind']}::{item['record_id']}")
    tag = " :violet[Review]" if _is_flagged(item) else ""
    left.button(f"{TAG_DOTS[bucket(item)]}{tag} {text}", key=f"pick::{item['kind']}::{item['record_id']}",
                on_click=_select, args=(item,), help=str(item.get("label") or item.get("record_id")),
                type="primary" if _same(item, selected) else "secondary", **_BUTTON_FILL)
    if page is not None:
        right.markdown(f":gray[p.{page}]")


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
    _caret_row(node["section_id"], node.get("title") or "(untitled)", node["change_count"], expandable, is_open)
    if expandable and is_open:
        with st.container(key=f"ind::{node['section_id']}"):
            for item in items:
                _row(item, selected)
            for child in children:
                _tree_node(child, selected, opened)


def _order_control() -> str:
    return st.radio("Order", ORDER_OPTIONS, key="tree_order", horizontal=True, label_visibility="collapsed")


def _tree(filtered: dict, selected: dict | None, has_sections: bool, mode: str) -> None:
    st.markdown("**Structure**")
    if mode == ORDER_OPTIONS[0] and not numbers_ascending(filtered["sections"]):
        st.caption(ORDER_NOTE)
    opened = _open_set()
    with st.container(height=TREE_HEIGHT):
        with st.container(key="treebox"):
            if not filtered["sections"] and not filtered["unplaced"]:
                st.caption("No matching sections or changes." if has_sections
                           else "No section structure was found for this job.")
            for node in filtered["sections"]:
                _tree_node(node, selected, opened)
            if filtered["unplaced"]:
                _caret_row(UNPLACED_ID, "Unplaced", len(filtered["unplaced"]), True, UNPLACED_ID in opened)
                if UNPLACED_ID in opened:
                    with st.container(key=f"ind::{UNPLACED_ID}"):
                        for item in filtered["unplaced"]:
                            _row(item, selected)


# --------------------------------------------------------------------------- viewer

def _breadcrumb(item: dict) -> str:
    path = item.get("section_path") or []
    where = " › ".join(_plain(p) for p in path) if path else "Unplaced"
    return f"{where}   {TAGS[bucket(item)]}"


def _llm_opinion(opinion: Any) -> None:
    """Compact, advisory LLM opinion box (variant A); nothing when the LLM did not look at it."""
    if not isinstance(opinion, dict) or not opinion.get("outcome"):
        return
    outcome = str(opinion["outcome"]).replace("_", " ").capitalize()
    with st.container(border=True, key="llm_opinion"):
        st.caption(":material/auto_awesome: LLM opinion · advisory")
        reason = _plain(opinion.get("reason") or "No reason given.")
        st.markdown(f":violet-badge[{_plain(outcome)}] {reason}")
        meta = [opinion.get("evaluator") or "evaluator not recorded"]
        refs = [r for r in opinion.get("evidence_refs") or [] if isinstance(r, str)]
        if refs:
            meta.append("looked at " + ", ".join(refs[:6]) + (" …" if len(refs) > 6 else ""))
        st.caption(_plain(" · ".join(meta)))


def _viewer(client, job_id: str, item: dict, flat: list[dict]) -> None:
    kind, record_id, ck = item["kind"], item["record_id"], _key(client)
    st.markdown(_breadcrumb(item))
    if _is_flagged(item):
        st.markdown(f":violet[Needs review \u2014 {_plain(item.get('review_reason') or 'reason not recorded')}]")
    _llm_opinion(item.get("llm_opinion"))
    st.caption(LEGEND)
    try:
        details = _details(client, ck, job_id, kind, record_id)
    except ApiError as exc:
        st.error(f"Could not load details: {exc.message}")
        return
    payload = details.get(kind) or {}
    try:
        located = _locations(client, ck, job_id, kind, record_id)
    except ApiError as exc:
        st.warning(f"Page locations are unavailable: {exc.message}")
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
            st.warning(f"Could not render the {side} page: {exc.message}")
            continue
        source_missing = source_missing or images[side] is None

    shown = [data for data in images.values() if data]
    size = _png_size(shown[0]) if shown else _DEFAULT_PAGE
    cols = st.columns(2)
    for col, side in zip(cols, ("old", "new")):
        with col:
            page, data = targets[side], images[side]
            name = side.capitalize()
            if data:
                _show_image(data)
                st.caption(f"{name} \u00b7 page {page}" + (" \u00b7 section context" if context[side] else ""))
            else:
                _show_image(_placeholder_png(*size))
                if page is None:
                    st.caption(f"{name} \u00b7 no page location")
                else:
                    st.caption(f"{name} \u00b7 page {page} (image unavailable)")
    if source_missing:
        st.info("The source PDF is not available for this job, so page images cannot be shown.")

    ids = [(i["kind"], i["record_id"]) for i in flat]
    index = ids.index((kind, record_id)) if (kind, record_id) in ids else 0
    prev_col, next_col = st.columns(2, gap="small", width=260)
    prev_col.button("Previous", key="prev_finding", disabled=index <= 0, on_click=_select,
                    args=(flat[max(index - 1, 0)],), **_BUTTON_FILL)
    next_col.button("Next", key="next_finding", disabled=index >= len(flat) - 1, on_click=_select,
                    args=(flat[min(index + 1, len(flat) - 1)],), **_BUTTON_FILL)
    st.caption(f"Change ID: {record_id}")


def render(client, job_id: str, status: dict | None = None) -> None:
    try:
        outline = _outline(client, _key(client), job_id)
    except ApiError as exc:
        st.error(f"Could not load the result for job {job_id}: {exc.message}")
        return
    st.markdown(TREE_CSS, unsafe_allow_html=True)
    _header(status or {}, job_id, outline)
    _chips(chip_counts(outline))
    kind, ctype, query, flagged = _filters()
    left, right = st.columns([2, 3])
    with left:
        mode = _order_control()
    shown = with_title_root(outline)
    root = shown["sections"][0]["section_id"] if shown is not outline else None
    if root and st.session_state.get("root_open") != (job_id, root):
        _open_set().add(root)  # the title root starts expanded, once per job
        st.session_state["root_open"] = (job_id, root)
    filtered = filter_outline(shown, kind, ctype, query, flagged)
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
            st.info("No findings match these filters." if flatten(filter_outline(outline)) else "No findings in this result.")
        else:
            _viewer(client, job_id, current, flat)
