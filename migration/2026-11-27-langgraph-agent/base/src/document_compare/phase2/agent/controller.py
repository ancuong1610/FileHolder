from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableLambda

from document_compare.phase2.agent.gateway import ReaderGateway
from document_compare.phase2.agent.prompts import PLANNER_SYSTEM, planner_prompt
from document_compare.phase2.agent.tia_client import TiaTextClient
from document_compare.phase2.agent.tools import build_tools
from document_compare.phase2.reader.result_reader import ReaderError


_OBSERVATION_LIMIT = 60_000
_RECORD_ID = re.compile(r"(?<![\w-])(?:TABLE-CHANGE-\d{5}|(?:CHANGE|BLOCK|MOVE|REVIEW)-\d{6})\b")
_RECORD_KINDS = {
    "CHANGE": "change", "BLOCK": "block", "MOVE": "move", "TABLE-CHANGE": "table", "REVIEW": "review",
}


@dataclass
class ChatAnswer:
    answer: str
    cited: list[dict[str, str]]
    unverified_ids: list[str]
    partial: bool


def _compact_observations(observations: list[dict[str, Any]]) -> None:
    # A single reader response can exceed the prompt threshold. Keep the newest
    # observation intact even in that case.
    for observation in observations[:-1]:
        if len(json.dumps(observations, default=str)) <= _OBSERVATION_LIMIT:
            break
        if "result" in observation:
            ids = _RECORD_ID.findall(json.dumps(observation["result"], default=str))
            observation["result"] = {"compacted": True, "record_ids": sorted(set(ids))[:50]}


def _chat_answer(answer: str, observed_ids: set[str], selected_id: str | None, partial: bool) -> ChatAnswer:
    cited = []
    unverified = []
    for record_id in dict.fromkeys(_RECORD_ID.findall(answer)):
        if record_id == selected_id or record_id in observed_ids:
            kind = _RECORD_KINDS[record_id.rsplit("-", 1)[0]]
            cited.append({"kind": kind, "record_id": record_id})
        else:
            unverified.append(record_id)
    return ChatAnswer(answer, cited, unverified, partial)


class TiaComparisonAgent:
    """LangChain-controlled action loop over the three reader result tools."""

    def __init__(self, tia: TiaTextClient, gateway: ReaderGateway, max_steps: int = 5, result_limit: int = 10) -> None:
        self.tia = tia
        self.tools = build_tools(gateway, result_limit=result_limit)
        self.max_steps = max_steps
        self.result_limit = result_limit
        self.planner = RunnableLambda(self._plan)

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

    def ask(
        self, question: str, job_id: str, *, history: list[dict[str, str]] | None = None,
        ui_context: dict[str, str] | None = None,
    ) -> ChatAnswer:
        observations: list[dict[str, Any]] = []
        observed_ids: set[str] = set()
        for step in range(self.max_steps):
            final_step = step == self.max_steps - 1
            _compact_observations(observations)
            decision = self.planner.invoke({
                "question": question, "job_id": job_id, "observations": observations,
                "history": history, "ui_context": ui_context, "final_step": final_step,
            })
            action = decision["action"]
            if action == "final":
                answer = decision.get("answer")
                if not isinstance(answer, str) or not answer.strip():
                    raise RuntimeError("TIA final action has no answer.")
                return _chat_answer(answer, observed_ids, (ui_context or {}).get("selected_record_id"), final_step)
            if final_step:
                break
            tool = self.tools.get(action)
            if tool is None:
                observations.append({"action": action, "error": "unknown_action"})
                continue
            arguments = decision.get("arguments") or {}
            if not isinstance(arguments, dict):
                observations.append({"action": action, "error": "arguments_must_be_object"})
                continue
            arguments["job_id"] = job_id  # conversation state is authoritative
            try:
                result = tool.invoke(arguments)
            except ReaderError as exc:
                result = exc.as_dict()
            except Exception as exc:
                result = {"error": type(exc).__name__, "message": str(exc)}
            else:
                # Keep citations verifiable after compaction, even beyond its first 50 IDs.
                observed_ids.update(_RECORD_ID.findall(json.dumps(result, default=str)))
            observations.append({"action": action, "arguments": arguments, "result": result})
        raise RuntimeError("Agent reached its step limit without producing a final answer.")

    def ask_text(self, question: str, job_id: str) -> str:
        """Return the legacy text-only answer for the terminal reviewer."""
        return self.ask(question, job_id).answer.strip()
