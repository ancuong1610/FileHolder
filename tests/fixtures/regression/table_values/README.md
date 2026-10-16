# table_values fixture

A synthetic DI pair whose table edits must stay reviewable: a changed cell, an
inserted/deleted/reordered row, a changed column, a repeated-header split,
merged cells and an extraction-uncertain artifact cell.

Input/output: read-only `old_di_raw.json` / `new_di_raw.json`; run via
`tests/support/regression_support.py` in an isolated temporary root.

Check: `python -m unittest tests.phase1.test_regression_fixtures`.
