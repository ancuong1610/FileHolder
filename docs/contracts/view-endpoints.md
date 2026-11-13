# View and chat endpoints

Three routes added to the loopback API (`phase2/api/service.py`) for the
Streamlit reviewer. Existing routes, artifact names and `CRR_` keys are
unchanged. The routes only read saved results; the one outbound call is chat's
model request.

Owners: `phase2/viewer/locations.py` (locations), `phase2/viewer/page_render.py`
(page image), the route functions at the end of `phase2/api/service.py`.
Check: `python -m unittest tests.phase2.test_view_api tests.phase2.test_viewer_locations tests.phase2.test_page_render`.

Common rules for all three routes:

- `job_id` is validated with the reader's validator; the job must be complete
  (otherwise 409 `not_ready`).
- `kind` is `change`, `table` or `review`; `record_id` is that kind's ID from the
  reader (`change_id`, `table_change_id`, `review_id`). An unknown record is 404.
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
- `table` findings return the changed cells' polygons. A cell without a polygon
  falls back to the regions of elements whose `represented_by_table_id` equals
  the cell's `source_table_id`, limited to the cell's `primary_page`; if none,
  `page_only`.
- `review` records carry no element reference, so both sides are `unavailable`
  (text only) and `change_type` is `null`.

## `GET /jobs/{job_id}/view/pages/{side}/{page}.png`

Returns a PNG (`Cache-Control: no-store`) of one source-PDF page.

| Parameter | Meaning |
| --- | --- |
| `side` | `old` or `new`. |
| `page` | Page number from 1; must exist in the PDF. |
| `kind`, `record_id` | Optional, only together (else 422 `invalid_arguments`). Outlines the finding's regions that lie on this page. |
| `uncertain` | Optional boolean, default `false`. Draws the outline dashed. |
| `scale` | Optional, default 1.5, allowed 0.5-3.0. |

Highlight colour follows the finding type: deleted red, added green, anything
else amber. Type and state are shown as text by the UI; colour is not the only
signal. Page rotation is handled by PyMuPDF; accuracy on real PDFs with rotation
or crop boxes is not verified.

## `POST /jobs/{job_id}/chat`

Body `{"question": "..."}`, 1-2000 characters after trimming. Returns
`{"job_id": "...", "answer": "...", "limitations": ["..."]}`.

- Uses the existing agent over `SavedWorkspaceGateway` for that job, so it can
  call only `summarize`, `search` and `details`; the ten-step budget applies.
- Works only through the internal hub client, and is configured when both
  `TIA_API_KEY` and `LLM_MODEL` are set. `openai_compatible` is not wired into
  chat. No new environment key exists.
- A question sends bounded reader output to the configured model. Provider and
  model text is never echoed in errors.

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
