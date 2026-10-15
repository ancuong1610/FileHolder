"""Residual resegmentation reconciliation (cosmetic-noise suppressor).

Pairs leftover DELETED/ADDED candidates whose texts are near-twins under
token overlap (DI paragraph splits/merges, list-marker prefix changes,
hyphenation artifacts). Pairs are surfaced as RESEGMENTATION_REVIEW evidence;
they are never auto-accepted as unchanged, so no real change can be hidden.

A conservative raw-text gate keeps genuine quantitative edits out of this
cosmetic bucket: a number-bearing pair only pairs when its raw evidence is
equal under layout-only normalization. Different digits, signs, operators,
parentheses, fraction separators, short units or scale words (21 -> 99,
- 21 -> 21, < 21 -> > 21, (21) -> 21, 1/2 -> 1 2, 21kg -> 21g, 21 million ->
21 billion) leave the candidates as visible DELETE/ADD residuals rather than
pairing them as the same value.
"""
from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)

# Raw-text quantitative evidence: signed/plus/decimal numbers (ASCII minus,
# Unicode minus U+2212, plus; the sign may be separated from its digits by
# whitespace) and operator/unit markers (<, >, U+2264 <=, U+2265 >=, /=,
# percent) that carry a numeric change but are invisible to TOKEN_RE, which
# keeps only runs of >=3 plain word characters.
#
# The signature is an ordered tuple of whitespace-normalized matches, so a
# reordered or repeated literal ("12 34" vs "34 12", "1 1" vs "1") is not
# collapsed the way a set would collapse it.
NUMERIC_EVIDENCE_RE = re.compile(
    r"[+\-\u2212]?\s*\d+(?:[.,]\d+)*\s*%?|[<>\u2264\u2265/=]",
    re.UNICODE,
)

_NUMERIC_BEARING_RE = re.compile(r"\d", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)
# A hyphen at a line break is a layout artifact ("per-\ncent" == "percent").
_LINE_BREAK_HYPHEN_RE = re.compile(r"-[ \t]*\r?\n[ \t]*", re.UNICODE)
# A space before the percent unit is layout, not evidence ("12,5 %" == "12,5%").
_PERCENT_SPACING_RE = re.compile(r"\s*%", re.UNICODE)

# ponytail: split/merge twins stay within ~2x token count; stricter ratio
# would need real split-aware pairing if BU reports missed resegmentations.
MIN_LENGTH_RATIO = 0.5


def _tokens(text: Any) -> set[str]:
    return set(TOKEN_RE.findall(str(text or "").casefold()))


