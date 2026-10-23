---
name: fileholder-investigate
description: Investigate a stage error, unexplained comparison result or regression by recording evidence and tracing the smallest saved reproduction before proposing a repair.
---

# Investigate a comparison defect

Use when a stage fails, a result is unexplained, or a regression needs a root
cause. Do not use for a known wording-only edit or a general architecture
question. Read the shared [authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules; they take precedence.

## Read

1. [Review guide](../../../docs/review-guide.md) to select the owning stage or
   module and its focused test.
2. [Architecture](../../../docs/architecture.md) for the stage boundary and
   dependency direction.
3. [Phase testing](../../../docs/operations/phase-testing.md) for the smallest
   saved input or replay command.

Read only the named owner and its test, not the whole source tree.

## Procedure

1. Record the failing stage, exception, command, input availability and whether
   the workspace was OLD or NEW. Ask for a missing workspace or artifact only
   when it blocks reproduction; do not invent one.
2. Reproduce with the smallest saved stage input. If no saved evidence exists,
   state that the defect is unconfirmed and request the artifact.
3. Trace the value back to its producer. Separate observed code/artifact evidence
   from hypotheses and from unresolved review.
4. Add a regression test before a repair when the defect is confirmed. Do not
   remove validation merely to make the run finish.
5. Keep raw text, IDs, locations and old/new conservation intact. Stage 13
   advice never becomes Stage 14 authority.

## Verify

Run the focused test named by the review guide, then the smallest affected
module test. Do not start a full suite per hypothesis. A synthetic pass proves a
contract/regression, not company-PDF, semantic or Windows acceptance.

## Stop and report

Stop for a product decision, missing private artifact, credentials, provider
call or a fix that changes contracts or stage ownership. After two similar failed
repairs, change the diagnosis or report the concrete blocker. Report the stage,
reproduction, evidence, changed test/files, exact check results and what remains
unknown.
