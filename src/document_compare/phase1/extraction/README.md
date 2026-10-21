# Phase 1 extraction

Helpers for Document Intelligence (DI) field/page data and optional native PDF
outline evidence.

Read first: `di_fields.py` (shared page-number and chunk-reference helpers), then
`pdf_outline.py` (source-bound embedded-bookmark prepass and sidecar validation).

Input/output: pure functions over DI/raw extraction structures. `pdf_outline.py`
reads a PDF and writes versioned sidecars under the caller's `output/`; it never
changes extraction text or matching.

Dependencies: standard library, the `documents`/`doc_intelligence` client
libraries used by s01/s02, and PyMuPDF for the outline prepass.

Check: `python -m unittest tests.phase1.test_di_chunk_references tests.phase1.test_pdf_outline`.
