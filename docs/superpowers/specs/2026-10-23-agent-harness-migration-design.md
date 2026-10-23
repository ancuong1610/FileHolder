# FileHolder coding-agent harness migration

- Status: design accepted following owner confirmation of the selective migration,
  team-readiness requirement and Copilot/GitLab Duo + GPT target. The owner then
  approved the implementation plan with “làm đi”; repository implementation is
  complete. Native Copilot/Duo loading and Windows acceptance remain unverified;
  the execution plan records checks and limitations.
  Commit/push was separately authorized after verification with “push nào”.
  Provider calls and global configuration changes remain unauthorized.
- Scope owner: project maintainer; technical execution: coding lead.
- Basis: owner approved selective adaptation from `a3se-ws2627`, preserving
  Knowledge Transfer and product behavior. Approval of this direction permits
  this design, not automatic implementation, commits or publication.
- Baseline: FileHolder `1a28c2447118128554bd8c24301298e28cdbf1c0`.

## 1. Intent and success criteria

Improve how a coding agent finds authoritative context, selects a bounded task,
preserves existing decisions, verifies changes and reports limitations. This is
the repository's development harness, **not** the Phase-2 result-reading agent.

Success means correct task routing, smaller relevant reading paths, explicit
decision/evidence boundaries, and executable structural checks. A cleaner tree
alone does not establish better agent behavior or comparison accuracy.

The owner additionally requires a **team-usable integrated deliverable**, not
design documents alone. A developer who clones/pulls the repository must be
able to follow the quickstart and use the harness with a supported coding-agent
client. Ordinary client installation/sign-in is a prerequisite, not something
the repository silently installs or configures globally.

Supported team clients are **GitHub Copilot and GitLab Duo**, with a GPT model
selected through the client where available under the team's plan/admin policy.
The repository harness is model-agnostic: it does not wire an OpenAI SDK, supply
API credentials, select a paid model, or bypass company/provider restrictions.
OpenCode/Codex compatibility is incidental, not the team's acceptance target.
Copilot/Duo with GPT is not offline/local inference: code or attached context
may be processed by the configured assistant service under organization policy.
The quickstart must distinguish that from the optional local NLI experiment and
must not attach private PDFs, raw DI, result text or secrets by default.

Binding requirements:

- Preserve `docs/KnowlesgeTranser Phase 1.md`, including its current filename and
  links. The migration must leave its bytes unchanged. Baseline SHA-256:
  `801a034d61de3be2c07814ce3a348b847b6ce2027125f93ba944b2e97d675e21`.
- Keep the existing Python package tree, 15 stages, contracts, environment keys,
  source/ID conservation, saved workspaces and report/reader behavior unchanged.
- Keep commands and factual explanations in their existing owning documents;
  reference them rather than copying them into every skill.
- Keep small tasks small. No mandatory plan, specialist swarm, review cycle or
  full suite for every question or micro-fix.
- No global OpenCode/Codex configuration changes, model selection, new MCP,
  agent server, persistent agent memory, Node/Java/Docker dependency or provider
  call. No automatic commit/push or modification of the reference repository.

## 2. Reference evidence and adaptation choice

The local reference is `/Users/cuong/Documents/a3se-ws2627`, inspected at commit
`6db08ea`. Relevant evidence:

- `AGENTS.md`: short entry point, evidence/assumption/decision separation and
  links to the task-specific reading path.
- `docs/README.md`: lifecycle reading routes, one authoritative location per
  fact, topic growth only when independent parts are needed.
- `.agents/skills/create-code/SKILL.md`: bounded context, authority, initial/
  continuation/correction modes, readiness and evidence-based completion.
- `tools/harness/validate.mjs`: actual static checks for links, README routing,
  decision records and reference-specific identifiers/issue templates.

The reference has useful conventions, not demonstrated superiority on FileHolder
tasks. Its documentation includes proposed/unreviewed material; its validator
does not prove semantic correctness, product tests or agent effectiveness.

