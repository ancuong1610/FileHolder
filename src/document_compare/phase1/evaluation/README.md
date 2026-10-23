# Phase 1 evaluation (optional)

Offline benchmarks, diagnostics, evidence-review tooling and the optional Gate B
semantic shadow experiment. Optional: off the runtime pipeline path and never
imported by Stage 14 or other runtime code.

Read first: `evaluate.py` (scoring + `eval` command), `matching_diagnostics.py`.

Input/output: reads saved pipeline output and writes a separate report/bundle;
never rewrites the input artifacts.

Dependencies: `phase1/contracts/outcomes.py`, `phase1/matching`; invoked via
`python -m document_compare eval|eval-gt|benchmark-performance|review-evidence`.

Check: `python -m unittest discover -s tests/phase1 -p 'test_*evaluation*'`.

## Gate B semantic shadow (optional, advisory)

`semantic_shadow.py`, `semantic_shadow_model.py` and `semantic_shadow_cli.py`
implement a bounded local NLI shadow experiment. They are not a production
semantic verifier and add no runtime/package dependency. Importing the policy or
CLI does not import `torch`/`transformers`; the real backend imports them lazily
only when instantiated with an explicit local model directory.

```bash
python -m document_compare.phase1.evaluation.semantic_shadow_cli \
  --pairs path/to/pairs.json \
  --out path/to/new-output-directory \
  --model-dir /explicit/local/model/snapshot \
  --max-pairs 200
```

The selected research candidate is `cross-encoder/nli-deberta-v3-small` at pinned
revision `fa2804872c3b4bd748f38c0185cc85775361e735` (Apache-2.0, English
SNLI/MNLI). The backend calls `from_pretrained` only on that local directory with
`local_files_only=True`, `trust_remote_code=False` and (for the model)
`use_safetensors=True`. It never calls an HF download helper, accepts a remote
model ID or starts a service. Labels are read from the model config, not assumed
by index; anything other than exactly `contradiction`/`entailment`/`neutral`
fails closed. Inference is CPU/eval mode under `torch.inference_mode()`.

### Input

```json
{"pairs": [{
  "pair_id": "P1",
  "old": {"element_id": "O1", "text": "complete old clause"},
  "new": {"element_id": "N1", "text": "complete new clause"},
  "correspondence": "verified",
  "language": "en",
  "context_complete": true
}]}
```

`pair_id` must be unique, and old/new text must be non-empty. Supplied hashes are
ignored; output hashes are recomputed from the actual text. `context_complete`
must be supplied as `true`; it is never inferred from a truncation or head cap.

### Bounds and policy

- At most 200 pairs and 400 directional inferences per run; `--max-pairs` cannot
  raise the hard cap.
- The canonical source envelope must serialize to at most 6,000 Unicode
  characters. The actual model window is
  `min(tokenizer.model_max_length, config.max_position_embeddings, 512)`, and both
  pair directions are counted with special tokens and `truncation=False`. An
  over-size text or over-window pair is skipped whole; nothing is head-sliced and
  the model is not called.
- Every input row appears in `shadow_results.json` as assessed/skipped/error, with
  explicit reasons such as `unsupported_language`, `missing_context`,
  `source_too_large`, `token_window_exceeded`, `budget_exhausted`,
  `inference_error` or `invalid_model_scores`.
- Directional softmax scores are reported as `model_scores`, not confidence or
  accuracy percentages. Each direction must contain exactly the three labels as
  finite probabilities in `[0, 1]` summing to `1` within `1e-6`. Raw logits, a
  boolean, a missing/extra label or a non-normalized mapping is an error row; the
  runner does not guess or renormalize it. The backend contract is explicit
  softmax output. Bidirectional entailment suggests `possible_equivalent` only
  for a verified, complete English pair with no protected-fact difference. Any
  contradiction suggests `possible_material_change` advisory only; neutral,
  mixed or asymmetric directions are `undetermined`. Proposed/unresolved
  correspondence is never a pair assertion.
- Protected literal guards cover signed numbers, threshold operators, units,
  dates, negation, modal force, conditional/exception terms and scope qualifiers.
  Values retain order and multiplicity, and each fact carries a short local
  binding context. A changed sign, duplicate negation, swapped values/units or
  reassigned subject/qualifier context blocks equivalence; the guard is
  deliberately conservative and is not a general semantic parser. Quoted or
  incomplete fragments cannot produce a verified-equivalence claim. A paraphrase
  with unchanged protected fact context can still be suggested as equivalent.
- Every row carries `basis=model_advisory` and `authority=false`. No final status,
  canonical record, element ID, Stage 13/14 disposition or reader projection is
  changed or suppressed. Output has a stable `shadow_id`, source hashes, limits
  and provenance fields. `model_id`/`revision` are declared candidate
  expectations; `revision_verification` remains explicit (normally
  `pinned_declaration_not_file_hash`) and `artifact_sha256`/`artifact_file_count`/
  `artifact_bytes` fingerprint the actual local model files. The artifact hash is
  retained in the stable ID and cannot be silently dropped by caller metadata.

### Output

`--out` must be a fresh directory; an existing path is refused before model load.
The directory claim is race-safe (`exist_ok=False`). The CLI writes
`shadow_results.json` and a concise `shadow_summary.md`. It reads the input but
never writes it and does not modify a saved workspace. Permission/IO failures
become safe error codes without raw paths or tracebacks; partial-failure cleanup
unlinks only this invocation's two output filenames and never removes the
directory or foreign entries.

Treat `shadow_results.json` as source-derived local evidence: it retains side
element IDs and protected-fact/context fragments as well as hashes. It is **not**
an anonymized export. Safe stderr/summary codes do not make the full JSON safe
to upload when inputs contain private material. This experiment used only the
controlled public-source-derived/synthetic research inputs.

Actual 2026-10-23 CPU results and the failed model-readiness conclusion are in
[the Gate B evaluation report](../../../../docs/operations/semantic-shadow-evaluation.md).

### Tests and evidence limits

`tests/phase1/test_semantic_shadow.py` uses a dependency-free fake backend. Those
tests prove policy, bounds, failure accounting and local-only loading contracts;
they are **not** real NLI accuracy evidence. The exposed 72-pair corpus and
48-pair holdout are development/regression evidence now. A shadow run does not
establish an untouched holdout, semantic release readiness, >=80% supported
recall, >=95% supported precision, legal truth or calibrated confidence. Failed
or missing rows stay visible in the denominator.
