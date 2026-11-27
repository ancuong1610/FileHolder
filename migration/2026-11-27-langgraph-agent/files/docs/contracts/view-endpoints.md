# View and chat endpoints

Four routes added to the loopback API (`phase2/api/service.py`) for the
Streamlit reviewer. Existing routes, artifact names and `CRR_` keys are
unchanged. The routes only read saved results; the one outbound call is chat's
model request.

Owners: `phase2/viewer/locations.py` (locations), `phase2/viewer/outline.py` (outline), `phase2/viewer/page_render.py`
(page image), the route functions at the end of `phase2/api/service.py`.
Check: `python -m unittest tests.phase2.test_view_api tests.phase2.test_viewer_locations tests.phase2.test_page_render`.

Common rules for all three routes:

- `job_id` is validated with the reader's validator; the job must be complete
  (otherwise 409 `not_ready`).
- `kind` is `change`, `table`, `review` or `move`. `record_id` is the matching
  reader ID (`change_id`, `table_change_id`, `review_id`, `move_id`). An unknown
  record is 404.
- Errors use the reader's shape: `{"detail": {"error": "<code>", "message": "..."}}`.
  An invalid `side` or a non-integer `page` is rejected by FastAPI validation (422)
  in FastAPI's own detail shape.
- Page images and result text are not logged.

## `GET /jobs/{job_id}/view/locations?kind=&record_id=`

Returns where one finding sits on each side.

```json
{
  "kind": "change",
  "record_id": "...",
  "change_type": "MODIFIED",
  "sides": {
    "old": {"counterpart": "present", "location": "polygon", "page_count": 12,
            "primary_page": 3, "regions": [{"page_number": 3, "polygon": [0.1, 0.2, 0.9, 0.2, 0.9, 0.25, 0.1, 0.25]}]},
    "new": {"counterpart": "present", "location": "polygon", "page_count": 12,
            "primary_page": 3, "regions": []}
  }
}
```

- `polygon` is 8 numbers (x, y per corner) normalized to 0-1 by that page's own
  `width` and `height` from `old_document.json` / `new_document.json`, so it does
  not depend on the extraction unit. Pages are numbered from 1.
- `counterpart` is `none` when the side has no element (old side of an addition,
  new side of a deletion), otherwise `present`.
- `location` is `polygon`, `page_only` (page known, no usable polygon) or
  `unavailable`. A location is never guessed: an element ID missing from the
  document JSON, a missing document file or an unusable polygon degrades to
  `page_only` or `unavailable`.
