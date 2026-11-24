# Phase 1: matching thresholds and benchmark metrics

## Execution and reproducibility limits

Stage 07 can be imported without resolving a workspace or executing file I/O;
script/CLI execution uses its guarded `main()`. The diagnostics helper is for
serial offline experiments, not concurrent calls with different overrides.

Deterministic offline runs use a fixed Python hash seed. Existing set iteration
in context retrieval can change tied candidate ordering across seeds; this is a
pre-existing limitation, not proof of general cross-seed semantic stability.
Thresholds and candidate ordering were not retuned here.

This note documents (a) the unchanged lexical matching defaults, (b) Gate A's
additive source-proof/residual safety policy, (c) change impact and moves, and
(d) the benchmark schema used by
`phase1/evaluation/evaluate.py`. It exists so a result can be read honestly: an
uncovered condition is never reported as a perfect score.

## Matching defaults (not changed here)

From `phase1/stages/s07_modified_matching.py`:

- `MIN_REVIEW_SCORE = 0.72`
- `MIN_AUTOMATIC_SCORE = 0.86`
- `MIN_AUTOMATIC_MARGIN = 0.10`
- `SHORT_TEXT_REVIEW_CHARACTERS = 40`
- Score weights: `0.65 * char_ratio + 0.25 * token_f1 + 0.10 * length_ratio`.

The historical calibration of these values is not recorded in the repository;
they are the existing defaults and were deliberately not retuned. Gate A adds
evidence and guarded routing; it does not lower these global thresholds or
change provider authority. Existing result fields stay compatible.

## Clause containment

Stage 07 also considers same-family pairs from the existing shortlists whose
normal similarity score is below the review floor. Ordered containment is the
sum of matching-block sizes from `SequenceMatcher` on casefolded
`matching_text` word tokens, divided by the shorter token count. The shorter
text must have at least eight tokens, containment must be at least 0.90, and
the longer text may have at most three times as many tokens.

The pair must be the only containment candidate for both elements, and both
elements must remain unmatched by the normal similarity path. The pair records
`clause_containment` and `similarity_evidence.token_containment`. It is a
`modified_alignment_review` when either text has fewer than 40 characters
and a `modified_alignment_candidate` otherwise. Existing retrieval caps,
similarity weights and thresholds apply as before.

## Text family and page furniture

Stages 06 and 07 use one matching family for `paragraph`, `heading`,
`list_item`, `footnote` and `title`. Tables and each layout type stay
separate. Stage 06 keys by family and comparison text. Duplicate assignments
continue to rely on shared nonempty provisional anchor context and occurrence
order. Stage 07 uses the family for retrieval and compatibility, and Stage 09
uses it for residual pairing. A pair with differing source types records
`type_changed` with the original OLD and NEW types. An exact match with only a
type change is unchanged content. For modified pairs, Stage 14 adds
`element_type_changed` to the normal impact kinds.

Stage 05 records the furniture evidence path as `role_supported` or
`recurrence_edge_only`. The second path requires normalized text of at most
40 characters and five words, at most one occurrence per page, and occurrence
on at least five pages and 30% of physical pages. At least 90% of all
occurrences must share a top or bottom edge band, and every occurrence must
be free of a DI heading role. A polygon must fit the page bounds in either the
reported or rotated coordinate frame to count as recurrence edge evidence.
A `recurrence_edge_only` record becomes a layout member and is excluded from
matching only when the other document derives furniture with the same group key
using either evidence path. Without corroboration, the record carries
`exclusion_confirmed_by_other_document: false` and the text stays comparable.
Source roles, types, text and IDs remain available in the structured document.

## Gate A: source-bound material evidence and residual safety

Owners: `contracts/semantic_assessment.py`, `matching/semantic_evidence.py`,
`matching/residual_proposals.py`, and stages 07–09/11/14.

`semantic_assessment` is optional and additive. Schema `1.0.0`, policy `gateA-1`
separates `correspondence` (`verified|proposed|unresolved|not_applicable`),
`relation` (`material_change|equivalent|undetermined`) and `basis`
(`deterministic_witness|model_advisory|not_assessed`). Exact source hashes, side
element IDs, code-point spans, complete prefix/suffix context, guard outcomes
and limitations accompany a witness. Missing legacy assessment is `not_assessed`,
not semantic proof; reading it never migrates or rewrites saved statuses.

