# Company-machine handover

## Scope

Local comparison of two versions of a digital PDF, using the existing internal DI extraction and provider integration. This is a verified offline handover candidate, not a claim of verified company-network, Windows, provider, original-source extraction or universal semantic accuracy. Final acceptance is your real-data review on the company machine.

The additional `comparison_inline.pdf` removes only corroborated line-end hyphenation noise. Deleted text is struck red; inserted text is blue. Permanent hyphens, quantitative changes, negation and uncertain differences remain visible. A dictionary or LLM does not invent the original German word. If DI has lost the line-break evidence, the view stays conservative. `comparison_redline.pdf` remains the evidence-oriented original view.

## Install on Windows (no activation required)

Use the approved company Python/package sources and certificate settings. Do not disable TLS verification or organizational execution policies.

```powershell
git clone --branch multimodal-difftool-phase1 --single-branch https://github.com/ancuong1610/FileHolder.git
cd FileHolder
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip check
```

No activation is required: call the venv interpreter path directly as above.

If the repository already exists, preserve local changes and `.env`; switch/pull this branch using your normal Git workflow rather than cloning over existing work.

Install `bbk_ai_hub_sdk` separately from the **approved internal index** for the current reviewer chat/internal Stage 13 provider. Public requirements intentionally do not include it. Follow README provider requirements; configure a local `.env` from `.env.example` without overwriting an existing configuration. Keep keys and internal endpoints inside the company environment. Do not paste credentials into reports.

## Verify before real documents

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m document_compare demo --fixture internal_policy_report --root "workspace\handover smoke"
```

The demo destination must be fresh. It is synthetic, mock-only and does not call DI/model providers. Inspect both PDFs in its `output` directory. Eleven optional private-fixture tests are skipped in a public checkout; the synthetic/adversarial engine, renderer, service and MCP tests remain mandatory. Development raw DI/PDF samples are deliberately not published.

## Real comparison and agent

1. Put the old and new PDFs in `input/`. Retain the correct old/new orientation when prompted. Limits: distinct selected files, PDF signature, at most 50 MiB per file.
2. Run `.\.venv\Scripts\python.exe -m document_compare.phase2.workflow.local_reviewer`. This is the real extraction/provider workflow, not mock mode; normal company provider permissions/costs apply.
3. Open the printed redline link. The same job also provides `/jobs/<job_id>/artifacts/comparison_inline.pdf` and `/jobs/<job_id>/artifacts/inline_pdf_audit.json` on the configured loopback API (default `http://127.0.0.1:8321`). Outputs remain under `workspace/jobs/<job_id>/output/`.
4. Ask the agent for changed clauses/numbers/tables, exact old/new values, change IDs/pages, and which items remain unresolved. A review-required item must not be represented as approved.
5. Type `quit`/`exit` to stop the local reviewer; job artifacts remain for inspection.

## Manual acceptance checklist

- Ordinary German/English paragraphs, Umlauts, long clauses and multi-page content.
- German line-end split words, a real compound hyphen, and a mixed wrap + actual wording change.
- Small numeric changes, zero, negatives, decimal comma/dot, percentages, comparison signs and units. Both old and new values must remain visible.
- Negation and obligation changes, e.g. `nicht`, `muss` versus `kann`; these must not disappear as formatting.
- Changed, added and deleted tables, merged cells and continued tables with repeated headers. Inspect cell values, source IDs and old/new orientation in both PDFs.
- A truly unchanged pair and layout-only changes: do not expect source extraction noise to be certified away without evidence.
- Agent answers reference the selected job and source/change IDs; ambiguity or missing evidence is explicit. Ask about a nonexistent change to check that the agent does not invent one.

For a defect, retain the job locally and note the job ID, PDF page, change/table/cell ID, expected value and observed value. Do not upload company documents, raw DI, `.env` or provider logs outside the company environment. Unsupported/malformed extraction is an actionable error/review condition, not a successful empty comparison.

## Boundaries

This is loopback/local operation, not a multi-user deployment. Do not expose the service on a public/shared interface. Mac offline installation and synthetic checks do not prove Windows or company-provider behavior. Docling evaluation is a separate development workflow, not required on the company machine or a certificate of semantic correctness.
