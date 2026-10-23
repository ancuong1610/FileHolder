"""Reusable read projection over a completed canonical comparison result.

This module is the single implementation of result filtering shared by the HTTP
service read endpoints, the reader MCP adapter and the consuming agent. It is
pure: no filesystem, network, FastAPI or provider access. Callers pass an
already-loaded canonical result plus optional structured document elements.

Contract summary (see docs/THREE_TOOL_READER.md):

- ``summarize`` is safe for unfinished jobs and never reads result records.
  Counts are derived from the records themselves, not from possibly stale
  ``statistics``.
- ``search``/``details`` require a completed result (``done``).
- Every record reference is typed (``change``/``table``/``review``); missing,
  non-string or duplicate record ids are contract failures, never overwritten.
- Search cursors bind job, query/filters and canonical content fingerprint and
  reject malformed/cross-job/query/stale cursors. Cursor encoding is not
  authentication.
- Query, limits, context windows, text windows and pagination are bounded. Long
  text (change/cell/context) is returned in explicit offset/limit windows with a
  continuation offset; oversized metadata is clipped with an explicit marker.
- Errors carry stable safe codes and never leak server paths or logs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Iterable

from document_compare.phase1.contracts.result_state import (
    record_result_state,
    result_state_counts,
)
from document_compare.phase1.contracts.semantic_assessment import (
    normalize_assessment,
)

JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")

MAX_LIMIT = 50
DEFAULT_LIMIT = 10
MAX_QUERY_LENGTH = 200
MAX_CONTEXT_WINDOW = 5
MAX_CELL_LIMIT = 100
DEFAULT_CELL_LIMIT = 20
MAX_TEXT_LENGTH = 4000
DEFAULT_TEXT_LIMIT = MAX_TEXT_LENGTH
MAX_TEXT_OFFSET = 1_000_000
MAX_SNIPPET_LENGTH = 240
CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 4096
MAX_OFFSET = 100000
MAX_RECORD_ID_LENGTH = 80
MAX_PAGES = 50
MAX_CHANGE_TYPE_LENGTH = 40
# Bounded metadata projection (evidence/resolution/token_diff/provenance/rows).
MAX_METADATA_ITEMS = 25
MAX_METADATA_DEPTH = 4
MAX_METADATA_TEXT = 512
MAX_TABLE_LIST = 50
# Review linked-context paging (anchors per side per page).
MAX_CONTEXT_SOURCES = 3
MAX_ANCHOR_ID_LENGTH = 128
# Aggregate successful-response budget; never silently drop main fields.
MAX_RESPONSE_BYTES = 65536
BUDGET_HINT = "Reduce text_limit/cell_limit/context_window"

ID_FIELDS = {"change": "change_id", "table": "table_change_id", "review": "review_id"}
CANONICAL_ARTIFACT = "comparison_result.json"

SEARCH_KINDS = ("all", "change", "table", "review")
DETAIL_KINDS = ("change", "table", "review")
STATUSES = ("all", "accepted", "review")

GROUND_TRUTH_NOTICE = (
    "Engine acceptance is not legal or semantic ground truth; uncertain items "
    "(legacy review records) are unresolved and are not confirmed changes."
)

# Safe, relative artifact links (no host, no filesystem path).
ARTIFACT_LINK_NAMES = (
    "comparison_redline.pdf",
    "comparison_inline.pdf",
    "bu_comparison_redline.html",
)


class ReaderError(Exception):
    """Read contract failure with a stable safe code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# Small shape helpers
# ---------------------------------------------------------------------------

def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ReaderError("invalid_arguments", "job_id must be 12 lowercase hexadecimal characters")
    return job_id


