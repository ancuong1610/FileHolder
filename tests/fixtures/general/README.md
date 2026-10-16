# General fixtures

Bundled already-extracted DI document pairs used by the offline demo and
pipeline tests. Each pair is a fully synthetic, compact merged Azure Document
Intelligence document set (no spans/words/lines needed for s02–s15).

Read first: `internal_policy_report/` (the demo default); then `crr3/` and
`scai_api_guide/`. Each case README documents provenance and declared metrics.

Input/output: read-only inputs seeded by `python -m document_compare demo
--fixture <name> --root <fresh>`; per-case bytes are baseline-pinned.

Dependencies: none at import time; consumed by the CLI and pipeline tests.

Check: `python -m unittest tests.integration.test_offline_setup`.