The narrow English witness handles an explicit standalone polarity change,
different modal force, an unambiguous numeric component or a threshold operator
over otherwise identical source context. `must` and `shall` have the same
mandatory force; different spelling alone is not materiality. Quotes, fragments,
multiple clauses, conditions/exceptions, coordination, ambiguous numeric locale,
signed/grouped/fractional tokens and unsupported unit-only edits abstain. This is
not general semantic understanding or calibrated/legal certainty.

Only a short-text-only review reason may be reconsidered: unique reciprocal
correspondence, finite margin >=0.10 and clean extraction must also pass. Stage
14 validates source binding and independently recomputes the witness. A route,
caller boolean, stale span/hash or model advice alone cannot approve/suppress.
Stage 13 remains advisory, including its mock `REAL_CHANGE` response.

Stage 09's residual proposals are evidence, never committed matches or source
consumption. At most five proposals per residual and 1,000 actual score checks
per job are allowed, with cached scores, stable ordering and explicit partial,
unsearched, exhausted or cap-pruned coverage. Compatible residuals excluded by
indexing/pruning are not falsely reported as searched absence. Plausible or
unresolved counterparts route to uncertainty rather than independent accepted
delete/add claims. Complete no-opposite evidence retains the existing one-sided
source-presence behaviour; it is not relabelled as semantic proof.

One canonical proposal-group explanation deduplicates user-facing uncertainty;
all original change records, candidate/element IDs and side passages remain.
Reports list group OLD and NEW sources separately as **unpaired**, without
inventing correspondence or hiding numeric/negation content. Legacy accepted-only
index and reader kind/status filters remain unchanged.

Checks: `python -m unittest tests.phase1.test_semantic_assessment tests.phase1.test_semantic_residuals tests.phase1.test_semantic_safety_regressions`.
The exposed 72-case experiment is development/regression evidence for this change.
A separately frozen 48-pair set is reserved until candidate source freeze; both
sets are AI-authored controlled examples, not expert-certified natural revisions.
No >=80% supported recall / >=95% precision claim follows from these code checks.

## Change impact and moves

Stage 14 adds an `impact` object to each change and table change. The object
carries `version`, `level`, `priority`, `kinds`, `signals`, `basis` and
`language_profile`. For table modifications with text differences, changed-cell
classifications determine the strongest impact and contribute their kinds and
signals. Changes to cell spans, row or column membership, or other table
structure carry `table_structure_changed` and `content` impact. They have
normal priority, elevated if a changed cell has high-priority signals. Impact
and priority describe the observed difference independently of resolution and
review status.

| Level | Deterministic rule |
| --- | --- |
| `content` | Text is added or deleted, or the old and new lexical tokens differ. For paired modifications, a changed material signal gives `high` priority. An isolated addition or deletion is `high` only with at most 40 word tokens and at least one of `modal`, `negation`, `condition`, `number`, `date` or `reference`. Other content findings have `normal` priority. |
| `editorial` | Tokens match after editorial normalization. Examples include punctuation, letter case, certain German gender forms and equivalent number formats. German `ß` folds to `ss` at the end of a word or before a consonant. Line-break hyphenation (`Risikotragfähigkeitskon- zepte`, a hyphen plus space before a lowercase letter, except before `und`/`oder`/`bis`/`sowie`/`bzw.`) is joined (kind `hyphenation`). Fixed abbreviations with one expansion (`z. B.`, `d. h.`, `u. a.`, `i. d. R.`, `bzw.`, `ggf.`, `gem.`, `vgl.`, `inkl.`, `usw.`, `Abs.`, `Nr.`, `Tz.`, `Rn.`) are expanded (kind `abbreviation`). When both sides start with an enumeration label such as `a)`, `(b)`, `iv)` or `3)`, the labels are compared separately; a label-only difference is `list_label`. `1.` is not treated as a label because it can be a date day. These findings have `low` priority and remain visible; Stage 11 review routing is unchanged. |
| `non_content` | Text is identical or equal after glyph folding. Folding covers whitespace, ligatures, Unicode composition, invisible characters and selected quote, dash and bullet variants. Deterministically resolved glyph-only differences are kept as low-priority `non_semantic_events` and omitted from the findings list. |
| `moved` | A paired element or block changed relative reading order or a body element crossed a matched section boundary. Moves are separate records with their own priorities. |

