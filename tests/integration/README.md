# tests/integration

Cross-phase and setup checks: package/import boundaries, pipeline setup,
runtime validation and README coverage.

Read first: `test_architecture.py` (namespaces, 15 stages, dependency direction,
README coverage).

Input/output: reads the repository tree and runs whole-pipeline smoke flows in
temporary roots; no fixed artifacts written.

Dependencies: spans both phases deliberately (this is the only place they meet).

Check: `python -m unittest discover -s tests/integration`.
