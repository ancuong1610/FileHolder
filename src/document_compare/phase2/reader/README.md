# Phase 2 reader

Read-only projection of saved canonical/structured artifacts.

Read first: `result_reader.py` — builds summaries, bounded search and details;
contract in [docs/contracts/three-tool-reader.md](../../../../docs/contracts/three-tool-reader.md).

Input/output: reads an existing job's saved artifacts; returns bounded typed
envelopes; never mutates artifacts or accepts arbitrary paths.

Dependencies: `shared` JSON/path helpers; no Phase 2 agent or writer imports.

Check: `python -m unittest tests.phase2.test_reader`.
