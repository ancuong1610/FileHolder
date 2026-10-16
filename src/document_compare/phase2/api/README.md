# Phase 2 API

The FastAPI HTTP service and operational summary.

Read first: `service.py` (job create/status/report/artifact endpoints) and
`summary.py` (operational summary).

Input/output: accepts uploads, runs the Phase 1 pipeline into
`workspace/jobs/<job_id>/`, serves artifacts; loopback-only.

Dependencies: `phase1/pipeline`, `shared`; `python -m uvicorn
document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321`.

Check: `python -m unittest tests.phase2.test_service`.
