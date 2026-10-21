# Outline-first automatic results implementation plan

**Goal:** use native PDF outlines when supported and deliver an automatic,
human-readable comparison that keeps uncertainty visible without approval tasks.

**Spec:** `../specs/2026-10-21-outline-first-automatic-results-design.md` (approved;
source strikethrough deferred). One coherent writer implements the coupled slice;
the lead inspects the actual diff and runs the final checks.

## Boundaries

- Keep the 15 stages, external input paths, existing artifact names and public
  HTTP/MCP/reader shapes. No new dependencies or real provider calls.
- No source-strikethrough implementation, input relocation, new UI application,
  Docker, automatic browser/screenshot loops, commits, pushes or deployments.
- Do not claim a fix for private table-extraction quality without its failing
  page/artifacts. Do not change Stage 10 candidate/threshold policies here.
- Preserve raw evidence, conservation, source identity, furniture invariants,
  bounded reader behavior, existing workspace readability and evaluator advice.
- Preserve legacy `review` machine fields as uncertainty compatibility; new
  user-facing results must not require human action or call uncertainty accepted.

## Milestones

### 1. PDF outline acquisition and Stage 5 support

Owners: new small helper under `phase1/extraction/` or `structure/`, Stage 1,
Stage 3 provenance, Stage 5/reconstruction; new focused outline tests.

- [x] RED: synthetic embedded outline present/absent/partial, invalid/external
  destination, page labels versus physical pages, stale SHA-256, legacy/no-PDF
  inputs and outline-read failure without aborting valid extraction.
- [x] Implement safe versioned old/new outline sidecars before DI. Carry the
  source SHA through the internal model and only consume verified matching
  evidence; fallback is mandatory and original source paths stay external.
- [x] Reconcile destination/title/label evidence into Stage 5 links and provisional
  context. Do not promote furniture, invent hierarchy, discard uncovered headings
  or accept a stale sidecar. No-outline behavior remains unchanged.
- [x] GREEN: `python -m unittest tests.phase1.test_pdf_outline tests.phase1.test_structure_evidence tests.phase1.test_extraction_retry tests.phase1.test_di_chunk_references`.

Milestone 1 evidence (2026-10-21, same worktree/venv):

- RED (new focused tests): `tests.phase1.test_pdf_outline` + pending
  `tests.phase1.test_automatic_reports` -> 13 tests, 4 failures + 9 errors
  (missing outline helper/API, no `result_state`, old `HUMAN_REVIEW`, incomplete
  reports). The report/routing tests remain pending by design.
- Detected fixture defect during the first focused run: the pre-existing
  `test_extraction_retry` fake PDF/mocked `pdf_sha256` made the optional outline
  prepass see a genuine hash mismatch, so extraction stopped before its intended
  retry assertions. The fixture now mocks the optional prepass; a separate
  genuine mismatch regression asserts Stage 1 still fails before extraction.
- GREEN: `python -m unittest tests.phase1.test_pdf_outline
  tests.phase1.test_structure_evidence tests.phase1.test_extraction_retry
  tests.phase1.test_di_chunk_references` -> 115 tests, OK, 6 optional skips.
  Log: `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/outline-auto-milestone1-rerun2.log`.
- Implemented: versioned sidecars with SHA-256/page count/status/validated entries;
  native-local destination kind plus finite-coordinate and page-consistency checks;
  nonlocal positive-page negatives; Stage 1 collects before DI including cached
  chunks, keeps the full source page count for identity under a legacy debug page
  subset, and refuses a mutated source identity; Stage 3 carries
  `document.source.extraction_source`; Stage 5 consumes only matching evidence,
  adds an additive `outline` block/sections and provisional outline anchors, and
  preserves no-outline/legacy behavior. Structure evidence 1.2.0 accepts legacy
  1.1.x snapshots.
- Remaining limit for this milestone: no real/private-PDF accuracy claim; the
  private table-quality case remains unverified. Printed-TOC/outline conflicts
  stay unmatched/ambiguous by design.

### 2. Automatic uncertainty results across canonical and reader

Owners: `s11_routing.py`, `s14_canonical_result.py`, a small shared Phase-1
resolution helper if useful; Phase-2 bounded reader/summary/agent wording; focused
tests under existing Phase-1/Phase-2 suites.

