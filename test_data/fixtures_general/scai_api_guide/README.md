# SCAI API guide general fixture (fully synthetic)

`old_di_raw.json` / `new_di_raw.json` are compact merged Azure Document
Intelligence (prebuilt-layout) JSON documents: `apiVersion`, `modelId`,
`stringIndexType`, `content`, `pages`, `paragraphs`, and one `tables` entry
with `rowCount`/`columnCount`/`cells`/`boundingRegions`. Spans, words, and
lines are omitted (optional for stages s02-s15). All page geometry and
bounding-region polygons are valid and inside the page bounds.

## Data provenance

**Fully synthetic.** All endpoint/parameter prose and the parameter table are
invented for this fixture; there is **no private BU content** and no
legal-domain evidence of any kind (no Regulation/CELEX citations), so `auto`
resolves `generic` with `confidence: uncertain`.

## Behaviors exercised

- **Auto domain resolution**: no legal-domain evidence -> `generic`.
- **Heading modification** (review): `Completions API reference
  documentation` -> `Completions API guide documentation` (similarity 0.822,
  review band). The mock evaluator returns UNCERTAIN, so the change is
  retained as a MODIFIED change with review status AND queued in the review
  queue (`ambiguous_modified_pair`).
- **Prose modification** (accepted): `... generated tokens per request.` ->
  `... per response.` (similarity 0.962, automatic alignment).
- **Addition** (accepted): new heading `Embeddings API` plus its prose
  paragraph.
- **Deletion** (accepted): the legacy-completions paragraph.
- **Table modification** (accepted): parameter-table description cell
  `Sampling temperature` -> `Sampling temperature (0 to 2)`
  (TABLE_MODIFIED, OLD-LT0001 -> NEW-LT0001).
- **Mock LLM path**: the heading review is the only SPECIAL_CASE_LLM_OPTIONAL
  route; s12 builds one bounded packet and s13 evaluates it with the
  deterministic offline mock.

## Expected semantic examples (declared independently)

The fixture is exercised by the offline pipeline and regression tests in
`tests/test_pipeline.py` and `tests/test_regression_fixtures.py`.

| Category | Expected examples |
| --- | --- |
| MODIFIED | 2: prose pair (per request -> per response); heading pair (reference -> guide documentation) |
| ADDED | 2: `Embeddings API` heading; embeddings prose paragraph |
| DELETED | 1: legacy-completions paragraph |
| TABLE_MODIFIED | 1: OLD-LT0001 -> NEW-LT0001 |
| REVIEW | 1: `ambiguous_modified_pair` for the heading pair |

The heading pair is declared in both MODIFIED and REVIEW because the final
canonical output legitimately contains both representations: a retained
MODIFIED change with review status and a review-queue entry.

## Mode matrix expectations

| Mode | Domain profile | Domain verification | Outcome |
| --- | --- | --- | --- |
| auto | generic (uncertain, auto) | `not_applicable` on every change/table change | 5 changes + 1 table change (5 accepted, 1 review); 1 review-queue entry |
| generic | generic (confident, explicit) | `not_applicable`; no domain review | identical generic result |
| crr3 | crr3 (confident, explicit) | non-table changes `unsupported` (no `Artikel N` evidence), table change `ambiguous`; domain review populated | identical generic result; nothing blocked |

## Declared metrics (auto mode, `CRR_RUN_LLM=mock`)

| Category | Precision | Recall | F1 | Counts (expected/predicted/matched) |
| --- | --- | --- | --- | --- |
| MODIFIED | 1.0 | 1.0 | 1.0 | 2 / 2 / 2 |
| ADDED | 1.0 | 1.0 | 1.0 | 2 / 2 / 2 |
| DELETED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| TABLE_MODIFIED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| REVIEW | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |

The test asserts F1 >= 1.0 for every declared category (i.e. exact match
against the independently declared examples). These floors are supported by
the examples above and are reproducible: the pipeline is deterministic and
the mock evaluator is offline.

## Generic-dimension byte-comparability note

The generic dimension (s14 `generic_projection`) is byte-identical across
modes. Full canonical packet decisions still retain their s12 audit
`input_hash`; `generic_projection` omits the mode-derived hash because crr3
packet envelopes contain additive `domain_verification` evidence that would
otherwise make an otherwise mode-agnostic projection differ by mode.
