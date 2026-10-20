# Documentation

Project documentation, organized by what a reviewer needs.

Read first: [architecture](architecture.md) for the layout and stage map, then
[review-guide](review-guide.md) to route a task to its owning file and check.

For a detailed walkthrough of all 15 stages, data representations, decisions,
artifacts and limitations, read
[Knowledge Transfer — Phase 1](KnowlesgeTranser%20Phase%201.md). Source PDFs are
currently configured outside the comparison workspace; moving them inside is
deferred, not a prerequisite of that guide.

- `contracts/` — active interface contracts (reader tools, matching thresholds).
- `operations/` — install/run/acceptance handover for a real environment.
- `releases/` — historical release/status notes and the export manifest.
- `superpowers/` — the approved design spec and implementation plan.

Check: `python -m unittest tests.integration.test_architecture.ReadmeCoverageTest`.
