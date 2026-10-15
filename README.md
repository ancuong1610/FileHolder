# Document Comparison Service

Local tool that compares two versions of the same PDF and reports changes.
Scope: two-version document comparison only.

For the company-machine installation, real PDF/agent run and manual acceptance
checklist, see [Company handover](docs/COMPANY_HANDOVER.md).

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

Domain settings are retired. Comparison is generic only: an unset, blank, or
legacy `auto` `CRR_DOMAIN_MODE` resolves to `generic`, and an explicit `crr3`
now fails with an actionable configuration error instead of implying legal
verification. `CRR_EVIDENCE_DIR` is retired and has no production consumer;
no amendment JSON or CRR evidence directory is required for any comparison.
CRR-looking text in a document is ordinary content, not a trigger.

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
- The child pipeline is forced to `CRR_RUN_LLM=mock` even if your shell has
  `CRR_RUN_LLM=real`; your shell environment is not modified. Comparison is
  generic only (the retired `CRR_DOMAIN_MODE` is not needed).
- Console output is labeled `[MOCK/SYNTHETIC]`. Provenance is written to
  `output/mock_run.json`; the canonical result remains `output/comparison_result.json`
  with its unchanged schema, and the redline is `output/comparison_redline.pdf`.

The demo is a synthetic reproduction, not a comparison of your own PDFs. To
compare real documents, use the real flow below.

## Offline evidence review (T-02/T-03, supplied artifacts only)

`s05_anchors` additionally records a versioned, additive `structure_evidence`
layer on each structured document: TOC candidates/entries, conservative
entry-to-body links, section level/parent hypotheses and derived element
membership. It preserves raw text, roles, IDs, spans, geometry, eligibility,
order and table accounting; it does not create an authoritative hierarchy and
TOC tables stay in comparison.

Build a local, read-only review bundle from an existing output directory
(no network, no DI/provider call, no model download):

```bash
python -m crr_compare review-evidence \
  --artifacts /path/to/output \
  --out /path/to/fresh-review-dir \
  [--reference /path/to/reference.json]
```

- `--artifacts` is an existing pipeline output directory (saved artifacts, not
  a job upload). Missing layers are reported; the additive structure is
  reconstructed from saved normalized/structured artifacts when absent, and
  the saved artifacts are never rewritten.
- `--out` must be fresh (empty or absent) and must not be inside the artifact
  tree. Files are written atomically.
- Writes `index.html` (self-contained, escaped, no external assets),
  `metrics.json`, `structure_evidence_{old,new}.json`, `provenance.json`
  (artifact SHA-256 plus implementation/configuration versions) and a
  `reference_template.json` explicitly marked UNVERIFIED.
- `--reference` is an optional versioned (1.1.0) annotation input with
  document fingerprints and per-side declared coverage (`coverage.old` /
  `coverage.new`). Allowed kinds are `synthetic_authored` (synthetic=true,
  unverified allowed, notes required) and `source_verified` (verified=true,
  synthetic=false, notes required); the UNVERIFIED template is rejected.
  Without a reference, metrics are `not_scored`/null; a missing expected key
  for a covered ID is invalid; partial coverage cannot establish a global
  precision; empty positive truth with false predictions is reported as a
  `false_positive` failure. Extraction text/location accuracy and comparison
  semantic accuracy stay `not_scored` unless independently verified
  references exist.
- This is "Docling-informed evidence review — reference-only": it is not
  Docling output, not an upstream Docling metric, and no Docling package is
  installed or invoked. It is evidence visualization, not source-page truth.

## Offline performance benchmark (supplied artifacts only)

`benchmark-performance` answers "how well is the engine actually doing, and
where does it fail?" without any provider call. It reads a saved pipeline
output directory and uses the saved Step-3 `old_document.json` as the immutable
OLD side; for each change category it copies that internal model, injects a
controlled declared edit, writes the copy as the NEW side, records the exact
truth **before** running anything, then runs stages 04–15 with the mock
evaluator and scores the canonical result against the declared truth.

