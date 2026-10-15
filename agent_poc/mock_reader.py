"""Offline, credential-free three-tool reader demonstration.

Run:

    python -m agent_poc.mock_reader --out workspace/reader-mock

This wires the *real* transport path end to end, with no credentials, no
provider call and no DI pipeline:

    TiaComparisonAgent (scripted mock planner)
      -> LangChain StructuredTools (exactly summarize/search/details)
      -> reader FastMCP stdio process (crr_compare.reader_mcp_server)
      -> real httpx loopback
      -> real FastAPI service (crr_compare.service, owned uvicorn thread,
         bound loopback random port)
      -> reusable read projection over independently authored synthetic
         canonical fixtures saved on disk

The fixtures, the job identities and the expected truth are authored *before*
any query runs. Expected values are never derived from a reader response.

What this verifies: the read contract and the agent/tool/MCP/HTTP wiring. What
it does NOT verify: real-model reasoning, semantic accuracy, retrieval recall,
prompt-injection resistance or real-company document correctness. The scripted
planner replaces a language model; it exercises transport and contracts only.

Safety: service.JOBS_ROOT is redirected to a private temp directory for this
process only and the in-memory job cache is isolated and restored on exit.
Private PDFs and raw DI are never read, and no network beyond loopback is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

from agent_poc.agent import TiaComparisonAgent
from agent_poc.mcp_gateway import StdioReaderGateway
from crr_compare import service

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "workspace" / "reader-mock"

JOB_MAIN = "111111111111"
JOB_OTHER = "222222222222"
JOB_UNFINISHED = "333333333333"

MAIN_MARKER = "payment"
OTHER_MARKER = "OTHERJOBMARKER"
TABLE_MARKER = "cellmarker"
LONG_PARA_TAIL = "TAILENDMARKER"
LONG_PARA_PREFIX = "LONGPARA "
LONG_PARA_TEXT = LONG_PARA_PREFIX + ("A" * 4100) + LONG_PARA_TAIL

RESULT_LIMIT = 2
READ_TOOLS = ("summarize", "search", "details")

MOCK_NOTICE = (
    "Mock/scripted planner: verifies the read contract and the agent->tools->"
    "reader-MCP->HTTP->service wiring only. It does not verify real LLM "
    "reasoning, semantic accuracy, retrieval recall or prompt-injection "
    "resistance."
)


# ---------------------------------------------------------------------------
# Independently authored fixtures (written before any query runs)
# ---------------------------------------------------------------------------

def _resolution(status: str, needs_review: bool, reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "route": "ACCEPT_DETERMINISTIC" if status == "accepted" else "REVIEW_REQUIRED",
        "case_type": "modified",
        "method": "deterministic",
        "status": status,
        "needs_review": needs_review,
        "review_reasons": reasons or [],
    }


def _change(
    index: int,
    ctype: str,
    old_text: str,
    new_text: str,
    *,
    status: str = "accepted",
    needs_review: bool = False,
    reasons: list[str] | None = None,
    new_page: int = 2,
) -> dict[str, Any]:
    return {
        "change_id": f"CHANGE-{index:06d}",
        "source_candidate_id": f"C{index}",
        "source_group": "modified",
        "change_type": ctype,
        "old": {
            "element_id": f"OLD-{index}",
            "element_type": "PARA",
            "text": old_text,
            "normalized_text": old_text.casefold(),
            "comparison_text": old_text.casefold(),
            "primary_page": 1,
            "reading_order": index - 1,
            "provisional_anchor_context": [],
        },
        "new": {
            "element_id": f"NEW-{index}",
            "element_type": "PARA",
            "text": new_text,
            "normalized_text": new_text.casefold(),
            "comparison_text": new_text.casefold(),
            "primary_page": new_page,
            "reading_order": index - 1,
            "provisional_anchor_context": [],
        },
        "token_diff": {"source_token_diff": {"counts": {"replace": 1}}},
        "similarity": 0.5,
        "resolution": _resolution(status, needs_review, reasons),
        "provenance": {"step9_candidate_id": f"C{index}", "matching_not_recomputed": True},
    }


def _cell(cell_id: str, text: str, row: int, column: int) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "source_table_id": "OLD-T1",
        "source_row_index": row,
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "kind": "content",
        "text": text,
        "normalized_text": text.casefold(),
        "primary_page": 3,
        "primary_polygon": None,
        "spans": [],
    }


def _table_change() -> dict[str, Any]:
    # Six cell changes so cell pagination needs three pages at cell_limit=2; the
    # changed quantity cell is last so reaching it proves pagination completed.
    rows = [
        ("Item", "Widget", 0, 0),
        ("Quantity", "3", 0, 1),
        ("Item", "Gadget", 1, 0),
        ("Quantity", "5", 1, 1),
        ("Item", "Gizmo", 2, 0),
    ]
    cell_changes: list[dict[str, Any]] = []
    for index, (header, value, row, column) in enumerate(rows):
        cell_changes.append(
            {
                "change_id": f"TABLE-0001-R{row:05d}-C{column:03d}",
                "classification": "UNCHANGED",
                "old": _cell(f"OC{index}", value, row, column),
                "new": _cell(f"NC{index}", value, row, column),
                "old_row_index": row,
                "new_row_index": row,
                "column_index": column,
                "row_match_type": "matched",
                "row_similarity": 1.0,
                "_header": header,
            }
        )
    cell_changes.append(
        {
            "change_id": "TABLE-0001-R00002-C001",
            "classification": "MODIFIED",
            "old": _cell("OC5", f"{TABLE_MARKER} qty 21", 2, 1),
            "new": _cell("NC5", f"{TABLE_MARKER} qty 99", 2, 1),
            "old_row_index": 2,
            "new_row_index": 2,
            "column_index": 1,
            "row_match_type": "matched",
            "row_similarity": 0.8,
            "_header": "Quantity",
        }
    )
    for change in cell_changes:
        change.pop("_header", None)
    return {
        "table_change_id": "TABLE-CHANGE-00001",
        "source_table_match_id": "TABLE-MATCH-0001",
        "change_type": "TABLE_MODIFIED",
        "classification": "MODIFIED",
        "old": {
            "logical_table_id": "OLD-T1",
            "header_signature": ["Item", "Quantity"],
            "row_count": 3,
            "column_count": 2,
        },
        "new": {
            "logical_table_id": "NEW-T1",
            "header_signature": ["Item", "Quantity"],
            "row_count": 3,
            "column_count": 2,
        },
        "cell_changes": cell_changes,
        "resolution": _resolution("accepted", False),
        "review_reasons": [],
    }


def canonical_main() -> dict[str, Any]:
    changes = [
        _change(1, "MODIFIED", "Invoice payment is due in 21 days", "Invoice payment is due in 99 days"),
        _change(2, "MODIFIED", "payment clause alpha original", "payment clause alpha revised"),
        _change(3, "MODIFIED", "payment clause beta original", "payment clause beta revised"),
        _change(4, "MODIFIED", "payment clause gamma original", "payment clause gamma revised"),
        _change(
            5,
            "DELETED",
            "payment clause removed",
            "",
            status="review",
            needs_review=True,
            reasons=["semantic_signal"],
        ),
        _change(6, "ADDED", "", "payment clause newly added"),
        _change(7, "MODIFIED", "long paragraph start", LONG_PARA_TEXT),
    ]
    review = [
        {
            "review_id": "REVIEW-000001",
            "source_kind": "non_table_change",
            "change_id": "CHANGE-000005",
            "source_candidate_id": "C5",
            "case_type": "modified_review",
            "resolution": _resolution("review", True, ["semantic_signal"]),
        }
    ]
    return {
        "schema_version": "3.0.0",
        "comparison_stage": "phase_1_final_comparison",
        "old_document": {
            "document_id": "OLD-DOC",
            "page_count": 4,
            "source_element_count": 40,
            "provisional_anchor_count": 10,
        },
        "new_document": {
            "document_id": "NEW-DOC",
            "page_count": 5,
            "source_element_count": 41,
            "provisional_anchor_count": 11,
        },
        "processing": {"version": "reader-mock", "matching_recomputed": False, "llm_optional": True},
        "statistics": {
            "changes": 7,
            "table_changes": 1,
            "review_queue": 1,
            "change_type_counts": {"MODIFIED": 5, "DELETED": 1, "ADDED": 1, "TABLE_MODIFIED": 1},
            "resolution_status_counts": {"accepted": 7, "review": 1},
        },
        "changes": changes,
        "table_changes": [_table_change()],
        "review_queue": review,
        "non_semantic_events": [],
        "unresolved_table_reviews": [],
    }


def canonical_other() -> dict[str, Any]:
    change = _change(
        1,
        "MODIFIED",
        f"{OTHER_MARKER} alpha license",
        f"{OTHER_MARKER} beta license",
    )
    return {
        "schema_version": "3.0.0",
        "comparison_stage": "phase_1_final_comparison",
        "old_document": {"document_id": "OTHER-OLD", "page_count": 1, "source_element_count": 2},
        "new_document": {"document_id": "OTHER-NEW", "page_count": 1, "source_element_count": 2},
        "processing": {"version": "reader-mock", "matching_recomputed": False, "llm_optional": True},
        "statistics": {"changes": 1, "table_changes": 0, "review_queue": 0},
        "changes": [change],
        "table_changes": [],
        "review_queue": [],
        "non_semantic_events": [],
        "unresolved_table_reviews": [],
    }


def _structured(prefix: str) -> dict[str, Any]:
    elements = [
        {
            "element_id": f"{prefix}-{index}",
            "element_type": "PARA",
            "reading_order": index - 1,
            "primary_page": 1,
            "text": f"{prefix} context line {index}",
        }
        for index in range(1, 8)
    ]
    return {"document": {"document_id": f"{prefix}-DOC", "elements": elements}}


def _fixture_files() -> dict[str, dict[str, Any]]:
    return {
        JOB_MAIN: {
            "job_status.json": {"job_id": JOB_MAIN, "session_id": "s-main", "state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)]},
            "output/comparison_result.json": canonical_main(),
            "output/old_document_structured.json": _structured("OLD"),
            "output/new_document_structured.json": _structured("NEW"),
        },
        JOB_OTHER: {
            "job_status.json": {"job_id": JOB_OTHER, "session_id": "s-other", "state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)]},
            "output/comparison_result.json": canonical_other(),
        },
        JOB_UNFINISHED: {
            "job_status.json": {"job_id": JOB_UNFINISHED, "session_id": "s-run", "state": "running", "stages_completed": ["01", "02"]},
        },
    }


def write_fixtures(jobs_root: Path) -> None:
    for job_id, files in _fixture_files().items():
        for relative, payload in files.items():
            path = jobs_root / job_id / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# Authoritative expected truth, authored before any query runs.
EXPECTED = {
    "overview": {"state": "done", "complete": True, "changes": 7, "table_changes": 1, "review_open": 1},
    "search_details_numeric_context": {
        "record_id": "CHANGE-000001",
        "old_has_21": True,
        "new_has_99": True,
        "context_available": True,
    },
    "review_distinction": {
        "review_record_id": "REVIEW-000001",
        "review_needs_review": True,
        "review_confirmed": False,
        "accepted_all_confirmed": True,
    },
    "multipage_complete": {"pages": 3, "collected": 6, "total_matches": 6, "unique_ids": 6},
    "table_cell_pagination": {
        "pages": 3,
        "collected": 6,
        "cell_total": 6,
        "last_old_has_21": True,
        "last_new_has_99": True,
        "header": ["Item", "Quantity"],
    },
    "long_text_tail": {
        "text_length": len(LONG_PARA_TEXT),
        "first_truncated": True,
        "first_next_offset": 4000,
        "tail_has_marker": True,
        "second_next_offset": None,
    },
    "empty_search": {"total_matches": 0, "empty": True, "returned_len": 0, "truncated": False, "next_cursor": None},
    "missing_reference": {"error_code": "not_found"},
    "unfinished_job": {"state": "running", "complete": False, "search_error": "not_ready", "limitations": ["job_not_done"]},
    "attempted_job_switch": {
        "planner_requested_other": True,
        "all_calls_bound_to_main": True,
        "main_marker_present": True,
        "other_marker_present": False,
    },
    "forbidden_action": {"unknown_action": True, "gateway_calls": 0, "write_calls": 0},
    "step_budget_bounded": {"error_type": "RuntimeError", "steps": 3},
}


# ---------------------------------------------------------------------------
# Scripted planner: reads OBSERVATIONS_JSON from the prompt and reacts to the
# real returned values/references/cursors (never a canned expected answer).
# ---------------------------------------------------------------------------

def _extract_observations(prompt: str) -> list[dict[str, Any]]:
    marker = "OBSERVATIONS_JSON:\n"
    if marker not in prompt:
        return []
    start = prompt.index(marker) + len(marker)
    end = prompt.find("\nChoose the next action.", start)
    raw = prompt[start:] if end < 0 else prompt[start:end]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


class ScriptedPlanner:
    def __init__(self, strategy: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> None:
        self.strategy = strategy
        self.decisions: list[dict[str, Any]] = []
        self.seen_observations: list[dict[str, Any]] = []

    def invoke(self, prompt: str, system_message: str) -> str:
        self.seen_observations = _extract_observations(prompt)
        decision = self.strategy(self.seen_observations)
        self.decisions.append(decision)
        return json.dumps(decision)


class TracingReaderGateway:
    """Records every gateway call name/arguments/result for the report."""

    def __init__(self, inner: StdioReaderGateway) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def _record(self, name: str, arguments: dict[str, Any], call: Callable[[], Any]) -> dict[str, Any]:
        result = call()
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result

    def summarize(self, job_id: str) -> dict[str, Any]:
        return self._record("summarize", {"job_id": job_id}, lambda: self.inner.summarize(job_id))

    def search(self, job_id: str, **filters: Any) -> dict[str, Any]:
        return self._record(
            "search", {"job_id": job_id, **filters}, lambda: self.inner.search(job_id, **filters)
        )

    def details(self, job_id: str, kind: str, record_id: str, **options: Any) -> dict[str, Any]:
        arguments = {"job_id": job_id, "kind": kind, "record_id": record_id, **options}
        return self._record(
            "details", arguments, lambda: self.inner.details(job_id, kind, record_id, **options)
        )


# ---------------------------------------------------------------------------
# Scenario strategies (pure functions of the observation history)
# ---------------------------------------------------------------------------

def _last_action(observations: list[dict[str, Any]]) -> str | None:
    return observations[-1].get("action") if observations else None


def _last_result(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not observations:
        return None
    result = observations[-1].get("result")
    return result if isinstance(result, dict) else None


def _results_of(observations: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [o["result"] for o in observations if o.get("action") == action and isinstance(o.get("result"), dict)]


def _strategy_overview(observations):
    if _last_action(observations) != "summarize":
        return {"action": "summarize", "arguments": {}}
    summary = _last_result(observations) or {}
    counts = summary.get("counts") or {}
    return {
        "action": "final",
        "answer": (
            f"state={summary.get('state')} complete={summary.get('complete')} "
            f"changes={counts.get('changes')} table_changes={counts.get('table_changes')} "
            f"review_open={counts.get('review_open')}"
        ),
    }


def _observe_overview(answer, observations, calls, error):
    summary = (_results_of(observations, "summarize") or [{}])[0]
    counts = summary.get("counts") or {}
    return {
        "state": summary.get("state"),
        "complete": summary.get("complete"),
        "changes": counts.get("changes"),
        "table_changes": counts.get("table_changes"),
        "review_open": counts.get("review_open"),
    }


def _strategy_numeric(observations):
    action = _last_action(observations)
    if action is None:
        return {"action": "search", "arguments": {"query": "due", "kind": "all"}}
    if action == "search":
        result = _last_result(observations) or {}
        record = next((r for r in result.get("returned") or [] if r.get("kind") == "change"), None)
        if record is None:
            return {"action": "final", "answer": "no change found"}
        return {
            "action": "details",
            "arguments": {"kind": "change", "record_id": record["record_id"], "context_window": 1},
        }
    if action == "details":
        detail = _last_result(observations) or {}
        old_text = ((detail.get("change") or {}).get("old") or {}).get("text", "")
        new_text = ((detail.get("change") or {}).get("new") or {}).get("text", "")
        context = ((detail.get("context") or {}).get("new") or {}).get("available")
        return {"action": "final", "answer": f"old={old_text} new={new_text} context_available={context}"}
    return {"action": "final", "answer": "done"}


def _observe_numeric(answer, observations, calls, error):
    search = (_results_of(observations, "search") or [{}])[0]
    returned = search.get("returned") or []
    record_id = returned[0].get("record_id") if returned else None
    detail = (_results_of(observations, "details") or [{}])[0]
    old_text = ((detail.get("change") or {}).get("old") or {}).get("text", "")
    new_text = ((detail.get("change") or {}).get("new") or {}).get("text", "")
    context = ((detail.get("context") or {}).get("new") or {}).get("available")
    return {
        "record_id": record_id,
        "old_has_21": "21" in old_text,
        "new_has_99": "99" in new_text,
        "context_available": bool(context),
    }


def _strategy_review(observations):
    searches = [o for o in observations if o.get("action") == "search"]
    if len(searches) == 0:
        return {"action": "search", "arguments": {"query": "", "kind": "review"}}
    if len(searches) == 1:
        return {"action": "search", "arguments": {"query": "", "kind": "change", "status": "accepted"}}
    return {"action": "final", "answer": "review items distinguished from accepted changes"}


def _observe_review(answer, observations, calls, error):
    searches = _results_of(observations, "search")
    review_records = (searches[0].get("returned") or []) if searches else []
    accepted_records = (searches[1].get("returned") or []) if len(searches) > 1 else []
    review = review_records[0] if review_records else {}
    return {
        "review_record_id": review.get("record_id"),
        "review_needs_review": review.get("needs_review"),
        "review_confirmed": review.get("confirmed"),
        "accepted_all_confirmed": bool(accepted_records)
        and all(r.get("confirmed") and not r.get("needs_review") for r in accepted_records),
    }


def _strategy_multipage(observations):
    searches = [o for o in observations if o.get("action") == "search"]
    if not searches:
        return {"action": "search", "arguments": {"query": MAIN_MARKER, "kind": "all"}}
    last = searches[-1].get("result") or {}
    if last.get("truncated"):
        return {
            "action": "search",
            "arguments": {"query": MAIN_MARKER, "kind": "all", "cursor": last.get("next_cursor")},
        }
    collected = sum(len((s.get("result") or {}).get("returned") or []) for s in searches)
    return {"action": "final", "answer": f"total_matches={last.get('total_matches')} collected={collected}"}


def _observe_multipage(answer, observations, calls, error):
    searches = _results_of(observations, "search")
    ids = [r.get("record_id") for s in searches for r in (s.get("returned") or [])]
    total = searches[-1].get("total_matches") if searches else None
    return {
        "pages": len(searches),
        "collected": len(ids),
        "total_matches": total,
        "unique_ids": len(set(ids)),
    }


def _strategy_table(observations):
    action = _last_action(observations)
    if action is None:
        return {"action": "search", "arguments": {"query": TABLE_MARKER, "kind": "table"}}
    if action == "search":
        result = _last_result(observations) or {}
        record = next((r for r in result.get("returned") or [] if r.get("kind") == "table"), None)
        if record is None:
            return {"action": "final", "answer": "no table found"}
        return {
            "action": "details",
            "arguments": {"kind": "table", "record_id": record["record_id"], "cell_offset": 0, "cell_limit": 2},
        }
    if action == "details":
        detail = _last_result(observations) or {}
        table = detail.get("table") or {}
        if table.get("cell_changes_truncated"):
            return {
                "action": "details",
                "arguments": {
                    "kind": "table",
                    "record_id": detail.get("record_id"),
                    "cell_offset": table.get("next_offset"),
                    "cell_limit": 2,
                },
            }
        return {"action": "final", "answer": "table cells paged to the last cell"}
    return {"action": "final", "answer": "done"}


def _observe_table(answer, observations, calls, error):
    details = _results_of(observations, "details")
    cells = [c for d in details for c in (d.get("table") or {}).get("cell_changes") or []]
    last_cell = cells[-1] if cells else {}
    old_text = (last_cell.get("old") or {}).get("text", "")
    new_text = (last_cell.get("new") or {}).get("text", "")
    header = ((details[-1].get("table") or {}).get("new") or {}).get("header_signature") if details else None
    total = (details[-1].get("table") or {}).get("cell_total") if details else None
    return {
        "pages": len(details),
        "collected": len(cells),
        "cell_total": total,
        "last_old_has_21": "21" in old_text,
        "last_new_has_99": "99" in new_text,
        "header": header,
    }


def _strategy_long(observations):
    action = _last_action(observations)
    if action is None:
        return {"action": "search", "arguments": {"query": LONG_PARA_PREFIX.strip().casefold(), "kind": "change"}}
    if action == "search":
        result = _last_result(observations) or {}
        record = next((r for r in result.get("returned") or [] if r.get("kind") == "change"), None)
        if record is None:
            return {"action": "final", "answer": "no long paragraph change found"}
        return {
            "action": "details",
            "arguments": {"kind": "change", "record_id": record["record_id"], "text_limit": 4000},
        }
    if action == "details":
        detail = _last_result(observations) or {}
        new = (detail.get("change") or {}).get("new") or {}
        next_offset = new.get("text_next_offset")
        if next_offset is None:
            return {"action": "final", "answer": "no continuation available"}
        return {
            "action": "details",
            "arguments": {"kind": "change", "record_id": detail.get("record_id"), "text_offset": next_offset, "text_limit": 4000},
        }
    return {"action": "final", "answer": "long paragraph tail retrieved"}


def _observe_long(answer, observations, calls, error):
    details = _results_of(observations, "details")
    first = ((details[0].get("change") or {}).get("new") or {}) if details else {}
    second = ((details[1].get("change") or {}).get("new") or {}) if len(details) > 1 else {}
    return {
        "text_length": first.get("text_length"),
        "first_truncated": first.get("text_truncated"),
        "first_next_offset": first.get("text_next_offset"),
        "tail_has_marker": LONG_PARA_TAIL in (second.get("text") or ""),
        "second_next_offset": second.get("text_next_offset"),
    }


def _strategy_empty(observations):
    if not observations:
        return {"action": "search", "arguments": {"query": "zzz-nomatch-zzz", "kind": "all"}}
    return {"action": "final", "answer": "no matches"}


def _observe_empty(answer, observations, calls, error):
    search = (_results_of(observations, "search") or [{}])[0]
    return {
        "total_matches": search.get("total_matches"),
        "empty": search.get("empty"),
        "returned_len": len(search.get("returned") or []),
        "truncated": search.get("truncated"),
        "next_cursor": search.get("next_cursor"),
    }


def _strategy_missing(observations):
    action = _last_action(observations)
    if action is None:
        return {"action": "search", "arguments": {"query": "due", "kind": "change"}}
    if action == "search":
        return {"action": "details", "arguments": {"kind": "change", "record_id": "CHANGE-999999"}}
    return {"action": "final", "answer": "missing reference reported, nothing invented"}


def _observe_missing(answer, observations, calls, error):
    detail = (_results_of(observations, "details") or [{}])[0]
    return {"error_code": detail.get("error")}


def _strategy_unfinished(observations):
    action = _last_action(observations)
    if action is None:
        return {"action": "summarize", "arguments": {}}
    if action == "summarize":
        # Deliberately attempt a search to prove unfinished jobs are rejected.
        return {"action": "search", "arguments": {"query": MAIN_MARKER, "kind": "all"}}
    return {"action": "final", "answer": "job is not done; results are not ready"}


def _observe_unfinished(answer, observations, calls, error):
    summary = (_results_of(observations, "summarize") or [{}])[0]
    search = (_results_of(observations, "search") or [{}])[0]
    return {
        "state": summary.get("state"),
        "complete": summary.get("complete"),
        "search_error": search.get("error"),
        "limitations": summary.get("limitations"),
    }


def _strategy_switch(observations):
    if not observations:
        # Attempted job switch: the planner asks for a different job's data.
        return {
            "action": "search",
            "arguments": {"job_id": JOB_OTHER, "query": MAIN_MARKER, "kind": "all"},
        }
    return {"action": "final", "answer": "conversation bound job is authoritative"}


def _observe_switch(answer, observations, calls, error):
    all_bound = bool(calls) and all(c.get("arguments", {}).get("job_id") == JOB_MAIN for c in calls)
    blob = json.dumps([c.get("result") for c in calls], ensure_ascii=False).casefold()
    return {
        "all_calls_bound_to_main": all_bound,
        "main_marker_present": MAIN_MARKER in blob,
        "other_marker_present": OTHER_MARKER.casefold() in blob,
    }


def _strategy_forbidden(observations):
    if not observations:
        # No MCP write tool exists; this must surface as unknown_action.
        return {"action": "submit_comparison", "arguments": {"old_pdf_path": "/etc/passwd"}}
    return {"action": "final", "answer": "write action refused; only read tools exist"}


def _observe_forbidden(answer, observations, calls, error):
    first = observations[0] if observations else {}
    write_calls = [c for c in calls if c.get("name") not in READ_TOOLS]
    return {
        "unknown_action": first.get("error") == "unknown_action",
        "gateway_calls": len(calls),
        "write_calls": len(write_calls),
    }


def _strategy_budget(observations):
    # Never finishes: the agent must stop at max_steps instead of looping.
    return {"action": "summarize", "arguments": {}}


def _observe_budget(answer, observations, calls, error):
    return {
        "error_type": "RuntimeError" if error and error.startswith("RuntimeError") else error,
        "steps": len(calls),
    }


class Scenario:
    def __init__(self, name, question, job_id, strategy, observe, *, max_steps=8):
        self.name = name
        self.question = question
        self.job_id = job_id
        self.strategy = strategy
        self.observe = observe
        self.max_steps = max_steps
        self.expected = EXPECTED[name]


SCENARIOS = [
    Scenario("overview", "What is the overall state of this comparison?", JOB_MAIN, _strategy_overview, _observe_overview),
    Scenario(
        "search_details_numeric_context",
        "What numeric change was made and what surrounds it?",
        JOB_MAIN,
        _strategy_numeric,
        _observe_numeric,
    ),
    Scenario("review_distinction", "Which items are unresolved review items?", JOB_MAIN, _strategy_review, _observe_review),
    Scenario("multipage_complete", "List all payment changes.", JOB_MAIN, _strategy_multipage, _observe_multipage),
    Scenario("table_cell_pagination", "Show the table cell changes.", JOB_MAIN, _strategy_table, _observe_table),
    Scenario("long_text_tail", "Show the end of the long paragraph change.", JOB_MAIN, _strategy_long, _observe_long),
    Scenario("empty_search", "Search for something that does not exist.", JOB_MAIN, _strategy_empty, _observe_empty),
    Scenario("missing_reference", "Open a record that does not exist.", JOB_MAIN, _strategy_missing, _observe_missing),
    Scenario("unfinished_job", "What is the status while the job is running?", JOB_UNFINISHED, _strategy_unfinished, _observe_unfinished),
    Scenario(
        "attempted_job_switch",
        "Show the other document's changes.",
        JOB_MAIN,
        _strategy_switch,
        _observe_switch,
    ),
    Scenario("forbidden_action", "Delete the comparison result.", JOB_MAIN, _strategy_forbidden, _observe_forbidden),
    Scenario("step_budget_bounded", "Keep looking forever.", JOB_MAIN, _strategy_budget, _observe_budget, max_steps=3),
]


# ---------------------------------------------------------------------------
# Owned process lifecycle (bounded readiness, cleanup even on exception)
# ---------------------------------------------------------------------------

def _start_service() -> tuple[uvicorn.Server, threading.Thread, socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    config = uvicorn.Config(service.app, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="reader-mock-http", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("reader mock HTTP service exited before becoming ready")
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("reader mock HTTP service did not become ready within 15s")

    base = f"http://127.0.0.1:{port}"
    probe_deadline = time.monotonic() + 10
    while time.monotonic() < probe_deadline:
        try:
            if httpx.get(f"{base}/openapi.json", timeout=1.0).status_code == 200:
                return server, thread, sock, port
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("reader mock HTTP service openapi.json unreachable")


def _stop_service(server: uvicorn.Server | None, thread: threading.Thread | None) -> None:
    if server is None:
        return
    server.should_exit = True
    if thread is not None:
        thread.join(timeout=10)


async def _run_scenario(scenario: Scenario, gateway: StdioReaderGateway) -> dict[str, Any]:
    planner = ScriptedPlanner(scenario.strategy)
    tracing = TracingReaderGateway(gateway)
    agent = TiaComparisonAgent(planner, tracing, max_steps=scenario.max_steps, result_limit=RESULT_LIMIT)
    answer: str | None = None
    error: str | None = None
    try:
        answer = await asyncio.to_thread(agent.ask, scenario.question, scenario.job_id)
    except RuntimeError as exc:
        error = f"{type(exc).__name__}: {exc}"
    observed = scenario.observe(answer, planner.seen_observations, tracing.calls, error)
    observed["planner_requested_other"] = any(
        isinstance(d.get("arguments"), dict)
        and isinstance(d["arguments"].get("job_id"), str)
        and d["arguments"]["job_id"] != scenario.job_id
        for d in planner.decisions
    )
    passed = all(observed.get(key) == value for key, value in scenario.expected.items())
    return {
        "name": scenario.name,
        "question": scenario.question,
        "job_id": scenario.job_id,
        "expected": scenario.expected,
        "observed": observed,
        "pass": passed,
        "final_answer": answer,
        "error": error,
        "steps": [d.get("action") for d in planner.decisions],
        "trace": tracing.calls,
        "limits": {"max_steps": scenario.max_steps, "result_limit": RESULT_LIMIT},
    }


async def _run_harness(jobs_root: Path) -> dict[str, Any]:
    write_fixtures(jobs_root)
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    report: dict[str, Any] = {
        "command": "python -m agent_poc.mock_reader",
        "transport_chain": "agent -> LangChain tools -> reader MCP (stdio) -> httpx loopback -> FastAPI service -> saved canonical fixtures",
        "mock_planner_notice": MOCK_NOTICE,
        "limits": {"result_limit": RESULT_LIMIT, "max_steps_default": 8, "max_steps_budget": 3},
        "scenarios": [],
    }
    try:
        server, thread, _sock, port = _start_service()
        base = f"http://127.0.0.1:{port}"
        report["service"] = {"base": base, "port": port}
        env = {
            **os.environ,
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "FASTMCP_LOG_LEVEL": "WARNING",
            "CRR_API_BASE": base,
            "CRR_MCP_UPLOAD_ROOT": str(REPO_ROOT / "input"),
            "CRR_PROJECT_ROOT": str(REPO_ROOT / "workspace"),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])),
        }
        server_file = str(REPO_ROOT / "crr_compare" / "reader_mcp_server.py")
        transport = PythonStdioTransport(server_file, env=env, cwd=str(REPO_ROOT), python_cmd=sys.executable)
        async with Client(transport) as client:
            tools = sorted(t.name for t in await client.list_tools())
            report["tool_discovery"] = {
                "expected": sorted(READ_TOOLS),
                "observed": tools,
                "pass": tools == sorted(READ_TOOLS),
            }
            loop = asyncio.get_running_loop()
            gateway = StdioReaderGateway(client, loop)
            for scenario in SCENARIOS:
                report["scenarios"].append(await _run_scenario(scenario, gateway))
    finally:
        _stop_service(server, thread)

    failures = [s["name"] for s in report["scenarios"] if not s["pass"]]
    if not report.get("tool_discovery", {}).get("pass"):
        failures.insert(0, "tool_discovery")
    report["summary"] = {
        "total": len(report["scenarios"]),
        "passed": len(report["scenarios"]) - len([s for s in report["scenarios"] if not s["pass"]]),
        "failed": failures,
        "ok": not failures,
    }
    return report


def _prepare_out_dir(out_dir: Path) -> Path:
    requested = Path(out_dir).expanduser()
    if requested.is_symlink():
        raise SystemExit("refusing a symbolic link as the mock output directory")
    path = requested.resolve()
    if path in (REPO_ROOT.resolve(), Path.home().resolve(), Path(path.anchor)):
        raise SystemExit(f"refusing to use {path} as a fresh output directory")
    if (path / ".git").exists():
        raise SystemExit(f"refusing to erase a git checkout at {path}")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise SystemExit("mock output must be a fresh or empty directory; existing files were preserved")
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_demo(out_dir: Path | None = None, *, keep_jobs: bool = False) -> dict[str, Any]:
    """Run every scenario and return the report. Writes report.json to out_dir."""
    out_path = _prepare_out_dir(out_dir or DEFAULT_OUT)
    tmp_root = Path(tempfile.mkdtemp(prefix="reader-mock-jobs-"))
    jobs_root = tmp_root / "jobs"
    saved_jobs_root = service.JOBS_ROOT
    saved_jobs_cache = service._jobs
    service.JOBS_ROOT = jobs_root
    service._jobs = {}
    try:
        report = asyncio.run(_run_harness(jobs_root))
    finally:
        service.JOBS_ROOT = saved_jobs_root
        service._jobs = saved_jobs_cache
        if not keep_jobs:
            shutil.rmtree(tmp_root, ignore_errors=True)
    report["report_path"] = str(out_path / "report.json")
    (out_path / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def format_compact(report: dict[str, Any]) -> str:
    lines = ["reader-mock: three-tool agent/reader transport demonstration (synthetic, no credentials)"]
    discovery = report.get("tool_discovery", {})
    lines.append(
        f"  tool discovery: expected={discovery.get('expected')} observed={discovery.get('observed')} "
        f"{'PASS' if discovery.get('pass') else 'FAIL'}"
    )
    for scenario in report.get("scenarios", []):
        lines.append(f"  [{'PASS' if scenario['pass'] else 'FAIL'}] {scenario['name']}")
    summary = report.get("summary", {})
    lines.append(f"  {summary.get('passed')}/{summary.get('total')} scenarios passed")
    lines.append(f"report: {report.get('report_path')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline three-tool reader demonstration.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="fresh output directory for report.json")
    parser.add_argument("--keep-jobs", action="store_true", help="keep the temporary synthetic job fixtures")
    args = parser.parse_args(argv)
    report = run_demo(Path(args.out), keep_jobs=args.keep_jobs)
    print(format_compact(report))
    return 0 if report.get("summary", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
