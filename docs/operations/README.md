# Operations

Install, run and acceptance guidance for a real (company-machine) environment.

Read first: [company-handover](company-handover.md) — Windows install, the real
PDF/agent run and the manual acceptance checklist. For team coding-agent setup
with GitHub Copilot or GitLab Duo, read the
[agent harness quickstart](agent-harness-quickstart.md).

Input/output: this folder is prose only; it produces no artifacts. Runtime jobs
land under the ignored `workspace/jobs/<job_id>/`.

Dependencies: describes `phase1/pipeline`, `phase2/api`, `phase2/workflow`,
`phase2/mcp`; commands use the `document_compare` namespace only. The harness
validator is development tooling and not a runtime dependency.

Check: `python -m unittest discover -s tests` after an editable install; use
`python tools/harness/validate.py` for harness file/link structure.
