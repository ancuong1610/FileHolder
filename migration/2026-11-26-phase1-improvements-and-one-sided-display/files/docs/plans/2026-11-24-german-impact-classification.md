# German-first impact classification, moves and change blocks — implementation plan

**Status:** ACTIVE (T1–T13 done; Phase 2 parts of T10 open)
**Prepared:** 2026-11-24 (written up after implementation, 2026-11-26)
**Evidence basis:** inspected Phase 1 stages 03–14 and Phase 2 reader/viewer/UI;
offline replays of one real German regulatory pair (122 → 82 pages, cached DI)
and synthetic German probes. Commits `a51930f`, `5c818ac`, `3055d23`, `bbc17b5`
on branch `feat/german-impact-classification`; transfer package
`migration/2026-11-24-german-impact-classification/`. Unknowns: end-to-end on
fresh DI, a human-labelled reference, non-German documents.

## 1. Goal and scope

**Goal:** a reviewer of two versions of a (mostly German) regulatory document
sees content changes first, editorial changes second, and provable extraction
noise not at all — with far fewer, better-grouped items and no hidden change.

**In scope:**
- Deterministic `impact` axis on every change and table change.
- Paragraph split/merge (resegmentation) as non-content; move detection.
- Type-family matching, role-free running header/footer, German list regex.
- Change blocks, narrowed "Wichtig" priority, clause containment pairing.
- German editorial folding (hyphenation wrap, list labels, abbreviations).
- Reader/viewer/UI exposure (impact filter, moves, blocks, German labels).

**Out of scope:**
- Matching thresholds/weights of Stage 07 (`MIN_REVIEW_SCORE` 0.72,
  `MIN_AUTOMATIC_SCORE` 0.86, `MIN_AUTOMATIC_MARGIN` 0.10).
- LLM-based classification; Stage 13 stays advisory.
- Stage 10 table continuation, page-label consistency, Gliederung cleanup
  (recorded as T8–T10).
- `docs/KnowlesgeTranser Phase 1.md` (a test pins its bytes).

## 2. Accepted decisions and constraints

- Guiding rule: a change may be grouped or labelled, **never hidden**. When unsure
  it stays visible (content or review). Only glyph-fold-equal pairs without a
  semantic signal token are suppressed.
- Impact levels `content | editorial | non_content` (+ `moved` for move records),
  priority `high | normal | low`, `basis = deterministic_rule`,
  `language_profile = de-en`, version `impact-1.0.0`.
- `non_content` iff `glyph_fold(old) == glyph_fold(new)` (NFKC, `Cf` removal,
  quote/dash canonicalisation from Stage 04, whitespace collapse, pure bullet
  glyphs `• ◦ ▪ ‣ ⁃ ·`). State/checkbox glyphs are never folded (signal
  `state_marker`).
- `editorial` iff editorial tokens equal: German-first number canonicalisation,
  number words, `Prozent`/`%`, gender forms, punctuation drop, targeted `ß`→`ss`
  (word-final or before consonant only), plus line-break hyphenation
  (`x- y` before lowercase, not before a conjunction), fixed abbreviation
  expansions (kind set only when usage counts differ), shared list-label
  stripping (`a)`, `(b)`, `iv)`, `3)`; not `1.`).
- Content signals: modal, negation, qualifier, condition, number, date,
  reference (German + English lexicons). One-sided text is `high` only when
  ≤ 40 words **and** it carries one of these signals; block members are `normal`.
- Moves: LIS over matched pairs plus "changed section" rule; unchanged moved
  elements are not added to `changes`; moves are not review items.
- Change blocks: runs of ≥ 3 consecutive ADDED (NEW side) / DELETED (OLD side)
  records, never crossing a non-member heading; tables excluded; member records
  stay in `changes` unchanged; one review entry per block.
- Clause containment (Stage 07): ordered token containment ≥ 0.90, shorter side
  ≥ 8 tokens, length ratio ≤ 3, unique in both directions; reason
  `clause_containment`. Constants are new and separate from the thresholds.
- Role-free furniture is excluded only when confirmed by the other document.
- All result fields are additive (`impact`, `moves`, `change_blocks`, statistics
  keys); no `schema_version` bump; reader keeps reading legacy results.
- English `change_type` values are wire values; German is display text only
  (`ui/texts.py`). Services loopback-only. No new dependencies.
- Confidentiality: no input text, raw DI or private results in docs, tests or
  commits; probes are synthetic.

## 3. File and interface map

