# Knowledge Transfer — Phase 1

This guide explains the Phase 1 runtime and how data changes at each step. It is
for developers, reviewers and coding agents who need to understand or operate
the project, not just edit its code.

**Code reference:** `fde80a5` (`refactor-phase-layout`). Thresholds and contracts
below describe that implementation, not universal document-comparison rules.
Examples are illustrative, not observations about the private EBA PDFs.

For the compact ownership map, read [architecture](architecture.md). For runnable
commands, configuration and acceptance checks, read
[phase testing](operations/phase-testing.md). Coding agents should also read
[AGENTS.md](../AGENTS.md).

**Input-location correction:** in the current usage, source PDFs are declared
outside the comparison workspace through `CRR_OLD_PDF` and `CRR_NEW_PDF`, commonly
under the repository's `input/` directory. Do not assume that a workspace contains
an `input/` directory. Moving source PDFs into each workspace is a possible future
improvement, **not implemented or required by this guide**. Some existing runtime
paths have workspace-local input conventions; those are identified separately
below and must not be confused with the externally declared source inputs.

## 1. The central design

> Phase 1 builds evidence for comparing two document versions. It does not give
> both PDFs to an LLM and ask the model to discover all differences.

Extraction, normalization, matching, token differences, table comparison and
deterministic routing do most of the work. The optional Stage 13 evaluator
provides advice; it does not own the final acceptance decision.

```text
OLD PDF (external path)              NEW PDF (external path)
             \                         /
              +-----------------------+
                          |
              01  Extract with DI / reuse chunks
              02  Validate extraction quality
              03  Build neutral internal models
              04  Normalize text conservatively
              05  Add provisional context / structure evidence
                          |
                 +--------+---------+
                 |                  |
              NON-TABLES           TABLES
              06  Exact matching    |
              07  Modified matching |
              08  Candidate / diff  |
              09  Movement /        |
                  resegmentation    |
                 |             10  Logical table comparison
                 +--------+---------+
                          |
              11  Deterministic routing
              12  Bounded review packets
              13  Optional evaluator
                          |
              14  Canonical result
              15  Reports, CSV, manifest and serving index
```

Three activities must stay separate:

| Activity | Question |
| --- | --- |
| Matching | Which OLD element corresponds to which NEW element? |
| Diff | Within that pair, which tokens or source spans differ? |
| Resolution | Is the difference accepted, non-semantic, suppressed, structural-only, or still under review? |

A successful alignment is not automatically an accepted substantive change.

Phase 1 includes **all 15 stages**, including report generation. Phase 2 is the
API/MCP/reader/agent interaction over saved results, not the second half of the
15-stage engine. Stage 13's evaluator and Phase 2's chat model are different uses
of model infrastructure.

## 2. Data concepts to understand first

### 2.1. Source inputs versus workspace

A typical externally configured run looks like this:

```text
repository/
├── .env
├── input/
│   ├── old version.pdf
│   └── new version.pdf
└── workspace/
    └── EBA_STEP2/
        ├── cache/
        │   └── di_chunks/
        └── output/
            ├── old_di_raw.json
            ├── new_di_raw.json
            ├── ...
            └── comparison_result.json
```

```dotenv
CRR_OLD_PDF="input/old version.pdf"
CRR_NEW_PDF="input/new version.pdf"
CRR_PROJECT_ROOT=workspace/EBA_STEP2
```

The workspace is a comparison's **cache and artifact root**. It is not itself a
PDF path. Relative PDF paths are resolved from the process working directory;
they are not implicitly relative to `CRR_PROJECT_ROOT`.

Two implementation conventions also exist:

- Without explicit PDF overrides, Stage 1 has a fallback path of
  `<workspace>/input/document/{old,new}.pdf`. This is a code fallback, not a
  description of where the current externally configured inputs are stored.
- The API upload workflow creates its own isolated job directories and stores
  uploaded copies under the job's `input/document/`. That upload workflow is
  separate from selecting external source paths for a direct Phase 1 run.

No input relocation is performed as part of this documentation task.

### 2.2. OLD and NEW are directional

```text
Only in OLD -> deletion candidate
Only in NEW -> addition candidate
Corresponding OLD/NEW with different content -> modification candidate
```

Reversing the PDFs reverses addition/deletion and old/new values. Preserve the
orientation in configuration, reports and explanations.

### 2.3. Elements and cells

The engine does not compare only two enormous strings. It works with elements:
paragraphs, headings, list items, footnotes, layout elements and tables. Tables
have cells with row/column coordinates and spans.

An abbreviated element might be:

```json
{
  "element_id": "OLD-P00012",
  "element_type": "paragraph",
  "text": "The maximum payment amount is 100 EUR.",
  "primary_page": 4,
  "reading_order": 25
}
```

IDs join evidence across artifacts. Element IDs are based on extraction
collection order; they are not permanent semantic identities guaranteed to
survive a different extraction or a changed document.

### 2.4. Three text representations

| Field | Purpose |
| --- | --- |
| `text` | Original extracted text, retained as source evidence. |
| `normalized_text` | Conservative Stage 4 normalization. |
| `comparison_text` | Stage 5 matching representation; may remove a detected margin-marker prefix. |

Illustrative transformation:

```text
text:            "M12 The payment pro-\ncess takes 2 days."
normalized_text: "M12 The payment process takes 2 days."
comparison_text: "The payment process takes 2 days."
```

The last transformation is allowed only when `M12` belongs to the detected
marker cohort. A string that merely resembles a code is not sufficient.

Separate representations let later stages ask whether a difference originated
in extraction, normalization or matching-specific filtering.

### 2.5. Two offset coordinate systems

- **DI spans** address the full extracted document's `content` using the DI
  string-index convention.
- **Token-diff character offsets** address one particular OLD/NEW representation
  string used to build that diff.

Normalized text may have a different length from raw text. Never apply a
normalized/comparison offset to raw text without verifying alignment. Renderers
have conservative checks and fallbacks for this reason.

