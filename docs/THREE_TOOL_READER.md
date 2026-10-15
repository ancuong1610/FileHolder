# Three-tool MCP and agent reader

Status: owner-approved read/workflow boundary; implemented; final verification recorded below.

## Boundary and selected approach

The owner approved `summarize`, `search`, `details` as the agent's read-only result interface. Submit and polling stay in the existing orchestration workflow; no action multiplexer is added to the three tools. This approval applies to both MCP and the consuming agent, including a synthetic mock demonstration.

Reuse installed FastMCP and the existing HTTP service, not a custom MCP framework. The official FastMCP tools documentation supports typed schemas, structured dictionary results, read-only annotations and safe error handling: https://gofastmcp.com/servers/tools. Preserve the existing five-tool workflow MCP entry point for compatibility. Add a separate reader MCP entry point exposing exactly three tools; this is preferable to exposing job submission to the Q&A planner or removing existing orchestration tools.

The service owns a reusable read projection over saved canonical/structured artifacts. MCP adapts HTTP requests; the agent does not download the full report and reimplement search. No database migration, new provider, vector store, semantic-accuracy claim or public deployment is part of this slice. Local pilot does not imply the production architecture is permanently local. Shared-production authentication/storage remains open.

## Contracts

Every call is scoped by a validated job ID. Summaries distinguish `queued`/`running`/`failed` from a completed result; search/details reject unfinished jobs rather than read partial output. Job scoping and preventing the planner from changing its bound job are not substitutes for server-side authorization in a future shared deployment.

- `summarize(job_id)`: bounded overall status, counts for element/table changes and unresolved review, processing/extraction/structure limitations where present, old/new identity where available, and safe relative artifact links. Counts are computed from records, not narrated by another LLM. Engine acceptance is not legal/semantic ground truth. Missing data is explicit.
- `search(job_id, query="", kind="all", status="all", change_type="", cursor=None, limit=10)`: bounded literal/token-based search across changes, tables (including cell text) and review records. Return stable typed references, useful snippets, status/review flags, source pages, total matches, truncation and a next cursor. `limit` is validated and capped at 50; no silent first-page-as-complete result. Default scope includes review, not accepted-only.
- `details(job_id, kind, record_id, context_window=1, cell_offset=0, cell_limit=20, text_offset=0, text_limit=4000, context_source_offset=0)`: canonical old/new values, locations/evidence, resolution/review reasons and bounded surrounding document context, with table cell rows/columns/headers where available. For a review record, `linked_refs` gives typed, canonical-validated references to its linked change/table (the review stays unresolved) and `context` groups bounded neighbour text per source anchor id, paged by `context_source_offset` with per-side `sources_total`/`source_offset`/`sources_truncated`/`next_source_offset` (`MAX_CONTEXT_SOURCES` anchors per side per page; old and new share the offset). Long text/cells/context have explicit offset/limit windows and a continuation mechanism (`text_next_offset`, `cell_*`, `next_source_offset`), not invisible clipping or a full-report dump. Unsupported/missing source context is named rather than invented from tentative hierarchy. Every successful envelope is bounded by `MAX_RESPONSE_BYTES`; exceeding it returns `response_too_large` rather than silently dropping fields. Optional offsets/limits are documented with the implementation.

Response metadata identifies the result snapshot. Search cursors bind job, query/filters and canonical fingerprint, with deterministic ordering; malformed, cross-job/query and stale cursors are rejected. Cursor encoding is not authentication. Typed record references distinguish change, table and review IDs; duplicate/ambiguous IDs are contract failures rather than dictionary overwrite. Query/limits/context windows are bounded. No tool accepts arbitrary filesystem paths or returns provider credentials/logs.

Service read errors have stable safe codes (`not_found`, `not_ready`, `invalid_arguments`, `invalid_cursor`, `stale_cursor`, `invalid_artifact`, `response_too_large`, `context_unavailable` as applicable), not leaked stack traces or private server paths. HTTP/MCP transport errors remain distinguishable from a successful empty search.

