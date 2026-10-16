# Phase 1 evaluation (optional)

Offline benchmarks, diagnostics and evidence-review tooling. Optional: off the
runtime pipeline path and never imported by Stage 14 or other runtime code.

Read first: `evaluate.py` (scoring + `eval` command), `matching_diagnostics.py`.

Input/output: reads saved pipeline output and writes a separate report/bundle;
never rewrites the input artifacts.

Dependencies: `phase1/contracts/outcomes.py`, `phase1/matching`; invoked via
`python -m document_compare eval|eval-gt|benchmark-performance|review-evidence`.

Check: `python -m unittest discover -s tests/phase1 -p 'test_*evaluation*'`.
