# Coding-agent harness migration implementation plan

> One coherent writer packet, followed by lead verification and one final
> boundary-focused review. Follow the repository's proportional routing policy;
> do not create a worker or review cycle per file.

**Goal:** deliver a repository-local coding harness usable by the owner and
colleagues with supported Copilot/GitLab Duo agent clients and their allowed GPT.

**Architecture:** shared root instructions, task-local skills and existing owning
documentation; a thin Copilot entrypoint; Python static validation plus a team
quickstart. No product runtime dependency or client/API integration service.

**Tech stack:** Markdown/Agent Skills frontmatter, Python >=3.10 standard library,
existing `unittest`. No install beyond the project's existing environment.

**Spec:** [accepted design](../specs/2026-10-23-agent-harness-migration-design.md).
**State:** repository migration implemented and verified on `feat/agent-harness`.
Native Copilot/Duo and Windows acceptance remain unverified; the quickstart
provides the in-client confirmation/fallback path. After verification, the owner
separately authorized commit/push with “push nào”.

## Global constraints

- Preserve Knowledge Transfer bytes/path and baseline SHA-256
  `801a034d61de3be2c07814ce3a348b847b6ce2027125f93ba944b2e97d675e21`.
- Do not edit `src/`, requirements, package configuration, existing test behavior,
  contracts, canonical records, confidential inputs/workspaces or reference repo.
- Root AGENTS must retain confidentiality, permissions, one-way phase ownership,
  source conservation, evaluator authority, bounded reader and no-overwrite rules.
- No global config, credentials, GPT endpoint/model selection, new provider calls,
  Node/Java/Docker, server, MCP, hidden agent state, commit or push.
- Skills live only in `.agents/skills/`; prefix IDs `fileholder-`. No duplicated
  per-client copies. Do not silently convert proposed choices into accepted policy.
- Keep questions/micro-fixes on the direct route; independent review is not a
  universal step. Use the same interpreter for tests and subprocesses.
- The current workspace is already a linked worktree. Preserve unrelated
  `.DS_Store`; use a dedicated ordinary worktree only if needed for isolation.

## Review focus

1. Instructions must not authorize provider calls, exposure of private context,
   model acceptance or destructive operations merely because an agent can run tools.
2. Native skill discovery varies by client surface/version; manual Markdown
   fallback and unverified compatibility must not be advertised as native support.
3. A read-only validator must not traverse ignored inputs/workspaces, follow paths
   outside its declared scope or execute commands embedded in documents.
4. New routing must retain existing factual owners, Knowledge Transfer and valid
   old links without loading every document or superseding higher-level policy.
5. A clean checkout must work without personal configs, reference checkout,
   temporary absolute paths, model downloads or pre-existing conversation state.

## One implementation packet

### A. Pin structural and preservation expectations first

**Create:** `tests/integration/test_harness.py`.

**Validator interface:** `validate_repository(root: Path) -> list[str]` in
`tools/harness/validate.py`; empty list means structural success. CLI
`python tools/harness/validate.py [--root PATH]` exits 0 on success, 1 on findings,
2 on invalid/unavailable root. Read-only; report repository-relative diagnostics.

- [x] Write and run RED fixture tests: valid minimal harness, missing required
  entrypoint/index, broken local Markdown link, encoded-space destination,
  duplicate or malformed skill name/description, missing/invalid decision status,
  and missing required Knowledge Transfer/architecture/testing/contract routes.
- [x] Add scope tests: ignored/private roots and symlinks outside selected roots
  are not recursively inspected; external URLs are not fetched; Markdown code
  examples are not executed or treated as live local links. Reports are stable.
- [x] Add real-repository tests pinning Knowledge Transfer hash, the five skill
  IDs, Copilot adapter/shared-router links and absence of per-client skill copies.
  Do not pin arbitrary prose or claim these tests prove agent understanding.

### B. Add shared authority, routes and five skills

**Create:** `.agents/README.md`, `.agents/skills/README.md`, the five `SKILL.md`
paths in the design, and `docs/governance/{README,authority,workflows,definition-of-done}.md`.
Create `docs/governance/decisions/README.md` and
`0001-adopt-selective-agent-harness.md` recording only the actual approved choice.

**Modify:** `AGENTS.md`, `docs/README.md`, `docs/review-guide.md` only where routing
requires it; `docs/superpowers/{README.md,specs/README.md,plans/README.md}` to
distinguish historical process evidence from current documentation and this work.

- [x] Write task procedures with `name` matching the directory and clear
  `description`: `fileholder-investigate`, `fileholder-change`,
  `fileholder-evaluate`, `fileholder-read-results`, `fileholder-handover`.
- [x] Each procedure links to existing owning docs, states permissions/exclusions,
  relevant focused checks, continuation state and concrete stop conditions. Use
  links resolved from the actual skill directory; no personal file references.
- [x] Keep root AGENTS short, linking detail into governance and the skill index.
  Cross-check all current invariants against the original AGENTS; do not remove
  a rule merely to shorten the file or duplicate large global skills.
- [x] Retain existing source paths and Knowledge Transfer unchanged. Rewrite
  original reference wording for FileHolder; if substantial text/code is copied,
  include the MIT attribution/license and identify exact copied portions.

### C. Add client entrypoint, team quickstart and validator