## Agent

The Q&A planner can use exactly `summarize`, `search`, `details` plus its final-answer control action. Its bound job is authoritative; attempted job changes are rejected/overridden. Keep bounded steps, result sizes and transport waits. Status/poll/submission are not model tools. Prompts require citations from returned references, accepted/review separation, pagination awareness, and no factual completeness claim while results are truncated or unresolved. Document content is evidence, not executable instructions.

The interactive local workflow opens the existing orchestration MCP client for submission/polling and the reader MCP client for Q&A. Retain old public gateway methods only where needed for orchestration/backward compatibility; do not keep two different implementations of result filtering.

## Verification checklist

- [x] Isolated ordinary Git worktree; baseline 515 collected, 504 passed and 11 optional private-fixture skips.
- [x] RED then GREEN unit/contract tests: summary counts, scoped refs, cursor traversal/staleness, text/cell limits, source context, missing/unfinished/malformed jobs, accepted versus review and empty results.
- [x] Reader MCP discovery over real stdio exposes exactly the three typed tools; existing workflow discovery remains unchanged.
- [x] Agent + real reader MCP + HTTP/read backend with independently authored synthetic saved artifacts: overview, search-to-details, numeric values, table context, pagination, no-hit/missing ref, unfinished job, attempted job switch, forbidden action and step limit. Retain traces and per-case expected/observed outcomes.
- [x] Runnable offline mock demonstration, no credentials/DI/provider calls; all owned server/processes have bounded readiness and cleanup. Output preparation rejects existing non-empty paths and symlinks rather than deleting files.
- [x] Scripted/mock planner verifies contracts and orchestration only, not real-model reasoning, retrieval recall, injection resistance or real-company semantic correctness.
- [x] Relevant existing tests and final full suite; actual diff inspected; independent review and focused final confirmation approved the data-scoping/privacy and retrieval boundaries.

Verification: **618 tests collected, 607 passed, 11 optional private-fixture
tests skipped**. The real-transport mock passed **12/12 scenarios** and exposed
exactly the three reader tools. This is contract/integration evidence, not real
LLM reasoning or company-provider validation.

## Scope of files

New read projection/schema helpers under `crr_compare/`; additive HTTP read endpoints in `service.py`; new reader-only FastMCP module. Agent gateway/tools/planner and local reviewer integrate the three-tool surface. New synthetic tests and mock CLI live with the project; private development PDFs/raw DI are never required or uploaded. Preserve Phase 1 pipeline, canonical output, rendering and the original orchestration MCP behavior.

## Run and inspect the mock

```sh
python -m agent_poc.mock_reader --out workspace/reader-mock
```

The destination must be absent or empty. Results are written to `report.json`;
re-runs use a fresh destination. Twelve independently authored scenarios cover
overview; numeric search/details and surrounding source text; accepted/review
separation; three-page traversal; table cell pages/header context; long-text tail
retrieval; empty search; missing reference; running job; attempted job switch;
forbidden submission; and step exhaustion. Tool discovery must be exactly
`details`, `search`, `summarize`. Trace values are synthetic, not company data.

The scripted planner generates its later actions from real returned references,
cursors and values; independent expectations are checked against raw observed
responses. This is an integration/contract check, not a learned planner benchmark.
The production planner receives the current three structured tool argument schemas
at every step, so text/cell/context continuation arguments are not hidden behind
an outdated handwritten tool list.

## Recorded review boundary

Independent review and final focused confirmation examined the read/agent
boundary; no blocking findings remained. No semantic or authorization guarantee
is inferred from that review.

Lead inspection additionally corrected real gaps before final checks: full-content
snapshot hashing, strict cursor validation, retrievable text windows, bounded
review-source context, aggregate response budget, planner schema disclosure, and
non-destructive mock output preparation. Search reports when review source text
is not indexed in canonical data, rather than claiming exhaustive retrieval.
