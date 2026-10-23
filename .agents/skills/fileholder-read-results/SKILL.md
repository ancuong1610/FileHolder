---
name: fileholder-read-results
description: Explain one saved comparison result with the bounded result reader, preserved OLD/NEW orientation and typed IDs, without rerunning the engine or mutating the workspace.
---

# Read a saved result

Use when someone asks what a completed comparison found or why a change/review
item appears. Do not use to rerun Phase 1, edit a result or judge legal meaning.
Read the shared [authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules; they take precedence.

## Read

1. [Three-tool reader contract](../../../docs/contracts/three-tool-reader.md) for
   `summarize`, `search` and `details`.
2. [Phase testing](../../../docs/operations/phase-testing.md) for saved-workspace
   modes and `local_reviewer --check-only`.
3. [Result reader owner](../../../src/document_compare/phase2/reader/result_reader.py)
   when a filtering or pagination behavior is in question.

## Procedure

1. Confirm the selected workspace, phase and OLD/NEW orientation first. If any is
   missing, ask; do not guess from a nearby folder.
2. Use the typed reader tools: summarize for orientation, search for bounded
   candidates, details for specific change/review IDs and pages. Cite the IDs and
   pages you actually received.
3. Keep unresolved review unresolved. A review item is not approval, and engine
   or model acceptance is not legal or semantic ground truth.
4. Never dump a full report or private prompt into a model prompt, and do not
   reimplement filtering, pagination or context bounds in the agent. Those bounds
   belong in `result_reader.py`.
5. Do not modify the saved root, overwrite a result or rerun the engine while
   explaining it. Reading is read-only.

## Verify

Run `python -m document_compare.phase2.workflow.local_reviewer --phase phase2
--workspace WORKSPACE --check-only` when a local consistency check is needed; it
is offline and does not rerun Phase 1. The bounded reader is the source of typed
IDs, not a free-text summary.

## Stop and report

Stop when the workspace/direction is unavailable, the requested evidence is not
in the saved result, or the question requires a new comparison or product
decision. Report the workspace, reader calls used, cited IDs/pages, unresolved
items and what could not be established.
