# Phase testing and TOC furniture correction

## Approved scope

Keep the current two-phase architecture. Exclude derived page furniture from
TOC detection, enrich stage-purpose documentation, add coding-agent guidance,
and allow Phase 1, saved-workspace Phase 2, or the combined workflow.

## Boundaries

- One implementation writer; no unrelated refactor or new dependencies.
- Never weaken structure validation or mutate raw extraction to hide a conflict.
- Reuse the existing derived-furniture result and bounded result reader.
- Saved-workspace mode is read-only; never rerun extraction or rewrite its files.
- Keep credentials private and services loopback-only; no real provider calls
  during automated verification.
- Git metadata points to a missing temporary repository. Do not repair it or
  commit. Compare against the external pre-change snapshot instead.
- EBA source PDFs/artifacts are unavailable: synthetic regression evidence is
  not a claim of verification against that pair.

## Milestones and checks

- [x] Add failing Stage 5 regressions for false TOC headings and furniture
  interleaved within a multi-page TOC, preserving genuine TOC/body headings.
  Exclude furniture consistently and prioritize layout membership. Run
  `python -m unittest tests.phase1.test_structure_evidence`.
- [x] Extend the local workflow CLI with explicit phase selection and a saved
  workspace argument. Preserve the no-argument combined flow. Phase 1 runs the
  existing engine; Phase 2 uses the existing reader on validated saved artifacts.
  Include a credential-free saved-reader check, actionable missing/malformed
  artifact errors, path-with-spaces and no-source-write tests. Run
  `python -m unittest tests.phase2.test_local_flow tests.phase2.test_saved_workspace`.
- [x] Enrich `docs/architecture.md` with all 15 purposes and phase boundaries;
  add `AGENTS.md` and an operations testing guide; link README, handover and
  environment examples. Explain offline/mock versus live tests and EBA replay.
- [x] Inspect the snapshot diff; run `python -m unittest discover -s tests`,
  offline demo, CLI help, and saved-reader checks; self-review the final
  saved-artifact boundary and correct concrete findings.
- [ ] Independent final review: blocked by expired agent service API key.

Use Python 3.11 from the temporary verification venv and `PYTHONPATH=src`.
Record actual checks and limitations in the final report; do not claim Windows,
company providers, or real EBA acceptance from offline checks.

## Execution evidence / rulings

- Implementer failed before edits: agent service API key expired. Lead executed
  the bounded packet directly; no overlapping writers.
- Stage 5 RED: three new regressions produced the exact furniture membership
  ValueError, an interrupted text TOC (1 vs 2 entries) and missed table adjacency
  (0 vs 1). GREEN: structure suite 52 tests, 5 optional private-fixture skips.
- Saved-reader tests ran RED before implementing the new adapter/selection.
  Initial GREEN: 41 workflow tests. Real demo smoke then found the Stage 5
  `document.elements` wrapper mismatch; a new failing regression confirmed it
  and the loader was corrected to match the existing API loader contract.
- Verification env required the README's existing setuptools/distutils workaround
  for editable install. Preliminary uninstalled/snapshot checks were environment
  failures, not evidence of product regression; final checks use editable install.
- Independent reviewer also unavailable (same expired agent service API key).
  Final review must be recorded as self-review, not independent approval.
- Saved-workspace Phase 2 intentionally uses the existing local bounded reader,
  not new API registration/import/persistence machinery. HTTP/MCP transport
  remains covered separately by the existing mock harness (12/12 scenarios).
- Final self-review checked fixed-filename path containment, job identity binding,
  explicit incomplete-status rejection, canonical typed records, wrapper shape,
  optional-context notices, no adapter writes, bounded reader reuse and CLI
  defaults. No additional blocking findings. This is not independent approval.
- Final code suite: `python -m unittest discover -s tests` — 647 tests, OK,
  11 optional private-fixture skips. Offline demo stages 02–15 + selected saved
  reader check passed with a path containing spaces. API/MCP mock 12/12 passed;
  CLI help, `pip check`, documentation links and changed-file whitespace passed.
- Still unverified: private EBA pair, Windows/company providers and real chat.
  Git remains broken; no commits or metadata repair performed.