| File / contract | Responsibility | Change | Focused check |
| --- | --- | --- | --- |
| `phase1/matching/impact.py` (new) | impact classifier | `classify_impact`, `glyph_fold`, `editorial_tokens` | `tests.phase1.test_impact` |
| `phase1/matching/moves.py` (new) | move detection | LIS + section-change moves, `MOVE-…` records | `tests.phase1.test_structure_changes` |
| `phase1/matching/element_types.py` (new) | text family | heading/paragraph/list/footnote family | `tests.phase1.test_typing_and_furniture` |
| `phase1/stages/s03_internal_schema.py` | typing | list regex with German abbreviation exclusions | same |
| `phase1/stages/s06_exact_matching.py`, `s07_modified_matching.py` | matching | family-gated keys; clause containment | `tests.phase1.test_change_blocks` |
| `phase1/stages/s09_movement.py`, `s11_routing.py` | movement/routing | resegmentation groups; glyph-fold suppression rule | `tests.phase1.test_structure_changes`, `test_impact` |
| `phase1/stages/s14_canonical_result.py` | canonical result | `impact`, `moves`, `change_blocks`, statistics, block review collapse | `tests.phase1.test_change_blocks` |
| `phase1/structure/furniture.py` | furniture | role-free edge recurrence, cross-document confirmation | `tests.phase1.test_typing_and_furniture` |
| `phase2/reader/result_reader.py` | bounded reader | `impact` / `priority` / `expand_blocks` filters, move & block records | `tests.phase2.test_reader` |
| `phase2/viewer/{locations,outline}.py` | viewer | multi-member polygons, block/move outline items | `tests.phase2.test_viewer_*` |
| `phase2/ui/{review,texts}.py` | UI | badges Inhalt/Redaktionell/Verschoben/Wichtig, block items | `tests.phase2.test_ui_smoke`, `test_change_blocks` |
| `docs/contracts/phase1-matching.md`, `three-tool-reader.md`, `view-endpoints.md`, `docs/architecture.md` | contracts | document new fields and known limits | `tools/harness/validate.py` |

## 4. Ordered tasks

### T1 — Impact axis [DONE]

**Purpose:** every change carries a deterministic impact; glyph noise leaves `changes`.
**Allowed files:** `matching/impact.py`, `s11_routing.py`, `s14_canonical_result.py`, `tests/phase1/test_impact.py`.
**Produces:** `classify_impact(old, new) -> dict`; Stage 11 route
`SPECIAL_CASE_RULE / glyph_or_invisible_char_artifact`; statistics
`impact_level_counts`, `impact_priority_counts`, `table_impact_level_counts`.
**Acceptance:** German non-content/editorial/content cases from the tests hold;
a glyph-equal pair with differing digits is not suppressed.
**Task state:** DONE (`a51930f`).

### T2 — Resegmentation and moves [DONE]

**Purpose:** split/merge is non-content; moved paragraphs/sections are reported.
**Allowed files:** `s09_movement.py`, `s11_routing.py`, `s14_canonical_result.py`, `matching/moves.py`, `tests/phase1/test_structure_changes.py`.
**Produces:** `non_semantic_events` kinds `paragraph_split|paragraph_merge`; top-level `moves`; `movement.move_id` on moved modified changes.
**Acceptance:** conservation validators hold (each element accounted once); synthetic probe 4/4; resegmentation only under the same heading and without extraction review; 2,000-element bucket test < 2 s.
**Task state:** DONE (`a51930f`).

### T3 — Reader, viewer, UI, docs for impact and moves [DONE]

**Purpose:** reviewers can filter and see impact and moves.
**Produces:** reader `impact` filter with `excluded_unknown_impact`; move records; German badges and labels.
**Task state:** DONE (`a51930f`).

### T4 — Typing and furniture fixes [DONE]

**Purpose:** DI role differences no longer break identical pairs; the running header is not noise.
**Acceptance:** real pair: heading↔paragraph identical texts matched; running header excluded in both documents; abbreviation starts (`z. B.`, `u. ä.`) not list items.
**Task state:** DONE (`a51930f`).

### T5 — Independent review repairs [DONE]

**Purpose:** fix review findings (state glyphs, furniture confirmation, resegmentation scope, section-change moves, `ß` rule, numeric separators, multi-member viewer polygons, table structure impact, performance bound, list regex, modal lexicon).
**Known limits recorded, not fixed:** a heading demoted to body text with identical text is not reported; change IDs are positional.
**Task state:** DONE (`a51930f`).

### T6 — Change blocks, narrower Wichtig, clause containment [DONE]

**Purpose:** fewer, grouped items on real documents.
**Allowed files:** `s14_canonical_result.py`, `s07_modified_matching.py`, `matching/impact.py`, Phase 2 reader/viewer/UI, `tests/phase1/test_change_blocks.py`, `tests/phase2/test_change_blocks.py`.
**Produces:** top-level `change_blocks` (`BLOCK-…`); block review entries absorbing counterpart-less residual groups (`proposal_group_ids`); members keep their kinds plus `block_member`.
**Acceptance:** every member change stays in `changes`; review queue reduced; probes unchanged.
**Task state:** DONE (`5c818ac`).

