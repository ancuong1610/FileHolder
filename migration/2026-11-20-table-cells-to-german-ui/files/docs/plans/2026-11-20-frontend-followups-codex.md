# Work order: compare frontend follow-ups (for Codex)

Lead: Claude (reviews the final diff). Implementer: Codex. Repo: this folder.
Read first: `AGENTS.md`, `src/document_compare/phase2/ui/README.md`,
`src/document_compare/phase2/viewer/outline.py`, `src/document_compare/phase2/ui/review.py`.

## Already done (do not redo)

- Table findings already point at the changed cell: `viewer/locations.py::_table_side`
  returns a thin `role="table"` frame plus tinted `role="cell"` regions for every
  non-UNCHANGED cell that has `primary_polygon`. Falls back to the whole table only
  when a cell has no polygon.
- Search by change ID: `ui/review.py::_matches` now also matches `record_id`
  (substring, punctuation such as `` ` . , ; : ( ) `` stripped). Root cause: the
  sidebar filter only searched the label and section titles, never the ID the chat
  quotes (e.g. `CHANGE-000001`).
- Job ID is shown with a copy button above a finished result (`ui/app.py::main`).

## Task A — section order from the document's own TOC (no AI)

Problem: "Section number" in `ui/review.py::order_sections` sorts by the first
integer in the title (`_FIRST_INT`). MaRisk-style documents number sections with
letters (`AT 1`, `BT 2`, `A`, `B`, `C`, `I`, `II`…), so the order breaks.

Do:
1. In `viewer/outline.py`, when `document.structure_evidence.toc.usable` is true
   on a side, attach a `toc_rank` (0-based position in `toc.entries`) to each
   section that a TOC entry links to (check the entry fields in Phase 1
   `src/document_compare/phase1/stages/s05_anchors.py` /
   `structure/reconstruction.py`; link via `heading_element_ids` or the entry's
   linked section/heading ID). Prefer the new side, else old. Additive field only.
2. In `ui/review.py`, replace the integer sort with this key order:
   a. `toc_rank` when present (the document's own TOC wins);
   b. else a natural section key parsed from the title prefix: dotted tokens
      where each token is a number, a Roman numeral, or letters
      (`A` < `B` < … , `AT` / `BT` prefixes kept as letter tokens), e.g.
      `BT 1.2.3` → `("bt", 1, 2, 3)`;
   c. sections without any label keep document (reading) order, after labelled ones.
   Keep the "Document order" option unchanged. Update `numbers_ascending` to use
   the same key.
3. Tests (`tests/phase2/test_ui_smoke.py` or a new `test_ui_order.py`): numeric
   `2 < 10`; letters `A < B < C`; MaRisk `AT 1 < AT 4.3 < BT 1`; Roman `II < IV`;
   TOC rank beats the title key; unlabelled keep relative order. Outline test with a
   fixture structured JSON whose `toc.usable=true`.
   Note: bundled fixtures have `toc.detected=false`, so build a small synthetic one.

## Task B — German interface

Translate every user-visible string in `ui/app.py`, `ui/review.py`, `ui/chat.py`
(titles, buttons, captions, notices, filter labels, radio options, errors,
empty states) to German. Display labels for change types: MODIFIED → Geändert,
DELETED → Gelöscht, ADDED → Hinzugefügt; Old/New → Alt/Neu.

Rules: translate only display text. Do not change API routes, error codes, query
values, `change_type` values sent to the API, or session-state keys. Keep options
mapped internally (e.g. a dict label→value) so filters still send the English
values. Put strings in one module `ui/texts.py` (German constants) so it can be
reviewed in one place. Update the tests' expected strings accordingly.

## Constraints

- Phase 1 never imports Phase 2; services stay on 127.0.0.1; no new dependencies.
- Keep each file's line endings (`service.py`, `pyproject.toml` are CRLF).
- No commit/push; no real provider calls.

## Verify

```bash
.venv/bin/python -m unittest discover -s tests/phase2
.venv/bin/python tools/harness/validate.py
```

Then manual: start `compare-api` and `compare-ui` (`.claude/launch.json`), open
job `b452d37d3cfd`; check German labels, ordering toggle, ID search
`CHANGE-000001`, and a table finding highlighting one cell.

Report back: changed files, test output, anything not verified.