### 2.6. Confidence is not one universal probability

DI word confidence, anchor confidence labels, matching similarity scores and
evaluator confidence describe different things. None is a calibrated probability
that the comparison is legally or semantically correct.

## 3. Pipeline orchestration and replay

Owner: `src/document_compare/phase1/pipeline/cli.py`.

The runner selects a workspace, sets `CRR_PROJECT_ROOT`, and executes the named
stage files in order. Stage completion and duration are printed. Unhandled
exceptions stop the run at that stage.

The complete run is **not one transaction**. If Stage 5 fails, Stage 1–4 artifacts
can remain available. Later stages often read multiple earlier artifacts, not
only the immediately preceding output.

For example, Stage 10 uses the Stage 5 structured documents and Stage 9 movement
metadata. Replaying a range requires the relevant earlier artifacts to exist
and match the expected contracts. Range replay writes selected outputs; preserve
the original evidence before intentionally replacing it.

```bash
# Reproduce Stage 5 locally from saved Stage 4 artifacts
python -m document_compare run --root "workspace/EBA_STEP2" --from-stage 05 --to-stage 05
```

The direct pipeline CLI does not load the repository `.env` for you; explicit
phase modes of `local_reviewer` do. See the operations guide for environment
precedence and for keeping downstream replay in mock mode.

## 4. Stage-by-stage logic and data transformations

Stage files below are under `src/document_compare/phase1/stages/` unless noted.

### Stage 01 — Extract PDFs with Document Intelligence

**Owner:** `s01_extract.py`.

**Purpose:** turn each PDF into machine-readable text, layout, tables and source
locations. This stage does not compare OLD against NEW.

**Input:** externally configured PDF paths, or supported existing chunk caches.

#### Processing

1. Resolve the configured OLD and NEW PDF paths. If a source PDF exists, take the
   PDF-based extraction/cache path. Otherwise the runtime can try the legacy
   document-name chunk-cache path; it does not invent empty extraction.
2. Read the PDF page count, unless an explicit debug first-page range is used.
3. Split requested page ranges into chunks of at most **100 pages**.
4. Calculate the PDF's full-file SHA-256 and use it as the PDF-based cache key:
   `cache/di_chunks/<pdf-sha256>/`.
5. For each chunk, reuse the matching saved result unless force-reextraction is
   requested. If absent, call DI's `prebuilt-layout` model and save the result
   immediately. A later failed chunk does not delete earlier successes.
6. Merge chunks into one raw document and validate the merged result.

The implementation sends the PDF bytes with a `pages` range; it does **not**
physically create smaller PDF files for each chunk. Missing-cache extraction is
a real provider operation, even when Stage 13 is configured as mock.

The extraction helper retries only transient errors. Its current default is at
most three total calls, with exponential waits of 2 and 4 seconds. Auth/config
and other non-retryable failures stop without that retry loop.

#### Why merging is more than concatenation

- **Page numbers:** a provider may restart numbering inside a chunk. Nested
  page numbers are adjusted when needed; top-level pages are canonicalized from
  the requested physical range.
- **Content spans:** later chunk offsets must be shifted by the content already
  merged, including the separator. The DI string-index type matters.
- **Element references:** chunk-local pointers such as `/paragraphs/0` must be
  rebased against the corresponding collection's cumulative length. A pointer
  in the second chunk must not accidentally refer to the first chunk's element.

Validation checks complete ordered pages, duplicate/missing pages, in-bounds
spans and resolvable references. Merge metadata records reference-rebasing
support/version. An older cached merged artifact with structured references but
without suitable rebasing metadata is not trusted for direct reuse; saved
chunks can be remerged locally instead.

**Output:** `old_di_raw.json`, `new_di_raw.json` in `output/`, plus chunk caches.
Raw results can contain `content`, pages/words/lines, paragraphs, tables/cells,
roles, spans, geometry, confidence and other provider collections.

**Not established:** extraction correctness for every character, semantic
equivalence, or any addition/deletion. A merged raw JSON file alone is not the
same as a complete Stage 1 chunk cache. To consume already-extracted raw files,
starting from Stage 2 may be appropriate.

### Stage 02 — Validate extraction quality

**Owner:** `s02_validate_extraction.py`.

**Purpose:** identify extraction defects before treating them as document edits.
If NEW extraction loses a paragraph, that is not automatically proof that the
source document deleted the paragraph.

**Input:** the two raw DI documents; source PDFs are optional for native-text
cross-checking.

#### Checks

| Area | What is inspected |
| --- | --- |
| Page sequence | Ordered pages starting at one; missing/duplicate pages. |
| Spans | Valid offset/length, content bounds, and exact content agreement where supported. |
| Polygons | Shape, finite coordinates, known page and page bounds. |
| Tables | Positive dimensions, cell indices/spans, bounds, overlaps and locations. |
| Confidence/scale | Word/line counts, mean/min confidence and low-confidence words. |
| Structure diagnostics | Role counts, heading/TOC-like patterns and recurring-margin candidates. |
| Native PDF text | Per-page token coverage and text deficit compared with PyMuPDF extraction. |

The current low-word-confidence diagnostic threshold is **0.97**. It is not a
universal rejection threshold or a semantic confidence score.

Native-text checks distinguish warnings from review signals. For example:

- Native text exists but DI text does not: review signal.
- Low native coverage or a large token-count delta: diagnostic warnings.
- Native coverage below **0.80** together with a DI token deficit above **0.20**:
  probable missing text requiring review.

If the PDF is unavailable, native validation is explicitly unavailable. A scanned
PDF with no native text does not magically provide ground truth for DI OCR.

**Transformation:** create quality reports; do not rewrite raw DI text to hide
problems. Document flags and `requires_review` are recorded. A quality report
requiring review is not the same as an unhandled contract failure that stops the
pipeline. Recurring-margin diagnostics here are not the Stage 5 furniture
classifier's final decisions.

**Output:** `old_extraction_validation.json`, `new_extraction_validation.json`.

