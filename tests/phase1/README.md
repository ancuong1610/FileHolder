# tests/phase1

Tests for the Phase 1 engine: extraction, normalization, matching, movement,
tables, routing, canonical result, reporting and evaluation.

Read first: `test_reconcile.py` and `test_pipeline.py`-style flows; mirror the
owning module's README. Newer focused modules include `test_pdf_outline.py`
(native outline sidecars/binding/fallback) and `test_automatic_reports.py`
(uncertain routing, normalized state and complete HTML/PDF reports).

Input/output: synthetic inputs from [../fixtures/](../fixtures/README.md); each
test uses an isolated temporary workspace root with `CRR_RUN_LLM=mock`.

Dependencies: `document_compare.phase1.*`; must not import `phase2`.

Check: `python -m unittest discover -s tests/phase1` or one file, e.g.
`python -m unittest tests.phase1.test_table_stage_module`.
