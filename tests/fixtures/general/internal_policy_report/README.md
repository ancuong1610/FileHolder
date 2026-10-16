# Internal policy/report general fixture (fully synthetic)

`old_di_raw.json` / `new_di_raw.json` are compact merged Azure Document
Intelligence (prebuilt-layout) JSON documents: `apiVersion`, `modelId`,
`stringIndexType`, `content`, `pages`, `paragraphs`, and no tables. Spans,
words, and lines are omitted (optional for stages s02-s15). All page geometry
and bounding-region polygons are valid and inside the page bounds.

## Data provenance

**Fully synthetic.** All prose is invented neutral policy/report text written
for this fixture; it contains **no private BU content** and no legal-domain
evidence of any kind, so `auto` resolves `generic` with
`confidence: uncertain`.

## Behaviors exercised

- **Auto domain resolution**: no legal-domain evidence -> `generic`.
- **Addition** (accepted): `All retention schedules must be approved by the
  compliance committee before activation.`
- **Deletion** (accepted): `Records older than ten years shall be destroyed
  automatically.`
- **Modification** (accepted): `The data protection officer shall review this
  policy annually.` -> `... biannually.` (similarity 0.959, automatic
  alignment).
- **Reordered/moved block** (review): Sections 1-3 (`Purpose`, `Scope`,
  `Definitions`) move from the front of the document to the end. s09 detects
  the contiguous 3-heading block (rank shift +9, 9 external crossings) and
  emits `MOVEMENT-BLOCK-REVIEW-000001`; s11 routes it HUMAN_REVIEW
  (`movement_block_review`) and it appears in the review queue.
- **Identity**: running the old document against itself produces zero
  changes, zero table changes, and an empty review queue.

## Expected semantic examples (declared independently)

The fixture is exercised by the offline demo and pipeline tests in
`tests/test_offline_setup.py` and `tests/test_pipeline.py`.

| Category | Expected examples |
| --- | --- |
| MODIFIED | 1: annually -> biannually pair |
| ADDED | 1: retention-schedules paragraph |
| DELETED | 1: records-destruction paragraph |
| MOVEMENT_BLOCK_REVIEW | 1: frozenset of the moved heading texts {Section 1: Purpose, Section 2: Scope, Section 3: Definitions} |
| TABLE_MODIFIED | none declared -> `not_applicable` |
| REVIEW | none declared -> `not_applicable` |

## Mode matrix expectations

| Mode | Domain profile | Domain verification | Outcome |
| --- | --- | --- | --- |
| auto | generic (uncertain, auto) | `not_applicable` on every change | 3 changes, all accepted; 1 review-queue entry (movement block) |
| generic | generic (confident, explicit) | `not_applicable`; no domain review | identical generic result |
| crr3 | crr3 (confident, explicit) | all changes `unsupported` (no `Artikel N` evidence); domain review populated | identical generic result; nothing blocked |

## Declared metrics (auto mode, `CRR_RUN_LLM=mock`)

| Category | Precision | Recall | F1 | Counts (expected/predicted/matched) |
| --- | --- | --- | --- | --- |
| MODIFIED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| ADDED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| DELETED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| MOVEMENT_BLOCK_REVIEW | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| TABLE_MODIFIED | not_applicable | not_applicable | not_applicable | 0 expected |
| REVIEW | not_applicable | not_applicable | not_applicable | 0 expected |

The test asserts F1 >= 1.0 for every declared category (i.e. exact match
against the independently declared examples) and `not_applicable` for
undeclared categories. These floors are supported by the examples above and
are reproducible: the pipeline is deterministic and the mock evaluator is
offline.
