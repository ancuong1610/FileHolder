# Compare frontend (Phase 2) — implementation plan

**Status:** COMPLETE (offline-verified)
**Prepared:** 2026-11-11
**Evidence basis:** user-approved spec
[2026-11-11-compare-frontend-design.md](../specs/2026-11-11-compare-frontend-design.md);
code inspected on a personal-machine copy without Git metadata. Unknown: highlight
accuracy on real PDFs (rotation, crop boxes); whether `bbk_ai_hub_sdk` chat works
from the API process on the work machine.

Executors read the spec and this plan together. Use test-driven steps: write the
test, see it fail, implement, see it pass.

## 1. Goal and scope

**Goal:** A reviewer uploads an old and a new PDF in a local Streamlit page,
watches the run, reviews each finding on the original pages side by side with
highlights, and asks questions about the saved result.

**In scope:**
- New read-only package `phase2/viewer/` (locations, page rendering, synthetic seeding).
- Three additive API endpoints (locations, page PNG, chat).
- New Streamlit package `phase2/ui/` (upload, review, chat).
- Offline tests and documentation for the above.

**Out of scope:**
- Any Phase 1 change. Synced scrolling, click-on-highlight, word-level highlights.
- Authentication, non-loopback exposure, a jobs-list endpoint, editing findings.
- Wiring `openai_compatible` into chat. Fixing the seven pre-existing test failures.

## 2. Accepted decisions and constraints

Decisions D1–D6 are in the spec. Refinements made while planning:

- **P1.** Chat uses the existing `TiaTextClient` only. It counts as configured
  when `TIA_API_KEY` and `LLM_MODEL` are set. Otherwise the endpoint returns 503
  `chat_not_configured`. The spec's mention of `openai_compatible` for chat was
  wrong: that provider is not wired into the agent today.
- **P2.** The synthetic PDF builder lives in `src/.../phase2/viewer/synthetic.py`,
  not `tests/support/`, so the manual seeding command can use it. Precedent:
  `phase2/agent/mock_reader.py`.
- **P3.** `review` records have no element reference. Their location is
  `unavailable` on both sides in v1 and the UI shows text only.
- **P4.** A table cell without a polygon falls back to the regions of elements
  whose `represented_by_table_id` equals the cell's `source_table_id`, limited to
  the cell's `primary_page`; if none, `page_only`.
- **P5.** `streamlit` is approved by D2. Installing it into the local `.venv` is
  part of T6. It is declared as optional extra `ui` in `pyproject.toml`.

Global constraints (apply to every task):

- Phase 1 never imports Phase 2. `phase2/ui` talks to the API over HTTP only.
- Existing endpoints, artifact names and `CRR_` keys are unchanged. No new env key.
- Services bind to `127.0.0.1`. Streamlit usage statistics are off.
- New code never writes into a job's `output/` or mutates a saved result.
- No provider, model or network call in any test. No page image or result text in logs.
- Every new folder under `src/`, `tests/`, `docs/` has a `README.md`.
- No commit, push or deployment. This copy has no Git metadata; do not initialize it.
- Python: use `.venv/bin/python` (3.11). Tests are `unittest`, run from the repo root.

Review focus (each is pinned by a named test in the owning task):

1. Page number outside the PDF, or a region on a page the PDF lacks → 422, no crash (T3, T4).
2. Job done but source PDFs absent → 404 `source_pdf_unavailable`; UI falls back to text (T4, T7).
3. Element ID in the result missing from the document JSON → `unavailable`, never a guessed box (T2).
4. Path tricks in `side` / `page` / `record_id` → rejected by validation, no file access outside the job (T4).
5. API down or job failed while the UI polls → visible error, job ID kept, no endless spinner (T6).

## 3. File and interface map