Selected approach: adapt those conventions to existing FileHolder ownership.
Reject wholesale copying of its 30 skills, educational/domain assumptions,
requirement namespaces, issue forms and diagram toolchain. Also reject a simple
folder rename without task routing and checks.

Reference license: MIT, copyright 2026 Dr. Markus Voß. This design adapts ideas;
if implementation copies substantial source/prose, retain the applicable MIT
notice/text and record exact copied files. Do not imply reference decisions are
accepted FileHolder product requirements.

## 3. Target structure and authority

```text
AGENTS.md
.github/
  copilot-instructions.md   # Thin Copilot entrypoint to shared repository rules
.agents/
  README.md
  skills/
    README.md
    fileholder-investigate/SKILL.md
    fileholder-change/SKILL.md
    fileholder-evaluate/SKILL.md
    fileholder-read-results/SKILL.md
    fileholder-handover/SKILL.md
docs/
  README.md
  governance/
    README.md
    authority.md
    workflows.md
    definition-of-done.md
    decisions/
      README.md
      0001-adopt-selective-agent-harness.md
  architecture.md
  KnowlesgeTranser Phase 1.md
  review-guide.md
  contracts/                 # Existing owners, unchanged paths
  operations/                # Existing commands and operating safety
    agent-harness-quickstart.md  # Self-service team setup and first task
  releases/                  # Historical evidence
  superpowers/               # Existing design/plan history and current proposal
tools/
  README.md
  harness/
    README.md
    validate.py
tests/integration/
  test_harness.py
```

Responsibilities:

| Location | Owns |
| --- | --- |
| `AGENTS.md` | Short mandatory boundaries and task router; no full process manual |
| `.github/copilot-instructions.md` | Copilot-discovered bootstrap pointing to shared rules, not a second source of product knowledge |
| `docs/README.md` | Documentation map and active versus historical reading paths |
| `docs/governance/authority.md` | Artifact authority and facts/assumptions/proposals/decisions |
| `docs/governance/workflows.md` | Proportionate task routes, continuation and escalation |
| `docs/governance/definition-of-done.md` | Evidence required for the selected task, not universal release approval |
| `.agents/skills/*/SKILL.md` | Reusable task procedure, permitted scope, checks and stop conditions |
| Existing architecture, contracts, operations and Knowledge Transfer | Current project-specific knowledge in their existing concerns |
| Existing subsystem READMEs and review guide | Code owners and focused verification commands |
| `docs/governance/decisions/` | New consequential harness decisions, not retroactive fabricated history |

Conflicts between code, accepted contracts and prose must be recorded and
investigated, not silently resolved by declaring one document universally true.
The maintainer owns product/authority changes. Specialists have only their
explicitly delegated scope; model suggestions and successful tests do not
constitute product approval.

Active architecture/testing guidance is the normal starting point. Historical
specs, releases and old execution plans remain linked background/evidence, not
the latest runtime specification. Existing filenames and document locations
remain valid; no mass document move or metadata retrofit is required.

## 4. Task routes and repository skills

Each skill has a narrow trigger, scope/exclusions, authoritative reading links,
procedure, proportional verification, stop conditions and concise report format.
It does not reproduce global brainstorming/TDD/debugging instructions or override
harness/system policy. Project-prefixed IDs avoid collisions with global skills.

| Skill | When used | Key boundary |
| --- | --- | --- |
| `fileholder-investigate` | Stage error, unexplained result or regression | Record stage/command/artifacts; smallest reproduction and producer trace before repair |
| `fileholder-change` | Approved implementation or bounded correction | Preserve contracts/ownership; one writer for overlapping files; no new authority by accident |
| `fileholder-evaluate` | Compare versions, extraction or semantic evidence | Explicit dataset/workspace/OLD-NEW orientation; separate tests, extraction, semantic truth and production acceptance |
| `fileholder-read-results` | Explain a saved result | Bounded summarize/search/details, typed IDs/pages; no rerun or saved-root mutation |
| `fileholder-handover` | Verify and hand off an agreed change | Real diff, exact checks, docs impact and skipped cases; no implicit commit/push/deployment |

