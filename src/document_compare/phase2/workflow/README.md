# Phase 2 workflow

Local orchestration: open the API + workflow MCP, submit/poll a job, then bind
Q&A to it (no-flag mode). Explicit phase modes can run the engine alone or
read a selected saved workspace locally without API/MCP/extraction.

Read first: `local_reviewer.py` (has a `__main__` guard; run with
`python -m document_compare.phase2.workflow.local_reviewer`).

Input/output: reads two selected PDFs from `input/`, leaves outputs under
`workspace/jobs/<job_id>/` in no-flag mode. With `--phase phase1|phase2|both
--workspace PATH`, use that workspace; Phase 2 is read-only.

`saved_workspace.py` validates saved artifacts and adapts the existing bounded
reader to the agent's three-tool gateway. `--phase phase2 --workspace PATH
--check-only` exercises that projection without credentials or model calls.
See [phase testing](../../../../docs/operations/phase-testing.md) for exact
commands, optional context, real-chat requirements and transport checks.

Dependencies: `phase2/api` (child process) and `phase2/mcp` clients; `asyncio`.

Check: `python -m unittest tests.phase2.test_local_flow tests.phase2.test_saved_workspace`.
