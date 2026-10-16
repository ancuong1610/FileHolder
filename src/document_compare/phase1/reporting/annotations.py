"""Reporting-only annotation of token-diff operations for the redline.

This module is deliberately pure and side-effect free. It NEVER changes the
canonical result, routing, matching, schemas, or any rendered source text; it
only classifies the token operations already present on a change so the HTML
and PDF redlines can show an additive grouping of "possible wrap/formatting
differences" versus "remaining content differences (uncertain)".

Conservative rules (approved policy):

* A *possible* wrap difference is an explicit hyphen with adjacent whitespace
  between lowercase word fragments (``vorhan- den`` / ``Positi- onen``). It is
  NOT global removal of arbitrary hyphens or whitespace and NOT case folding.
* Without source line-break proof it is labelled POSSIBLE, never "verified
  formatting".
* ``re-sign``/``resign``, ``co-operating``/``cooperating``, ``now here``/
  ``nowhere`` and case-only changes can never be a possible wrap.
* A whole text that is equivalent after the strictly limited wrap transform may
  group multi-operation wrap-position changes (``Positi- onen`` / ``Positio-
  nen``); otherwise each operation is checked on its own. Inserted sentences
  remain content differences.
* Remaining operations are only ever called "content differences
  (uncertain)": the annotation never asserts they are meaningful and never
  infers a DI/extraction error.
* Any ambiguous/malformed token diff leaves the original differences unchanged.
"""
from __future__ import annotations

import re
from typing import Any

# Visible hyphen and soft-hyphen variants used by the pipeline tokenizers.
_WRAP_HYPHENS = "-\u00ad\u2010\u2011"
_WHITESPACE = " \t\n\r"
_HYPHEN_CLASS = re.escape(_WRAP_HYPHENS)

# A hyphen flanked by optional whitespace. It is only removed when the matched
# span contains whitespace AND sits directly between lowercase fragments.
_WRAP_CANDIDATE_RE = re.compile(r"[ \t\n\r]*[" + _HYPHEN_CLASS + r"][ \t\n\r]*")
# A visible hyphen directly before a real newline, e.g. ``vorhan-\nden``.
_LINE_BREAK_HYPHEN_RE = re.compile(
    r"(\w+)[-\u00ad\u2010\u2011][ \t]*\n[ \t]*(\w+)"
)
# Whitespace-only strip (used only to check a char offset against the tokens).
_WS_STRIP_RE = re.compile(r"[ \t\n\r]+")


CLASSIFICATION_POSSIBLE_WRAP = "possible_wrap"
CLASSIFICATION_CONTENT = "content"

_AMBIGUOUS_SUMMARY = (
    "Operation grouping unavailable; original differences shown unchanged."
)
_NO_OPERATIONS_SUMMARY = "No token operations."
_NOT_VERIFIED_SUFFIX = "(not verified formatting)"


def limited_wrap_transform(text: Any) -> str | None:
    """Remove ONLY explicit hyphen + whitespace between lowercase fragments.

    Returns the transformed text when at least one such hyphen/whitespace span
    was removed, otherwise ``None``. Case is preserved: an arbitrary hyphen
    without adjacent whitespace (``re-sign``), an arbitrary word boundary
    (``now here``), and case-only changes are never transformed.
    """
    if not isinstance(text, str) or not text:
        return None
    if not any(hyphen in text for hyphen in _WRAP_HYPHENS):
        return None
    parts: list[str] = []
    last = 0
    changed = False
    for match in _WRAP_CANDIDATE_RE.finditer(text):
        span = match.group(0)
        if not any(char in _WHITESPACE for char in span):
            # Explicit hyphen PLUS whitespace is required: a bare ``re-sign``
            # is never a possible wrap.
            continue
        if match.start() == 0 or match.end() >= len(text):
            continue
        before = text[match.start() - 1]
        after = text[match.end()]
        if not (before.islower() and after.islower()):
            continue
        parts.append(text[last:match.start()])
        last = match.end()
        changed = True
    if not changed:
        return None
    parts.append(text[last:])
    return "".join(parts)