**Create:** `.github/copilot-instructions.md`,
`docs/operations/agent-harness-quickstart.md`, `tools/README.md`,
`tools/harness/{README.md,validate.py}`.
**Modify:** root `README.md`, `docs/operations/README.md`,
`tests/integration/README.md` to expose usage/checks.

- [x] Add a thin Copilot bootstrap: root AGENTS, docs router, shared skills and
  essential no-private-data/no-implicit-provider-call boundary. No second source
  of algorithms, thresholds or product requirements. Duo uses root AGENTS.
- [x] Quickstart covers clone/pull, supported agent mode, client sign-in/admin
  prerequisites, new conversation after updates, skill invocation and checking
  references/discovery. Explain that GPT may process assistant context remotely;
  repository files do not install or configure a GPT endpoint.
- [x] State official Copilot surface/version dependence and Duo limits from the
  design. Include five example requests and a manual read-file fallback, clearly
  labelled. Do not claim unsupported GitLab web Chat loads skills.
- [x] Implement only scoped structural validation needed by section A. Check
  active routing/skills/new governance and directly relevant existing links;
  distinguish historical unavailable references. Never scan entire workspace.
- [x] Run GREEN: `python -m unittest tests.integration.test_harness
  tests.integration.test_architecture`; then `python tools/harness/validate.py`.

### D. Verify the real deliverable and hand it back

- [x] Lead inspects actual new/tracked diff and `git diff --check`, validates local
  links and checks Knowledge Transfer hash. Confirm no product/input/config drift.
- [x] Run validator and harness tests from a fresh tracked-file candidate snapshot
  without personal config, ignored data or temporary paths. Do not copy `.env`.
- [x] Run `python -m unittest discover -s tests` at the coherent boundary. Report
  exact counts, skips and failures, not a predetermined target count.
- [x] Record five read-only routing smoke cases before/after against the same
  expected owners/checks/permissions in a short evidence section of this plan.
  A smoke using the current assistant is not proof of native Copilot/Duo loading.
- [x] Where installed/signed-in clients are safely available, check native skill
  discovery in a fresh context without changing global settings or making new
  unapproved external calls. Otherwise document the exact unverified clients and
  give the owner a short in-client confirmation procedure; do not fake acceptance.
- [x] One final independent review justified by instruction/permission and
  validation-scope uncertainty; combine authority, privacy, compatibility and
  simplicity. Correct concrete defects with focused tests, not preference loops.
- [x] Final handoff links quickstart, gives first request and validator command,
  reports evidence/remaining gaps and changed files. No automatic commit/push.

## Execution choice and stop conditions

Recommended execution is **one implementer packet under lead ownership**, not a
separate worker for each milestone. Lead keeps a coherent diff and verifies real
outputs. A missing installed client blocks a tested-client claim, not completion
of repository files and honest self-service guidance. Stop for a product-runtime
change, new model/API integration, credential need, confidentiality exception or
requirement to change higher-level policy. After two similar failed repairs,
reassess the diagnosis before another attempt.

## Final verification evidence

- Approved direction was implemented as one writer packet with lead verification;
  root AGENTS, governance, five shared skills, thin Copilot bootstrap, quickstart
  and the stdlib validator are present. Reference structure was adapted to actual
  FileHolder ownership, not copied wholesale; no reference files were modified.
- Structural RED tests first failed because required files/metadata/validator
  were missing. Focused tests after the final correction: **39 OK** (29 harness
  plus ten architecture), and `python tools/harness/validate.py` exits 0.
- Full regression: `python -m unittest discover -s tests` → **830 tests OK,
  11 optional skips**. Baseline was 801/11. Same Python 3.11 environment used.
- A clean tracked-file candidate snapshot, overlaid only with allowed new/changed
  harness files, passes validator and the same 39 focused tests. No ignored
  documents, `.env`, saved workspaces or personal agent configuration copied.
- All 126 original source/test/config/Knowledge Transfer hashes remain identical.
  The declared Knowledge Transfer baseline SHA-256 is unchanged.
- Five batched read-only routing scenarios pass before and after. The control
  already complied, so no measured productivity or behavior improvement is claimed.
  Post-migration smoke explicitly used manual repository-file reads, not native
  Copilot/Duo discovery. The current OpenCode runtime could load the new skill;
  this is incidental and does not certify either team client.
- One independent broad review plus a focused re-review of a confirmed privacy
  correction. The lead caught a missed in-root symlink alias into synthetic
  workspace content; spy regressions were RED before a centralized safe-path fix.
  Direct files and directory aliases are now refused before content reads. Scoped
  re-review confirmed the correction. This is not a general security-sandbox claim.
- Native Copilot/Duo was **not exercised**. CLI tools were absent from PATH;
  VS Code.app exists, but no Copilot/GitLab extension directory was found in the
  standard checked locations. Other profiles/remote installations are unknown.
  Windows and live GPT/provider checks are unperformed, not silently accepted.
- Copilot's 15-line bootstrap is optional broader-surface compatibility, pointing
  to shared AGENTS/docs/skills; AGENTS alone can suffice on supported agent modes.
  No separate Copilot harness, duplicated rules, GPT SDK or credentials added.
- `git diff --check` passes. No product dependency, source, contract, private-data,
  provider call, model download, global configuration, commit or push change.

Local detailed evidence: `workspace/agent-harness-20261023/` (not product input
or a native-client certification). Team entrypoint:
[agent harness quickstart](../../operations/agent-harness-quickstart.md).
