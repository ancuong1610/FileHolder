# Phase 1 matching

Matching and reconciliation helpers.

Gate A adds `semantic_evidence.py` (narrow complete-source material witnesses,
never model authority) and `residual_proposals.py` (bounded, non-consuming possible
counterparts with explicit search coverage). These separate correspondence from
meaning; they do not lower the global lexical thresholds or prove arbitrary
paraphrase equivalence. Proposal groups preserve uncertainty and all source IDs.

Read first: `reconciliation.py` (residual reconciliation) and `language.py`
(language profile). Thresholds are documented in
[docs/contracts/phase1-matching.md](../../../../docs/contracts/phase1-matching.md).

Input/output: pure functions over structured text records; no workspace I/O.

Dependencies: standard library and `phase1/contracts`.

Check: `python -m unittest tests.phase1.test_reconcile tests.phase1.test_semantic_assessment tests.phase1.test_semantic_residuals tests.phase1.test_semantic_safety_regressions`.
