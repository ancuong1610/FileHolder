# Phase 1 pipeline

The CLI, stage runner and generic-only mode configuration.

Read first: `cli.py` (argparse subcommands) and `mode.py` (generic-only mode).

Input/output: `python -m document_compare run|eval|redline|eval-gt|demo|benchmark-performance|review-evidence`;
`__main__.py` at the package root delegates here.

Dependencies: `phase1/stages`, `phase1/contracts`; never Phase 2.

Check: `python -m unittest tests.integration.test_pipeline tests.integration.test_offline_setup`.
