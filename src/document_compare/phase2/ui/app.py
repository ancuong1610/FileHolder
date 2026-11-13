"""Streamlit shell: upload two PDFs, follow progress, open a job, then review it.

Run with ``streamlit run src/document_compare/phase2/ui/app.py``. Talks to the
local HTTP API only. Nothing here logs result text, questions or image bytes.
"""
from __future__ import annotations

import time
import uuid

import streamlit as st

from document_compare.phase2.ui import chat, review
from document_compare.phase2.ui.api_client import ApiClient, ApiError, base_from_env

TOTAL_STAGES = 15
POLL_SECONDS = 2
EXTRACTION_NOTICE = "Comparing sends both PDFs to the configured extraction provider."


@st.cache_resource
def _cached_client(base: str) -> ApiClient:
    return ApiClient(base)


def _build_client() -> ApiClient | None:
    try:
        return _cached_client(base_from_env())
    except ValueError as exc:
        st.error(str(exc))
        return None


TAB_CSS = (
    "<style>"
    "[data-testid='stSidebar'] [role='tablist']{width:100%;}"
    "[data-testid='stSidebar'] [role='tablist'] > [role='tab']{flex:1 1 0;justify-content:center;text-align:center;}"
    "</style>"
)


def _controls(client):
    """Sidebar tabs: compare, open job, agent. Returns the agent tab to fill later."""
    with st.sidebar:
        st.markdown(TAB_CSS, unsafe_allow_html=True)  # static style: tabs share the sidebar width equally
        compare_tab, open_tab, agent_tab = st.tabs(["Compare", "Open job", "Comparison agent"])
    with compare_tab:
        old = st.file_uploader("Old PDF", type=["pdf"], key="old_pdf")
        new = st.file_uploader("New PDF", type=["pdf"], key="new_pdf")
        st.caption(EXTRACTION_NOTICE)
        if st.button("Compare", key="compare", type="primary"):
            if old is None or new is None:
                st.warning("Select both PDFs before comparing.")
            else:
                try:
                    job = client.submit(old.name, old.getvalue(), new.name, new.getvalue(), uuid.uuid4().hex)
                except ApiError as exc:
                    st.error(f"Upload failed: {exc.message}")
                else:
                    _select_job(str(job.get("job_id") or ""))
    with open_tab:
        id_col, button_col = st.columns([3, 1], vertical_alignment="bottom")
        job_id = id_col.text_input("Job ID", key="open_job_id")
        if button_col.button("Open", key="open_job"):
            if job_id.strip():
                _select_job(job_id.strip())
            else:
                st.warning("Enter a job ID.")
    return agent_tab


def _select_job(job_id: str) -> None:
    if not job_id:
        return
    st.session_state["job_id"] = job_id
    st.session_state.pop("selected", None)
    st.session_state.pop("tree_open", None)
    st.session_state.pop("tree_synced", None)
    st.session_state["chat_history"] = []
    st.session_state.pop("chat_error", None)


def _progress(status: dict) -> None:
    done = min(len(status.get("stages_completed") or []), TOTAL_STAGES)
    st.progress(done / TOTAL_STAGES)
    st.caption(f"{done}/{TOTAL_STAGES} stages complete")


def main(client=None) -> None:
    injected = client is not None
    st.set_page_config(page_title="Document compare", layout="wide")
    st.title("Document compare")
    if client is None:
        client = _build_client()
        if client is None:
            return
    agent_tab = _controls(client)

    job_id = st.session_state.get("job_id")
    if not job_id:
        with agent_tab:
            st.caption("Open a job to use the agent.")
        st.info("Upload two PDFs and press Compare, or open an existing job by ID.")
        return
    try:
        status = client.status(job_id)
    except ApiError as exc:
        st.error(f"Could not read the status of job {job_id}: {exc.message}")
        return
    state = str(status.get("state") or "queued")
    if state != "done":
        with agent_tab:
            st.caption("Available when the comparison is done.")
    if state == "failed":
        st.caption(f"Job ID: {job_id}")
        st.error(f"Job {job_id} failed. {status.get('error_tail') or 'No error text was returned.'}")
        return
    if state != "done":
        st.caption(f"Job ID: {job_id}")
        st.write(f"Job is {state}.")
        _progress(status)
        if not injected:  # tests inject a client and drive reruns themselves
            time.sleep(POLL_SECONDS)
            st.rerun()
        return
    review.render(client, job_id, status)
    with agent_tab:
        chat.render(client, job_id)


if __name__ == "__main__":
    main()
