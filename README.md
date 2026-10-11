# Document Comparison Service

Local tool that compares two versions of the same PDF and reports changes.
Scope: two-version document comparison only.

## Architecture

- `crr_compare/` — vendored engine. The 15-stage pipeline runs
  `s01_extract` → `s15_serve`; `service.py` and `mcp_server.py` are the
  API/MCP boundaries.
- `crr_compare/service.py` — FastAPI job service. Owns job creation,
  isolated `workspace/jobs/<id>/` workspaces, background pipeline execution,
  and status/report/artifact/summary endpoints. It is the only component that
  invokes the engine.
- `crr_compare/mcp_server.py` — FastMCP adapter over the HTTP API. Exposes
  `submit_comparison`, `check_status`, `retrieve_report`,
  `retrieve_structured_changes`, and `retrieve_summary` over real stdio
  transport. Independently runnable.
- `agent_poc/local_reviewer.py` — local-review terminal agent. Prompts for two
  PDF filenames, submits/polls/reads via MCP, prints progress, the operational
  summary, and the redline URL, then binds Q&A to the job.
- `run_local.py` — thin entrypoint: validates environment, starts the FastAPI
  child process, opens the MCP stdio client, runs the reviewer, and stops the
  children on exit while preserving job outputs.

Data flow: engine → service → MCP stdio → local reviewer. All runtime output
lives under the ignored `workspace/`.

## Pipeline map (s01–s15)

Each stage follows **Input → Work → Output → Review**; later stages consume the
named artifact from the previous stage. `s01_extract` PDF/API input → extraction
→ DI JSON → extraction warnings; `s02_validate_extraction` DI JSON → validate
spans/pages/tables → validation JSON → extraction review; `s03_internal_schema`
validated DI → neutralize → document JSON → schema review; `s04_normalize`
document → conservative normalization → normalized JSON → normalization flags;
`s05_anchors` normalized JSON → anchor evidence → structured JSON → provisional
context review; `s06_exact_matching` structured pair → exact matching → exact
matches → duplicate/unmatched review; `s07_modified_matching` residuals →
bounded retrieval/scoring → modified matches → diagnostics/review candidates;
`s08_candidates` matches → assemble changes → candidates JSON → conservation
checks; `s09_movement` candidates → movement/resegmentation safety → enriched
candidates → structural review; `s10_tables` structured tables → logical-table
matching → table comparison JSON → cell/table review; `s11_routing` candidates
and tables → route → review routing → route safety; `s12_packets` eligible
routes → packetize → review packets → payload checks; `s13_llm_evaluator`
packets → mock/real evaluation → decisions → evaluator trace; `s14_canonical_result`
all decisions → canonicalize → result/queue/CSV → resolution review;
`s15_serve` canonical artifacts → render/serve → HTML/PDF/manifest/index → final
human review. Offline demos use synthetic fixtures and `CRR_RUN_LLM=mock`.

## Python and environment

Python 3.10+ (verified on 3.11). Create and activate a virtual environment,
then install the public dependencies.

macOS / Linux:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once for the
current shell, then activate again.

## Setup

```bash
pip install -r requirements.txt
```

This installs only public dependencies and succeeds without the internal
`bbk_ai_hub_sdk`.

`bbk_ai_hub_sdk` is internal-only and not publicly installable. It is needed
separately for:

- Stage 13 with the default `LLM_PROVIDER=bbk_ai_hub`, and
- the current local reviewer chat, which uses the internal TIA/BBK client.

Internal users: install it from your approved internal package index following
your team's instructions. This README intentionally provides no internal index
URL and no credential-bearing command.

Copy `.env.example` to `.env` and fill in values (`.env` is gitignored).
Environment names from `.env.example`:

| Variable | Purpose |
| --- | --- |
| `TIA_API_KEY` | Azure Document Intelligence key and hub key (required for real extraction). |
| `TIA_AZURE_ENDPOINT` | Azure Document Intelligence endpoint (required for real extraction). |
| `BBK_AI_HUB_API_KEY` | Optional alternative hub key for Stage 13 `bbk_ai_hub`. |
| `OPENAI_API_KEY` | Required for `LLM_PROVIDER=openai_compatible` Stage 13. |
| `OPENAI_BASE_URL` | Optional base URL for the OpenAI-compatible provider. |
| `LLM_PROVIDER` | `bbk_ai_hub` (default) or `openai_compatible`. Must be set; a blank value is not a valid provider. |
| `LLM_MODEL` | Required with `openai_compatible`. |
| `CRR_RUN_LLM` | `false`, `mock`, or `real`; default `mock`. `run_local.py` forces `real` for its child processes. |
| `CRR_API_BASE` | Loopback API base, default `http://127.0.0.1:8321`. |
| `CRR_MCP_UPLOAD_ROOT` | MCP upload containment root, default `./input`. |
| `CRR_PROJECT_ROOT` | Runtime workspace root, default `./workspace`. |
| `FASTMCP_CHECK_FOR_UPDATES` | Set `off` to skip FastMCP update checks. |

Provider requirements:

- Real extraction always needs `TIA_API_KEY` + `TIA_AZURE_ENDPOINT`.
- Real Stage 13 needs one provider:
  - `bbk_ai_hub`: `TIA_API_KEY` or `BBK_AI_HUB_API_KEY`, plus the internal
    `bbk_ai_hub_sdk`.
  - `openai_compatible`: `OPENAI_API_KEY` + `LLM_MODEL` (optional
    `OPENAI_BASE_URL`); no internal SDK for Stage 13.
- The current local chat still uses the internal TIA/BBK client and therefore
  its internal `bbk_ai_hub_sdk`, even when Stage 13 uses
  `openai_compatible`. A fully public credentialed chat flow without that SDK
  is not provided.

