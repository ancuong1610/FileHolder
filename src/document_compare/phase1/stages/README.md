# Phase 1 stages

Purpose: the fifteen ordered pipeline stages `s01`–`s15`.

Read first: `s01_extract.py` (input plus optional native-outline prepass),
`s05_anchors.py` (outline/TOC structure evidence), `s11_routing.py` (uncertain
routing), `s14_canonical_result.py` (canonical result and normalized state).

Input/output: each stage reads `output/*.json` artifacts from the job root and writes the next artifact; `s15_serve.py` also renders the complete PDF/HTML/CSV/index bundle. `phase2_change_index.json` remains accepted-only; the primary HTML/PDF use canonical records.

Dependencies: `phase1/{pipeline,contracts,matching,structure,reporting,extraction}` and `shared`; Stage 14 uses `phase1.contracts.outcomes` and `result_state`. No Phase-2 imports.

Check: `python -m unittest tests.integration.test_architecture tests.phase1.test_stage_import_safety`.
