# Documentation

Project documentation, organized by what a reviewer needs.

Read first: [architecture](architecture.md) for the layout and stage map, then
[review-guide](review-guide.md) to route a task to its owning file and check.
Coding-agent task routing and authority live in [governance](governance/README.md).

For a detailed walkthrough of all 15 stages, data representations, decisions,
artifacts and limitations, read
[Knowledge Transfer — Phase 1](KnowlesgeTranser%20Phase%201.md). Source PDFs are
currently configured outside the comparison workspace; moving them inside is
deferred, not a prerequisite of that guide.

- `contracts/` — active interface contracts (reader tools, matching thresholds).
- `governance/` — harness authority, proportional workflows and decisions.
- `operations/` — install/run/acceptance and the
  [agent harness quickstart](operations/agent-harness-quickstart.md).
- `releases/` — historical release/status notes and the export manifest.
- `superpowers/` — dated design/plan process records; historical background, not
  the current runtime specification.

Check: `python -m unittest tests.integration.test_architecture.ReadmeCoverageTest`
and, for the coding-agent harness, `python tools/harness/validate.py`.
