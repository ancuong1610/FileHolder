<<<<<<< README.md
# Document Comparison Service

Compare an **old** and a **new** version of the same PDF. The project creates one
saved result containing supported changes, uncertain differences with reasons,
and extraction limitations. It is a local, two-version comparison tool.

## Start here

Use the following order when you are new to the project:

1. Install the project into a Python virtual environment.
2. Run the offline demo to check that the comparison engine and reports work.
3. Run the full test suite before changing code, or focused tests while working
   on one area.
4. Use real PDFs only after confirming the configured data-provider and model
   access are approved for those documents.

For the 15-stage engine and repository layout, see
[architecture](docs/architecture.md). For task-to-file ownership and focused
tests, see the [review guide](docs/review-guide.md). For detailed operational
instructions, provider use, and acceptance limits, see
[phase testing](docs/operations/phase-testing.md). Coding agents should read
[AGENTS.md](AGENTS.md) first.

## Choose a command

Run commands from the repository root using the same Python interpreter that
installed the project. On Windows, replace `python` with
`.\\.venv\\Scripts\\python.exe`. Use a **fresh workspace** for every new demo or
comparison; generated `workspace/` content is not source code.

### 1. Install the project

Use this once per virtual environment, and again after dependency or packaging
changes. It makes `document_compare` importable for commands and subprocesses.

```bash
python -m pip install -e .
```

If installation fails in the company Windows environment because of the
setuptools/distutils shim, use the approved environment-specific workaround:

```bash
SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install --no-deps --no-build-isolation -e .
```

Do not disable TLS verification or organizational security policies. The internal
`bbk_ai_hub_sdk` is intentionally not installed by this repository; install it
only from the approved company index when real Stage 13 or saved-result chat
requires it.

### 2. Verify the engine offline — recommended first run

Run this when you want a quick, safe confirmation that Phase 1 can produce a
complete result and reports. It uses bundled synthetic extraction data, runs
stages 02–15 with mock evaluation, and makes no provider, network, or PDF-upload
call.

```bash
python -m document_compare demo --fixture internal_policy_report --root "workspace/demo run"
```

The destination must be new. Afterwards inspect the `output/` directory,
particularly `comparison_result.json`, `comparison_redline.pdf`,
`comparison_inline.pdf`, and `bu_comparison_redline.html`.

### 3. Test the codebase

Run the complete suite before merging broad changes. A failure means an assertion
did not match; an error means a test could not complete; a skip means a test was
intentionally not run (for example, a private fixture is unavailable).

```bash
# All tests
python -m unittest discover -s tests

# Tests by area
python -m unittest discover -s tests/phase1
python -m unittest discover -s tests/phase2
python -m unittest discover -s tests/integration

# Focused examples
python -m unittest tests.phase1.test_reconcile
python -m unittest tests.phase2.test_reader
```

Synthetic tests prove code and contract behavior, not universal comparison
accuracy or acceptance for private documents.

### 4. Run Phase 1 on real PDFs

Use this only when the PDFs and any configured extraction provider are approved
for use. Phase 1 is all 15 comparison and reporting stages. Set `CRR_OLD_PDF`
and `CRR_NEW_PDF` to the old and new document, then choose a fresh workspace.

```bash
python -m document_compare.phase2.workflow.local_reviewer --phase phase1 --workspace JOB_ROOT
```

