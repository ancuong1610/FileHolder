# Contracts

Active interface contracts. These describe behavior the code must keep; they
are not a changelog.

Read first: [three-tool-reader](three-tool-reader.md) (the `summarize`/`search`/
`details` read boundary and response limits).

- `three-tool-reader.md` — reader MCP/agent read contract, cursor/limit rules,
  normalized `result_state`, and compatible legacy filters.
- `phase1-matching.md` — matching thresholds and benchmark scoring schema.

Dependencies: describes `phase2/mcp`, `phase2/reader` and `phase1/matching`,
`phase1/evaluation`; links out to `docs/architecture.md`.

Check: `python -m unittest tests.phase2.test_reader tests.phase1.test_matching_diagnostics`.
