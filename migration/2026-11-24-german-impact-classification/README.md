# Migration: German impact classification, moves, change blocks (4 commits)

For the coding agent on the work PC. Bring the repo from `c2e3717` (= code of
`9ad3757`, the previous migration "table cells → German UI") up to `bbc17b5`.
The work PC cannot clone GitHub; this folder arrives inside the **ZIP download**
of branch `feat/german-impact-classification`
(https://github.com/ancuong1610/FileHolder → branch
`feat/german-impact-classification` → Code → Download ZIP).

| # | Commit | What |
|---|---|---|
| 1 | `a51930f` | Phase 1: `impact` axis on every change (content / editorial / non_content / moved, priority, German signals), move detection (`moves`), paragraph split/merge grouping, type-family matching (Stage 03 list/paragraph typing can no longer break pairs), role-free header/footer detection confirmed by both documents. Phase 2: impact filter, move records, German labels |
| 2 | `5c818ac` | Change blocks: runs of ≥3 consecutive ADDED/DELETED become one `BLOCK-…` finding with one review entry; one-sided text is "Wichtig" only when short and normative; clause insertion/removal paired as MODIFIED (Stage 07 containment) |
| 3 | `3055d23` | Line-break hyphenation, list renumbering and fixed abbreviations are `editorial`; UI "Wichtig" filter; German review reasons |
| 4 | `bbc17b5` | Restores CRLF in two files (no content change) |

45 files: 8 added, 37 modified, 0 deleted — see `changed-files.txt`. No new
dependencies, no config/env changes, no API route removed or renamed (only
additive fields/parameters: `impact`, `moves`, `change_blocks`, search
`impact` / `priority` / `expand_blocks`).

## Folder contents

- `files/` — the **full new version** (at `bbc17b5`) of every added/modified file, same paths as the repo.
- `combined-c2e3717..bbc17b5.diff` — the whole change as one diff (**preferred**).
- `000{1..4}-*.patch` — the four commits separately.
- `base/` — the `c2e3717` version of every modified file (3-way merge ancestor).
- `apply_migration.py` — per-file 3-way merge onto a work repo without shared history (Method 0).

Verified off-site: on a clean `c2e3717`, each of the three methods (combined
diff, the four patches with `git am --keep-cr --3way`, copying `files/`)
reproduces the `bbc17b5` tree byte-for-byte.

## Precondition

The work repo already contains the previous migration
(`migration/2026-11-20-table-cells-to-german-ui`): `src/document_compare/phase2/ui/texts.py`
exists and `phase2/viewer/locations.py` contains `role="cell"`. If not, apply
that migration first and stop to tell the user.

Do not unzip over the work repo. Unzip to a separate folder and copy from there.
Commit or stash local work first, then work on a new branch:
`git switch -c feat/german-impact-classification`.

Many files are **CRLF**. Never let an editor or tool convert line endings.

## Method 0 — `apply_migration.py` (recommended on the work PC)

`git apply --3way` cannot work on the work PC: the ZIP has no git history, so
the base blobs are missing ("missing base blobs"), and without `--3way` every
file the work PC changed locally fails (seen: `three-tool-reader.md`,
`agent/{gateway,prompts,tools}.py`, `mcp/reader_server.py`,
`reader/result_reader.py`, `tests/phase2/test_mcp.py`). This package therefore
also ships `base/` = the `c2e3717` version of every modified file, and a
script that does the 3-way merge per file with `git merge-file`:

```bash
git status                     # must be clean (commit/stash first), on a new branch
python <unzipped>/migration/2026-11-24-german-impact-classification/apply_migration.py --repo .
python <unzipped>/migration/2026-11-24-german-impact-classification/apply_migration.py --repo . --apply
```

The first call is a dry run that lists, per file: `replaced` (work copy equals
`c2e3717`), `added`, `already_applied`, `merged_clean` (work-PC edits kept),
`merged_with_conflicts`. Line endings: files are compared ignoring CRLF/LF and
each written file keeps the work file's line ending style.

For `merged_with_conflicts`, open the file, search `<<<<<<< work-pc`, and
combine both sides: keep the work-PC code and add this package's change
(`files/<path>` is the target version, `base/<path>` the common ancestor).
Never delete a work-PC feature to make a hunk fit. Then `git diff` to review
and run Verify.

Verified off-site on a simulated work PC (Windows-style CRLF on LF files,
local edits in `gateway.py`, `result_reader.py`, and an overlapping edit in
`prompts.py`): 34 replaced, 8 added, 2 merged clean with local edits kept,
1 conflict marked; after resolving, everything else equals `bbc17b5` and the
suite shows only the 7 known failures.