def _raw_text(element: dict[str, Any] | None) -> str:
    """The source text for the quantitative gate.

    ``text`` (raw extraction) is read before ``normalized_text``/
    ``comparison_text`` because the latter may be a lossy normalization that
    no longer proves a numeric difference.
    """
    if not isinstance(element, dict):
        return ""
    for key in ("text", "normalized_text", "comparison_text"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _numeric_signature(text: Any) -> tuple[str, ...]:
    """Ordered tuple of raw numeric/sign/operator/unit markers in ``text``.

    Whitespace inside a match is removed so ``12,5 %`` and a line-wrapped
    ``12,5``/``%`` compare as the same evidence.
    """
    return tuple(
        _WHITESPACE_RE.sub("", match)
        for match in NUMERIC_EVIDENCE_RE.findall(str(text or ""))
    )


def _layout_normalize(text: Any) -> str:
    """Reduce ``text`` to a NUMBER-bearing pair's layout-only skeleton.

    Only unambiguous layout is removed: a line-break hyphenation, runs of
    whitespace, and spacing around the percent unit. Every other character
    (symbol, sign, parenthesis, fraction separator, unit or scale word) is
    preserved, so a real difference stays visible.
    """
    value = _LINE_BREAK_HYPHEN_RE.sub("", str(text or ""))
    value = _WHITESPACE_RE.sub(" ", value)
    value = _PERCENT_SPACING_RE.sub("%", value)
    return value.strip()


def _quantitative_compatible(old_raw: str, new_raw: str) -> bool:
    """True only when a NUMBER-bearing pair carries genuinely equal evidence.

    The token score is blind to short/signed/decimal numbers and to operator,
    unit and punctuation context, so a real edit would otherwise score as a
    cosmetic near-twin. A number-bearing pair may only pair when its raw text
    is equal under layout-only normalization; any other difference (a
    different symbol, a separated or different sign, parentheses, a fraction
    separator, a different short unit or scale word) is unsafe to swallow and
    is left as a visible, reviewable DELETE/ADD pair. A false-negative
    cosmetic pair is safer than a swallowed change.
    """
    if not (
        _NUMERIC_BEARING_RE.search(old_raw)
        or _NUMERIC_BEARING_RE.search(new_raw)
    ):
        return True
    if _numeric_signature(old_raw) != _numeric_signature(new_raw):
        return False
    return _layout_normalize(old_raw) == _layout_normalize(new_raw)


def _element_text(element: dict[str, Any] | None) -> str:
    if not isinstance(element, dict):
        return ""
    for key in ("comparison_text", "normalized_text", "text"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _overlap(old_tokens: set[str], new_tokens: set[str]) -> tuple[float, float]:
    if not old_tokens or not new_tokens:
        return 0.0, 0.0
    shared = len(old_tokens & new_tokens)
    jaccard = shared / len(old_tokens | new_tokens)
    containment = shared / min(len(old_tokens), len(new_tokens))
    return jaccard, containment


def _length_compatible(old_tokens: set[str], new_tokens: set[str]) -> bool:
    """True when both sides are plausibly the same text resegmented.

    Containment alone is NOT enough: a short genuine deletion whose words all
    occur inside a large inserted article would score containment 1.0 and be
    silently consumed as a 'pair'. Require comparable token counts so only
    real split/merge twins qualify.
    """
    if not old_tokens or not new_tokens:
        return False
    smaller, larger = sorted((len(old_tokens), len(new_tokens)))
    return smaller / larger >= MIN_LENGTH_RATIO


def reconcile_residuals(
    deleted_candidates: list[dict[str, Any]],
    added_candidates: list[dict[str, Any]],
    *,
    min_score: float = 0.80,
    min_margin: float = 0.05,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedy reciprocal best-buddy pairing of residual delete/add candidates.

    Returns (pairs, leftover_deleted, leftover_added).
    """
    old_sets = [(c, _tokens(_element_text(c.get("old")))) for c in deleted_candidates]
    new_sets = [(c, _tokens(_element_text(c.get("new")))) for c in added_candidates]

    # Conservative gate BEFORE any near-twin scoring: a NUMBER-bearing pair is
    # only cosmetic when its RAW evidence is genuinely equal under layout-only
    # normalization. Ordered numeric evidence (sign/operator/unit, order and
    # repetition) plus the broader raw punctuation stream keep every symbol,
    # separated sign, short unit and scale difference visible as DELETE/ADD.
    old_raw = [_raw_text(c.get("old")) for c in deleted_candidates]
    new_raw = [_raw_text(c.get("new")) for c in added_candidates]

    scored: list[tuple[float, float, int, int]] = []
    for oi, (_, otoks) in enumerate(old_sets):
        for ni, (_, ntoks) in enumerate(new_sets):
            if not _quantitative_compatible(old_raw[oi], new_raw[ni]):
                continue
            jaccard, containment = _overlap(otoks, ntoks)
            score = max(
                jaccard,
                containment * 0.95 if _length_compatible(otoks, ntoks) else 0.0,
            )
            if score >= min_score:
                scored.append((score, jaccard, oi, ni))
    scored.sort(key=lambda item: (-item[0], item[2], item[3]))

    by_old: dict[int, list[tuple[float, int]]] = {}
    by_new: dict[int, list[tuple[float, int]]] = {}
    for score, _, oi, ni in scored:
        by_old.setdefault(oi, []).append((score, ni))
        by_new.setdefault(ni, []).append((score, oi))

    used_old: set[int] = set()
    used_new: set[int] = set()
    pairs: list[dict[str, Any]] = []

    for score, jaccard, oi, ni in scored:
        if oi in used_old or ni in used_new:
            continue
        old_ranked = sorted(by_old[oi], reverse=True)
        new_ranked = sorted(by_new[ni], reverse=True)
        if old_ranked[0][1] != ni or new_ranked[0][1] != oi:
            continue
        margin = min(
            score - (old_ranked[1][0] if len(old_ranked) > 1 else 0.0),
            score - (new_ranked[1][0] if len(new_ranked) > 1 else 0.0),
        )
        if margin < min_margin:
            continue

        old_candidate = old_sets[oi][0]
        new_candidate = new_sets[ni][0]
        old_element = old_candidate.get("old")
        new_element = new_candidate.get("new")
        identical = _element_text(old_element) == _element_text(new_element)
        pairs.append(
            {
                "candidate_id": f"RESEG-{len(pairs) + 1:06d}",
                "candidate_type": "RESEGMENTATION_REVIEW",
                "old": old_element,
                "new": new_element,
                "similarity": round(score, 6),
                "needs_review": True,
                "review_reasons": [
                    "exact_duplicate_assignment"
                    if identical
                    else "resegmentation_marker_or_hyphenation_artifact"
                ],
                "token_evidence": {
                    "jaccard": round(jaccard, 6),
                    "margin": round(margin, 6),
                },
                "source_deleted_candidate_id": old_candidate.get("candidate_id"),
                "source_added_candidate_id": new_candidate.get("candidate_id"),
                "movement_classification": None,
                "token_diff": None,
            }
        )
        used_old.add(oi)
        used_new.add(ni)

    leftover_deleted = [
        c for i, c in enumerate(deleted_candidates) if i not in used_old
    ]
    leftover_added = [
        c for i, c in enumerate(added_candidates) if i not in used_new
    ]
    return pairs, leftover_deleted, leftover_added
