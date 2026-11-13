# tests/phase2

Tests for the Phase 2 layer: HTTP service, workflow orchestration, MCP surfaces,
reader projection, the agent/mock, and the compare viewer
(finding locations, page images, view/chat routes) and its Streamlit UI.

Read first: `test_service.py` and `test_reader.py`; the read boundary contract is
[docs/contracts/three-tool-reader.md](../../docs/contracts/three-tool-reader.md).

Input/output: drives the API/MCP with synthetic saved artifacts under temporary
roots; the mock uses an owned loopback port and cleans up its processes.

View/UI modules: `test_viewer_locations.py`, `test_viewer_synthetic.py`,
`test_page_render.py`, `test_view_api.py` (contract:
[view-endpoints](../../docs/contracts/view-endpoints.md)), `test_ui_client.py`,
`test_ui_smoke.py` (skipped without Streamlit; `ui_stub.py` replaces the API
client). They use generated PDFs and scripted planners, with no network or model.

Dependencies: `document_compare.phase2.*` and `phase1` via the public service.

Check: `python -m unittest discover -s tests/phase2`.
