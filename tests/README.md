# Tests

Offline test suite, split by ownership. No network, credentials or real provider
calls; the evaluator runs in mock mode.

Read first: `integration/test_architecture.py` (structural guards), then the
folder for the area you changed. Review routing is in
[docs/review-guide.md](../docs/review-guide.md).

Input/output: [fixtures/](fixtures/README.md) hold synthetic inputs;
[support/](support/README.md) holds shared builders. Tests use temporary roots.

Dependencies: the installed `document_compare` package plus `requirements.txt`;
no private fixtures are required (optional ones skip).

Check: `python -m unittest discover -s tests` or a single path such as
`python -m unittest tests.phase1.test_reconcile`.
