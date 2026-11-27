# Issue + plan: reviewer clicks are slow (server-side latency)

Status: PROPOSED (no task released yet). Owner: Claude (lead), implementer per task.

## Issue

**Symptom.** In the Streamlit reviewer every click on a finding (or "Weiter")
takes about half a second off-site and noticeably longer on the work PC. Opening
a job takes about one second.

**Measured** (off-site, 2026-11-27, real pair with a 19 MB `comparison_result.json`,
about 640 findings; timings only, no document content printed). Streamlit AppTest with
an in-process client over the real service functions, plus cProfile of one
selection:

| Step | Total | of which API | API calls |
|---|---|---|---|
| open job | 990 ms | 895 ms | 6 |
| select a finding (7 samples) | 490–680 ms | 440–640 ms | 5 |
| re-select the same finding | 51 ms | 0 ms (st.cache_data) | — |

Streamlit itself costs about 50 ms per rerun. **About 90 % of a click is the API.**

One selection = `read/details` + `view/locations` + 2 × `view/pages/{side}/{page}.png`.
The profile of one selection (about 1.08 s under the profiler) breaks down as:

| Cost | Share | Where |
|---|---|---|
| `reviews_by_change` → `linked_change_ids`, 1578 calls | ~55 % | `viewer/locations.py:176-200` |
| `json.loads` of the same artifacts on every request | ~28 % | `api/service.py:_load_canonical`, `_structured_elements`, `viewer/locations.py:_load_document`, `viewer/outline.py:_load_document` |
| `snapshot_fingerprint` (json.dumps + sha256 of canonical) | ~5 % | `reader/result_reader.py:172` |
| PDF page render + PNG encode | ~3 % (15 ms/page) | `viewer/page_render.py` |

**Root causes.**
1. `linked_change_ids(review, changes)` rebuilds a dict of *all* changes and
   scans *all* changes for `source_candidate_id` for **each** review, and
   `reviews_by_change` runs it for every review on **every** `resolve_locations`
   call. This makes the work O(reviews × changes) per request; the PNG endpoint
   pays it once per side.
2. The service is stateless per request: each request re-reads and re-parses
   the 19 MB canonical result and the 7–8 MB structured documents (several
   times per request).

**Rejected alternative: cache the source PDFs and JSON in the browser (frontend).**
This attacks the ~3 % render cost only. Streamlit renders in Python, so browser
caching would require rewriting the viewer as a custom pdf.js component that
re-implements locations and overlays in JavaScript. It would also put the
confidential PDFs and the 19 MB result into browser storage. High cost, low gain,
and a data-handling risk.

## Goal / scope

Make a finding selection fast without changing any API response, canonical
field, reader/MCP output or UI behaviour. Target: **API ≤ 150 ms per selection,
open job ≤ 400 ms** on the same off-site job (re-measure on the work PC; use
the ratio, not the absolute number).

Non-goals: browser-side caching, pdf.js, changing the reader/MCP contract,
changing render scale/quality, new dependencies, background workers.

## Accepted decisions

- D1. Fix the algorithm first (T1); it is pure, safe and gives the biggest gain.
- D2. Cache **parsed** artifacts in the API process memory (T2), keyed by
  `(resolved path, st_mtime_ns, st_size)`. A re-run of a stage rewrites the file,
  which changes the key, so there is no stale data and no manual invalidation.
- D3. Bounded LRU: at most 2 jobs' worth of artifacts (canonical + 2 structured
  documents per job), configurable by `CRR_ARTIFACT_CACHE_JOBS` (0 = off).
  Expected memory is a few hundred MB per job for the largest real pair; document
  this in `phase2/api/README.md`.
- D4. Cached objects are shared and **must be treated as read-only**. Do not use
  `deepcopy` (it would cancel the gain). Instead, audit the call sites and add a
  test that asserts the cached object is byte-identical (via
  `snapshot_fingerprint`) before and after a full sequence of endpoint calls.
