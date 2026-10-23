# Specs

Design specifications; each document declares its own scope and approval state.
The historical authoritative source for the phase-layout refactor is
`2026-10-16-phase-layout-design.md` (target tree, module mapping, boundaries,
acceptance).

- [Coding-agent harness migration](2026-10-23-agent-harness-migration-design.md)
  — design accepted; selective adaptation preserving Knowledge Transfer and
  runtime behavior, targeting team use with Copilot/GitLab Duo. Implementation
  implemented; native client loading and Windows acceptance remain unverified.

Input/output: prose specs only; no runtime artifacts. Execution records live in
[../plans/](../plans/README.md).

Check: tree/tests match the spec via
`python -m unittest tests.integration.test_architecture`.
