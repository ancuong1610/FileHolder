# Regression fixtures

Declarative DI pairs that pin value-survival behavior: numbers, dates,
references, negation and table edits must reach canonical output and the redline.

Read first: `matching_pairs.json` (declared pairs), then the two case folders.

Input/output: read-only inputs; `tests/support/regression_support.py` builds the
runs and executes them through the public pipeline CLI in a temporary root.

Dependencies: consumed by `tests/phase1/test_regression_fixtures.py`.

Check: `python -m unittest tests.phase1.test_regression_fixtures`.