| File / contract | Responsibility | Planned change | Focused check |
| --- | --- | --- | --- |
| `src/document_compare/phase2/viewer/synthetic.py` | Synthetic PDFs and seeded demo job | Create | `python -m unittest tests.phase2.test_viewer_synthetic` |
| `src/document_compare/phase2/viewer/locations.py` | Finding → pages and normalized polygons | Create | `python -m unittest tests.phase2.test_viewer_locations` |
| `src/document_compare/phase2/viewer/page_render.py` | Page → PNG with highlights | Create | `python -m unittest tests.phase2.test_page_render` |
| `src/document_compare/phase2/api/service.py` | HTTP surface | Add 3 routes after the reader routes | `python -m unittest tests.phase2.test_view_api` |
| `src/document_compare/phase2/ui/api_client.py` | httpx wrapper for the UI | Create | `python -m unittest tests.phase2.test_ui_client` |
| `src/document_compare/phase2/ui/app.py` | Streamlit page | Create | `python -m unittest tests.phase2.test_ui_smoke` |
| `.streamlit/config.toml`, `pyproject.toml` | Telemetry off, loopback; `ui` extra | Create / modify | `python -m unittest tests.integration.test_offline_setup` (no new failures) |
| README, architecture, review guide, phase testing, `docs/contracts/view-endpoints.md` | Documentation | Update / create | `python tools/harness/validate.py`; `python -m unittest tests.integration.test_architecture` |

Baseline before T1: `python -m unittest discover -s tests` gives 846 tests, 6
failures, 1 error, all pre-existing drift. "No regression" below means the same
seven and no others.

## 4. Ordered tasks

### T1 — Synthetic PDFs and seeded demo job [DONE]

**Purpose:** Offline test data: real PDFs whose text sits where the fixture's polygons say.

**Allowed files:** `src/document_compare/phase2/viewer/{__init__.py,README.md,synthetic.py}`,
`tests/phase2/test_viewer_synthetic.py`

**Interfaces:**
- Consumes: `document_compare.phase1.pipeline.cli.run_demo(fixture: str, root: Path) -> Path`;
  document JSON `document.pages[].{page_number,width,height}` and
  `document.elements[].{text,bounding_regions}`.
- Produces:
  - `build_synthetic_pdf(document: dict, target: Path) -> None` — one PDF page per
    `pages` entry sized `width*72 × height*72` points; each element's text drawn
    inside its first region's bounding box with PyMuPDF.
  - `seed_demo_job(jobs_root: Path, fixture: str = "internal_policy_report") -> str`
    — runs the demo into `jobs_root/<job_id>`, writes `input/document/old.pdf` and
    `new.pdf` from `output/{old,new}_document.json`, writes `job_status.json` with
    `state: "done"` and all 15 stages, returns the 12-hex `job_id`.
  - CLI: `python -m document_compare.phase2.viewer.synthetic --jobs-root PATH` prints the job ID.

**Steps:**

- [ ] Write `test_pdf_matches_document_geometry`: build from the demo's
  `old_document.json`; assert page count equals `page_count`, page 1 rect is
  `612×792`, and `page.get_text("text", clip=<polygon box of element 1>)`
  contains that element's first word.
- [ ] Write `test_seed_demo_job_is_readable`: seed into a temp dir; assert the ID
  matches `^[0-9a-f]{12}$`, both PDFs start with `%PDF-`, and
  `SavedWorkspaceGateway(job_dir).check()["reader_check"] == "passed"`.
- [ ] Write `test_seed_refuses_existing_job_dir`: a second seed never overwrites
  an existing job directory (new ID each call).
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_viewer_synthetic`.
  Expected before implementation: import error.
- [ ] Implement both functions and the CLI.
- [ ] Run the same command. Expected: PASS.
- [ ] Run: `.venv/bin/python -m unittest tests.integration.test_architecture`. Expected: PASS.

**Acceptance criteria:** A seeded job passes the saved-workspace reader check and contains two valid PDFs.

**Stop conditions:** `run_demo` cannot target a job directory without changing Phase 1.

**Task state:** DONE

### T2 — Finding locations [DONE]

**Purpose:** Turn a finding ID into pages and unit-independent polygons per side.

**Allowed files:** `src/document_compare/phase2/viewer/locations.py`, `tests/phase2/test_viewer_locations.py`

**Interfaces:**
- Consumes: `reader.ReaderError(code, message)`; canonical `changes[]`,
  `table_changes[]`, `review_queue[]`; `output/{side}_document.json`.
- Produces: `resolve_locations(output_dir: Path, canonical: dict, kind: str, record_id: str) -> dict`
  returning

  ```json
  {"kind": "change", "record_id": "CHANGE-000001", "change_type": "MODIFIED",
   "sides": {"old": {"counterpart": "present", "location": "polygon",
                     "page_count": 3, "primary_page": 3,
                     "regions": [{"page_number": 3, "polygon": [0.09, 0.4, 0.9, 0.4, 0.9, 0.43, 0.09, 0.43]}]},
             "new": {"...": "same shape"}}}
  ```

  `counterpart` is `present` or `none`; `location` is `polygon`, `page_only` or
  `unavailable`; polygon values are x/width and y/height of that page, clamped to 0–1.
  `kind` is `change`, `table` or `review`. Raises `ReaderError("not_found")` for an
  unknown record and `ReaderError("invalid_arguments")` for an unknown kind.

**Steps:**

- [ ] Write tests on a seeded job (T1) plus `mock_reader.canonical_main()` for tables:
  - `test_modified_has_polygons_on_both_sides` — both `location == "polygon"`, values in 0–1, `primary_page == 3`.
  - `test_added_has_no_old_counterpart` — old `counterpart == "none"`, `regions == []`; mirror test for deleted.
  - `test_multi_region_element_returns_every_region` — hand-built element with two `bounding_regions` on pages 2 and 3.
  - `test_table_cell_without_polygon_is_page_only` and `test_table_cell_falls_back_to_table_element` (P4).
  - `test_review_record_is_unavailable` (P3).
  - `test_missing_element_is_unavailable_not_guessed` (review focus 3).
  - `test_missing_document_json_is_unavailable`, `test_unknown_record_is_not_found`, `test_unknown_kind_is_invalid`.
  - `test_zero_page_size_is_unavailable` — page `width` 0 never divides.
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_viewer_locations`. Expected before: import error.
- [ ] Implement `resolve_locations`. Read fixed filenames only; reject a path that resolves outside `output_dir`.
- [ ] Run the same command. Expected: PASS.

