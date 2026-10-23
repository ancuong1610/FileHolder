# Copilot repository bootstrap

Read and follow [AGENTS.md](../AGENTS.md) first. It is the shared source for
permissions, phase ownership, evidence rules and task routing; this file is only
the Copilot discovery entrypoint.

- Documentation map: [docs/README.md](../docs/README.md)
- Proportional routes: [governance/workflows.md](../docs/governance/workflows.md)
- Project skills: [.agents/skills/README.md](../.agents/skills/README.md)
- Team setup and client limits: [agent-harness-quickstart.md](../docs/operations/agent-harness-quickstart.md)

Do not place credentials, private PDFs, raw DI, result text or model traces in
assistant context. Do not make an implicit provider/model call, install clients,
change global configuration, commit, push or deploy. Copilot skill discovery is
surface/version-dependent; start a fresh conversation after instruction updates.