- Every region carries an additive `role`: `element` (change, review and move
  elements), `table` (the whole table: bounding regions of the table element,
  found by `represented_by_table_id` or the side's `source_table_ids`) or `cell`.
- A paragraph split or merge returns regions for all saved member element IDs
  on each side, including fragments on different pages.
- `table` findings return one `table` region per table-element region plus one
  `cell` region per changed cell (`classification` not `UNCHANGED`, side has a cell
  with a polygon; inches normalized like any polygon). If nothing resolves but a
  changed cell names a page, the side is `page_only`.
- `review` records resolve their regions from `evidence.old_element_ids` /
  `new_element_ids`. When the evidence names no element at all, the elements of the
  linked changes are used (a change is linked when named in `evidence.change_ids` or
  when its `source_candidate_id` equals the record's). `location` is
  `polygon`/`page_only`, and `unavailable` only when nothing resolves.
  `change_type` is `null`.
- `move` records resolve all member regions from `old_element_ids` and
  `new_element_ids` in their respective document artifacts. `change_type`
  is `null`. Each side's first resolved location supplies its primary page.
- Top level also carries `needs_review` (the record's `resolution.status` is
  `review`, a review record, or a review record is linked to the change) and
  `review_reason` (plain words, `null` when none is recorded).

## `GET /jobs/{job_id}/view/outline`

Returns the section structure of the saved comparison with every finding placed
in it. Same job rules as above (404 unknown job, 409 `not_ready`).

```json
{
  "documents": {"old": {"page_count": 12}, "new": {"page_count": 13}},
  "sections": [
    {"section_id": "DOC-NEW-S0002", "title": "Section 4", "side": "new", "level": null,
     "change_count": 2,
     "children": [],
     "items": [{"kind": "change", "record_id": "CHANGE-000001", "change_type": "MODIFIED",
                "label": "first 90 characters of the change text...",
                "page_old": 3, "page_new": 3, "context_old": false, "context_new": false,
                "section_path": ["Section 4"],
                "needs_review": false, "review_reason": null, "review_ids": [],
                "impact": {"level": "content", "priority": "high", "kinds": [], "signals": ["modal"]}}]}
  ],
  "unplaced": [],
  "review": [{"kind": "review", "record_id": "REVIEW-000001", "change_type": null,
              "label": "unresolved residual counterpart", "page_old": null, "page_new": null,
              "context_old": false, "context_new": false, "section_path": [],
              "needs_review": true, "review_reason": "Block of content moved",
              "review_ids": ["REVIEW-000001"], "linked_to": []}],
  "total": 4
}
```

- Sections come from `{old,new}_document_structured.json`
  (`document.structure_evidence.sections` and `membership.by_element`). The tree
  is the NEW document's sections; an OLD section whose casefolded,
  whitespace-collapsed title equals a new one is merged into it, any other OLD
  section becomes a node with `side: "old"` after the new ones. Nesting follows
  `parent_section_id` only when `parent_known` is true, otherwise sections are
  top-level. All sections are listed, including those with no findings.
- `toc_rank` is optional. It is the zero-based position in a usable
  `document.structure_evidence.toc.entries` list of the first entry linked to
  the section's body heading. A merged section uses the new document's rank
  when available and otherwise falls back to the old document's rank.
- `change_count` is the node's own items plus all descendants.
- A change or table is placed by its NEW element's section (ADDED, MODIFIED and
  other types) or its OLD element's section (DELETED), falling back to the other
  side. A table is located through the element whose `represented_by_table_id`
  equals its `logical_table_id`. Anything without a section (preamble, unknown
  element, missing structured artifact) is in `unplaced`. Each change/table
  appears exactly once in `sections` or `unplaced`.
- Change, table and move items carry the reader's `impact` projection or
  `null` for unknown impact. A move appears once, in the NEW section and
  reading order of its first member, or in `unplaced` if NEW membership is
  unavailable. Its `label` previews the member text and its `from_anchor` and
  `to_anchor` provide heading labels when known. The UI combines these with the
  `Verschoben` badge. The outline includes local reorders. Streamlit hides
  them until `Lokale Umstellungen anzeigen` is selected, and its section
  counts and summary chips follow the active filters.
- `context_old` / `context_new` (boolean on every item): `true` when that side's
  page is a context page, i.e. the finding has no element on that side (ADDED has
  no old element, DELETED no new one) and `page_old` / `page_new` was filled in
  instead of left `null`. The page is the heading page of the same section on
  that side (sections matched by normalized title); if the section exists only on
  the other side, the heading page of the nearest previous section (tree order)
  that exists on the missing side; if there is none (or no sections at all), the
  other side's page clamped to the missing document's page count. It stays `null`
  when no page can be derived. Context pages carry no highlight.
- LLM opinion. Every item has `llm_opinion`: `null`, or
  `{"outcome", "reason", "evaluator", "evidence_refs"}` copied from the record's
  `resolution.packet_decision` (Stage 13, advisory; a linked review record's
  decision is copied onto its change). The model's `confidence` is deliberately
  not passed on because it is not calibrated. Stage 14 `resolution.status` stays
  the final decision.
- Review linking. Every item has `needs_review` (its `resolution.status` is
  `review`, or a review record is linked to it), `review_reason` and `review_ids`.
  Reasons: `unresolved_residual_counterpart` / `plausible_unresolved_counterpart`
  "Possible match on the other side not confirmed"; `movement_block_review` /
  `contiguous_structural_block_relative_order_relocation` "Block of content
  moved"; `ambiguous_modified_pair` / `short_text_similarity_pair` "Short or
  ambiguous change — LLM could not decide"; otherwise the case type in words.
  A review record is linked to a change when `evidence.change_ids` names it or its
  `source_candidate_id` equals the change's; a linked record marks those items and
  is not an item itself. Every other review record is an item with `kind: "review"`,
  `change_type: "REVIEW"`, placed in the section of its first new (else old)
  element, pages from its first element on each side, label from the evidence text
  preview or the reason; without an element it is `unplaced`.
- Table items are labelled `Table · p.<page> · <n> cells changed`
  (n = cells not `UNCHANGED`; omitted when 0). A table outside any section goes to
  the nearest previous section in reading order on that side (also found through
  `source_table_ids`); with no previous section it is `unplaced`.
- `review` (kept for compatibility) lists ALL `review_queue` records, each with
  `linked_to` (change IDs it is linked to). `total` counts the items in `sections`
  and `unplaced` (moves and standalone review items included, linked reviews counted once).
- A missing structured artifact is not an error (`409 invalid_artifact` only for
  unparsable JSON). Sections are an evidence-based hypothesis, not legal structure.

## `GET /jobs/{job_id}/view/pages/{side}/{page}.png`

Returns a PNG (`Cache-Control: no-store`) of one source-PDF page.

| Parameter | Meaning |
| --- | --- |
| `side` | `old` or `new`. |
| `page` | Page number from 1; must exist in the PDF. |
| `kind`, `record_id` | Optional, only together (else 422 `invalid_arguments`). Outlines the finding's regions that lie on this page. |
| `uncertain` | Optional boolean, default `false`. Draws the outline dashed; also forced on when the record `needs_review`. |
| `scale` | Optional, default 1.5, allowed 0.5-3.0. |

Highlight colour follows the finding type: deleted red, added green, anything
else amber; review records are purple. `table` regions are thin outlines without
tint, `cell` and `element` regions are tinted and outlined. Type and state are shown as text by the UI; colour is not the only
signal. Page rotation is handled by PyMuPDF; accuracy on real PDFs with rotation
or crop boxes is not verified.

## `POST /jobs/{job_id}/chat`

The request requires `question`, 1–2,000 characters after trimming.

- `history` defaults to an empty list. Send up to six earlier turns as
  `{"role": "user" | "assistant", "content": "..."}`, in chronological order.
  Each turn contains 1–4,000 characters and all turns together contain at most
  12,000 characters.
- `context` is optional and each field is optional. `selected_kind` accepts
  `change`, `block`, `move`, `table` or `review`. `selected_record_id` accepts
  `CHANGE-`, `BLOCK-`, `MOVE-` or `REVIEW-` followed by six digits, or
  `TABLE-CHANGE-` followed by five digits. `preset` accepts `important`,
  `content`, `review` or `all`. `section` accepts up to 200 characters.
- Invalid history or context returns HTTP 422 with FastAPI validation details.

The response includes `job_id`, `answer`, `limitations`, `cited`,
`unverified_ids` and `suggestions`. Each entry in `cited` is a `{kind, record_id}` object.
The list contains unique IDs in answer order that appeared in reader results
from this turn or matched the selected ID. `unverified_ids` lists other IDs
found in the answer and adds `unverified_citation` to `limitations` when
nonempty. An answer from the last allowed planner call adds `partial_answer`.
`suggestions` contains up to three plain-text follow-up questions, each at
most 120 characters, and is empty when the planner supplies none.

- Uses the existing agent over `SavedWorkspaceGateway` for that job, so it can
  call only `summarize`, `search` and `details`. The tenth planner call is
  reserved for the answer. Failure to produce it returns `chat_failed`.
- Works only through the internal hub client, and is configured when both
  `TIA_API_KEY` and `LLM_MODEL` are set. `openai_compatible` is not wired into
  chat. No new environment key exists.
- A question sends the supplied history and context and bounded reader output
  to the configured model. Provider and model text is never echoed in errors.

## `POST /jobs/{job_id}/chat/stream`

Uses the same request and pre-stream HTTP validation as `/chat`. A successful
HTTP response has media type `application/x-ndjson` and one JSON event per line.

| `type` | Other fields | When |
| --- | --- | --- |
| `step` | `step` (one-based planner step, or `0` for selected-finding prefetch), `action` (`search`, `details`, `summarize`), `query`, `record_id` | Before the announced tool runs. Prefetch uses no planner step. Query text is limited to 120 characters. Unused fields are null. |
| `fallback` | `query`, `variant` | A broader query returned hits after a no-hit literal search. Retries preserve the search filters and use the same planner step. |
| `final` | `job_id`, `answer`, `limitations`, `cited`, `unverified_ids`, `suggestions` | Last event on success. These fields match the `/chat` payload. |
| `error` | `code` | Last event on failure after the stream starts. The code is `chat_failed` or a reader error code. |

## Job status file names

`POST /jobs` and the seeded demo job also store `old_name` and `new_name` in the
job status (returned by `/jobs/{id}/status`): the upload's base name, control
characters removed, at most 120 characters. Jobs created earlier lack the keys.

## Error codes

| HTTP | `error` | When |
| --- | --- | --- |
| 404 | `not_found` | Unknown job or record. |
| 404 | `source_pdf_unavailable` | Job is complete but the side's source PDF is not stored. |
| 409 | `not_ready` | Job has not completed. |
| 409 | `invalid_artifact` | Saved status or result unreadable or inconsistent. |
| 422 | `invalid_arguments` | Unknown `kind`, only one of `kind`/`record_id`, page or scale out of range, bad question length, invalid job ID. |
| 502 | `chat_failed` | Planner or provider failure while answering. |
| 503 | `chat_not_configured` | `TIA_API_KEY` or `LLM_MODEL` is not set. |
| 400/409/413 | other reader codes | As defined by the reader mapping (for example `response_too_large`). |

## Read-only guarantees

- No route writes into a job directory or `output/`; PDFs are opened read-only
  and highlights are drawn on an in-memory copy.
- File access is limited to the fixed names `output/old_document.json`,
  `output/new_document.json` and `input/document/{old,new}.pdf` inside the job;
  `side` is restricted to `old`/`new` and IDs are validated, so path fragments
  in `side`, `page` or `record_id` are rejected.
- Stage 14 stays the authority. These routes show saved results and do not
  change or re-run them.
