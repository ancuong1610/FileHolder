# Phase 1 contracts

Cross-stage contracts: validation, evidence review identities, and the
production outcome-migration helper.

Read first: `outcomes.py` (`build_outcome_migration`, imported by evaluation),
then `validation.py` and `review_identity.py`.

Input/output: pure helpers over in-memory records; no file I/O of their own.

Dependencies: standard library only. Kept here (not `shared`) so Phase 1 owns
its business contracts.

Check: `python -m unittest tests.integration.test_architecture tests.phase1.test_route_identity`.