Leading purely decorative bullets (`• ◦ ▪ ‣ ⁃ ·`) may fold away. A leading
hyphen followed by a space can fold away unless a digit, percent sign or
currency follows. State marks such as `□`, `■` and `☑` remain distinct and
give a `state_marker` signal when changed. Stage 11 requires the same ordered
digits, plus and minus signs, and state marks in the raw text before using
glyph equivalence to suppress a finding. Editorial normalization retains
numeric operators between numbers, including `/`, `:`, `-`, `–`, `x` and
`×`. Changing or removing one yields a `number` signal.

The German-first lexicons (`language_profile: de-en`) include English fallback
terms. Changed token spans can produce `modal`, `negation`, `qualifier`,
`condition`, `number`, `date`, and `reference` signals. Token edit operations
produce `added_text`, `deleted_text`, and `replaced_text` signals.
The rules preserve changed numbers, dates, negations and references as evidence
when those tokens change.

Stage 09 reconciles unambiguous splits and merges of one paragraph into two to
four adjacent paragraphs. The whole and its parts must share the same folded
preceding heading key and have usable extraction evidence. Ambiguous token
buckets leave their candidates in the ordinary matching and review flow.
Glyph-equivalent groups route through Stage 11 to
`structural_only` events in Stage 14, suppressing redundant add/delete findings
while preserving the source members. Groups that also differ editorially
surface as editorial modifications. Material token differences stay in the
ordinary matching and review flow.

Stage 09 compares established pairs in OLD and NEW reading order. It flags
pairs outside a longest increasing subsequence and body pairs whose preceding
headings do not form an established heading pair. It groups flagged pairs when
contiguous on both sides.
Stage 14 emits these blocks in `moves` with a `MOVE-…` ID, element IDs and text
evidence on both sides, preceding headings (`from_anchor` and `to_anchor`),
`element_count`, `contains_modified` and `impact`. The move kinds are
`section_move` (heading-led block), `paragraph_move`, and `local_reorder`.
A single unmodified body element under the same heading is a low-priority local
reorder and is hidden by default in the reviewer. Other moves are normal
priority, elevated when they involve a heading or a high-priority modification.
A moved modification also remains in `changes` with `movement.move_id`.
Stage 14 statistics expose `moves`, `impact_level_counts`,
`impact_priority_counts` and `table_impact_level_counts`.

This is deterministic lexical and reading-order evidence. Editorial equivalence,
materiality and movement can depend on context or extraction quality. Verification
for these rules is limited to synthetic probes and regression fixtures.

## Change blocks

Stage 14 records maximal runs of at least three visible additions on the NEW
side or deletions on the OLD side. Layout and furniture are skipped. An
intervening body element outside the run, including a surviving heading or a
table, ends the run. Blocks record the preceding heading, member IDs in reading
order, page numbers and whether the run includes a heading. The
`changes` list retains every member with its original identity and an additive
`block_id`.

Blocks carry `content` impact at `normal` priority. Their impact kind is
`section_added` or `section_deleted` if they include a heading and
`block_added` or `block_deleted` otherwise. Member impact keeps its signals
and uses `normal` priority and the `block_member` kind. The block's signals
are the union of its members' signals. Statistics report `change_blocks` and
`changes_in_blocks`.

The review queue uses one `change_block` entry for members that need review.
A residual proposal group whose members all lie in one block and that carries
no counterpart proposal only says "no counterpart found"; it is absorbed into
the block entry (listed in `proposal_group_ids`). A residual group with at
least one counterpart proposal, or spanning several blocks, keeps its own
review entry. Member change records are never removed. Change disposition still comes from Stage 14's deterministic
rules, and Stage 13 remains advisory.

## Known limits

- A heading demotion with identical text does not produce a change record.
- Change IDs are positional. Regenerating a comparison may renumber them.

## Opt-in matching diagnostics

Run `python -m document_compare.phase1.evaluation.matching_diagnostics --root NEW_ROOT` to write one
`matching_diagnostics_report.json`; an existing report is never overwritten.
Each experiment runs in a fresh subprocess and overrides only one of
`MIN_REVIEW_SCORE`, `MIN_AUTOMATIC_SCORE`, or `MIN_AUTOMATIC_MARGIN` in memory.
The opt-in `build_result(exact, diagnostics=None)` collector records only
element IDs, stage names, retrieval phase/method, and numeric decisions—never
source text—and does not change the canonical result.

