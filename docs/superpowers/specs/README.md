# Specs

Approved specifications. The authoritative source for this refactor is
`2026-10-16-phase-layout-design.md` (target tree, module mapping, boundaries,
acceptance).

Input/output: prose specs only; no runtime artifacts. Execution records live in
[../plans/](../plans/README.md).

Check: tree/tests match the spec via
`python -m unittest tests.integration.test_architecture`.
