"""Narrow source-backed material-delta witness (Gate A).

Only a complete old/new clause that differs by exactly one of these atoms can
produce a deterministic witness:

- one explicit negation atom (``SHALL NOT`` -> ``SHALL``);
- one deontic modal atom (``shall`` -> ``may``);
- one unambiguous decimal/date component edit;
- one threshold-operator edit (``at least`` -> ``at most``).

Everything else abstains: paraphrases, unit-only edits, ambiguous locale
formats, quoted text, conditions, exceptions, coordinated clauses and multiple
changed atoms.  This is not a synonym table or a calibrated confidence score.
"""
from __future__ import annotations

import math
import re
from typing import Any

from document_compare.phase1.contracts.semantic_assessment import (
    NEGATION_WORDS,
    THRESHOLD_OPERATOR_SEMANTICS,
    build_assessment,
    canonical_decimal,
    clause_guards,
    complete_clause,
    is_deterministic_material_proof,
    modal_force,
    source_text,
    unambiguous_numeric_span,
)

MIN_UNIQUE_CORRESPONDENCE_MARGIN = 0.10

_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_THRESHOLD_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"no\s+less\s+than|no\s+more\s+than|at\s+least|at\s+most|"
    r"less\s+than|more\s+than|greater\s+than|fewer\s+than|"
    r"<=|>=|<|>"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _diff_spans(old: str, new: str) -> tuple[int, int, int, int]:
    """Minimal single contiguous changed region (common prefix/suffix)."""
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]
    ):
        suffix += 1
    return prefix, len(old) - suffix, prefix, len(new) - suffix


def _span(text: str, start: int, end: int) -> dict[str, Any]:
    return {"start": start, "end": end, "text": text[start:end]}


def _context(text: str, start: int, end: int) -> dict[str, str]:
    return {"prefix": text[:start], "suffix": text[end:]}


def _witness(
    kind: str,
    old: str,
    new: str,
    *,
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
    guards: dict[str, bool],
    old_operator: str | None = None,
    new_operator: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "old_span": _span(old, old_start, old_end),
        "new_span": _span(new, new_start, new_end),
        "old_context": _context(old, old_start, old_end),
        "new_context": _context(new, new_start, new_end),
        "old_operator": old_operator,
        "new_operator": new_operator,
        "old_value": old_value,
        "new_value": new_value,
        "guards": {
            **guards,
            "single_changed_atom": True,
            "complete_context": True,
        },
    }


def _atom_is_standalone(text: str, start: int, end: int) -> bool:
    """Require word boundaries so ``not`` inside ``notable`` is not an atom."""
    raw = text[start:end]
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


def _threshold_witness(
    old: str,
    new: str,
    guards: dict[str, bool],
) -> dict[str, Any] | None:
    old_matches = list(_THRESHOLD_RE.finditer(old))
    new_matches = list(_THRESHOLD_RE.finditer(new))
    if len(old_matches) != 1 or len(new_matches) != 1:
        return None
    old_match, new_match = old_matches[0], new_matches[0]
    old_operator = " ".join(old_match.group(1).split()).casefold()
    new_operator = " ".join(new_match.group(1).split()).casefold()
    old_semantics = THRESHOLD_OPERATOR_SEMANTICS.get(old_operator)
    new_semantics = THRESHOLD_OPERATOR_SEMANTICS.get(new_operator)
    if old_semantics is None or new_semantics is None:
        return None
    if old_semantics == new_semantics:
        # Equivalent spelling ("at least" / "no less than") is not a change.
        return None
    sentinel = "<THRESHOLD>"
    old_skeleton = old[: old_match.start()] + sentinel + old[old_match.end() :]
    new_skeleton = new[: new_match.start()] + sentinel + new[new_match.end() :]
    if old_skeleton != new_skeleton:
        return None
    return _witness(
        "threshold_operator",
        old,
        new,
        old_start=old_match.start(),
        old_end=old_match.end(),
        new_start=new_match.start(),
        new_end=new_match.end(),
        guards=guards,
        old_operator=old_operator,
        new_operator=new_operator,
    )


