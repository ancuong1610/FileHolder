# shared

Generic, phase-agnostic helpers. No business logic.

Read first: `paths.py` (repo/package/workspace root resolution) and `json_io.py`
(load/save JSON).

Input/output: pure path/JSON utilities; they never write job outputs by
themselves.

Dependencies: standard library only — imports neither Phase 1 nor Phase 2.

Check: `python -m unittest tests.integration.test_architecture`.
