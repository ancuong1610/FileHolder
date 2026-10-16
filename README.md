# Document Comparison Service

Local tool that compares two versions of the same PDF and reports the changes,
with an optional read-only agent that answers questions over the saved result.
Scope: two-version document comparison only.

New here? Read [docs/architecture.md](docs/architecture.md) for the layout and
15-stage map, and [docs/review-guide.md](docs/review-guide.md) to route a task to
its owning file and check. For a real environment, see
[docs/operations/company-handover.md](docs/operations/company-handover.md).

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
supports paths with spaces. Inspect `output/comparison_redline.pdf` and
`output/comparison_inline.pdf` afterwards.

## Commands

```bash
python -m pip install -e .
python -m document_compare demo --fixture internal_policy_report --root "workspace/demo run"
python -m document_compare run --root JOB_ROOT --from-stage 02 --to-stage 15
python -m document_compare.phase2.workflow.local_reviewer
python -m document_compare.phase2.agent.mock_reader --out workspace/reader-mock
python -m document_compare.phase2.mcp.workflow_server
python -m document_compare.phase2.mcp.reader_server
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m unittest discover -s tests
```

Tests may also run by area, e.g. `python -m unittest tests.phase1.test_reconcile`
or `python -m unittest tests.phase2.test_reader`.

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
  semantic accuracy is claimed.
- `workspace/` and `input/*` (except `input/.gitkeep`) are gitignored; keep
  credentials in `.env` only and never commit or paste them.
