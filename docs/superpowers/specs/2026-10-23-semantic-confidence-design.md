# Semantic confidence and residual-pair safety

Status: **Gate A implemented and evaluated; Gate B local shadow experiment
implemented and evaluated**. The owner approved Gate A on 2026-10-23 and subsequently instructed
completion of the explicitly remaining Gate B. Approval does not authorize production
model calls, new product dependencies, changing evaluator authority, committing,
or pushing. The lead owns the bounded execution plan and technical checks.

Gate A's new 48-case evaluation failed the overall recall gate (18/32=56.2%);
precision was 18/18 on that controlled sample, not a population guarantee. Inline
review visibility was subsequently corrected without changing engine decisions.
The Gate B execution plan is
`../plans/2026-10-23-semantic-confidence-gate-b.md`. The 72+48 now-exposed cases
are research/regression data, not a fresh final holdout for Gate B. A model's
shadow suggestions will not be reported as supported engine improvements.

Gate B's measured recommendation is **do not promote the candidate to production
authority**. The raw oracle-pair diagnostic reached 60/74 material detection but
only 60/67 material-claim precision; guarded equivalence still missed two actor
changes. Canonical supported metrics are unchanged. See
`../../operations/semantic-shadow-evaluation.md` for actual runs and limits.

## 1. Product intent and boundaries

The primary job is to detect meaningful old/new content changes and explain them
with trustworthy source correspondence. A lexical difference is not automatically
a meaning change; a high similarity score is not semantic proof. Return a complete
automatic result without an operator approval task. Preserve uncertainty rather
than manufacturing certainty or hiding unresolved evidence.

The evaluation targets remain supported event recall >=80% and supported-record
precision >=95%. They are measured release gates on declared datasets, not promised
outcomes or guarantees about arbitrary PDFs. Do not substitute candidate recall
for supported recall. Keep Docling as optional evaluation tooling, not the
production extractor replacement.

Retain 15 stages, external PDF paths, old/new conservation, raw text/locations,
one-way phase dependencies and saved-workspace compatibility. Source strikethrough,
Stage 10 scalability, OCR/model replacement, deployment and a new UI are out of
scope. Tables retain the existing matching policy in the first implementation.

## 2. Observed root causes

Baseline engine `ba4a2d0`, frozen 72-pair experiment, actual local Docling inputs:

- 46/48 positive events have the correct target pair/cell if uncertainty counts.
- Only 22/48 have correct **supported** pairs/cells; strict record precision is
  22/36. Five equivalent negatives produce ten supported delete/add records.
- Two real meaning changes produce four supported unpaired delete/add records;
  these are correspondence penalties, not claims that their meaning is unchanged.
- Actual extraction checks preserve the tested text, numeric values and tables;
  final native validations have no extraction-review failures.

Source and intermediate artifacts establish three distinct causes:

1. **Lexical retrieval/selection cannot establish equivalence.** Stage 07 uses
   `0.65 * char_ratio + 0.25 * token_f1 + 0.10 * length_ratio`, review floor 0.72,
   automatic score 0.86 and margin 0.10. NEG-004, NEG-020, POS-026 and POS-044
   score approximately 0.689, 0.447, 0.654 and 0.696; they never become paired
   candidates. Stage 09's lexical residual reconciliation does not rescue them.
2. **Unmatched is mistaken for safe acceptance.** Stage 11's one-sided branch
   accepts residual additions/deletions when no positive review flag exists.
   Stage 14 follows that route. Failure to find a lexical pair is not proof that
   an equivalent counterpart is absent.
3. **Correspondence ambiguity and meaning ambiguity are collapsed.** POS-018
   has a reciprocal pair at score 0.934 with a deletion of `NOT`; its shorter
   side is below 40 characters, so it remains review solely because of
   `short_text_similarity_pair`. Existing Stage 13 advice cannot approve it.

Relevant owners: `s07_modified_matching.py`, `matching/reconciliation.py`,
`s08_candidates.py`, `s09_movement.py`, `s11_routing.py`, `s12_packets.py`,
`s13_llm_evaluator.py`, `s14_canonical_result.py`. Reproductions and source quotes
are saved in `workspace/semantic-eval-20261022/REPORT.md` and its example records.

## 3. Approaches considered

| Approach | Benefit | Limit / risk |
| --- | --- | --- |
| Lower lexical thresholds / remove the short-text rule | Small code change | Confuses unrelated clauses, equivalent wording and actual changes. Rejected as the semantic solution. |
| Send every residual to an LLM and trust its verdict | Broad language coverage | Unbounded cost/data exposure, unsupported authority change, prompt injection and uncalibrated confidence. Rejected. |
| **Bounded hybrid, introduced in two gates** | Source guards and explicit abstention first; evaluate a maintained semantic model separately | Needs measured calibration and independently labeled data; cannot promise the targets before testing. Recommended. |

### Community-first evidence

