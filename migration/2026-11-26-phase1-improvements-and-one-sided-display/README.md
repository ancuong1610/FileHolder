# Migration: Phase 1 improvements + one-sided display (1 commit)

For the coding agent on the work PC. Bring the work repo from the previous
migration (`19db943`, package `2026-11-25-review-presets-and-chat-assistant`)
up to `add226f`. Delivered inside the **ZIP download** of branch
`feat/german-impact-classification`.

What it contains (24 files: 21 modified, 3 added — see `changed-files.txt`):

1. **T8 — page-split tables joined by geometry** (Stage 10). A table that
   continues on the next page without a repeated header is joined when: adjacent
   pages, same column count, previous table is the last body element on its page
   and the next is the first, column left edges within 2 % of page width, no
   heading in between, and (if both have headers) header similarity ≥ 0.50.
   New continuation reason `geometry_continuation`.
2. **T10 — section tree nesting** (Stage 05). Body headings that are not in the
   printed TOC are nested under the preceding TOC section
   (`parent_basis = "preceding_toc_section"`); validator checks parent cycles.
3. **T11 — whole-page changes** (Stage 14). New top-level `page_changes`
   (`PAGE-000001` …) when every body element of a page is deleted/added; member
   changes get `page_change_id`, blocks get `full_pages`; statistics key
   `page_changes`. UI label „Seite 34 vollständig gelöscht (Alt)“.
4. **T12 — one-sided display.** One-sided changes, tables and blocks carry
   `other_side_anchor` (`element_id`, `page`, `position` = `after` |
   `document_start`); page records carry `other_side_anchor_element_id`. The
   empty side now opens the anchor page with a **dashed marker** below the anchor
   element, caption „Neu · Seite 3 · gestrichelte Linie: hier entfernt“.
   Anchors skip moved elements, TOC↔body pairs and out-of-order pairs.
5. **T9 — page labels.** Finding rows show „S. x (Alt)“ / „S. y (Neu)“ from the
   finding's own page; table reviews get the table's source page.
6. **T13 — Knowledge Transfer Phase 1 rewritten**; its digest in
   `tests/integration/test_harness.py` re-pinned. Contract
   `docs/contracts/phase1-matching.md` and the plan
   `docs/plans/2026-11-24-german-impact-classification.md` updated.

All canonical-result changes are **additive** (existing fields and values
unchanged). Reader and MCP are **not changed** (frozen). No new dependencies,
no config/env changes. A re-run of Stage 14 is needed for existing jobs to get
`page_changes` / `other_side_anchor`; old jobs still open (no marker, section
context page as before).

## Folder contents

- `files/` — new versions (at `add226f`), `base/` — `19db943` versions (merge ancestor).
- `apply_migration.py` — per-file 3-way merge, same tool as the last packages.
- `combined-19db943..add226f.diff` — reference only.

## Step 0 — prerequisite

The work repo must already contain the previous package (`19db943`: review
presets + chat assistant). If not, apply
`migration/2026-11-25-review-presets-and-chat-assistant` first.

## Step 1 — apply

```bash
git status                     # clean, on a new branch
python <unzipped>/migration/2026-11-26-phase1-improvements-and-one-sided-display/apply_migration.py --repo .
python <unzipped>/migration/2026-11-26-phase1-improvements-and-one-sided-display/apply_migration.py --repo . --apply
```

Report categories: `replaced`, `added`, `already_applied`, `merged_clean`,
`merged_with_conflicts` (markers `<<<<<<< work-pc` … `>>>>>>> add226f`).

## Conflict rules

- `phase1/stages/s14_canonical_result.py` (CRLF): take the package version of
  `_matched_anchor_pairs`, `_in_order_pairs`, `_toc_element_ids`,
  `_walk_anchor_context`, `_add_other_side_anchors`, `_build_page_changes`;
  keep unrelated work-PC code.
- `phase2/ui/review.py`, `ui/texts.py`: keep work-PC UI code, add the package's
  `page_side` row label, anchor caption and `PAGE_SHORT_SIDE` /
  `ANCHOR_*` / `BLOCK_FULL_PAGE*` texts. All user-facing text German, in `texts.py`.
- `phase2/api/service.py`: only the marker block in `view_page_png` is new;
  keep the work-PC way of creating TIA client / gateway.
- `docs/KnowlesgeTranser Phase 1.md`: take the package version as a whole
  (otherwise the pinned digest test fails).
- Keep line endings of each file (the script does this). Do not touch
  `phase2/mcp/` or `phase2/reader/` beyond what the package changes.

## Verify

```bash
python -m compileall -q src tests
python tools/harness/validate.py
$env:PYTHONPATH="src"; python -m unittest discover -s tests -t .
```

Compare with the run before applying: no new failures (WinError 1314 symlink =
known environment error). Off-site: full suite 1132 tests, only the 6 known
environment failures. On a simulated work PC (`19db943` checkout with every
file converted to CRLF and a local edit in `ui/texts.py`) the script gave
20 replaced / 3 added / 1 merged_clean, the result equals `add226f` apart from
the kept local edit, a second run changes nothing, and the full suite gives the
same 1132 tests / 6 known failures.

Manual, loopback only: re-run a comparison (or Stage 14 on an existing job),
open it in the UI and check

1. a deleted paragraph: Neu pane shows a page with a red dashed line under the
   preceding matched paragraph, caption „… gestrichelte Linie: hier entfernt“;
2. an added paragraph: same on the Alt pane, „hier eingefügt“;
3. list rows show „S. x (Alt)“ for deletions and „S. y (Neu)“ otherwise;
4. a fully deleted page: block label „Seite N vollständig gelöscht (Alt)“.

**Known limits:** a fully deleted page with fewer than 3 elements has no block,
so its `PAGE-…` record is only in the canonical result. Phase 2 single-order
outline and the „Im Inhaltsverzeichnis, aber nicht im Text gefunden“ note are
not done yet. Behaviour on the real work-PC documents is not verified here.
No commit/push to the company remote without the user's go-ahead.