def possible_wrap_equivalent(old_text: Any, new_text: Any) -> bool:
    """True when two texts differ only by the strictly limited wrap transform."""
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return False
    if old_text == new_text:
        return False
    transformed_old = limited_wrap_transform(old_text)
    transformed_new = limited_wrap_transform(new_text)
    if transformed_old is None and transformed_new is None:
        return False
    left = old_text if transformed_old is None else transformed_old
    right = new_text if transformed_new is None else transformed_new
    return left == right


def _extract_diff_changes(token_diff: Any) -> list | None:
    """Return the ordered operation list, or ``None`` when parsing is ambiguous."""
    if isinstance(token_diff, list):
        return token_diff
    if not isinstance(token_diff, dict):
        return None
    changes = token_diff.get("changes")
    if isinstance(changes, list):
        return changes
    for key in (
        "comparison_token_diff",
        "source_token_diff",
        "normalized_token_diff",
        "conservative_layout_token_diff",
    ):
        nested = token_diff.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("changes"), list):
            return nested["changes"]
    return None


def _token_join(operation: dict, side: str) -> str | None:
    """Concatenated tokens for a side, or ``None`` when the tokens are absent."""
    tokens = operation.get(side + "_tokens")
    if isinstance(tokens, list):
        return "".join(str(token) for token in tokens)
    return None


def _operation_text(operation: dict, side: str, representation: Any) -> str | None:
    """Reliable text for one operation side, or ``None`` when unknown.

    Preference order: an explicit ``*_text`` field, then a char-offset slice
    that is consistent with the side's tokens against the provided
    representation. Token concatenation is deliberately NOT used here: joined
    tokens erase word boundaries, so they can never prove (or disprove) a wrap.
    """
    text = operation.get(side + "_text")
    if isinstance(text, str):
        return text
    start = operation.get(side + "_char_start")
    end = operation.get(side + "_char_end")
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end):
        return None
    if not isinstance(representation, str) or end > len(representation):
        return None
    sliced = representation[start:end]
    joined = _token_join(operation, side)
    if joined is not None and _WS_STRIP_RE.sub("", sliced) != _WS_STRIP_RE.sub("", joined):
        # The offsets do not describe the representation we were given; do not
        # trust the slice as proof.
        return None
    return sliced


def _operation_display(operation: dict, side: str, representation: Any) -> str:
    """Human-readable operation text; token fallback joins with spaces.

    The space-joined fallback is for DISPLAY ONLY and is never used for the
    possible-wrap classification.
    """
    text = _operation_text(operation, side, representation)
    if text is not None:
        return text
    tokens = operation.get(side + "_tokens")
    if isinstance(tokens, list):
        return " ".join(str(token) for token in tokens if str(token))
    return ""


def _source_span_for_side(raw: Any, tokens: Any) -> str | None:
    """The single raw-source span matching the token sequence, or ``None``.

    The tokens must appear in order with only whitespace between them, so the
    matched span is the actual source text for that side (whitespace included).
    ``None`` is returned when there is no match or more than one match: a
    missing or ambiguous pairing must stay content rather than be broadly
    grouped. No global hyphen/whitespace strip is performed here.
    """
    if not isinstance(raw, str) or not raw or not isinstance(tokens, list):
        return None
    pieces = [str(token) for token in tokens if str(token)]
    if not pieces:
        return None
    pattern = re.compile(r"[ \t\n\r]*".join(re.escape(piece) for piece in pieces))
    spans = [match.group(0) for match in pattern.finditer(raw)]
    if len(spans) != 1:
        return None
    return spans[0]