- Sentence Transformers supplies maintained cross-encoder inference rather than
  requiring a custom model framework. The official model card for
  `cross-encoder/nli-deberta-v3-small` declares Apache-2.0, English SNLI/MultiNLI
  training, and contradiction/entailment/neutral scores. Its published SNLI/MNLI
  accuracy is **not** legal-version-comparison precision. It is a research
  candidate, not an adopted production dependency.
- SLeDoC demonstrates retrieval plus claim/NLI comparison, but its application
  expects a separately supplied model endpoint or external provider. It is design
  evidence, not a service to install or send documents to.
- RegTrack's legal-change taxonomy and ContractNLI's evidence/exception handling
  inform evaluation. Dataset availability, licenses and task mapping must be
  verified before reuse; no unperformed benchmark is claimed.
- Docling checks extraction/representation; it cannot supply semantic-change truth.

Sources inspected:
https://huggingface.co/cross-encoder/nli-deberta-v3-small
https://github.com/huggingface/sentence-transformers
https://github.com/s-nlp/SLeDoC
https://aclanthology.org/2026.acl-srw.68/

The custom gap is a small source-bound comparison policy and integration with
existing conservation/routing, not a replacement inference framework or extractor.

## 4. Recommended gate A: deterministic safety and separate evidence axes

This is the first bounded production implementation packet after spec/plan
approval. It requires no new model, provider call, or ML dependency.

### 4.1 Bound residual correspondence before claiming add/delete

Reuse Stage 07/09 retrieval and source contexts to record possible residual
counterparts, including candidates not strong enough to commit as matches.
Use element type, exact-match neighbours, supported section/anchor context and
reading-order evidence; structure remains a hypothesis, not a hard partition.
Absent/partial outline must retain fallback behaviour.

Keep retrieval bounded: initially at most five counterpart proposals per residual
element and 1,000 additional residual-pair checks per document pair, with
deterministic ordering/tie-breaking. These are safety budgets, not calibrated
semantic thresholds; test exhaustion and report work/coverage explicitly. Exhaustion and
unsearched regions are explicit coverage limitations, not proof of absence.
Do not introduce another all-pairs full-string comparison pass.

For a one-sided candidate with a plausible unresolved counterpart, Stage 11 emits
uncertainty, not independently supported deletion/addition. A proposal is not a
committed pair and must not consume either side. Preserve both original candidates
and refer to them from a single deduplicated uncertainty explanation.

Only the matching owner may commit a recovered pair after unique reciprocal
correspondence is supported and existing extraction/duplicate/conservation guards
pass. Stage 13 and Stage 14 must not rematch documents. Identical section titles,
similar wording or position alone cannot force a pair.

Genuine one-sided presence/absence evidence stays distinct from material meaning.
A truncated candidate search, missing extraction, unsupported relocation or
unresolved duplicate blocks a semantic-certainty claim.

### 4.2 Source-backed material delta, not global threshold relaxation

Keep short text conservative by default. Add a narrow proof-based path only when
the pair is already uniquely supported by independent correspondence evidence
and a complete source-aligned clause has a validated material delta.

Initial proof categories: explicit polarity/obligation changes and unambiguous
value/date/unit edits with unchanged qualifying context. Record exact old/new
spans, operators, values and source locations. Do not claim this handles arbitrary
actors, scope, conditions or exceptions without a validated proof.

Examples and negative controls must include:

- `SHALL NOT store` -> `SHALL store`, with unchanged subject/object/context;
- equal quantities in different formatting or units versus genuinely changed
  quantities; ambiguous locale, rounding, currencies or units abstain;
- equal values but changed `at least`/`at most` operators;
- negation inside quotations, exceptions and coordinated clauses;
- repeated short clauses, wrong sections, competing counterpart candidates;
- hidden qualifiers outside the selected clause.

A token `NOT`, a different digit, a model score or a 0.934 lexical score alone
is insufficient. If relevant context cannot be represented completely, abstain.
No synonym table tailored to the five exposed false alarms is acceptable.

### 4.3 Additive evidence contract

Use one Phase-1-owned normalization/validation helper for an optional
`semantic_assessment` attached to the existing candidate/resolution:

- schema version and policy version;
- `correspondence`: `verified`, `proposed`, `unresolved`, or `not_applicable`;
- `relation`: `material_change`, `equivalent`, or `undetermined`;
- `basis`: `deterministic_witness`, `model_advisory`, or `not_assessed`;
- old/new element or cell IDs, source text hashes and exact evidence spans;
- witness/guard outcomes and explicit limitations;
- model identity/mode only if an advisory model was actually executed.

Span validators check offsets, substrings, source hash, side identity and complete
required context. No calibrated-confidence percentage is invented. Proposal IDs
and links must not duplicate semantic records or alter the source ledger.

Stage 14 keeps final deterministic ownership. In gate A:

| Evidence | Semantic result |
| --- | --- |
| Verified pair + complete valid deterministic material witness + all guards pass | May be supported, with explicit basis |
| Unresolved correspondence, incomplete context, extraction problem or guard conflict | Uncertain |
| Proven existing layout/artifact rule | Existing non-semantic handling, with audit |
| Model advice, absent model, mock result or malformed/stale witness | Cannot approve or suppress |