### Stage 03 — Build neutral internal document models

**Owner:** `s03_internal_schema.py`.

**Purpose:** decouple comparison logic from the DI-specific response shape.

**Input:** raw extraction and validation reports (required under the current
default `REQUIRE_VALIDATION_REPORTS`).

**Output:** `old_document.json`, `new_document.json`, schema `0.2.0`.

An abbreviated wrapper is:

```json
{
  "schema_version": "0.2.0",
  "processing": {},
  "document": {
    "document_id": "DOC-OLD",
    "source": {},
    "validation_summary": {},
    "pages": [],
    "elements": [],
    "statistics": {}
  }
}
```

#### Element construction

- Paragraph IDs resemble `OLD-P00001`; table IDs resemble `OLD-T0001`; cell IDs
  resemble `OLD-T0001-C0001`.
- Preserve raw text, original role, source collection/index, spans, bounding
  regions, primary page/polygon and confidence evidence.
- Derive neutral types such as header, footer, page number, heading, list item,
  footnote or paragraph. Footnotes are not automatically excluded like headers.
- Build table cells with row/column coordinates, spans, kind and locations.

#### Duplicate table representation

DI may return the same text both as a paragraph and as table cells. The stage
finds paragraph/table polygon overlap, using a **0.5** overlap threshold, and
records the best table representation when supported:

```json
{
  "inside_table": true,
  "represented_by_table_id": "OLD-T0001",
  "comparison_eligible": false,
  "exclusion_reasons": ["duplicate_table_representation"]
}
```

The paragraph remains in the model as evidence; it is not independently counted
again by non-table matching. Headers/footers/page numbers are also marked as
ineligible for comparison according to the neutral classification.

#### Ordering and quality

Reading order is primarily based on the first source span offset, with source
priority/order tie-breakers; it is not simply the PDF page number or Y position.
Each element gets `reading_order`.

Word confidence is aggregated for words inside element spans. Page-level native
validation evidence is propagated to elements on those pages. The document-level
integrity summary remains separate: do not assume every document flag becomes
the same per-element review flag.

#### Structure is not authoritative yet

The model records DI/lexical heading evidence, including conflicts. It does not
create an authoritative hierarchy; `section_path` starts empty and hierarchy
knowledge is explicitly unknown.

Raw DI may contain figures, styles and key-value collections, but this neutral
comparison path primarily models paragraphs and tables. There is no general
pixel/image-difference branch just because a raw result contains figure metadata.

### Stage 04 — Normalize text conservatively

**Owner:** `s04_normalize.py`.

**Purpose:** reduce representational noise while preserving source strings.

**Input:** Stage 3 models. **Output:** `old_document_normalized.json`,
`new_document_normalized.json`.

The stage deep-copies the source model and adds `normalized_text` and
`normalization_flags` to elements and table cells. It does not rewrite `text`,
IDs, source locations or raw spans.

Rules include:

1. Unicode NFC.
2. Soft-hyphen removal.
3. Non-breaking-space normalization.
4. CRLF/CR line-ending normalization.
5. Selected quote/dash variant canonicalization.
6. Guarded end-of-line hyphen joining.
7. Whitespace collapse and trimming.

For a visible newline-hyphen join, the continuation must begin lowercase and
must not be a suspended-hyphen stopword. Semantic same-line hyphens such as
`risk-based` are not simply removed. Uppercase, digit/reference or suspended
continuations that fail the rule retain a visible boundary.

```json
{
  "text": "The pro-\ncess takes 2 days.",
  "normalized_text": "The process takes 2 days.",
  "normalization_flags": ["line_break_hyphenation_joined"]
}
```

There is no blanket lowercasing at this stage, numeric rewriting, punctuation
deletion, dictionary-based correction, general OCR repair or arbitrary list-label
rewrite. The normalization contract is intended to be idempotent.

### Stage 05 — Add provisional context and structure evidence

**Owners:** `s05_anchors.py`, `phase1/structure/furniture.py`,
`phase1/structure/reconstruction.py`.

**Purpose:** supply evidence-backed landmarks and structural hypotheses without
pretending that DI roles or a reconstructed TOC are authoritative hierarchy.

**Input:** normalized documents. **Output:** `old_document_structured.json`,
`new_document_structured.json`, schema `0.3.0`.

#### A. Detect a repeated left-margin marker cohort

The current detector looks for short letter-and-digit marker-like text near the
left margin: up to approximately **16%** of page width, with at least **5**
candidates on at least **3** pages. Eligibility, element type and geometry matter.
A single code-looking string is insufficient.

It produces marker IDs/texts and a profile of the evidence.

#### B. Create comparison text

Start with normalized text. Remove a leading prefix only when it matches an
actually detected marker, longest known marker first. Keep the original and
normalized strings; record `comparison_text_flags` and the removed prefix.

#### C. Derive page furniture once

The classifier combines recurrence, a supporting cohort of layout-role
occurrences, and shared page-edge geometry. Current lower bounds include at
least three occurrences on three pages and supporting layout occurrences on
multiple pages, with additional share/geometry gates.

Same-text body occurrences outside the supported edge band are not automatically
classified as furniture. Roles/types/text/spans/IDs are not rewritten. The same
classification is reused for anchor rejection and structure reconstruction.

#### D. Create provisional anchors

DI heading/title evidence is stronger provisional evidence. A guarded fallback
can recognize short Arabic/dotted/Roman/alphabetic heading-like labels, with
conditions on punctuation, uppercase title text and valid geometry. Its bounds
include at most **6 words** and **100 characters** for the numbered fallback.

Tables, ineligible/layout elements, margin markers and derived furniture do not
become provisional anchors.

#### E. Attach recent-anchor context

Each element gets its nearest anchor and a rolling window of up to **6** recent
anchor IDs. An anchor is made current before its own context is attached.

