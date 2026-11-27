# Phase 2 API

The FastAPI HTTP service and operational summary.

Read first: `service.py` (job create/status/report/artifact endpoints) and
`summary.py` (operational summary).

Input/output: accepts uploads, runs the Phase 1 pipeline into
`workspace/jobs/<job_id>/`, serves artifacts; loopback-only.

Dependencies: `phase1/pipeline`, `shared`; `python -m uvicorn
document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321`.

The API process shares a read-only cache of parsed canonical and document JSON
for completed jobs. Each entry is keyed by resolved path, nanosecond modification
time, and file size. The least recently used entries are evicted at a limit of
six artifacts per job configured by `CRR_ARTIFACT_CACHE_JOBS` (default 2, for a
total of 12 entries). A large job can use a few hundred MB of memory. Set the
variable to 0 to disable caching.

Check: `python -m unittest tests.phase2.test_service`.
