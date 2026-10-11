from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableLambda

from agent_poc.agent_tools import build_tools
from agent_poc.mcp_gateway import ComparisonGateway
from agent_poc.prompts import PLANNER_SYSTEM, planner_prompt
from agent_poc.tia_client import TiaTextClient


class TiaComparisonAgent:
    """LangChain-controlled action loop over comparison MCP data."""

    def __init__(self, tia: TiaTextClient, gateway: ComparisonGateway, max_steps: int = 5, result_limit: int = 8) -> None:
        self.tia = tia
        self.tools = build_tools(gateway, result_limit=result_limit)
        self.max_steps = max_steps
        self.planner = RunnableLambda(self._plan)

    def _plan(self, values: dict[str, Any]) -> dict[str, Any]:
        raw = self.tia.invoke(
            planner_prompt(
                values["question"],
                values["job_id"],
                values["observations"],
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

    def ask(self, question: str, job_id: str) -> str:
        observations: list[dict[str, Any]] = []
        for _ in range(self.max_steps):
            decision = self.planner.invoke({"question": question, "job_id": job_id, "observations": observations})
            action = decision["action"]
            if action == "final":
                answer = decision.get("answer")
                if not isinstance(answer, str) or not answer.strip():
                    raise RuntimeError("TIA final action has no answer.")
                return answer.strip()
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
            except Exception as exc:
                result = {"error": type(exc).__name__, "message": str(exc)}
            observations.append({"action": action, "arguments": arguments, "result": result})
        raise RuntimeError("Agent reached its step limit without producing a final answer.")

