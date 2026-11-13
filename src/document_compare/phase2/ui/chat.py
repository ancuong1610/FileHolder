"""Comparison-agent chat bound to the open job. History lives in session state only."""
from __future__ import annotations

import streamlit as st

from document_compare.phase2.ui.api_client import ApiError

HISTORY_HEIGHT = 420
NOT_CONFIGURED = "Agent not configured on this machine."
PLACEHOLDER = "Ask about this comparison (sends excerpts to the model)"


def _ask(client, job_id: str, question: str, history: list) -> None:
    history.append({"role": "user", "content": question})
    st.session_state.pop("chat_error", None)
    try:
        with st.spinner("Asking..."):
            reply = client.chat(job_id, question)
    except ApiError as exc:
        st.session_state["chat_error"] = {"code": exc.code, "message": exc.message}
        return
    history.append({"role": "assistant", "content": str(reply.get("answer") or ""),
                    "limitations": [str(x) for x in reply.get("limitations") or []]})


def render(client, job_id: str) -> None:
    """Call inside the sidebar's agent tab."""
    history = st.session_state.setdefault("chat_history", [])
    box = st.container(height=HISTORY_HEIGHT)  # scrolls internally; the input stays below it
    question = st.chat_input(PLACEHOLDER, key="chat_question")
    if question and question.strip():
        _ask(client, job_id, question.strip(), history)
    with box:
        for message in history:
            with st.chat_message(message["role"]):
                st.text(message["content"])
                if message.get("limitations"):
                    st.caption("Limitations: " + ", ".join(message["limitations"]))
    error = st.session_state.get("chat_error")
    if error and error["code"] == "chat_not_configured":
        st.caption(NOT_CONFIGURED)
    elif error:
        st.error(error["message"])
