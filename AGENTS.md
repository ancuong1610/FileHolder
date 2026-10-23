# Coding-agent guide

You are a project collaborator: explain the system, investigate defects, help
people run comparisons and interpret evidence, as well as implement changes.
Answer in the user's language; repository documentation is maintained in English.

## Start here

1. [README](README.md): install, offline demo, commands and scope limits.
2. [Documentation router](docs/README.md) and [architecture](docs/architecture.md):
   where a fact or responsibility lives; all 15 stage purposes and dependencies.
3. [Governance workflows](docs/governance/workflows.md) and the
   [skill index](.agents/skills/README.md): choose the smallest proportional task.
4. [Phase testing](docs/operations/phase-testing.md), [review guide](docs/review-guide.md)
   and [contracts](docs/contracts/README.md) for the selected task.
5. [Knowledge Transfer — Phase 1](docs/KnowlesgeTranser%20Phase%201.md) for the
   full stage-by-stage representation and algorithm detail.

Historical specs, plans and releases are background evidence, not proof that a
current feature works. Read the owning file and its focused test, not all source.
For an architecture question, explain purpose → data flow → relevant files and
separate observed code/artifact evidence from hypotheses and unknowns.

## Mandatory boundaries

- **Confidentiality.** `.env`, `input/*`, cached DI and `workspace/` may contain
  confidential material. Never commit, upload, paste or log credentials, internal
  endpoints, documents, raw DI, model traces or private result text outside the
  approved environment. Copilot/GitLab Duo with GPT may process assistant context
  remotely under organization policy; that is not local inference. State the
  outbound-data consequence and obtain approval before a real provider call.
- **Permissions.** Ask only for blocking product decisions, unavailable artifacts
  or access. Inspect current changes and preserve unrelated user work. Avoid
  unrelated refactors and new frameworks. If Git metadata is broken, report it;
  do not initialize, repoint or reset it without permission. Do not add
  provider/SDK credentials, select a paid model, change global client
  configuration, expose a service, install unapproved software, deploy, publish,
  commit or push merely because a tool can do it.
- **Phase ownership.** `src/document_compare/phase1/` owns the engine, stages
  01–15, structure, matching, contracts and reporting; stage 15 is still Phase 1
  despite historical filenames. `phase2/` owns API, workflow, MCP, bounded reader
  and agent. `shared/` holds generic path/JSON helpers. Phase 2 may depend on
  Phase 1/shared; Phase 1 never imports Phase 2; shared imports neither.
- **Source conservation.** Preserve raw extraction text, IDs, locations and
  old/new conservation. Anchors, structure and furniture are evidence-based
  hypotheses; derived furniture must not become TOC content or section headings,
  and its membership is `layout`. Keep meaningful numbers and negations. Leave
  existing document paths and Knowledge Transfer bytes unchanged unless the
  change explicitly owns that content.
- **Evaluator authority.** Stage 13 recommendations are advisory; Stage 14
  deterministic rules own final resolution. Keep review items visible. Gate B,
  local NLI and Docling are optional evaluation/shadow evidence, never canonical
  status, calibrated confidence or legal truth. Do not claim 80%/95% semantic
  recall/precision or production readiness.
- **Bounded reader.** Filtering, pagination and context bounds belong in
  `phase2/reader/result_reader.py`. Use its summarize/search/details tools with
  typed IDs and pages; do not reimplement bounds in the agent or load full
  reports/private prompts into model context.
- **Saved results.** Reading a selected workspace does not rerun the engine or
  mutate the root. An existing full result is not permission to overwrite it.
- **Contracts and operations.** Preserve `CRR_` environment keys and existing
  artifact/HTTP/MCP contracts. Services remain loopback-only. Never remove
  validation merely to make a run finish.

## Route by task

| Task | First action |
| --- | --- |
| Question, review, status, inspection | Answer read-only; no plan or reviewer required |
| Tiny clear edit or UI micro-fix | Smallest direct edit; one focused check |
| Stage error or unexplained regression | [investigate](.agents/skills/fileholder-investigate/SKILL.md): reproduce and trace before repair |
| Approved implementation or bounded correction | [change](.agents/skills/fileholder-change/SKILL.md): preserve contracts and user work |
| Version/extraction/semantic comparison | [evaluate](.agents/skills/fileholder-evaluate/SKILL.md): state dataset and OLD/NEW orientation |
| Explain a saved result | [read-results](.agents/skills/fileholder-read-results/SKILL.md): bounded reader, no rerun |
| Agreed change ready to hand off | [handover](.agents/skills/fileholder-handover/SKILL.md): real diff and exact checks |

Inspect current changes before editing. Keep one writer for overlapping files;
parallelize read-only discovery only. Independent review is reserved for
consequential authority/privacy/persistence/concurrency or meaningful
uncertainty, not every file or microtask. After two similar failed repairs,
change the diagnosis or escalate the concrete blocker.

## Documentation impact

Assess impact from the actual diff before commit and update the owning document in
the same change:

| Change | Documentation to assess/update |
| --- | --- |
| Commands, config, setup | Root README and operations/testing |
| Stages, dependencies, data flow | Architecture |
| Algorithms and representations | Knowledge Transfer Phase 1 |
| Statuses, schemas, artifacts, API/reader behavior | Contracts and consumer guides |
| Agent workflow and invariants | AGENTS.md and governance |

## Run and verify

Use a Python >=3.10 virtual environment (3.11 recommended), install
`requirements.txt` and `python -m pip install -e .`. Use that same interpreter for
subprocesses/tests. See README for the environment-specific setuptools workaround;
never weaken company TLS/security policy.

```bash
# Fresh, synthetic, offline Phase 1; refuses existing output
python -m document_compare demo --fixture internal_policy_report --root "workspace/agent demo"
# Read that exact workspace, no credentials/network/model calls
python -m document_compare.phase2.workflow.local_reviewer --phase phase2 --workspace "workspace/agent demo" --check-only
# Harness structure, then phase/full regression at a meaningful boundary
python tools/harness/validate.py
python -m unittest discover -s tests/phase1
python -m unittest discover -s tests/phase2
python -m unittest discover -s tests
```

For real Phase 1, saved-result chat, combined runs, API/MCP mock checks and EBA
replay, follow [phase testing](docs/operations/phase-testing.md). Mock Stage 13
does not prevent real Stage 1 extraction. For team client setup, read the
[agent harness quickstart](docs/operations/agent-harness-quickstart.md).

Finish with changed files, exact checks/results, documentation impact,
skipped/unverified cases and the next user-visible action. No automatic
commit/push. Tests passing proves contracts/regressions, not universal comparison
accuracy or that a specific client loaded a skill.
