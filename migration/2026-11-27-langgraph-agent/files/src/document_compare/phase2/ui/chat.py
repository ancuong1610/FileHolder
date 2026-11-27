"""Comparison-agent chat bound to the open job. History lives in session state only."""
from __future__ import annotations

import re

import streamlit as st

from document_compare.phase2.ui import review, texts
from document_compare.phase2.ui.api_client import ApiError

HISTORY_HEIGHT = 420
_PENDING_SUGGESTION = "chat_pending_suggestion"

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


def _stream_reply(client, job_id: str, question: str, history: list[dict], context: dict) -> dict:
    reply = None
    with st.status(texts.CHAT_PENDING, expanded=False) as status:
        for event in client.chat_stream(job_id, question, history=history, context=context):
            if event["type"] == "final":
                reply = event
            elif event["type"] == "fallback":
                status.update(label=texts.CHAT_STEP_FALLBACK.format(
                    query=sanitize_markdown(event["query"]), variant=sanitize_markdown(event["variant"]),
                ))
            elif event["type"] == "step":
                action = event["action"]
                if action == "search":
                    query = event["query"]
                    label = (
                        texts.CHAT_STEP_SEARCH.format(query=sanitize_markdown(query))
                        if query else texts.CHAT_STEP_SEARCH_ALL
                    )
                elif action == "details":
                    record_id = event["record_id"]
                    label = (
                        texts.CHAT_STEP_DETAILS.format(record_id=record_id)
                        if record_id else texts.CHAT_STEP_DETAILS_UNKNOWN
                    )
                else:
                    label = texts.CHAT_STEP_SUMMARY
                status.update(label=label)
        if reply is None:
            raise ApiError(None, "chat_failed", "")
        status.update(label=texts.CHAT_COMPLETE, state="complete")
    return reply


def _ask(client, job_id: str, question: str, history: list[dict]) -> None:
    recent_turns = _recent_turns(history)
    history.append({"role": "user", "content": question})
    st.session_state.pop("chat_error", None)
    try:
        if hasattr(client, "chat_stream"):
            reply = _stream_reply(client, job_id, question, recent_turns, _ui_context(client, job_id))
        else:
            with st.spinner(texts.CHAT_PENDING):
                reply = client.chat(job_id, question, history=recent_turns, context=_ui_context(client, job_id))
    except ApiError as exc:
        st.session_state["chat_error"] = {"code": exc.code, "message": exc.message}
        return
    history.append({
        "role": "assistant", "content": str(reply.get("answer") or ""),
        "limitations": [str(x) for x in reply.get("limitations") or []],
        "cited": reply.get("cited") or [], "unverified_ids": reply.get("unverified_ids") or [],
        "suggestions": reply.get("suggestions") or [],
    })


def _queue_suggestion(job_id: str, question: str) -> None:
    st.session_state[_PENDING_SUGGESTION] = (job_id, question)


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
    for suggestion_index, suggestion in enumerate(message.get("suggestions") or []):
        st.button(sanitize_markdown(suggestion), key=f"chat_suggest::{job_id}::{index}::{suggestion_index}",
                  on_click=_queue_suggestion, args=(job_id, suggestion))


def render(client, job_id: str) -> None:
    """Call inside the sidebar's agent tab."""
    history = st.session_state.setdefault("chat_history", [])
    box = st.container(height=HISTORY_HEIGHT)  # scrolls internally; the input stays below it
    question = st.chat_input(texts.CHAT_HINT, key="chat_question")
    pending = st.session_state.pop(_PENDING_SUGGESTION, None)
    if pending and pending[0] == job_id:
        question = pending[1]
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
