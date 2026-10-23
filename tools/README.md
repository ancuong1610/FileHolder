# Tools

Repository maintenance utilities, separate from the product runtime.

- [harness/validate.py](harness/validate.py) — read-only structural validation of
  the coding-agent harness. Run `python tools/harness/validate.py` from the
  repository root. See [harness/README.md](harness/README.md) for the checks and
  exit codes.

No tool here installs clients, fetches URLs, executes documented commands or
changes product data.
