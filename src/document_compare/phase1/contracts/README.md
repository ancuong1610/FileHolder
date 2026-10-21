# Phase 1 contracts

Cross-stage contracts: validation, evidence review identities, normalized result
state, and the production outcome-migration helper.

Read first: `result_state.py` (shared `supported`/`uncertain`/`non_semantic`
normalization), then `outcomes.py` (`build_outcome_migration`, imported by
evaluation), `validation.py` and `review_identity.py`.

Input/output: pure helpers over in-memory records; no file I/O of their own.
`result_state.py` derives state from legacy deterministic fields and never
rewrites a saved artifact.

Dependencies: standard library only. Kept here (not `shared`) so Phase 1 owns
its business contracts.

Check: `python -m unittest tests.integration.test_architecture tests.phase1.test_route_identity tests.phase1.test_automatic_reports`.
