---
name: fileholder-evaluate
description: Evaluate version, extraction or semantic evidence with explicit dataset and OLD/NEW orientation, separating regression tests from semantic truth and production acceptance.
---

# Evaluate comparison evidence

Use when comparing versions, extraction quality, matching outcomes or semantic
evidence. Do not use a model score to accept canonical changes. Read the shared
[authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules; they take precedence.

## Read

1. [Phase testing](../../../docs/operations/phase-testing.md) for workspace,
   phase and depth selection.
2. [Evaluation notes](../../../src/document_compare/phase1/evaluation/README.md)
   for the optional tooling and its limits.
3. [Semantic shadow evaluation](../../../docs/operations/semantic-shadow-evaluation.md)
   for the offline Gate B experiment.
4. [Contracts](../../../docs/contracts/README.md) for the evidence and reader
   interfaces being compared.

## Procedure

1. Name the dataset, workspace, version pair and orientation (which side is OLD
   and which is NEW). Do not mix runs or silently relabel a saved result.
2. Separate four claims: deterministic test/regression evidence, extraction
   quality, semantic truth, and production acceptance. Evidence for one is not
   evidence for another.
3. Keep Stage 13 advisory and Stage 14 authoritative. Gate B is a shadow-only
   research experiment with failed promotion criteria; its scores never change
   canonical status.
4. Treat Docling and local NLI as evaluation-only, optional tooling. Do not add
   them as runtime dependencies or download a model without approval.
5. Do not claim 80%/95% supported recall/precision, calibrated confidence, legal
   truth or semantic release readiness from a small or already exposed corpus.
6. Record unknowns, excluded pairs and any missing artifact rather than filling
   them by inference.

## Verify

Run the named evaluation or focused test in the selected environment. Report
exact fixture/workspace identity, command, counts and failures. Do not make a
provider or paid call without stating what data leaves the machine and obtaining
user approval.

## Stop and report

Stop if orientation or workspace is ambiguous, a private corpus is required
without approval, or the request is really a product acceptance decision. Report
the evidence axis, comparison orientation, exact results, limitations and the
next check the owner can run.