If you already ran `git apply --reject`/partial applies, first restore the
touched files (`git checkout -- .` and delete `*.rej`), then use Method 0.

## Method 1 — apply the combined diff (only if the work copy equals `c2e3717`)

From the work repo root:

```bash
git apply --3way --check <unzipped>/migration/2026-11-24-german-impact-classification/combined-c2e3717..bbc17b5.diff
git apply --3way <unzipped>/migration/2026-11-24-german-impact-classification/combined-c2e3717..bbc17b5.diff
```

"Falling back to direct application" messages are normal. Resolve any
conflict markers by hand (rules below). Per-commit alternative:
`git am --keep-cr --3way 0001-*.patch 0002-*.patch 0003-*.patch 0004-*.patch`.

## Method 2 — copy files (only if the diff will not apply)

Copy only when the work copy of that file still equals `c2e3717`; otherwise
merge by hand.

1. New files, copy as-is: `phase1/matching/{impact,moves,element_types}.py`,
   `tests/phase1/test_{impact,structure_changes,typing_and_furniture,change_blocks}.py`,
   `tests/phase2/test_change_blocks.py`.
2. Phase 1 stages and structure (`s03`, `s05`–`s09`, `s11`, `s14`,
   `structure/furniture.py`, `structure/reconstruction.py`,
   `matching/reconciliation.py`, `matching/residual_proposals.py`,
   `contracts/*.py`, `reporting/report_model.py`): copy only if the work backend
   has not changed them; otherwise apply the matching hunks of the combined diff.
3. Phase 2 (`reader/`, `viewer/`, `ui/`, `agent/`, `mcp/`, `api/service.py`):
   changes are additive; same rule.
4. Docs: merge sections by hand if the work copies differ.
   **Never touch `docs/KnowlesgeTranser Phase 1.md`** (a test pins its bytes).

## Conflict rules

- Keep the guiding rule: a change may be grouped or labelled, never hidden.
  Do not turn any review into a suppression while resolving.
- Do not change thresholds/weights in Stage 07 (`MIN_REVIEW_SCORE` 0.72,
  `MIN_AUTOMATIC_SCORE` 0.86, `MIN_AUTOMATIC_MARGIN` 0.10). The containment
  constants (`MIN_CONTAINMENT_TOKENS`, `MIN_TOKEN_CONTAINMENT`,
  `MAX_CONTAINMENT_LENGTH_RATIO`) are new and separate.
- Stage 13 stays advisory; Stage 14 owns disposition.
- If the work backend renamed Stage 09/14 inputs, adapt the new code, not the
  contracts. Reader must keep reading legacy results without `impact`,
  `moves` or `change_blocks`.
- API stays loopback-only; English `change_type` values are wire values,
  German is display text only (`ui/texts.py`).

## Verify

```bash
python -m unittest discover -s tests
python tools/harness/validate.py
```

Off-site: 1076 tests, 7 failures that already existed before this change and
are unrelated (`test_knowledge_transfer_bytes_are_preserved`,
`test_env_example_only_names_and_placeholders`, `test_requirements_exclude_internal_sdk`,
`test_no_bbk_in_public_requirements`, `test_required_tree`,
`test_redline_render_function_and_output_unchanged`,
`test_ambiguous_fixture_baseline_comparison_and_incomplete_uncertainty`);
harness valid. On the work PC compare against the failure list **before**
applying: no new failure is acceptable.

## Re-run existing comparisons (no new DI call)

Old `comparison_result.json` files still open (impact shows as unknown). To get
the new classification, re-run from Stage 02 on the cached DI output
(`output/old_di_raw.json`, `output/new_di_raw.json` must exist):

```bash
python -m document_compare run --root "workspace/<job root>" --from-stage 02 --to-stage 15
```

Then start the UI (loopback only) and check one real pair:

```bash
python -m uvicorn document_compare.phase2.api.service:app --host 127.0.0.1 --port 8321
python -m streamlit run src/document_compare/phase2/ui/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Expect: badges Inhalt / Redaktionell / Verschoben / Wichtig; "Auswirkung"
filter incl. "Wichtig"; deleted/added sections as one item
("Abschnitt gelöscht", "n Absätze gelöscht") with "Änderungen im Block";
"Einzeln anzeigen" shows single changes; hyphenation-only edits are
Redaktionell. Off-site reference (one German MaRisk pair, 122 vs 82 pages):
1365 → 581 visible findings, review queue 1289 → 526, Wichtig 812 → 202.

## Not verified anywhere

End-to-end on real PDFs through DI (only cached DI replays); results against a
human-labelled reference; documents other than German regulatory text.
Known limits: a heading demoted to body text with identical text is not
reported; change IDs are positional. No commit/push to the company remote
without the user's go-ahead.