def _validate_choice(value: Any, allowed: Iterable[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ReaderError("invalid_arguments", f"{field} must be one of {', '.join(allowed)}")
    return value


def _validate_int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReaderError("invalid_arguments", f"{field} must be an integer")
    if value < low or value > high:
        raise ReaderError("invalid_arguments", f"{field} must be between {low} and {high}")
    return value


def _validate_query(query: Any) -> str:
    if query is None:
        return ""
    if not isinstance(query, str):
        raise ReaderError("invalid_arguments", "query must be a string")
    if len(query) > MAX_QUERY_LENGTH:
        raise ReaderError("invalid_arguments", f"query must be at most {MAX_QUERY_LENGTH} characters")
    if any(ord(ch) < 32 and ch not in "\t" for ch in query):
        raise ReaderError("invalid_arguments", "query must not contain control characters")
    return query


def _validate_change_type(change_type: Any) -> str:
    if change_type is None:
        return ""
    if not isinstance(change_type, str) or len(change_type) > MAX_CHANGE_TYPE_LENGTH:
        raise ReaderError("invalid_arguments", "change_type must be a short string")
    return change_type


# ---------------------------------------------------------------------------
# Fingerprint and cursor
# ---------------------------------------------------------------------------

def snapshot_fingerprint(canonical: dict[str, Any]) -> str:
    """Deterministic fingerprint of the full canonical result content.

    Hashing the whole canonical document (not only ids and statistics) means a
    changed old/new text, resolution or table cell with the same ids yields a
    different snapshot, so a cursor bound to the old content is rejected.
    """
    if not isinstance(canonical, dict):
        raise ReaderError("invalid_artifact", "comparison result is not a canonical object")
    raw = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode and strictly validate every cursor field.

    Any malformed encoded value, wrong version, unknown filter value or bad
    offset is an ``invalid_cursor``; the binding to a specific job, filter set
    and snapshot is checked by the caller as ``stale_cursor``. This is not
    authentication and grants no authority.
    """
    if not isinstance(cursor, str) or not cursor:
        raise ReaderError("invalid_cursor", "cursor must be a non-empty string")
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise ReaderError("invalid_cursor", "cursor exceeds the maximum length")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ReaderError("invalid_cursor", "cursor is not a valid reader cursor") from exc
    if not isinstance(payload, dict):
        raise ReaderError("invalid_cursor", "cursor is not a valid reader cursor")
    version = payload.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version != CURSOR_VERSION:
        raise ReaderError("invalid_cursor", "cursor version is not supported")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ReaderError("invalid_cursor", "cursor job binding is invalid")
    query = payload.get("query")
    if not isinstance(query, str) or len(query) > MAX_QUERY_LENGTH:
        raise ReaderError("invalid_cursor", "cursor query is invalid")
    if payload.get("kind") not in SEARCH_KINDS:
        raise ReaderError("invalid_cursor", "cursor kind is invalid")
    if payload.get("status") not in STATUSES:
        raise ReaderError("invalid_cursor", "cursor status is invalid")
    change_type = payload.get("change_type")
    if not isinstance(change_type, str) or len(change_type) > MAX_CHANGE_TYPE_LENGTH:
        raise ReaderError("invalid_cursor", "cursor change_type is invalid")
    offset = payload.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET:
        raise ReaderError("invalid_cursor", "cursor offset is invalid")
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ReaderError("invalid_cursor", "cursor fingerprint is invalid")
    return payload


# ---------------------------------------------------------------------------
# Bounded metadata projection
# ---------------------------------------------------------------------------

def _bound_json(value: Any, depth: int, state: dict[str, bool]) -> Any:
    if depth > MAX_METADATA_DEPTH:
        state["clipped"] = True
        return "<clipped>"
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > MAX_METADATA_ITEMS:
            state["clipped"] = True
            items = items[:MAX_METADATA_ITEMS]
        return {str(key): _bound_json(nested, depth + 1, state) for key, nested in items}
    if isinstance(value, list):
        if len(value) > MAX_METADATA_ITEMS:
            state["clipped"] = True
            value = value[:MAX_METADATA_ITEMS]
        return [_bound_json(nested, depth + 1, state) for nested in value]
    if isinstance(value, str):
        if len(value) > MAX_METADATA_TEXT:
            state["clipped"] = True
            return value[:MAX_METADATA_TEXT]
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    state["clipped"] = True
    return str(value)[:MAX_METADATA_TEXT]


def _bounded_json(value: Any) -> tuple[Any, bool]:
    """Return (bounded copy, clipped). Never dumps an unbounded nested array."""
    state = {"clipped": False}
    projected = _bound_json(value, 0, state)
    return projected, state["clipped"]


def _response_bytes(payload: Any) -> int:
    try:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return 0
    return len(raw.encode("utf-8"))


def _enforce_budget(payload: Any) -> None:
    """Raise when an aggregate successful response exceeds the byte budget.

    The budget guards the whole envelope, so bounded per-field metadata can still
    be rejected as a whole instead of silently dropping numbers or old/new text.
    """
    if _response_bytes(payload) > MAX_RESPONSE_BYTES:
        raise ReaderError("response_too_large", BUDGET_HINT)


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

def _identity(document: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {"available": False}
    document_id = document.get("document_id")
    return {
        "available": isinstance(document_id, str) and bool(document_id),
        "document_id": document_id if isinstance(document_id, str) else None,
        "page_count": document.get("page_count"),
        "source_element_count": document.get("source_element_count"),
        "provisional_anchor_count": document.get("provisional_anchor_count"),
    }


def _record_counts(changes: list[Any], tables: list[Any]) -> tuple[dict[str, int], dict[str, int]]:
    """Counts derived from records; never trust possibly stale statistics."""
    change_type_counts: dict[str, int] = {}
    resolution_status_counts: dict[str, int] = {}
    for item in [*changes, *tables]:
        if not isinstance(item, dict):
            continue
        change_type = item.get("change_type")
        if isinstance(change_type, str) and change_type:
            change_type_counts[change_type] = change_type_counts.get(change_type, 0) + 1
        status = _resolution(item).get("status")
        if not isinstance(status, str) or not status:
            status = "review"
        resolution_status_counts[status] = resolution_status_counts.get(status, 0) + 1
    return change_type_counts, resolution_status_counts


def summarize(
    job_id: str,
    state: str,
    canonical: dict[str, Any] | None = None,
    *,
    artifacts: list[str] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Bounded status summary. Never reads records for unfinished jobs."""
    job_id = validate_job_id(job_id)
    state = str(state or "queued")
    base: dict[str, Any] = {
        "job_id": job_id,
        "state": state,
        "complete": state == "done",
    }
    if state != "done":
        base["status"] = state
        base["limitations"] = ["job_not_done"]
        if state == "failed":
            base["failure"] = True
        base["ground_truth_notice"] = GROUND_TRUTH_NOTICE
        return base

    if not isinstance(canonical, dict):
        base["complete"] = False
        base["status"] = "invalid_artifact"
        base["limitations"] = ["invalid_artifact"]
        base["ground_truth_notice"] = GROUND_TRUTH_NOTICE
        return base

    changes = _as_list(canonical.get("changes"))
    tables = _as_list(canonical.get("table_changes"))
    reviews = _as_list(canonical.get("review_queue"))
    try:
        _typed_index(canonical)
    except ReaderError:
        base["complete"] = False
        base["status"] = "invalid_artifact"
        base["limitations"] = ["invalid_artifact"]
        base["ground_truth_notice"] = GROUND_TRUTH_NOTICE
        return base
    change_type_counts, resolution_status_counts = _record_counts(changes, tables)
    semantic_basis_counts: dict[str, int] = {}
    for item in [*changes, *tables]:
        if not isinstance(item, dict):
            continue
        basis = _source_bound_assessment(item).get("basis")
        semantic_basis_counts[basis] = semantic_basis_counts.get(basis, 0) + 1

    derived_limitations: list[str] = []
    if reviews:
        derived_limitations.append("review_open")
    if canonical.get("unresolved_table_reviews"):
        derived_limitations.append("table_reviews_open")
    if any(
        _resolution(item).get("method") == "llm_advisory"
        for item in [*changes, *tables]
        if isinstance(item, dict)
    ):
        derived_limitations.append("llm_advisory_present")
    if not _identity(_as_dict(canonical.get("old_document"))).get("available") or not _identity(
        _as_dict(canonical.get("new_document"))
    ).get("available"):
        derived_limitations.append("document_identity_missing")
    for extra in limitations or []:
        if extra not in derived_limitations:
            derived_limitations.append(extra)

    base.update(
        {
            "snapshot": snapshot_fingerprint(canonical),
            "counts": {
                "changes": len(changes),
                "table_changes": len(tables),
                "review_open": len(reviews),
                "change_type_counts": change_type_counts,
                "resolution_status_counts": resolution_status_counts,
                "semantic_basis_counts": semantic_basis_counts,
                "result_state_counts": result_state_counts(
                    changes, tables, reviews, _as_list(canonical.get("non_semantic_events"))
                ),
            },
            "documents": {
                "old": _identity(_as_dict(canonical.get("old_document"))),
                "new": _identity(_as_dict(canonical.get("new_document"))),
            },
            "limitations": derived_limitations,
            "ground_truth_notice": GROUND_TRUTH_NOTICE,
        }
    )
    if artifacts is not None:
        base["artifacts"] = {
            name: f"/jobs/{job_id}/artifacts/{name}" for name in artifacts if name in ARTIFACT_LINK_NAMES
        }
    if _response_bytes(base) > MAX_RESPONSE_BYTES:
        return {
            "job_id": job_id,
            "state": state,
            "complete": False,
            "status": "response_too_large",
            "limitations": ["response_too_large"],
            "error": {"error": "response_too_large", "message": BUDGET_HINT},
            "ground_truth_notice": GROUND_TRUTH_NOTICE,
        }
    return base


# ---------------------------------------------------------------------------
# Record projection
# ---------------------------------------------------------------------------

def _resolution(item: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(item.get("resolution"))


def _status(item: dict[str, Any]) -> str:
    return str(_resolution(item).get("status") or "")


def _result_state(item: dict[str, Any], kind: str) -> str:
    return record_result_state(item, kind=kind)


def _needs_review(item: dict[str, Any], kind: str) -> bool:
    """A review record is always unresolved, even if its resolution omits the flag."""
    if kind == "review":
        return True
    return bool(_resolution(item).get("needs_review")) or _status(item) == "review"


def _confirmed(item: dict[str, Any], kind: str) -> bool:
    """Accepted with a needs_review flag is not a confirmed change."""
    return _status(item) == "accepted" and not _needs_review(item, kind)


def _record_id(item: Any, kind: str) -> str:
    if not isinstance(item, dict):
        raise ReaderError("invalid_artifact", f"{kind} record is not an object")
    value = item.get(ID_FIELDS[kind])
    if not isinstance(value, str) or not value or len(value) > MAX_RECORD_ID_LENGTH:
        raise ReaderError("invalid_artifact", f"{kind} record has a missing or invalid record id")
    return value


def _iter_typed(canonical: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """All typed records, validating container and row shapes."""
    typed: list[tuple[str, dict[str, Any]]] = []
    for key, kind in (("changes", "change"), ("table_changes", "table"), ("review_queue", "review")):
        value = canonical.get(key)
        if value is None:
            value = []
        if not isinstance(value, list):
            raise ReaderError("invalid_artifact", f"{key} must be a list of records")
        for item in value:
            if not isinstance(item, dict):
                raise ReaderError("invalid_artifact", f"{kind} record is not an object")
            typed.append((kind, item))
    return typed


def _typed_index(canonical: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Typed record index; duplicate ids are a contract failure, not overwrite."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, item in _iter_typed(canonical):
        key = (kind, _record_id(item, kind))
        if key in index:
            raise ReaderError("invalid_artifact", f"duplicate {kind} record id")
        index[key] = item
    return index


def _pages(*sides: dict[str, Any] | None) -> list[int]:
    pages = {
        side.get("primary_page")
        for side in sides
        if isinstance(side, dict) and isinstance(side.get("primary_page"), int)
    }
    return sorted(pages)[:MAX_PAGES]


def _table_pages(table: dict[str, Any]) -> list[int]:
    pages = {
        cell.get("primary_page")
        for change in _as_list(table.get("cell_changes"))
        for cell in (_as_dict(change).get("old"), _as_dict(change).get("new"))
        if isinstance(cell, dict) and isinstance(cell.get("primary_page"), int)
    }
    return sorted(pages)[:MAX_PAGES]


def _cell_texts(table: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for change in _as_list(table.get("cell_changes")):
        for side in ("old", "new"):
            cell = _as_dict(change).get(side)
            if isinstance(cell, dict) and cell.get("text"):
                texts.append(str(cell["text"]))
    return texts


def _bounded_reasons(item: dict[str, Any]) -> list[str]:
    reasons = _resolution(item).get("review_reasons")
    if not isinstance(reasons, list):
        return []
    return [str(reason)[:MAX_METADATA_TEXT] for reason in reasons[:MAX_METADATA_ITEMS]]


def _record_fields(item: dict[str, Any], kind: str) -> dict[str, Any]:
    """Primary searchable field texts for a record, for useful snippets."""
    if kind == "change":
        old = _as_dict(item.get("old"))
        new = _as_dict(item.get("new"))
        return {
            "record_id": _record_id(item, kind),
            "old_text": old.get("text") or old.get("comparison_text") or "",
            "new_text": new.get("text") or new.get("comparison_text") or "",
            "change_type": item.get("change_type") or "",
            "review_reasons": _bounded_reasons(item),
        }
    if kind == "table":
        old = _as_dict(item.get("old"))
        new = _as_dict(item.get("new"))
        cells, _ = _bounded_json(_cell_texts(item))
        old_header, _ = _bounded_json(old.get("header_signature"))
        new_header, _ = _bounded_json(new.get("header_signature"))
        return {
            "record_id": _record_id(item, kind),
            "cell_text": " | ".join(str(part) for part in cells) if isinstance(cells, list) else "",
            "old_header": " | ".join(str(part) for part in old_header) if isinstance(old_header, list) else "",
            "new_header": " | ".join(str(part) for part in new_header) if isinstance(new_header, list) else "",
            "change_type": item.get("change_type") or "",
        }
    evidence, _ = _bounded_json(item.get("evidence"))
    return {
        "record_id": _record_id(item, kind),
        "case_type": item.get("case_type") or _resolution(item).get("case_type") or "",
        "review_reasons": _bounded_reasons(item),
        "evidence": evidence,
    }


def _flatten(value: Any) -> str:
    parts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for nested in node.values():
                visit(nested)
        elif isinstance(node, list):
            for nested in node:
                visit(nested)
        elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
            parts.append(str(node))

    visit(value)
    return " ".join(parts)


def _terms(query: str) -> list[str]:
    return re.findall(r"[\w.-]+", query.casefold())


def _snippet(text: str, terms: list[str]) -> str:
    clean = " ".join(str(text).split())
    if not clean:
        return ""
    if terms:
        low = clean.casefold()
        positions = [low.find(term) for term in terms if low.find(term) >= 0]
        if positions:
            start = max(0, min(positions) - 40)
            return clean[start : start + MAX_SNIPPET_LENGTH]
    return clean[:MAX_SNIPPET_LENGTH]


def _searchable(record: dict[str, Any]) -> str:
    return _flatten(record.get("fields"))


def _build_search_records(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for (kind, record_id), item in _typed_index(canonical).items():
        fields = _record_fields(item, kind)
        needs_review = _needs_review(item, kind)
        if kind == "change":
            pages = _pages(_as_dict(item.get("old")), _as_dict(item.get("new")))
        elif kind == "table":
            pages = _table_pages(item)
        else:
            pages = []
        records.append(
            {
                "kind": kind,
                "record_id": record_id,
                "change_type": item.get("change_type"),
                "status": _status(item) or ("review" if needs_review else "unresolved"),
                "result_state": _result_state(item, kind),
                "needs_review": needs_review,
                "confirmed": _confirmed(item, kind),
                "pages": pages,
                "fields": fields,
            }
        )
    order = {"change": 0, "table": 1, "review": 2}
    records.sort(key=lambda r: (order.get(r["kind"], 9), r["record_id"]))
    return records


def _match(record: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = _searchable(record).casefold()
    return all(term in haystack for term in terms)


def _best_snippet(record: dict[str, Any], terms: list[str]) -> tuple[str, str]:
    best = ("", "")
    for name, value in record["fields"].items():
        if name == "record_id":
            continue
        text = _flatten(value)
        if not text:
            continue
        low = text.casefold()
        if terms and not any(term in low for term in terms):
            continue
        candidate = _snippet(text, terms)
        if candidate and (not best[0] or len(candidate) > len(best[0])):
            best = (candidate, name)
    if not best[0]:
        for name, value in record["fields"].items():
            text = _flatten(value)
            if text:
                return _snippet(text, terms), name
    return best


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_coverage(canonical: dict[str, Any], resolved_kind: str) -> dict[str, Any] | None:
    """Explicit notice when review source text is not part of the indexed schema.

    Review evidence can reference structured element ids whose body text is not
    in the canonical result, so those matches cannot be claimed as complete. No
    full-source index is built here; the gap is named instead.
    """
    if resolved_kind not in ("all", "review"):
        return None
    unindexed = 0
    for kind, review in _iter_typed(canonical):
        if kind != "review":
            continue
        evidence = _as_dict(review.get("evidence"))
        if _as_list(evidence.get("old_element_ids")) or _as_list(evidence.get("new_element_ids")):
            unindexed += 1
    warning = None if unindexed == 0 else "review_source_text_not_indexed"
    return {
        "review_source_text_indexed": unindexed == 0,
        "unindexed_review_count": unindexed,
        "complete": unindexed == 0,
        "warning": warning,
    }


def search(
    job_id: str,
    canonical: dict[str, Any],
    *,
    query: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    change_type: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    job_id = validate_job_id(job_id)
    if not isinstance(canonical, dict):
        raise ReaderError("invalid_artifact", "comparison result is not a canonical object")
    fingerprint = snapshot_fingerprint(canonical)

    resolved_query = _validate_query(query)
    resolved_kind = _validate_choice(kind if kind is not None else "all", SEARCH_KINDS, "kind")
    resolved_status = _validate_choice(status if status is not None else "all", STATUSES, "status")
    resolved_change_type = _validate_change_type(change_type)
    resolved_limit = _validate_int(limit if limit is not None else DEFAULT_LIMIT, "limit", 1, MAX_LIMIT)
    offset = 0

    if cursor is not None:
        payload = _decode_cursor(cursor)
        if payload.get("job_id") != job_id:
            raise ReaderError("stale_cursor", "cursor does not belong to this job")
        if payload.get("fingerprint") != fingerprint:
            raise ReaderError("stale_cursor", "cursor was issued for a different result snapshot")
        if query not in (None, "") and query != payload.get("query"):
            raise ReaderError("stale_cursor", "cursor does not match this query")
        if kind not in (None, "all") and kind != payload.get("kind"):
            raise ReaderError("stale_cursor", "cursor does not match this kind filter")
        if status not in (None, "all") and status != payload.get("status"):
            raise ReaderError("stale_cursor", "cursor does not match this status filter")
        if change_type not in (None, "") and change_type != payload.get("change_type"):
            raise ReaderError("stale_cursor", "cursor does not match this change_type filter")
        resolved_query = payload.get("query") or ""
        resolved_kind = str(payload.get("kind") or "all")
        resolved_status = str(payload.get("status") or "all")
        resolved_change_type = str(payload.get("change_type") or "")
        offset = payload["offset"]

    terms = _terms(resolved_query)
    matched: list[dict[str, Any]] = []
    for record in _build_search_records(canonical):
        if resolved_kind != "all" and record["kind"] != resolved_kind:
            continue
        if resolved_status == "accepted" and not record["confirmed"]:
            continue
        if resolved_status == "review" and not record["needs_review"]:
            continue
        if resolved_change_type and str(record["change_type"] or "").casefold() != resolved_change_type.casefold():
            continue
        if not _match(record, terms):
            continue
        snippet, source = _best_snippet(record, terms)
        matched.append(
            {
                "kind": record["kind"],
                "record_id": record["record_id"],
                "change_type": record["change_type"],
                "status": record["status"],
                "result_state": record["result_state"],
                "needs_review": record["needs_review"],
                "confirmed": record["confirmed"],
                "pages": record["pages"],
                "snippet": snippet,
                "snippet_source": source,
            }
        )

    total = len(matched)
    page = matched[offset : offset + resolved_limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < total:
        next_cursor = _encode_cursor(
            {
                "v": CURSOR_VERSION,
                "job_id": job_id,
                "query": resolved_query,
                "kind": resolved_kind,
                "status": resolved_status,
                "change_type": resolved_change_type,
                "fingerprint": fingerprint,
                "offset": next_offset,
            }
        )
    coverage = _search_coverage(canonical, resolved_kind)
    warnings: list[str] = []
    if coverage and coverage.get("warning"):
        warnings.append(str(coverage["warning"]))
    result = {
        "job_id": job_id,
        "snapshot": fingerprint,
        "kind": resolved_kind,
        "query": resolved_query,
        "status": resolved_status,
        "change_type": resolved_change_type,
        "total_matches": total,
        "returned": page,
        "offset": offset,
        "limit": resolved_limit,
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
        "empty": total == 0,
        "search_coverage": coverage,
        "warnings": warnings,
        "limits": {"max_limit": MAX_LIMIT, "max_query_length": MAX_QUERY_LENGTH},
    }
    _enforce_budget(result)
    return result


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------

def _bounded_text(value: Any, offset: int = 0, limit: int = DEFAULT_TEXT_LIMIT) -> dict[str, Any]:
    """Return an explicit text window with offset, length and continuation."""
    text = "" if value is None else str(value)
    total = len(text)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        offset = 0
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        limit = DEFAULT_TEXT_LIMIT
    chunk = text[offset : offset + limit]
    end = offset + len(chunk)
    return {
        "text": chunk,
        "text_offset": offset,
        "text_limit": limit,
        "text_length": total,
        "text_truncated": offset > 0 or end < total,
        "text_next_offset": end if end < total else None,
    }


def _compact_document_element(
    element: dict[str, Any], text_offset: int, text_limit: int
) -> dict[str, Any]:
    return {
        "element_id": element.get("element_id"),
        "element_type": element.get("element_type"),
        "reading_order": element.get("reading_order"),
        "primary_page": element.get("primary_page"),
        **_bounded_text(element.get("text"), text_offset, text_limit),
    }


def _reading_order_key(element: dict[str, Any]) -> int:
    value = element.get("reading_order")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return -1


def _context(
    elements: list[dict[str, Any]] | None,
    *,
    element_id: Any,
    reading_order: Any,
    window: int,
    text_offset: int,
    text_limit: int,
) -> dict[str, Any]:
    """Bounded context located by stable element id, falling back to reading order.

    A duplicate element id or ambiguous reading order is reported as unavailable
    rather than guessed from a tentative hierarchy.
    """
    if elements is None:
        return {"available": False, "reason": "context_unavailable", "items": []}
    ordered = sorted((e for e in elements if isinstance(e, dict)), key=_reading_order_key)
    if isinstance(element_id, str) and element_id:
        matches = [index for index, item in enumerate(ordered) if item.get("element_id") == element_id]
        if len(matches) > 1:
            return {"available": False, "reason": "ambiguous_element_id", "items": []}
        if not matches:
            return {"available": False, "reason": "element_not_found", "items": []}
        position = matches[0]
    else:
        if not isinstance(reading_order, int) or isinstance(reading_order, bool):
            return {"available": False, "reason": "reading_order_unavailable", "items": []}
        matches = [
            index for index, item in enumerate(ordered) if item.get("reading_order") == reading_order
        ]
        if len(matches) > 1:
            return {"available": False, "reason": "ambiguous_reading_order", "items": []}
        if not matches:
            return {"available": False, "reason": "element_not_found", "items": []}
        position = matches[0]
    low = max(0, position - window)
    high = min(len(ordered), position + window + 1)
    return {
        "available": True,
        "reason": None,
        "before": position - low,
        "after": high - position - 1,
        "items": [
            _compact_document_element(item, text_offset, text_limit) for item in ordered[low:high]
        ],
    }


def _bounded_cell(
    cell: dict[str, Any] | None, text_offset: int, text_limit: int
) -> dict[str, Any] | None:
    if not isinstance(cell, dict):
        return None
    polygon, polygon_clipped = _bounded_json(cell.get("primary_polygon"))
    spans, spans_clipped = _bounded_json(cell.get("spans"))
    return {
        "cell_id": cell.get("cell_id"),
        "source_table_id": cell.get("source_table_id"),
        "row_index": cell.get("row_index"),
        "column_index": cell.get("column_index"),
        "row_span": cell.get("row_span"),
        "column_span": cell.get("column_span"),
        "kind": cell.get("kind"),
        "primary_page": cell.get("primary_page"),
        **_bounded_text(cell.get("text"), text_offset, text_limit),
        "primary_polygon": polygon,
        "spans": spans,
        "clipped": polygon_clipped or spans_clipped,
    }


def _paginate_cells(
    cell_changes: list[dict[str, Any]],
    offset: int,
    limit: int,
    text_offset: int,
    text_limit: int,
) -> dict[str, Any]:
    total = len(_as_list(cell_changes))
    page = []
    for change in _as_list(cell_changes)[offset : offset + limit]:
        change = _as_dict(change)
        page.append(
            {
                "change_id": change.get("change_id"),
                "classification": change.get("classification"),
                "old_row_index": change.get("old_row_index"),
                "new_row_index": change.get("new_row_index"),
                "column_index": change.get("column_index"),
                "row_match_type": change.get("row_match_type"),
                "old": _bounded_cell(change.get("old"), text_offset, text_limit),
                "new": _bounded_cell(change.get("new"), text_offset, text_limit),
            }
        )
    truncated = offset + len(page) < total
    return {
        "cell_changes": page,
        "cell_offset": offset,
        "cell_limit": limit,
        "cell_total": total,
        "cell_changes_truncated": truncated,
        "next_offset": offset + len(page) if truncated else None,
    }


def _bounded_table_side(side: Any) -> dict[str, Any] | None:
    if not isinstance(side, dict):
        return None
    projected, clipped = _bounded_json(side)
    header = side.get("header_signature")
    if isinstance(header, list):
        projected["header_signature"] = [
            str(item)[:MAX_METADATA_TEXT] for item in header[:MAX_TABLE_LIST]
        ]
        clipped = clipped or len(header) > MAX_TABLE_LIST
    elif isinstance(projected, dict) and "header_signature" in projected:
        projected["header_signature"] = None
    if clipped and isinstance(projected, dict):
        projected["clipped"] = True
    return projected


def _source_bound_assessment(record: dict[str, Any]) -> dict[str, Any]:
    old, new = record.get("old"), record.get("new")
    return normalize_assessment(
        record.get("semantic_assessment"),
        old_element=old if isinstance(old, dict) else None,
        new_element=new if isinstance(new, dict) else None,
    )


def _change_projection(record: dict[str, Any], text_offset: int, text_limit: int) -> dict[str, Any]:
    old = _as_dict(record.get("old"))
    new = _as_dict(record.get("new"))
    token_diff, token_clipped = _bounded_json(record.get("token_diff"))
    provenance, provenance_clipped = _bounded_json(record.get("provenance"))
    resolution, resolution_clipped = _bounded_json(_resolution(record))
    if isinstance(resolution, dict):
        resolution["result_state"] = _result_state(record, "change")
    semantic_assessment, semantic_clipped = _bounded_json(
        _source_bound_assessment(record)
    )
    clipped = [
        name
        for name, flag in (
            ("token_diff", token_clipped),
            ("provenance", provenance_clipped),
            ("resolution", resolution_clipped),
            ("semantic_assessment", semantic_clipped),
        )
        if flag
    ]
    return {
        "change_id": record.get("change_id"),
        "change_type": record.get("change_type"),
        "result_state": _result_state(record, "change"),
        "old": {
            **_bounded_text(old.get("text"), text_offset, text_limit),
            "element_id": old.get("element_id"),
            "element_type": old.get("element_type"),
            "primary_page": old.get("primary_page"),
            "reading_order": old.get("reading_order"),
        },
        "new": {
            **_bounded_text(new.get("text"), text_offset, text_limit),
            "element_id": new.get("element_id"),
            "element_type": new.get("element_type"),
            "primary_page": new.get("primary_page"),
            "reading_order": new.get("reading_order"),
        },
        "token_diff": token_diff,
        "similarity": record.get("similarity"),
        "resolution": resolution,
        "semantic_assessment": semantic_assessment,
        "provenance": provenance,
        "clipped": clipped,
    }


def _string_list(value: Any, field: str) -> list[str]:
    """Validate an optional list of short non-empty ids; dedup preserving order."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReaderError("invalid_artifact", f"{field} must be a list of element ids")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > MAX_ANCHOR_ID_LENGTH:
            raise ReaderError("invalid_artifact", f"{field} contains an invalid element id")
        if item not in out:
            out.append(item)
    return out


def _append_anchor(bucket: list[str], value: Any) -> None:
    if isinstance(value, str) and value and value not in bucket:
        bucket.append(value)


def _table_source_anchors(table: dict[str, Any], old: list[str], new: list[str]) -> None:
    for side, bucket in (("old", old), ("new", new)):
        side_dict = _as_dict(table.get(side))
        for key in ("source_table_ids", "table_ids", "logical_table_id"):
            value = side_dict.get(key)
            if isinstance(value, list):
                for entry in value:
                    _append_anchor(bucket, entry)
            else:
                _append_anchor(bucket, value)
    for change in _as_list(table.get("cell_changes")):
        change = _as_dict(change)
        for side, bucket in (("old", old), ("new", new)):
            _append_anchor(bucket, _as_dict(change.get(side)).get("source_table_id"))


def _review_anchor_sources(
    review: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Anchors derived only from the review's own links and evidence ids."""
    evidence = _as_dict(review.get("evidence"))
    old = _string_list(evidence.get("old_element_ids"), "evidence.old_element_ids")
    new = _string_list(evidence.get("new_element_ids"), "evidence.new_element_ids")
    _append_anchor(old, evidence.get("old_element_id"))
    _append_anchor(new, evidence.get("new_element_id"))

    linked_refs: list[dict[str, Any]] = []
    change_id = review.get("change_id")
    if isinstance(change_id, str) and change_id:
        change = index.get(("change", change_id))
        if change is None:
            raise ReaderError("invalid_artifact", "review links to an unknown change id")
        linked_refs.append(_linked_ref("change", change_id, change))
        _append_anchor(old, _as_dict(change.get("old")).get("element_id"))
        _append_anchor(new, _as_dict(change.get("new")).get("element_id"))
    table_id = review.get("table_change_id")
    if isinstance(table_id, str) and table_id:
        table = index.get(("table", table_id))
        if table is None:
            raise ReaderError("invalid_artifact", "review links to an unknown table id")
        linked_refs.append(_linked_ref("table", table_id, table))
        _table_source_anchors(table, old, new)
    return old, new, linked_refs


def _linked_ref(kind: str, record_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Typed ref for a review link; the review stays unresolved either way."""
    return {
        "kind": kind,
        "record_id": record_id,
        "found": True,
        "change_type": record.get("change_type"),
        "needs_review": True,
        "confirmed": False,
    }


def _review_context(
    elements: list[dict[str, Any]] | None,
    anchors: list[str],
    offset: int,
    limit: int,
    window: int,
    text_offset: int,
    text_limit: int,
) -> dict[str, Any]:
    """Paged neighbour context, one bounded group per anchor id."""
    page = anchors[offset : offset + limit]
    total = len(anchors)
    truncated = offset + len(page) < total
    groups = [
        {
            "anchor_id": anchor,
            **_context(
                elements,
                element_id=anchor,
                reading_order=None,
                window=window,
                text_offset=text_offset,
                text_limit=text_limit,
            ),
        }
        for anchor in page
    ]
    if total == 0:
        return {
            "available": False,
            "reason": "context_unavailable",
            "source_offset": offset,
            "source_limit": limit,
            "sources_total": 0,
            "sources_truncated": False,
            "next_source_offset": None,
            "groups": [],
        }
    return {
        "available": True,
        "reason": None,
        "source_offset": offset,
        "source_limit": limit,
        "sources_total": total,
        "sources_truncated": truncated,
        "next_source_offset": offset + len(page) if truncated else None,
        "groups": groups,
    }


def _context_warnings(out: dict[str, Any]) -> None:
    warnings = out["warnings"]

    def note(side: str, reason: Any) -> None:
        reason = reason or "context_unavailable"
        named = f"{side}_context_{reason}"
        if named not in warnings:
            warnings.append(named)
        if reason == "context_unavailable" and "context_unavailable" not in warnings:
            warnings.append("context_unavailable")

    for side in ("old", "new"):
        context = _as_dict(_as_dict(out.get("context")).get(side))
        if not context.get("available"):
            note(side, context.get("reason"))
        for group in _as_list(context.get("groups")):
            group = _as_dict(group)
            if not group.get("available"):
                note(side, group.get("reason"))


def details(
    job_id: str,
    canonical: dict[str, Any],
    *,
    kind: str,
    record_id: str,
    context_window: int = 1,
    cell_offset: int = 0,
    cell_limit: int = DEFAULT_CELL_LIMIT,
    text_offset: int = 0,
    text_limit: int = DEFAULT_TEXT_LIMIT,
    context_source_offset: int = 0,
    old_elements: list[dict[str, Any]] | None = None,
    new_elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    job_id = validate_job_id(job_id)
    if not isinstance(canonical, dict):
        raise ReaderError("invalid_artifact", "comparison result is not a canonical object")
    kind = _validate_choice(kind, DETAIL_KINDS, "kind")
    if not isinstance(record_id, str) or not record_id or len(record_id) > MAX_RECORD_ID_LENGTH:
        raise ReaderError("invalid_arguments", "record_id must be a short string")
    context_window = _validate_int(context_window, "context_window", 0, MAX_CONTEXT_WINDOW)
    cell_offset = _validate_int(cell_offset, "cell_offset", 0, MAX_OFFSET)
    cell_limit = _validate_int(cell_limit, "cell_limit", 1, MAX_CELL_LIMIT)
    text_offset = _validate_int(text_offset, "text_offset", 0, MAX_TEXT_OFFSET)
    text_limit = _validate_int(text_limit, "text_limit", 1, MAX_TEXT_LENGTH)
    context_source_offset = _validate_int(context_source_offset, "context_source_offset", 0, MAX_OFFSET)

    index = _typed_index(canonical)
    record = index.get((kind, record_id))
    if record is None:
        raise ReaderError("not_found", f"unknown {kind} record_id")

    out: dict[str, Any] = {
        "job_id": job_id,
        "snapshot": snapshot_fingerprint(canonical),
        "kind": kind,
        "record_id": record_id,
        "canonical_reference": {
            "artifact": CANONICAL_ARTIFACT,
            "job_id": job_id,
            "kind": kind,
            "record_id": record_id,
        },
        "warnings": [],
        "limits": {
            "max_context_window": MAX_CONTEXT_WINDOW,
            "max_cell_limit": MAX_CELL_LIMIT,
            "max_text_length": MAX_TEXT_LENGTH,
            "default_text_limit": DEFAULT_TEXT_LIMIT,
            "max_text_offset": MAX_TEXT_OFFSET,
            "max_context_sources": MAX_CONTEXT_SOURCES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
        },
    }

    if kind == "change":
        old = _as_dict(record.get("old"))
        new = _as_dict(record.get("new"))
        out["change"] = _change_projection(record, text_offset, text_limit)
        out["record"] = {"change_id": record.get("change_id"), "change_type": record.get("change_type")}
        out["context"] = {
            "old": _context(
                old_elements,
                element_id=old.get("element_id"),
                reading_order=old.get("reading_order"),
                window=context_window,
                text_offset=text_offset,
                text_limit=text_limit,
            ),
            "new": _context(
                new_elements,
                element_id=new.get("element_id"),
                reading_order=new.get("reading_order"),
                window=context_window,
                text_offset=text_offset,
                text_limit=text_limit,
            ),
        }
    elif kind == "table":
        old = _as_dict(record.get("old"))
        new = _as_dict(record.get("new"))
        paginated = _paginate_cells(
            _as_list(record.get("cell_changes")), cell_offset, cell_limit, text_offset, text_limit
        )
        resolution, resolution_clipped = _bounded_json(_resolution(record))
        if isinstance(resolution, dict):
            resolution["result_state"] = _result_state(record, "table")
        semantic_assessment, semantic_clipped = _bounded_json(
            normalize_assessment(record.get("semantic_assessment"))
        )
        out["table"] = {
            "table_change_id": record.get("table_change_id"),
            "change_type": record.get("change_type"),
            "classification": record.get("classification"),
            "result_state": _result_state(record, "table"),
            "old": _bounded_table_side(record.get("old")),
            "new": _bounded_table_side(record.get("new")),
            "resolution": resolution,
            "semantic_assessment": semantic_assessment,
            "review_reasons": _bounded_reasons(record),
            "clipped": [
                name
                for name, flag in (
                    ("resolution", resolution_clipped),
                    ("semantic_assessment", semantic_clipped),
                )
                if flag
            ],
            **paginated,
        }
        out["context"] = {
            "old": _context(
                old_elements,
                element_id=old.get("element_id"),
                reading_order=old.get("first_reading_order"),
                window=context_window,
                text_offset=text_offset,
                text_limit=text_limit,
            ),
            "new": _context(
                new_elements,
                element_id=new.get("element_id"),
                reading_order=new.get("first_reading_order"),
                window=context_window,
                text_offset=text_offset,
                text_limit=text_limit,
            ),
        }
    else:
        evidence, evidence_clipped = _bounded_json(record.get("evidence"))
        resolution, resolution_clipped = _bounded_json(_resolution(record))
        if isinstance(resolution, dict):
            resolution["result_state"] = "uncertain"
        semantic_assessment, semantic_clipped = _bounded_json(
            normalize_assessment(record.get("semantic_assessment"))
        )
        correspondence = (
            evidence.get("correspondence")
            if isinstance(evidence, dict) and evidence.get("correspondence")
            else semantic_assessment.get("correspondence")
        )
        old_anchors, new_anchors, linked_refs = _review_anchor_sources(record, index)
        out["review"] = {
            "review_id": record.get("review_id"),
            "source_kind": record.get("source_kind"),
            "change_id": record.get("change_id"),
            "table_change_id": record.get("table_change_id"),
            "case_type": record.get("case_type"),
            "result_state": "uncertain",
            "needs_review": True,
            "confirmed": False,
            "resolution": resolution,
            "semantic_assessment": semantic_assessment,
            "source_correspondence": correspondence,
            "evidence": evidence,
            "linked_refs": linked_refs,
            "clipped": [
                name
                for name, flag in (
                    ("evidence", evidence_clipped),
                    ("resolution", resolution_clipped),
                    ("semantic_assessment", semantic_clipped),
                )
                if flag
            ],
        }
        out["context"] = {
            "old": _review_context(
                old_elements,
                old_anchors,
                context_source_offset,
                MAX_CONTEXT_SOURCES,
                context_window,
                text_offset,
                text_limit,
            ),
            "new": _review_context(
                new_elements,
                new_anchors,
                context_source_offset,
                MAX_CONTEXT_SOURCES,
                context_window,
                text_offset,
                text_limit,
            ),
        }

    _context_warnings(out)
    out["ground_truth_notice"] = GROUND_TRUTH_NOTICE
    _enforce_budget(out)
    return out
