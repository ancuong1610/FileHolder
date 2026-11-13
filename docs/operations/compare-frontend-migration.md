# Migrating the compare frontend into the current backend

Audience: the coding agent on the work laptop. Goal: bring the Phase 2 Streamlit
compare frontend (built off-site against a copy of this repo) into the current
backend without breaking existing contracts. Spec:
[compare frontend design](../specs/2026-11-11-compare-frontend-design.md); plan
and ledger: [compare frontend plan](../plans/2026-11-11-compare-frontend.md);
HTTP contract: [view endpoints](../contracts/view-endpoints.md).

## What the change contains

New, self-contained (copy as-is):

- `src/document_compare/phase2/viewer/` — `locations.py` (finding → page +
  normalized polygons), `page_render.py` (PyMuPDF page PNG + highlight),
  `synthetic.py` (offline demo job seeder), `README.md`.
- `src/document_compare/phase2/ui/` — Streamlit app: `app.py`, `review.py`,
  `chat.py`, `api_client.py` (pure HTTP client of the API), `README.md`.
- `.streamlit/config.toml` — loopback bind, headless, no telemetry, 50 MB upload.
- Tests: `tests/phase2/test_viewer_synthetic.py`, `test_viewer_locations.py`,
  `test_page_render.py`, `test_view_api.py`, `test_ui_client.py`,
  `test_ui_smoke.py`, `ui_stub.py`.
- Docs: `docs/contracts/view-endpoints.md`, this file.

Edits to existing files (merge, do not overwrite):

- `src/document_compare/phase2/api/service.py` — **add-only**: imports
  (`os`, `Literal`, `Response`, `BaseModel`, viewer functions), `_coded()`
  helper, and three routes after `/read/details`:
  `GET /jobs/{id}/view/locations`, `GET /jobs/{id}/view/pages/{side}/{page}.png`,
  `POST /jobs/{id}/chat`. No existing route changed. File is **CRLF** — keep it.
- `pyproject.toml` (CRLF) — optional extra `ui = ["streamlit>=1.37,<2"]`.
- `README.md`, `docs/architecture.md`, `docs/review-guide.md`,
  `docs/operations/phase-testing.md` (section 7), `docs/contracts/README.md`,
  `tests/phase2/README.md` — added sections/links only.

## Backend assumptions to verify first

The frontend was built against an older copy. Before merging, confirm these still
exist with the same shape in the current backend; adapt the viewer, not the
backend, if they differ:

1. `phase2/reader/result_reader.py` exports `ID_FIELDS` and `ReaderError`
   (`as_dict()` → `{"error", "message"}`), and `/read/summary|search|details`
   keep their parameters and cursor pagination.
2. Saved job layout: `workspace/jobs/<12 hex>/` with `job_status.json`,
   `input/document/{old,new}.pdf`, and extraction `*_document.json` whose
   `bounding_regions` carry 8-number polygons **in inches** and pages carry
   `width`/`height` in inches. Locations normalize against those to 0–1.
3. Table changes: cells resolve via `represented_by_table_id` on the same page.
   Review-queue records have no geometry (`location: "unavailable"`, by design).
4. Chat route lazily imports `phase2.agent.tia_client.TiaTextClient`,
   `phase2.agent.controller.TiaComparisonAgent(client, gateway, max_steps, result_limit)`
   and `phase2.workflow.saved_workspace` (`SavedWorkspaceGateway`). Chat is
   enabled only when `TIA_API_KEY` and `LLM_MODEL` are set; otherwise 503
   `chat_not_configured`. Use the hub client the backend already uses — do not
   add a new provider or credentials.
5. `phase1.pipeline.cli.run_demo` (used only by the synthetic seeder).
6. `pymupdf>=1.24`, `httpx`, `fastapi` already in `requirements.txt`.

## Steps

1. Branch from current main: `git switch -c feat/compare-frontend`.
2. Copy the new directories and test files listed above.
3. Apply the `service.py` and `pyproject.toml` hunks by hand or with
   `git apply --3way`; check `git diff --stat` shows only additions there and
   line endings unchanged (`file <path>` → "CRLF").
4. Reconcile the assumptions above; fix mismatches in `phase2/viewer/` or
   `phase2/ui/api_client.py`.
5. Install: `python -m pip install -e ".[ui]"` (README setuptools workaround if
   needed).
6. Verify:

   ```bash
   python tools/harness/validate.py
   python -m unittest discover -s tests/phase2
   python -m unittest discover -s tests
   ```

   Off-site baseline: phase2 243 OK; full suite 931 with 7 failures that are
   environment-only (no git, missing hub SDK, checksums of the copy). On the work
   laptop those 7 should pass; any new failure is a migration defect.
7. Manual check, loopback only:

   ```bash
   python -m document_compare.phase2.viewer.synthetic --jobs-root workspace/jobs
   python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
   python -m streamlit run src/document_compare/phase2/ui/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
   ```

   Open the printed job ID. Expect amber boxes on both sides for MODIFIED, red
   dashed old-side for DELETED, green dashed new-side for ADDED.

## Not verified off-site — do these on the work laptop

- Highlight placement on **real** PDFs (rotated pages, crop boxes, scanned
  documents). Rendering scales by `page.cropbox`; rotation is untested.
- A real upload → Stage 1 extraction run (sends both PDFs to the extraction
  provider — get approval first).
- Live chat against the hub client (real provider call — get approval first).

## Boundaries

Phase 1 must not import Phase 2. Services stay on 127.0.0.1. Keep `CRR_` keys and
existing HTTP/MCP contracts. Never commit `.env`, `input/*`, `workspace/*` or
result text. No push/deploy without the user's go-ahead.
