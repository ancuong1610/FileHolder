"""Comparison-agent chat bound to the open job. History lives in session state only."""
from __future__ import annotations

import re

import streamlit as st

from document_compare.phase2.ui import review, texts
from document_compare.phase2.ui.api_client import ApiError

HISTORY_HEIGHT = 420

_MARKDOWN_LINK = re.compile(
    r"(?P<image>!)?\[(?P<label>(?:\\.|[^\[\]\\]|\[[^\[\]]*\])*)\]"
    r"(?:\((?:\\.|[^()\\]|\([^()]*\))*\)|\[[^\[\]\n]*\])"
)
_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]+\]:[^\n]*$", re.MULTILINE)
_HTML_TAG = re.compile(r"""<!--[\s\S]*?(?:-->|$)|</?[a-zA-Z][\w:-]*(?=[\s/>])(?:"[^"]*"|'[^']*'|[^'">])*>""")
_AUTOLINK = re.compile(r"<(?:[a-zA-Z][\w+.-]*:[^\s<>]+|[^\s<>@]+@[^\s<>@]+)>")
_BRACKET = re.compile(r"(\\*)([\[\]])")


def sanitize_markdown(text: str) -> str:
    """Keep short Markdown while removing links, images and HTML from answers."""
    text = _REFERENCE_DEFINITION.sub("", text)
    text = _MARKDOWN_LINK.sub(lambda match: "" if match["image"] else match["label"], text)
    text = _HTML_TAG.sub("", _AUTOLINK.sub("", text))
    # Escape any remaining brackets, including syntax with nested link labels.
    return _BRACKET.sub(lambda match: "\\" * (len(match[1]) | 1) + match[2], text)


def _recent_turns(history: list[dict]) -> list[dict[str, str]]:
    turns = []
    remaining = 12_000
    for message in reversed(history[-6:]):
        content = message["content"][:min(4000, remaining)]
        if content:
            turns.append({"role": message["role"], "content": content})
            remaining -= len(content)
    return list(reversed(turns))


def _ui_context(client, job_id: str) -> dict[str, str]:
    context = {}
    preset = st.session_state.get("f_preset")
    if preset is not None:
        context["preset"] = preset
    selected = st.session_state.get("selected")
    if selected:
        context.update(selected_kind=selected["kind"], selected_record_id=selected["record_id"])
        section = review.selected_section(client, job_id, selected)
        if section:
            context["section"] = section[:200]
    return context


def _ask(client, job_id: str, question: str, history: list[dict]) -> None:
    recent_turns = _recent_turns(history)
    history.append({"role": "user", "content": question})
    st.session_state.pop("chat_error", None)
    try:
        with st.spinner(texts.CHAT_PENDING):
            reply = client.chat(job_id, question, history=recent_turns, context=_ui_context(client, job_id))
    except ApiError as exc:
        st.session_state["chat_error"] = {"code": exc.code, "message": exc.message}
        return
    history.append({
        "role": "assistant", "content": str(reply.get("answer") or ""),
        "limitations": [str(x) for x in reply.get("limitations") or []],
        "cited": reply.get("cited") or [], "unverified_ids": reply.get("unverified_ids") or [],
    })


def _show_answer(job_id: str, index: int, message: dict) -> None:
    st.markdown(sanitize_markdown(message["content"]))
    for citation in message.get("cited") or []:
        record_id = citation["record_id"]
        st.button(record_id, key=f"chat_cite::{job_id}::{index}::{record_id}", help=texts.CHAT_OPEN_CITATION,
                  on_click=review.select_citation, args=(job_id, citation))
    limitations = message.get("limitations") or []
    if "partial_answer" in limitations:
        st.caption(texts.CHAT_PARTIAL_ANSWER)
    if "unverified_citation" in limitations:
        st.caption(texts.CHAT_UNVERIFIED_IDS.format(ids=", ".join(message.get("unverified_ids") or [])))
    labels = [texts.LIMITATION_LABELS.get(code, code) for code in limitations
              if code not in {"partial_answer", "unverified_citation"}]
    if labels:
        st.caption(texts.LIMITATIONS.format(items=", ".join(labels)))


def render(client, job_id: str) -> None:
    """Call inside the sidebar's agent tab."""
    history = st.session_state.setdefault("chat_history", [])
    box = st.container(height=HISTORY_HEIGHT)  # scrolls internally; the input stays below it
    question = st.chat_input(texts.CHAT_HINT, key="chat_question")
    if question and question.strip():
        _ask(client, job_id, question.strip(), history)
    with box:
        for index, message in enumerate(history):
            with st.chat_message(message["role"]):
                if message["role"] == "assistant":
                    _show_answer(job_id, index, message)
                else:
                    st.text(message["content"])
    error = st.session_state.get("chat_error")
    if error and error["code"] == "chat_not_configured":
        st.caption(texts.AGENT_NOT_CONFIGURED)
    elif error:
        st.error(texts.api_error(error["code"]))
