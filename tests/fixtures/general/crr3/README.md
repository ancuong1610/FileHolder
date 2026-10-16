# CRR3 general fixture (synthetic/public)

`old_di_raw.json` / `new_di_raw.json` are compact merged Azure Document
Intelligence (prebuilt-layout) JSON documents: `apiVersion`, `modelId`,
`stringIndexType`, `content`, `pages`, `paragraphs`, and one `tables` entry
with `rowCount`/`columnCount`/`cells`/`boundingRegions`. Spans, words, and
lines are omitted (optional for stages s02-s15). All page geometry and
bounding-region polygons are valid and inside the page bounds.

## Data provenance

**Fully synthetic.** The prose is invented CRR3-style amendment text written
for this fixture; it contains **no private BU content** and no real
confidential material. The only public reference is the base-instrument
citation `Regulation (EU) No 575/2013` in the title of both documents, which
is the exact base evidence the domain detector requires. German headings
(`Artikel N`) are used because the CRR3 amendment evidence shipped in
`test_data/` is the German official text and the verifier resolves
`Artikel N` article numbers.

## Behaviors exercised

- **Auto domain resolution**: both documents carry the exact base-instrument
  citation, so `auto` resolves `crr3` (confident) and domain verification is
  attached.
- **Heading modification** (accepted): `Artikel 92 Eigenmittelanforderungen
  für Institute` -> `Artikel 92 Eigenmittelanforderungen für Kreditinstitute`
  (similarity 0.877, automatic alignment, ACCEPT_DETERMINISTIC).
- **Prose modification** (accepted): `... zusätzliche Informationen jährlich
  zu melden.` -> `... monatlich zu melden.` (similarity 0.950).
- **Addition** (accepted): new heading `Artikel 92a Eigenmittelanforderungen
  für Krypto-Asset-Positionen` plus its prose paragraph.
- **Deletion** (accepted): the transitional-arrangements paragraph under
  `Artikel 2 Anwendungsbereich`.
- **Table modification** (accepted): one risk-weight cell `75%` -> `65%`
  (TABLE_MODIFIED, OLD-LT0001 -> NEW-LT0001).
- **Domain verification mix** (crr3 mode): amended articles (4, 92, 430,
  92a) resolve `ambiguous`; the unamended `Artikel 2` resolves `unsupported`;
  the table change resolves `ambiguous`. All statuses are additive metadata
  and never block generic resolution.

## Expected semantic examples (declared independently)

The fixture is exercised by the offline pipeline and regression tests in
`tests/test_pipeline.py` and `tests/test_regression_fixtures.py`.

| Category | Expected examples |
| --- | --- |
| MODIFIED | 2: heading pair (Institute -> Kreditinstitute); prose pair (jährlich -> monatlich) |
| ADDED | 2: `Artikel 92a ...` heading; crypto-asset prose paragraph |
| DELETED | 1: transitional-arrangements paragraph |
| TABLE_MODIFIED | 1: OLD-LT0001 -> NEW-LT0001 |
| REVIEW | none declared -> `not_applicable` |

## Mode matrix expectations

| Mode | Domain profile | Domain verification | Outcome |
| --- | --- | --- | --- |
| auto | crr3 (confident, auto) | ambiguous x5 + unsupported x1 | 5 changes + 1 table change, all accepted; review_queue empty |
| generic | generic (confident, explicit) | `not_applicable` on every change/table change; no domain review | identical generic result |
| crr3 | crr3 (confident, explicit) | ambiguous x5 + unsupported x1; 6 domain-review entries | identical generic result; nothing blocked |

## Declared metrics (auto mode, `CRR_RUN_LLM=mock`)

| Category | Precision | Recall | F1 | Counts (expected/predicted/matched) |
| --- | --- | --- | --- | --- |
| MODIFIED | 1.0 | 1.0 | 1.0 | 2 / 2 / 2 |
| ADDED | 1.0 | 1.0 | 1.0 | 2 / 2 / 2 |
| DELETED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| TABLE_MODIFIED | 1.0 | 1.0 | 1.0 | 1 / 1 / 1 |
| REVIEW | not_applicable | not_applicable | not_applicable | 0 expected |

The test asserts F1 >= 1.0 for every declared category (i.e. exact match
against the independently declared examples) and `not_applicable` for
undeclared categories. These floors are supported by the examples above and
are reproducible: the pipeline is deterministic and the mock evaluator is
offline.