**Acceptance criteria:** No returned polygon is ever invented; every degraded case is labelled.

**Stop conditions:** A change record on a real fixture (`crr3`, `scai_api_guide`) references elements by something other than `element_id`.

**Task state:** DONE

### T3 — Page renderer [DONE]

**Purpose:** One PDF page as a PNG with outlined, tinted regions.

**Allowed files:** `src/document_compare/phase2/viewer/page_render.py`, `tests/phase2/test_page_render.py`

**Interfaces:**
- Consumes: normalized polygons from T2.
- Produces:
  - `STYLES = {"deleted": (220, 38, 38), "added": (22, 163, 74), "modified": (217, 119, 6)}`
  - `style_for(change_type: str, side: str) -> str` — `DELETED`→`deleted`, `ADDED`→`added`, anything else→`modified`.
  - `render_page(pdf_path: Path, page_number: int, polygons: list[list[float]], *, style: str = "modified", uncertain: bool = False, scale: float = 1.5) -> bytes`
    — PNG bytes; fill opacity 0.25; outline solid, or dashed when `uncertain`.
    Raises `ValueError` for a page outside `1..page_count`, `scale` outside
    `0.5..3.0`, an unknown style, or a polygon that is not 8 finite numbers.

**Steps:**

- [ ] Write tests using a T1 synthetic PDF:
  - `test_pixel_inside_polygon_is_tinted_and_outside_is_not` — compare against an unhighlighted render of the same page.
  - `test_image_size_follows_scale` — width `round(612*scale)` ±1.
  - `test_rotated_page_highlight_lands_on_text` — rotate the page 90°, assert the tinted box still covers the element's rendered text box.
  - `test_uncertain_outline_differs_from_solid`.
  - `test_rejects_bad_page_scale_style_polygon` (review focus 1).
  - `test_source_pdf_is_unchanged` — SHA-256 before and after.
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_page_render`. Expected before: import error.
- [ ] Implement with `pymupdf`: draw on an in-memory copy of the page (never save),
  map normalized points through the page's rotation, render with `get_pixmap`.
- [ ] Run the same command. Expected: PASS.

**Acceptance criteria:** Highlights land on the element in normal and rotated pages; the source file is byte-identical afterwards.

**Stop conditions:** Rotation cannot be made correct on the synthetic page; report with the failing image.

**Task state:** DONE

### T4 — View endpoints: locations and page image [DONE]

**Purpose:** Expose T2 and T3 over the existing loopback API.

**Allowed files:** `src/document_compare/phase2/api/service.py` (add only), `tests/phase2/test_view_api.py`

**Interfaces:**
- Consumes: `_read_status`, `_reader_canonical`, `_reader_http`, `_job_dir` in `service.py`;
  `resolve_locations`, `render_page`, `style_for`.
- Produces:
  - `GET /jobs/{job_id}/view/locations?kind=&record_id=` → T2 dict.
  - `GET /jobs/{job_id}/view/pages/{side}/{page}.png?kind=&record_id=&uncertain=false&scale=1.5`
    → `image/png`, header `Cache-Control: no-store`. `kind`/`record_id` are
    optional together; without them the page is plain. Only regions on `{page}` are drawn.
  - Errors: reader mapping for 404/409/422; missing `input/document/{side}.pdf`
    → 404 with `detail.error == "source_pdf_unavailable"`; renderer `ValueError` → 422.

**Steps:**

- [ ] Write tests with FastAPI `TestClient` and `service.JOBS_ROOT` patched to a
  temp dir holding a T1 seeded job (follow `tests/phase2/test_service.py`):
  - `test_locations_ok`, `test_page_png_ok` (body starts with PNG signature), `test_page_png_with_highlight_differs_from_plain`.
  - `test_unknown_job_404`, `test_running_job_409`, `test_unknown_record_404`.
  - `test_page_out_of_range_422`, `test_bad_scale_422`, `test_kind_without_record_id_422`.
  - `test_side_must_be_old_or_new_422`, `test_record_id_traversal_rejected` (review focus 4).
  - `test_missing_source_pdf_404_code` (review focus 2).
  - `test_existing_routes_unchanged` — the OpenAPI path set before is a subset of after.
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_view_api`. Expected before: 404s.
- [ ] Implement the two routes. `side` is a `Literal["old","new"]`; `page` is an `int` path parameter.
- [ ] Run the same command. Expected: PASS.
- [ ] Run: `.venv/bin/python -m unittest discover -s tests/phase2`. Expected: no regression.

