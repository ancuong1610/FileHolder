# Phase-separated repository design

## Approved intent and scope

Owner approved the proposed Phase 1/Phase 2 structure and explicitly rejected `compat/`. Optimize the repository for a reviewer: know the execution flow, which module owns a responsibility, and where to test a change without reading everything. This is a coordinated structural refactor, not an algorithm/accuracy, DB, provider, authentication or deployment redesign.

Base revision: `2c1418a219e14e434c927909c93ac14196d22ab4` (includes the latest MCP/agent work). Old Python namespaces and CLI entry points intentionally stop working. Do not create legacy wrappers, aliases or duplicate implementations. The currently configured provider credential names, runtime environment keys, canonical schemas, artifact filenames and HTTP/MCP contracts stay unchanged; renaming those external contracts is not necessary to remove old Python namespaces.

## Target layout

```text
README.md
pyproject.toml
requirements.txt
src/
  document_compare/
    __init__.py
    __main__.py
    phase1/
      stages/          # Exactly s01–s15, existing filenames/order retained
      pipeline/        # CLI, runner, generic-only mode configuration
      extraction/      # DI field/page helpers
      structure/       # TOC, hierarchy, recurring furniture
      matching/        # Reconciliation and language-profile helpers
      contracts/       # Validation, review identities, outcome migration
      reporting/       # Original/inline PDF, HTML, diff annotations/spans
      evaluation/      # Optional benchmarks/diagnostics/evidence review
    phase2/
      api/             # HTTP service and operational summary
      workflow/        # Local submit/poll/start/stop orchestration
      mcp/             # Existing workflow surface + three-tool reader surface
      reader/          # Read-only summary/search/details projection
      agent/           # Planner/controller, tools, gateway, prompts, mock
    shared/            # Generic path and JSON I/O helpers only
tests/
  phase1/
  phase2/
  integration/
  support/
  fixtures/{general,regression,structure}/
docs/
  architecture.md
  review-guide.md
  contracts/
  operations/
  releases/
input/                 # Private/runtime input, preserve contents
workspace/             # Generated/runtime, ignored, preserve contents
```

Every maintained source, test, fixture and documentation directory gets a concise `README.md`; this excludes `.git`, environments, caches and generated workspaces. Avoid empty taxonomy folders. No `compat/`, `crr_compare/`, `agent_poc/` package or `run_local.py` forwarding script remains in the final tree.

## Ownership and dependency direction

- Phase 1 owns all 15 extraction/comparison/review/report stages. Stage 15 remains in Phase 1: generating PDFs/HTML/CSV/index artifacts is reporting, not agent/API logic.
- Phase 2 invokes Phase 1 through its pipeline entry point and consumes saved artifacts. Phase 1 never imports Phase 2 or its HTTP/MCP/agent frameworks.
- Shared helpers depend on neither phase. Keep DI page-field helpers in Phase 1, not in a catch-all shared business module.
- Runtime engine code must not depend on optional evaluation tooling. Extract the production `build_outcome_migration` helper (and only its necessary pure dependencies) from `evaluate.py` into Phase 1 contracts; evaluation imports that implementation rather than duplicating it.
- Preserve the Stage-15 generated `phase2_change_index.json` filename as an artifact contract; it is not a Phase-2 Python dependency. Explain the historical name in reporting documentation rather than change schemas/consumers during this refactor.
- Do not opportunistically rewrite large algorithms. Split only where necessary to establish these boundaries; module/function anchors and review guides provide the first readability improvement.

## Production module moves

All `crr_compare/stages/s*.py` move to `src/document_compare/phase1/stages/` without changing stage numbering, filenames or business logic.

| Existing module | New location under `src/document_compare/` |
| --- | --- |
| `crr_compare/cli.py` | `phase1/pipeline/cli.py` |
| `crr_compare/domain.py` | `phase1/pipeline/mode.py` |
| `crr_compare/validation.py` | `phase1/contracts/validation.py` |
| `crr_compare/route_identity.py` | `phase1/contracts/review_identity.py` |
| `crr_compare/lang_profile.py` | `phase1/matching/language.py` |
| `crr_compare/reconcile.py` | `phase1/matching/reconciliation.py` |
| `crr_compare/structure.py` | `phase1/structure/reconstruction.py` |
| `crr_compare/derived_structure.py` | `phase1/structure/furniture.py` |
| `crr_compare/redline.py` | `phase1/reporting/html.py` |
| `crr_compare/redline_pdf.py` | `phase1/reporting/redline_pdf.py` |
| `crr_compare/inline_pdf.py` | `phase1/reporting/inline_pdf.py` |
| `crr_compare/render_spans.py` | `phase1/reporting/spans.py` |
| `crr_compare/diff_annotations.py` | `phase1/reporting/annotations.py` |
| `evaluate.py`, `mutations.py`, `gt_benchmark.py`, `performance_benchmark.py`, `matching_diagnostics.py`, `evidence_review.py`, `evidence_review_html.py` in `crr_compare/` | Same stems under `phase1/evaluation/` |
| `crr_compare/service.py` | `phase2/api/service.py` |
| `crr_compare/summary.py` | `phase2/api/summary.py` |
| `crr_compare/result_reader.py` | `phase2/reader/result_reader.py` |
| `crr_compare/mcp_server.py` | `phase2/mcp/workflow_server.py` |
| `crr_compare/reader_mcp_server.py` | `phase2/mcp/reader_server.py` |
| `agent_poc/local_reviewer.py` | `phase2/workflow/local_reviewer.py` |
| `agent_poc/agent.py` | `phase2/agent/controller.py` |
| `agent_poc/agent_tools.py` | `phase2/agent/tools.py` |
| `agent_poc/mcp_gateway.py` | `phase2/agent/gateway.py` |
| `agent_poc/prompts.py`, `tia_client.py`, `mock_reader.py` | Same stems under `phase2/agent/` |

