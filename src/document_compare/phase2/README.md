# Phase 2

The service/orchestration/agent layer that consumes Phase 1 results: HTTP API,
local workflow, MCP surfaces and the read-only reader projection.

Read first: [api/](api/README.md) and [mcp/](mcp/README.md); the read boundary
contract is [docs/contracts/three-tool-reader.md](../../../docs/contracts/three-tool-reader.md).

Input/output: submits jobs to the Phase 1 pipeline and reads saved artifacts; no
document transformation of its own.

Dependencies: may call the Phase 1 public pipeline or saved artifacts, plus
[shared/](../shared/README.md); Phase 1 never imports Phase 2.

Check: `python -m unittest discover -s tests/phase2`.
