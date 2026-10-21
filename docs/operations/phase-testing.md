# Run and test by phase (developers and coding agents)

**Phase 1** is the complete 15-stage PDF comparison/reporting engine.
**Phase 2** reads its saved canonical result for questions. It does not mean
the second half of the 15 stages. Stage 13 evaluation and Phase 2 chat are
different model uses.

Use commands from the repository directory with the same installed virtualenv
interpreter. `python` below means Python >=3.10 (3.11 recommended). On Windows,
replace `python` with `.\.venv\Scripts\python.exe`; quote paths containing spaces.

## Working with a coding agent

Open the repository in your coding tool and ask it to read `AGENTS.md` first.
No special agent plugin or new model integration is required. A coding agent
helps operate and explain this project; it is distinct from the project's own
Phase 2 Q&A agent. Useful requests include:

- “Explain why Stage 5 exists and trace its inputs/outputs; do not edit code.”
- “Run the offline demo in a fresh workspace, check Phase 2 on that workspace,
  and report commands, artifacts and limitations. Do not call providers.”
- “Read workspace/EBA_STEP2 without modifying it; tell me whether Phase 2 can
  use it and what context is missing.”
- “Investigate this Stage 5 error from saved Stage 4 artifacts; distinguish
  reproduced facts from hypotheses before proposing a fix.”

Give the agent the exact workspace and desired test depth. Ask it to show the
command and obtain approval before live extraction/chat; do not paste secrets
into conversation. Have it report evidence and next actions, not just “passed”.

## 1. Choose the test depth

| Check | Data/provider use | What it proves |
| --- | --- | --- |
| Unit/contract tests | synthetic; no live providers | code behavior and interface regressions |
| Offline demo + saved reader check | bundled already-extracted DI; mock evaluation | Phase 1 stages 02–15 and local Phase 2 projections work together |
| API/MCP mock reader | synthetic; scripted model, real local transports | transport/controller contracts, not real LLM reasoning |
| Real PDFs / real chat | DI/model calls as configured | actual environment behavior; requires human output acceptance |

```bash
python -m unittest discover -s tests/phase1
python -m unittest discover -s tests/phase2
python -m unittest discover -s tests/integration
python -m unittest discover -s tests
# Focus on this defect / saved-workspace feature
python -m unittest tests.phase1.test_structure_evidence
python -m unittest tests.phase2.test_local_flow tests.phase2.test_saved_workspace
# Outline prepass, automatic uncertainty results, complete reports, reader state
python -m unittest tests.phase1.test_pdf_outline tests.phase1.test_automatic_reports
```

Private-fixture tests may skip when their inputs are absent. Report skips and
failures rather than calling them proof about private EBA documents.

## 2. Offline Phase 1, then Phase 2 on the same workspace

```bash
python -m document_compare demo --fixture internal_policy_report --root "workspace/phase demo"
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/phase demo" --check-only
```

The demo root must be fresh. Inspect `output/comparison_redline.pdf`,
`output/comparison_inline.pdf`, `output/bu_comparison_redline.html`,
`output/comparison_result.json` and the review queue. The primary HTML/PDF report
shows supported changes and uncertain differences separately, with counts,
before/after values, section/page groups and a technical details/appendix area;
it must not claim “no differences” while uncertainty or extraction limitations
remain. The check prints a bounded summary and `reader_check: passed`; it exercises
real summarize/search and a sample details call when records exist. Reader output
exposes normalized `result_state` while legacy `accepted`/`review` filters keep
working. An empty valid result reports `details_check: skipped_no_records`, not
fabricated coverage. It does not start API/MCP or make a DI/model call.

Test real API/MCP transports separately with the existing mock harness:

```bash
python -m document_compare.phase2.agent.mock_reader --out "workspace/reader smoke"
```

Use a fresh output destination. This seeds synthetic data; it does not test your
selected real workspace or actual model reasoning.

## 3. Real Phase 1 only

Copy `.env.example` to `.env` only if `.env` does not already exist. Put PDFs in
`input/` and configure locally, for example the reported EBA pair:

```dotenv
CRR_OLD_PDF="input/04_STEP2 SCT Interface Specifications V20240318.pdf"
CRR_NEW_PDF="input/17_STEP2 20240530 SCT Interface Specifications V20241125 MARKED.pdf"
CRR_PROJECT_ROOT=workspace/EBA_STEP2
CRR_RUN_LLM=mock
```

Configure `TIA_API_KEY` and `TIA_AZURE_ENDPOINT` for missing DI extraction chunks.
`mock` disables live **Stage 13** evaluation, not real extraction. Set
`CRR_RUN_LLM=real` only when approved, with the provider configuration from
`.env.example`. Internal `bbk_ai_hub_sdk` comes only from the approved company index.

```bash
python -m document_compare.phase2.workflow.local_reviewer --phase phase1 --workspace "workspace/EBA_STEP2"
```

This loads the repository `.env` without replacing existing process environment
values and runs stages 01–15, without API or chat. Explicit `--workspace` wins
over `CRR_PROJECT_ROOT`. If omitted in an explicit phase mode, the setting is
used, then `<repo>/workspace`. PDF paths in `.env` are relative to the working
directory; the workspace argument is an output/input-artifact root, not the PDF
directory. Without PDF overrides, Stage 1 expects
`<workspace>/input/document/{old,new}.pdf`.

