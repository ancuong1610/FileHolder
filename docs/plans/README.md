# Implementation plans

This directory stores approved, repository-local implementation plans for bounded
features, fixes, migrations or other multi-step work. A plan records decisions
made during planning so an implementation agent can follow an explicit path
without re-opening settled design questions.

Plans are working artifacts, not runtime inputs and not evidence that the
implementation is correct. The implementing agent must still inspect the current
working tree, preserve unrelated work, follow [AGENTS.md](../../AGENTS.md), and
stop when the plan conflicts with current code, contracts, access or safety
boundaries.

## Naming

Create one plan per bounded effort:

```text
YYYY-MM-DD-<short-kebab-case-topic>.md
```

For example: `2026-11-06-table-retrieval-guard.md`.

Do not use the repository-root `task.md` as a reusable plan location. Move or
rewrite such a file into this directory when it becomes an active plan. Keep
private PDFs, raw extraction data, credentials, internal endpoints and full saved
result text out of plans.

## Plan lifecycle

1. Use the [bbk-plan](../../.agents/skills/bbk-plan/SKILL.md) skill
   to create or review a plan. Its required plan format is the repository template:
   goal/scope, accepted decisions, file/interface map, released ordered tasks,
   verification, handoff and an execution ledger.
2. Keep scope, accepted decisions, constraints, exact owners/tests and stop
   conditions explicit.
3. Mark task release and execution state in the plan. The agent executes only
   the task the user explicitly releases.
4. Update the plan ledger as work proceeds; do not claim a task passed solely
   because the plan says it should.
5. Use [bbk-handover](../../.agents/skills/bbk-handover/SKILL.md)
   for final diff and verification reporting.

A written plan is appropriate for multi-stage, high-uncertainty or
cross-boundary work. Questions and clear micro-fixes follow the proportional
routes in [governance workflows](../governance/workflows.md) and do not require a
plan.
