# Phase 1 stages

Purpose: the fifteen ordered pipeline stages `s01`–`s15`.

Read first: `s01_extract.py` (input), `s11_routing.py` (review routing), `s14_canonical_result.py` (canonical result).

Input/output: each stage reads `output/*.json` artifacts from the job root and writes the next artifact; `s15_serve.py` also renders the PDF/HTML/CSV/index bundle.

Dependencies: `phase1/{pipeline,contracts,matching,structure,reporting,extraction}` and `shared`; Stage 14 uses `phase1.contracts.outcomes`. No Phase-2 imports.

Check: `python -m unittest tests.integration.test_architecture tests.phase1.test_stage_import_safety`.