A full canonical result already present causes this full-run entry point to
refuse overwrite. Choose a fresh workspace or intentionally replay a stage range
as below. Keep old/new orientation explicit.

## 4. Phase 2 only: select an existing workspace

```bash
# Validate the saved result before paying for chat
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/EBA_STEP2" --check-only
# Real interactive chat on that exact saved result
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/EBA_STEP2"
# A previous API-created job is also a workspace
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/jobs/JOB_ID" --check-only
```

Select the directory **containing `output/`**, not `output/` itself. This mode
uses the same bounded reader implementation as HTTP through a local read-only
gateway. It does not register/copy the workspace, submit PDFs, rerun any stage,
start API/MCP or alter its source files. It pins one in-memory result snapshot
per session; restart to load updated artifacts.

Required: readable `output/comparison_result.json` with the completed canonical
stage marker, typed change/table/review lists and statistics object. Duplicate
record identities and malformed present artifacts fail, rather than becoming a
successful empty comparison. If `job_status.json` exists, its state must be
`done`; it cannot override a failed/running job just because an old result exists.
Standalone Phase-1/demo roots do not require a job status.

Structured old/new document context and extraction validation reports are
optional; missing ones are explicitly reported as limitations. Without context,
the reader cannot invent surrounding source paragraphs. Missing rendered PDFs do
not prevent canonical reading. Stage 4-only roots cannot support Phase 2 yet.

Real saved chat requires `TIA_API_KEY`, `LLM_MODEL` and the internal SDK; it does
not need the extraction endpoint or source PDFs. It uses the existing TIA client,
not the Stage 13 `LLM_PROVIDER` selection. Approved bounded evidence may be sent
to the model, so keep the run inside the authorized environment. `--check-only`
requires none of these chat credentials. Type `quit` / `exit` to stop.

Suggested acceptance questions (also useful prompts for your coding agent):

- “Summarize changes in this workspace, distinguishing supported changes from uncertain differences.”
- “Find changes to payment limits; give old/new values, typed IDs and pages.”
- “Explain the evidence for CHANGE-… and what remains uncertain.”
- Ask about a nonexistent ID: the answer must not invent a record.

## 5. Run both phases

```bash
python -m document_compare.phase2.workflow.local_reviewer --phase both --workspace "workspace/new comparison"
```

Configure PDF paths/extraction and chat as above. This runs the engine in the
chosen workspace, then local read-only chat on the exact saved result. Stage 13
respects `CRR_RUN_LLM`; Phase 2 chat is still real. If chat configuration is
missing after Phase 1 succeeds, artifacts remain: fix configuration and use
`--phase phase2`, not another full run.

The **no-flag** command is preserved:

```bash
python -m document_compare.phase2.workflow.local_reviewer
```

It prompts for two PDFs and uses the existing loopback API + workflow MCP +
reader MCP + real chat. It creates `workspace/jobs/<job_id>/` and forces real
Stage 13; it is not saved-workspace mode. Use explicit phase/workspace flags when
you want selection and reuse, and the mock harness for credential-free transports.

## 6. Reproduce / replay the Stage 5 EBA failure

Keep the failing workspace locally. First verify it contains both
`output/old_document_normalized.json` and `output/new_document_normalized.json`
from Stage 4. Do not call extraction again merely to investigate Stage 5.

```bash
# Synthetic regressions include the exact reported ValueError
python -m unittest tests.phase1.test_structure_evidence.FurnitureTocRegressionTest
# Recompute Stage 5 only from saved Stage 4 artifacts (writes structured outputs)
python -m document_compare run --root "workspace/EBA_STEP2" --from-stage 05 --to-stage 05
# After inspecting Stage 5, intentionally regenerate downstream artifacts
python -m document_compare run --root "workspace/EBA_STEP2" --from-stage 05 --to-stage 15
```

These stage-range commands intentionally replace selected/downstream artifacts:
preserve a copy first if the original failed-run evidence is needed. The direct
pipeline CLI sets the workspace but does not load the repository `.env`; Stage 13
uses the process environment (`mock` by default). Ensure it is `mock` before
offline replay; do not inherit `CRR_RUN_LLM=real` accidentally.

Inspect Stage 5 `structure_evidence`: furniture IDs must have `layout`
membership, not be TOC sources or section headings; genuine TOC entries and
links must survive. The fix excludes derived furniture throughout TOC processing,
not just changes membership order, and keeps validation enabled.

If the workspace contains `output/old_pdf_outline.json` /
`output/new_pdf_outline.json`, check the recorded source SHA-256, physical page
count, status and diagnostics. An absent, invalid, stale or partial sidecar is a
normal fallback to reconstruction, not a reason to trust it; only entries whose
title and physical page both match a body element may appear as outline links.
Native bookmarks are not a printed contents page and do not prove hierarchy.
Outline acquisition failure must not by itself invalidate otherwise valid DI
extraction.

The private EBA PDFs/artifacts are not bundled here. Synthetic regressions prove
the failure mechanism, **not** acceptance on that pair. Record the actual command,
workspace, stage outcome and human inspection when running it in the company
environment. Never send `.env`, private PDFs, raw DI or result text outside it.
