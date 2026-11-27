# Migration: chat agent on LangGraph + smarter answers (uncommitted work on top of `3663b4e`)

For the coding agent on the work PC. Bring the work repo from `3663b4e`
(viewer latency T1/T2, migrated by hand) to the LangGraph agent. Delivered inside
the **ZIP download** of branch `feat/german-impact-classification`.

20 files: 16 modified, 4 added (see `changed-files.txt`).

## What it contains

1. **Agent on LangGraph** (`phase2/agent/controller.py`). Graph
   `START → prepare → plan → act → [search_fallback] → plan … → check → finish`.
   The JSON planner protocol and the three tools (summarize, search, details) are unchanged;
   there is still no native tool calling. Step limit and error messages are as before.
2. **Search fallback without an extra LLM call.** When a search finds nothing, the agent tries up to 3 variants:
   - without change words („geändert“, „neu“ …);
   - stemmed („Fristen“ → „Frist“);
   - the longest term.
3. **Prefetch.** For „Erklär diese Änderung“ with a selected finding, the `details` of that finding are loaded before the planner runs.
4. **Lean observations** (`agent/observations.py`, new). The model no longer sees snapshot, limits, hashes or `<clipped>` token lists. A new `change_brief` carries the edits Alt→Neu, the changed numbers, pages, impact and review state. Citation checks still use the full result.
5. **Answer playbook + self-check.** Answers follow a fixed shape for each intent. One retry is made when the answer:
   - cites IDs that the tools never returned;
   - cites no ID at all;
   - is a raw JSON dump.
6. **Streaming.** New `POST /jobs/{job_id}/chat/stream` (NDJSON: `step`, `fallback`, `final`, `error`). The UI shows live status lines („Suche nach …“, „Lese CHANGE-… “).
   - `/chat` keeps all of its keys and adds one: `suggestions` (up to 3 follow-up questions, shown as buttons).
7. **Help text** in `agent/help_de.py` explains the page labels, the dashed marker, fully deleted pages and the tree order.
8. **`tools/agent_eval.py`** (new) runs 10 German test questions on one job. See Step 3.

Reader and MCP are **not changed** (frozen). The canonical result is unchanged, so there is no re-run of the pipeline.

**New dependency: `langgraph>=1.2,<2`** (in `requirements.txt`).

## Step 0 — prerequisite

The work repo must contain `3663b4e` (`viewer/artifact_cache.py` exists, `reviews_by_change` is indexed).

## Step 1 — apply

```bash
git status                       # clean, on a new branch
python <unzipped>/migration/2026-11-27-langgraph-agent/apply_migration.py --repo .
python <unzipped>/migration/2026-11-27-langgraph-agent/apply_migration.py --repo . --apply
pip install "langgraph>=1.2,<2"
python -c "import langgraph, langchain_core; print('ok')"
```

If pip cannot reach the index, use the company mirror or proxy. Do not add other packages.

## Conflict rules

- `phase2/api/service.py` (CRLF): the new parts are `_prepare_chat`, `_chat_kwargs`, `_chat_payload` and the `/chat/stream` route. Keep the work-PC way of creating the TIA client and gateway.
- `phase2/ui/texts.py`: keep the work-PC texts and add the `CHAT_STEP_*`, `CHAT_COMPLETE` and suggestion texts.
- `phase2/agent/controller.py`: take the package version as a whole.
- `tests/phase2/test_view_api.py`: keep both sides.
- Keep each file's line endings; the script does this.

## Verify

```bash
python -m compileall -q src tests tools
python tools/harness/validate.py
$env:PYTHONPATH="src"; python -m unittest discover -s tests -t .
```

There must be no new failures compared with the run before applying (WinError 1314 symlink is a known environment error).

**Tested off-site.** The simulated work PC was a `3663b4e` checkout with all files converted to CRLF and local edits in `service.py`, `texts.py` and `test_view_api.py`. Results:
- 13 replaced, 4 added, 3 merged_clean, 0 conflicts;
- apart from the kept local edits, the result equals the package;
- CRLF was kept and a second run changes nothing;
- the full suite gives 1167 tests with only the 6 known environment failures.

## Step 3 — test with the real TIA model (loopback only)

Start the API and UI as usual, then:

```bash
python tools/agent_eval.py --job <job_id>
```

- stdout shows only a table (seconds, steps, cited and unverified counts, partial);
- the full report goes to `workspace/agent_eval/<timestamp>.md`, which git ignores and which stays on the work PC;
- a custom question set can be given with `--questions file.txt` (one question per line).

Checks in the UI:
1. „Was sind die wichtigsten Änderungen?“ shows live status lines and an answer with **bold IDs** and suggestion buttons.
2. Select a finding and ask „Erklär diese Änderung.“: the first status is „Lese CHANGE-… “, and the answer has Alt/Neu quotes with pages.
3. „Welche Fristen wurden geändert?“: if the literal search is empty, the status „Keine Treffer für …, versuche …“ appears.

**Not verified off-site:** behaviour with the real TIA model, and the UI in a real browser (AppTest only).

No commit or push to the company remote without the user's go-ahead.
