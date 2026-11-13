# Table comparison fix — controlled execution plan

Status: RECOVERY / T0 ACTIVE. T1–T5 are not released.
Prepared: 2026-10-30.
Evidence basis: coding agent's supplied interruption report, not an independently inspected working tree.

## 1. Ownership and execution rules

The user and planning lead own scope, design choices, acceptance criteria, and task release. The work-machine coding agent implements the active task and supplies evidence. It may make routine implementation choices within that task; it must not expand scope or activate later tasks itself.

- Execute only the task explicitly marked ACTIVE and released in the latest instruction.
- Read applicable AGENTS.md instructions. Preserve unrelated work.
- Finish routine inspection, edits, and focused checks for the active task without repeatedly asking for confirmation.
- At its completion, update the execution ledger and return the handoff. Stop before the next task. This boundary is an explicit requirement of this plan.
- If blocked, preserve progress and report the exact command/error and the smallest proposed next action. Never label a blocked test as passed.
- Do not reset the repository, discard whole files, overwrite saved job artifacts, or run broad automated rewrites.
- Do not commit, merge, push, deploy, or invoke paid extraction/model services under this plan. Those actions need a separate instruction.
- Do not add parallel agents, new architecture, or optional improvements.

## 2. Goal and fixed product decisions

Improve general-PDF table comparison by reducing incorrect continuation joins and expensive full-content comparison of unrelated tables.

Approved direction:

- A: stronger local continuation decisions.
- B: lightweight identity evidence from existing structured data.
- C: candidate retrieval before expensive fuzzy scoring.
- Section context and geometry support these decisions; neither is a mandatory identity key.

Keep the existing row/cell comparison, artifact contracts, and automatic result production. Missing captions, moved tables, changed pagination, or changed column counts must not alone rule out a counterpart.

Ambiguity is a completed uncertain output. There is no human table-selection, approval, or correction workflow. The user-facing agent explains saved results and cannot alter them. Existing review_queue terminology represents recorded uncertainty. Malformed inputs may still fail according to existing contracts; automatic completion does not mean swallowing contract errors.

Success requires both demonstrated reduction in expensive work and preservation of supported matching decisions. A lower runtime alone is insufficient.

## 3. Reported current state — VERIFY IN T0

| File | Reported state | Current trust level |
| --- | --- | --- |
| .env.example | Pre-existing user change; untouched by agent | Preserve; verify only metadata/diff scope, never print secrets |
| src/document_compare/phase1/stages/s10_tables.py | Continuation context, evidence audit, indexed retrieval, caps added | Incomplete and untested |
| src/document_compare/phase1/stages/s11_routing.py | Equivalent tables with review evidence route uncertain | Regression written; not executed |
| src/document_compare/phase1/stages/s14_canonical_result.py | Uncertain equivalent tables retained | Regression written; not executed |
| tests/phase1/test_automatic_reports.py | Routing/canonical regression added | Not executed |

Reported checks: stage modules compile; diff whitespace warning is in unrelated .env.example. Compilation does not validate matching or downstream behavior.

Reported retrieval settings: 12 candidates per table, 12 per counterpart, 3 feature-poor fallback candidates. These are implementation heuristics, not approved correctness guarantees.

Reported gap: main reads incomplete_old_ids / incomplete_new_ids but does not use them to prevent unsupported one-sided outcomes.

Reported environment issues: Stage 10 tests need PYTHONPATH=src; report tests import unavailable pymupdf in the selected interpreter. Verify the repository's supported interpreter before treating this as a dependency defect.

The cause of the interruption is unknown. Do not infer that it was caused by algorithm complexity.

## 4. Scope and preservation rules

Expected production owners: s10_tables.py, s11_routing.py, s14_canonical_result.py. Focused existing tests and a concise subsystem documentation note are permitted when their task is released. Verify actual paths first.

Preserve raw text, OLD/NEW orientation, source table/cell IDs, spans, polygons, pages, fragment provenance, repeated-header accounting, exactly-once conservation, detailed confident-pair comparison, one-sided table content, reports, and readers.