def _validated_side_range(
    text: Any, operation: dict, side: str
) -> tuple[int, int] | None:
    """The exact char range for one operation side, or ``None`` if untrustworthy."""
    if not isinstance(text, str):
        return None
    start = operation.get(side + "_char_start")
    end = operation.get(side + "_char_end")
    if not (isinstance(start, int) and isinstance(end, int)):
        return None
    if not (0 <= start <= end <= len(text)):
        return None
    sliced = text[start:end]
    declared = operation.get(side + "_text")
    if isinstance(declared, str):
        if declared != sliced:
            return None
    tokens = operation.get(side + "_tokens")
    if isinstance(tokens, list):
        joined = "".join(str(token) for token in tokens)
        if _WS_STRIP_RE.sub("", sliced) != _WS_STRIP_RE.sub("", joined):
            return None
    elif not isinstance(declared, str):
        return None
    return (start, end)


def reliable_operation_ranges(
    old_text: Any, new_text: Any, token_diff: Any
) -> list[dict[str, Any]] | None:
    """Validated char ranges for each token operation, or ``None``.

    Report-only helper: it returns ``None`` (never a guessed range) when the
    envelope is ambiguous, an offset is missing/out of bounds, a slice does not
    match the declared text/tokens, the ranges are not monotonic, or the
    unchanged source gaps between/after the ranges are not exactly equal.
    Comparing the intervening and trailing gaps is what stops a partial/stale
    diff from leaving a real source difference unhighlighted. Callers must then
    use a conservative alignment rather than apply mismatched offsets.
    """
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return None
    changes = _extract_diff_changes(token_diff)
    if changes is None:
        return None
    ranges: list[dict[str, Any]] = []
    previous_old = 0
    previous_new = 0
    for index, operation in enumerate(changes):
        if not isinstance(operation, dict):
            return None
        old_range = _validated_side_range(old_text, operation, "old")
        new_range = _validated_side_range(new_text, operation, "new")
        if old_range is None or new_range is None:
            return None
        if old_range[0] < previous_old or new_range[0] < previous_new:
            return None
        # The source between (or before) the changed ranges must be identical,
        # so every difference is accounted for by a range. Exact equality only:
        # a whitespace-only mismatch triggers the conservative fallback.
        if old_text[previous_old:old_range[0]] != new_text[previous_new:new_range[0]]:
            return None
        previous_old, previous_new = old_range[1], new_range[1]
        ranges.append({
            "index": index,
            "operation": operation.get("operation", "replace"),
            "old": old_range,
            "new": new_range,
        })
    if old_text[previous_old:] != new_text[previous_new:]:
        return None
    if not ranges and old_text != new_text:
        # Empty operations are only acceptable when the texts are identical.
        return None
    return ranges


def _source_linebreak_joins(raw: Any) -> set[str]:
    if not isinstance(raw, str) or "\n" not in raw:
        return set()
    joins: set[str] = set()
    for match in _LINE_BREAK_HYPHEN_RE.finditer(raw):
        left, right = match.group(1), match.group(2)
        if right[:1].islower():
            joins.add(left + right)
    return joins


def _ambiguous(annotation: dict) -> dict:
    annotation["ambiguous"] = True
    annotation["whole_text_possible_wrap"] = False
    annotation["operations"] = []
    annotation["possible_wrap_count"] = 0
    annotation["content_count"] = 0
    annotation["summary"] = _AMBIGUOUS_SUMMARY
    return annotation