This explicit mode reads the repository `.env`; `--workspace` overrides
`CRR_PROJECT_ROOT`. With `CRR_RUN_LLM=mock`, Stage 13 does not call a real LLM,
but Stage 1 may still use configured document extraction for PDFs without saved
artifacts. See [phase testing](docs/operations/phase-testing.md#3-real-phase-1-only)
for configuration and data-handling requirements.

Use the direct pipeline command only to replay a saved stage range. It does not
load `.env` for you and can replace downstream artifacts:

```bash
python -m document_compare run --root JOB_ROOT --from-stage 02 --to-stage 15
```

### 5. Read an existing comparison result (Phase 2)

Use Phase 2 after Phase 1 has completed. It reads the selected workspace in
place—it does not rerun the comparison or alter source PDFs. Pass the directory
that contains `output/`, not `output/` itself.

```bash
# Offline structural/readability check; no chat model call
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace JOB_ROOT --check-only

# Interactive saved-result chat; requires approved chat configuration
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace JOB_ROOT
```

Each chat question is bounded to at most ten planner/tool turns over the saved
reader result. The limit prevents repeated retrieval loops; a question that does
not reach a final answer within that budget reports an error rather than making
unbounded model calls.

### 6. Run comparison, then chat

Use this only when both real PDF extraction and interactive chat are approved.
The workspace must be fresh. If Phase 1 succeeds but chat setup fails, keep the
artifacts and later use `--phase phase2`; do not rerun the full comparison.

```bash
python -m document_compare.phase2.workflow.local_reviewer --phase both --workspace FRESH_JOB_ROOT
```

### Review in the browser (Phase 2 UI)

A Streamlit page that is a client of the loopback HTTP API: upload an old and a
new PDF, follow progress, open a finished job by ID, browse the findings as a
section tree (filter by type and kind), step through them with the old and new
pages side by side and the changed region outlined, and ask the comparison agent
about the saved result from a sidebar tab. It only reads saved results.

```bash
python -m pip install -e ".[ui]"
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m streamlit run src/document_compare/phase2/ui/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Run the last two commands in separate terminals. Loopback binding and disabled
telemetry are set explicitly on the command line so they do not depend on the
working directory; the checked-in `.streamlit/config.toml` still provides the
50 MB upload cap. The UI reaches the API at `CRR_API_BASE` (default
`http://127.0.0.1:8321`).

- Uploading two real PDFs runs real Stage 1 extraction through the configured
  provider; use it only when that is approved.
- Chat works only through the internal hub client and is available when
  `TIA_API_KEY` and `LLM_MODEL` are set. Otherwise the panel says chat is not
  configured and the rest of the page keeps working. A chat question sends
  bounded reader output to the configured model.
- Review-queue items have no page location; they are shown as text only. When a
  job's source PDFs are missing, pages are replaced by the old/new text.
- Offline check without credentials: seed a synthetic job with
  `python -m document_compare.phase2.viewer.synthetic --jobs-root workspace/jobs`
  and open the job ID it prints last. See
  [phase testing](docs/operations/phase-testing.md).
- Status: verified offline only. A manual browser check on a seeded synthetic
  job (2026-11-11) showed highlights on the changed text for a modified and a
  deleted finding. Highlight accuracy on real PDFs (rotation, crop boxes) and
  live chat are not verified.

Endpoints behind it: [view endpoints](docs/contracts/view-endpoints.md).

### Advanced local interfaces

These commands are for API/MCP transport development and diagnostics, rather
than the normal first-run path:

```bash
# Seed a synthetic reader workspace for transport checks
python -m document_compare.phase2.agent.mock_reader --out workspace/reader-mock

# Start local MCP servers
python -m document_compare.phase2.mcp.workflow_server
python -m document_compare.phase2.mcp.reader_server

# Start the loopback HTTP API
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321

# Legacy combined interactive API/MCP/chat flow; prompts for PDFs and uses real Stage 13
python -m document_compare.phase2.workflow.local_reviewer
```

The local services remain loopback-only. The no-flag reviewer command is not a
replacement for the explicit Phase 1/Phase 2 workflow above.

## Old → new commands (no compatibility shim)

The old Python namespaces and CLI entry points are intentionally removed. There
is no `compat/` package, no legacy wrappers or aliases, and no duplicate
implementations. Replace old invocations:

| Old | New |
| --- | --- |
| `python run_local.py` | `python -m document_compare.phase2.workflow.local_reviewer` |
| `python -m crr_compare demo …` | `python -m document_compare demo …` |
| `python -m crr_compare eval …` | `python -m document_compare eval …` |
| `python -m crr_compare.mcp_server` | `python -m document_compare.phase2.mcp.workflow_server` |
| `python -m crr_compare.reader_mcp_server` | `python -m document_compare.phase2.mcp.reader_server` |
| `python -m uvicorn crr_compare.service:app …` | `python -m uvicorn document_compare.phase2.api.service:app …` |
| `python -m agent_poc.mock_reader …` | `python -m document_compare.phase2.agent.mock_reader …` |

Runtime environment keys keep their legacy `CRR_` prefix (a preserved external
config contract), even though the old Python CLI is gone; this is not a config
migration and no new keys are introduced. Artifact filenames and HTTP/MCP
contracts are unchanged.

## Troubleshooting

- Editable install fails with a `distutils`/`core` assertion under this
  environment's setuptools/distutils shim. Local workaround after installing
  `requirements.txt`: `SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install
  --no-deps --no-build-isolation -e .`. This is environment-specific; do not
  disable TLS verification or organizational execution policies.
- Missing internal SDK: install `bbk_ai_hub_sdk` from your approved internal
  index (needed for Stage 13 `bbk_ai_hub` and the local chat).
- Port conflict on `8321`: stop the other process or set `CRR_API_BASE` to
  another loopback port.

## Scope and limits

- Local pilot only — not a production architecture or deployment decision.
  Database, authentication and shared deployment remain open.
- Contract/integration evidence only; no real-LLM reasoning or real-company
  semantic accuracy is claimed. Synthetic tests do not prove company-PDF or
  Windows acceptance.
- Gate B shadow output is advisory research evidence, not calibrated confidence,
  legal truth or an authoritative disposition. The previously exposed 72-pair
  corpus and 48-pair holdout are development/regression evidence now. No
  untouched holdout, >=80% supported recall, >=95% supported precision or
  semantic release readiness is claimed from this code or its fake-backend tests.
- Native PDF bookmarks are optional structural evidence, not a printed table of
  contents or authoritative hierarchy. Missing, partial, invalid or stale
  outline evidence falls back to the existing reconstruction path.
- Source-PDF strikethrough detection is explicitly deferred. Report
  strike-through marks compare old/new text; they are not source formatting.
  Private table-extraction quality remains unverified without a representative
  failing sample. Stage 10 bounds fuzzy table scoring through deterministic
  header/body/context/shape candidate retrieval, but this does not prove that
  every true table counterpart is retrieved on private documents.
- `workspace/` and `input/*` (except `input/.gitkeep`) are gitignored; keep
  credentials in `.env` only and never commit or paste them.

