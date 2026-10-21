# Document Comparison Service

Local tool that compares two versions of the same PDF and reports one complete
result: supported changes, uncertain differences with reasons, and extraction
limitations. There is no approval task or queue to finish a comparison; the
optional read-only agent answers questions over the saved result. Scope:
two-version document comparison only.

New here? Read [docs/architecture.md](docs/architecture.md) for the layout and
15-stage map, and [docs/review-guide.md](docs/review-guide.md) to route a task to
its owning file and check. For a real environment, see
[docs/operations/company-handover.md](docs/operations/company-handover.md).
For separate Phase 1 / saved-workspace Phase 2 / combined runs, see
[phase testing](docs/operations/phase-testing.md). Coding agents should start
with [AGENTS.md](AGENTS.md), including explanation, investigation and safe testing.
For the full stage-by-stage logic and data transformations, read
[Knowledge Transfer — Phase 1](docs/KnowlesgeTranser%20Phase%201.md).

## Install (editable)

```bash
python -m pip install -e .
```

Dependencies come from `requirements.txt` (single source via `pyproject.toml`
dynamic metadata). The internal `bbk_ai_hub_sdk` is intentionally not included;
install it separately for the default Stage 13 provider and the local chat.

## Five-minute offline demo

No credentials, network or PDF uploads. It seeds a bundled synthetic DI pair and
runs Stages 02–15 with the mock evaluator:

```bash
python -m document_compare demo --fixture internal_policy_report --root "workspace/demo run"
```

The root must be fresh; the demo refuses to overwrite existing `output/` and
supports paths with spaces. Inspect `output/comparison_redline.pdf`,
`output/comparison_inline.pdf` and `output/bu_comparison_redline.html` afterwards.
The primary HTML/PDF report includes supported changes, uncertain differences
with plain-language reasons, section/page grouping and a separate technical
details area or appendix. It is not an accepted-only summary.

## Commands

```bash
python -m pip install -e .
python -m document_compare demo --fixture internal_policy_report --root "workspace/demo run"
python -m document_compare run --root JOB_ROOT --from-stage 02 --to-stage 15
python -m document_compare.phase2.workflow.local_reviewer --phase phase1 --workspace JOB_ROOT
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace JOB_ROOT --check-only
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace JOB_ROOT
python -m document_compare.phase2.workflow.local_reviewer --phase both --workspace FRESH_JOB_ROOT
python -m document_compare.phase2.workflow.local_reviewer
python -m document_compare.phase2.agent.mock_reader --out workspace/reader-mock
python -m document_compare.phase2.mcp.workflow_server
python -m document_compare.phase2.mcp.reader_server
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m unittest discover -s tests
```

Tests may also run by area, e.g. `python -m unittest tests.phase1.test_reconcile`
or `python -m unittest tests.phase2.test_reader`.

Explicit phase modes load the repository `.env`; `--workspace` overrides
`CRR_PROJECT_ROOT`. Phase 2 reads the selected completed workspace in place,
without rerunning Phase 1. `--check-only` is offline; saved-result chat is real
and uses the local reader, not HTTP/MCP. No flags keeps the original combined
upload/API/MCP/chat flow. See the testing guide for credentials and acceptance.

## Old → new commands (no compatibility shim)

The old Python namespaces and CLI entry points are intentionally removed. There
is no `compat/` package, no legacy wrappers or aliases, and no duplicate
implementations. Replace old invocations:

| Old | New |
| --- | --- |
| `python run_local.py` | `python -m document_compare.phase2.workflow.local_reviewer` |
| `python -m crr_compare demo …` | `python -m document_compare demo …` |
| `python -m crr_compare eval …` | `python -m document_compare eval …` |
| `python -m crr_compare.mcp_server` | `python -m document_compare.phase2.mcp.workflow_server` |
| `python -m crr_compare.reader_mcp_server` | `python -m document_compare.phase2.mcp.reader_server` |
| `python -m uvicorn crr_compare.service:app …` | `python -m uvicorn document_compare.phase2.api.service:app …` |
| `python -m agent_poc.mock_reader …` | `python -m document_compare.phase2.agent.mock_reader …` |

Runtime environment keys keep their legacy `CRR_` prefix (a preserved external
config contract), even though the old Python CLI is gone; this is not a config
migration and no new keys are introduced. Artifact filenames and HTTP/MCP
contracts are unchanged.

## Troubleshooting

- Editable install fails with a `distutils`/`core` assertion under this
  environment's setuptools/distutils shim. Local workaround after installing
  `requirements.txt`: `SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install
  --no-deps --no-build-isolation -e .`. This is environment-specific; do not
  disable TLS verification or organizational execution policies.
- Missing internal SDK: install `bbk_ai_hub_sdk` from your approved internal
  index (needed for Stage 13 `bbk_ai_hub` and the local chat).
- Port conflict on `8321`: stop the other process or set `CRR_API_BASE` to
  another loopback port.

## Scope and limits

- Local pilot only — not a production architecture or deployment decision.
  Database, authentication and shared deployment remain open.
- Contract/integration evidence only; no real-LLM reasoning or real-company
  semantic accuracy is claimed. Synthetic tests do not prove company-PDF or
  Windows acceptance.
- Native PDF bookmarks are optional structural evidence, not a printed table of
  contents or authoritative hierarchy. Missing, partial, invalid or stale
  outline evidence falls back to the existing reconstruction path.
- Source-PDF strikethrough detection is explicitly deferred. Report
  strike-through marks compare old/new text; they are not source formatting.
  Private table-extraction quality remains unverified without a representative
  failing sample, and the Stage 10 all-pairs performance issue is not repaired
  in this slice.
- `workspace/` and `input/*` (except `input/.gitkeep`) are gitignored; keep
  credentials in `.env` only and never commit or paste them.
