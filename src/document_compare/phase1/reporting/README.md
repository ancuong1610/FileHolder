# Phase 1 reporting

Rendering of the final bundle: human report model, original redline PDF, inline
PDF, HTML and diff spans/annotations.

Read first: `report_model.py` (shared human-facing projection and grouping), then
`redline_pdf.py` (primary product PDF), `inline_pdf.py` (single-flow view),
`html.py`, `spans.py`.

Input/output: reads canonical artifacts plus optional structured documents; writes
`comparison_redline.pdf`, `comparison_inline.pdf`, `inline_pdf_audit.json`, HTML
and the Stage-15 index. The primary HTML/PDF include supported and uncertain
records; the index remains accepted-only.

Gate A shows source support separately from a deterministic material witness;
legacy accepted text without assessment stays `not_assessed`. Proposal-group
OLD/NEW passages are listed separately as unpaired sources with exact text/pages,
not a fabricated pair or only an ID list. Shared counts deduplicate the group
without deleting its member change records.

`comparison_inline.pdf` keeps every canonical text change in the flow, including
a change under review. Its `Uncertain differences` section adds the recorded
review reasons and, when the saved result resolves them, separate unpaired OLD/
NEW source passages. A review item whose passage cannot be resolved names the
missing evidence IDs and says the source passage is unavailable; it is never
shown as no difference. These are unpaired review-evidence passages, not a
redline assertion: red strike/blue underline means textual deletion/insertion in
this report, not proof of source-PDF strikethrough styling. This inline artifact
is separate from the HTML report and index supported-only markers.

Dependencies: ReportLab and `phase1/contracts`; no Phase 2 imports.

Check: `python -m unittest tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf tests.phase1.test_automatic_reports`.
