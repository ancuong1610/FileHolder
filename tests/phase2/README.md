# tests/phase2

Tests for the Phase 2 layer: HTTP service, workflow orchestration, MCP surfaces,
reader projection and the agent/mock.

Read first: `test_service.py` and `test_reader.py`; the read boundary contract is
[docs/contracts/three-tool-reader.md](../../docs/contracts/three-tool-reader.md).

Input/output: drives the API/MCP with synthetic saved artifacts under temporary
roots; the mock uses an owned loopback port and cleans up its processes.

Dependencies: `document_compare.phase2.*` and `phase1` via the public service.

Check: `python -m unittest discover -s tests/phase2`.