def annotate_change(
    old_text: Any,
    new_text: Any,
    token_diff: Any,
    *,
    source_old: Any = None,
    source_new: Any = None,
) -> dict:
    """Classify each token operation without mutating any input.

    ``old_text``/``new_text`` are the compared strings, ``token_diff`` is the
    change's token-diff envelope (or a bare operation list), and
    ``source_old``/``source_new`` are optional raw source fields used only to
    flag a source line-break consistent with a possible wrap. The result is a
    plain dict; the inputs are never modified.
    """
    annotation: dict[str, Any] = {
        "ambiguous": False,
        "whole_text_possible_wrap": False,
        "operations": [],
        "possible_wrap_count": 0,
        "content_count": 0,
        "summary": "",
    }
    changes = _extract_diff_changes(token_diff)
    if changes is None:
        return _ambiguous(annotation)
    if not changes:
        annotation["summary"] = _NO_OPERATIONS_SUMMARY
        return annotation
    if any(not isinstance(operation, dict) for operation in changes):
        return _ambiguous(annotation)

    whole = possible_wrap_equivalent(old_text, new_text)
    annotation["whole_text_possible_wrap"] = whole

    linebreak_joins = _source_linebreak_joins(source_old) | _source_linebreak_joins(
        source_new
    )

    operations: list[dict[str, Any]] = []
    for index, operation in enumerate(changes):
        old_tokens = operation.get("old_tokens")
        new_tokens = operation.get("new_tokens")
        op_old = _operation_text(operation, "old", old_text)
        op_new = _operation_text(operation, "new", new_text)
        if (
            not isinstance(old_tokens, list)
            and not isinstance(new_tokens, list)
            and op_old is None
            and op_new is None
        ):
            # No reliable representation and no tokens: cannot classify. Leave
            # the original differences unchanged rather than inventing counts.
            return _ambiguous(annotation)
        if whole:
            is_wrap = True
        else:
            # Compare the actual matched source span for each side under the
            # limited wrap transform only. Missing or multiple matches stay
            # content (prefer a missing possible annotation over broad
            # grouping); no aggregate global hyphen/whitespace strip.
            old_candidate = (
                op_old
                if op_old is not None
                else _source_span_for_side(source_old, old_tokens)
            )
            new_candidate = (
                op_new
                if op_new is not None
                else _source_span_for_side(source_new, new_tokens)
            )
            if old_candidate is None or new_candidate is None:
                is_wrap = False
            else:
                is_wrap = possible_wrap_equivalent(old_candidate, new_candidate)
        consistent = False
        if is_wrap and linebreak_joins:
            for candidate_text in (op_old, op_new):
                if candidate_text is None:
                    continue
                stripped = limited_wrap_transform(candidate_text)
                if stripped and stripped in linebreak_joins:
                    consistent = True
                    break
        operations.append({
            "index": index,
            "operation": operation.get("operation", "replace"),
            "old_text": _operation_display(operation, "old", old_text),
            "new_text": _operation_display(operation, "new", new_text),
            "classification": (
                CLASSIFICATION_POSSIBLE_WRAP if is_wrap else CLASSIFICATION_CONTENT
            ),
            "source_linebreak_consistent": consistent,
        })
    annotation["operations"] = operations
    annotation["possible_wrap_count"] = sum(
        1 for op in operations if op["classification"] == CLASSIFICATION_POSSIBLE_WRAP
    )
    annotation["content_count"] = (
        len(operations) - annotation["possible_wrap_count"]
    )
    annotation["summary"] = summary_text(annotation)
    return annotation


def summary_text(annotation: dict) -> str:
    """Render the additive grouping summary for one change/cell."""
    if annotation.get("ambiguous"):
        return _AMBIGUOUS_SUMMARY
    operations = annotation.get("operations") or []
    if not operations:
        return _NO_OPERATIONS_SUMMARY
    total = len(operations)
    wrap = annotation.get("possible_wrap_count", 0)
    content = annotation.get("content_count", 0)
    parts: list[str] = []
    if wrap:
        parts.append(
            f"{wrap} of {total} operation(s) look like possible wrap/formatting "
            f"differences {_NOT_VERIFIED_SUFFIX}"
        )
    if content:
        parts.append(
            f"{content} operation(s) remain content differences (uncertain)"
        )
    if not parts:
        parts.append(f"{total} operation(s) (uncertain)")
    return "; ".join(parts) + "."
