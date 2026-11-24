# Migration: review presets + context-aware chat assistant (1 commit)

For the coding agent on the work PC. Bring the work repo from the previous
migration (`bbc17b5`, package `2026-11-24-german-impact-classification`) up to
`19db943`. Delivered inside the **ZIP download** of branch
`feat/german-impact-classification`.

What it contains (21 files: 20 modified, 1 added — see `changed-files.txt`):

1. **Review presets UI** — one „Ansicht“ choice (Wichtige Änderungen / Alle
   inhaltlichen Änderungen / Offene Prüffälle / Alles) replaces Kategorie,
   Auswirkung and „Nur mit Prüfbedarf“; blocks always grouped; local
   reading-order reorders hidden with a count caption. This is the **same
   specification** the work-PC bot was given on 2026-11-24.
2. **Chat assistant** — `/chat` accepts optional `history` (≤6 turns) and
   `context` (selected finding, preset, section); German product help
   (`agent/help_de.py`); answers in the user's language; per-finding
   uncertainty; partial answer on the last step; cited IDs verified
   (`cited`, `unverified_ids`); sanitized Markdown with a button per cited ID.
   Still exactly the three reader tools. MCP is not changed (frozen).

No new dependencies, no config/env changes, API changes are additive.

## Folder contents

- `files/` — new versions (at `19db943`), `base/` — `bbc17b5` versions (merge ancestor).
- `apply_migration.py` — per-file 3-way merge, same tool as last time.
- `combined-bbc17b5..19db943.diff` — reference only (`git apply --3way` cannot work without shared history).

## Step 0 — the presets the work bot already implemented (read first)

The chat assistant depends on **this package's** preset code (`f_preset`,
`review.selected_section`, `review.select_citation`). Two copies of the same
feature would conflict, so:

- **Work-bot preset change NOT committed yet** (recommended): discard it in the
  UI files, then apply this package:
  ```bash
  git status
  git checkout -- src/document_compare/phase2/ui/review.py src/document_compare/phase2/ui/texts.py src/document_compare/phase2/ui/README.md tests/phase2/test_ui_smoke.py tests/phase2/test_change_blocks.py
  ```
  Keep any work-bot edits in other files only if they are unrelated to presets.
- **Already committed:** keep the commit, run the script, and in
  `merged_with_conflicts` UI files take the **package side (`19db943`)** for
  preset logic and tests, while keeping any unrelated work-PC UI code.

## Step 1 — apply

```bash
git status                     # clean (except what Step 0 says), on a new branch
python <unzipped>/migration/2026-11-25-review-presets-and-chat-assistant/apply_migration.py --repo .
python <unzipped>/migration/2026-11-25-review-presets-and-chat-assistant/apply_migration.py --repo . --apply
```

Report categories: `replaced`, `added`, `already_applied`, `merged_clean`,
`merged_with_conflicts` (markers `<<<<<<< work-pc` … `>>>>>>> 19db943`).

## Conflict rules

- `agent/prompts.py`: the planner rules were rewritten. Take the package's
  PLANNER_SYSTEM rules, then re-add work-PC specifics (e.g. the work-PC
  `context` argument in the `search(...)` action line and any work-PC tool
  arguments). Never drop a safety rule from either side.
- `agent/controller.py`, `api/service.py` (`/chat`): keep work-PC behaviour
  and add history/context/partial/cited/unverified_ids. If the work PC changed
  how the TIA client or gateway is created, keep the work-PC way.
- UI files: see Step 0. All user-facing text German, in `ui/texts.py`.
- Keep line endings of each file (the script does this). Do not touch
  `phase2/mcp/`, the reader or Phase 1.

## Verify

```bash
python -m compileall -q src tests
python tools/harness/validate.py
$env:PYTHONPATH="src"; python -m unittest discover -s tests/phase2 -t .
```

Compare `tests/phase2` with the run before applying: no new failures
(WinError 1314 symlink = known environment error). Off-site: full suite 1092
tests, the 7 known failures only; on a simulated work PC (CRLF working tree +
local edit) the script reproduced `19db943` exactly.

Manual, loopback only, with TIA configured: open a job, select a finding,
ask in the chat panel

1. „Was bedeutet Redaktionell?“ → answer from help, no IDs needed.
2. „Erklär diese Änderung.“ → answer about the selected finding, its ID as a button.
3. „Und welche wichtigen Änderungen gibt es im selben Abschnitt?“ → follow-up uses history/context.
4. A question in English → English answer.

**Not verified anywhere:** behaviour with the real TIA model (off-site tests use
a scripted planner). Known weakness, next package: search is literal
(„Fristen geändert“ finds nothing while „Frist“ finds results).
No commit/push to the company remote without the user's go-ahead.
