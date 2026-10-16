# Operations

Install, run and acceptance guidance for a real (company-machine) environment.

Read first: [company-handover](company-handover.md) — Windows install, the real
PDF/agent run and the manual acceptance checklist.

Input/output: this folder is prose only; it produces no artifacts. Runtime jobs
land under the ignored `workspace/jobs/<job_id>/`.

Dependencies: describes `phase1/pipeline`, `phase2/api`, `phase2/workflow`,
`phase2/mcp`; commands use the `document_compare` namespace only.

Check: `python -m unittest discover -s tests` after an editable install.
