# tests/support

Shared test-only helpers. Imported by phase tests; not a test module itself.

Read first: `regression_support.py` (declarative DI fixtures + isolated pipeline
runner) and `structure_fixtures.py` (`build`/`load`/`write_all_fixtures`).

Input/output: builds fixture JSON in memory; `write_all_fixtures()` regenerates
the committed [../fixtures/structure/](../fixtures/structure/README.md) files.

Dependencies: public `document_compare.phase1.pipeline.cli` only — it never
imports a stage algorithm directly.

Check: exercised via `python -m unittest tests.phase1.test_regression_fixtures`.
