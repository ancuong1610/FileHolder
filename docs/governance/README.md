# Governance

Repository-local rules for how coding agents take a task from context to verified
handoff. This is development governance, not the Phase 2 runtime agent and not a
replacement for product ownership.

- [Authority](authority.md) — who owns facts, decisions and evidence.
- [Workflows](workflows.md) — proportional routes from question to handoff.
- [Definition of done](definition-of-done.md) — evidence for the selected task.
- [Decisions](decisions/README.md) — consequential harness choices and status.
- [Project skills](../../.agents/skills/README.md) — narrow task procedures.
- [Team quickstart](../operations/agent-harness-quickstart.md) — client setup.

Use the lowest route that fits. Questions and inspections stay read-only; tiny
clear fixes use one focused check; unknown defects gather evidence before repair.
Do not create a plan, reviewer or subagent for a task that does not need one.
Higher-level system and organizational policy always takes precedence.

Structural check: `python tools/harness/validate.py`.
