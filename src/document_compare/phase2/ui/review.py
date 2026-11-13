"""Findings list and old/new page viewer. Plain Streamlit widgets, API client only.

Result text is only ever shown through ``st.text`` or escaped plain markdown;
nothing here uses ``unsafe_allow_html`` and nothing is logged.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

import streamlit as st

from document_compare.phase2.ui.api_client import ApiError

PAGE_SIZE = 20
KINDS = ["all", "change", "table", "review"]
STATES = {"All": "all", "Accepted": "accepted", "Needs review": "review"}
STATE_LABELS = {"supported": "Supported", "uncertain": "Uncertain", "non_semantic": "Non-semantic"}
LEGEND = (
    "Highlights: red = deleted (old), green = added (new), amber = modified or table cell. "
    "Dashed outline = uncertain. Colour is never the only signal: type and state are shown above."
)
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$&:])")


def _plain(value: Any) -> str:
    """Escape markdown so result text renders literally."""
    return _MD_SPECIAL.sub(r"\\\1", str(value if value is not None else "")).replace("\n", " ")


def _key(client) -> str:
    return str(getattr(client, "cache_key", "default"))


@st.cache_data(show_spinner=False, max_entries=256)
def _details(_client, cache_key: str, job_id: str, kind: str, record_id: str) -> dict:
    return _client.details(job_id, kind, record_id)


@st.cache_data(show_spinner=False, max_entries=256)
def _locations(_client, cache_key: str, job_id: str, kind: str, record_id: str) -> dict:
    return _client.locations(job_id, kind, record_id)


@st.cache_data(show_spinner=False, max_entries=64)
def _page_png(_client, cache_key: str, job_id: str, side: str, page: int, kind: str,
              record_id: str, uncertain: bool) -> bytes | None:
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


# --------------------------------------------------------------------------- header

def _header(client, job_id: str) -> dict | None:
    try:
        summary = client.summary(job_id)
    except ApiError as exc:
        st.error(f"Could not load the result for job {job_id}: {exc.message}")
        return None
    counts = summary.get("counts") or {}
    states = counts.get("result_state_counts") or {}
    docs = summary.get("documents") or {}

    def doc_line(name: str, doc: dict) -> str:
        pages = doc.get("page_count")
        return f"{name}: {doc.get('document_id') or 'unknown'}" + (f" ({pages} pages)" if pages else "")

    st.caption(f"{doc_line('Old', docs.get('old') or {})}  |  {doc_line('New', docs.get('new') or {})}")
    cols = st.columns(4)
    cols[0].metric("Changes", counts.get("changes", 0))
    cols[1].metric("Table changes", counts.get("table_changes", 0))
    cols[2].metric("Uncertain", states.get("uncertain", counts.get("review_open", 0)))
    cols[3].metric("Supported", states.get("supported", 0))
    by_type = counts.get("change_type_counts") or {}
    if by_type:
        st.caption("By type: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items()))
                   + f"  |  Non-semantic: {states.get('non_semantic', 0)}")
    limits = summary.get("limitations") or []
    if limits:
        st.caption("Limitations: " + ", ".join(str(x) for x in limits))
    if summary.get("ground_truth_notice"):
        st.caption(str(summary["ground_truth_notice"]))
    return summary


# --------------------------------------------------------------------------- findings list

def _filters(summary: dict) -> dict:
    types = sorted(((summary.get("counts") or {}).get("change_type_counts") or {}).keys())
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    kind = c1.selectbox("Kind", KINDS, key="f_kind")
    change_type = c2.selectbox("Change type", ["All", *types], key="f_change_type")
    state = c3.selectbox("State", list(STATES), key="f_state")
    query = c4.text_input("Search text", key="f_query")
    return {"kind": kind, "change_type": "" if change_type == "All" else change_type,
            "status": STATES[state], "query": query.strip()}


def _fetch(client, job_id: str, filters: dict, cursor: str | None = None) -> dict:
    return client.search(job_id, limit=PAGE_SIZE, cursor=cursor, **filters)


def _load(client, job_id: str, filters: dict, more: bool = False) -> bool:
    store = st.session_state.get("findings")
    try:
        data = _fetch(client, job_id, filters, store["next_cursor"] if more and store else None)
    except ApiError as exc:
        st.error(f"Could not load findings: {exc.message}")
        return False
    rows = list(data.get("returned") or [])
    if more and store:
        rows = store["rows"] + rows
    st.session_state["findings"] = {
        "key": (job_id, tuple(sorted(filters.items()))), "rows": rows,
        "next_cursor": data.get("next_cursor"), "total": data.get("total_matches", len(rows)),
    }
    return True


def _select(row: dict) -> None:
    st.session_state["selected"] = {"kind": row["kind"], "record_id": row["record_id"]}
    st.session_state["old_page"] = None
    st.session_state["new_page"] = None


def _row_label(row: dict) -> str:
    pages = row.get("pages") or []
    where = "p." + ",".join(str(p) for p in pages) if pages else "no page"
    state = STATE_LABELS.get(row.get("result_state"), str(row.get("result_state") or "unknown"))
    kind_type = row.get("change_type") or row.get("kind")
    snippet = str(row.get("snippet") or "")[:90]
    return _plain(f"{kind_type} | {state} | {where} | {snippet}")


def _findings(client, job_id: str, filters: dict) -> None:
    key = (job_id, tuple(sorted(filters.items())))
    store = st.session_state.get("findings")
    if not store or store.get("key") != key:
        if store:  # filters or job changed: a selection outside the new list must not linger
            st.session_state.pop("selected", None)
            st.session_state["old_page"] = None
            st.session_state["new_page"] = None
        if not _load(client, job_id, filters):
            return
        store = st.session_state["findings"]
    rows = store["rows"]
    st.caption(f"{len(rows)} of {store['total']} findings loaded")
    selected = st.session_state.get("selected") or {}
    ids = [(r["kind"], r["record_id"]) for r in rows]
    index = ids.index((selected.get("kind"), selected.get("record_id"))) if (
        selected.get("kind"), selected.get("record_id")) in ids else None

    nav1, nav2, _ = st.columns([1, 1, 4])
    if nav1.button("Previous", key="prev_finding", disabled=not rows):
        _select(rows[0 if index is None else max(index - 1, 0)])
    if nav2.button("Next", key="next_finding", disabled=not rows):
        if index is not None and index == len(rows) - 1 and store["next_cursor"]:
            if _load(client, job_id, filters, more=True):
                store = st.session_state["findings"]
                rows = store["rows"]
        _select(rows[0 if index is None else min(index + 1, len(rows) - 1)])
        selected = st.session_state.get("selected") or {}

    with st.container(height=280):
        for row in rows:
            is_sel = (row["kind"], row["record_id"]) == (
                (st.session_state.get("selected") or {}).get("kind"),
                (st.session_state.get("selected") or {}).get("record_id"))
            label = ("Selected: " if is_sel else "") + _row_label(row)
            st.button(label, key=f"pick::{row['kind']}::{row['record_id']}", on_click=_select,
                      args=(row,), **_BUTTON_FILL)
    if store["next_cursor"] and st.button("Load more", key="load_more"):
        _load(client, job_id, filters, more=True)
        st.rerun()
    if not rows:
        st.info("No findings match these filters.")


# --------------------------------------------------------------------------- viewer

def _texts(kind: str, payload: dict) -> tuple[str, str]:
    if kind == "change":
        return str((payload.get("old") or {}).get("text") or ""), str((payload.get("new") or {}).get("text") or "")
    if kind == "table":
        olds, news = [], []
        for cell in payload.get("cell_changes") or []:
            for side, bucket in (("old", olds), ("new", news)):
                part = cell.get(side) or {}
                if part.get("text"):
                    row, col = part.get("row_index"), part.get("column_index")
                    bucket.append(f"[r{(row or 0) + 1} c{(col or 0) + 1}] {part['text']}")
        return "\n".join(olds), "\n".join(news)
    return "", ""


def _reasons(payload: dict) -> list[str]:
    reasons = payload.get("review_reasons") or (payload.get("resolution") or {}).get("review_reasons") or []
    return [str(r) for r in reasons]


def _step_page(side: str, delta: int, count: Any) -> None:
    """Button callback: runs before the rerun, so disabled flags are never stale."""
    current = st.session_state.get(f"{side}_page") or st.session_state.get(f"{side}_target") or 1
    page = max(1, current + delta)
    if isinstance(count, int) and count >= 1:
        page = min(page, count)
    st.session_state[f"{side}_page"] = page


def _page_controls(side: str, count: Any, current: int) -> None:
    bounded = isinstance(count, int) and count >= 1
    c1, c2, c3 = st.columns([1, 2, 1])
    c1.button("−", key=f"{side}_prev", disabled=current <= 1, on_click=_step_page, args=(side, -1, count))
    c3.button("+", key=f"{side}_next", disabled=not bounded or current >= count,
              on_click=_step_page, args=(side, 1, count))
    c2.caption(f"Page {current}" + (f" of {count}" if bounded else ""))


def _viewer(client, job_id: str, sel: dict) -> None:
    kind, record_id, ck = sel["kind"], sel["record_id"], _key(client)
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
    state = payload.get("result_state") or (payload.get("resolution") or {}).get("result_state") or "unknown"
    uncertain = state == "uncertain"
    ctype = payload.get("change_type") or (located or {}).get("change_type") or kind

    targets = {"old": target_page(old_side, new_side), "new": target_page(new_side, old_side)}
    where = ", ".join(f"{s} p.{p}" for s, p in targets.items() if p) or "no page location"
    st.markdown(f"**Type:** {_plain(ctype)}  |  **State:** {STATE_LABELS.get(state, _plain(state))}  |  "
                f"**Pages:** {where}  |  **Record:** {_plain(record_id)}")
    for reason in _reasons(payload):
        st.markdown("- Reason: " + _plain(reason))
    st.caption(LEGEND)

    if any(targets.values()):
        images: dict[str, bytes | None] = {}
        cols = st.columns(2)
        for col, (side, info) in zip(cols, (("old", old_side), ("new", new_side))):
            with col:
                st.subheader("Old" if side == "old" else "New")
                if info.get("counterpart") == "none":
                    st.caption("No counterpart on this side")
                if targets[side] is None:
                    st.caption("No page to show for this side.")
                    continue
                st.session_state[f"{side}_target"] = targets[side]
                page = st.session_state.get(f"{side}_page") or targets[side]
                _page_controls(side, info.get("page_count"), page)
                try:
                    images[side] = _page_png(client, ck, job_id, side, page, kind, record_id, uncertain)
                except ApiError as exc:
                    st.warning(f"Could not render this page: {exc.message}")
                    continue
                if images[side] is not None:
                    _show_image(images[side])
        if any(v is None for v in images.values()):
            st.info("The source PDF is not available for this job, so page images are replaced by text.")
    else:
        st.info("This finding has no page location, so only its text is shown.")

    st.markdown("#### Text")
    old_text, new_text = _texts(kind, payload)
    left, right = st.columns(2)
    with left:
        st.markdown("**Old**")
        st.text(old_text or "(no text on this side)")
    with right:
        st.markdown("**New**")
        st.text(new_text or "(no text on this side)")
    if kind == "review":
        evidence = payload.get("evidence")
        if evidence:
            with st.expander("Evidence"):
                st.json(evidence)


def render(client, job_id: str) -> None:
    summary = _header(client, job_id)
    if summary is None:
        return
    if not summary.get("complete", True) and summary.get("state") != "done":
        st.info("The result is not ready yet.")
        return
    st.markdown("### Findings")
    filters = _filters(summary)
    _findings(client, job_id, filters)
    sel = st.session_state.get("selected")
    if sel:
        st.divider()
        _viewer(client, job_id, sel)
    else:
        st.info("Select a finding to see the old and new pages.")
