# Phase 2 workflow

Local orchestration: open the API + workflow MCP, submit/poll a job, then bind
Q&A to it.

Read first: `local_reviewer.py` (has a `__main__` guard; run with
`python -m document_compare.phase2.workflow.local_reviewer`).

Input/output: reads two selected PDFs from `input/`, leaves outputs under
`workspace/jobs/<job_id>/`.

Dependencies: `phase2/api` (child process) and `phase2/mcp` clients; `asyncio`.

Check: `python -m unittest tests.phase2.test_local_flow`.
