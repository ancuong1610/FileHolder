from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol
from fastmcp import Client


class ReaderGateway(Protocol):
    """The three-tool read-only result interface used by the Q&A planner."""

    def summarize(self, job_id: str) -> dict[str, Any]: ...
    def search(self, job_id: str, **filters: Any) -> dict[str, Any]: ...
    def details(self, job_id: str, kind: str, record_id: str, **options: Any) -> dict[str, Any]: ...


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


DEFAULT_CALL_TIMEOUT = 60.0


def _await_future(future: Any, timeout: float) -> Any:
    """Bounded wait on a cross-loop future; cancel instead of leaking on timeout."""
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise


class StdioReaderGateway:
    """ReaderGateway over an active reader FastMCP stdio Client (three tools).

    Submission/polling use the workflow MCP client directly and never reach
    the Q&A planner. Results are not filtered from a full-report client.
    """

    def __init__(
        self, client: Client, loop: asyncio.AbstractEventLoop, timeout: float = DEFAULT_CALL_TIMEOUT
    ) -> None:
        self._client = client
        self._loop = loop
        self._timeout = timeout

    def _call_sync(self, name: str, arguments: dict[str, Any]) -> Any:
        coro = self._client.call_tool(name, arguments)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        result = _unwrap(_await_future(future, self._timeout))
        return result if isinstance(result, dict) else json.loads(result)

    def summarize(self, job_id: str) -> dict[str, Any]:
        return self._call_sync("summarize", {"job_id": job_id})

    def search(
        self,
        job_id: str,
        query: str = "",
        kind: str = "all",
        status: str = "all",
        change_type: str = "",
        cursor: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "job_id": job_id,
            "query": query,
            "kind": kind,
            "status": status,
            "change_type": change_type,
            "limit": limit,
        }
        if cursor is not None:
            arguments["cursor"] = cursor
        return self._call_sync("search", arguments)

    def details(
        self,
        job_id: str,
        kind: str,
        record_id: str,
        context_window: int = 1,
        cell_offset: int = 0,
        cell_limit: int = 20,
        text_offset: int = 0,
        text_limit: int = 4000,
        context_source_offset: int = 0,
    ) -> dict[str, Any]:
        return self._call_sync(
            "details",
            {
                "job_id": job_id,
                "kind": kind,
                "record_id": record_id,
                "context_window": context_window,
                "cell_offset": cell_offset,
                "cell_limit": cell_limit,
                "text_offset": text_offset,
                "text_limit": text_limit,
                "context_source_offset": context_source_offset,
            },
        )
