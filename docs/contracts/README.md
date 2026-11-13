# Contracts

Active interface contracts. These describe behavior the code must keep; they
are not a changelog.

Read first: [three-tool-reader](three-tool-reader.md) (the `summarize`/`search`/
`details` read boundary and response limits).

- `three-tool-reader.md` — reader MCP/agent read contract, cursor/limit rules,
  normalized `result_state`, and compatible legacy filters.
- `phase1-matching.md` — matching thresholds and benchmark scoring schema.
- `view-endpoints.md` — read-only finding-location and page-image routes plus the
  chat route used by the Streamlit UI; response shapes and error codes.

Dependencies: describes `phase2/mcp`, `phase2/reader`, `phase2/viewer`, the view/chat
routes of `phase2/api` and `phase1/matching`, `phase1/evaluation`; links out to `docs/architecture.md`.

Check: `python -m unittest tests.phase2.test_reader tests.phase2.test_view_api tests.phase1.test_matching_diagnostics`.
