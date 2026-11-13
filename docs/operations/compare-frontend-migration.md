# Migrating the compare frontend into the current backend

Audience: the coding agent on the work laptop. Goal: bring the Phase 2 Streamlit
compare frontend (built off-site against a copy of this repo) into the current
backend without breaking existing contracts. Spec:
[compare frontend design](../specs/2026-11-11-compare-frontend-design.md); plan
and ledger: [compare frontend plan](../plans/2026-11-11-compare-frontend.md);
HTTP contract: [view endpoints](../contracts/view-endpoints.md).

Source: branch `feat/compare-frontend` on the FileHolder repo. Commit `bc10a37`
is only a backend snapshot of the off-site copy — do **not** take it. The
frontend is two commits, apply both in order: `d7f9c90` (first frontend) and
`47c6f0f` (redesign: section tree, search, context pages, sidebar tabs). Easiest:
`git cherry-pick d7f9c90 47c6f0f` onto your branch, resolving against the
current backend.

## What the change contains

New, self-contained (copy as-is):

- `src/document_compare/phase2/viewer/` — `locations.py` (finding → page +
  normalized polygons), `import_workspace.py` (saved workspace → job, one command), `outline.py` (section tree with change counts and
  section-context pages), `page_render.py` (PyMuPDF page PNG + highlight),
  `synthetic.py` (offline demo job seeder), `README.md`.
- `src/document_compare/phase2/ui/` — Streamlit app: `app.py` (sidebar tabs
  Compare / Open job / Comparison agent), `review.py` (header, chips, filters,
  search, structure tree, page-pair viewer), `chat.py`, `api_client.py` (pure
  HTTP client of the API), `README.md`.
- `.streamlit/config.toml` — loopback bind, headless, no telemetry, 50 MB upload.
- Tests: `tests/phase2/test_viewer_synthetic.py`, `test_viewer_locations.py`,
  `test_viewer_outline.py`, `test_viewer_import_workspace.py`, `test_page_render.py`, `test_view_api.py`,
  `test_ui_client.py`, `test_ui_smoke.py`, `ui_stub.py`.
- Docs: `docs/contracts/view-endpoints.md`, this file.

Edits to existing files (merge, do not overwrite):

- `src/document_compare/phase2/api/service.py` — **add-only**: imports
  (`os`, `unicodedata`, `Literal`, `Response`, `BaseModel`, viewer functions),
  `_coded()` and `_display_name()` helpers, two new keys `old_name`/`new_name`
  (upload file names) in the `create_job` status dict, and four routes after
  `/read/details`: `GET /jobs/{id}/view/locations`, `GET /jobs/{id}/view/outline`,
  `GET /jobs/{id}/view/pages/{side}/{page}.png`, `POST /jobs/{id}/chat`. No
  existing route or response key changed. File is **CRLF** — keep it.
- `src/document_compare/phase2/viewer/locations.py` — small helper exposed for
  `outline.py` (per-side element IDs of a record).
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
7. Section tree input: `output/{old,new}_document_structured.json` →
   `document.structure_evidence.sections` (`section_id`, `title`, `level`,
   `parent_section_id`, `parent_known`, `heading_element_id`) and
   `structure_evidence.membership.by_element[element_id].section_id`; elements
   carry `primary_page`. Old and new sections are merged by normalized title.
   If Phase 1 renamed these fields, adapt `outline.py` only.
8. Streamlit ≥ 1.46 (uses `st.columns(width=…)`, `st.container(height=…)`,
   tertiary buttons, `st-key-*` CSS classes); built and checked on 1.67. The tree
   CSS in `review.py` and the tab CSS in `app.py` target 1.67's DOM — re-check in
   a browser after any Streamlit upgrade.

What the UI reads per job: `comparison_result.json` (findings, via the bounded
reader), `{old,new}_document.json` (polygons), `{old,new}_document_structured.json`
(section tree), `input/document/{old,new}.pdf` (page images), `job_status.json`.

## Steps

1. Branch from current main: `git switch -c feat/compare-frontend`.
2. `git fetch` the FileHolder branch and `git cherry-pick d7f9c90 47c6f0f`
   (or copy the new directories/tests listed above and apply the edits by hand).
