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


def _controls(client) -> None:
    """Sidebar: upload and open-job controls."""
    with st.sidebar:
        st.subheader("Compare two PDFs")
        old = st.file_uploader("Old", type=["pdf"], key="old_pdf")
        new = st.file_uploader("New", type=["pdf"], key="new_pdf")
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
        st.subheader("Open an existing job")
        job_id = st.text_input("Job ID", key="open_job_id")
        if st.button("Open job", key="open_job"):
            if job_id.strip():
                _select_job(job_id.strip())
            else:
                st.warning("Enter a job ID.")


def _select_job(job_id: str) -> None:
    if not job_id:
        return
    st.session_state["job_id"] = job_id
    st.session_state.pop("selected", None)
    st.session_state["old_page"] = None
    st.session_state["new_page"] = None
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
    _controls(client)

    job_id = st.session_state.get("job_id")
    if not job_id:
        st.info("Upload two PDFs and press Compare, or open an existing job by ID.")
        return
    st.caption(f"Job ID: {job_id}")
    try:
        status = client.status(job_id)
    except ApiError as exc:
        st.error(f"Could not read the status of job {job_id}: {exc.message}")
        return
    state = str(status.get("state") or "queued")
    if state == "failed":
        st.error(f"Job {job_id} failed. {status.get('error_tail') or 'No error text was returned.'}")
        return
    if state != "done":
        st.write(f"Job is {state}.")
        _progress(status)
        if not injected:  # tests inject a client and drive reruns themselves
            time.sleep(POLL_SECONDS)
            st.rerun()
        return
    review.render(client, job_id)
    chat.render(client, job_id)


if __name__ == "__main__":
    main()
