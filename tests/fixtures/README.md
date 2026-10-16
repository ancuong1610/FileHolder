# Fixtures

Public synthetic fixtures, split by purpose. All content is invented; no private
BU, PDF or credential material is present.

Read first: [general/](general/README.md) (demo pairs), then
[regression/](regression/README.md) and [structure/](structure/README.md).

Input/output: read-only test inputs; the demo also seeds `general/` pairs into a
fresh root. Existing bytes must not change (baseline hash manifest in
`workspace/refactor-checks/fixture-hashes.json`).

Dependencies: consumed by tests and `python -m document_compare demo`.

Check: `python -m unittest tests.phase1.test_regression_fixtures`.