### T7 — German editorial folding and Wichtig filter [DONE]

**Purpose:** hyphenation wraps, list relabelling and abbreviations are Redaktionell.
**Task state:** DONE (`3055d23`; CRLF restore `bbc17b5`).

### T8 — Stage 10 multi-page table continuation [DONE]

**Purpose:** continuation fragments without a repeated header (e.g. a long
modification-history table) are joined, so later pages are compared.
**Evidence:** synthetic probe in `workspace/p8_table_probe/` (not committed):
with row insertions 0/6 later-page changes detected; `can_continue_logical_table`
requires a repeated header (similarity ≥ 0.92, or ≥ 0.80 plus shared context).
**Proposed (needs decision):** geometry-based continuation — adjacent pages,
same column count, aligned column boundaries, table at page bottom/top, no
contradictory boundary — without a header requirement.
**Stop conditions:** any change that would merge unrelated tables in existing fixtures.
**Implemented:** reason `geometry_continuation`: adjacent pages, same column count, previous table last body element on its page and current first, every column left edge within 0.02 page width, no contradictory boundary; two real headers with similarity < 0.50 still block.
**Task state:** DONE (uncommitted).

### T9 — Consistent Alt/Neu page labels [DONE]

**Purpose:** the outline page shown matches the sort basis; DELETED shows the
old page, others the new page, always labelled „S. x (Alt)“ / „S. y (Neu)“;
table reviews get a page.
**Task state:** DONE (uncommitted).

### T10 — TOC-driven section tree (Gliederung) [DONE: Phase 1 nesting]

**Purpose:** a usable section tree built from the printed TOC that Stage 05
already reads from DI (the source PDFs have no embedded bookmarks, so the
PyMuPDF outline prepass finds nothing).
**Evidence (real pair, cached DI):** printed TOC detected and usable on both
sides — OLD 77 entries / 76 linked, NEW 65 / 62 linked, levels 1–3 from
namespaced labels; NEW TOC comes from a DI table, OLD from a table plus text
lines. But 177 (OLD) / 88 (NEW) sections are `body_heading_unlisted`: DI
`sectionHeading` paragraphs not in the TOC, all unlabelled, mostly < 10 words,
with `level` and `parent_section_id` null — this is why the Gliederung looks flat.
**Proposed:**
- Phase 1 Stage 05 (additive): nest each `body_heading_unlisted` section under
  the nearest preceding TOC-linked section, `level = parent.level + 1`, basis
  stays `body_heading_unlisted` and a new `parent_basis = "preceding_toc_section"`.
- Phase 1 Stage 05: record TOC entries that never linked (`unmatched`) as
  diagnostics shown in the UI („Im Inhaltsverzeichnis, aber nicht im Text gefunden“).
- Phase 2 outline: one order (TOC order of the NEW side; DELETED-only sections
  placed after their OLD predecessor); drop the „Abschnittsnummer“ option;
  hide sections without findings by default.
**Stop conditions:** a change to TOC linking rules (Section G) or to membership
that breaks existing structure tests.
**Implemented:** positional nesting with `parent_basis` (`numeric_label` / `preceding_toc_section`) and a cycle check; the existing outline nests via `parent_known`. Unmatched-TOC diagnostics in the UI and the single-order outline remain open (Phase 2).
**Task state:** DONE for Phase 1 (uncommitted).

### T11 — Whole-page deletion/addition summary [DONE]

**Purpose:** colleague request: when every body element of a page is DELETED
(or ADDED), report „Seite 34 (Alt) vollständig gelöscht“ as one finding instead
of making the user walk each element. The earlier implementation is not in this
repository's history (searched all 30 commits); look for it on the work PC
before re-implementing.
**Proposed:**
- Stage 14 (additive): `page_changes` list. A page qualifies when all of its
  non-furniture, non-layout body elements and tables are members of DELETED
  (OLD) / ADDED (NEW) changes or table changes. Record: `page_change_id`
  (`PAGE-000001`), `side`, `page`, `change_ids`, `block_ids`,
  `table_change_ids`, `anchor_page_other_side` (page of the nearest preceding
  matched element on the other side), consecutive pages grouped into one record.
  Member records stay in `changes` (never hidden).
- Reader/viewer/UI: outline item „Seite 34–36 gelöscht“; the other side shows
  the anchor page with a dashed marker „Hier entfernt: Alt S. 34–36“ instead of
  an empty pane.
**Implemented:** as proposed; a fully deleted page with fewer than 3 elements has no block and is in the canonical result only.
**Task state:** DONE (uncommitted).

### T12 — Display of one-sided findings [DONE]