```json
{
  "provisional_anchor_context": {
    "nearest_anchor_id": "DOC-OLD-A00003",
    "recent_anchor_ids": ["DOC-OLD-A00002", "DOC-OLD-A00003"],
    "context_is_authoritative_hierarchy": false
  }
}
```

**Recent anchors are not a parent-to-child section path.** The window may include
sibling headings or other nearby landmarks.

#### F. Detect TOC sources

Explicit supported headings include `Contents`, `Table of contents`, `Inhalt`
and `Inhaltsverzeichnis`. Entries can be text lines ending in printed page
numbers, split title/page elements, or title/page table rows.

An unheaded table candidate needs corroboration from body headings; not every
table with numeric cells is a TOC. Table-overlapping paragraphs are accounted
for without duplicating the table's TOC representation.

#### G. Link entries to body headings conservatively

Canonical exact titles support linking. Repeated titles can be disambiguated by
the full numeric label **and namespace**, not just the bare title or number.
Contradictory parsed labels remain unmatched. Multiple remaining candidates stay
ambiguous rather than selecting the first occurrence.

Printed TOC page numbers and physical PDF page numbers are separate evidence;
their disagreement is recorded, not silently corrected.

#### H. Create section hypotheses

Dotted numeric labels can support levels such as `2` -> level 1 and `2.1` ->
level 2. Parent evidence must match the proper numeric prefix and namespace.
Duplicate or missing parent labels stay uncertain.

Without a usable TOC, body-heading fallback remains flat rather than inventing
a certain tree. Unlisted body headings remain visible as section hypotheses.

#### I. Assign membership and validate

Each element is accounted for in exactly one membership category:

```text
layout | toc | section | preamble | uncertain
```

Validation checks coverage/counts, unique identities, resolvable references and
consistent furniture/heading membership. Derived furniture must be `layout`
and must not be a section heading.

#### The reported EBA failure mechanism and correction

Previously a recurring header whose text was `Contents` could be both derived
furniture and a TOC source. TOC-first membership produced `toc`, violating the
validator's required `layout` category.

The correction excludes furniture from TOC heading/entry/table-source processing,
skips it rather than terminating multi-page TOC scanning, skips it when finding
table adjacency and overlap sources, and prioritizes layout membership. The
validator remains enabled. Synthetic tests reproduce the original exception;
they do not substitute for verification on the private EBA pair.

#### Important boundary

`structure_evidence` is additive. Matching primarily uses comparison text and
provisional anchor context; it does not treat the TOC/section graph as certain.
Derived furniture classification does **not** itself change raw types or
`comparison_eligible`. Therefore anchor/TOC exclusion is not a claim that every
mislabelled running header disappears from every subsequent matching path.

### Stage 06 — Exact non-table matching

**Owner:** `s06_exact_matching.py`.

**Purpose:** remove confidently identical comparison representations before
bounded fuzzy matching.

**Input:** structured pair. **Output:** `exact_element_matches.json`.

Candidate extraction excludes ineligible elements, tables, empty normalized
text and detected repeated non-semantic margin markers. Tables are owned by
Stage 10. Comparable elements are compacted to the fields needed downstream;
anchor IDs are translated to comparable anchor keys.

The exact key is:

```text
(element_type, comparison_text)
```

- If a key appears exactly once on both sides, pair it globally. The page need
  not be the same.
- With duplicate text, group by the **complete ordered recent-anchor-key tuple**.
  A shared tuple must be nonempty and have equal occurrence counts; those
  occurrences are paired in order.
- Unresolved duplicates stay in ambiguous groups and in the unmatched lists.

Matches record method, confidence, context relation, assignment ambiguity and
page/order-change metadata. A rule score of `1.0` is not semantic certainty.

Outputs include `matches`, `ambiguous_duplicate_groups`, `unmatched_old`,
`unmatched_new` and ignored-marker records.

**Exact means exact under the comparison key**, not byte-identical PDFs or
necessarily identical source strings. Duplicate occurrence alignment still
records source-assignment ambiguity where appropriate.

### Stage 07 — Match residual elements as possible modifications

**Owner:** `s07_modified_matching.py`.

**Purpose:** find supported OLD/NEW counterparts rather than reporting each
small edit as unrelated deletion and addition.

**Input:** Stage 6 residuals. **Output:** `modified_element_matches.json`.

#### Retrieval and scoring

1. Retrieve same-type NEW candidates with shared provisional context, bounded
   to **40** candidates.
2. If necessary, use rare informative tokens, bounded to **30** candidates. If
   the context path produces no pair above the review floor, a rare-token retry
   is also available.
3. Cheap pre-rank with `0.80 * token_F1 + 0.20 * length_ratio`.
4. Keep at most **12** expensive candidates per OLD per shortlist pass.
5. Compute:

```text
score = 0.65 * character_SequenceMatcher_ratio
      + 0.25 * token_F1
      + 0.10 * length_ratio
```

Provisional context is a retrieval accelerator and recorded evidence. It adds
**zero** to the content-similarity score. This is not semantic embedding search.

Pairs below **0.72** are not retained in the qualifying rankings. Selection
requires reciprocal best: OLD's best NEW must also choose that OLD as best.
The minimum advantage over the runner-up is measured on both sides.

Automatic alignment candidacy requires score >= **0.86**, best margin >=
**0.10**, and neither side being a short text under **40 characters**. Existing
review reasons still matter: even a pair in the modified alignment group can
carry `needs_review`, for example when shared anchor support is absent.

#### Identical-text guard and residuals

Identical comparison text left unresolved by Stage 6 becomes
`duplicate_exact_review`, not a fabricated modification. Other selected pairs
become modified candidates or modified-review pairs. Unconsumed OLD/NEW elements
continue as residuals.

The output records similarity components, reciprocal/margin evidence, retrieval
method and review reasons. "Automatic alignment" is not final acceptance.

**Limit:** bounded retrieval/top-K is a performance/recall trade-off. The true
counterpart may not enter the shortlist, or may change too much to match.

### Stage 08 — Assemble candidates and representation-specific token diffs

