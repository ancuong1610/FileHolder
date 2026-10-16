"""FastMCP adapter for the generic PDF comparison HTTP API."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

from document_compare.shared.paths import REPO_ROOT


API_BASE = os.getenv("CRR_API_BASE", "http://127.0.0.1:8321").rstrip("/")
UPLOAD_ROOT = Path(os.getenv("CRR_MCP_UPLOAD_ROOT", REPO_ROOT / "workspace" / "uploads")).resolve()
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
mcp = FastMCP("PDF Comparison")


def _request(method: str, path: str, **kwargs: Any) -> dict:
    try:
        response = httpx.request(method, API_BASE + path, timeout=60, **kwargs)
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Comparison API request failed: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Comparison API returned a non-object response")
    return value


def _job_id(value: str) -> str:
    if not JOB_ID_RE.fullmatch(value):
        raise ValueError("job_id must be 12 lowercase hexadecimal characters")
    return value


def _validate_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(
            "session_id must be 1-128 characters using only [A-Za-z0-9_-]"
        )


def submit_comparison(old_pdf_path: str, new_pdf_path: str, session_id: str) -> dict:
    """Submit two PDFs under the configured upload root for comparison."""
    _validate_session_id(session_id)
    paths = [Path(value).expanduser().resolve() for value in (old_pdf_path, new_pdf_path)]
    if any(path.suffix.casefold() != ".pdf" or not path.is_file() for path in paths):
        raise ValueError("Both uploads must be existing PDF files")
    if any(not path.is_relative_to(UPLOAD_ROOT) for path in paths):
        raise ValueError(f"Uploads must be under {UPLOAD_ROOT}")
    for path in paths:
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(f"{path.name} is not a PDF (missing %PDF- signature)")
    with paths[0].open("rb") as old_file, paths[1].open("rb") as new_file:
        return _request("POST", "/jobs", files={
            "old_pdf": (paths[0].name, old_file, "application/pdf"),
            "new_pdf": (paths[1].name, new_file, "application/pdf"),
        }, data={"session_id": session_id})


def check_status(job_id: str) -> dict:
    """Return pipeline state and stage progress for a job."""
    return _request("GET", f"/jobs/{_job_id(job_id)}/status")


def retrieve_report(job_id: str) -> dict:
    """Return the complete canonical comparison report for a job."""
    return _request("GET", f"/jobs/{_job_id(job_id)}/report")


def retrieve_structured_changes(job_id: str) -> dict:
    """Return the canonical structured changes for a job."""
    return _request("GET", f"/jobs/{_job_id(job_id)}/changes")


def retrieve_summary(job_id: str) -> dict[str, Any]:
    """Return the current-run health summary for a job."""
    return _request("GET", f"/jobs/{_job_id(job_id)}/summary")


for tool in (
    submit_comparison,
    check_status,
    retrieve_report,
    retrieve_structured_changes,
    retrieve_summary,
):
    mcp.tool(tool)


if __name__ == "__main__":
    mcp.run()