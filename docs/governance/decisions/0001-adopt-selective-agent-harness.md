# 0001 Adopt the selective coding-agent harness

- ID: 0001
- Status: accepted
- Date: 2026-10-23
- Owner: project maintainer
- Approval: owner accepted the design and approved implementation (“làm đi”);
  technical execution remains with the coding lead.
- Design record: `docs/superpowers/specs/2026-10-23-agent-harness-migration-design.md`
- Plan record: `docs/superpowers/plans/2026-10-23-agent-harness-migration.md`

## Decision

Adopt a small repository-local harness for team use with GitHub Copilot and
GitLab Duo plus a client-selected GPT model:

- one shared root [AGENTS.md](../../../AGENTS.md) and documentation router;
- one shared skill collection under `.agents/skills/`;
- concise authority, workflow and completion rules under `docs/governance/`;
- a thin Copilot bootstrap, team quickstart and read-only structural validator.

The harness is model-agnostic. It does not install clients, select a model, add an
OpenAI SDK or credentials, make a provider call, or change global configuration.

## Rejected alternatives

- Wholesale copying of the reference repository's skill catalog, issue forms and
  educational assumptions.
- A folder rename without task routing, authority boundaries or checks.
- Per-client skill copies or redundant `.gitlab/duo/chat-rules.md` knowledge.
- OpenCode/Codex as the team acceptance target; their compatibility is incidental.

## Consequences

Skill discovery remains surface- and version-dependent and must be verified in the
installed client. Manual Markdown reading is an explicit fallback, not native
discovery. Structural tests prove files and regressions only, not agent
understanding, productivity or comparison accuracy. The migration preserves
Knowledge Transfer, the Python phases, contracts and saved-result behavior.