**Owner:** `s08_candidates.py`.

**Purpose:** convert match outputs into a uniform candidate dataset.

**Input:** Stage 6 and 7 results. **Output:** `comparison_candidates.json`.

| Matching evidence | Candidate group |
| --- | --- |
| Exact match | `UNCHANGED` |
| Modified alignment | `MODIFIED_CANDIDATE` |
| Review alignment | `MODIFIED_REVIEW` |
| Identical text / ambiguous assignment | `DUPLICATE_EXACT_REVIEW` |
| Residual OLD | `DELETED_CANDIDATE` |
| Residual NEW | `ADDED_CANDIDATE` |

Paired modifications get three separate token diffs:

```text
source_token_diff
normalized_token_diff
comparison_token_diff
```

Tokenization retains words, numbers and punctuation/operator signals. The diff
uses `SequenceMatcher` operations (`replace`, `insert`, `delete`), with token
ranges, representation-local character ranges, exact OLD/NEW segments, context
tokens and counts. For example, `100 EUR` -> `150 EUR` retains a numeric
replacement rather than only a full-paragraph similarity score.

If different comparison strings have no token-sequence change, the candidate is
marked `comparison_token_equivalent` and review evidence is attached. Source or
normalized differences may still contain a meaningful signal.

One-sided candidates carry extraction-quality reasons. Absent/malformed quality
metadata is unknown, not proof of safe extraction. Conservation checks ensure
each comparable element belongs to the expected groups exactly once.

"Candidate" remains intentional: final add/delete/change classification has not
yet occurred.

### Stage 09 — Movement, resegmentation and structural split/merge evidence

**Owners:** `s09_movement.py`, `phase1/matching/reconciliation.py`.

**Input:** Stage 8 candidates. **Output:**
`comparison_candidates_with_movement.json`.

#### A. Globally unique exact residual relocation

A residual OLD/NEW pair with the same type/text must be globally unique in both
complete comparable-element collections, not just unique in a small local list.
Supported pairs become `RELOCATED_UNCHANGED` and consume the corresponding
provisional deletion/addition, retaining references to both originals.

A page or absolute reading-order shift alone is not primary movement proof.
Inserting a cover page can shift many pages without relocating their content.

#### B. Generic resegmentation reconciliation

Near-twin residuals can represent extraction splits/markers/hyphenation rather
than unrelated edits. Token Jaccard/containment, length compatibility,
reciprocal-best and margin gates support a 1:1 pair. Defaults include score
**0.80** and margin **0.05**.

Pairs become `RESEGMENTATION_REVIEW`, never automatically accepted unchanged.
Structural residuals are excluded from this generic reconciler and retained for
the structural split/merge owner.

**Quantitative safety:** short-token overlap can miss digits, signs or units.
For number-bearing pairs, raw evidence must agree under strict layout-only
normalization. Differences such as `21 -> 99`, `-21 -> 21`, `<21 -> >21`,
`1/2 -> 1 2`, `21kg -> 21g`, or a scale-word change remain visible residuals
instead of being swallowed as cosmetic near-twins.

#### C. Contiguous structural block movement

Among paired structural records, search for a block of **3–12** elements whose
internal order is preserved and which is contiguous in NEW. It must shift
coherently by at least **8** structural ranks and cross at least **3** external
records. Overlapping/nested detections are reduced to avoid review flooding.

Result: `MOVEMENT_BLOCK_REVIEW`. This is evidence-only, not a certain semantic
movement decision or a hierarchy claim.

#### D. Exact structural split/merge and boundary evidence

Detect one OLD element equal to the concatenation of two nearby NEW structural
elements, or the reverse. Record `STRUCTURAL_SPLIT_MERGE_REVIEW`; arbitrary
fuzzy split/merge is not enabled. These evidence records **do not consume** the
underlying add/delete candidates.

Paired structural text can also generate boundary-review evidence without
reclassifying the original candidate.

Existing paired groups are carried forward, with residuals, relocated records,
resegmentation pairs and evidence-only queues. Validation checks identity/count
conservation and prevents duplicate consumption or review evidence silently
removing candidates.

### Stage 10 — Compare logical tables, rows and cells

**Owner:** `s10_tables.py`.

**Purpose:** handle tables as structured data rather than flattened paragraphs.

**Input:** Stage 5 structured documents and Stage 9 metadata. **Output:**
`table_comparison_result.json`.

#### A. Physical tables become logical tables

Consecutive physical fragments can represent one multi-page table. Joining
requires adjacent pages, equal column count and repeated-header evidence:
header similarity >= **0.80** with shared context, or >= **0.92** when context
support is missing/different. Equal column count alone is insufficient.

Repeated continuation headers may be omitted from logical body rows, but omitted
rows/cells are counted explicitly. Logical cells preserve physical source-table
and source-row identities.

```text
physical cells = cells placed in logical rows
               + documented repeated-header omissions
```

#### B. Match logical tables

Exact matching uses column count and logical text signatures. Duplicate exact
tables need supported assignment rather than arbitrary pairing.

Remaining tables use:

```text
combined similarity = 0.65 * content
                    + 0.20 * header
                    + 0.10 * column shape
                    + 0.05 * row shape
```

Candidate gates depend on context support. Selected fuzzy matches require
reciprocal best, combined score >= **0.62**, and margins >= **0.03** on both
sides. Context difference is not movement proof.

#### C. Align rows

Sequence matching aligns exact row signatures first. Replacement blocks use
reciprocal fuzzy matching with similarity/margin gates and monotonic ordering.
Large blocks have a position-based fallback to bound all-pairs work. Unmatched
rows remain deleted/added rows.

#### D. Compare cells

Cells in paired rows are compared by column index, including row/column span and
kind changes. Outcomes include:

