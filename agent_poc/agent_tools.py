from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from agent_poc.mcp_gateway import ComparisonGateway, canonical_from, matches_query


def _resolution_status(item: dict[str, Any]) -> str:
    return str((item.get("resolution") or {}).get("status") or "")


def _compact_change(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_id": item.get("change_id") or item.get("candidate_id"),
        "change_type": item.get("change_type"),
        "status": _resolution_status(item),
        "old": item.get("old"),
        "new": item.get("new"),
        "evidence": item.get("evidence") or item.get("similarity_evidence"),
    }


def build_tools(gateway: ComparisonGateway, result_limit: int = 8) -> dict[str, StructuredTool]:
    def get_comparison_status(job_id: str) -> dict[str, Any]:
        """Get job state and completed pipeline stages."""
        return gateway.status(job_id)

    def get_comparison_summary(job_id: str) -> dict[str, Any]:
        """Get counts for confirmed changes, tables, and unresolved review cases."""
        report = canonical_from(gateway.report(job_id))
        changes = report.get("changes", [])
        tables = report.get("table_changes", [])
        reviews = report.get("review_queue", [])
        accepted = [item for item in [*changes, *tables] if _resolution_status(item) == "accepted"]
        return {
            "job_id": job_id,
            "statistics": report.get("statistics", {}),
            "accepted_count": len(accepted),
            "open_review_count": len(reviews),
            "warning": "Open review cases are unresolved and are not confirmed changes.",
        }

    def search_confirmed_changes(job_id: str, query: str = "", change_type: str = "") -> dict[str, Any]:
        """Search accepted-only changes. Review cases are deliberately excluded."""
        report = canonical_from(gateway.report(job_id))
        candidates = [*(report.get("changes") or []), *(report.get("table_changes") or [])]
        found = []
        for item in candidates:
            if _resolution_status(item) != "accepted":
                continue
            if change_type and str(item.get("change_type", "")).casefold() != change_type.casefold():
                continue
            if matches_query(item, query):
                found.append(_compact_change(item))
        return {"total_matches": len(found), "returned": found[:result_limit], "truncated": len(found) > result_limit}

    def search_open_reviews(job_id: str, query: str = "") -> dict[str, Any]:
        """Search unresolved review items and their LLM advice, without treating them as facts."""
        report = canonical_from(gateway.report(job_id))
        found = [item for item in report.get("review_queue", []) if matches_query(item, query)]
        return {
            "total_matches": len(found),
            "returned": found[:result_limit],
            "truncated": len(found) > result_limit,
            "status": "unresolved",
        }

    def get_change_evidence(job_id: str, change_id: str) -> dict[str, Any]:
        """Get full evidence for one accepted change ID or one review ID."""
        report = canonical_from(gateway.report(job_id))
        pools = [report.get("changes", []), report.get("table_changes", []), report.get("review_queue", [])]
        for pool in pools:
            for item in pool or []:
                identifiers = {item.get("change_id"), item.get("review_id"), item.get("candidate_id")}
                if change_id in identifiers:
                    return item
        return {"error": "not_found", "change_id": change_id}

    functions = [
        get_comparison_status,
        get_comparison_summary,
        search_confirmed_changes,
        search_open_reviews,
        get_change_evidence,
    ]
    return {
        function.__name__: StructuredTool.from_function(function)
        for function in functions
    }

