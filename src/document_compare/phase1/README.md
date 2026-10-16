# Phase 1

The comparison engine: extraction, matching, review routing, canonical result
and reporting. Owns all 15 pipeline stages.

Read first: [stages/](stages/README.md); for navigation
[docs/architecture.md](../../../docs/architecture.md) and
[docs/review-guide.md](../../../docs/review-guide.md).

Input/output: reads the job root and writes `output/*.json` plus the rendered
PDF/HTML/CSV bundle.

Dependencies: [shared/](../shared/README.md) only. Phase 1 never imports Phase 2;
runtime code never imports [evaluation/](evaluation/README.md).

Check: `python -m unittest discover -s tests/phase1`.