- [x] RED: new producers emit `UNCERTAIN_RESULT`, not `HUMAN_REVIEW`; legacy saved
  routes still read; resolution adds `result_state` while preserving legacy
  accepted/review/status keys; unknown/missing evidence never becomes accepted.
- [x] Implement normalized supported/uncertain/non-semantic result states. Keep
  deterministic acceptance/suppression guards and LLM advisory boundaries. No
  pause/approval/resume workflow and no duplicate uncertainty records.
- [x] Ensure reader summary/search/details and chat distinguish uncertainty using
  normal language, with unchanged filters, typed IDs, limits and job isolation.
- [x] GREEN: `python -m unittest tests.phase1.test_route_identity tests.phase1.test_generic_only tests.phase1.test_evaluation_metrics tests.phase2.test_reader tests.phase2.test_agent_reader tests.phase2.test_saved_workspace`.

Milestone 2 evidence (2026-10-21, same worktree/venv):

- Implemented: `s11_routing.py` emits `UNCERTAIN_RESULT` for duplicate, wrap,
  structural, tokenization, frontier, movement, table and unclassified cases;
  `HUMAN_REVIEW` is still accepted/read for legacy artifacts. `s14` writes
  explicit `resolution.result_state` and `statistics.result_state_counts` through
  the shared pure module `phase1/contracts/result_state.py`. The reader exposes
  `counts.result_state_counts`, per-search-item `result_state`, and typed
  `change`/`table`/`review` result state plus resolution state in details; the API
  summary exposes top-level `result_states`. Legacy `kind=review`,
  `status=accepted|review` filters and `review_queue`/`needs_review` fields are
  unchanged. Agent prompts describe uncertainty without an approval step.
- Targeted worker run: **254 tests OK** across the combined routing, reporting
  and reader suites (not only the milestone-2 command).
- Full worker run before the final orientation/state corrections: **682 tests,
  OK (skipped=11)**. Log:
  `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/outline-auto-worker-full1.log`.
- After that full run the lead applied narrow corrections and added regressions:
  side-aware section maps, side-specific table pages, and an explicit
  `result_state` that cannot override the state derived from deterministic
  `status`/`needs_review`. Final lead verification is recorded below.

### 3. Human-friendly complete reports

Owners: `phase1/reporting/` and Stage 15. Keep the existing reports/manifest and
accepted-only serving-index contract; use canonical records for completeness.

- [x] RED: primary report includes supported and uncertain differences, readable
  old/new document labels, summary counts, source pages and exact numeric values.
  Unresolved-only input must not produce a no-differences claim.
- [x] Improve HTML/PDF information order: plain-language summary; section/page
  groups; readable before/after text and row/column tables; technical details
  folded away in HTML or separated in a PDF appendix. Reuse current visual
  conventions, accessible text labels and local assets; preserve raw-source diff
  safety, warnings, escaping, pagination and report audit behavior.
- [x] Include evidence-only uncertain cases with reasons/references, without
  fabricating an old/new pairing. CSV is optional detail, never the only result.
- [x] GREEN: `python -m unittest tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf tests.phase1.test_diff_annotations tests.phase1.test_hyphen_wrap_output tests.phase1.test_automatic_reports`.

Milestone 3 evidence (2026-10-21, same worktree/venv):

- Implemented: `reporting/report_model.py` is the shared pure projection; HTML and
  PDF render one summary (document identities, supported/uncertain/non-semantic
  counts, limitations, truthful no-difference message), separated supported and
  uncertain sections, section/page groups and exact before/after text. Table
  changes render row/column/cell rows. Evidence-only uncertainty keeps reasons
  and references without a fabricated pair. HTML folds IDs/routes/scores into
  `<details>`; PDF puts them in a separated technical appendix. `render_pdf`
  accepts optional old/new structured documents while keeping the two-argument
  contract. `phase2_change_index.json` stays accepted-only.
- `tests.phase1.test_automatic_reports` covers the report/reader/routing
  regressions. The worker's full run above (682 OK, 11 skips) included these
  report tests before the final narrow corrections; the final lead rerun is
  recorded below.