**Purpose:** for DELETED content the NEW pane is empty today. Always show the
other side's anchor page (nearest preceding matched element) with an insertion
marker, for single changes, blocks and pages (shares the anchor from T11).
**Task state:** DONE (uncommitted).

### T13 — Rewrite Knowledge Transfer Phase 1 [DONE]

**Purpose:** `docs/KnowlesgeTranser Phase 1.md` predates this plan (no impact,
moves, change blocks, containment, editorial folding, typing/furniture
changes); its pinned digest already fails
(`test_knowledge_transfer_bytes_are_preserved`).
**Allowed files:** the KT document and `KNOWLEDGE_TRANSFER_SHA256` in
`tests/integration/test_harness.py` only (AGENTS.md: KT bytes change only when
the change owns that content — this task does).
**Steps:** align every stage section with current code (Stages 03, 05–11, 14),
add Impact/Moves/Blocks sections, the DI facts (Stage 01: `prebuilt-layout`,
OCR built in, no add-on features, text content format), and known limits;
then re-pin the digest.
**Task state:** DONE (uncommitted); digest re-pinned, harness test passes.

## 5. Documentation and handoff

- Documentation updated: `docs/contracts/phase1-matching.md` (impact, moves,
  blocks, containment, known limits), `three-tool-reader.md`, `view-endpoints.md`,
  `docs/architecture.md`, `phase2/ui/README.md`.
- Final checks: `git diff --check`;
  `PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests`;
  `.venv/bin/python tools/harness/validate.py`.
- Re-run existing comparisons from cached DI:
  `python -m document_compare run --root "workspace/<job root>" --from-stage 02 --to-stage 15`.

## 6. Execution ledger

| Task | Release | Execution state | Evidence / next action |
| --- | --- | --- | --- |
| T1 | RELEASED | DONE | `test_impact` (19 tests); suite 996 → only the 7 known failures. |
| T2 | RELEASED | DONE | `test_structure_changes` (19 tests); probe 4/4. |
| T3 | RELEASED | DONE | reader/viewer/UI tests green. |
| T4 | RELEASED | DONE | `test_typing_and_furniture` (14 tests); real pair: running header 0 visible. |
| T5 | RELEASED | DONE | one regression test per finding; probes 24/24, 4/4. |
| T6 | RELEASED | DONE | `tests/phase1/test_change_blocks.py` (9), `tests/phase2/test_change_blocks.py` (7). Real pair: visible findings 1365 → 581, review queue 1289 → 526, Wichtig 812 → 202. |
| T7 | RELEASED | DONE | Redaktionell 2 → 43 on the real pair; probes 14/14. Suite 1076 tests, only the 7 known failures; harness valid. |
| T8 | RELEASED | DONE | `GeometryContinuationTest` (8 tests). Synthetic probe S1/S2/S4 6/6 rows, S3 3/3, 0 false positives, 0 unresolved groups. Real pair unchanged (no continuation candidates). |
| T9 | RELEASED | DONE | Rows show `S. x (Alt|Neu)` from the item's own page (`page_side`); table reviews get the table's source page. `test_viewer_outline`, `test_ui_smoke`. |
| T10 | RELEASED | DONE (Phase 1) | `tests/phase1/test_structure_nesting.py` (9). Real pair: root sections OLD 197 → 23, NEW 103 → 19; links/membership unchanged. |
| T11 | RELEASED | DONE | `tests/phase1/test_page_changes.py` (9), Phase 2 label tests. Real pair: 18 `PAGE-…` records (13 OLD, 5 NEW), all inside blocks; changes/blocks/review queue identical to HEAD. Anchor uses exact non-moved matches. Old work-PC implementation not found in this repository. |
| T12 | RELEASED | DONE | Stage 14 `other_side_anchor` on one-sided changes/tables/blocks (+ `other_side_anchor_element_id` on pages); viewer draws a dashed `marker` below the anchor element. Anchor pairs exclude moves, TOC↔body pairs and out-of-order pairs (longest increasing chain). Real pair: 1137 one-sided changes anchored (1 `document_start`), 0 backward page steps; synthetic UI checked. Suite 1132 tests, 6 known failures. |
| T13 | RELEASED | DONE | KT rewritten against current code; `test_knowledge_transfer_bytes_are_preserved` passes. Suite 1120 tests, 6 known failures. |

Known pre-existing failures (unrelated): `test_knowledge_transfer_bytes_are_preserved`,
`test_env_example_only_names_and_placeholders`, `test_requirements_exclude_internal_sdk`,
`test_no_bbk_in_public_requirements`, `test_required_tree`,
`test_redline_render_function_and_output_unchanged`,
`test_ambiguous_fixture_baseline_comparison_and_incomplete_uncertainty`.
