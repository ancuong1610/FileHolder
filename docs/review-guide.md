# Review guide

Route a task to the owning file, then run the smallest relevant check. Read in
this order before touching code: `docs/architecture.md` → the owning folder
`README.md` → the named module → its test. Do not read all source.

## Read order for a change

1. `docs/architecture.md` — where the responsibility lives and the one-way
   dependency direction.
2. The folder README for the subsystem you will edit.
3. The specific module/function named below.
4. The matching test file; run it before and after the edit.

## Task → owner → check

| Symptom / task | Owning file(s) and function | Check |
| --- | --- | --- |
| Numeric/date/sign extraction wrong | `phase1/stages/s01_extract.py`, `phase1/stages/s04_normalize.py` | `python -m unittest tests.phase1.test_normalize_hyphenation` |
| Wrong pairing of changed text | `phase1/stages/s07_modified_matching.py`, `phase1/matching/reconciliation.py` | `python -m unittest tests.phase1.test_reconcile tests.phase1.test_matching_stage_module` |
| Missing/duplicated table cells | `phase1/stages/s10_tables.py` | `python -m unittest tests.phase1.test_table_stage_module` |
| Wrong movement/resegmentation of blocks | `phase1/stages/s09_movement.py` | `python -m unittest tests.phase1.test_movement_stage_module` |
| TOC / hierarchy / furniture wrong | `phase1/stages/s05_anchors.py`, `phase1/structure/reconstruction.py`, `phase1/structure/furniture.py` | `python -m unittest tests.phase1.test_structure_evidence` |
| Native outline/bookmark ignored, mis-bound or wrongly promoted | `phase1/extraction/pdf_outline.py`, `phase1/structure/reconstruction.py`, `phase1/stages/s01_extract.py`, `phase1/stages/s05_anchors.py` | `python -m unittest tests.phase1.test_pdf_outline tests.phase1.test_structure_evidence` |
| Uncertain route/state wrong, or approval semantics leak back in | `phase1/stages/s11_routing.py`, `phase1/stages/s14_canonical_result.py`, `phase1/contracts/result_state.py` | `python -m unittest tests.phase1.test_automatic_reports tests.phase1.test_route_identity` |
| Complete HTML/PDF report missing uncertainty or grouping wrong | `phase1/reporting/report_model.py`, `phase1/reporting/html.py`, `phase1/reporting/redline_pdf.py` | `python -m unittest tests.phase1.test_automatic_reports tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf` |
| Review item routed or dropped incorrectly | `phase1/stages/s11_routing.py` | `python -m unittest tests.phase1.test_route_identity tests.phase1.test_generic_only` |
| Wrong PDF markup / redline | `phase1/reporting/redline_pdf.py`, `phase1/reporting/inline_pdf.py`, `phase1/reporting/html.py` | `python -m unittest tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf tests.phase1.test_diff_annotations` |
| Canonical result schema / outcome migration | `phase1/stages/s14_canonical_result.py`, `phase1/contracts/outcomes.py` | `python -m unittest tests.phase1.test_evaluation_metrics` |
| Benchmark / diagnostics metrics | `phase1/evaluation/evaluate.py`, `phase1/evaluation/matching_diagnostics.py` | `python -m unittest tests.phase1.test_evaluation_metrics tests.phase1.test_matching_diagnostics` |
| Job API / status / summary wrong | `phase2/api/service.py`, `phase2/api/summary.py` | `python -m unittest tests.phase2.test_service` |
| Agent search/details incomplete or wrong | `phase2/reader/result_reader.py`, `phase2/mcp/reader_server.py`, `phase2/agent/tools.py` | `python -m unittest tests.phase2.test_reader tests.phase2.test_agent_reader` |
| Finding highlighted on the wrong page/region, or location degrades wrongly | `phase2/viewer/locations.py` | `python -m unittest tests.phase2.test_viewer_locations` |
| Page image, highlight, rotation or scale wrong | `phase2/viewer/page_render.py` | `python -m unittest tests.phase2.test_page_render` |
| Page/location/chat route or its error code wrong | `phase2/api/service.py` (view and chat routes) | `python -m unittest tests.phase2.test_view_api` |
| Synthetic PDFs or seeded demo job wrong | `phase2/viewer/synthetic.py` | `python -m unittest tests.phase2.test_viewer_synthetic` |
| Streamlit page, findings list, page viewer or chat panel wrong | `phase2/ui/app.py`, `phase2/ui/review.py`, `phase2/ui/chat.py`, `phase2/ui/api_client.py` | `python -m unittest tests.phase2.test_ui_client tests.phase2.test_ui_smoke` |
| Reader result state or legacy status/kind filter wrong | `phase2/reader/result_reader.py`, `phase2/api/summary.py`, `phase1/contracts/result_state.py` | `python -m unittest tests.phase1.test_automatic_reports tests.phase2.test_reader` |
| Workflow MCP / subprocess launch broken | `phase2/mcp/workflow_server.py`, `phase2/workflow/local_reviewer.py` | `python -m unittest tests.phase2.test_mcp tests.phase2.test_local_flow` |
| Phase selection / saved workspace reuse wrong | `phase2/workflow/local_reviewer.py`, `phase2/workflow/saved_workspace.py` | `python -m unittest tests.phase2.test_local_flow tests.phase2.test_saved_workspace` |
| Import boundary or stage count broken | `phase1/stages/`, dependency edges | `python -m unittest tests.integration.test_architecture tests.integration.test_stage_import_safety` |
| README / docs links broken | folder `README.md`s, `docs/` | `python -m unittest tests.integration.test_architecture.ReadmeCoverageTest` |
| Agent harness structure, skills or route links broken | `AGENTS.md`, `docs/governance/`, `.agents/skills/`, `tools/harness/validate.py` | `python -m unittest tests.integration.test_harness` and `python tools/harness/validate.py` |

## Boundaries not to break

- Phase 1 never imports Phase 2; `shared` imports neither phase.
- `phase2/ui` talks to the API over HTTP only; `phase2/viewer` and the view
  routes never write into a job's `output/`.
- Keep the `CRR_` runtime env-key prefix and artifact/schema names as-is.
- Do not re-add `run_local.py`, a `compat/` package, or the old `crr_compare` /
  `agent_poc` namespaces.

The original phase-layout refactor did not claim an accuracy improvement.
Subsequent TOC/furniture corrections, outline-first structure, automatic
uncertainty results and saved-workspace modes are documented in
[architecture](architecture.md) and [phase testing](operations/phase-testing.md).
Synthetic regressions do not certify private-document or universal accuracy.
