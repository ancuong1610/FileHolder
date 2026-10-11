from __future__ import annotations

import asyncio
import importlib
import json
import re
from pathlib import Path
from typing import Any, Protocol
from fastmcp import Client
from agent_poc.api_client import ComparisonApiClient

class ComparisonGateway(Protocol):
    def status(self, job_id: str) -> dict[str, Any]: ...
    def report(self, job_id: str) -> dict[str, Any]: ...
    def structured_changes(self, job_id: str) -> dict[str, Any]: ...


class McpHandlerGateway:
    """Calls the same registered functions exposed by crr_compare.mcp_server.

    This is intentionally in-process for the agent CLI. Use mcp_smoke_test.py
    separately to verify the actual stdio MCP transport.
    """

    def __init__(self) -> None:
        self.module = importlib.import_module("crr_compare.mcp_server")

    def status(self, job_id: str) -> dict[str, Any]:
        return self.module.check_status(job_id)

    def report(self, job_id: str) -> dict[str, Any]:
        return self.module.retrieve_report(job_id)

    def structured_changes(self, job_id: str) -> dict[str, Any]:
        return self.module.retrieve_structured_changes(job_id)

class ApiGateway:
    """Calls the comparison FastAPI service directly."""

    def __init__(self, api_base: str) -> None:
        self.client = ComparisonApiClient(api_base)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.client.status(job_id)

    def report(self, job_id: str) -> dict[str, Any]:
        return self.client.report(job_id)

    def structured_changes(self, job_id: str) -> dict[str, Any]:
        return self.client.changes(job_id)

class MockGateway:
    def __init__(self, report_path: Path) -> None:
        self.canonical = json.loads(report_path.read_text(encoding="utf-8"))

    def status(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)]}

    def report(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "report": self.canonical}

    def structured_changes(self, job_id: str) -> dict[str, Any]:
        changes = self.canonical.get("changes", [])
        return {"count": len(changes), "changes": changes}


def _unwrap(result: Any) -> Any:
    for attr in ("data", "structured_content"):
        value = getattr(result, attr, None)
        if value is not None:
            return value
    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", None)
        if text is not None:
            return text
    return result


class StdioMcpGateway:
    """Sync ComparisonGateway over an active FastMCP stdio Client owned by another loop."""

    def __init__(self, client: Client, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    def _call_sync(self, name: str, arguments: dict[str, Any]) -> Any:
        coro = self._client.call_tool(name, arguments)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        result = _unwrap(future.result())
        return result if isinstance(result, dict) else json.loads(result)

    def status(self, job_id: str) -> dict[str, Any]:
        return self._call_sync("check_status", {"job_id": job_id})

    def report(self, job_id: str) -> dict[str, Any]:
        return self._call_sync("retrieve_report", {"job_id": job_id})

    def structured_changes(self, job_id: str) -> dict[str, Any]:
        return self._call_sync("retrieve_structured_changes", {"job_id": job_id})


def canonical_from(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("report", payload)
    if not isinstance(report, dict):
        raise TypeError("MCP report payload has no canonical report object.")
    return report


def searchable_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            parts.append(str(item))

    visit(value)
    return " ".join(parts).casefold()


def matches_query(item: dict[str, Any], query: str) -> bool:
    terms = re.findall(r"[\w.-]+", query.casefold())
    haystack = searchable_text(item)
    return not terms or all(term in haystack for term in terms)

