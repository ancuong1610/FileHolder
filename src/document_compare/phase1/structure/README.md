# Phase 1 structure

TOC, hierarchy, recurring-furniture reconstruction and native-outline links.

Read first: `reconstruction.py` (TOC/hierarchy and outline reconciliation) and
`furniture.py` (recurring furniture). Consumed mainly by `stages/s05_anchors.py`.

Input/output: pure helpers over structured document records; the s05 stage owns
the on-disk `structure_evidence` layer. `structure_evidence.outline` is additive
and never authoritative hierarchy.

Dependencies: standard library and `phase1/extraction`; no Phase 2 imports.

Check: `python -m unittest tests.phase1.test_structure_evidence tests.phase1.test_pdf_outline`.