Do not silently reinterpret historical `accepted`/`supported` as semantic proof.
Legacy records lacking assessment read as `not_assessed`; their saved statuses
are not migrated. New presentation explains source support separately from
semantic assessment. Existing reader filters, typed IDs and page/context bounds
remain valid. HTML/PDF and reader projections show uncertainty and its basis.

Gate A is allowed to improve safety through abstention. It must not be advertised
as solving general equivalence or meeting 80% recall without measurements.

## 5. Recommended gate B: evaluate a local semantic verifier in shadow mode

After separate approval for the public model download/resource use, try a pinned
maintained English NLI cross-encoder locally in an isolated environment. No document
text leaves the machine. Verify model/license/revision, safetensors availability,
tokenizer and dependency compatibility; do not enable `trust_remote_code` or
change company security policy. Initially cap shadow assessment at 200 clause
pairs per run; budget exhaustion remains visible and cannot change dispositions.
Stop at a concrete access/resource blocker.

Assess complete bounded clauses in both directions. Bidirectional entailment is
evidence of possible equivalence, contradiction of possible meaning change, and
neutral/one-way entailment may indicate scope expansion/contraction or missing
context. None is automatically an authoritative outcome.

Protected numbers, operators, units, dates, negation and exceptions are checked
against the actual source, with disagreements explicitly uncertain. Do not
silently truncate a decisive qualifier to fit a packet or model token window.
Existing Stage 12 packet caps are retained; oversized/incomplete semantic evidence
cannot produce a supported result. Existing head truncation is not a completeness
proof and must not be reused as one.

Gate B is **shadow/advisory only**: record what the model would conclude and its
errors/runtime, without promoting, suppressing or repairing canonical records.
Existing Stage 13 `REAL_CHANGE` / `ARTIFACT` / `UNCERTAIN` and mock behaviour are
not silently redefined as semantic truth. Reuse existing integration boundaries
where possible; do not add an HTTP service, custom model server or new workflow.

Giving model evidence resolution authority would require a later explicit owner
decision, new validated policy/contract and acceptance evidence. Keeping Stage 14
as a function that calls `accept(model_score)` would not preserve the current
advisory-only invariant. This draft does **not** authorize that change.

## 6. Evaluation, compatibility and acceptance

The exposed 72-pair corpus and its original holdout are now **development/regression
evidence** for the next design. Their original baseline remains valid historically,
but they cannot be sold as unseen final validation after designing against them.

Before implementation, freeze a new independent evaluation set and scoring
contract with no new-engine outputs available. Include naturally occurring version
pairs with independently reviewed annotations where permitted. Evaluation annotation
is a QA activity, not a mandatory operator approval step in the runtime pipeline.
Synthetic AI-authored labels alone do not prove legal/business accuracy.

Report separately:

- event recall for correctly paired supported material changes;
- precision per supported canonical record, including unmatched/extra records;
- candidate paired recall including uncertainty, abstention/coverage and false
  alarms on semantic-equivalent controls;
- unpaired real-change flags (do not imply they were completely invisible);
- error/timeouts, numeric/negation/context fidelity, latency and model work;
- sample sizes and descriptive confidence intervals by category/language/split.

Do not alter labels, omit failed cases or adjust thresholds on the new final set.
Rules/model operating points are chosen on separate development/calibration data
and locked before final scoring. Keep final-set labels out of implementation
worker prompts and model inputs; its independent author retains the sealed
manifest until the final run. A failed target stays a failed target.

Acceptance for gate A: reproduce current failures as regressions; no unsupported
equivalence/suppression; no duplicate old/new consumption; source-backed polarity
proof and its counterexamples tested; legacy saved results stay read-only and
bounded; all affected active docs updated. Semantic release readiness additionally
requires the measured >=80% / >=95% gates on the declared independent set, and
explicitly does not assert universal or legal certainty.

Relevant existing checks: `tests.phase1.test_matching_stage_module`,
`test_reconcile`, `test_movement_stage_module`, `test_route_identity`,
`test_generic_only`, `test_automatic_reports`, `tests.phase2.test_reader`,
`test_saved_workspace`, architecture/import checks, then full regression at the
coherent boundary. New semantic-evidence tests cover the contract/guards above.
Exact new test names and commands belong in the implementation plan, not a claim
that these unimplemented tests have passed.

## 7. Owner review and execution boundary

Requested approval: implement **gate A first**, preserving advisory-only model
authority, and prepare a separately approved local shadow experiment for gate B.
No immediate user document upload, credential change, paid API call or Docling
production integration is needed.

After this written design is approved, the lead writes the bounded implementation
plan and sends one coherent implementation packet for overlapping production files.
Review the final change once for source binding, persistence/legacy compatibility,
abstention and semantic suppression risks; verify actual diff and command outputs.
Do not commit, push, launch a service or deploy without separate authorization.
