# Repository agent harness

Shared, repository-local guidance for coding assistants. It is a development
harness, not the Phase 2 result-reading agent and not a runtime dependency.

Start with [AGENTS.md](../AGENTS.md), the [documentation router](../docs/README.md)
and the [governance routes](../docs/governance/README.md). Use a
[project skill](skills/README.md) when its narrow task applies; a question or
micro-fix can stay on the direct route without a skill or plan.

- Authority and evidence: [governance/authority.md](../docs/governance/authority.md)
- Proportional routes: [governance/workflows.md](../docs/governance/workflows.md)
- Completion evidence: [governance/definition-of-done.md](../docs/governance/definition-of-done.md)
- Structural check: `python tools/harness/validate.py`
- Team client setup: [agent-harness-quickstart.md](../docs/operations/agent-harness-quickstart.md)

Client compatibility is surface- and version-dependent. Skills live only here;
do not copy them into `.github/skills`, `.gitlab/duo/chat-rules.md` or another
client folder. Never put credentials, private documents, raw DI, result text or
model traces into agent context without the approved environment and user consent.
