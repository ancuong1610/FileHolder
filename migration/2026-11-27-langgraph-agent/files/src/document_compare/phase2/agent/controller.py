from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from html import unescape
from typing import Any, Literal, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from document_compare.phase2.agent.gateway import ReaderGateway
from document_compare.phase2.agent.observations import planner_observation
from document_compare.phase2.agent.prompts import PLANNER_SYSTEM, planner_prompt
from document_compare.phase2.agent.tia_client import TiaTextClient
from document_compare.phase2.agent.tools import build_tools
from document_compare.phase2.reader.result_reader import ReaderError


_OBSERVATION_LIMIT = 60_000
_RECORD_ID = re.compile(r"(?<![\w-])(?:TABLE-CHANGE-\d{5}|(?:CHANGE|BLOCK|MOVE|REVIEW)-\d{6})\b")
_RECORD_KINDS = {
    "CHANGE": "change", "BLOCK": "block", "MOVE": "move", "TABLE-CHANGE": "table", "REVIEW": "review",
}
_HELP_QUESTION = re.compile(
    r"\b(?:was bedeutet|was heisst|wie (?:funktioniert|benutze|verwende|bediene|öffne)|"
    r"what (?:does|do)\b[^?]*\bmean|how (?:does\b[^?]*\bwork|do i (?:use|open|navigate)))\b"
)
_SELECTED_REFERENCE = re.compile(
    r"\b(?:diese|dieser|dieses|diesen|hier|das|dies|this|that|it|selected|ausgewählt|markiert|"
    r"erklär(?:e)?|explain)\b"
)
_SUGGESTION_MARKUP = re.compile(
    r"[<>]|\[[^\]]*\]\s*(?:\(|\[|:)|\b(?:[a-z][a-z0-9+.-]*://|mailto:|www\.)"
    r"|\b[\w.+-]+@[\w.-]+|\b[\w-]+\.[a-z]{2,}(?!\w)", re.IGNORECASE,
)
_TOOL_DUMP = re.compile(r'\A\s*(?:```(?:json)?\s*)?(?:\{|\[\s*\{)|"record_id"\s*:')


@dataclass
class ChatAnswer:
    answer: str
    cited: list[dict[str, str]]
    unverified_ids: list[str]
    partial: bool
    suggestions: list[str] = field(default_factory=list)


def _compact_observations(observations: list[dict[str, Any]]) -> None:
    # A single reader response can exceed the prompt threshold. Keep the newest
    # observation intact even in that case.
    for observation in observations[:-1]:
        if len(json.dumps(observations, default=str)) <= _OBSERVATION_LIMIT:
            break
        if "result" in observation:
            ids = _RECORD_ID.findall(json.dumps(observation["result"], default=str))
            observation["result"] = {"compacted": True, "record_ids": sorted(set(ids))[:50]}
            observation.pop("change_brief", None)
            observation.pop("member_change_briefs", None)


def _suggestions(value: Any) -> list[str]:
    cleaned = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, str):
            continue
        suggestion = " ".join(item.split())
        if suggestion and not _SUGGESTION_MARKUP.search(unescape(suggestion)):
            cleaned.append(suggestion[:120].rstrip())
            if len(cleaned) == 3:
                break
    return cleaned


def contains_tool_dump(answer: str) -> bool:
    return bool(_TOOL_DUMP.search(answer))


def _chat_answer(
    answer: str, observed_ids: set[str], selected_id: str | None, partial: bool, suggestions: Any,
) -> ChatAnswer:
    cited = []
    unverified = []
    for record_id in dict.fromkeys(_RECORD_ID.findall(answer)):
        if record_id == selected_id or record_id in observed_ids:
            kind = _RECORD_KINDS[record_id.rsplit("-", 1)[0]]
            cited.append({"kind": kind, "record_id": record_id})
        else:
            unverified.append(record_id)
    return ChatAnswer(answer, cited, unverified, partial, _suggestions(suggestions))


class _ChatState(TypedDict, total=False):
    question: str
    job_id: str
    history: list[dict[str, str]] | None
    ui_context: dict[str, str] | None
    observations: list[dict[str, Any]]
    observed_ids: set[str]
    step: int
    max_steps: int
    decision: dict[str, Any]
    answer: ChatAnswer
    retry_step: int
    partial: bool
    events: list[dict[str, Any]]


_STEP_LIMIT = "Agent reached its step limit without producing a final answer."
_CHANGE_WORDS = frozenset({
    "geändert", "geaendert", "gelöscht", "geloescht", "hinzugefügt", "hinzugefuegt",
    "neu", "neue", "neuer", "neues", "entfernt", "ergänzt", "ergaenzt",
    "änderung", "aenderung", "änderungen", "aenderungen",
    "changed", "deleted", "added", "new", "removed", "change", "changes",
})
_SUFFIXES = ("ungen", "en", "er", "es", "e", "n", "s")


def _stem(term: str) -> str:
    for suffix in _SUFFIXES:
        if term.casefold().endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[:-len(suffix)]
    return term


