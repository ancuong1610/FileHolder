# Semantic Confidence Gate A Implementation Plan

**Goal:** reduce unsupported residual add/delete claims and distinguish verified
correspondence from source-backed material change without model authority.

**Architecture:** one coherent writer implements bounded residual proposals,
validated deterministic witnesses, routing/canonical policy and the bounded
reader/report projection. The lead owns baseline, independent evaluation, actual
diff inspection and one final risk-focused review. No per-task reviewer loops.

**Tech stack:** existing Python >=3.10, standard library, existing engine/reader
and unittest. No new dependencies, providers or production model inference.

**Spec:** `../specs/2026-10-23-semantic-confidence-design.md`, gate A only,
approved by the owner with an instruction to execute. Gate B is not authorized.

## Global constraints

- Keep 15 stages, external input paths and source/ID/old-new conservation.
- Stage 13 remains advisory; mock/real advice cannot approve or suppress.
- Preserve existing artifact names, reader filters/kinds, bounds and saved roots.
- Five proposals per residual element; 1,000 additional pair checks per job.
  Deterministic order, visible budget exhaustion and unsearched coverage.
- No Stage 10 matching/performance policy change, Docling production replacement,
  source-strikethrough work, models, services, paid calls, commits or pushes.
- No global similarity-threshold reduction, synonym list trained on examples,
  automatic acceptance of every digit/NOT edit, or invented confidence percent.
- Source-bound complete witnesses may decide; ambiguity/truncation/conflict abstains.
- Existing 72 cases are development/regression evidence, not an unseen holdout.
- Freeze the new independently authored holdout before production edits. Do not
  expose its clauses/labels to the implementation worker. Its independent labels
  remain AI-authored, not legal-expert truth; report that limitation.

## Review focus

1. Duplicate/repeated clauses and same titles across different sections must not
   create or approve fabricated correspondence.
2. Quote/exception/scope/operator/unit changes must not be hidden by an apparently
   simple value or negation witness.
3. Hash/side/offset/context mismatches and model-shaped forged assessments must
   not reach deterministic supported/suppressed resolution.
4. Retrieval budget exhaustion and unavailable anchors/extraction must not imply
   counterpart absence or discard residuals.
5. Legacy saved roots must remain read-only; projections must not claim semantic
   verification for old records or bypass reader byte/pagination limits.

## Environment and baseline

- Existing isolated linked worktree retained; branch `feat/semantic-confidence`,
  baseline commit `ba4a2d0`.
- Python: `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/semantic-gateA-venv/bin/python`.
- Prior temporary venv was damaged (missing configuration/package files). A new
  venv installs existing `requirements.txt` and the editable project only. No
  product dependency or source change was used to repair the environment.
- Fresh baseline: **695 tests OK, 11 skipped**. Log:
  `/private/var/folders/jf/myvcgn3s5fjcgv323wsnz1c80000gn/T/opencode/semantic-gateA-ready-baseline.log`.

## Milestones (one writer, sequential interfaces)

### 0. Freeze independent final input

- [x] Independent author freezes fresh PDF/block cases and labels outside the
  repository; author reports only root/hash/counts and self-audit results.
- [x] Lead records freeze identity without reading gold before feature freeze.
  Unsupported/insufficient-context cases and errors stay in coverage accounting.

Freeze evidence: 48 pairs (32 material positives, 16 equivalent controls), 96
single-page PDFs and at least six uncertainty-marked cases. The independent
author self-audited labels and PDF/source correspondence; lead verified all 98
file hashes without reading manifest cases. Manifest filename is `manifest.json`,
SHA-256 `af6df50e43db51d2f7070d314c2589351be93f91a0f1bd3fa7799505ac5b6633`.
The sealed path is retained by the lead outside implementation prompts. Labels
are AI-authored controlled interpretations, not expert-certified natural revisions.

### 1. Deterministic evidence and bounded proposals

Files: add `phase1/contracts/semantic_assessment.py` for source validation and
normalization; add small `phase1/matching/semantic_evidence.py` and
`phase1/matching/residual_proposals.py` only if responsibilities need separation.
Integrate with `stages/s07_modified_matching.py`, `s08_candidates.py` and
`s09_movement.py`; do not broadly refactor these stages.

