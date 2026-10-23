# Workflows

Choose the smallest route that can produce trustworthy evidence. A route is a
reading and verification guide, not a mandatory ceremony.

## Routes

| Request | Route | Verification |
| --- | --- | --- |
| Question, review, status or inspection | Read-only; cite owners and unknowns | No edit or full suite |
| Tiny clear edit or visual micro-fix | Direct edit; one focused check | Smallest relevant check |
| Stage error, regression or unexplained result | [investigate skill](../../.agents/skills/fileholder-investigate/SKILL.md) | Reproduce, trace producer, regression before repair |
| Approved implementation or bounded correction | [change skill](../../.agents/skills/fileholder-change/SKILL.md) | Focused tests, then boundary check |
| Version/extraction/semantic comparison | [evaluate skill](../../.agents/skills/fileholder-evaluate/SKILL.md) | Dataset/direction evidence, separate claims |
| Explain a saved result | [read-results skill](../../.agents/skills/fileholder-read-results/SKILL.md) | Bounded reader, typed IDs, no rerun |
| Agreed change ready to hand off | [handover skill](../../.agents/skills/fileholder-handover/SKILL.md) | Real diff, exact checks, docs, skips |

A question that becomes a change starts a new bounded step. A tiny edit that
reveals an unknown-cause defect switches to investigation before repair.

## Working rules

1. Inspect current changes and relevant owners before editing. Preserve unrelated
   user work, existing workspaces and historical documents.
2. Keep one writer for overlapping files. Parallelize read-only discovery only.
3. Prefer a short checklist for an ordinary feature and a written plan for a
   multi-stage or high-uncertainty change. Do not plan a micro-fix.
4. Keep an initial, continuation or correction step explicit when resuming, so a
   later agent does not assume the earlier state.
5. Independent review is reserved for consequential authority, privacy,
   persistence, concurrency, migration or meaningful uncertainty. It is not a
   per-file or per-microtask step.
6. After two substantially similar unsuccessful corrections, change the diagnosis
   or escalate the concrete blocker. Do not repeat the loop.

## Stop conditions

Stop for a missing product decision, unavailable artifact/access, confidentiality
exception, credential need, new provider/model integration, destructive action or
a change that expands beyond the approved scope. Report the exact evidence and the
decision needed. Do not silently convert a proposed choice into accepted policy.

Final guidance lives in [AGENTS.md](../../AGENTS.md); this page explains routing
without duplicating product facts or commands owned elsewhere.
