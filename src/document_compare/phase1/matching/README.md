# Phase 1 matching

Matching and reconciliation helpers.

Read first: `reconciliation.py` (residual reconciliation) and `language.py`
(language profile). Thresholds are documented in
[docs/contracts/phase1-matching.md](../../../../docs/contracts/phase1-matching.md).

Input/output: pure functions over structured text records; no workspace I/O.

Dependencies: standard library and `phase1/contracts`.

Check: `python -m unittest tests.phase1.test_reconcile`.