Interfaces: pure assessment helpers consume actual old/new source elements,
matching/extraction evidence and full raw text. Produce optional versioned
`semantic_assessment` as specified: correspondence/relation/basis, IDs, source
hashes and exact spans, policy version, guard results and limitations. Pure
proposal retrieval consumes residual elements plus existing exact-match/context
evidence; returns bounded proposals and explicit examined/unsearched coverage.
Proposals never consume elements or become committed matches merely by existing.

- [ ] RED: `tests.phase1.test_semantic_assessment` proves legacy not-assessed;
  hash/side/offset rejection; no mutation; no model/mock approval; complete versus
  quoted/conditional/exception clauses; numeric formatting/unit/operator/date
  ambiguity; explicit polarity and obligation witnesses with counterexamples.
- [ ] RED: `tests.phase1.test_semantic_residuals` proves deterministic top-five,
  1,000-check bound, exhaustion, unrelated sections, repeated clauses, absent or
  partial structure, reciprocal conflicts and conservation of every residual.
- [ ] Implement narrow helpers and propagate evidence through existing producers.
  Inspect emitted Stage 07/08/09 records, not just final status.
- [ ] GREEN: `python -m unittest tests.phase1.test_semantic_assessment tests.phase1.test_semantic_residuals tests.phase1.test_matching_stage_module tests.phase1.test_reconcile tests.phase1.test_movement_stage_module`.

### 2. Routing and canonical source-proof policy

Files: `s11_routing.py`, `s14_canonical_result.py`; adjust the existing pure
result-state/review identity helpers only for necessary group deduplication.
Stage 12 packet propagation may be additive if required; Stage 13 outcomes and
authority remain unchanged.

- [ ] RED: reproduce the exposed equivalent controls as unsafe independent
  accepted delete/add; reproduce the short polarity pair as uncertain.
- [ ] Unresolved plausible counterparts route to `UNCERTAIN_RESULT`, preserving
  original candidates and source refs. Unsearched coverage cannot accept absence.
- [ ] Only verified correspondence + valid complete deterministic witness may
  override a short-text-only review reason. Extraction/duplicate/scope/conflict
  guards dominate; other review reasons cannot be cleared opportunistically.
- [ ] Stage 14 independently revalidates the witness against actual source data;
  a route/boolean/serialized assessment alone is not authority. Stale, forged,
  incomplete and model-advisory evidence remains uncertain.
- [ ] Deduplicate a proposal group's user-facing uncertainty explanation without
  deleting its candidate records, source membership or typed references. Use
  one canonical identity, not concatenated old/new queue aliases.
- [ ] GREEN: `python -m unittest tests.phase1.test_semantic_assessment tests.phase1.test_semantic_residuals tests.phase1.test_route_identity tests.phase1.test_generic_only tests.phase1.test_hyphen_wrap_output tests.phase1.test_evaluation_metrics`.

### 3. Bounded projection, reporting and documentation

Files: `phase1/reporting/report_model.py`, HTML/PDF renderers only where assessment
presentation needs it; `phase2/reader/result_reader.py`, API summary and agent
wording only as necessary. Existing accepted-only index stays accepted-only.

- [ ] RED: legacy statuses stay readable but semantic basis is `not_assessed`;
  source-supported versus material-change assessment is explicit, not invented.
  Same local old/new IDs, uncertain proposal groups, long text and table refs
  remain correctly bounded and oriented. Saved input hashes must not change.
- [ ] Show why a case is supported/uncertain and its source witness; keep raw
  evidence available and technical detail secondary. Evidence-only proposals
  must not render invented before/after pairs. No visual/browser loop.
- [ ] Update active README, architecture, matching/result/reader contracts,
  Knowledge Transfer and relevant owner READMEs with actual implementation.
  Update intentional source hash pins only with rationale and unchanged safety
  invariants; never weaken output/source-fidelity assertions to make tests green.
- [ ] GREEN: `python -m unittest tests.phase1.test_semantic_assessment tests.phase1.test_automatic_reports tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf tests.phase1.test_diff_annotations tests.phase2.test_reader tests.phase2.test_agent_reader tests.phase2.test_saved_workspace tests.integration.test_architecture`.