No OCR/provider changes, general caption parser, embeddings, LLM matcher, new identity schema, API/UI work, manual review feature, text-matching redesign, or new dependency. Do not change extraction or silently truncate source text to obtain speed.

If a shared helper or contract change outside these boundaries is necessary, report the concrete dependency and minimal proposed diff before making it.

## 5. Ordered tasks

### T0 — Recover and verify the working state [ACTIVE]

Purpose: establish exactly what exists before further implementation.

Actions:

1. Read instructions and record branch, HEAD, status, and relevant diff summary. Inspect current stage/test diffs. Do not print .env values.
2. Save a recoverable patch of agent-owned changes using explicit file paths. Preserve it locally outside tracked source; record its path. Do not include unrelated .env changes or confidential job data.
3. Locate the interruption traceback/log if available. If unavailable, state that the cause is unknown.
4. Map the current call path: physical tables → continuation grouping → exact matching → retrieval → fuzzy assignment → unmatched handling → Stage 11 → Stage 14. Give actual function names and line references.
5. Confirm where incomplete-search metadata is produced, propagated, and ignored. Inspect both one-sided classification and acceptance/ambiguity checks.
6. Identify the supported interpreter and focused test commands from project instructions/configuration. Check required imports without changing dependencies.
7. Locate an available saved failing job, if present, and determine whether it contains usable pre-Stage-10 artifacts. Do not rerun extraction. If absent, state it.

Allowed writes: this task file's ledger and an untracked recovery patch only. No production/test changes.

Deliverable: short state report, diff summary, recovery patch path, exact validation commands, incomplete-search call path, and any proposed adjustment to T1–T5 based on actual code.

Done when: each reported modification is verified or corrected, the unfinished boundary is identified, and work is recoverable. Stop for lead review.

### T1 — Validate the routing/canonical correction [NOT RELEASED]

Purpose: accept or correct the isolated uncertainty-preservation behavior.

Allowed edits: s11_routing.py, s14_canonical_result.py, focused routing/report tests. Do not edit Stage 10 in this task.

Actions:

1. Use the supported interpreter and test configuration identified in T0.
2. Exercise both UNCHANGED and LAYOUT_EQUIVALENT, each with clean evidence and valid review evidence. Inspect semantics of review reasons before treating any nonempty metadata as uncertainty.
3. Verify Stage 11 routes and Stage 14 canonical output together. Verify clean equivalents retain existing behavior and uncertain equivalents retain reasons and source evidence.
4. Run the existing relevant report tests. Make only corrections required for this behavior.

Done when: focused tests pass and no existing equivalent-table behavior is unintentionally changed. If the environment prevents execution, mark BLOCKED; do not substitute py_compile as acceptance. Stop with diff and test evidence.

### T2 — Validate and finish continuation checks [NOT RELEASED]

Purpose: prevent demonstrated false joins using the already-added local evidence helper.

Allowed edits: Stage 10 continuation helpers and focused tests. Leave retrieval unchanged.

Actions:

1. Inspect current local label/heading association. Ensure nearby evidence is attached to the correct physical fragment and cannot cross unrelated tables indiscriminately.
2. Test separate adjacent-page tables with identical headers and distinct labels/boundaries. They must remain separate.
3. Test a valid multi-page continuation, including repeated header omission and conservation.
4. Test absent caption/context/geometry. Preserve a documented existing fallback; never require every feature or crash on absence.
5. Correct only demonstrated defects in existing checks. Keep page/shape/header evidence; use reliable boundary contradictions to reject false joins.
6. Check audit output is additive/compatible and bounded to relevant local evidence. Avoid recording unnecessary table text.

Done when: fixtures establish both rejected false joins and retained true continuations, with table/cell accounting intact. Do not claim this proves the private case without its artifacts. Stop with evidence.

### T3 — Finish retrieval and uncertainty handling [NOT RELEASED]

