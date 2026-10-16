# Phase-separated repository implementation plan

**Goal:** Implement the owner-approved tree with no compatibility layer, readable reviewer navigation and unchanged comparison/MCP behavior.
**Spec:** `docs/superpowers/specs/2026-10-16-phase-layout-design.md`.
**Architecture:** `src/document_compare/{phase1,phase2,shared}`; Phase 1 keeps all 15 stages, Phase 2 owns API/MCP/agent, shared stays generic. One writer for the tightly coupled move/import/test migration; one final independent review.
**Tech stack:** Existing Python/ReportLab/FastAPI/FastMCP/LangChain dependencies; standard setuptools packaging, no new runtime dependencies.

## Constraints and review focus

- Owner approved the layout and explicitly removed `compat/`; no additional product questions are required for routine migration details.
- No algorithm, artifact schema/name, provider, credential, runtime env-key or DB migration; no data cleanup or publishing.
- Root/path calculations, lazy provider imports, Stage-14 evaluation leakage, Stage-15 ownership and MCP process launch targets are the highest-risk boundaries.
- Never lower test coverage or copy private artifacts to make the move pass. Keep baseline outputs and input data untouched.

## Milestone 1 — One coherent move and import migration

- [x] Create isolated worktree on the correct base `2c1418a`; record passing full baseline plus three demos and 12-case reader mock.
- [x] Add failing architecture/package tests for new import paths, exactly 15 stages, no old namespaces, Phase-1→Phase-2 prohibition and shared-only dependencies.
- [x] Apply the exact module mapping in the spec, split only common helpers and the production outcome-migration helper needed to enforce ownership.
- [x] Add setuptools `pyproject.toml`, root module CLI and the explicit new local-reviewer module entry point; update subprocess/module/server paths without sys.path hacks.
- [x] Move tests/support/fixtures, rewrite imports, test discovery/root calculations and known dead-API tests. Remove confirmed obsolete adapters only after reference checks.
- [x] Install with `python -m pip install --no-deps --no-build-isolation -e .`, run focused package/import/pipeline/MCP tests, then resolve only confirmed migration errors. This machine required `SETUPTOOLS_USE_DISTUTILS=stdlib` for its existing environment; see the root README troubleshooting note.

## Milestone 2 — Navigation, cleanup and verification

- [x] Write short folder READMEs and root start page; architecture diagram/stage map and task-oriented review guide; update active handover/contracts and clearly separate historical records.
- [x] Validate all maintained source/test/docs folders have README, local documentation links resolve and current runnable examples use only the new namespace.
- [x] Run all three new-namespace demos to fresh roots and compare declared canonical semantic projections against `workspace/refactor-baseline-current`. All three complete canonical JSON objects are equal without excluding any fields; original and inline PDFs exist.
- [x] Run new-namespace reader mock; require exact three-tool discovery and 12/12 cases, existing five-tool workflow discovery and owned cleanup.
- [x] Run final full suite and pip check; verify unchanged fixture bytes, no runtime/private data moved/deleted, and clean diff whitespace. Evidence: `workspace/refactor-checks/full-suite.log` (628 run, 617 passed, 11 optional skips), `workspace/refactor-after/` (three demos and 12/12 mock); 29 fixture file hashes unchanged and zero broken local documentation links.
- [x] Regenerate relocated release manifest after final documentation and code state; all public file hashes verified, with the manifest itself excluded from self-hashing.
- [x] Independently review the coherent final change, inspect actual rename-aware diff and command output; report new commands, checks, intentional breaking names and remaining external-machine/provider limits. Independent final review approved with no concrete blockers. The owner subsequently authorized committing and pushing this refactor on its separate branch.

Execution method is lead-owned bounded implementer work under the existing operating policy, not parallel overlapping writers. Stop repeating a defect after two unsuccessful attempts; return a concrete blocker if a new product/schema decision is genuinely required.