- D5. Derived indexes (`reviews_by_change` result, the `_Doc` element/page
  indexes in `locations.py`, the fingerprint) may be memoized on the same cache
  entry. The reader module stays frozen: memoize the fingerprint at the service
  level only if no reader change is needed; otherwise leave it (5 %).
- D6. The thread lock around the LRU only guards insert/evict. Parsing happens
  outside the lock; a duplicate parse under a race is acceptable.

## File / interface map

| File | Change |
|---|---|
| `src/document_compare/phase2/viewer/locations.py` | T1: index changes once in `reviews_by_change`; `linked_change_ids` keeps its signature and output (thin wrapper over the indexed helper). T2: `_load_document` via the cache. |
| `src/document_compare/phase2/viewer/outline.py` | T2: `_load_document` via the cache. |
| `src/document_compare/phase2/api/service.py` | T2: `_load_canonical`, `_structured_elements` via the cache. The validation in `_load_canonical` still runs, but only on a cache miss. |
| new `src/document_compare/phase2/api/artifact_cache.py` | T2: `load_json(path) -> Any` with LRU + key from `os.stat`. No logging of content. |
| `src/document_compare/phase2/ui/review.py` | T3 (optional): prefetch the next finding. |
| `tests/phase2/test_viewer_locations.py` | T1: equivalence test old vs new `reviews_by_change` on a synthetic canonical with evidence ids, `block_id` + `change_ids`, and shared `source_candidate_id`. |
| new `tests/phase2/test_artifact_cache.py` | T2: hit/miss, invalidation on rewrite (mtime/size change), LRU eviction, `CRR_ARTIFACT_CACHE_JOBS=0`, read-only fingerprint test across endpoints. |
| new `tools/perf/viewer_latency.py` | T0: timing script (prints timings and counts only). |

## Tasks

- **T0 — measurement tool.** Commit the AppTest timing script as
  `tools/perf/viewer_latency.py --job <id>`. It prints a per-step table of total,
  API and call counts, plus a cProfile top-15 by cumulative time with function
  names only. It prints no record text. Use it before and after each task, off-site
  and on the work PC.
- **T1 — fix `reviews_by_change`.** Build once per call: `existing` (change_id →
  change) and `by_candidate` (source_candidate_id → [change_id] in change order).
  Resolve each review against these indexes. Output order must stay identical
  (named ids first, then candidate matches, de-duplicated with
  `dict.fromkeys`). Expected: selection API time about halved.
- **T2 — parsed-artifact cache** per D2–D6. Expected: open job and
  selection drop by a further ~25–30 % of the original.
- **T3 (optional) — prefetch.** After rendering the viewer, warm
  `st.cache_data` for the next item in `flat` (details, locations, 2 PNGs). Run it
  in a background thread or after the render so the current click does not wait.
  Only do this if T1+T2 still miss the target on the work PC.

## Verification

```bash
python -m compileall -q src tests
python tools/harness/validate.py
PYTHONPATH=src python -m unittest discover -s tests -t .
PYTHONPATH=src python tools/perf/viewer_latency.py --job <id>
```

Acceptance:
- full suite: no new failures (known environment failures unchanged);
  MCP/reader tests unchanged and passing;
- every endpoint response for a sample of records is identical before and after
  (golden comparison on a synthetic job);
- latency table meets the targets above; report before/after numbers.

## Stop conditions

Stop and report if: a response would change; a cached object is mutated by
any endpoint (fingerprint test fails) and the fix is not local; the reader or MCP
would need changes; memory for one job exceeds about 1 GB.

## Ledger

| Task | State | Evidence |
|---|---|---|
| T0 | open | prototype in session scratchpad |
| T1 | open | — |
| T2 | open | prototype (memoized `json.loads`) gave 490–680 → 270–450 ms per selection |
| T3 | open | — |
