from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from document_compare.phase2.agent.gateway import ReaderGateway


def build_tools(gateway: ReaderGateway, *, result_limit: int = 10) -> dict[str, StructuredTool]:
    """Exactly the three read-only result tools plus the agent's final action.

    Each tool forwards to the reader gateway; the agent binds the authoritative
    job ID, so a planner-supplied job_id is always overridden (see agent.py).
    """

    def summarize(job_id: str) -> dict[str, Any]:
        """Bounded job status, counts, identity, limitations and relative artifact links.

        Safe for a job that is queued/running/failed; it never reads result records.
        """
        return gateway.summarize(job_id)

    def search(
        job_id: str,
        query: str = "",
        kind: str = "all",
        status: str = "all",
        change_type: str = "",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Literal/token search over changes, tables (cell text) and review records.

        kind is all/change/table/review; status is all/accepted/review; default
        scope includes unresolved review. When the response is truncated, call
        again with the returned next_cursor (and no other changed filter).
        """
        return gateway.search(
            job_id,
            query=query,
            kind=kind,
            status=status,
            change_type=change_type,
            cursor=cursor,
            limit=result_limit,
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
    ) -> dict[str, Any]:
        """Canonical old/new values, evidence and bounded surrounding context.

        kind is change/table/review. Long cells/text are returned in an explicit
        offset/limit window; text_next_offset is the continuation pointer when
        more text remains. For tables, page cell changes with cell_offset/
        cell_limit and follow next_offset until cell_changes_truncated is false.
        For a review, context groups neighbour text by source anchor id and
        context_source_offset pages those anchors (old and new share the offset).
        Missing source context is warned, never invented.
        """
        return gateway.details(
            job_id,
            kind=kind,
            record_id=record_id,
            context_window=context_window,
            cell_offset=cell_offset,
            cell_limit=cell_limit,
            text_offset=text_offset,
            text_limit=text_limit,
            context_source_offset=context_source_offset,
        )

    functions = [summarize, search, details]
    return {function.__name__: StructuredTool.from_function(function) for function in functions}
