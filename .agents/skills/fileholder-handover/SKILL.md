---
name: fileholder-handover
description: Verify and hand off an agreed FileHolder change with the actual diff, exact checks, documentation impact and explicit skipped or unverified cases.
---

# Verify and hand off a change

Use at the end of an agreed change or bounded correction. Do not use to invent a
review loop for a question or to authorize a commit. Read the shared
[authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules; they take precedence.

## Read

1. [Definition of done](../../../docs/governance/definition-of-done.md) for the
   evidence expected for the selected task.
2. [Review guide](../../../docs/review-guide.md) for the focused checks that
   apply to the touched owners.
3. [Phase testing](../../../docs/operations/phase-testing.md) for real-workspace,
   provider or combined-run checks.

## Procedure

1. Inspect the actual diff, not only a worker summary. Confirm unrelated user
   changes and ignored workspaces were preserved.
2. Run the focused checks for the touched area, then the smallest meaningful
   boundary check. Use the same Python interpreter for tests and subprocesses.
   Do not repeat a full suite after every file.
3. For harness changes, run `python tools/harness/validate.py` and the relevant
   integration tests. These prove file structure and regressions, not that a
   Copilot or GitLab Duo client loaded a skill.
4. Check documentation impact from the diff: commands/setup, stages/data flow,
   algorithms, contracts/reader behavior or agent workflow. Update the owning doc
   or state why none is needed.
5. Run `git diff --check` and confirm no credentials, private input, raw DI,
   result text or generated workspace output entered the change.

## Stop and report

Stop for a concrete failure that needs a product decision, a changed contract,
new authority or an unavailable artifact. Do not commit, push, deploy, publish or
alter global client configuration without explicit permission.

Report changed files, exact commands and results, documentation changes,
skipped/unverified cases (including unavailable clients), and the next
user-visible action. A passing suite does not prove universal comparison accuracy
or native client compatibility.
