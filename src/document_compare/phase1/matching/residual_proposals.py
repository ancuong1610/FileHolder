"""Bounded residual counterpart proposals (Gate A).

A residual deletion/addition must not be accepted merely because lexical
matching did not find a counterpart.  This module records *possible*
counterparts using the existing element type, exact-neighbour interval,
provisional anchor context and lexical indexing evidence.

Hard boundaries:

- at most five proposals per residual element and 1,000 pair checks per job;
- deterministic order and stable proposal/group ids;
- a proposal is evidence only: it never consumes a candidate and never becomes
  a committed match;
- examined, unsearched and budget-exhausted coverage is explicit.  Absence of
  a lexical pair is never proof that no counterpart exists.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

POLICY_VERSION = "gateA-1"
MAX_PROPOSALS_PER_RESIDUAL = 5
MAX_PAIR_CHECKS = 1000
MIN_TOKEN_LENGTH = 3

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _side(candidate: dict[str, Any], side: str) -> dict[str, Any] | None:
    value = candidate.get(side)
    return value if isinstance(value, dict) else None


def _element_text(element: dict[str, Any] | None) -> str:
    if not isinstance(element, dict):
        return ""
    for key in ("comparison_text", "normalized_text", "text"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _element_type(element: dict[str, Any] | None) -> str:
    return str((element or {}).get("element_type") or "")


def _order(element: dict[str, Any] | None) -> int | None:
    value = (element or {}).get("reading_order")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _context_keys(element: dict[str, Any] | None) -> set[str]:
    context = (element or {}).get("provisional_anchor_context") or {}
    return {
        str(key)
        for key in context.get("recent_anchor_keys") or []
        if isinstance(key, str) and key
    }


def _tokens(element: dict[str, Any] | None) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(_element_text(element))
        if len(token) >= MIN_TOKEN_LENGTH
    }


def _sort_key(candidate: dict[str, Any], side: str) -> tuple[int, str]:
    order = _order(_side(candidate, side))
    return (
        order if order is not None else 10**12,
        str(candidate.get("candidate_id") or ""),
    )


def _index(
    candidates: list[dict[str, Any]],
    side: str,
) -> tuple[dict[tuple[str, str], set[int]], dict[tuple[str, str], set[int]]]:
    token_postings: dict[tuple[str, str], set[int]] = defaultdict(set)
    context_postings: dict[tuple[str, str], set[int]] = defaultdict(set)
    for index, candidate in enumerate(candidates):
        element = _side(candidate, side)
        element_type = _element_type(element)
        for token in _tokens(element):
            token_postings[(element_type, token)].add(index)
        for key in _context_keys(element):
            context_postings[(element_type, key)].add(index)
    return token_postings, context_postings


def _anchors(matched_pairs: list[dict[str, Any]] | None) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    for candidate in matched_pairs or []:
        old = _side(candidate, "old")
        new = _side(candidate, "new")
        old_order, new_order = _order(old), _order(new)
        if old_order is not None and new_order is not None:
            anchors.append((old_order, new_order))
    return sorted(set(anchors))


def _interval_ids_for_deleted(
    element: dict[str, Any] | None,
    anchors: list[tuple[int, int]],
    added: list[dict[str, Any]],
) -> set[int]:
    order = _order(element)
    if order is None or not anchors:
        return set()
    lower = [pair for pair in anchors if pair[0] < order]
    upper = [pair for pair in anchors if pair[0] > order]
    if not lower or not upper:
        return set()
    low = max(lower, key=lambda pair: pair[0])[1]
    high = min(upper, key=lambda pair: pair[0])[1]
    if high <= low:
        return set()
    return {
        index
        for index, candidate in enumerate(added)
        if (new_order := _order(_side(candidate, "new"))) is not None
        and low < new_order < high
    }


def _interval_ids_for_added(
    element: dict[str, Any] | None,
    anchors: list[tuple[int, int]],
    deleted: list[dict[str, Any]],
) -> set[int]:
    order = _order(element)
    if order is None or not anchors:
        return set()
    lower = [pair for pair in anchors if pair[1] < order]
    upper = [pair for pair in anchors if pair[1] > order]
    if not lower or not upper:
        return set()
    low = max(lower, key=lambda pair: pair[1])[0]
    high = min(upper, key=lambda pair: pair[1])[0]
    if high <= low:
        return set()
    return {
        index
        for index, candidate in enumerate(deleted)
        if (old_order := _order(_side(candidate, "old"))) is not None
        and low < old_order < high
    }


def _pool(
    element: dict[str, Any] | None,
    opposite: list[dict[str, Any]],
    *,
    side: str,
    token_postings: dict[tuple[str, str], set[int]],
    context_postings: dict[tuple[str, str], set[int]],
    interval: set[int],
) -> set[int]:
    """Bounded candidate locations from tokens, context and the interval."""
    locations: set[int] = set(interval)
    element_type = _element_type(element)
    tokens = _tokens(element)
    # Rare tokens first: deterministic and avoids common-word flooding.
    ordered_tokens = sorted(
        tokens,
        key=lambda token: (
            len(token_postings.get((element_type, token), set())),
            token,
        ),
    )
    for token in ordered_tokens[:8]:
        locations |= token_postings.get((element_type, token), set())
    for key in sorted(_context_keys(element)):
        locations |= context_postings.get((element_type, key), set())
    return locations


def _score(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> float:
    old_tokens, new_tokens = _tokens(old), _tokens(new)
    shared = old_tokens & new_tokens
    if old_tokens or new_tokens:
        token_score = 2 * len(shared) / (len(old_tokens) + len(new_tokens))
    else:
        token_score = 0.0
    old_text, new_text = _element_text(old), _element_text(new)
    length_score = (
        min(len(old_text), len(new_text)) / max(len(old_text), len(new_text), 1)
        if old_text or new_text else 0.0
    )
    old_context, new_context = _context_keys(old), _context_keys(new)
    context_denominator = max(1, min(len(old_context), len(new_context)))
    context_score = (
        len(old_context & new_context) / context_denominator
        if old_context and new_context else 0.0
    )
    return round(
        0.60 * token_score + 0.15 * length_score + 0.25 * context_score, 6
    )


def _proposal_id(deleted_id: str, added_id: str) -> str:
    raw = f"{deleted_id}|{added_id}".encode("utf-8")
    return "SEMANTIC-PROP-" + hashlib.sha256(raw).hexdigest()[:16]


def _group_sort_key(
    member_ids: set[str],
    by_id: dict[str, dict[str, Any]],
) -> tuple[int, tuple[str, ...]]:
    orders = [
        order
        for member_id in member_ids
        if (order := _order(_side(by_id[member_id], _side_name(by_id[member_id])))) is not None
    ]
    return (min(orders) if orders else 10**12, tuple(sorted(member_ids)))


def _side_name(candidate: dict[str, Any]) -> str:
    return "old" if isinstance(candidate.get("old"), dict) else "new"


def build_residual_proposals(
    deleted: list[dict[str, Any]],
    added: list[dict[str, Any]],
    *,
    matched_pairs: list[dict[str, Any]] | None = None,
    max_proposals: int = MAX_PROPOSALS_PER_RESIDUAL,
    max_pair_checks: int = MAX_PAIR_CHECKS,
) -> dict[str, Any]:
    """Return bounded proposals, groups and explicit coverage.

    No input candidate is mutated; proposals carry their own stable ids and
    ``consumes_candidates == False``.
    """
    deleted = [item for item in deleted or [] if isinstance(item, dict)]
    added = [item for item in added or [] if isinstance(item, dict)]
    max_proposals = max(0, min(int(max_proposals), MAX_PROPOSALS_PER_RESIDUAL))
    max_pair_checks = max(0, min(int(max_pair_checks), MAX_PAIR_CHECKS))
    checks_left = max_pair_checks
    budget_exhausted = False
    # One deterministic score per old/new candidate pair: ranking and proposal
    # materialization reuse it so the reported budget also bounds real work.
    score_cache: dict[tuple[str, str], float] = {}

    def pair_key(
        deleted_candidate: dict[str, Any],
        added_candidate: dict[str, Any],
    ) -> tuple[str, str]:
        return (
            str(deleted_candidate.get("candidate_id") or ""),
            str(added_candidate.get("candidate_id") or ""),
        )

    anchors = _anchors(matched_pairs)
    deleted_token_index, deleted_context_index = _index(deleted, "old")
    added_token_index, added_context_index = _index(added, "new")

    interval_added: dict[str, set[int]] = {
        str(candidate.get("candidate_id")): _interval_ids_for_deleted(
            _side(candidate, "old"), anchors, added
        )
        for candidate in deleted
    }
    interval_deleted: dict[str, set[int]] = {
        str(candidate.get("candidate_id")): _interval_ids_for_added(
            _side(candidate, "new"), anchors, deleted
        )
        for candidate in added
    }

    # Deterministic processing order: deleted side then added side, each by
    # reading order and stable candidate id.
    work: list[tuple[str, dict[str, Any]]] = [
        ("deleted", candidate)
        for candidate in sorted(deleted, key=lambda item: _sort_key(item, "old"))
    ] + [
        ("added", candidate)
        for candidate in sorted(added, key=lambda item: _sort_key(item, "new"))
    ]

    candidate_coverage: dict[str, dict[str, Any]] = {}
    ranked_ids: dict[str, list[str]] = {}
    interval_pairs: set[tuple[str, str]] = set()

    for side, candidate in work:
        candidate_id = str(candidate.get("candidate_id") or "")
        element = _side(candidate, "old" if side == "deleted" else "new")
        opposite = added if side == "deleted" else deleted
        if side == "deleted":
            opposite_element = "new"
            token_postings, context_postings = added_token_index, added_context_index
            interval = interval_added.get(candidate_id, set())
        else:
            opposite_element = "old"
            token_postings, context_postings = deleted_token_index, deleted_context_index
            interval = interval_deleted.get(candidate_id, set())

        compatible = [
            index
            for index, other in enumerate(opposite)
            if _element_type(_side(other, opposite_element)) == _element_type(element)
        ]
        compatible_set = set(compatible)
        if not compatible:
            candidate_coverage[candidate_id] = {
                "status": "no_opposite",
                "examined": 0,
                "unsearched": 0,
                "opposite_residuals": 0,
                "budget_exhausted": False,
                "limitations": [],
            }
            continue

        locations = _pool(
            element,
            opposite,
            side=side,
            token_postings=token_postings,
            context_postings=context_postings,
            interval=interval,
        )
        locations &= compatible_set
        ordered_locations = sorted(
            locations,
            key=lambda index: _sort_key(opposite[index], opposite_element),
        )

        if not ordered_locations:
            candidate_coverage[candidate_id] = {
                "status": "partial",
                "examined": 0,
                "unsearched": len(compatible),
                "opposite_residuals": len(compatible),
                "budget_exhausted": False,
                "limitations": ["no_indexed_counterpart_signal"],
            }
            continue

        scored = []
        examined = 0
        other_id = lambda index: str(opposite[index].get("candidate_id") or "")
        for index in ordered_locations:
            other = opposite[index]
            if side == "deleted":
                deleted_candidate, added_candidate = candidate, other
            else:
                deleted_candidate, added_candidate = other, candidate
            key = pair_key(deleted_candidate, added_candidate)
            if key not in score_cache:
                if checks_left <= 0:
                    budget_exhausted = True
                    break
                score_cache[key] = _score(
                    _side(deleted_candidate, "old"),
                    _side(added_candidate, "new"),
                )
                checks_left -= 1
            scored.append((score_cache[key], index))
            examined += 1
        scored.sort(
            key=lambda item: (
                -item[0],
                _sort_key(opposite[item[1]], opposite_element),
            )
        )
        chosen = scored[:max_proposals]
        ranked_ids[candidate_id] = [other_id(index) for _, index in chosen]

        # Record unique-interval pairs for classification.
        if len(interval) == 1 and side == "deleted":
            interval_id = other_id(next(iter(interval)))
            reverse = interval_deleted.get(interval_id, set())
            if reverse == {deleted.index(candidate)}:
                interval_pairs.add((candidate_id, interval_id))
        elif len(interval) == 1 and side == "added":
            interval_id = other_id(next(iter(interval)))
            reverse = interval_added.get(interval_id, set())
            if reverse == {added.index(candidate)}:
                interval_pairs.add((interval_id, candidate_id))

        # Compatible opposite residuals outside the indexed pool were never
        # examined: they count as unsearched even when every indexed location
        # was inspected.  "Complete" therefore means every compatible
        # counterpart was actually examined, not merely every indexed one.
        unexamined_locations = len(ordered_locations) - examined
        unindexed_compatible = len(compatible) - len(ordered_locations)
        unsearched = len(compatible) - examined
        limitations = []
        if unexamined_locations:
            limitations.append("budget_exhausted")
        if unindexed_compatible:
            limitations.append("compatible_counterparts_outside_index")
        candidate_coverage[candidate_id] = {
            "status": "partial" if unsearched else "complete",
            "examined": examined,
            "unsearched": unsearched,
            "opposite_residuals": len(compatible),
            "budget_exhausted": bool(unexamined_locations),
            "limitations": limitations,
        }

    # Materialize proposals with deterministic order and stable ids.
    proposals: list[dict[str, Any]] = []
    by_deleted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_added: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for deleted_candidate in sorted(deleted, key=lambda item: _sort_key(item, "old")):
        deleted_id = str(deleted_candidate.get("candidate_id") or "")
        for added_id in ranked_ids.get(deleted_id, []):
            added_candidate = next(
                (
                    item for item in added
                    if str(item.get("candidate_id") or "") == added_id
                ),
                None,
            )
            if added_candidate is None:
                continue
            proposal = {
                "proposal_id": _proposal_id(deleted_id, added_id),
                "deleted_candidate_id": deleted_id,
                "added_candidate_id": added_id,
                "old_element_id": (_side(deleted_candidate, "old") or {}).get("element_id"),
                "new_element_id": (_side(added_candidate, "new") or {}).get("element_id"),
                "element_type": _element_type(_side(deleted_candidate, "old")),
                "score": None,
                "shared_anchor_keys": sorted(
                    _context_keys(_side(deleted_candidate, "old"))
                    & _context_keys(_side(added_candidate, "new"))
                ),
                "classification": "plausible_counterpart",
                "consumes_candidates": False,
            }
            # Reuse the cached rank score; materialization must not spend
            # additional pair-check budget or recompute text work.
            proposal["score"] = score_cache[pair_key(deleted_candidate, added_candidate)]
            proposals.append(proposal)
            by_deleted[deleted_id].append(proposal)
            by_added[added_id].append(proposal)

    proposals.sort(key=lambda item: (item["deleted_candidate_id"], item["added_candidate_id"]))

    # If an added residual was never reached as a deleted-side target, build
    # its proposals from the symmetric ranked ids.
    for added_candidate in sorted(added, key=lambda item: _sort_key(item, "new")):
        added_id = str(added_candidate.get("candidate_id") or "")
        if by_added.get(added_id):
            continue
        for deleted_id in ranked_ids.get(added_id, []):
            deleted_candidate = next(
                (
                    item for item in deleted
                    if str(item.get("candidate_id") or "") == deleted_id
                ),
                None,
            )
            if deleted_candidate is None:
                continue
            proposal = {
                "proposal_id": _proposal_id(deleted_id, added_id),
                "deleted_candidate_id": deleted_id,
                "added_candidate_id": added_id,
                "old_element_id": (_side(deleted_candidate, "old") or {}).get("element_id"),
                "new_element_id": (_side(added_candidate, "new") or {}).get("element_id"),
                "element_type": _element_type(_side(added_candidate, "new")),
                "score": score_cache[pair_key(deleted_candidate, added_candidate)],
                "shared_anchor_keys": sorted(
                    _context_keys(_side(deleted_candidate, "old"))
                    & _context_keys(_side(added_candidate, "new"))
                ),
                "classification": "plausible_counterpart",
                "consumes_candidates": False,
            }
            proposals.append(proposal)
            by_deleted[deleted_id].append(proposal)
            by_added[added_id].append(proposal)

    # Enforce the per-element cap after the symmetric pass.  A proposal may be
    # selected from either side; every residual keeps at most ``max_proposals``.
    def _kept_pairs(by_key: dict[str, list[dict[str, Any]]], other_key: str) -> set[tuple[str, str]]:
        kept: set[tuple[str, str]] = set()
        for _key, values in by_key.items():
            ordered = sorted(
                values,
                key=lambda item: (-float(item.get("score") or 0.0), item[other_key]),
            )
            for proposal in ordered[:max_proposals]:
                kept.add(
                    (
                        proposal["deleted_candidate_id"],
                        proposal["added_candidate_id"],
                    )
                )
        return kept

    kept = _kept_pairs(by_deleted, "added_candidate_id") & _kept_pairs(
        by_added, "deleted_candidate_id"
    )
    proposals = [
        proposal for proposal in proposals
        if (
            proposal["deleted_candidate_id"],
            proposal["added_candidate_id"],
        ) in kept
    ]
    proposals = list({
        (proposal["deleted_candidate_id"], proposal["added_candidate_id"]): proposal
        for proposal in proposals
    }.values())
    proposals.sort(key=lambda item: (item["deleted_candidate_id"], item["added_candidate_id"]))
    by_deleted = defaultdict(list)
    by_added = defaultdict(list)
    for proposal in proposals:
        by_deleted[proposal["deleted_candidate_id"]].append(proposal)
        by_added[proposal["added_candidate_id"]].append(proposal)

    # The symmetric cap intersection can prune every proposal for a node that
    # did have plausible indexed signals.  That is retained evidence, not proof
    # of absence: mark the coverage partial so routing cannot accept it.
    for candidate_id, coverage in candidate_coverage.items():
        ranked_count = len(ranked_ids.get(candidate_id, []))
        retained_count = len(by_deleted.get(candidate_id, [])) + len(
            by_added.get(candidate_id, [])
        )
        coverage["retained_proposals"] = retained_count
        coverage["unretained_plausible_counterparts"] = max(
            0, ranked_count - retained_count
        )
        if ranked_count and retained_count == 0:
            coverage["status"] = "partial"
            coverage["limitations"] = sorted(
                set(coverage.get("limitations") or [])
                | {"proposal_cap_pruned_counterpart"}
            )

    # Classification uses the complete symmetric proposal sets.
    for proposal in proposals:
        deleted_id = proposal["deleted_candidate_id"]
        added_id = proposal["added_candidate_id"]
        old_element = _side(
            next(item for item in deleted if str(item.get("candidate_id")) == deleted_id),
            "old",
        )
        new_element = _side(
            next(item for item in added if str(item.get("candidate_id")) == added_id),
            "new",
        )
        old_context, new_context = _context_keys(old_element), _context_keys(new_element)
        if old_context and new_context and not (old_context & new_context):
            proposal["classification"] = "cross_section_conflict"
        elif (deleted_id, added_id) in interval_pairs:
            proposal["classification"] = "unique_residual_interval"
        elif len(by_deleted.get(deleted_id, [])) > 1 or len(by_added.get(added_id, [])) > 1:
            proposal["classification"] = "competing_counterparts"
        elif old_context and new_context and (old_context & new_context):
            proposal["classification"] = "shared_context"
        else:
            proposal["classification"] = "plausible_counterpart"

    # Connected components: one canonical user-facing group per proposal
    # component; partial/unsearched residuals become singleton groups so their
    # uncertainty is still explained once.
    by_id: dict[str, dict[str, Any]] = {
        str(item.get("candidate_id")): item for item in deleted + added
    }
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for proposal in proposals:
        union(proposal["deleted_candidate_id"], proposal["added_candidate_id"])
    for candidate_id, coverage in candidate_coverage.items():
        if coverage["status"] == "partial":
            find(candidate_id)

    components: dict[str, set[str]] = defaultdict(set)
    for node in list(parent):
        components[find(node)].add(node)

    ordered_components = sorted(
        components.values(),
        key=lambda members: _group_sort_key(members, by_id),
    )
    candidate_groups: dict[str, str] = {}
    groups: list[dict[str, Any]] = []
    for index, members in enumerate(ordered_components, start=1):
        group_id = f"RESIDUAL-GROUP-{index:06d}"
        member_proposals = [
            proposal for proposal in proposals
            if proposal["deleted_candidate_id"] in members
            and proposal["added_candidate_id"] in members
        ]
        deleted_ids = sorted({
            proposal["deleted_candidate_id"] for proposal in member_proposals
        } | {member for member in members if isinstance(by_id.get(member, {}).get("old"), dict)})
        added_ids = sorted({
            proposal["added_candidate_id"] for proposal in member_proposals
        } | {member for member in members if isinstance(by_id.get(member, {}).get("new"), dict)})
        for member in members:
            candidate_groups[member] = group_id
        groups.append({
            "group_id": group_id,
            "deleted_candidate_ids": deleted_ids,
            "added_candidate_ids": added_ids,
            "old_element_ids": sorted({
                str((_side(by_id[member], "old") or {}).get("element_id"))
                for member in members
                if isinstance(by_id.get(member, {}).get("old"), dict)
                and (_side(by_id[member], "old") or {}).get("element_id")
            }),
            "new_element_ids": sorted({
                str((_side(by_id[member], "new") or {}).get("element_id"))
                for member in members
                if isinstance(by_id.get(member, {}).get("new"), dict)
                and (_side(by_id[member], "new") or {}).get("element_id")
            }),
            "proposal_ids": sorted(
                proposal["proposal_id"] for proposal in member_proposals
            ),
            "proposals": member_proposals,
            "correspondence": "proposed" if member_proposals else "unresolved",
            "limitations": sorted({
                limitation
                for member in members
                for limitation in candidate_coverage.get(member, {}).get("limitations", [])
            }),
            "consumes_candidates": False,
        })

    unsearched_elements = sum(
        1
        for coverage in candidate_coverage.values()
        if coverage["unsearched"] > 0
        or coverage.get("unretained_plausible_counterparts", 0) > 0
    )
    total_opposite = sum(
        coverage["opposite_residuals"] for coverage in candidate_coverage.values()
    )
    return {
        "policy_version": POLICY_VERSION,
        "proposals": proposals,
        "groups": groups,
        "candidate_coverage": candidate_coverage,
        "candidate_groups": candidate_groups,
        "coverage": {
            "policy_version": POLICY_VERSION,
            "max_proposals_per_residual": max_proposals,
            "max_pair_checks": max_pair_checks,
            "pair_checks": max_pair_checks - checks_left,
            "budget_exhausted": budget_exhausted,
            "examined_residual_elements": sum(
                1 for coverage in candidate_coverage.values() if coverage["examined"] > 0
            ),
            "unsearched_residual_elements": unsearched_elements,
            "opposite_residuals": total_opposite,
        },
    }
