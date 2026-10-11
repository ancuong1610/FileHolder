"""Residual resegmentation reconciliation (cosmetic-noise suppressor).

Pairs leftover DELETED/ADDED candidates whose texts are near-twins under
token overlap (DI paragraph splits/merges, list-marker prefix changes,
hyphenation artifacts). Pairs are surfaced as RESEGMENTATION_REVIEW evidence;
they are never auto-accepted as unchanged, so no real change can be hidden.
"""
from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)

# ponytail: split/merge twins stay within ~2x token count; stricter ratio
# would need real split-aware pairing if BU reports missed resegmentations.
MIN_LENGTH_RATIO = 0.5


def _tokens(text: Any) -> set[str]:
    return set(TOKEN_RE.findall(str(text or "").casefold()))


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

    scored: list[tuple[float, float, int, int]] = []
    for oi, (_, otoks) in enumerate(old_sets):
        for ni, (_, ntoks) in enumerate(new_sets):
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
