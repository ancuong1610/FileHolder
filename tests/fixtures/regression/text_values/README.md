# text_values fixture

A synthetic DI pair whose wording/number edits must survive to canonical output
and the redline text: duration `30`→`60` days, a changed date, a percentage, a
document reference, an inserted/removed `not`, and `shall`→`may`.

Input/output: read-only `old_di_raw.json` / `new_di_raw.json`; run via
`tests/support/regression_support.py` in an isolated temporary root.

Check: `python -m unittest tests.phase1.test_regression_fixtures`.
