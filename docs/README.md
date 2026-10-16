# Documentation

Project documentation, organized by what a reviewer needs.

Read first: [architecture](architecture.md) for the layout and stage map, then
[review-guide](review-guide.md) to route a task to its owning file and check.

- `contracts/` — active interface contracts (reader tools, matching thresholds).
- `operations/` — install/run/acceptance handover for a real environment.
- `releases/` — historical release/status notes and the export manifest.
- `superpowers/` — the approved design spec and implementation plan.

Check: `python -m unittest tests.integration.test_architecture.ReadmeCoverageTest`.