**Acceptance criteria:** Both routes work on the seeded job; no existing route or response changed.

**Stop conditions:** A needed helper in `service.py` would have to change behaviour for existing routes.

**Task state:** DONE

### T5 — Chat endpoint [DONE]

**Purpose:** Let the UI ask the existing agent about one job.

**Allowed files:** `src/document_compare/phase2/api/service.py` (add only), `tests/phase2/test_view_api.py`

**Interfaces:**
- Consumes: `TiaComparisonAgent(tia, gateway, max_steps=10, result_limit=8).ask(question, job_id) -> str`;
  `SavedWorkspaceGateway(job_dir)`; `TiaTextClient(api_key)`; `mock_reader.ScriptedPlanner`.
- Produces:
  - `POST /jobs/{job_id}/chat`, body `{"question": str}` (1–2000 characters after strip)
    → `{"job_id", "answer", "limitations": [...]}`; `limitations` comes from the gateway's summary.
  - `service._chat_client() -> Any | None` — returns a `TiaTextClient` when
    `TIA_API_KEY` and `LLM_MODEL` are set, else `None`. Tests patch this function.
  - Errors: 503 `chat_not_configured`; agent `RuntimeError` (step limit, bad
    planner output) → 502 `chat_failed` with a fixed message, no raw model text;
    job not done → 409.

**Steps:**

