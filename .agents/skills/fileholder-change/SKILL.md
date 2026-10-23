---
name: fileholder-change
description: Implement an approved FileHolder change or bounded correction while preserving contracts, phase ownership, source conservation and existing user work.
---

# Make a bounded change

Use when the owner has approved an implementation or a correction is clearly
inside existing authority. A question, inspection or tiny clear fix can stay on
the direct route without this skill. Read the shared
[authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules; they take precedence.

## Read

1. [Review guide](../../../docs/review-guide.md) for the owning file and focused
   check.
2. [Architecture](../../../docs/architecture.md) for phase boundaries and the
   one-way dependency rule.
3. [Phase testing](../../../docs/operations/phase-testing.md) when a real
   workspace, provider or combined flow is involved.

Inspect current changes, relevant code and the matching test before editing.
Preserve unrelated work and existing workspaces.

## Procedure

1. State the bounded outcome, acceptance check and files you expect to touch.
2. Keep one writer for overlapping files. Do not refactor unrelated code, add a
   framework or change contracts by accident.
3. For a confirmed defect, add the regression first. For ordinary behavior,
   update or add the smallest focused test that proves the change.
4. Preserve raw extraction text, IDs, locations, old/new conservation, `CRR_`
   keys and artifact/HTTP/MCP contracts. Stage 13 recommendations stay advisory;
   Stage 14 deterministic rules own final resolution.
5. Keep reader filtering and pagination in
   `phase2/reader/result_reader.py`. Do not load full reports into model prompts.
6. Assess documentation impact from the actual diff and update the owning doc in
   the same change.

## Verify

Run the focused test from the review guide. Then run the smallest affected
phase/integration check and `git diff --check`. Run a full suite only at a
meaningful boundary, not after every file.

## Stop and report

Stop if the scope needs new product authority, a contract/migration change, a
new dependency, credentials, a provider call or edits outside the approved
files. Do not commit, push, deploy or publish without explicit authorization.
Report changed files, exact commands/results, documentation impact, skipped
checks and the next user-visible action.