Split `common.py`: root/package/workspace path helpers → `shared/paths.py`; load/save JSON → `shared/json_io.py`; page-number helpers → `phase1/extraction/di_fields.py`. Rewrite imports by symbol, not a blind text replacement of document contents.

## Entry points and packaging

Use standard setuptools `src/` packaging with Python 3.10+ and editable installation. Existing public dependencies remain in `requirements.txt` (single source for package dependency metadata); no new runtime dependency/framework. Add build artifacts and egg-info to ignore rules. No `sys.path.insert` bootstrap or checkout-specific interpreter paths.

Documented replacements:

```sh
python -m pip install -e .
python -m document_compare demo --fixture internal_policy_report --root FRESH_ROOT
python -m document_compare run --root JOB_ROOT --from-stage 02 --to-stage 15
python -m document_compare.phase2.workflow.local_reviewer
python -m document_compare.phase2.agent.mock_reader --out FRESH_OUTPUT
python -m document_compare.phase2.mcp.workflow_server
python -m document_compare.phase2.mcp.reader_server
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m unittest discover -s tests
```

The root package CLI delegates to the Phase-1 pipeline CLI. The local-reviewer module gains an explicit runnable guard in place of removed `run_local.py`. Update all subprocess commands, MCP server launch targets, dynamically named imports, runner paths, tests and docs together. Installation and running from paths containing spaces must work.

Path resolution must distinguish repository/package location from runtime job root; do not write jobs into `src/` or site-packages. Offline demo/evaluation fixture access is explicitly a source-checkout development workflow; normal comparison must not import test helpers or require bundled tests. Missing fixtures fail actionably. Existing runtime env keys are documented rather than silently ignored/renamed.

## Tests, fixtures and cleanup

- Move existing tests by ownership: engine/structure/rendering/evaluation → `tests/phase1`; reader/API/MCP/agent → `tests/phase2`; pipeline/setup/import/architecture checks crossing phases → `tests/integration`.
- Move `regression_support.py` and `structure_fixtures.py` into `tests/support`, updating every import and root/path calculation. Move all public synthetic fixture JSON unchanged into `tests/fixtures/{general,regression,structure}`.
- Retain meaningful regression coverage and the explicitly optional private-fixture tests. Private `output_sample` is not copied or published. Test count may change only for explained obsolete API tests and added boundary tests, not by deleting failures.
- Confirmed inactive legacy `agent_poc/config.py`, `api_client.py`, old in-process/API/mock gateways and their client-side filtering helpers may be removed after a final reference check. Preserve the active reader stdio gateway and actual workflow MCP behavior. Migrate gateway/transport tests to active APIs rather than abandon their safety checks.
- The unused `tests/synthetic_stage_support.py` may be removed after confirming no references. No generated/user files are cleanup targets.
- Move operational docs to `docs/operations`, MCP contract to `docs/contracts`, historical release/status notes and export manifest to `docs/releases`. Label historical records as historical; update active commands/links. Do not remove valuable evidence merely because it is old.
- Refresh the release manifest for new paths/hashes after verification, excluding itself from self-hashing.

## Reviewer navigation and README contract

Root README is a short start page, not the full manual. `docs/architecture.md` contains a tree, one dependency diagram, a concise 15-stage input/output map and runtime flow. `docs/review-guide.md` routes tasks such as incorrect numeric extraction, wrong matching, missing table cells, wrong PDF markup or incomplete agent search to their owning files/functions/tests.

Each folder README explains purpose, input/output, read-first files, allowed dependencies and the smallest relevant check. Use lowercase snake_case Python names, lower-case hyphenated documentation filenames and uppercase `README.md`. Avoid duplicate prose; link to the authoritative guide/contract.

## Acceptance / non-regression

Baseline from the correct base: 618 tests collected, 607 passed, 11 optional private skips; three Phase-1 demos and the real-transport 12-case MCP/agent mock pass (`workspace/refactor-baseline-current/`).

Required after migration: editable install and pip check; all three demos; canonical semantic equivalence against baseline (IDs, kinds, raw old/new text/location, review reasons/resolution/joins/cell accounting; exclude only declared volatile timestamps/timings/runtime paths); original and inline PDFs present; exactly 15 stages; five workflow and three reader tools unchanged; all 12 mock cases pass; full discovered suite; architecture/import-boundary and README/link coverage tests; no old executable namespace/entry-point references outside the explicit migration/history documentation. Fixture bytes and runtime data remain intact. One independent final review covers the coherent refactor and preservation evidence. No push/deployment is implied by moving files locally.
