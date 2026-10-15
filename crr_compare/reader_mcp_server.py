"""FastMCP reader adapter: exactly three read-only result tools over the HTTP API.

Run:
    python -m crr_compare.reader_mcp_server

This entry point is separate from ``crr_compare.mcp_server`` (the five-tool
orchestration surface), so job submission/polling stays out of the Q&A planner.
Every call is scoped by a validated job ID and adapts the service read
endpoints; no result filtering is reimplemented here.

Tools:
    summarize(job_id)
    search(job_id, query="", kind="all", status="all", change_type="", cursor=None, limit=10)
    details(job_id, kind, record_id, context_window=1, cell_offset=0, cell_limit=20,
            text_offset=0, text_limit=4000, context_source_offset=0)
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastmcp import FastMCP

API_BASE = os.getenv("CRR_API_BASE", "http://127.0.0.1:8321").rstrip("/")
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")

mcp = FastMCP("PDF Comparison Reader")


def _job_id(value: str) -> str:
    if not isinstance(value, str) or not JOB_ID_RE.fullmatch(value):
        raise ValueError("job_id must be 12 lowercase hexadecimal characters")
    return value


def _get(path: str, params: dict[str, Any]) -> dict:
    clean = {key: value for key, value in params.items() if value is not None}
    try:
        response = httpx.get(API_BASE + path, params=clean, timeout=60)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Comparison reader request failed: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict) and isinstance(detail.get("error"), str):
            return {"error": detail["error"], "message": str(detail.get("message") or "")}
        return {"error": "request_failed", "message": f"reader request failed with status {response.status_code}"}
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError("Comparison reader returned a non-JSON response") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Comparison reader returned a non-object response")
    return value


def summarize(job_id: str) -> dict:
    """Return a bounded status summary that is safe for unfinished jobs."""
    return _get(f"/jobs/{_job_id(job_id)}/read/summary", {})


def search(
    job_id: str,
    query: str = "",
    kind: str = "all",
    status: str = "all",
    change_type: str = "",
    cursor: str | None = None,
    limit: int = 10,
) -> dict:
    """Literal/token search across changes, tables (cell text) and review records."""
    return _get(
        f"/jobs/{_job_id(job_id)}/read/search",
        {
            "query": query,
            "kind": kind,
            "status": status,
            "change_type": change_type,
            "cursor": cursor,
            "limit": limit,
        },
    )


def details(
    job_id: str,
    kind: str,
    record_id: str,
    context_window: int = 1,
    cell_offset: int = 0,
    cell_limit: int = 20,
    text_offset: int = 0,
    text_limit: int = 4000,
    context_source_offset: int = 0,
) -> dict:
    """Return canonical old/new values, evidence and bounded document context.

    Long text/cells/context are returned in an explicit offset/limit window with
    ``text_next_offset`` as the continuation pointer when more text remains. For
    a review record, ``context`` groups bounded neighbour text by source anchor
    id and pages anchors with ``context_source_offset``.
    """
    return _get(
        f"/jobs/{_job_id(job_id)}/read/details",
        {
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


for tool in (summarize, search, details):
    mcp.tool(tool)


if __name__ == "__main__":
    mcp.run()