Purpose: make the already-started candidate retrieval safe and bounded.

Allowed edits: Stage 10 retrieval, matching, unmatched handling, focused tests. Any necessary downstream integration change requires a concrete scope proposal first.

Actions:

1. Reuse the current indexes/features; do not introduce another retrieval architecture. Verify representations are computed once per table and rankings are not repeatedly rebuilt.
2. Retain the exact-match path and its duplicate/ambiguity safeguards.
3. Identify search coverage separately from scores. Trace caps on both OLD and NEW sides, including fallback retrieval and candidates dropped by counterpart limits.
4. Distinguish an exhaustive search with no supported counterpart from restricted search. Zero retrieved candidates does not alone prove addition/deletion.
5. Propagate affected residual IDs and available candidate relationships through existing unresolved-result mechanisms. Ensure each table is consumed exactly once and is not also emitted as a one-sided candidate.
6. Check whether truncation can hide a plausible rival and artificially improve the best/second-best margin. Do not retain a confident pair solely because its competitor was omitted. Equally, a cap elsewhere must not turn all independent, supported matches uncertain. Document the specific acceptance policy supported by the current signals.
7. Keep unresolved grouping specific to affected relationships where the current contract allows it. Do not add a new graph subsystem. If the contract cannot safely represent the case, report the smallest integration proposal.
8. Measure retrieval construction as well as expensive scoring. Bounded scoring must not conceal repeated full-text all-pairs work in retrieval.

Required focused cases:

- A moved table with changed page/context remains retrievable through other evidence.
- A modified table with a changed column count remains retrievable and comparable.
- Duplicate/similar candidates remain uncertain when evidence cannot distinguish them.
- The intended counterpart or plausible rival is excluded by a cap: no false confidence or unsupported independent addition/deletion.
- OLD-side and NEW-side caps, plus feature-poor fallback, are exercised.
- A clean genuinely one-sided table under adequately covered search retains supported existing behavior.
- Many changed tables with repeated headers exercise fuzzy retrieval, not only exact matching.

Done when: restricted search is explicitly represented, residual handling is integrated, confidence is not manufactured by truncation, and expensive scoring is demonstrably below the full cross-product on the table-heavy fixture. Stop with work counts and tests.

If finishing this task requires broader design: preserve the unfinished patch and propose isolating only retrieval changes while restoring the prior path. Do not silently drop A+B+C or perform a whole-file revert that removes validated continuation work. This fallback requires lead decision.

### T4 — Integrated correctness and performance checks [NOT RELEASED]

Purpose: establish whether the combined fix meets the goal.

Actions:

1. Run focused Stage 10, routing, canonical, report, and conservation checks in the supported environment.
2. Compare the interrupted-work baseline's original predecessor with the completed implementation using the same pre-Stage-10 artifacts and configuration. Use isolated output directories. Distinguish pre-fix baseline from interrupted state.
3. Prefer a saved failing job. Otherwise use labeled synthetic fixtures and explicitly limit the conclusion.
4. Include numeric/selection-state changes, malformed-cell checks, and one-sided content preservation through existing tests where available.
5. Inspect report/reader output for uncertain tables and clean equivalents. Compilation alone is insufficient.

Required comparison table:

| Measurement | Pre-fix | Completed fix | Interpretation |
| --- | --- | --- | --- |
| Physical/logical tables and largest logical group | | | |
| Continuation joins and known wrong joins | | | |
| Residual OLD×NEW possible pairs | | | |
| Pairs actually receiving expensive similarity | | | |
| Candidate limits / affected residual IDs | | | |
| Correct pairs / uncertain / one-sided outcomes | | | |
| Grouping, retrieval/scoring, row/cell, total time | | | |

Use deterministic counts for assertions. Wall-clock time is supporting evidence. Report regressions or increased uncertainty honestly. No universal runtime or accuracy target is invented here.

Done when: no unexplained correctness regression, conservation and consumers pass, expensive work is reduced on the fuzzy-path fixture, and real-case status is accurately stated. Stop with report.

