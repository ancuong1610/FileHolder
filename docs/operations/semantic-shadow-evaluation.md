# Gate B: local semantic shadow evaluation (2026-10-23)

## Decision and scope

The requested Gate B experiment is implemented as optional evaluation tooling,
not a production semantic adjudicator. It uses a maintained local Transformers
NLI model, keeps full source boundaries and explicitly abstains on unsafe inputs.
No model output accepts, suppresses or consumes a canonical change.

**Measured conclusion: do not promote this candidate to production authority.**
It still produces both false material-change suggestions and false equivalence
suggestions. The overall semantic release goal remains unmet; completing the
experiment is not completing general semantic understanding.

Owners: `phase1/evaluation/semantic_shadow.py`, `semantic_shadow_model.py`,
`semantic_shadow_cli.py`; usage and input schema are in
[the evaluation README](../../src/document_compare/phase1/evaluation/README.md).
The [execution plan](../superpowers/plans/2026-10-23-semantic-confidence-gate-b.md)
records constraints and checks.

## Actual inference, not mock results

Model: `cross-encoder/nli-deberta-v3-small`, Apache-2.0 declaration, immutable
revision `fa2804872c3b4bd748f38c0185cc85775361e735`. Public safetensors/tokenizer/
config/card downloads were frozen with file hashes. All inference ran locally
on CPU, offline, without remote code, pickle weights, private uploads or a
provider endpoint. Package/model downloads are not private-document inference.

The temporary Python 3.11 experiment uses torch 2.9.0, transformers 4.57.1 and
sentencepiece 0.2.1; these are not production requirements. Direct Transformers
is supported by the publisher, avoiding another wrapper/server. NLI training
on English SNLI/MNLI is not document/legal comparison certification.

### Oracle-target diagnostic: 114 pairs

Both exposed controlled corpora were reused: 72 older cases plus the subsequently
exposed 48-case set. Six true one-sided cases have no two-sided NLI input and are
explicitly excluded from this **pair diagnostic**, not silently scored as correct.
Gold was stored separately and was not included in model inputs. Supplied oracle
correspondence makes this an upper-bound language diagnostic, not engine recall.

| Gold (count) | Suggested material | Suggested equivalent | Undetermined |
| --- | ---: | ---: | ---: |
| Material (74), raw bidirectional model | 60 | 7 | 7 |
| Equivalent (40), raw bidirectional model | 7 | 33 | 0 |
| Material (74), guarded shadow policy | 60 | 2 | 12 |
| Equivalent (40), guarded shadow policy | 7 | 18 | 15 |

The naive contradiction rule gives 60/74=81.1% diagnostic material recall and
60/67=89.6% diagnostic material-claim precision: **below 95%**. Protected guards
reduce unsafe equivalence suggestions but also abstain more. Two actor changes
are still suggested equivalent. Equivalence suggestions are correct on only
18/20 pairs in this diagnostic, so they cannot safely suppress production diffs.
No operating point or model was retuned on these scored case IDs.

The guarded CLI completed 114/114 pairs, 228 directional inferences, zero errors
or truncations, about 5.53s including model load on this machine. Softmax scores
are raw model outputs, not calibrated certainty percentages.

### Actual pipeline candidates: 88 pairs

An additional lane selected 78 saved canonical text pairs and ten bounded,
uncommitted residual proposals **without gold-guided selection**. Correspondence
stayed verified/proposed/unresolved as supplied by actual evidence; a candidate
was not promoted merely because the model liked its wording.

The CLI completed 88/88 pairs, 176 directional inferences, zero errors, about
2.87s including model load. It suggested 61 possible material changes and left
27 undetermined. None of these suggestions altered production results. Counts
of suggestions are not claims that all 61 are semantically correct.

### Real-model safety boundaries

- Ten explicit boundary cases retain all rows: missing context, unsupported
  languages, over-size evidence and unverified correspondence yield no semantic
  assertion. This is a contract challenge, not expert-labeled semantic gold.
- Another actual-model run requested 1,000 pairs over 202 rows. The limit was
  clamped to 200: exactly 400 directional inferences, one whole 1,216-token pair
  skipped before inference, and one budget-exhausted row retained explicitly.
- All 120 saved canonical results, engine files and production requirements
  remained hash-identical before/after the shadow runs.

## Verification and evidence

The focused unit tests use fake scores to prove policy separately from actual
inference. RED/GREEN counterexamples cover malformed/logit scores, signed values,
swapped fact bindings, provenance preservation and safe output failures.
Full regression after the implementation: **801 tests OK, 11 optional skips**.
Focused verification is 27 shadow tests plus ten architecture tests (37 total).
One independent final code review reported no actionable defect in the inspected
shadow feature; it ran the 27 shadow tests. Its summary incorrectly described
output as hash-only: the lead verified that JSON deliberately retains source IDs
and protected-context fragments, and documented that it is **not anonymized**.
No private-data upload or blanket privacy/semantic-correctness guarantee follows
from this review. Error codes/Markdown summary are separate from full evidence.

Research artifacts (local, not packaged product data):
`workspace/semantic-gateB-20261023/` contains `EXPERIMENT.md`, package/model/source
freezes, inputs separate from gold, raw directional scores, guarded CLI reports,
actual timing/budget records, and `final-shadow-analysis.json`.

The original Gate A/inline changes remain uncommitted alongside this optional
slice; no commit, push, production model enablement or provider call occurred.

## Limits and next decision

The samples are small, exposed and AI-authored, not independent legal-expert
annotations of naturally occurring versions. Context-complete input flags do not
prove that the source snippet captured every whole-document qualification.
Protected guards are conservative lexical/context checks, not an actor/scope
reasoning engine. Only English is supported in this experiment.

Production supported metrics remain Gate A's measured results, including 56.2%
recall on the previous 48-case evaluation. Shadow suggestions do not raise that
number. A future production semantic policy needs a better validated verifier,
independent natural-document labels and explicit authorization to change model
authority. Do not lower thresholds, hide uncertainty or turn model entailment
into automatic suppression to manufacture a passing score.
