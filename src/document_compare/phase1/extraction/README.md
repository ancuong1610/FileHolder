# Phase 1 extraction

Helpers for Document Intelligence (DI) field/page data.

Read first: `di_fields.py` (shared page-number and chunk-reference helpers).

Input/output: pure functions over DI/raw extraction structures; no workspace I/O.

Dependencies: standard library and the `documents`/`doc_intelligence` client
libraries used by s01/s02.

Check: `python -m unittest tests.phase1.test_di_chunk_references`.