| Cell classification | Meaning |
| --- | --- |
| `UNCHANGED` | Equal at the cell's source comparison level, without structural change. |
| `LAYOUT_EQUIVALENT` | Equivalent under the table's conservative layout policy. |
| `MODIFIED` | A content difference is retained. |
| `ADDED` / `DELETED` | Cell exists on one side. |
| `STRUCTURE_CHANGED_REVIEW` | Span/kind/structural evidence needs review. |
| `EXTRACTION_ARTIFACT_REVIEW` | Equivalence appears only under stronger artifact hypotheses. |

Checkbox selection-state changes are considered; deleting selection markers is
not sufficient to prove equivalence.

There are separate conservative and aggressive normalizations. The aggressive
version is review-only, not automatic equivalence proof. Importantly, table
`layout_text` **case-folds**; unlike Stage 4's general normalization, a case-only
table difference can be classified layout-equivalent. Raw cell strings remain.

#### E. Aggregate and preserve unresolved work

Cell outcomes determine table classification. Reasons can include changed
column count, merged cells/header kind, near-threshold table similarity, low row
alignment, context differences, extraction review and cell/row boundaries.

Unmatched tables on both sides in the same provisional context become an
`unresolved_table_group`, not prematurely finalized add/delete tables. Other
residuals become one-sided candidates, retaining cell content when available.

Physical-table conservation and exactly-once cell accounting are validated.

### Stage 11 — Route cases with deterministic rules

**Owner:** `s11_routing.py`.

**Purpose:** determine the decision path for each case, separately from matching
and report rendering.

**Input:** movement/candidate and table results. **Output:** `review_routing.json`.

| Route | Meaning |
| --- | --- |
| `ACCEPT_DETERMINISTIC` | Current rules support accepting the change. |
| `NON_SEMANTIC` | Not a substantive content-change case. |
| `SPECIAL_CASE_RULE` | A special pattern/rule applies; not automatically suppression. |
| `SPECIAL_CASE_LLM_OPTIONAL` | Optional bounded evaluator advice can be requested. |
| `HUMAN_REVIEW` | Retain as unresolved human work. |

Order matters: the first applicable rule controls the route.

#### Main decision branches

- Relocated unchanged records are non-semantic; duplicate exact assignment is
  human review.
- Missing/malformed/review-required extraction evidence must not be treated as
  permission for artifact suppression.
- Hyphen/whitespace-only appearances are nominations, not proof. `re-sign` versus
  `resign`, or `now here` versus `nowhere`, can have meaningful boundaries.
  Suppressing a wrap requires appropriate raw-source newline proof for every
  changed boundary and no protected semantic/value signal.
- Other detectors nominate possible word splits, markers, heading boundaries,
  tokenization-neutral changes, list-label differences and OCR-like single
  glyphs. Numeric guards prevent values from being dismissed as markers/glyphs.
- Modified-review pairs go to optional evaluation unless an earlier rule applies.
- One-sided elements are checked for matching residual text on the opposite
  side, review evidence, and an aligned-coverage frontier. Unsupported absence
  must not become a confident addition/deletion.
- The frontier is a heuristic from the paired groups inspected by `_frontier()`,
  with a margin of **12** reading-order positions, not a complete-page coverage
  certificate.
- A clear one-sided or modified case without applicable review evidence can be
  accepted deterministically.
- Tables and movement/split/merge/boundary/resegmentation evidence get explicit
  routes and their own traceable identities. One-sided tables go to optional
  evaluation; unresolved table groups remain human review.

`generic_review_list` selects unresolved human/optional routes. A
`SPECIAL_CASE_RULE` route can still end as review under Stage 14; do not use
this intermediate list as the sole final review inventory.

Legacy `CRR_` names do not mean the runtime is performing legal/domain
verification. Domain verification is retired; generic comparison handles the
document content.

### Stage 12 — Build bounded review packets

**Owner:** `s12_packets.py`.

**Purpose:** provide evidence for one eligible case without sending an unbounded
document/report to the evaluator.

**Input:** routing and the referenced evidence. **Output:**
`llm_review_packets.json`.

Only `SPECIAL_CASE_LLM_OPTIONAL` routes get packets. Human-review cases are not
automatically all sent to the model.

Depending on source kind, a packet contains compact old/new elements, similarity,
diffs, changed cells, structural evidence, references, case/review reasons and
allowed outcomes. It is one case per packet.

The complete serialized packet must fit **6,000 characters**, not 6,000 tokens.
Deterministic compaction reduces text caps and configured list lengths; a
minimal fallback preserves identity and key old/new/classification evidence. If
it still cannot fit, packet generation fails rather than silently removing the
bound. Identifiers receive special preservation treatment.

`input_hash` traces the evaluated envelope. Its presence alone is not a claim
that every artifact in a reused workspace has been cryptographically validated
against every other artifact. Packets can omit context through intentional
compaction, which limits what an evaluator can know.

### Stage 13 — Run the optional evaluator

**Owner:** `s13_llm_evaluator.py`.

**Input:** bounded packets. **Output:** `llm_review_decisions.json`; mock mode
also writes `llm_review_responses.jsonl` request/response trace metadata.

| `CRR_RUN_LLM` | Behavior |
| --- | --- |
| `false` | No model call; each packet becomes `UNCERTAIN`, confidence 0, evaluator `not_run`. |
| `mock` | Deterministic rule-based outcomes exercise the response contract without a provider. |
| `real` | Call the configured internal hub or OpenAI-compatible provider. |

The three outcomes are `REAL_CHANGE`, `ARTIFACT` and `UNCERTAIN`.

```json
{
  "outcome": "UNCERTAIN",
  "confidence": 0.3,
  "reason": "Insufficient evidence",
  "evidence_refs": ["OLD-P00012"]
}
```

The whole response must parse as one strict JSON object: no prose/fence
extraction, unsupported extra keys, invalid outcome, out-of-range confidence or
malformed reason/references. Provider or parser failures produce a conservative
`UNCERTAIN` fallback with the failure reason; they do not automatically accept
or suppress the case.

The evaluator is instructed not to rematch documents or invent missing source
content. Mock mode proves contract behavior, not real reasoning accuracy.

