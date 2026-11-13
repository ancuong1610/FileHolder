---
name: bbk-plan
description: Create or review a bounded implementation plan that records decisions, owners, verification and task-release boundaries before coding.
---

# Plan a bounded implementation

Use for a multi-step feature, bug fix, migration or high-uncertainty correction
when a written plan will guide later implementation. Use it to review an existing
plan as well. Do not use it for a question, inspection or clear micro-fix.

Read the shared [authority](../../../docs/governance/authority.md) and
[workflow](../../../docs/governance/workflows.md) rules first. A plan records
accepted intent; it cannot grant authority to change a contract, call a provider,
handle confidential data, commit, push or expand scope.

## Read

1. [Architecture](../../../docs/architecture.md) and
   [review guide](../../../docs/review-guide.md) for ownership and focused checks.
2. The named specification, issue, result or existing task plan. Treat historical
   plans as background evidence; inspect current owners before implementation.
3. [Plan artifact guidance](../../../docs/plans/README.md).
4. The smallest applicable contract and operation guidance for the proposed work.

## Create or review

1. State the goal, scope, non-goals, known facts, open questions and safety or
   confidentiality boundaries. Record OLD/NEW orientation when comparison data is
   involved.
2. List accepted decisions separately from assumptions and unknowns. Do not turn a
   proposed option into a decision.
3. Map expected files, owning modules, interfaces/contracts and the focused tests
   before assigning tasks. Preserve phase ownership and existing user work.
4. Decompose work into ordered tasks. Each task must name:
   - purpose and allowed files;
   - exact implementation/test actions and expected evidence;
   - acceptance criteria and stop conditions;
   - a release state (`NOT RELEASED`, `ACTIVE`, `BLOCKED`, `DONE`).
5. Include an execution ledger. The implementation agent executes only the task
   explicitly released by the user, updates its evidence, and stops before a later
   task unless the user releases it.
6. Specify verification proportionately: focused tests first, then the smallest
   affected boundary check. Never substitute compilation for behavior evidence.
7. Include documentation impact and handover expectations. Use the handover skill
   after implementation rather than embedding commit, push, deployment or
   subagent requirements in the plan.

Save a new plan as `docs/plans/YYYY-MM-DD-<short-kebab-case-topic>.md`. Keep plan
content concise enough to guide work, but concrete enough that an implementation
agent does not need to guess accepted design choices, owners, tests or boundaries.

## Required plan format

Use this structure as the baseline for every new plan. Adapt headings only when
they do not apply; do not omit the decision, release, verification or handoff
evidence that makes a plan executable.

````markdown
# <Feature or fix> — implementation plan

**Status:** DRAFT | READY FOR REVIEW | ACTIVE | BLOCKED | COMPLETE
**Prepared:** YYYY-MM-DD
**Evidence basis:** <inspected code/artifact, user-approved specification, or
explicitly reported information; name important unknowns>

## 1. Goal and scope

**Goal:** <one outcome-oriented sentence>

**In scope:**
- <bounded behavior or deliverable>

**Out of scope:**
- <explicit non-goal>

## 2. Accepted decisions and constraints

- <accepted decision and its rationale/source>
- <contract, phase-ownership, source-conservation, confidentiality or platform
  constraint>
- <assumption or unknown, clearly labelled as such>

## 3. File and interface map

| File / contract | Responsibility | Planned change | Focused check |
| --- | --- | --- | --- |
| `path/to/owner.py` | <owner> | <specific change> | `<exact command>` |

## 4. Ordered tasks

### T1 — <task name> [NOT RELEASED]

**Purpose:** <independently useful outcome>

**Allowed files:** `path/to/owner.py`, `tests/path/to/test.py`

**Interfaces:**
- Consumes: `<exact existing or earlier-task signature/contract>`
- Produces: `<exact new or changed signature/contract>`

**Steps:**

- [ ] Write or update `<test_name>` in `tests/path/to/test.py` to prove
  `<specific behavior and assertions>`.
- [ ] Run: `<focused test command>`.
  Expected before implementation: `<failure or existing result that demonstrates
  the missing behavior>`.
- [ ] Implement `<function_or_component(signature)>` in `path/to/owner.py` using
  `<specific algorithm, invariant or data flow only where the test/signature does
  not decide it>`.
- [ ] Run: `<focused test command>`.
  Expected after implementation: PASS.
- [ ] Run: `<smallest affected boundary check>`.

**Acceptance criteria:**
- <observable behavior, contract or conservation requirement>

**Stop conditions:**
- <decision, artifact, contract, provider, dependency or safety blocker>

**Task state:** NOT RELEASED | ACTIVE | BLOCKED | DONE

### T2 — <next task> [NOT RELEASED]

<Repeat the same structure. Depend on prior task interfaces explicitly rather
than duplicating their implementation details.>

## 5. Documentation and handoff

- Documentation impact: <owning documentation to update, or why none is expected>
- Final checks: `git diff --check`; <focused checks>; <smallest boundary check>
- Handoff must report changed files, exact commands/results, documentation impact,
  skipped/unverified cases and the next user-visible action.

## 6. Execution ledger

| Task | Release | Execution state | Evidence / next action |
| --- | --- | --- | --- |
| T1 | NOT RELEASED | NOT STARTED | Await explicit user release. |
| T2 | NOT RELEASED | NOT STARTED | Depends on T1. |
````

A task is right-sized when it has one independently testable deliverable. Use
exact file paths, signatures, test names and expected behavior when planning
evidence supports them. Do not copy complete implementation bodies into the
plan when tests and interfaces already determine the code. Do not add commit,
push, deployment, paid-provider, global-configuration or subagent instructions.

## Review checklist

When asked whether a plan aligns with this skill, report each item as **aligned**,
**gap**, or **needs decision**:

- The plan has a bounded goal, scope and non-goals.
- It separates observed facts, accepted decisions, assumptions and unknowns.
- Each task identifies allowed files, owner, verification and stop conditions.
- Task release states prevent the agent from silently advancing scope.
- Tests and acceptance evidence prove behavior rather than only syntax/compilation.
- Contracts, phase boundaries, source conservation and confidentiality rules are
  preserved.
- Documentation impact and final handover are addressed.
- It contains no credentials, private input, raw DI, internal endpoint or full
  private result text.
- It does not require automatic commits, pushes, deployments, paid/provider calls
  or unapproved dependencies.

## Stop and report

Stop for a missing product decision, unavailable artifact, private-data approval,
contract/migration change, provider call, new dependency or conflict with current
repository evidence. State the smallest decision or evidence needed. Do not edit
production code while only planning or reviewing a plan.