## Offline mock demo (synthetic)

No credentials, no network, and no uploaded PDFs are needed. The demo seeds a
bundled, already-extracted Document Intelligence fixture pair (it never runs
Stage 01 extraction), then runs Stages 02-15 with the deterministic mock
evaluator and the generic domain:

```bash
python -m crr_compare demo --fixture internal_policy_report --root /path/to/demo-root
```

- `--fixture` is one of `crr3`, `internal_policy_report` (default), or
  `scai_api_guide`, all fully synthetic.
- `--root` is a fresh directory. The demo refuses to run if its `output/`
  already contains data and never overwrites existing output; it does not need
  to exist beforehand, and paths with spaces are supported.
- The child pipeline is forced to `CRR_RUN_LLM=mock` and
  `CRR_DOMAIN_MODE=generic` even if your shell has `CRR_RUN_LLM=real`; your
  shell environment is not modified.
- Console output is labeled `[MOCK/SYNTHETIC]`. Provenance is written to
  `output/mock_run.json`; the canonical result remains `output/comparison_result.json`
  with its unchanged schema, and the redline is `output/comparison_redline.pdf`.

The demo is a synthetic reproduction, not a comparison of your own PDFs. To
compare real documents, use the real flow below.

## Real run: `python run_local.py`

Put exactly two selected PDFs in `input/` (other PDFs may exist; you choose
two by filename). Then:

```bash
python run_local.py
```

Flow:

1. Loads `.env`, requires credentials, starts FastAPI on `127.0.0.1` (default
   port 8321), and waits for readiness.
2. Opens the FastMCP stdio client, which launches
   `python -m crr_compare.mcp_server` with `CRR_MCP_UPLOAD_ROOT=input/`.
3. Lists available PDFs and prompts for exactly two filenames. Validation:
   bare filename, resolves inside `input/`, `.pdf` suffix, `%PDF-` signature,
   two distinct files, and ≤ 50 MiB. Invalid input re-prompts; it does not
   exit.
4. Submits via MCP `submit_comparison` with a generated session id, polls
   `check_status`, and prints stage progress.
5. On success, prints the operational summary via `retrieve_summary` and a
   clickable redline URL:
   `http://127.0.0.1:8321/jobs/<job_id>/artifacts/comparison_redline.pdf`.
6. Real Azure extraction (s01) and real Stage 13 run during the job.
7. Ask questions bound to that job (the reviewer can call `retrieve_report`,
   `retrieve_structured_changes`, `retrieve_summary`, `check_status`). Type
   `quit` or `exit` to end the chat.
8. On exit, child processes stop and `workspace/jobs/<job_id>/` is preserved.

## Benchmark vs accuracy

- The operational summary describes this run's execution health (progress,
  change/disposition counts, extraction warnings, evaluator mode). It is a
  health signal, not an accuracy score.
- Accuracy comes only from the deterministic offline benchmark's PASS/FAIL
  against its existing floors.
- There is no aggregate quality claim and no dashboard; the two are never
  conflated.

## Commands

Benchmark (offline, mock LLM; floors only):

```bash
CRR_RUN_LLM=mock python -m crr_compare eval --root workspace
```

Writes `workspace/eval/eval_report.json` and exits non-zero when floors fail.

Tests (offline):

```bash
python -m unittest discover -s tests -v
```

This public source-only branch is self-contained for offline testing. Its
fixtures are synthetic and stage smoke tests use temporary directories;
private PDFs, review exports, workspace outputs, and credentials are
intentionally not included. The real-document workflow below is unchanged and
must be run only in an authorized environment.

Offline synthetic demo (no credentials, no network):

```bash
python -m crr_compare demo --fixture internal_policy_report --root /path/to/demo-root
```

API (independent):

```bash
python -m uvicorn crr_compare.service:app --host 127.0.0.1 --port 8321
```

MCP server (independent, stdio):

```bash
python -m crr_compare.mcp_server
```

Primary local run:

```bash
python run_local.py
```

## Artifacts

Per job under `workspace/jobs/<job_id>/output/`:

- `comparison_result.json` — canonical comparison result.
- `bu_comparison_review_manifest.json` — review manifest.
- `comparison_redline.pdf` — primary product redline (direct ReportLab).
- `bu_comparison_redline.html` — internal/intermediate only, not the primary
  PDF path.

Also available: `phase2_change_index.json`, `review_queue_export.csv`, and
table/audit files, all listed by `GET /jobs/<id>/artifacts`.

## Troubleshooting and security

- Credentials live only in `.env`; `.env` is gitignored. Never commit it or
  paste credentials into logs or commands.
- The API and MCP endpoints are loopback-only; `CRR_API_BASE` must be
  `http://127.0.0.1` or `http://localhost`.
- Input validation: bare filename, contained in `input/`, `.pdf` suffix,
  `%PDF-` signature, two distinct files, and ≤ 50 MiB (service cap
  `MAX_UPLOAD_BYTES`).
- Missing credentials: `python run_local.py` exits naming the missing
  variables (never their values). Set `TIA_API_KEY` + `TIA_AZURE_ENDPOINT`;
  with `openai_compatible`, also `OPENAI_API_KEY` + `LLM_MODEL`.
- Missing internal SDK: `ModuleNotFoundError: bbk_ai_hub_sdk` — install it
  from your approved internal index (needed for Stage 13 `bbk_ai_hub` and the
  local chat).
- Port conflict on 8321: stop the other process or set `CRR_API_BASE` to
  another loopback port.
- `workspace/` and `input/*` (except `input/.gitkeep`) are gitignored, so
  generated jobs and dropped PDFs are not committed.

## Scope

The `agent_poc` local reviewer is for local review only. This is a local
product/review tool, not a production architecture or deployment; production
deployment decisions are out of scope.