def _modal_witness(old: str, new: str, guards: dict[str, bool]) -> dict[str, Any] | None:
    """Compare whole modal atoms, including words sharing a prefix (must/may)."""
    pattern = r"\b(?:shall|must|may)\b"
    old_matches = list(re.finditer(pattern, old, re.IGNORECASE))
    new_matches = list(re.finditer(pattern, new, re.IGNORECASE))
    if len(old_matches) != 1 or len(new_matches) != 1:
        return None
    old_match, new_match = old_matches[0], new_matches[0]
    old_atom, new_atom = old_match.group().casefold(), new_match.group().casefold()
    if modal_force(old_atom) == modal_force(new_atom):
        return None
    if (
        old[:old_match.start()] != new[:new_match.start()]
        or old[old_match.end():] != new[new_match.end():]
    ):
        return None
    return _witness(
        "modal_obligation", old, new,
        old_start=old_match.start(), old_end=old_match.end(),
        new_start=new_match.start(), new_end=new_match.end(),
        guards=guards, old_operator=old_atom, new_operator=new_atom,
    )


def _numeric_token_around(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Expand the changed atom to its maximal decimal/date numeric token."""
    left = start
    while left > 0:
        if text[left - 1].isdigit():
            left -= 1
        elif (
            text[left - 1] == "."
            and left - 2 >= 0
            and text[left - 2].isdigit()
        ):
            left -= 1
        else:
            break
    right = end
    while right < len(text):
        if text[right].isdigit():
            right += 1
        elif (
            text[right] == "."
            and right + 1 < len(text)
            and text[right + 1].isdigit()
        ):
            right += 1
        else:
            break
    token = text[left:right]
    if not _NUMBER_RE.fullmatch(token):
        return None
    # Identifiers/versions are not quantities: a letter or underscore directly
    # before the token, or a letter directly after it, abstains.
    if left > 0 and (text[left - 1].isalpha() or text[left - 1] == "_"):
        return None
    if right < len(text) and (text[right].isalpha() or text[right] == "_"):
        return None
    if not unambiguous_numeric_span(text, left, right):
        return None
    return left, right


def _quantity_witness(
    old: str,
    new: str,
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
    guards: dict[str, bool],
) -> dict[str, Any] | None:
    old_token = _numeric_token_around(old, old_start, old_end)
    new_token = _numeric_token_around(new, new_start, new_end)
    if old_token is None or new_token is None:
        return None
    old_start, old_end = old_token
    new_start, new_end = new_token
    old_value = canonical_decimal(old[old_start:old_end])
    new_value = canonical_decimal(new[new_start:new_end])
    if old_value is None or new_value is None or old_value == new_value:
        return None
    if old[:old_start] != new[:new_start] or old[old_end:] != new[new_end:]:
        return None
    return _witness(
        "quantity_value",
        old,
        new,
        old_start=old_start,
        old_end=old_end,
        new_start=new_start,
        new_end=new_end,
        guards=guards,
        old_value=old_value,
        new_value=new_value,
    )


def prove_material_delta(old_text: Any, new_text: Any) -> dict[str, Any] | None:
    """Return one complete deterministic witness, or ``None`` (abstain).

    The function is pure and inspects only the two exact source strings.  No
    score, model output, document domain or candidate identity is consulted.
    """
    old = old_text if isinstance(old_text, str) else ""
    new = new_text if isinstance(new_text, str) else ""
    if not old or not new or old == new:
        return None
    guards = clause_guards(old, new)
    if any(guards.values()):
        return None
    # Only one finished, source-aligned clause may carry a proof.  Fragments,
    # multiple sentences and dangling modals abstain instead of asserting a
    # caller-supplied completeness boolean.
    if not (complete_clause(old) and complete_clause(new)):
        return None

    threshold = _threshold_witness(old, new, guards)
    if threshold is not None:
        return threshold
    modal = _modal_witness(old, new, guards)
    if modal is not None:
        return modal

    old_start, old_end, new_start, new_end = _diff_spans(old, new)
    old_atom = old[old_start:old_end]
    new_atom = new[new_start:new_end]
    old_stripped = old_atom.strip().casefold()
    new_stripped = new_atom.strip().casefold()
    if not old_stripped and not new_stripped:
        return None

    if (
        (
            old_stripped in NEGATION_WORDS and not new_stripped
        ) or (
            new_stripped in NEGATION_WORDS and not old_stripped
        )
    ) and (
        (not old_stripped or _atom_is_standalone(old, old_start, old_end))
        and (not new_stripped or _atom_is_standalone(new, new_start, new_end))
    ):
        return _witness(
            "polarity_negation",
            old,
            new,
            old_start=old_start,
            old_end=old_end,
            new_start=new_start,
            new_end=new_end,
            guards=guards,
            old_operator=old_stripped or None,
            new_operator=new_stripped or None,
        )

    return _quantity_witness(
        old,
        new,
        old_start,
        old_end,
        new_start,
        new_end,
        guards,
    )


def assess_pair(
    old_element: dict[str, Any] | None,
    new_element: dict[str, Any] | None,
    *,
    correspondence: str,
) -> dict[str, Any]:
    """Build an assessment from the actual source elements (no authority yet)."""
    old_text = source_text(old_element)
    new_text = source_text(new_element)
    witness = prove_material_delta(old_text, new_text)
    if witness is None:
        return build_assessment(
            correspondence=correspondence,
            relation="undetermined",
            basis="not_assessed",
            old_element=old_element,
            new_element=new_element,
            limitations=["no_deterministic_material_witness"],
        )
    return build_assessment(
        correspondence=correspondence,
        relation="material_change",
        basis="deterministic_witness",
        old_element=old_element,
        new_element=new_element,
        witness=witness,
    )


def reassess_residual(
    element: dict[str, Any] | None,
    *,
    side: str,
    correspondence: str,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Not-assessed assessment for a one-sided residual/proposal candidate."""
    return build_assessment(
        correspondence=correspondence,
        relation="undetermined",
        basis="not_assessed",
        old_element=element if side == "old" else None,
        new_element=element if side == "new" else None,
        limitations=list(limitations or []),
    )


def _extraction_safe(element: dict[str, Any] | None) -> bool:
    if not isinstance(element, dict):
        return False
    validation = element.get("extraction_validation")
    if not isinstance(validation, dict):
        return False
    return validation.get("requires_extraction_review") is False


def independent_correspondence_verified(candidate: dict[str, Any]) -> bool:
    """Strong unique reciprocal evidence, independent of the semantic proof.

    A score alone is never enough: the pair must be reciprocal-best with a real
    best-candidate margin, same element type, and clean extraction metadata.
    """
    if not isinstance(candidate, dict):
        return False
    old = candidate.get("old")
    new = candidate.get("new")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if old.get("element_type") != new.get("element_type"):
        return False
    evidence = candidate.get("similarity_evidence")
    if not isinstance(evidence, dict):
        return False
    if evidence.get("reciprocal_best") is not True:
        return False
    margin = evidence.get("minimum_best_margin")
    if not isinstance(margin, (int, float)) or isinstance(margin, bool):
        return False
    if not math.isfinite(margin):
        # NaN comparisons are always false and would otherwise look "clean".
        return False
    if margin < MIN_UNIQUE_CORRESPONDENCE_MARGIN:
        return False
    return _extraction_safe(old) and _extraction_safe(new)


def recompute_material_proof(
    assessment: Any,
    old_element: dict[str, Any] | None,
    new_element: dict[str, Any] | None,
) -> bool:
    """Independently re-derive the witness from the actual source text.

    A serialized/forged witness, a prefix of the truth, or a route boolean can
    never be sufficient.  The recomputed atom kind and spans must match.
    """
    if not is_deterministic_material_proof(assessment):
        return False
    old_text = source_text(old_element)
    new_text = source_text(new_element)
    recomputed = prove_material_delta(old_text, new_text)
    if recomputed is None:
        return False
    claimed = assessment.get("witness") or {}
    for key in ("kind", "old_span", "new_span", "old_context", "new_context"):
        if claimed.get(key) != recomputed.get(key):
            return False
    return True
