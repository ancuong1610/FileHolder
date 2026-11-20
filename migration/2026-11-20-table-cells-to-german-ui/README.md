# Migration: table-cell highlights → German UI (3 commits)

For the coding agent on the work PC. Bring the compare frontend from
`6f27f7b` up to `9ad3757`. The work PC cannot clone GitHub; this folder
arrives inside the **ZIP download** of branch `feat/compare-frontend`
(https://github.com/ancuong1610/FileHolder → branch `feat/compare-frontend` →
Code → Download ZIP).

| # | Commit | What |
|---|---|---|
| 1 | `736a9b9` | Table findings highlight the changed **cell** (thin table frame + tinted cell); review items, LLM opinions; `viewer/import_workspace.py` |
| 2 | `55c9aec` | Resizable structure panel |
| 3 | `9ad3757` | German UI (`ui/texts.py`), section order by document TOC then natural labels (numbers, letters, MaRisk `AT`/`BT`, Roman), search by change ID, copyable job ID |

21 files: 4 added, 17 modified — see `changed-files.txt`.

## Folder contents

- `files/` — the **full new version** (at `9ad3757`) of every added/modified file, same paths as the repo.
- `combined-6f27f7b..9ad3757.diff` — the whole change as one diff.
- `000{1,2,3}-*.patch` — the three commits separately.

## Precondition

The work repo already has the frontend at `6f27f7b`: `phase2/viewer/outline.py`
exists and `phase2/viewer/locations.py` has **no** `role="cell"`.
If `outline.py` is missing, stop and tell the user (earlier commits are needed first).

Do not unzip over the work repo. Unzip to a separate folder and copy from there.
Work on a new branch: `git switch -c feat/compare-frontend-cells-german`.

## Method 1 — apply the diff (preferred: keeps work-PC changes)

From the work repo root:

```bash
git apply --3way --check <unzipped>/migration/2026-11-20-table-cells-to-german-ui/combined-6f27f7b..9ad3757.diff
git apply --3way <unzipped>/migration/2026-11-20-table-cells-to-german-ui/combined-6f27f7b..9ad3757.diff
```

Resolve any conflict markers by hand (rules below). If you prefer per-commit:
`git am --keep-cr --3way 0001-*.patch 0002-*.patch 0003-*.patch`
(`--keep-cr` is required because `service.py` is CRLF).

## Method 2 — copy files (if the diff will not apply)

1. Copy these over the work repo as-is (Phase 2 frontend only, owned by this change):
   - `files/src/document_compare/phase2/ui/*`
   - `files/src/document_compare/phase2/viewer/*`
   - `files/tests/phase2/*`
   - `files/docs/plans/2026-11-20-frontend-followups-codex.md`
2. **Do not overwrite** `src/document_compare/phase2/api/service.py` (backend may
   differ). Hand-edit the PNG route `/jobs/{job_id}/view/pages/{side}/{page}.png`:
   ```python
   roles: list[str] = []                      # next to the other locals
   style = "review" if kind == "review" else style_for(str(located.get("change_type") or ""), side)
   uncertain = uncertain or bool(located.get("needs_review"))
   on_page = [r for r in regions if r.get("page_number") == page]
   polygons = [r["polygon"] for r in on_page]
   roles = [r.get("role") or "element" for r in on_page]
   png = render_page(pdf_path, page, polygons, style=style, uncertain=uncertain, scale=scale, roles=roles)
   ```
   Keep CRLF line endings (`file service.py` must still say CRLF).
3. Docs (`docs/contracts/view-endpoints.md`, `docs/operations/*.md`, the two
   `README.md`): merge the new sections by hand instead of overwriting, if the work
   copies differ from `6f27f7b`.

## Conflict rules

- `outline.py` reads `structure_evidence.toc` (`usable`, `entries[].body_element_id`,
  `entries[].status == "linked"`) and `sections[].heading_element_id`. If the work
  backend names these differently, adapt `outline.py`, never Phase 1.
- Cell highlight needs `table_changes[].cell_changes[].{old,new}.primary_polygon` /
  `primary_page` in `comparison_result.json`; otherwise it falls back to the whole table.
- Never change API routes, error codes or the English `change_type` values the UI
  sends; German is display text only (`ui/texts.py`).

## Verify

```bash
python -m unittest discover -s tests/phase2
python tools/harness/validate.py
python -m unittest discover -s tests
```

Off-site: phase2 308 tests OK, harness valid; on a clean `6f27f7b` both
`combined…diff` and the patches reproduce `9ad3757` exactly. Then manual,
loopback only:

```bash
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m streamlit run src/document_compare/phase2/ui/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Expect: German labels; a table finding shows a thin table frame plus one tinted
cell on both sides; searching `CHANGE-000001.` finds that change;
"Abschnittsnummer" orders MaRisk sections `AT 1 < AT 4.3 < BT 1`.

Not yet verified anywhere: TOC order on a real MaRisk document; cell highlights
on real PDFs. No commit/push to the company remote without the user's go-ahead.
