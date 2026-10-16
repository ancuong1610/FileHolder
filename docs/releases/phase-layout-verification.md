# Phase-layout verification — 2026-10-16

Structural refactor from `2c1418a219e14e434c927909c93ac14196d22ab4`, on the local `refactor-phase-layout` branch. No publication is implied.

## Executed checks

- Editable install: passed with existing dependencies and `--no-deps --no-build-isolation`; this machine additionally needed `SETUPTOOLS_USE_DISTUTILS=stdlib` (see root README).
- `python -m unittest discover -s tests`: 628 run, 617 passed, 11 optional private-fixture skips. Baseline: 618 run, 607 passed, the same 11 skips.
- `python -m pip check`: no broken requirements.
- `python -m document_compare demo --fixture NAME --root FRESH_ROOT`: passed for `internal_policy_report`, `crr3`, and `scai_api_guide`.
- Complete parsed `comparison_result.json` equality against each corresponding baseline: 3/3 identical, with **no fields excluded**. Original redline and additional inline PDFs present in all three outputs. This is canonical semantic equality, not a PDF byte-equality claim.
- `python -m document_compare.phase2.agent.mock_reader --out FRESH_OUTPUT`: 12/12 scripted real-transport cases passed. Five-tool workflow and three-tool reader discovery are also covered by the suite.
- All 29 moved public fixture files retain their SHA-256 hashes, including existing fixture READMEs.
- Architecture tests cover 15 ordered stages, namespace removal, import directions, Stage 14 evaluation independence and folder README coverage.
- Local Markdown links: zero missing targets. `git diff --check HEAD`: passed.

Local detailed logs and generated outputs are intentionally ignored: `workspace/refactor-checks/`, `workspace/refactor-baseline-current/`, and `workspace/refactor-after/`. Do not add private PDFs or generated company results to the release manifest.

## Limits

Synthetic checks establish structural/contract preservation, not accuracy on arbitrary PDFs, real LLM reasoning, retrieval recall, or prompt-injection resistance. Company Windows setup, internal provider credentials/SDK and real-PDF manual redline review still require the company environment. Hosting, DB/storage and production authentication remain separate open decisions.
