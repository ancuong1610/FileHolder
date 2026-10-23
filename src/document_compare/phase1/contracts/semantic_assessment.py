"""Versioned, source-bound semantic assessment contract (Gate A).

This module is pure and Phase-1 owned.  It defines the *shape* and the source
binding checks for an optional ``semantic_assessment`` attached to a candidate
or canonical change.  It never reruns matching, calls a model, mutates a saved
artifact, or turns evidence into acceptance by itself.

The important separation is explicit:

- ``correspondence`` records whether an old/new pairing is verified, merely
  proposed, unresolved, or not applicable.
- ``relation`` records material meaning (``material_change``), equivalence, or
  ``undetermined``.
- ``basis`` records whether a deterministic source witness, advisory model
  output, or no assessment exists.

Only a complete deterministic witness over the actual source text can support a
material-change conclusion.  A boolean attestation, a stale hash, a side-id
mismatch or a model advisory is never authority.
"""
from __future__ import annotations

import copy
import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any

ASSESSMENT_SCHEMA_VERSION = "1.0.0"
SEMANTIC_POLICY_VERSION = "gateA-1"

CORRESPONDENCE_STATES = ("verified", "proposed", "unresolved", "not_applicable")
RELATION_STATES = ("material_change", "equivalent", "undetermined")
ASSESSMENT_BASES = ("deterministic_witness", "model_advisory", "not_assessed")

WITNESS_KINDS = (
    "polarity_negation",
    "modal_obligation",
    "quantity_value",
    "threshold_operator",
)

# Closed grammatical classes, not a benchmark synonym table.  Modals are
# grouped by semantic force so a spelling change between equivalent mandatory
# modals ("must" / "shall") is not material proof.
NEGATION_WORDS = frozenset({"not", "no", "never"})
MODAL_FORCE = {
    "must": "mandatory",
    "shall": "mandatory",
    "may": "permissive",
}
MODAL_WORDS = frozenset(MODAL_FORCE)
THRESHOLD_OPERATOR_SEMANTICS = {
    "at least": "ge",
    "at most": "le",
    "no less than": "ge",
    "no more than": "le",
    "less than": "lt",
    "more than": "gt",
    "greater than": "gt",
    "fewer than": "lt",
    "<": "lt",
    "<=": "le",
    ">": "gt",
    ">=": "ge",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")

# Pure clause guards.  A deterministic proof is only attempted on a single
# complete clause.  Quoted text, conditions, exceptions and coordination make
# the changed atom ambiguous, so those cases abstain.
_CONDITIONAL_OR_EXCEPTION_RE = re.compile(
    r"\b(?:if|unless|except|provided|subject\s+to|other\s+than|notwithstanding)\b",
    re.IGNORECASE,
)
_COORDINATED_CLAUSE_RE = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)
_QUOTE_CHARACTERS = frozenset("\"'`\u2018\u2019\u201c\u201d")

