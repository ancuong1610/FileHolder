# Phase 2 MCP surfaces

Two stdio MCP entry points over the same HTTP service.

Read first: `workflow_server.py` (five orchestration tools) and
`reader_server.py` (exactly `summarize`/`search`/`details`).

Input/output: adapts HTTP requests to MCP tool calls; no own persistence.

Dependencies: `phase2/api` over loopback. Run with `python -m
document_compare.phase2.mcp.workflow_server` or `...mcp.reader_server`.

Check: `python -m unittest tests.phase2.test_mcp tests.phase2.test_reader`.