```bash
PYTHONHASHSEED=0 python -m crr_compare benchmark-performance \
  --artifacts /path/to/output \
  --out /path/to/fresh-benchmark-dir \
  [--seed 42]
```

- `--artifacts` must contain `old_document.json` (or both `old_di_raw.json`
  and `new_di_raw.json`, from which stages 02–03 are derived in an isolated
  staging root). It is never written to.
- `--out` must be fresh (empty or absent) and must not be inside the artifact
  tree; every file is written atomically.
- Writes `benchmark_report.json`, `index.html` (self-contained Vietnamese
  report, escaped, no external assets), `summary_vi.txt` (plaintext per-case
  outcome) and one `cases/<category>/` folder per category holding
  `truth.json`, `score.json` and the pipeline `output/`.
- Every case is labelled **synthetic AFTER EXTRACTION**: variations copy the
  saved Step-3 model; this is not a fresh DI result and not a comparison of the
  original PDFs. Retained spans/bounding regions are origin references only.
- Set `PYTHONHASHSEED=0` from **outside** the process to pin the environment.
  The report records the actual `python_hash_seed`, `interpreter` and
  `python_version`; `config_fingerprint` pins the input + selection config and
  is not a hash seed.
- Scoring keeps two explicit streams: (A) asserted/accepted exact-pair
  correctness (`tp + fn == truth_count`; a review-only detection is not a TP and
  remains an FN), and (B) review routing (`surfaced_for_review` vs
  `never_surfaced`). A separate, clearly labelled `detection_including_review`
  never resolves a case. Review-only suggestions are not counted as accepted
  false positives. Table cells are accepted only when the root table record's
  `resolution.status == "accepted"` **and** the cell is not `needs_review`.
  `before/after` is reported as unmeasured.
- **A true positive also requires the canonical raw text payload.** A record is a
  true positive only when it matches the exact declared `(kind, old_id, new_id)`
  (or `(classification, old_cell_id, new_cell_id)`), is `accepted`, **and** its raw
  canonical text equals the declared `before_text`/`after_text` on every side the
  truth declares. A right pair with a wrong raw text payload is a false positive
  **and** a false negative (`wrong_payload_mismatches`), reported separately from a
  wrong pair/kind (`wrong_pair_mismatches`). The absent `new` side of a deletion
  and the absent `old` side of an insertion have no expected text, so the
  prediction's unused text on that side is ignored rather than treated as a
  mismatch. A review-only prediction with a wrong payload is not "correct but
  needs review": it is a review suggestion and its truth stays a miss.
- **Declared evaluation scope (`benchmark_version = 1.1.0`):**
  `evaluation_scope = "canonical-pair-kind-raw-text"`. The scorer measures the
  canonical pair/kind, the accepted state and the canonical raw text. It does
  **not** measure PDF rendering or source-PDF fidelity
  (`PDF rendering / source PDF fidelity NOT measured`); it is not a Docling or
  `docling-core` metric and no Docling package is used. A change to how scoring
  is computed is never turned into a before/after improvement claim
  (`before/after` stays unmeasured).

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
- Controlled benchmark PASS/FAIL measures only its declared synthetic references
  and floors. It does not establish accuracy on real company document pairs.
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

Offline synthetic demo (no credentials, no network):

```bash
python -m crr_compare demo --fixture internal_policy_report --root /path/to/demo-root
```

Offline evidence review bundle (no credentials, no network):

```bash
python -m crr_compare review-evidence --artifacts /path/to/output --out /path/to/fresh-review-dir
```

Offline controlled performance benchmark (no credentials, no network):

```bash
PYTHONHASHSEED=0 python -m crr_compare benchmark-performance --artifacts /path/to/output --out /path/to/fresh-benchmark-dir --seed 42
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
- `comparison_inline.pdf` — additional single-flow redline: deleted text struck
  red, inserted text blue; conservatively joins corroborated German line-end
  hyphenations without changing canonical text. Uncertain joins remain visible;
  this is layout-noise reduction, not certified semantic equivalence.
- `inline_pdf_audit.json` — exact join/omission counts, affected IDs and limits
  for the additional inline view. The original redline remains available.
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
