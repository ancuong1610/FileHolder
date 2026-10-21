# Coding-agent guide

You are a project collaborator: explain the system, investigate defects, help
people run comparisons and interpret evidence, as well as implement changes.
Answer in the user's language; repository documentation is maintained in English.

## Read first, then follow the relevant owner

1. `README.md`: install, offline demo, commands and scope limits.
2. `docs/architecture.md`: two phases, all 15 stage purposes and dependency rules.
3. `docs/operations/phase-testing.md`: choose phase, workspace and test depth.
4. `docs/review-guide.md`: owning files and focused checks; subsystem READMEs.
5. `docs/contracts/` for interfaces; `docs/operations/company-handover.md` for
   company-machine/Windows acceptance. Historical specs/plans are background,
   not proof that a current feature works.

## How to work with people

- For an architecture question, explain purpose → data flow → relevant files.
  Distinguish observed code/artifact evidence from hypotheses and unknowns.
- For a result question, confirm the selected workspace and old/new orientation.
  Use the bounded reader's summarize/search/details and cite typed change/review
  IDs and pages. Do not invent missing context or treat unresolved review as
  approved. Engine acceptance is not legal/semantic ground truth.
- For an error, record stage, exception, command and input/artifact availability.
  Reproduce with the smallest saved stage input, trace its producer, add a
  regression before repair. Never remove validation merely to make a run finish.
- Before editing, inspect current changes and relevant code. Preserve user
  changes and existing workspaces. Avoid unrelated refactors and new frameworks.
  If Git metadata is broken, report it; do not initialize/repoint/reset it without
  permission. A local snapshot/diff can support independent investigation.
- Ask only for blocking product decisions, unavailable artifacts or access.
  Explain what the user can test next; do not claim provider/Windows/private-data
  acceptance from synthetic checks.

## Ownership and invariants

- `src/document_compare/phase1/`: engine, stages 01–15, structure, matching,
  contracts, reporting. Stage 15 is still Phase 1 despite historical filenames.
- `src/document_compare/phase2/`: API, workflow, MCP, bounded result reader, agent.
- `shared/`: generic path/JSON helpers only. Phase 2 may depend on Phase 1/shared;
  Phase 1 never imports Phase 2; shared imports neither. Evaluation is optional
  tooling, not a runtime engine dependency.
- Preserve raw extraction text, IDs, locations and old/new conservation.
  Anchors/structure/furniture are evidence-based hypotheses. Derived furniture
  must not become TOC content or section headings; its membership is `layout`.
- Stage 13 recommendations are advisory. Stage 14 deterministic rules own final
  resolution. Keep review items visible; preserve meaningful numbers/negations.
- Reader filtering/pagination/context bounds belong in `phase2/reader/result_reader.py`.
  Do not reimplement them in the agent or load full reports into model prompts.
- Preserve `CRR_` environment keys and existing artifact/HTTP/MCP contracts.

## Documentation impact before commit

Assess documentation impact from the actual diff before every commit, and update
affected docs in the same change. Not every document needs a ceremonial edit.

| Change | Documentation to assess/update |
| --- | --- |
| Commands, config, setup | Root README and operations/testing |
| Stages, dependencies, data flow | Architecture |
| Algorithms and representations | Knowledge Transfer Phase 1 |
| Statuses, schemas, artifacts, API/reader behavior | Contracts and consumer guides |
| Agent workflow and invariants | AGENTS.md |

Check related links and commands, then report the documentation changes or explain
why none are needed. Documentation approval does not authorize an automatic
commit or push.

## Run and verify

Use a Python >=3.10 virtual environment (3.11 recommended), install
`requirements.txt` and `python -m pip install -e .`. Use that same interpreter for
subprocesses/tests. See README for the known environment-specific setuptools
workaround; never weaken company TLS/security policy.

```bash
# Fresh, synthetic, offline Phase 1; refuses existing output
python -m document_compare demo --fixture internal_policy_report --root "workspace/agent demo"
# Read that exact workspace, no credentials/network/model calls
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/agent demo" --check-only
# Phase-specific unit tests, then full regression at a meaningful boundary
python -m unittest discover -s tests/phase1
python -m unittest discover -s tests/phase2
python -m unittest discover -s tests
```

For real Phase 1, saved-result chat, combined runs, actual API/MCP mock checks,
and EBA stage replay, follow `docs/operations/phase-testing.md`. Before a real
provider call, state what data will leave the machine and obtain user approval
for the call/cost. Mock Stage 13 does not prevent real Stage 1 extraction.

## Data and operations safety

- `.env`, `input/*`, cached DI and `workspace/` may contain confidential material.
  Never commit, upload, paste or log credentials, internal endpoints, documents,
  raw DI, model traces or private result text outside the approved environment.
- Saved Phase 2 reads the selected workspace without modifying it or rerunning
  the engine. An existing full result is not permission to overwrite it.
- Services remain loopback-only. Do not deploy, publish, expose a port publicly,
  install internal SDKs from unapproved sources or modify credentials implicitly.
- Finish with changed files, exact checks/results, skipped/unverified cases and
  the next user-visible action. Tests passing proves contracts/regressions, not
  universal comparison accuracy.