### 4. Final lead verification and acceptance evidence

- [ ] Inspect actual tracked and new-file diff; `git diff --check`.
- [ ] Run `python -m unittest discover -s tests` once at the coherent boundary.
- [ ] Fresh bundled demo + exact output assertions + saved reader `--check-only`;
  fresh actual API/MCP mock: `python -m document_compare.phase2.agent.mock_reader --out FRESH_ROOT`.
- [ ] Rerun the fixed 72-case development corpus through unchanged actual saved
  Docling carriers, scoring both baseline and candidate by the same definitions.
  Preserve all uncertain, failed and unpaired cases. Do not tune to holdout.
- [ ] Freeze candidate source identity, then unseal the new evaluation corpus
  and run baseline and candidate on identical carriers. Report event recall,
  supported-record precision, candidate paired recall, coverage, extra records,
  same-meaning false alarms, runtime and descriptive intervals. No promised pass.
- [ ] One coherent independent review for source binding, deterministic authority,
  conservation, suppression/abstention and persistence/reader compatibility.
  Correct concrete findings with focused regressions; no preference loops.
- [ ] Final report changed files/checks, baseline-versus-candidate metrics, any
  failed gates/limits and a user-visible generated report. No commit/push.

## Stop and correction boundaries

## Completion evidence (2026-10-23)

Milestones 1–4 were executed; the original per-check boxes above are retained as
the prescribed checklist, not a claim that every individual RED assertion was
captured separately. The two new modules were first tested RED (missing imports);
the lead also captured eight independent safety probes RED before correction.
Final tracked regressions cover source/ID/span forgery, whole-modal force,
numeric-boundary forgery, finite margins, incomplete context, actual budget work,
unindexed coverage, missing-side projection and summary/details consistency.

- Full suite: **762 tests OK, 11 optional skips**; final log
  `semantic-gateA-final-full.log` under the approved temporary evidence root.
- Two fresh offline demos and hash-stable saved-reader checks; API/MCP mock 12/12.
- Independent core review plus lead's remaining 13 short-route/rendering checks.
  Review coverage was limited and does not establish universal semantic accuracy.
- Source freeze before opening final labels:
  `f105c37ca45fcaf3f4a5fe7638b4b1ea7ce99cc16317452546aa07047de42f01`.
  No engine/scorer tuning followed unsealing. The original manifest lacked only
  `sources` metadata; a transparent `sources=[]` wrapper preserves every label,
  case and PDF byte. Original freeze/manifests remain available.
- Exposed 72 development pairs: recall 22/48 → 27/48 (45.8% → 56.2%),
  supported-record precision 22/36 → 27/27 (61.1% → 100%). Same-meaning false
  alarms 5/24 → 0/24. Actual saved Docling carriers were reused identically.
- Fresh 48 holdout pairs: recall 18/32 → 18/32 (56.2% unchanged), precision
  18/24 → 18/18 (75% → 100%). False alarms 1/16 → 0/16. Holdout inputs use
  identical ideal authored-block carriers, not new Docling/provider extraction.
- **Overall 80%/95% gate fails on recall**. Holdout precision's Wilson 95%
  interval is 82.4–100%; do not claim >=95% population precision.
- Six context-sensitive notes are not six gold-undetermined labels: all carry
  material labels under supplied context. Final gold has no undetermined class;
  report this abstention-evaluation gap. Both corpora are AI-authored controls.
- English README/architecture/matching/reader/Knowledge Transfer and owner notes
  synchronized. No source-strikethrough, Stage-10 policy, dependency, model,
  provider call, commit or push added.

Full local report: `workspace/semantic-gateA-20261023/REPORT.md`; machine counts,
intervals, uncertainty and runtime: `final-comparison.json` in that directory.
Gate A implementation is delivered; general semantic readiness is **not**.
Gate B remains separately unauthorized.

### Stop and correction rules

Missing private documents does not block deterministic implementation but blocks
private acceptance claims. Stop for any need to change evaluator authority,
install a product model/dependency, make an external inference call, break public
contracts or introduce broad Stage 10 changes. Two failed corrections at the same
defect require a changed diagnosis, not another identical loop. A failed target
is reported honestly; it is never repaired by relabeling the final gold.
