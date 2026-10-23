"""Gate B semantic shadow policy and bounded runner (advisory only).

This module is deliberately dependency-free.  It never imports a model runtime,
opens a network connection, changes a canonical status or consumes an old/new
element ID.  It turns a small backend's two directional NLI outputs into an
advisory shadow record and keeps every input row visible in the denominator.

Backend interface (implemented by ``semantic_shadow_model`` and by test fakes):

    backend.max_tokens: int
    backend.count_pair_tokens(first: str, second: str) -> int
    backend.predict(first: str, second: str) -> Mapping[str, float]
    backend.metadata: Mapping[str, Any]

``predict`` must return exactly the three canonical labels
(``contradiction``/``entailment``/``neutral``) as finite softmax probabilities
in ``[0, 1]`` summing to one.  The runner rejects logits, missing labels, extra
labels, booleans, out-of-range values and non-normalized mappings; a malformed
score is an error row, never a verdict.  It never guesses or renormalizes raw
logits.  A softmax score is reported as a model score, not as calibrated
confidence or accuracy.

Directional policy:

* both unique top labels are ``entailment`` -> ``possible_equivalent`` only
  for a verified, complete English source with no protected fact disagreement;
* any unique top ``contradiction`` -> ``possible_material_change`` advisory;
* one-way entailment -> ``undetermined`` (scope expansion/contraction);
* neutral/mixed/ambiguous -> ``undetermined``.

Every assessed row is ``basis=model_advisory`` and ``authority=false``.  The
runner does not emit a canonical final status and cannot suppress or approve a
change in Stages 13/14.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "semantic-shadow-1.0.0"
POLICY_VERSION = "gateB-1"

MAX_PAIRS = 200
MAX_DIRECTIONAL_INFERENCES = MAX_PAIRS * 2
MAX_SOURCE_CHARACTERS = 6000
MAX_MODEL_TOKENS = 512

CANONICAL_LABELS = ("contradiction", "entailment", "neutral")
CORRESPONDENCE_STATES = ("verified", "proposed", "unresolved")

# A model score is not a calibrated semantic probability.  Keep this wording in
# one place so no consumer can turn it into an "accuracy" percentage by accident.
SCORE_SEMANTICS = "model_softmax_scores_not_calibrated_confidence"
SCORE_SUM_TOLERANCE = 1e-6


class ShadowInputError(ValueError):
    """Safe, clause-free input error.  ``code`` is the only public message."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --- Protected source signature -------------------------------------------------
