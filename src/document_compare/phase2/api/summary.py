from __future__ import annotations
import json
from pathlib import Path
from typing import Any

def _load(job_root: Path, name: str) -> dict[str, Any]:
    path = Path(job_root) / "output" / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

def _resolution_counts(changes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for change in changes:
        status = (change.get("resolution") or {}).get("status", "review")
        counts[status] = counts.get(status, 0) + 1
    return counts

def _flag_counts(report: dict[str, Any]) -> tuple[int, int]:
    """Return (warning_count, issue_count) from explicit or nested flag lists."""
    warnings = 0
    issues = 0
    document_flags = report.get("document_flags") or []
    if report.get("requires_review"):
        issues += len(document_flags)
    else:
        warnings += len(document_flags)
    native = report.get("native_text_validation") or {}
    pages = native.get("pages") or []
    for page in pages:
        warnings += len(page.get("warning_flags") or [])
        issues += len(page.get("review_flags") or [])
    return warnings, issues

def _document_status(warnings: int, issues: int, requires_review: bool, report_present: bool) -> str | None:
    if not report_present:
        return None
    if requires_review:
        return "REVIEW"
    if issues:
        return "REVIEW"
    if warnings:
        return "WARNING"
    return "PASS"

def _document_summary(report: dict[str, Any], doc_info: dict[str, Any]) -> dict[str, Any]:
    warnings, issues = _flag_counts(report)
    requires_review = bool(report.get("requires_review"))
    summary = report.get("summary") or {}
    return {
        "pages": doc_info.get("page_count"),
        "elements": doc_info.get("source_element_count"),
        "content_characters": summary.get("content_characters"),
        "warning_count": warnings,
        "issue_count": issues,
        "requires_review": requires_review,
        "status": _document_status(warnings, issues, requires_review, bool(report)),
    }

def _extraction(old_val: dict[str, Any], new_val: dict[str, Any],
                old_doc: dict[str, Any], new_doc: dict[str, Any]) -> dict[str, Any]:
    old_summary = _document_summary(old_val, old_doc)
    new_summary = _document_summary(new_val, new_doc)
    statuses = [s for s in (old_summary["status"], new_summary["status"]) if s is not None]
    if "REVIEW" in statuses:
        verdict = "REVIEW"
    elif "WARNING" in statuses:
        verdict = "WARNING"
    elif statuses:
        verdict = "PASS"
    else:
        verdict = None
    return {
        "old": old_summary,
        "new": new_summary,
        "verdict": verdict,
    }

def _changes_counts(changes: list[dict[str, Any]], tables: list[dict[str, Any]]) -> dict[str, int]:
    key_map = {"MODIFIED": "modified", "ADDED": "added", "DELETED": "deleted"}
    counts: dict[str, int] = {"modified": 0, "added": 0, "deleted": 0, "table": 0}
    for change in changes:
        key = key_map.get(str(change.get("change_type", "")).upper())
        if key:
            counts[key] += 1
    counts["table"] = len(tables)
    return counts

def _disposition(changes: list[dict[str, Any]], stats: dict[str, Any],
                 review_queue: list[dict[str, Any]], non_semantic: list[dict[str, Any]]) -> dict[str, int]:
    disposition = dict(stats.get("resolution_status_counts") or _resolution_counts(changes))
    for k in ("accepted", "review", "suppressed"):
        disposition.setdefault(k, 0)
    review_count = stats.get("review_queue")
    if isinstance(review_count, int):
        disposition["review"] = review_count
    else:
        disposition["review"] = len(review_queue)
    suppressed_count = stats.get("non_semantic_events")
    if isinstance(suppressed_count, int):
        disposition["suppressed"] = suppressed_count
    else:
        disposition["suppressed"] = len(non_semantic)
    return disposition

def _matching(movement_stats: dict[str, Any]) -> dict[str, Any]:
    key_map = {
        "input_unchanged": "unchanged",
        "input_modified": "modified",
        "input_modified_review": "modified_review",
        "input_duplicate_exact_review": "duplicate_review",
        "relocated_unchanged": "relocated",
        "remaining_deleted_candidates": "old_unmatched",
        "remaining_added_candidates": "new_unmatched",
        "resegmentation_review_pairs": "resegmentation_review",
    }
    return {v: movement_stats.get(k) for k, v in key_map.items()}

def _table_summary(tables: list[dict[str, Any]], unresolved: list[dict[str, Any]]) -> dict[str, Any]:
    return {"changes": len(tables), "unresolved_reviews": len(unresolved)}

def build_summary(job_root: Path, status: dict[str, Any]) -> dict[str, Any]:
    result = _load(job_root, "comparison_result.json")
    old_val = _load(job_root, "old_extraction_validation.json")
    new_val = _load(job_root, "new_extraction_validation.json")
    candidates = _load(job_root, "comparison_candidates_with_movement.json")
    decisions = _load(job_root, "llm_review_decisions.json")

    old_doc = result.get("old_document") or {}
    new_doc = result.get("new_document") or {}
    changes = result.get("changes", [])
    tables = result.get("table_changes", [])
    stats = result.get("statistics") or {}
    review_queue = result.get("review_queue", [])
    non_semantic = result.get("non_semantic_events", [])
    processing = decisions.get("processing") or {}

    return {
        "job_id": status.get("job_id"),
        "progress": {
            "state": status.get("state", "queued"),
            "stages_completed": status.get("stages_completed", []),
            "total_stages": 15,
            "created_at": status.get("created_at"),
            "started_at": status.get("started_at"),
            "finished_at": status.get("finished_at"),
            "duration_seconds": status.get("duration_seconds"),
            "failure": status.get("error_tail") if status.get("state") == "failed" else None,
        },
        "documents": {
            "old": _document_summary(old_val, old_doc),
            "new": _document_summary(new_val, new_doc),
        },
        "extraction": _extraction(old_val, new_val, old_doc, new_doc),
        "changes": _changes_counts(changes, tables),
        "disposition": _disposition(changes, stats, review_queue, non_semantic),
        "matching": _matching((candidates.get("statistics") or {})),
        "tables": _table_summary(tables, result.get("unresolved_table_reviews", [])),
        "evaluator": {
            "mode": processing.get("mode"),
            "provider": processing.get("provider"),
            "model": processing.get("model"),
        },
    }