Questions and inspections remain lead-only/read-only. Tiny clear fixes use the
smallest relevant check. Ordinary features use one bounded implementer packet.
Unknown-cause defects first gather evidence. Independent review is reserved for
consequential authority/privacy/persistence/concurrency or meaningful uncertainty.
After two substantially similar unsuccessful corrections, change the diagnosis
or escalate the concrete blocker rather than repeat the loop.

Source-specific safeguards remain explicit: Stage 13 advisory/Stage 14 authority;
uncertainty is not a mandatory operator task; supported text is not semantic
proof; Docling is evaluation-only; Gate B is shadow-only and failed promotion
criteria; source-strikethrough extraction is not implemented. Link to owners for
details rather than duplicating thresholds or benchmark numbers in each skill.

Skills use `.agents/skills/` as a single shared repository source, supported by
the current official Copilot and GitLab Duo documentation. Do not mirror skill
copies in client-specific folders or make OpenCode/Codex the primary target.
The Copilot bootstrap is intentionally short and links to root AGENTS, the docs
router and skill index. GitLab Duo Agent Platform reads root AGENTS directly;
do not add redundant `chat-rules.md` copies or custom flows just for this.

Compatibility must be stated by client **surface and version**, not brand alone:

- Copilot skills work on documented agent-enabled surfaces, including IDE agent
  mode; exact IDE/version support follows its current feature matrix. Repository
  custom instructions use `.github/copilot-instructions.md`.
- Duo local project skills require a supported Agent Platform setup. Current
  documentation lists GitLab for VS Code >=6.71.4 or Duo CLI >=8.73.0. Root AGENTS
  support alone is not proof of native skill support. Duo Chat in the GitLab web
  UI does not support skills; supported flows are a different surface, and Code
  Review Flow is excluded. No custom flow integration is added in this slice.
- Start a fresh conversation after instruction/skill updates. Where native skill
  support is unavailable, the quickstart can explicitly attach/read a procedure
  as Markdown, but must label this as a manual fallback, not native discovery.
- Confirm actual discovery/loading in the installed team clients; otherwise
  record the unverified client/platform/version and a short human smoke checklist.
  Do not claim universal or already-tested compatibility from file layout alone.

Official sources checked for this design:
https://docs.github.com/en/copilot/concepts/agents/about-agent-skills ;
https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions ;
https://docs.gitlab.com/user/duo_agent_platform/customize/agents_md/ ;
https://docs.gitlab.com/user/duo_agent_platform/customize/agent_skills/ .

## 5. Small structural validator

Reuse Python >=3.10 and `unittest`, not the reference's Node-26 validator or a new
validation framework. The concrete gap is repository-specific routing/link and
skill/decision integrity; the existing README coverage guard checks only part
of this. Preserve its architecture and dependency checks.

`python tools/harness/validate.py` is read-only and deterministic. It checks:

- Required entry points and directly referenced active documentation exist.
- Local Markdown file/directory destinations resolve, including encoded spaces;
  do not fetch external URLs or pretend to validate their availability.
- Skill entrypoints have unique prefixed IDs, usable descriptions and valid
  repository-relative reading links; the collection has an index. Immediate
  skill directories use `SKILL.md`, not redundant README files.
- New governance directories have README routing. Decision files have a stable
  identifier, status and index entry; only real owner approval marks acceptance.
- Harness task routes retain links to Knowledge Transfer, architecture,
  phase-testing, review guide and current contracts.

Scope validation to declared maintained repository paths. Never traverse input,
workspace, model caches, credentials, virtual environments or the external
reference repo. Do not execute linked scripts, model calls or sample commands.
Historical links requiring unavailable evidence are reported separately, not
rewritten as current facts or used to demand publication of private data.
This is file-path/structure validation, not a full Markdown grammar, semantic
validator, secret scanner or assertion that an agent followed instructions.