# Closed token/grammar classes, not a benchmark synonym table.  A model may
# suggest equivalence only when these literal guards agree.  The point is to
# abstain on a changed number/operator/unit/date/polarity/modal/condition/scope,
# never to prove two different phrasings mean the same thing.
_NUMBER_RE = re.compile(
    r"(?<![\w.])[+\-\u2212]?\d+(?:[.,]\d+)*(?!\w|\.\d)"
)
_OPERATORS = (
    "no less than",
    "no more than",
    "greater than",
    "fewer than",
    "less than",
    "more than",
    "at least",
    "at most",
    ">=",
    "<=",
    ">",
    "<",
)
_OPERATOR_RE = re.compile(
    "|".join(re.escape(item) for item in sorted(_OPERATORS, key=len, reverse=True)),
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"(%|[$€£¥]|\b(?:usd|eur|gbp|jpy|vnd|"
    r"business\s+days?|calendar\s+days?|days?|hours?|minutes?|seconds?|"
    r"weeks?|months?|years?|percent|percentage\s+points?|"
    r"kg|mg|km|cm|mm|gb|mb|kb|tb|ms)\b)",
    re.IGNORECASE,
)
_DATE_NUMERIC_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
)
# Capitalized month names avoid treating the modal "may" as a date while still
# guarding an explicit month/date edit.  This is a literal guard, not a synonym.
_MONTH_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\b"
)
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|none|neither|nor|without|cannot)\b|\b\w+n['’]t\b",
    re.IGNORECASE,
)
_MODAL_RE = re.compile(
    r"\b(must|shall|required|may|can|should|will|might)\b", re.IGNORECASE
)
_MODAL_FORCE = {
    "must": "mandatory",
    "shall": "mandatory",
    "required": "mandatory",
    "may": "permissive",
    "can": "permissive",
    "should": "recommended",
    "will": "declarative",
    "might": "possible",
}
_CONDITIONAL_RE = re.compile(
    r"\b(?:if|unless|except|provided|subject\s+to|other\s+than|notwithstanding|"
    r"excluding|in\s+the\s+event)\b",
    re.IGNORECASE,
)
_SCOPE_RE = re.compile(
    r"\b(?:all|any|each|every|some|only|solely|exclusively|within|outside)\b",
    re.IGNORECASE,
)
_QUOTE_CHARACTERS = frozenset("\"'`\u2018\u2019\u201c\u201d")
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_CONTEXT_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
_FACT_CONTEXT_CHARACTERS = 80
_FRAGMENT_END_RE = re.compile(r"[,;:]$")
_DANGLING_END_RE = re.compile(
    r"\b(?:and|or|but|if|unless|of|to|for|with|by|under|shall|must|may|not|never)"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_PROTECTED_KEYS = (
    "numbers",
    "operators",
    "units",
    "dates",
    "negation",
    "modal_force",
    "conditional_exception",
    "scope_qualifiers",
    "fact_bindings",
    "quoted",
)


def _context_tokens(value: str) -> str:
    """Normalized short local context; modal spellings map to their force class."""
    tokens: list[str] = []
    for token in _CONTEXT_TOKEN_RE.findall(value.casefold()):
        force = _MODAL_FORCE.get(token)
        tokens.append(f"modal:{force}" if force else token)
    return " ".join(tokens)


def _protected_facts(text: str) -> list[dict[str, Any]]:
    """Ordered protected facts with short local binding contexts.

    This is a literal guard, not a parser.  Context equality is intentionally
    conservative: if a sign, count, order or nearby subject/qualifier binding
    changes, equivalence is not suggested.  Normalization only maps modal
    spellings to the same closed force class and ignores punctuation/whitespace.
    """
    value = text if isinstance(text, str) else ""
    facts: list[dict[str, Any]] = []

    def add(kind: str, atom: str, start: int, end: int) -> None:
        left = value[max(0, start - _FACT_CONTEXT_CHARACTERS) : start]
        right = value[end : end + _FACT_CONTEXT_CHARACTERS]
        facts.append(
            {
                "kind": kind,
                "value": atom,
                "left": _context_tokens(left),
                "right": _context_tokens(right),
                "start": start,
            }
        )

    for match in _NUMBER_RE.finditer(value):
        add("numbers", match.group(0), match.start(), match.end())
    for match in _OPERATOR_RE.finditer(value):
        add(
            "operators",
            " ".join(match.group(0).casefold().split()),
            match.start(),
            match.end(),
        )
    for match in _UNIT_RE.finditer(value):
        add(
            "units",
            " ".join(match.group(0).casefold().split()),
            match.start(),
            match.end(),
        )
    for pattern in (_DATE_NUMERIC_RE, _MONTH_RE):
        for match in pattern.finditer(value):
            add(
                "dates",
                " ".join(match.group(0).casefold().split()),
                match.start(),
                match.end(),
            )
    for match in _NEGATION_RE.finditer(value):
        add(
            "negation",
            " ".join(match.group(0).casefold().split()),
            match.start(),
            match.end(),
        )
    for match in _MODAL_RE.finditer(value):
        add(
            "modal_force",
            _MODAL_FORCE[match.group(1).casefold()],
            match.start(),
            match.end(),
        )
    for match in _CONDITIONAL_RE.finditer(value):
        add(
            "conditional_exception",
            " ".join(match.group(0).casefold().split()),
            match.start(),
            match.end(),
        )
    for match in _SCOPE_RE.finditer(value):
        add(
            "scope_qualifiers",
            " ".join(match.group(0).casefold().split()),
            match.start(),
            match.end(),
        )
    facts.sort(key=lambda fact: (fact["start"], fact["kind"], fact["value"]))
    return facts


def protected_signature(text: str) -> dict[str, Any]:
    """Ordered literal guarded facts plus local binding context."""
    value = text if isinstance(text, str) else ""
    facts = _protected_facts(value)
    signature: dict[str, Any] = {
        key: [fact["value"] for fact in facts if fact["kind"] == key]
        for key in _PROTECTED_KEYS
        if key not in {"fact_bindings", "quoted"}
    }
    signature["fact_bindings"] = [
        {
            "kind": fact["kind"],
            "value": fact["value"],
            "left": fact["left"],
            "right": fact["right"],
        }
        for fact in facts
    ]
    signature["quoted"] = any(
        character in _QUOTE_CHARACTERS for character in value
    )
    return signature


def protected_signature_differences(old_text: str, new_text: str) -> list[str]:
    old_signature = protected_signature(old_text)
    new_signature = protected_signature(new_text)
    return [
        key
        for key in _PROTECTED_KEYS
        if old_signature[key] != new_signature[key]
    ]


def fragment_context_suspect(text: str) -> bool:
    """Conservative quoted/dangling fragment detector for equivalence claims."""
    value = text if isinstance(text, str) else ""
    stripped = value.strip()
    if not stripped:
        return True
    if any(character in _QUOTE_CHARACTERS for character in stripped):
        return True
    if _FRAGMENT_END_RE.search(stripped):
        return True
    if _DANGLING_END_RE.search(stripped):
        return True
    return len(_WORD_RE.findall(stripped)) < 3


# --- Source envelope / stable IDs ----------------------------------------------
def serialize_source_envelope(
    *,
    pair_id: Any,
    old: Any,
    new: Any,
    correspondence: Any,
    language: Any,
    context_complete: Any,
) -> str:
    """Canonical compact source envelope used for the 6,000-character bound.

    The same Stage-12 cap is applied here without importing Stage 12.  An
    oversized envelope is skipped whole; text is never head-sliced.
    """
    old_value = old if isinstance(old, dict) else {}
    new_value = new if isinstance(new, dict) else {}
    envelope = {
        "pair_id": pair_id if isinstance(pair_id, str) else None,
        "old": {
            "element_id": (
                old_value.get("element_id")
                if isinstance(old_value.get("element_id"), str)
                else None
            ),
            "text": old_value.get("text") if isinstance(old_value.get("text"), str) else "",
        },
        "new": {
            "element_id": (
                new_value.get("element_id")
                if isinstance(new_value.get("element_id"), str)
                else None
            ),
            "text": new_value.get("text") if isinstance(new_value.get("text"), str) else "",
        },
        "correspondence": correspondence if isinstance(correspondence, str) else None,
        "language": language if isinstance(language, str) else None,
        "context_complete": context_complete if isinstance(context_complete, bool) else None,
    }
    return json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_row_id(
    *,
    pair_id: Any,
    input_index: int,
    source_envelope_sha256: str,
    model_identity: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> str:
    seed = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "pair_id": pair_id if isinstance(pair_id, str) else None,
        "input_index": input_index,
        "source_envelope_sha256": source_envelope_sha256,
        "model_id": model_identity.get("model_id"),
        "model_id_source": model_identity.get("model_id_source"),
        "revision": model_identity.get("revision"),
        "revision_verification": model_identity.get("revision_verification"),
        "artifact_sha256": model_identity.get("artifact_sha256"),
        "limits": dict(limits),
    }
    return hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


# --- Score handling ------------------------------------------------------------
def softmax_logits(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax over finite logits."""
    values: list[float] = []
    for value in logits:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("non_numeric_logit")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non_finite_logit")
        values.append(number)
    if not values:
        raise ValueError("empty_logits")
    maximum = max(values)
    exponentials = [math.exp(number - maximum) for number in values]
    total = sum(exponentials)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid_softmax")
    return [value / total for value in exponentials]


def _normalized_scores(scores: Any) -> dict[str, float] | None:
    """Require exactly three finite probabilities in [0, 1] summing to one.

    A raw-logit mapping is not renormalized here.  The backend contract is an
    explicit softmax output; guessing from logits would hide a broken backend
    and make the recorded score format false.
    """
    if not isinstance(scores, Mapping):
        return None
    if set(scores.keys()) != set(CANONICAL_LABELS):
        return None
    normalized: dict[str, float] = {}
    for label in CANONICAL_LABELS:
        value = scores[label]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            return None
        normalized[label] = number
    if abs(sum(normalized.values()) - 1.0) > SCORE_SUM_TOLERANCE:
        return None
    return normalized


def _unique_argmax(scores: Mapping[str, float]) -> tuple[str | None, bool]:
    maximum = max(scores.values())
    winners = [label for label, value in scores.items() if value == maximum]
    if len(winners) != 1:
        return None, True
    return winners[0], False


# --- Bounds / input validation --------------------------------------------------
def _coerce_max_pairs(requested: Any) -> tuple[int, int]:
    if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
        raise ShadowInputError("invalid_max_pairs")
    return requested, min(requested, MAX_PAIRS)


def validate_pairs_document(document: Any) -> None:
    """Global shape/uniqueness validation with clause-free error codes."""
    if not isinstance(document, dict):
        raise ShadowInputError("invalid_pairs_document")
    pairs = document.get("pairs")
    if not isinstance(pairs, list):
        raise ShadowInputError("invalid_pairs_document")
    seen: set[str] = set()
    for row in pairs:
        if not isinstance(row, dict):
            continue
        pair_id = row.get("pair_id")
        if isinstance(pair_id, str) and pair_id.strip():
            if pair_id in seen:
                raise ShadowInputError("duplicate_pair_id")
            seen.add(pair_id)


def _base_row(
    *,
    input_index: int,
    row: Any,
    old_text: str,
    new_text: str,
    envelope: str,
    model_identity: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    raw = row if isinstance(row, dict) else {}
    old = raw.get("old") if isinstance(raw.get("old"), dict) else {}
    new = raw.get("new") if isinstance(raw.get("new"), dict) else {}
    pair_id = raw.get("pair_id")
    source_hash = _sha256_text(envelope)
    differences = protected_signature_differences(old_text, new_text)
    return {
        "input_index": input_index,
        "pair_id": pair_id if isinstance(pair_id, str) and pair_id.strip() else None,
        "status": None,
        "skip_reason": None,
        "error_code": None,
        "basis": "model_advisory",
        "authority": False,
        "correspondence": raw.get("correspondence"),
        "language": raw.get("language"),
        "context_complete": raw.get("context_complete"),
        "old_element_id": old.get("element_id") if isinstance(old.get("element_id"), str) else None,
        "new_element_id": new.get("element_id") if isinstance(new.get("element_id"), str) else None,
        "old_text_sha256": _sha256_text(old_text),
        "new_text_sha256": _sha256_text(new_text),
        "old_text_characters": len(old_text),
        "new_text_characters": len(new_text),
        "source_envelope_characters": len(envelope),
        "source_envelope_sha256": source_hash,
        "shadow_id": _stable_row_id(
            pair_id=pair_id,
            input_index=input_index,
            source_envelope_sha256=source_hash,
            model_identity=model_identity,
            limits=limits,
        ),
        "advisory_relation": None,
        "nli_pattern": None,
        "pair_assertion": False,
        "protected_signature": {
            "old": protected_signature(old_text),
            "new": protected_signature(new_text),
        },
        "protected_signature_differences": differences,
        "protected_fact_disagreement": bool(differences),
        "directional_scores": None,
        "directional_token_counts": None,
        "limitations": [],
    }


def run_shadow(
    pairs_document: Any,
    backend: Any,
    *,
    max_pairs: int = MAX_PAIRS,
    model_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run bounded bidirectional shadow assessment over every supplied row."""
    validate_pairs_document(pairs_document)
    requested_pairs, pair_limit = _coerce_max_pairs(max_pairs)

    max_tokens = getattr(backend, "max_tokens", None)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ShadowInputError("invalid_backend_token_window")
    token_limit = min(max_tokens, MAX_MODEL_TOKENS)
    if not callable(getattr(backend, "count_pair_tokens", None)) or not callable(
        getattr(backend, "predict", None)
    ):
        raise ShadowInputError("invalid_backend")

    metadata = dict(getattr(backend, "metadata", {}) or {})
    provenance_keys = (
        "artifact_sha256",
        "artifact_file_count",
        "artifact_bytes",
        "artifact_hash_status",
        "revision_verification",
        "model_id_source",
    )
    backend_provenance = {
        key: metadata.get(key)
        for key in provenance_keys
        if metadata.get(key) is not None
    }
    if model_metadata:
        metadata.update(model_metadata)
    # Optional caller metadata may add context, but it must not silently erase
    # the backend's actual local artifact fingerprint or verification limits.
    for key, value in backend_provenance.items():
        if metadata.get(key) in (None, ""):
            metadata[key] = value
    model_identity = {
        "model_id": metadata.get("model_id"),
        "model_id_source": metadata.get("model_id_source", "backend_metadata"),
        "revision": metadata.get("revision"),
        "revision_verification": metadata.get(
            "revision_verification", "unverified_declaration"
        ),
        "artifact_sha256": metadata.get("artifact_sha256"),
        "artifact_file_count": metadata.get("artifact_file_count"),
        "artifact_bytes": metadata.get("artifact_bytes"),
        "artifact_hash_status": metadata.get("artifact_hash_status", "unavailable"),
        "labels": list(CANONICAL_LABELS),
        "max_tokens": token_limit,
        "score_format": "softmax_probability",
        "backend": metadata.get("backend", "injected"),
        "local_files_only": bool(metadata.get("local_files_only", False)),
    }
    limits = {
        "max_pairs": pair_limit,
        "max_directional_inferences": pair_limit * 2,
        "max_source_characters": MAX_SOURCE_CHARACTERS,
        "max_model_tokens": token_limit,
        "truncation": "never",
    }
    directional_limit = pair_limit * 2

    pairs = pairs_document["pairs"]
    results: list[dict[str, Any]] = []
    pairs_attempted = 0
    pairs_assessed = 0
    pairs_skipped = 0
    pairs_errored = 0
    directional_inferences = 0
    budget_exhausted = False

    for input_index, row in enumerate(pairs):
        raw = row if isinstance(row, dict) else {}
        old = raw.get("old") if isinstance(raw.get("old"), dict) else {}
        new = raw.get("new") if isinstance(raw.get("new"), dict) else {}
        old_text = old.get("text") if isinstance(old.get("text"), str) else ""
        new_text = new.get("text") if isinstance(new.get("text"), str) else ""
        envelope = serialize_source_envelope(
            pair_id=raw.get("pair_id"),
            old=old,
            new=new,
            correspondence=raw.get("correspondence"),
            language=raw.get("language"),
            context_complete=raw.get("context_complete"),
        )
        record = _base_row(
            input_index=input_index,
            row=row,
            old_text=old_text,
            new_text=new_text,
            envelope=envelope,
            model_identity=model_identity,
            limits=limits,
        )

        def finish(status: str, *, skip_reason=None, error_code=None):
            record["status"] = status
            record["skip_reason"] = skip_reason
            record["error_code"] = error_code
            results.append(record)
            return record

        if not isinstance(row, dict):
            pairs_errored += 1
            finish("error", error_code="invalid_pair_row")
            continue
        pair_id = raw.get("pair_id")
        if not (isinstance(pair_id, str) and pair_id.strip()):
            pairs_errored += 1
            finish("error", error_code="invalid_pair_id")
            continue
        if not old_text.strip() or not new_text.strip():
            pairs_errored += 1
            finish("error", error_code="missing_text")
            continue
        correspondence = raw.get("correspondence")
        if correspondence not in CORRESPONDENCE_STATES:
            pairs_skipped += 1
            finish("skipped", skip_reason="unsupported_correspondence")
            continue
        if raw.get("language") != "en":
            pairs_skipped += 1
            finish("skipped", skip_reason="unsupported_language")
            continue
        # A full-context flag is required input evidence; never infer it from
        # text, a head cap or the model score.
        if raw.get("context_complete") is not True:
            pairs_skipped += 1
            finish("skipped", skip_reason="missing_context")
            continue
        if len(envelope) > MAX_SOURCE_CHARACTERS:
            pairs_skipped += 1
            record["limitations"].append("no_head_truncation")
            finish("skipped", skip_reason="source_too_large")
            continue

        try:
            old_to_new_tokens = backend.count_pair_tokens(old_text, new_text)
            new_to_old_tokens = backend.count_pair_tokens(new_text, old_text)
        except Exception:
            pairs_errored += 1
            finish("error", error_code="token_count_error")
            continue
        for count in (old_to_new_tokens, new_to_old_tokens):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                pairs_errored += 1
                finish("error", error_code="token_count_error")
                break
        else:
            record["directional_token_counts"] = {
                "old_to_new": old_to_new_tokens,
                "new_to_old": new_to_old_tokens,
            }
            if old_to_new_tokens > token_limit or new_to_old_tokens > token_limit:
                pairs_skipped += 1
                record["limitations"].append("no_truncation")
                finish("skipped", skip_reason="token_window_exceeded")
                continue

            if (
                pairs_attempted >= pair_limit
                or directional_inferences + 2 > directional_limit
            ):
                budget_exhausted = True
                pairs_skipped += 1
                finish("skipped", skip_reason="budget_exhausted")
                continue

            pairs_attempted += 1
            directional: dict[str, dict[str, Any]] = {}
            failed = False
            for direction, first, second, tokens in (
                ("old_to_new", old_text, new_text, old_to_new_tokens),
                ("new_to_old", new_text, old_text, new_to_old_tokens),
            ):
                directional_inferences += 1
                try:
                    raw_scores = backend.predict(first, second)
                except Exception:
                    finish("error", error_code="inference_error")
                    failed = True
                    break
                normalized = _normalized_scores(raw_scores)
                if normalized is None:
                    finish("error", error_code="invalid_model_scores")
                    failed = True
                    break
                top, ambiguous = _unique_argmax(normalized)
                directional[direction] = {
                    "model_scores": {
                        label: normalized[label] for label in CANONICAL_LABELS
                    },
                    "argmax": top,
                    "ambiguous": ambiguous,
                    "token_count": tokens,
                    "score_format": "softmax_probability",
                }
            if failed:
                pairs_errored += 1
                record["directional_scores"] = None
                continue

            record["directional_scores"] = directional
            old_top = directional["old_to_new"]["argmax"]
            new_top = directional["new_to_old"]["argmax"]
            differences = record["protected_signature_differences"]
            limitations = record["limitations"]
            if differences:
                limitations.append("protected_fact_disagreement")
            if correspondence != "verified":
                limitations.append("correspondence_not_verified")
            if fragment_context_suspect(old_text) or fragment_context_suspect(new_text):
                limitations.append("fragment_context_not_verified")
            record["pair_assertion"] = correspondence == "verified"

            if old_top == "contradiction" or new_top == "contradiction":
                record["advisory_relation"] = "possible_material_change"
                record["nli_pattern"] = "contradiction"
                if old_top != new_top:
                    limitations.append("directional_disagreement")
            elif old_top is None or new_top is None:
                record["advisory_relation"] = "undetermined"
                record["nli_pattern"] = "ambiguous_scores"
                limitations.append("ambiguous_top_score")
            elif old_top == "entailment" and new_top == "entailment":
                record["nli_pattern"] = "bidirectional_entailment"
                can_suggest_equivalence = (
                    correspondence == "verified"
                    and not differences
                    and "fragment_context_not_verified" not in limitations
                )
                record["advisory_relation"] = (
                    "possible_equivalent" if can_suggest_equivalence else "undetermined"
                )
            elif old_top == "entailment" or new_top == "entailment":
                record["advisory_relation"] = "undetermined"
                record["nli_pattern"] = "asymmetric_entailment"
                limitations.append("scope_expansion_or_contraction")
            else:
                record["advisory_relation"] = "undetermined"
                record["nli_pattern"] = "neutral"
                limitations.append("scope_context_uncertainty")

            pairs_assessed += 1
            finish("assessed")

    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "mode": "shadow_advisory",
        "authority": False,
        "score_semantics": SCORE_SEMANTICS,
        "limits": limits,
        "model": model_identity,
        "budget": {
            "input_pairs": len(pairs),
            "max_pairs": pair_limit,
            "max_pairs_requested": requested_pairs,
            "max_directional_inferences": directional_limit,
            "pairs_attempted": pairs_attempted,
            "pairs_assessed": pairs_assessed,
            "pairs_skipped": pairs_skipped,
            "pairs_errored": pairs_errored,
            "directional_inferences": directional_inferences,
            "budget_exhausted": budget_exhausted,
        },
        "results": results,
    }


__all__ = [
    "CANONICAL_LABELS",
    "CORRESPONDENCE_STATES",
    "MAX_DIRECTIONAL_INFERENCES",
    "MAX_MODEL_TOKENS",
    "MAX_PAIRS",
    "MAX_SOURCE_CHARACTERS",
    "POLICY_VERSION",
    "SCHEMA_VERSION",
    "SCORE_SEMANTICS",
    "ShadowInputError",
    "fragment_context_suspect",
    "protected_signature",
    "protected_signature_differences",
    "run_shadow",
    "serialize_source_envelope",
    "softmax_logits",
    "validate_pairs_document",
]