### 4. Documentation and final verification

- [x] Add the approved pre-commit impact checklist to `AGENTS.md`; update root
  README, architecture, Knowledge Transfer, relevant contracts/operations and
  owner READMEs in the same eventual change. Historical release evidence stays
  historical. Label the deferred strikethrough/table-quality limits honestly.
- [x] `python -m unittest discover -s tests` (baseline 647, 11 optional skips).
- [x] Offline synthetic demo into a fresh root, actual report-content assertions
  and saved-reader `--check-only`; no claims of real-PDF/Windows/company accuracy.
- [x] Existing API/MCP mock reader: `python -m document_compare.phase2.agent.mock_reader --out FRESH_ROOT`.
- [x] Inspect full diff, `git diff --check`, docs links and related commands.
- [x] One final independent review of source binding, compatible uncertainty
  semantics and complete report/reader behavior; correct concrete findings and
  report any unavailable reviewer explicitly.

Documentation check evidence (2026-10-21, documentation-only pass): updated
`AGENTS.md`, root README, architecture, Knowledge Transfer, three-tool-reader,
phase-testing, review-guide, and the contracts/extraction/structure/reporting/
stages/reader/tests Phase-1 READMEs. `git diff --check` is clean and
`python -m unittest tests.integration.test_architecture.ReadmeCoverageTest` is
OK (1 test). Final lead evidence follows.

### Final lead verification (2026-10-21)

- Full suite, same Python/`PYTHONPATH=src`, `FASTMCP_CHECK_FOR_UPDATES=off`:
  `python -m unittest discover -s tests` — **695 tests OK, 11 optional skips**.
  Log: `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/outline-auto-final-verified.log`.
- Fresh `internal_policy_report` and `scai_api_guide` demos completed Stages 02–15.
  HTML/PDF content assertions checked supported/uncertain sections, before/after
  and the PDF technical appendix. Element/table payloads, legacy acceptance
  statuses and uncertainty case types match the pre-change golden baselines.
  These demos do not exercise Stage 1; synthetic outline tests cover that path.
- Both fresh saved readers passed `--phase phase2 --check-only`; hashes of all
  output files stayed unchanged. Both pre-change baseline workspaces also passed
  with unchanged file hashes and derived normalized counts.
- Actual loopback API/MCP mock reader: **12/12 scenarios passed**.
- Final demo/mock artifacts: `workspace/outline-results-final-6zu6a_hv/`.
  Each fixture's `output/` contains the human HTML/PDF reports. `mock.log` and
  `mock/report.json` retain transport check evidence.
- Lead edge probes found and regression-fixed side-local section ID collisions,
  side-specific table pages, contradictory explicit result states, nested reader
  state consistency, uncertain HTML table-cell omission, and mixed old/new values
  in the primary Before column. Partial outline roundtrips now preserve original
  source order/entry IDs. Corrections were RED/GREEN checked before the full run.
- A long-cell probe generated a 22-page PDF with its final numeric-bearing tail
  preserved; no unsplittable-table-row failure or silent truncation occurred.
- One independent reviewer inspected the coherent change and reported no
  actionable defects. Lead verification remains the acceptance evidence; optional
  redundant report-model alias was removed, while missing-destination fallback
  was retained for supported normalized/simple bookmark rows.
- Actual diff and `git diff --check` inspected. No provider calls, browser loops,
  dependency additions, commits, pushes or deployments were performed.
- Remaining acceptance: user visual feedback on the generated reports and a
  permitted real-PDF/company run. Source strikethrough stays deferred; the private
  table-extraction case and Stage 10 performance issue are not claimed repaired.

## Execution environment and record

- Branch: `feat/outline-automatic-results`, ordinary isolated Git worktree.
- Python 3.11 venv: `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/phase-testing-venv/bin/python`.
- Use the editable install bound to this worktree and `PYTHONPATH=src` when needed.
- Native worktree creation returned a generic failure; ordinary Git worktree
  creation succeeded. The original branch/workspace and untracked spec were kept.
- Capture RED/GREEN evidence, actual check counts, scope deviations and remaining
  uncertainty here. No duplicate full-suite run after every micro-fix.