Tests use small temporary fixtures for broken links, duplicate/malformed skills,
missing indexes, unresolved decisions, encoded paths and ignored/private roots.
No network, new test framework or product runtime import is needed.

## 6. Migration and preservation

1. Record baseline status and Knowledge Transfer hash. Leave unrelated files,
   including the untracked `.DS_Store`, alone. Use an ordinary worktree for the
   implementation slice if isolation from current work is needed.
2. Add the small governance core and indexed skills; adapt wording to actual
   FileHolder boundaries. Do not copy whole reference catalogs.
3. Make AGENTS/docs entrypoints concise routers, preserving existing safety
   requirements and document locations. Fix misleading historical-only index
   wording without rewriting the historical specs themselves.
4. Add validator tests first, then the validator and usage notes. No product
   package/requirements or Phase-2 agent changes.
5. Verify structure, preservation, task routing and relevant regression. Report
   the exact candidate diff and remaining discovery/behavior limitations.

Keep this as one coherent writer packet. A later implementation plan will name
exact checks and milestones after approval of this written design. No recurring
review per file, wholesale source refactor, automatic browser loop or installation.

## 7. Acceptance and evidence

Team-readiness acceptance:

- Add `docs/operations/agent-harness-quickstart.md`, linked from the root README
  and operations index: prerequisites, opening the repository, discovering or
  explicitly invoking a project skill, a first read-only task, validator command
  and troubleshooting missing skills. State tested client/platform coverage.
- Harness files travel with the repository. Normal use must not depend on
  `/Users/cuong/...`, the external reference checkout, personal global skills,
  a temporary venv, the author's model cache or this conversation's history.
  External-reference paths in design provenance are not runtime prerequisites.
- Validate from a clean tracked-file checkout with no copied private configuration
  or ignored workspace artifacts. Use portable relative links and Python commands;
  document platform-specific invocation where necessary. Do not claim Windows
  acceptance without testing it on Windows.
- Provide short example requests for investigation, bounded changes, evaluation,
  saved-result explanation and handover, stating expected skill/reading route.
- Confirm discovery and loading in the supported client(s) rather than assuming
  that generic Markdown instructions are executable skills everywhere. A client
  unavailable for testing is a declared compatibility gap, not a silent pass.

Structural/compatibility acceptance:

- Validator passes on the repository and fails intentionally broken fixtures.
- Existing architecture/README coverage checks still pass.
- Knowledge Transfer SHA-256 is identical; existing important paths still resolve.
- No runtime source, requirements, canonical artifact or user workspace changed.
- Skills are discoverable/loadable in a fresh available agent context; otherwise
  explicitly report that installation-level verification remains unperformed.
- Copilot and Duo quickstart paths distinguish native discovery, manual fallback
  and unsupported surfaces. GPT availability is a client/admin prerequisite, not
  an untested model-integration feature or a claim that all Duo installations
  expose the same GPT choices as Copilot.

Behavioral smoke evaluation uses five bounded read-only prompts, covering a
Stage-5 extraction/structure defect, saved-result explanation, semantic-evaluation
claim, tiny report change and a real-provider run request. Compare the old/new
routes against the same declared expectations: correct owner/source, minimal
context, proper check depth, OLD/NEW orientation, and permission boundary. No
actual paid/provider calls, private corpus reads or code changes in this smoke.
Report observable successes/failures; do not equate a new folder tree or static
test pass with proved agent productivity improvement.

Definition of done for this migration: scoped diff, structural/unit checks,
architecture compatibility, preserved knowledge, routing/discovery evidence,
English documentation and explicit skipped checks. Final report states changed
files, exact commands/results, reference attribution, uncertainties and the next
user-visible action. No commit/push unless separately requested.
