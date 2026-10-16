# Source

The installable package lives under `src/` (setuptools `src/` layout).

Read first: [document_compare/](document_compare/README.md) for the two phases
and the shared helpers.

Input/output: `src/` holds only Python package code; runtime artifacts go to the
ignored `workspace/`, never into the package tree.

Dependencies: `document_compare` imports only the standard library plus the
declared `requirements.txt` packages.

Check: `python -m unittest tests.integration.test_architecture`.