`phase1/stages/s07_modified_matching.py` is import-safe: the pure
`build_result` / `validate_result` functions are defined at module level, while
workspace/path resolution, the Step-6 input read, the atomic result save, and
the console report live inside `main()`, executed only under
`__name__ == "__main__"` (the CLI stage runner). Importing the module therefore
has no filesystem, environment, or output side effects, so the diagnostics
harness imports it directly instead of seeding a temporary workspace.
Overrides patch the module globals for one call and are restored in a
`finally` block.

The funnel measures retrieval, shortlist, scoring, selection, and disposition,
using the existing candidate and expensive-work caps. Synthetic truth pairs,
recall, precision, F1, and threshold sensitivity are harness checks only, not
calibration, production accuracy, or claims about arbitrary PDFs. Defaults
remain the existing values above.

## Mutation benchmark scoring (`eval_report.json`)

`python -m document_compare eval` writes `workspace/eval/eval_report.json`. The
canonical `comparison_result.json` schema is unchanged; this report is a
separate accuracy artifact with **additive** fields.

Each scored condition (`modify`, `delete`, `insert`, `move_block`,
`table_cell`, `noise_hyphenation`) now carries an explicit support block:

| Field | Meaning |
| --- | --- |
| `tp` | ground-truth items correctly surfaced |
| `fp` | surfaced items not backed by injected truth (`null` where the harness has no independent negative-prediction stream) |
| `fn` | ground-truth items not surfaced |
| `truth_count` | injected ground-truth items (`tp + fn`) |
| `prediction_count` | surfaced items considered for the condition |
| `status` | one of `evaluated`, `not_applicable`, `false_positive` |

Detection conditions (`modify`, `delete`, `insert`) also report
`precision` / `recall` / `f1`. These are **nullable**:

- `precision` is `null` when there are no predictions (`tp + fp == 0`).
- `recall` is `null` when there is no truth (`tp + fn == 0`).
- `f1` is `null` when either input is undefined.

`move_block` and `table_cell` are condition checks, not detection streams: they
have no independent negative-prediction set, so `precision` is `null` and
`recall` measures only how much of the known condition was preserved.
`table_cell` is counted per **unique** injected cell: each ground-truth cell id
surfaced by a non-`UNCHANGED` cell change is one `tp`, repeated records and a
shared old/new cell id collapse into one, and unrelated or `UNCHANGED` cells
earn no credit. The legacy any-hit `hit` bool is retained for compatibility, but
the pass gate requires **every** supported cell to be detected, so a partial
table detection can no longer pass.
`noise_hyphenation` measures suppression: an injected wrap that surfaces as a
change is a false positive.

### Status semantics

- `evaluated` — the condition had injected truth and was scored.
- `not_applicable` — no truth and no predictions. This is **not** a perfect
  score; the metric fields are `null`.
- `false_positive` — no truth, yet a change was surfaced. This is a tested
  failure, not a success.

### Coverage report

The mutation result also contains a top-level `coverage` object:

- `categories` — per-condition `truth_count`, `prediction_count`, `status`,
  `covered`.
- `supported` — conditions that had injected truth.
- `uncovered` — conditions with no injected truth.
- `false_positive` — empty-truth conditions that produced false positives.
- `fully_covered` — true only when every condition had truth.

The pass gate is evaluated only over supported conditions. Uncovered
conditions neither pass nor fail: they are printed as a
`COVERAGE GAP …` line and listed under `coverage.uncovered`, so the report can
never imply that an untested class was perfect.

### Current fixture coverage (seed 42)

`tests/fixtures/general/crr3` has only 5 eligible paragraphs and 5 headings,
so the default generator injects:

- covered: `modify` (5), `move_block` (4 elements), `table_cell` (1)
- uncovered: `delete`, `insert`, `noise_hyphenation` (0 truth)

Ground truth is **not** fabricated and fixture counts are **not** forced to fill
these gaps. Instead they are reported explicitly. Forced-injection coverage is
provided by `tests/integration/test_pipeline.py::MutationNoisePipelineTest`, which injects
one wrap on the real fixture and runs the actual s04–s15 pipeline to prove the
noise does not leak.

## Synthetic wrap-noise generator

`phase1/evaluation/mutations.py` now emits `noise_hyphenation` as a literal
end-of-line wrap (`word[:cut] + "-\n" + word[cut:]`). Under the approved s11
policy only a raw `-\n` with a lowercase continuation is source-proven wrap
noise; a same-line `- ` is ambiguous and correctly routes to review. This is a
generator-semantics fix, not a matcher change.