def _search_variants(query: str) -> list[str]:
    terms = re.findall(r"[\w.-]+", query)
    remaining = [term for term in terms if term.casefold() not in _CHANGE_WORDS]
    if not remaining:
        return []
    candidates = [
        " ".join(remaining) if remaining != terms else "",
        " ".join(_stem(term) for term in remaining),
        _stem(max(remaining, key=len)),
    ]
    seen = {" ".join(terms).casefold()}
    variants = []
    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            variants.append(candidate)
            seen.add(key)
    return variants


def _search_count(result: Any) -> int | None:
    if not isinstance(result, dict) or "error" in result:
        return None
    count = result.get("total_matches")
    returned = result.get("returned")
    if type(count) is int and count >= 0 and isinstance(returned, list) and bool(count) == bool(returned):
        return count
    return None


class TiaComparisonAgent:
    """Run the TIA JSON planner over the three bounded reader tools."""

    def __init__(self, tia: TiaTextClient, gateway: ReaderGateway, max_steps: int = 5, result_limit: int = 10) -> None:
        self.tia = tia
        self.tools = build_tools(gateway, result_limit=result_limit)
        self.max_steps = max_steps
        self.result_limit = result_limit

        graph = StateGraph(_ChatState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("plan", self._plan_node)
        graph.add_node("act", self._act)
        graph.add_node("search_fallback", self._search_fallback)
        graph.add_node("check", self._check)
        graph.add_node("finish", self._finish)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "plan")
        graph.add_conditional_edges("plan", self._after_plan)
        graph.add_conditional_edges("act", self._after_act)
        graph.add_edge("search_fallback", "plan")
        graph.add_conditional_edges("check", self._after_check)
        graph.add_edge("finish", END)
        self._graph = graph.compile()

    @staticmethod
    def _help_only(question: str) -> bool:
        return not _RECORD_ID.search(question.upper()) and bool(_HELP_QUESTION.search(question.casefold()))

    def _prepare(self, state: _ChatState) -> _ChatState:
        context = state.get("ui_context") or {}
        selected_id = context.get("selected_record_id")
        if not selected_id or not _RECORD_ID.fullmatch(selected_id):
            return {}
        question = state["question"]
        mentioned = set(_RECORD_ID.findall(question.upper()))
        if (mentioned - {selected_id} or self._help_only(question)
                or not (mentioned or _SELECTED_REFERENCE.search(question.casefold()))):
            return {}

        kind = context.get("selected_kind") or _RECORD_KINDS[selected_id.rsplit("-", 1)[0]]
        arguments = {"job_id": state["job_id"], "kind": kind, "record_id": selected_id}
        get_stream_writer()({
            "type": "step", "step": 0, "action": "details", "query": None, "record_id": selected_id,
        })
        result, ids = self._invoke_tool("details", arguments)
        return {
            "observations": [{"action": "details", "arguments": arguments, "result": result}],
            "observed_ids": ids,
        }

    def _plan(self, values: dict[str, Any]) -> dict[str, Any]:
        raw = self.tia.invoke(
            planner_prompt(
                values["question"],
                values["job_id"],
                values["observations"],
                history=values.get("history"),
                ui_context=values.get("ui_context"),
                final_step=values.get("final_step", False),
                tool_schemas={
                    name: tool.args_schema.model_json_schema()
                    for name, tool in self.tools.items()
                },
            ),
            PLANNER_SYSTEM,
        )

        try:
            decision = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"TIA planner returned non-JSON output: {raw[:1000]!r}"
            ) from exc

        if not isinstance(decision, dict) or not isinstance(
            decision.get("action"), str
        ):
            raise RuntimeError(
                f"TIA planner response must contain a string action. "
                f"Raw response: {raw[:2000]!r}"
            )

        return decision

    def _plan_node(self, state: _ChatState) -> _ChatState:
        step = state["step"] + 1
        partial = step == state["max_steps"]
        observations = [planner_observation(observation) for observation in state["observations"]]
        _compact_observations(observations)
        decision = self._plan({
            "question": state["question"], "job_id": state["job_id"], "observations": observations,
            "history": state.get("history"), "ui_context": state.get("ui_context"), "final_step": partial,
        })

        events = []
        action = decision["action"]
        arguments = decision.get("arguments") or {}
        if not partial and action in self.tools and isinstance(arguments, dict):
            query = arguments.get("query") if action == "search" else None
            record_id = arguments.get("record_id") if action == "details" else None
            valid_record_id = isinstance(record_id, str) and _RECORD_ID.fullmatch(record_id)
            events.append({
                "type": "step", "step": step, "action": action,
                "query": query[:120] if isinstance(query, str) else None,
                "record_id": record_id if valid_record_id else None,
            })
        return {
            "step": step, "partial": partial, "decision": decision,
            "events": events,
        }

    @staticmethod
    def _after_plan(state: _ChatState) -> Literal["check", "act"]:
        if state["decision"]["action"] == "final":
            return "check"
        if state["partial"]:
            raise RuntimeError(_STEP_LIMIT)
        return "act"

    def _invoke_tool(self, action: str, arguments: dict[str, Any]) -> tuple[Any, set[str]]:
        try:
            result = self.tools[action].invoke(arguments)
        except ReaderError as exc:
            return exc.as_dict(), set()
        except Exception as exc:
            return {"error": type(exc).__name__, "message": "Reader tool call failed."}, set()
        # Keep citations verifiable after compaction, even beyond its first 50 IDs.
        return result, set(_RECORD_ID.findall(json.dumps(result, default=str)))

    def _act(self, state: _ChatState) -> _ChatState:
        decision = state["decision"]
        action = decision["action"]
        arguments = decision.get("arguments") or {}
        ids = set()
        if action not in self.tools:
            observation = {"action": action, "error": "unknown_action"}
        elif not isinstance(arguments, dict):
            observation = {"action": action, "error": "arguments_must_be_object"}
        else:
            arguments = {**arguments, "job_id": state["job_id"]}
            result, ids = self._invoke_tool(action, arguments)
            observation = {"action": action, "arguments": arguments, "result": result}
        return {
            "observations": [*state["observations"], observation],
            "observed_ids": state["observed_ids"] | ids,
        }

    @staticmethod
    def _after_act(state: _ChatState) -> Literal["plan", "search_fallback"]:
        latest = state["observations"][-1]
        arguments = latest.get("arguments", {})
        query = arguments.get("query")
        if (
            latest["action"] == "search"
            and _search_count(latest.get("result")) == 0
            and isinstance(query, str) and query.strip()
            and arguments.get("cursor") is None
        ):
            return "search_fallback"
        return "plan"

    def _search_fallback(self, state: _ChatState) -> _ChatState:
        original = state["observations"][-1]["arguments"]
        query = original["query"]
        variants = _search_variants(query)
        if not variants:
            return {}

        tried = []
        events = []
        for variant in variants:
            arguments = {**original, "query": variant}
            result, ids = self._invoke_tool("search", arguments)
            tried.append(variant)
            if (_search_count(result) or 0) > 0:
                events.append({"type": "fallback", "query": query, "variant": variant})
                break

        observation = {
            "action": "search_fallback", "arguments": arguments,
            "original_query": query, "variants_tried": tried, "result": result,
        }
        return {
            "observations": [*state["observations"], observation],
            "observed_ids": state["observed_ids"] | ids,
            "events": events,
        }

    def _check(self, state: _ChatState) -> _ChatState:
        answer = state["decision"].get("answer")
        if "retry_step" in state or state["partial"] or not isinstance(answer, str):
            return {}

        answer_ids = set(_RECORD_ID.findall(answer))
        observed = state["observed_ids"]
        selected = (state.get("ui_context") or {}).get("selected_record_id")
        problems = []
        if answer_ids - observed - {selected}:
            problems.append("Cites unverified record IDs.")
        if observed and not answer_ids and not self._help_only(state["question"]):
            problems.append("Cite the observed finding IDs for comparison claims.")
        if contains_tool_dump(answer):
            problems.append("Answer contains a raw JSON or tool dump.")
        if not problems:
            return {}
        return {
            "observations": [*state["observations"], {"action": "answer_check", "problems": problems}],
            "retry_step": state["step"],
        }

    @staticmethod
    def _after_check(state: _ChatState) -> Literal["plan", "finish"]:
        if state.get("retry_step") == state["step"]:
            return "plan"
        return "finish"

    @staticmethod
    def _finish(state: _ChatState) -> _ChatState:
        answer = state["decision"].get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("TIA final action has no answer.")
        selected_id = (state.get("ui_context") or {}).get("selected_record_id")
        result = _chat_answer(answer, state["observed_ids"], selected_id, state["partial"],
                              state["decision"].get("suggestions"))
        return {"answer": result, "events": [{"type": "final", **asdict(result)}]}

    def _initial_state(
        self, question: str, job_id: str, history: list[dict[str, str]] | None,
        ui_context: dict[str, str] | None,
    ) -> _ChatState:
        if self.max_steps <= 0:
            raise RuntimeError(_STEP_LIMIT)
        return {
            "question": question, "job_id": job_id, "history": history,
            "ui_context": ui_context, "observations": [], "observed_ids": set(),
            "step": 0, "max_steps": self.max_steps,
        }

    def ask(
        self, question: str, job_id: str, *, history: list[dict[str, str]] | None = None,
        ui_context: dict[str, str] | None = None,
    ) -> ChatAnswer:
        result = self._graph.invoke(
            self._initial_state(question, job_id, history, ui_context),
            config={"recursion_limit": self.max_steps * 3 + 5},
        )
        return result["answer"]

    def stream(
        self, question: str, job_id: str, *, history: list[dict[str, str]] | None = None,
        ui_context: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        # A plan update is yielded before the next graph node can execute its tool.
        for mode, update in self._graph.stream(
            self._initial_state(question, job_id, history, ui_context),
            config={"recursion_limit": self.max_steps * 3 + 5},
            stream_mode=["custom", "updates"], output_keys=["events"],
        ):
            if mode == "custom":
                yield update
                continue
            for node in update.values():
                if node:
                    yield from node.get("events", ())

    def ask_text(self, question: str, job_id: str) -> str:
        """Return the legacy text-only answer for the terminal reviewer."""
        return self.ask(question, job_id).answer.strip()
