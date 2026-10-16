# Phase 1 reporting

Rendering of the final bundle: original redline PDF, inline PDF, HTML and diff
spans/annotations.

Read first: `redline_pdf.py` (primary product PDF), `inline_pdf.py`
(single-flow view), `html.py`, `spans.py`.

Input/output: reads canonical artifacts; writes `comparison_redline.pdf`,
`comparison_inline.pdf`, `inline_pdf_audit.json`, HTML and the Stage-15 index.

Dependencies: ReportLab and `phase1/contracts`; no Phase 2 imports.

Check: `python -m unittest tests.phase1.test_redline_pdf tests.phase1.test_inline_pdf`.