### T5 — Final handoff [NOT RELEASED]

Actions: update only concise relevant documentation; record final changed files/functions, test commands/results, measurements, behavior changes, remaining limitations, and unresolved blockers. Update the ledger. Do not publish or merge.

Done when: the lead can review a concrete diff and evidence. Use READY FOR REVIEW, not “universally fixed.” A private-PDF fix can only be claimed if that case was rerun and inspected.

## 6. Handoff format after every active task

Return:

1. Task ID and outcome: DONE / BLOCKED / NEEDS DESIGN DECISION.
2. Verified facts, separated from remaining hypotheses.
3. Files/functions changed and brief reason; or “no code changed.”
4. Exact checks run, interpreter, pass/fail counts, and relevant error excerpts.
5. Artifact/patch paths and work counts when applicable.
6. Remaining risk and smallest proposed next step. Do not start it.

Do not repeatedly retry the same failing environment command or broaden tests without a new reason. If interrupted again, leave the task in IN PROGRESS/BLOCKED with completed and remaining actions recorded.

## 7. Execution ledger

Do not mark a task complete based on this planning document or the previous agent report.

| Task | Release | Execution state | Evidence / next action |
| --- | --- | --- | --- |
| T0 | ACTIVE | DONE | Verified on `refactor-structure` at `3272a1e5a5ea01ef184b4786c7ab8659a598074a`. Preserved pre-existing `.env.example` (3 added/3 removed; values not inspected). Recovery patch: `c:\Workspace\Python_Runtime\Work\table-comparison-t0-recovery-20261030.patch` (25,597 bytes), reverse-apply check passed. Supported interpreter: `.venv\Scripts\python.exe` / Python 3.11.7; editable package and PyMuPDF imports passed; no behavioral tests run. Metadata-only workspace inspection found `workspace\offline-demo\output` and `workspace\p1-test\output`, each with pre-Stage-10 inputs and later table/routing/result artifacts; neither is proven to be the interrupted failing job. No relevant log/traceback was found in safe workspace or repository metadata scans, so interruption cause is unknown. Verified gap: `s10_tables.py` creates incomplete-search ID sets at lines 1815–1820 but does not use them in unresolved grouping or one-sided candidate filtering (lines 1840–1888). T1: run focused routing/canonical tests. T2: add continuation regressions. T3: propagate restricted-search residuals into unresolved handling and test both OLD/NEW caps. |
| T1 | RELEASED | DONE | Used `.venv\Scripts\python.exe` (Python 3.11.7) and the configured fully-qualified unittest invocation. Expanded `RoutingSemanticsTest.test_equivalent_tables_keep_clean_non_semantic_and_review_evidence_uncertain` to cover `UNCHANGED` and `LAYOUT_EQUIVALENT`, each clean and with valid review evidence. Stage 11 keeps clean equivalents `NON_SEMANTIC` and routes evidence-bearing equivalents as `UNCERTAIN_RESULT` / `table_non_semantic_with_review_evidence`; Stage 14 omits the clean equivalents and retains the uncertain table changes, their original reasons, OLD/NEW logical-table references, and linked review entries. Focused test passed (1); `tests.phase1.test_automatic_reports` passed (25); `tests.phase1.test_route_identity` passed (6); agent-owned `git diff --check` passed. No production correction was required beyond the pre-existing T1 changes; focused test was corrected/expanded because it incorrectly expected only two total review entries despite the shared movement fixture contributing two structural reviews. T2–T5 remain unreleased. |
| T2 | RELEASED | DONE | Created checkpoint `c:\\Workspace\\Python_Runtime\\Work\\table-comparison-t2-checkpoint-20261030.patch` (27,643 bytes; reverse-check passed). Added synthetic continuation regressions proving direct-neighbor local evidence does not skip over intervening elements; adjacent-page same-header tables with contradictory labels remain separate; genuine repeated-header continuations join with source-table/cell accounting and compatible audit fields; and missing caption/context/geometry uses the strong-header fallback safely. `.venv\\Scripts\\python.exe -m unittest tests.phase1.test_table_stage_module -v` passed 20 tests (1 skipped only because private `output_sample` fixtures are unavailable); scoped `git diff --check` passed. No real/private saved job was run, so validation is synthetic only. No Stage 10 production correction was demonstrated. T3–T5 remain unreleased. |
| T3 | RELEASED | DONE | Created and reverse-checked checkpoint `c:\\Workspace\\Python_Runtime\\Work\\table-comparison-t3-checkpoint-20261030.patch` (34,896 bytes; excludes `.env.example`). Stage 10 now records each cap-excluded OLD/NEW plausible pair with retrieval evidence and exclusion reason, marks both affected endpoints as incomplete, blocks fuzzy acceptance whenever either endpoint had incomplete candidate coverage, and groups unmatched affected components as `incomplete_bounded_candidate_search_requires_review` with compact source references and preserved exclusion evidence. Those IDs are removed from one-sided add/delete candidates exactly once. Extracted `incomplete_search_unresolved_groups()` for focused synthetic testing; continuation and routing/canonical code were not altered in T3. `.venv\\Scripts\\python.exe -m unittest tests.phase1.test_table_stage_module -v` passed 27 tests and skipped 1 private-fixture-dependent test; bounded-only run passed 7 tests. `.venv\\Scripts\\python.exe -m unittest tests.phase1.test_automatic_reports tests.phase1.test_route_identity -v` passed 31 tests. Scoped `git diff --check` passed (only CRLF conversion warnings). Synthetic repeated-header fuzzy case: 169 possible residual pairs, 144 expensive similarity calls, 25 pruned, 0 fuzzy matches; a patched cap test proves an otherwise supported retained fuzzy pair remains unresolved when a plausible rival is omitted. Tests also cover OLD-side cap, NEW-side counterpart cap, feature-poor fallback, moved/changed-column table, adequately covered one-sided table, and unresolved one-sided exclusion. No real/private artifacts, extraction, full pipeline, or provider call was run, so real-document behavior remains unverified. Documentation impact: stage behavior changed, but T3 scope allowed only `task.md`; assess Architecture/Knowledge Transfer documentation in a later explicitly released documentation/review task. T4–T5 remain unreleased. |
| T4 | DONE | Labelled synthetic OLD→NEW fixtures: ambiguous 13×13 repeated-header set yielded 169 possible / 144 scored / 0 accepted / 13+13 uncertain; distinguishable repeated-header set yielded 13 supported pairs and no uncertainty. HEAD pre-fix baseline made 169 expensive calls vs 144 completed; Stage 10 entrypoint→11→14 preserved one incomplete-search group, source evidence and uncertainty without one-sided duplication. Stage-10 31 passed/1 expected private-fixture skip; routing/report 31 passed; performance conservation 59 passed; reader checks 95 passed with 1 Windows symlink-privilege error. No original failing job identified; no provider, extraction, or full pipeline. T5 remains unreleased. | Integrated evidence and performance |
| T5 | RELEASED | DONE | READY FOR CODE REVIEW — synthetic validation complete; original failing PDF unverified. Final agent-owned diff, including tests, is retained; `docs/architecture.md` records bounded fuzzy retrieval and uncertainty preservation. Reported suites total 216 passed, 1 skipped (private fixtures unavailable), and 1 Windows symlink-privilege environment error. The distinguishable synthetic fixture has no pre-fix baseline, and no complete Stage 10 total-time baseline-vs-current comparison exists; no general speedup is claimed. No commit, merge, deploy, or issue resolution. |

## 8. Initial instruction to the coding agent

Read this task.md and applicable repository instructions. Execute T0 only. Verify the supplied interruption report against the current working tree, preserve the agent-owned diff, identify the unfinished boundary and supported test environment, and update the ledger. Do not edit production code or tests. Return the T0 handoff and stop. T1–T5 require a later release from the user/planning lead.