# A complete clause must end with exactly one sentence terminator.  Interior
# terminators mean more than one sentence, and a dangling modal/negation means
# the changed predicate or its object was not captured.
_SENTENCE_TERMINATOR_RE = re.compile(r"[.!?](?=\s|$)")
_DANGLING_END_RE = re.compile(
    r"\b(?:no|not|never|shall|must|may)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_SEMICOLON_RE = re.compile(r";")
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def sha256_text(text: Any) -> str:
    """SHA-256 of the exact UTF-8 source text (no trimming)."""
    value = text if isinstance(text, str) else ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_text(element: dict[str, Any] | None) -> str:
    """Return the exact raw source text used for hashes/spans.

    ``text`` is the raw extraction representation before any lossy matching
    normalization, so a witness is always checked against the actual source.
    """
    if not isinstance(element, dict):
        return ""
    value = element.get("text")
    return value if isinstance(value, str) else ""


def _element_id(element: dict[str, Any] | None) -> str | None:
    if not isinstance(element, dict):
        return None
    value = element.get("element_id")
    return value if isinstance(value, str) and value else None


def _not_assessed(
    *,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "policy_version": SEMANTIC_POLICY_VERSION,
        "correspondence": "not_applicable",
        "relation": "undetermined",
        "basis": "not_assessed",
        "old_element_id": _element_id(old_element),
        "new_element_id": _element_id(new_element),
        "old_text_sha256": (
            sha256_text(source_text(old_element)) if old_element is not None else None
        ),
        "new_text_sha256": (
            sha256_text(source_text(new_element)) if new_element is not None else None
        ),
        "witness": None,
        "guards": {},
        "limitations": list(limitations or []),
        "model": None,
    }


def not_assessed_assessment(
    *,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Public legacy/native fallback: no semantic assessment exists."""
    return _not_assessed(
        old_element=old_element,
        new_element=new_element,
        limitations=limitations,
    )


def build_assessment(
    *,
    correspondence: str,
    relation: str,
    basis: str,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
    witness: dict[str, Any] | None = None,
    guards: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one assessment without validating it.

    Validation is a separate step so producers can never mark their own output
    authoritative merely by constructing this dictionary.
    """
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "policy_version": SEMANTIC_POLICY_VERSION,
        "correspondence": correspondence,
        "relation": relation,
        "basis": basis,
        "old_element_id": _element_id(old_element),
        "new_element_id": _element_id(new_element),
        "old_text_sha256": (
            sha256_text(source_text(old_element)) if old_element is not None else None
        ),
        "new_text_sha256": (
            sha256_text(source_text(new_element)) if new_element is not None else None
        ),
        "witness": copy.deepcopy(witness),
        "guards": copy.deepcopy(guards or (witness or {}).get("guards") or {}),
        "limitations": list(limitations or []),
        "model": copy.deepcopy(model),
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def unambiguous_numeric_span(text: str, start: int, end: int) -> bool:
    """Reject numeric slices from identifiers, signs, locale groups or fractions.

    Producer and validator share this rule: a valid hash/offset cannot turn the
    first digit in ``1,5`` into evidence for the quantity one.
    """
    if not (_is_int(start) and _is_int(end) and 0 <= start < end <= len(text)):
        return False
    if not _DECIMAL_RE.fullmatch(text[start:end]):
        return False
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if (before and (before.isalnum() or before in "_+-.")) or (
        after and (after.isalnum() or after in "_+-")
    ):
        return False
    separators = {",", "'", "\u2019", "\uff0c", "/", ":"}
    if after in separators | {"."} and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    if before in separators and start > 1 and text[start - 2].isdigit():
        return False
    if re.match(r"\s+\d", text[end:]) or re.search(r"\d\s+$", text[:start]):
        return False
    return True


def _span_errors(
    side: str,
    span: Any,
    context: Any,
    text: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(span, dict):
        return [f"{side}_span_missing"]
    start, end, atom = span.get("start"), span.get("end"), span.get("text")
    if not _is_int(start) or not _is_int(end) or not isinstance(atom, str):
        return [f"{side}_span_malformed"]
    if start < 0 or end < start or end > len(text):
        return [f"{side}_span_out_of_bounds"]
    if text[start:end] != atom:
        errors.append(f"{side}_span_offset_or_text_mismatch")

    if not isinstance(context, dict):
        errors.append(f"{side}_context_missing")
        return errors
    prefix, suffix = context.get("prefix"), context.get("suffix")
    if not isinstance(prefix, str) or prefix != text[:start]:
        errors.append(f"{side}_context_prefix_mismatch")
    if not isinstance(suffix, str) or suffix != text[end:]:
        errors.append(f"{side}_context_suffix_mismatch")
    return errors


def _canonical_decimal(text: Any) -> str | None:
    value = str(text if text is not None else "").strip()
    if not _DECIMAL_RE.fullmatch(value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if number == number.to_integral_value():
        return str(int(number))
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".")


def _atom_is_standalone(text: str, span: Any) -> bool:
    """True when the trimmed span atom has word boundaries in the source."""
    if not isinstance(span, dict):
        return False
    start, end, raw = span.get("start"), span.get("end"), span.get("text")
    if not _is_int(start) or not _is_int(end) or not isinstance(raw, str):
        return False
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    token_start, token_end = start + leading, end - trailing
    if token_start >= token_end:
        return False
    before = text[token_start - 1] if token_start > 0 else ""
    after = text[token_end] if token_end < len(text) else ""
    return not (
        (before and (before.isalnum() or before == "_"))
        or (after and (after.isalnum() or after == "_"))
    )


def _witness_errors(
    witness: Any,
    *,
    old_text: str,
    new_text: str,
) -> list[str]:
    """Validate exact spans/context and the narrow deterministic atom classes."""
    errors: list[str] = []
    if not isinstance(witness, dict):
        return ["witness_missing_or_not_object"]
    kind = witness.get("kind")
    if kind not in WITNESS_KINDS:
        return ["witness_kind_unsupported"]

    old_span = witness.get("old_span")
    new_span = witness.get("new_span")
    errors.extend(
        _span_errors("old", old_span, witness.get("old_context"), old_text)
    )
    errors.extend(
        _span_errors("new", new_span, witness.get("new_context"), new_text)
    )
    if errors:
        return errors

    old_atom = str((old_span or {}).get("text") or "")
    new_atom = str((new_span or {}).get("text") or "")
    old_context = witness.get("old_context") or {}
    new_context = witness.get("new_context") or {}
    context_identical = (
        old_context.get("prefix") == new_context.get("prefix")
        and old_context.get("suffix") == new_context.get("suffix")
    )
    if not context_identical:
        errors.append("witness_context_not_identical")

    # Guards are recomputed from the actual source strings; a caller-supplied
    # boolean alone is never authority.  The claimed guards must also match the
    # recomputed values so a forged attestation cannot travel unnoticed.
    source_guards = clause_guards(old_text, new_text)
    source_complete = complete_clause(old_text) and complete_clause(new_text)
    for guard in (
        "quoted",
        "conditional_or_exception",
        "coordinated_clause",
    ):
        if source_guards.get(guard) is not False:
            errors.append(f"witness_guard_failed:{guard}")
    if not source_complete:
        errors.append("witness_guard_failed:complete_context")
    recomputed_guards = {
        **source_guards,
        "single_changed_atom": context_identical,
        "complete_context": source_complete,
    }
    claimed_guards = witness.get("guards")
    if not isinstance(claimed_guards, dict):
        errors.append("witness_guards_missing")
    else:
        for guard, expected in recomputed_guards.items():
            if claimed_guards.get(guard) is not expected:
                errors.append(f"witness_guard_not_recomputed:{guard}")

    old_stripped = old_atom.strip().casefold()
    new_stripped = new_atom.strip().casefold()

    if kind == "polarity_negation":
        if not (
            (old_stripped in NEGATION_WORDS and new_stripped == "")
            or (new_stripped in NEGATION_WORDS and old_stripped == "")
            or (
                old_stripped in NEGATION_WORDS
                and new_stripped in NEGATION_WORDS
                and old_stripped != new_stripped
            )
        ):
            errors.append("witness_atom_not_a_single_negation")
        elif not (
            (not old_stripped or _atom_is_standalone(old_text, old_span))
            and (not new_stripped or _atom_is_standalone(new_text, new_span))
        ):
            errors.append("witness_atom_not_a_standalone_word")
    elif kind == "modal_obligation":
        old_force = modal_force(old_stripped)
        new_force = modal_force(new_stripped)
        if (
            old_stripped not in MODAL_WORDS
            or new_stripped not in MODAL_WORDS
            or old_stripped == new_stripped
        ):
            errors.append("witness_atom_not_a_modal_change")
        elif old_force == new_force:
            errors.append("witness_modal_force_not_different")
        if witness.get("old_operator") != old_stripped or witness.get(
            "new_operator"
        ) != new_stripped:
            errors.append("witness_modal_operator_inconsistent")
        if not (
            _atom_is_standalone(old_text, old_span)
            and _atom_is_standalone(new_text, new_span)
        ):
            errors.append("witness_atom_not_a_standalone_word")
    elif kind == "quantity_value":
        old_value = _canonical_decimal(old_atom.strip())
        new_value = _canonical_decimal(new_atom.strip())
        if not (
            unambiguous_numeric_span(old_text, old_span["start"], old_span["end"])
            and unambiguous_numeric_span(new_text, new_span["start"], new_span["end"])
        ):
            errors.append("witness_numeric_boundary_ambiguous")
        if old_value is None or new_value is None or old_value == new_value:
            errors.append("witness_atom_not_an_unambiguous_changed_number")
        elif witness.get("old_value") != old_value or witness.get("new_value") != new_value:
            errors.append("witness_numeric_values_inconsistent")
    elif kind == "threshold_operator":
        old_operator = str(witness.get("old_operator") or old_stripped)
        new_operator = str(witness.get("new_operator") or new_stripped)
        old_semantics = THRESHOLD_OPERATOR_SEMANTICS.get(old_operator.casefold())
        new_semantics = THRESHOLD_OPERATOR_SEMANTICS.get(new_operator.casefold())
        if (
            old_semantics is None
            or new_semantics is None
            or old_semantics == new_semantics
        ):
            errors.append("witness_atom_not_an_unambiguous_operator_change")
    return errors


def validate_semantic_assessment(
    assessment: Any,
    *,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one assessment against the actual source elements.

    Returns ``{"valid", "errors", "assessment"}``.  ``assessment`` is always a
    safe copy: an invalid input becomes a ``not_assessed`` projection, never an
    authoritative one.  The input mapping is never mutated.
    """
    errors: list[str] = []
    if not isinstance(assessment, dict):
        return {
            "valid": False,
            "errors": ["assessment_not_object"],
            "assessment": _not_assessed(
                old_element=old_element,
                new_element=new_element,
                limitations=["invalid_semantic_assessment"],
            ),
        }

    if assessment.get("schema_version") != ASSESSMENT_SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    if assessment.get("policy_version") != SEMANTIC_POLICY_VERSION:
        errors.append("unsupported_policy_version")
    correspondence = assessment.get("correspondence")
    relation = assessment.get("relation")
    basis = assessment.get("basis")
    if correspondence not in CORRESPONDENCE_STATES:
        errors.append("invalid_correspondence")
    if relation not in RELATION_STATES:
        errors.append("invalid_relation")
    if basis not in ASSESSMENT_BASES:
        errors.append("invalid_basis")
    limitations = assessment.get("limitations")
    if limitations is not None and not (
        isinstance(limitations, list)
        and all(isinstance(item, str) for item in limitations)
    ):
        errors.append("invalid_limitations")

    if basis == "deterministic_witness" and relation != "material_change":
        errors.append("deterministic_witness_requires_material_change")
    if basis == "deterministic_witness" and not isinstance(
        assessment.get("witness"), dict
    ):
        errors.append("deterministic_witness_missing")
    if basis == "model_advisory" and not isinstance(assessment.get("model"), dict):
        errors.append("model_advisory_requires_model_identity")
    if basis == "not_assessed" and relation != "undetermined":
        errors.append("not_assessed_requires_undetermined_relation")

    old_text = source_text(old_element) if old_element is not None else None
    new_text = source_text(new_element) if new_element is not None else None

    if old_element is not None:
        expected_id = _element_id(old_element)
        if assessment.get("old_element_id") != expected_id:
            errors.append("old_element_id_mismatch")
        expected_hash = sha256_text(old_text)
        actual_hash = assessment.get("old_text_sha256")
        if not isinstance(actual_hash, str) or not _SHA256_RE.fullmatch(actual_hash):
            errors.append("old_text_sha256_missing")
        elif actual_hash != expected_hash:
            errors.append("old_text_sha256_mismatch")
    if new_element is not None:
        expected_id = _element_id(new_element)
        if assessment.get("new_element_id") != expected_id:
            errors.append("new_element_id_mismatch")
        expected_hash = sha256_text(new_text)
        actual_hash = assessment.get("new_text_sha256")
        if not isinstance(actual_hash, str) or not _SHA256_RE.fullmatch(actual_hash):
            errors.append("new_text_sha256_missing")
        elif actual_hash != expected_hash:
            errors.append("new_text_sha256_mismatch")

    witness = assessment.get("witness")
    if basis == "deterministic_witness" and isinstance(witness, dict):
        if old_text is None or new_text is None:
            errors.append("deterministic_witness_requires_both_source_sides")
        else:
            errors.extend(
                _witness_errors(witness, old_text=old_text, new_text=new_text)
            )

    if errors:
        return {
            "valid": False,
            "errors": errors,
            "assessment": _not_assessed(
                old_element=old_element,
                new_element=new_element,
                limitations=["invalid_semantic_assessment"],
            ),
        }
    return {
        "valid": True,
        "errors": [],
        "assessment": copy.deepcopy(assessment),
    }


def normalize_assessment(
    assessment: Any,
    *,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safe public projection: missing/invalid means ``not_assessed``."""
    if assessment is None:
        return _not_assessed(old_element=old_element, new_element=new_element)
    result = validate_semantic_assessment(
        assessment, old_element=old_element, new_element=new_element
    )
    return result["assessment"]


def is_deterministic_material_proof(assessment: Any) -> bool:
    """Structural authority check; Stage 14 additionally recomputes the proof."""
    if not isinstance(assessment, dict):
        return False
    if assessment.get("basis") != "deterministic_witness":
        return False
    if assessment.get("relation") != "material_change":
        return False
    witness = assessment.get("witness")
    if not isinstance(witness, dict) or witness.get("kind") not in WITNESS_KINDS:
        return False
    guards = witness.get("guards")
    if not isinstance(guards, dict):
        return False
    return (
        guards.get("quoted") is False
        and guards.get("conditional_or_exception") is False
        and guards.get("coordinated_clause") is False
        and guards.get("single_changed_atom") is True
        and guards.get("complete_context") is True
    )


def modal_force(word: Any) -> str | None:
    """Closed semantic-force class for a single modal word."""
    if not isinstance(word, str):
        return None
    return MODAL_FORCE.get(word.strip().casefold())


def complete_clause(text: Any) -> bool:
    """Conservative English single-clause completeness check.

    This is intentionally narrow, not a general parser: it only accepts one
    finished sentence with a terminal terminator, no interior sentence break,
    no semicolon coordination, no line break, and no dangling modal/negation.
    Anything else leaves the changed predicate/scope unknown and must abstain.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if "\n" in stripped or "\r" in stripped:
        return False
    if _SEMICOLON_RE.search(stripped):
        return False
    terminators = list(_SENTENCE_TERMINATOR_RE.finditer(stripped))
    if len(terminators) != 1:
        return False
    if terminators[0].end() != len(stripped):
        return False
    if _DANGLING_END_RE.search(stripped):
        return False
    if len(_WORD_RE.findall(stripped)) < 3:
        return False
    return True


def clause_guards(old_text: str, new_text: str) -> dict[str, bool]:
    """Pure clause-level guards shared by producer and validator."""
    combined = f"{old_text}\n{new_text}"
    return {
        "quoted": any(character in _QUOTE_CHARACTERS for character in combined),
        "conditional_or_exception": bool(
            _CONDITIONAL_OR_EXCEPTION_RE.search(combined)
        ),
        "coordinated_clause": bool(_COORDINATED_CLAUSE_RE.search(combined)),
    }


def canonical_decimal(text: Any) -> str | None:
    """Public canonical decimal used by the narrow numeric witness."""
    return _canonical_decimal(text)