- [ ] Write tests:
  - `test_chat_not_configured_503` — env keys removed.
  - `test_chat_answers_with_scripted_planner` — patch `_chat_client` with a `ScriptedPlanner` that calls `summarize` then finals; assert the answer text.
  - `test_chat_uses_only_reader_tools` — wrap the gateway; recorded calls ⊆ `{summarize, search, details}` and all use the URL's `job_id`.
  - `test_chat_step_limit_502`, `test_chat_empty_or_long_question_422`, `test_chat_running_job_409`.
  - `test_chat_does_not_import_sdk_when_unconfigured` — `bbk_ai_hub_sdk` not in `sys.modules`.
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_view_api`. Expected before: new tests fail.
- [ ] Implement the route as a sync `def` so FastAPI runs it in its thread pool.
- [ ] Run the same command. Expected: PASS.

**Acceptance criteria:** Chat is bounded to the three reader tools and to the URL's job; unconfigured chat fails cleanly.

**Stop conditions:** Any step that would need a real model call or a new env key.

**Task state:** DONE

### T6 — Streamlit shell: client, upload, open job [DONE]

**Purpose:** A running page that submits a job, shows progress and opens a finished job.

**Allowed files:** `src/document_compare/phase2/ui/{__init__.py,README.md,api_client.py,app.py}`,
`.streamlit/config.toml`, `pyproject.toml`, `tests/phase2/test_ui_client.py`, `tests/phase2/test_ui_smoke.py`

**Interfaces:**
- Consumes: existing `POST /jobs`, `GET /jobs/{id}/status`, `/read/summary`; T4 and T5 routes.
- Produces: `class ApiClient(base_url: str, transport: httpx.BaseTransport | None = None)` with
  `submit(old_name, old_bytes, new_name, new_bytes, session_id) -> dict`, `status(job_id) -> dict`,
  `summary(job_id) -> dict`, `search(job_id, **filters) -> dict`, `details(job_id, kind, record_id) -> dict`,
  `locations(job_id, kind, record_id) -> dict`,
  `page_png(job_id, side, page, kind=None, record_id=None, uncertain=False) -> bytes | None`
  (`None` on `source_pdf_unavailable`), `chat(job_id, question) -> dict`.
  Any other non-2xx or transport failure raises `ApiError(status: int | None, code: str, message: str)`.
  `base_url` must be loopback `http`, validated like `local_reviewer._api_base`.
- `app.py` exposes `main(client: ApiClient | None = None)` so tests inject a stub.
  Session keys: `job_id`, `selected` (`kind`, `record_id`), `old_page`, `new_page`, `chat_history`.

**Steps:**

- [ ] Add to `pyproject.toml`: `[project.optional-dependencies] ui = ["streamlit>=1.37,<2"]`.
  Create `.streamlit/config.toml` with `[browser] gatherUsageStats = false` and `[server] address = "127.0.0.1"`, `headless = true`.
- [ ] Run: `.venv/bin/python -m pip install "streamlit>=1.37,<2"`.
- [ ] Write `tests/phase2/test_ui_client.py` with `httpx.MockTransport`:
  `test_rejects_non_loopback_base`, `test_submit_posts_two_pdfs_and_session_id`,
  `test_page_png_none_when_source_unavailable`, `test_error_maps_to_api_error`,
  `test_transport_failure_is_api_error` (review focus 5).
- [ ] Write in `tests/phase2/test_ui_smoke.py` (skip the module when `streamlit` is absent; use `streamlit.testing.v1.AppTest`):
  `test_app_loads_without_job`, `test_upload_requires_both_files`,
  `test_failed_job_shows_error_and_keeps_job_id`, `test_api_down_shows_error` (review focus 5),
  `test_open_existing_job_shows_summary_counts`.
- [ ] Run both test modules. Expected before: import errors.
- [ ] Implement `ApiClient` and the Upload / Open-job area. Upload shows the notice
  "Comparing sends both PDFs to the configured extraction provider." above the button.
  Poll status every 2 s while `queued`/`running`; show `len(stages_completed)/15`; stop on `done` or `failed`.
- [ ] Run both test modules. Expected: PASS.
- [ ] Run: `.venv/bin/python -m unittest tests.integration.test_offline_setup tests.integration.test_pipeline`.
  Expected: only the pre-existing failures.

**Acceptance criteria:** The page starts, opens a seeded job by ID and shows its counts; errors are visible.

**Stop conditions:** A test pins `pyproject.toml` content and rejects the extra; report instead of weakening the test.

**Task state:** DONE

### T7 — Review viewer and chat panel [DONE]

**Purpose:** The Acrobat-style review: findings list, side-by-side pages with highlights, text, chat.

**Allowed files:** `src/document_compare/phase2/ui/app.py`, `src/document_compare/phase2/ui/review.py`,
`src/document_compare/phase2/ui/chat.py`, `tests/phase2/test_ui_smoke.py`

**Interfaces:**
- Consumes: `ApiClient` (T6).
- Produces: `review.render(client, job_id) -> None`, `chat.render(client, job_id) -> None`,
  `review.target_page(side_info: dict, other_side: dict) -> int | None` — the side's
  `primary_page`, else the other side's page clamped to this side's `page_count`, else `None`.

**Steps:**

- [ ] Write tests:
  - `test_target_page_rules` — present, no counterpart (clamped), no page count.
  - `test_findings_list_shows_uncertain_by_default` — stub returns one supported and one uncertain row; both appear and the uncertain one carries the text "Uncertain".
  - `test_select_finding_requests_both_pages_with_highlight` — the stub records `page_png` calls for `old` and `new` with that `record_id`.
  - `test_next_previous_move_selection`, `test_load_more_uses_next_cursor`.
  - `test_no_counterpart_note_shown` — text "No counterpart on this side".
  - `test_source_unavailable_falls_back_to_text` (review focus 2) — `page_png` returns `None`; old and new text and a notice are shown.
  - `test_review_record_shows_text_only` (P3).
  - `test_chat_not_configured_notice`, `test_chat_appends_question_and_answer`, `test_chat_error_is_shown_not_raised`.
- [ ] Run: `.venv/bin/python -m unittest tests.phase2.test_ui_smoke`. Expected before: new tests fail.
- [ ] Implement:
  - Header from `summary`; filters (kind, change type, state, query) passed to `search`; "Load more" follows `next_cursor`.
  - Two columns labelled Old and New; `st.image` of `page_png`; per-side page − / + buttons bounded by `page_count`.
  - Type and state shown as text beside the pages; uncertain findings list their reasons from `details`.
  - Cache `page_png`, `locations` and `details` with `st.cache_data` keyed by job, record, side and page.
  - Chat in the sidebar with `st.chat_message` / `st.chat_input`, the notice "Questions send bounded result excerpts to the configured model.", and the reader's ground-truth notice.
- [ ] Run the same command. Expected: PASS.
- [ ] Manual check: `python -m document_compare.phase2.viewer.synthetic --jobs-root workspace/jobs`,
  start the API and the Streamlit app, open the printed job ID in the browser, select each
  finding and confirm the highlight sits on the changed text on both sides. Record what was seen.

**Acceptance criteria:** Selecting a finding shows the right pages with highlights; uncertain findings are visible and labelled; every degraded case shows a notice instead of an error.

**Stop conditions:** `AppTest` cannot drive a needed widget; report which, keep the manual check.

**Task state:** DONE

### T8 — Documentation and full regression [DONE]

**Purpose:** Owning documents match the code; nothing else broke.

**Allowed files:** `README.md` (new section only), `docs/architecture.md`, `docs/review-guide.md`,
`docs/operations/phase-testing.md`, `docs/contracts/view-endpoints.md`, `docs/contracts/README.md`,
`docs/specs/2026-11-11-compare-frontend-design.md` (status, P1–P4 corrections), `tests/phase2/README.md`

**Steps:**

- [ ] Apply the spec's documentation table. Add the run commands from spec §6
  (uvicorn target `document_compare.phase2.api.service:app`, port 8321).
- [ ] Write `docs/contracts/view-endpoints.md`: the three routes, parameters, response shapes, error codes.
- [ ] Update the spec for P1–P4 and set its status to IMPLEMENTED.
- [ ] Run: `.venv/bin/python tools/harness/validate.py`. Expected: `Harness structure valid.`
- [ ] Run: `.venv/bin/python -m unittest discover -s tests`. Expected: the baseline seven and no others.
- [ ] Leave the root README's existing leading conflict marker alone; it is pre-existing and out of scope.

**Acceptance criteria:** Docs name the new packages, routes and commands; full suite shows no regression.

**Stop conditions:** A harness check pins document bytes that this task must change.

**Task state:** DONE

## 5. Documentation and handoff

- Documentation impact: listed in T8; Knowledge Transfer Phase 1 is unchanged because Phase 1 is untouched.
- Final checks: harness validation; `tests/phase2`; full suite against the baseline; the T7 manual browser check.
- Handoff must report changed files, exact commands and results, documentation
  impact, skipped or unverified cases, and the next user-visible action.
- Unverified after this plan: highlight accuracy on real PDFs, and real chat
  through the hub SDK. Both need the work machine and approved documents.

## 6. Execution ledger

| Task | Release | Execution state | Evidence / next action |
| --- | --- | --- | --- |
| T1 | RELEASED | DONE | 3 tests (synthetic PDF builder and seeding). |
| T2 | RELEASED | DONE | 18 tests (`test_viewer_locations`). |
| T3 | RELEASED | DONE | 7 tests (`test_page_render`). |
| T4 | RELEASED | DONE | 21 tests with T5 (`test_view_api`, view and chat routes). |
| T5 | RELEASED | DONE | Done with T4; same test module. |
| T6 | RELEASED | DONE | 26 tests with T7 (`test_ui_client`, `test_ui_smoke`). |
| T7 | RELEASED | DONE | Done with T6; manual browser check on a seeded synthetic job 2026-11-11: highlights on the changed text for a modified and a deleted finding. Real-PDF highlight accuracy and live chat not verified. |
| T8 | RELEASED | DONE | Documentation updated; harness validation and doc tests run. |