**An LLM `REAL_CHANGE` result does not automatically turn a review case into
final accepted.**

### Stage 14 — Build the canonical comparison result

**Owner:** `s14_canonical_result.py`.

**Purpose:** create the single backend comparison contract. Matching is not
recomputed.

**Input:** Stage 9 movement, Stage 10 tables, Stage 11 routing and optional Stage
13 decisions. **Output:** `comparison_result.json`, with
`comparison_stage = phase_1_final_comparison`.

Input-stage markers, unique route/candidate identities and route coverage for
consumed candidates/evidence are validated before producing a result.

#### Resolution

| Evidence/route | Final status |
| --- | --- |
| `ACCEPT_DETERMINISTIC` | `accepted` |
| `NON_SEMANTIC` | `non_semantic` |
| Proven suppressible special-rule case | `suppressed_artifact` |
| The defined deterministic marker/structural case | `structural_only` |
| Optional-LLM, human, unknown or non-finalized special case | `review` |

Suppression is a specific whitelist with safeguards, not the meaning of every
`SPECIAL_CASE_RULE`. Stale/unsafe hyphen-wrap routes without required proof, or
with protected semantic-signal reasons, stay under review. List-label, glyph,
structural or table-artifact nominations must not be assumed suppressed merely
from their route name.

An evaluator decision is preserved as `packet_decision`, with method
`llm_advisory`. It does not give the model authority to accept, suppress or mark
structural-only. This is a legitimate combination:

```text
packet_decision.outcome = REAL_CHANGE
resolution.status      = review
```

#### Canonical collections

- **`changes`:** non-table `MODIFIED`, `ADDED`, `DELETED`, including both accepted
  and review records. The count is not the count of confirmed changes.
- **`table_changes`:** `TABLE_MODIFIED`, `TABLE_ADDED`, `TABLE_DELETED`, with
  resolution and cell details.
- **`review_queue`:** unresolved changes, duplicate assignments, movement,
  split/merge/boundary, resegmentation and table reviews. Entries are deduplicated
  by source evidence/candidate identity and case type, not by text alone.
- **`non_semantic_events`:** traceable suppressed artifacts, structural-only
  events and relocated-unchanged evidence. Suppression is not history deletion.
- **`unresolved_table_reviews`:** table groups that could not safely become
  finalized matched/add/delete cases.

Records retain typed IDs, source-candidate links, old/new values, diff evidence,
resolution and provenance. Element changes also have content fingerprints.
IDs are scoped to the artifacts/run; do not assume permanent cross-run semantic
identity when extraction/order/input changes.

Compatibility fields such as `domain_profile`, `domain_verification_review`,
`outcome_migration` and `generic_projection` do not add a live legal approval
gate. Retired verification cannot override generic resolution. Invariants
include unique typed change IDs and no accepted item that still needs review.

The canonical result is a **delta/evidence result**, not the complete original
document models. Full surrounding source context still needs the structured
documents.

### Stage 15 — Render and export, without re-deciding changes

**Owners:** `s15_serve.py`, `phase1/reporting/`.

**Purpose:** present the canonical result to people and downstream consumers.
Rendering does not recompute matching or change canonical resolution.

#### `phase2_change_index.json`

Despite its historical name, creating this artifact belongs to Phase 1. It
contains **accepted** changes as compact units, grouped by provisional-anchor
buckets. Units carry old/new text/page references, compact token operations and
bounded changed-cell information.

It is not the complete canonical result: review stays separate, suppressed
events are not semantic units, and details can be capped. The Phase 2 bounded
reader reads the canonical result; it is not restricted to this accepted-only
index.

#### `comparison_redline.pdf`

A newly rendered ReportLab report, not annotation injected into the original
source PDF. It shows old/new source strings, changed spans and review status:
removed/replaced text is struck red, inserted/replaced text is underlined red,
and possible wraps carry an explicitly unverified label.

If stored offsets cannot be verified against source text, rendering realigns
conservatively, shows differences and emits a warning rather than trusting bad
offsets. Review records remain visible, with a review-queue section.

#### `comparison_inline.pdf`

An additive one-flow view: unchanged context once, deleted old text struck red,
inserted new text blue. It does not modify the canonical result.

Cleanup needs source proof: a discretionary soft hyphen, or an explicit
newline-hyphen whose exact joined word is corroborated by the opposite side.
Possible-wrap labels alone are not proof. Permanent hyphens, meaningful word
boundaries, numbers, signs and negations remain protected; unproven joins stay
visible with warnings. No dictionary, LLM or download invents a word.

Whole layout-only items can be omitted only under the renderer's strict
equivalence/alignment/non-review conditions. A review item stays visible.
Unchanged table-cell text is omitted only when raw old/new strings really agree.

#### `inline_pdf_audit.json`

Records join/omission counts, warnings and limitations so presentation cleanup is
auditable instead of hidden.

#### `bu_comparison_redline.html`

Walks OLD reading order with modifications/deletions at OLD positions and
additions anchored near preceding matched OLD positions. It includes accepted
table changes. This view renders **accepted changes only**; open review work is
not presented as confirmed inline content.

Some previews/details are bounded. The HTML is not a replacement for complete
canonical evidence; its item count can differ from PDF/canonical collections.

#### `review_queue_export.csv`

Exports the final review queue for triage, with review/source IDs, case type,
route, status, reasons and available references. It uses UTF-8 BOM and a `;`
delimiter for Excel-friendly handling.

#### `bu_comparison_review_manifest.json`

Lists report/index artifact names and counts so a consumer can navigate the
bundle. It does not approve the comparison's semantic correctness.

## 5. One example through the whole pipeline

```text
OLD:
3. Payment limits
The maximum payment amount per transaction is 100 EUR.

NEW:
3. Payment limits
The maximum payment amount per transaction is 150 EUR.
```

This is an illustrative path, not a guarantee of an unmeasured matching score:

| Stage | What happens to this example |
| --- | --- |
| 01 | Extract heading/paragraph text, pages, spans and geometry. |
| 02 | Inspect extraction quality on both sides. |
| 03 | Create internal IDs/types and attach quality evidence. |
| 04 | Add normalized text without rewriting `100` into another value. |
| 05 | Create provisional heading context and comparison representations. |
| 06 | Heading may match exactly; the changed paragraph remains residual. |
| 07 | Paragraphs may become a supported reciprocal modified alignment. |
| 08 | Retain `100 -> 150` as token replacement with representation-specific evidence. |
| 09 | Quantitative safeguards must not swallow it as cosmetic resegmentation. |
| 10 | Tables follow their separate structured branch; this paragraph is not a table. |
| 11 | Accept only under the applicable rules; weak quality/alignment evidence keeps review. |
| 12 | Create a packet only if the route is optional evaluation. |
| 13 | Disabled/mock/real evaluator can provide a recommendation. |
| 14 | Resolve under deterministic route rules and preserve advice/uncertainty. |
| 15 | Render/export according to the final status, without changing it. |

The same visible `100 -> 150` edit can have different statuses in two runs if
their extraction or matching evidence differs. Similarity alone is not the
acceptance decision.

## 6. Artifact inventory and controlled information reduction

Artifacts below are relative to `<workspace>/output/`:

```text
01  old_di_raw.json
    new_di_raw.json
02  old_extraction_validation.json
    new_extraction_validation.json
03  old_document.json
    new_document.json
04  old_document_normalized.json
    new_document_normalized.json
05  old_document_structured.json
    new_document_structured.json
06  exact_element_matches.json
07  modified_element_matches.json
08  comparison_candidates.json
09  comparison_candidates_with_movement.json
10  table_comparison_result.json
11  review_routing.json
12  llm_review_packets.json
13  llm_review_decisions.json
    llm_review_responses.jsonl       # mock trace
14  comparison_result.json
15  phase2_change_index.json
    comparison_redline.pdf
    comparison_inline.pdf
    inline_pdf_audit.json
    bu_comparison_redline.html
    review_queue_export.csv
    bu_comparison_review_manifest.json
```

Stage 1 chunk caches live under the workspace's `cache/`, not in this inventory.
Mode-specific artifacts are not always present. The offline demo also writes
`mock_run.json` provenance and seeds already-extracted synthetic raw DI; it runs
Stages 02–15 and does not prove live extraction or compare your uploaded PDFs.

Data changes in three ways:

1. **Additive enrichment:** original elements gain normalized/comparison text,
   context and structural hypotheses while source evidence remains saved.
2. **Comparison-record construction:** elements become matches, candidates and
   canonical changes. Compact records carry only required fields, not every
   field from every earlier object.
3. **Consumer projection:** canonical data becomes accepted-only indexes,
   bounded packets/previews and presentation-oriented reports. Unchanged text,
   review records or long details may be excluded from a particular projection.

Consequently, do not reconstruct the complete source from a preview or assume
every field is copied forever. Return to the artifact that owns the evidence.

## 7. Invariants, limits and how to investigate a result

### What the design tries to enforce

- Preserve source text, identities, locations and provenance.
- Separate layout normalization from meaningful edits.
- Treat inferred structure as supporting evidence, not certain hierarchy.
- Separate matching, differences and final resolution.
- Conserve comparable elements, physical tables and cells under the documented
  accounting rules; do not double-consume or silently lose them.
- Keep unresolved evidence in review, including structural and table groups.
- Keep evaluator advice subordinate to deterministic final rules.
- Keep presentation transformations out of canonical decision-making.

### What a successful run does not prove

- Every extracted character is correct.
- Every true counterpart was found by bounded retrieval.
- High string similarity means equivalent meaning.
- The TOC graph is correct for every PDF.
- Derived furniture was removed from every comparison path.
- A missing match is necessarily a real addition or deletion.
- Accepted means human-approved or legally/semantically verified.
- A compact report/index contains all canonical evidence.
- All artifacts in an arbitrarily reused workspace are mutually fresh merely
  because they exist.
- Completing 15 stages proves universal comparison accuracy.

**`accepted` means accepted by the current engine rules, not verified ground
truth.** `CRR_RUN_LLM=mock` does not disable real Stage 1 extraction, and it does
not mock Phase 2 interactive chat.

### Follow the evidence chain backward

For one concrete result, start with its typed ID and trace:

```text
comparison_result.json
    | source_candidate_id / source_table_match_id
    v
review_routing.json
    | why accepted / review / suppressed?
    v
Stage 9 candidate/movement or Stage 10 table evidence
    | what was paired or grouped?
    v
Stage 7 / Stage 6 matching evidence (for non-table pairs)
    | why these OLD/NEW elements?
    v
old/new_document_structured.json
    | raw / normalized / comparison text, anchors and structure
    v
Stage 2 quality reports and Stage 1 raw DI
    | is the extraction trustworthy for this case?
    v
Original PDFs at their externally configured paths
```

For an unchanged or filtered element, begin at the matching/structured artifacts
instead: it may never have become a canonical change. For a suppressed case,
inspect `non_semantic_events`. Always preserve OLD/NEW orientation and separate
observed evidence from hypotheses.

Do not upload private PDFs, raw DI, `.env`, internal endpoints or result text
outside the approved environment. Use synthetic fixtures for shareable examples.

### A compact mental model

```text
01–02: What did extraction read, and what evidence suggests a reading problem?
03–05: Represent it as traceable, comparable data with provisional context.
06–10: Find counterparts, differences, movement/segmentation and table evidence.
11–13: Route clear cases and preserve those needing rules, advice or people.
14:    Build the canonical result without erasing uncertainty.
15:    Present/export it without deciding the truth again.
```

For a practical walkthrough, choose one `CHANGE-...` in the offline demo and
trace it backward through these artifacts. That makes the distinction between
source text, comparison representation, alignment, candidate and resolution
concrete without requiring private documents or provider calls.
