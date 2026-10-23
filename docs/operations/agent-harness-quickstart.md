# Agent harness team quickstart

Use this repository's coding-agent harness with a supported GitHub Copilot or
GitLab Duo client. It is the development harness, not the Phase 2 result-reading
agent. The repository does not install a client, sign you in, choose a GPT model
or configure a provider endpoint.

## Prerequisites

- Python >=3.10 (3.11 recommended) and the project virtual environment installed
  as described in the [README](../../README.md). Use that same interpreter for
  the validator, tests and subprocesses.
- A supported coding-agent client, installed and signed in under your
  organization's plan/admin policy. Installation and sign-in are prerequisites,
  not repository features.
- GitHub Copilot: an agent-enabled surface that supports repository custom
  instructions and project skills. Exact IDE/version support follows the current
  Copilot feature matrix and may differ by surface.
- GitLab Duo Agent Platform: local project skills require a supported setup.
  Current documentation lists GitLab for VS Code >=6.71.4 or Duo CLI >=8.73.0.
  Root `AGENTS.md` support alone does not imply native skill support.
- GPT availability and model selection are chosen through the client/organization
  where offered. The repository makes no model-selection promise.

## Open and verify the repository

Clone or pull the repository with your normal Git workflow, open the repository
root, and run:

```bash
python tools/harness/validate.py
```

Run it from the repository root with the same virtual environment on macOS,
Linux or Windows. Exit 0 means the declared harness files and links are
structurally valid; exit 1 prints findings; exit 2 means the root is invalid or
unavailable. The validator is read-only and does not scan `workspace/`, `input/`,
`.env`, model caches or virtual environments.

## Start a fresh conversation

Agent instructions and skills are discovered from repository files. After pulling
instruction or skill changes, start a **new conversation**; an old conversation
may keep stale context.

- **Copilot:** `.github/copilot-instructions.md` is the thin repository bootstrap.
  It points to root [AGENTS.md](../../AGENTS.md), the [docs router](../README.md)
  and the [skill index](../../.agents/skills/README.md). Skills are shared under
  `.agents/skills/`.
- **GitLab Duo:** the Agent Platform reads root [AGENTS.md](../../AGENTS.md)
  directly. Project skills require the supported version/surface above. GitLab
  web Chat does not support native project skills, and Code Review Flow is
  excluded. No custom flow integration is added.

Native discovery is not the same as manually reading a file. If your client does
not list or load project skills, see the fallback below.

## First request

Paste this into the client:

```text
Use fileholder-read-results in read-only mode. Explain how to inspect the saved
comparison in workspace/agent demo: confirm the OLD/NEW orientation, use the
bounded summarize/search/details reader calls, and cite typed IDs and pages. Do
not rerun the engine, modify the workspace, or call a provider. If that workspace
is absent, ask me for it instead of guessing.
```

Expected route: `fileholder-read-results`, the reader contract and phase testing.
No code change, provider call or workspace mutation is expected.

## Five workflow examples

| Goal | Example request | Expected route |
| --- | --- | --- |
| Investigate a Stage 5 defect | `Use fileholder-investigate for a Stage 5 heading/furniture defect. Name the owning stage, the smallest saved evidence you need, and the focused test. Do not edit yet.` | Stage 5/structure owner, reproduction and producer trace before repair |
| Make a bounded change | `Use fileholder-change for a wording-only heading fix in the inline PDF report. Read the review guide and report owner, add the smallest test, and do not run the full suite or commit.` | Report owner and focused report tests |
| Evaluate evidence | `Use fileholder-evaluate. Explain what a shadow NLI score can and cannot prove for canonical status, and list the dataset/OLD-NEW facts you need before any evaluation.` | Stage 13 advisory/Stage 14 authority; no 80%/95% readiness claim |
| Read a saved result | `Use fileholder-read-results to summarize the saved workspace I name. Cite change/review IDs and pages, keep unresolved items unresolved, and do not rerun or overwrite it.` | Bounded reader, typed IDs, read-only workspace |
| Hand off a change | `Use fileholder-handover. Inspect the real diff, run the focused checks, report documentation impact and skipped cases, and do not commit or push.` | Actual diff, exact checks, explicit gaps |

## Manual fallback (not native discovery)

If a client does not support project skills on your surface/version, explicitly
attach or read the relevant `.agents/skills/<fileholder-id>/SKILL.md` as Markdown
in the conversation. This is a **manual read-file fallback**, not native skill
discovery. Tell the user which fallback you used. Do not copy the skill into a
client-specific folder.

## Data and provider safety

Copilot or Duo with GPT may process assistant context remotely under the
organization's policy. That is not local/offline NLI. Do not attach private PDFs,
raw DI, result text, credentials or internal endpoints by default. The optional
local NLI experiment is separate and is not installed by this harness.

Do not add an OpenAI SDK or API key, choose a paid model, change global client
configuration or make a provider call without explicit approval. Do not commit,
push, deploy or publish as part of using a skill.

## Troubleshooting

- **Skill not listed/loaded:** confirm the client surface and minimum version,
  restart the client, start a fresh conversation, and confirm the skill path exists
  under `.agents/skills/`. Copilot surface support is version-dependent; an older
  Duo version may read root AGENTS but not project skills. Use the manual fallback
  and report the unverified version.
- **Validator fails:** fix the named repository-relative path/link or metadata
  finding; do not weaken the check to hide a missing owner.
- **Wrong repository context:** open the repository root and confirm the client is
  using this checkout, not a different worktree or global setup.

## Official references

- <https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>
- <https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions>
- <https://docs.gitlab.com/user/duo_agent_platform/customize/agents_md/>
- <https://docs.gitlab.com/user/duo_agent_platform/customize/agent_skills/>

## Tested coverage

Repository files, links and the structural validator are checked in this
checkout. Native signed-in Copilot/GitLab Duo loading was **not exercised** here.
The `copilot`, `glab`, `duo` and `code` commands were not found on `PATH` in this
environment. `/Applications/Visual Studio Code.app` does exist, but no standard
Copilot/GitLab extension directory was found; other profiles and extensions are
unknown. Static tests are not real client compatibility checks. Perform one
fresh-conversation skill-discovery check in your installed client and report the
client name, surface and version. File layout alone is not native-client
acceptance.
