# Phase 1 status

## Public export

The public Phase 1 export preserves the comparison engine and its schemas. It
contains no private PDFs, review packets, workspace outputs, credentials, or
company-specific evidence. The engine is unchanged; this export adds only
portable test setup and sanitized documentation.

## Checks and evidence

- Tests use the active interpreter (`sys.executable`) and deterministic offline
  settings (`PYTHONDONTWRITEBYTECODE=1`, `PYTHONHASHSEED=0`, mock evaluator,
  generic domain).
- Stage smoke tests create temporary inputs from the bundled synthetic
  `internal_policy_report` fixture through the public CLI seeder and pipeline.
- Validation tests exercise malformed duplicate records in both normal and
  optimized Python modes; they must fail validation in both modes.
- Synthetic fixtures are documented as synthetic and are not real PDFs or
  production accuracy evidence.

## Established behavior

- Duplicate routes retain distinct evidence identity and source links.
- Public installation does not claim the internal SDK; mock mode is explicit.
- Retry behavior is bounded and runtime validation survives `python -O`.
- Numeric, date, reference, negation, modal, and hyphenation safety cases are
  covered by the existing tests.
- Metrics disclose unsupported categories instead of implying perfect scores.
- File orchestration in stages s02–s10 is behind guarded `main()` functions;
  pure helpers can be imported without running the pipeline.

## Limits

Real Azure extraction, provider/LLM calls, private source documents, and
company credentialed workflows are intentionally not part of this public
export. Follow the README real-run instructions only in an authorized,
separate environment, and never place credentials or private documents in the
repository or test evidence.
