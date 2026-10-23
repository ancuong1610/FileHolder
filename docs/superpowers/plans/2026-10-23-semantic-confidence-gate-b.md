# Gate B local semantic shadow evaluation

**Goal:** execute the remaining local NLI shadow experiment, measuring paraphrase
and material-change errors without changing production dispositions.

**Approved direction:** section 5 of
`../specs/2026-10-23-semantic-confidence-design.md`; the owner's latest
“làm nốt” follows the explicit Gate-A/Gate-B distinction. Treat that as direction
to perform the local experiment, not permission to give model scores authority.
No repeat product interview, commit, push, deployment or private-data upload.

**Route:** one bounded implementer packet for optional evaluation code/tests/docs;
lead owns public model/resource discovery, isolated runtime, actual model runs,
source freeze and final independent risk review. Existing worktree is isolated;
preserve all uncommitted Gate-A/inline work. No Docker or production dependency.

## Boundaries and selected evidence

- Use maintained Transformers directly (supported by the model card), not a
  custom inference server. Candidate: `cross-encoder/nli-deberta-v3-small`, pinned
  revision `fa2804872c3b4bd748f38c0185cc85775361e735`, Apache-2.0, English SNLI/MNLI.
  Public card declares contradiction/entailment/neutral in config; published NLI
  accuracy is not comparison acceptance. Verify safetensors and local hashes.
- Alternative sentence-transformers is maintained but adds an unnecessary wrapper
  for this experiment; remote/provider NLI is rejected for privacy and authority.
- Download only public code/weights in an isolated temporary venv/cache. Document
  clauses never leave the machine. Use no auth token, remote code or pickle model.
- Hard cap 200 complete clause pairs (up to 400 directional inferences). Full
  serialized evidence <=6,000 characters; full tokenization must fit the actual
  model window in both directions. Never head-truncate qualifiers. Every skipped,
  failed, oversized or unsearched row stays explicit in the output denominator.
- Protected numeric/operator/unit/date/negation/modal/exception differences block
  an equivalence suggestion; no unsupported confidence percentages. All suggestions
  remain advisory, including contradiction. No stage-13/14/index/reader mutation.
- Existing 72+48 corpora are exposed development/research evidence. Freeze the
  model/policy before running additional independently authored tests; do not call
  their small AI-authored labels legal ground truth or claim an untouched holdout
  if source/model choice was influenced by its labels.

## Implementation and exact checks

- [x] Add optional `evaluation/semantic_shadow.py` (pure guard/result policy and
  bounded runner), `semantic_shadow_model.py` (lazy local-only Transformers
  safetensors backend) and `semantic_shadow_cli.py` (explicit fresh JSON output,
  pair input, no service). Keep optional ML imports outside runtime/import path.
- [x] RED/GREEN tests in `tests/phase1/test_semantic_shadow.py`: both directions,
  200 cap, no truncation, 6,000-char bound, protected-context disagreement,
  bad scores/labels, failure accounting, local-only loading, immutable inputs,
  output refusal and optional dependency absence. Fake scores test policy only;
  do not report them as real inference evidence.
- [x] Update evaluation README plus root/architecture notes accurately: optional
  experiment, no authority or semantic readiness claim.
- [x] Lead runs focused tests, inspects actual diff, then full regression with
  the existing Gate-A Python. Independent final review for privacy, local-only
  behavior, bounds/abstention, output persistence and source correspondence.
- [x] Lead records isolated package/model identities, downloads public snapshot,
  runs actual CPU inference on complete clauses, saves input/output hashes,
  runtime, coverage and errors. Report both directional raw scores without
  treating softmax as calibrated semantic certainty. Do not modify scored gold.
- [x] Final report distinguishes shadow semantic suggestions from unchanged
  canonical supported recall/precision. If the model misses numeric/negation or
  paraphrase changes, report failure and recommendation, not automatic promotion.

**Stop conditions:** unavailable public model/safetensors, insufficient disk/RAM,
company security restriction, a need to upload private data, or any need to alter
canonical authority. Retry access at most once; report concrete blockers. No
unbounded model search/tuning or claim that a finite benchmark completes general
semantic understanding.

## Execution evidence

Public snapshot download and offline CPU sanity inference succeeded. The model
directory contains only the pinned safe weights/tokenizer/config/card assets;
total 578,732,649 bytes. Runtime uses a separate Python 3.11 environment with
torch 2.9.0 / transformers 4.57.1 / sentencepiece 0.2.1; no production dependency
changed. An initial system-Python-3.9 resolution failure was diagnosed and a
separate correct venv used, without weakening any security policy.

Lead research artifacts: `workspace/semantic-gateB-20261023/`. Input freeze has
114 oracle target pairs (six one-sided cases explicitly excluded) plus 88 actual
canonical/proposal pairs selected without gold. Labels remain separate. Both
lanes are exposed research data; no new holdout claim. All 120 canonical results
and engine files are hash-frozen for the read-only boundary check.

Raw offline model diagnostic finished on all 114 oracle pairs (74 material,
40 equivalent): 60 material contradictions, 7 missed-material entailments,
7 material neutral/asymmetric, 33 correct equivalent entailments and 7 false
contradictions. The naive contradiction rule gives 60/74 recall and 60/67
precision, so the selected model alone **fails the 95% precision target**.
These are exposed, oracle-correspondence research measurements, not improved
engine supported metrics. Keep this failure visible; do not retune the policy
on its case IDs. Full evidence is in the experiment's raw-model files.

Final checks: 37 focused tests (27 shadow + ten architecture); full **801 tests
OK, 11 optional skips**. One independent review reported no actionable code
defect in inspected scope; the lead corrected its overly broad hash-only/privacy
description in documentation. Output JSON is source-derived, not anonymized.

Real guarded CLI runs: 114 oracle pairs / 228 directional calls (5.53s including
load), 88 actual candidates / 176 calls (2.87s), and ten boundary cases with no
semantic assertion. Guarded oracle suggestions retain seven false material calls
and two false-equivalence actor changes. Actual candidates yield 61 possible
material suggestions and 27 undetermined, not 61 proven changes.

A further real-model cap check retains all 202 input rows: a 1,216-token pair
is skipped whole, 200 pairs receive exactly 400 calls, and the last pair remains
budget-exhausted even when `--max-pairs 1000` is requested. All 120 canonical
result hashes, engine sources and production requirements stayed unchanged.

Final recommendation: **Gate B experiment complete; candidate not approved for
production authority and overall semantic release target still unmet.** No
commit/push. Full persistent summary:
`../../operations/semantic-shadow-evaluation.md`.