3. Resolve `service.py` and `pyproject.toml` so they stay add-only: check
   `git diff --stat` shows only additions there and line endings unchanged
   (`file <path>` → "CRLF"). Do not take `.env.example` from the branch; its
   endpoint values are placeholders.
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

   Off-site baseline: phase2 304 OK; full suite 992 with 7 failures that are
   environment-only (no git, missing hub SDK, checksums of the copy). On the work
   laptop those 7 should pass; any new failure is a migration defect.
7. Manual check, loopback only:

   ```bash
   python -m document_compare.phase2.viewer.synthetic --jobs-root workspace/jobs
   python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
   python -m streamlit run src/document_compare/phase2/ui/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
   ```

   In the sidebar "Open job" tab, open the printed job ID. Expect:
   - header with file names and page counts, chips Changes / Tables / Added /
     Deleted / Modified;
   - a structure tree rooted at the document title, counts per section, a
     "Document order / Section number" toggle (the demo's new PDF has
     Sections 1–3 after Section 12 on purpose), and a working search box;
   - amber boxes on both pages for MODIFIED; DELETED red dashed on the old page
     with the new page showing the same section ("· section context", no
     highlight); ADDED mirrored in green; both pages the same size;
   - Previous / Next side by side under the pages.

## Opening a real saved workspace (no rerun, no provider call)

```bash
python -m document_compare.phase2.viewer.import_workspace --workspace workspace/EBA_STEP2 --old "input/OLD.pdf" --new "input/NEW.pdf"
```

Run without arguments to be asked for the paths. It copies the workspace's
`output/` and the two PDFs (same OLD/NEW orientation as that run) into a new
`workspace/jobs/<id>/`, prints the ID, and never modifies the workspace. Open the
ID in the UI's "Open job" tab. Delete the job folder when done.

## Not verified off-site — do these on the work laptop

- Highlight placement on **real** PDFs (rotated pages, crop boxes, scanned
  documents). Rendering scales by `page.cropbox`; rotation is untested.
- A real upload → Stage 1 extraction run (sends both PDFs to the extraction
  provider — get approval first).
- Live chat against the hub client (real provider call — get approval first).
- A **nested** section tree: all off-site demo jobs come out flat
  (`basis: flat_fallback`, no parents), so nesting is only covered by unit tests.
  Check a real PDF with a table of contents or bookmarks. The "title as root"
  and "Section number" sort are UI-only display choices for flat outlines.
- Section-context pages on real documents (the page chosen is the one inside the
  section's page span closest to the change's page).
- Upload file names in the header (needs a real upload; demo jobs fall back to
  `<fixture>_old.pdf` / `old.pdf`).

## Deliberate UI decisions (from user review, 2026-11-13)

- Uncertain / supported counts, the State filter and the limitations text are
  hidden in the UI. The API and reader still return them; nothing was removed
  from contracts.
- Review items ARE shown (user decision, later the same day): items whose
  `resolution.status` is `review` get a purple "Review" tag and a plain reason;
  review-queue records linked to a change mark that change; standalone records
  (e.g. moved blocks) are their own tree items, highlighted purple dashed from
  their evidence element IDs. "Needs review N" chip and "Needs review only"
  filter. `/view/locations` now returns polygons for review records (it used to
  return `unavailable`).
- Tables highlight only changed cells (`cell_changes`), with a thin outline of
  the whole table; labelled "Table · p.N · N cells changed".
- LLM opinion (advisory) box: shown only for items with
  `resolution.packet_decision` — outcome, reason, evaluator, element IDs. The
  confidence number is deliberately not shown (not calibrated). Check it with a
  real Stage 13 run; off-site only the mock evaluator was seen.
- The text/details block under the pages is removed; only the change ID remains.
- Agent activity log under the chat: discussed, deferred.

## Boundaries

Phase 1 must not import Phase 2. Services stay on 127.0.0.1. Keep `CRR_` keys and
existing HTTP/MCP contracts. Never commit `.env`, `input/*`, `workspace/*` or
result text. No push/deploy without the user's go-ahead.
