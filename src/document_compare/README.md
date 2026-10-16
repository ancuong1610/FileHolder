# document_compare package

The installable package, split into two phases and one shared helper set.

Read first: [phase1/](phase1/README.md) (engine, 15 stages) and
[phase2/](phase2/README.md) (API/MCP/agent); [shared/](shared/README.md) holds
generic helpers.

Input/output: `python -m document_compare <sub>` runs the Phase 1 CLI; see
[docs/architecture.md](../../docs/architecture.md) for the stage map.

Dependencies: `phase2` → `phase1`/`shared`; `phase1` → `shared`; `shared` → neither.

Check: `python -m unittest tests.integration.test_architecture`.
