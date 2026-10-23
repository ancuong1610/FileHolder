# Authority and evidence

The project maintainer owns product intent, scope and authority changes. The
technical lead owns execution, verification and reporting. A specialist or agent
has only the scope explicitly delegated for the current task; a successful test
or a model suggestion is not product approval.

## Evidence classes

Keep these separate in a report:

- **Observed** — code, a saved artifact, command output or a reproduced failure.
- **Contract** — an accepted interface, stage ownership rule or schema.
- **Decision** — a consequential choice approved by the owner and recorded in
  [decisions](decisions/README.md).
- **Hypothesis** — a plausible explanation not yet confirmed by evidence.
- **Unknown** — material context the request did not supply.

Conflicts between code, accepted contracts and prose are recorded and
investigated. Do not declare one source universally true or silently rewrite a
historical record to fit the current code. Historical specs, plans and releases
are background evidence, not the latest runtime specification.

## Product and algorithm boundaries

- Keep raw extraction text, IDs, locations and OLD/NEW conservation intact.
- Stage 13 recommendations are advisory; Stage 14 deterministic rules own final
  resolution. Uncertainty is not an operator task and review items stay visible.
- Supports text does not prove semantic truth. Synthetic regression tests do not
  establish company-PDF, Windows, legal or universal accuracy.
- Gate B and local NLI are optional shadow/evaluation evidence. Docling is
  evaluation-only research. Scores never change canonical status.
- The bounded reader owns filtering, pagination and context limits. Do not load
  full reports or private prompts into an agent prompt.
- Preserve `CRR_` environment keys and existing artifact, HTTP and MCP contracts.

## Permission boundaries

`.env`, `input/*`, cached DI and `workspace/` may contain confidential material.
Never commit, upload, paste or log credentials, internal endpoints, documents, raw
DI, model traces or private result text outside the approved environment.

Copilot or GitLab Duo with GPT may process assistant context remotely under the
organization's policy; that is not local inference. State the outbound-data
consequence and obtain approval before a real provider call. No agent may add a
provider/SDK, choose a paid model, change credentials, expose a service, deploy,
publish or commit/push merely because it can run tools.
