# Phase 1 handover candidate — 2026-10-15

This branch is ready for the owner's company-machine review, not a claim that
real company PDFs/providers, Windows or semantic accuracy have been certified.
See [Company handover](COMPANY_HANDOVER.md) for installation and acceptance checks.

## Changes

- Generic comparison only; no CRR amendment provider or legal verification mode.
- Reconstructed TOC/hierarchy with conservative qualified-label matching and
  shared recurring-furniture evidence for body sections and provisional anchors.
- Exactly-once table cell accounting; malformed duplicate cell coordinates are
  rejected instead of silently overwriting numeric changes.
- Numeric/sign/operator/unit differences cannot disappear into cosmetic residual
  reconciliation. Structural split/merge review evidence is preserved.
- Added/deleted table cells now reach canonical output and the redline PDF with
  source IDs and locations. Legacy missing content is named explicitly.
- Additional `comparison_inline.pdf` and `inline_pdf_audit.json`: one-flow inline
  deletion/insertion markup and tightly constrained, corroborated word joining.
  Permanent hyphens, quantitative changes and uncertainty are preserved.

## Verification scope

The public checkout collects **515 tests**: **504 pass**, **11 optional private
sample replays are skipped** because those private fixtures are not published.
Mandatory synthetic/adversarial renderer, matching, table, service and MCP tests
remain active. A fresh public-requirements Python 3.11 environment passes
`pip check`; a fresh offline synthetic demo produces both PDFs and the audit.
Real MCP stdio discovery is included; real internal-provider agent answers are
reserved for company-side testing.

Locally, saved-artifact replay and canonical-to-PDF checks were also performed
without uploading supplied data. Those private artifacts are not in Git and are
not required for installation or the public regression suite.

## Limitations

- The inline view reduces proven layout noise; it does not prove semantic
  equivalence. When DI has lost line-break evidence, uncertain word joins remain
  visible. Omission/join counts and affected IDs are in the per-job audit.
- Table/matching uncertainty remains reviewable; an increased visible change
  count after a safety fix is not evidence of better semantic recall by itself.
- Production extraction remains DI. Docling evaluation is separate and optional,
  not a runtime requirement and not a certificate of whole-document accuracy.
- This is a local/loopback workflow, not an authenticated shared deployment.

No private PDFs, raw supplied DI samples, generated job output, credentials or
machine-local environments are included in this handover.
