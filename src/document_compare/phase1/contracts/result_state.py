"""Normalized comparison result states for new and legacy artifacts.

Canonical `supported` / `uncertain` / `non_semantic` is a presentation and
consumer normalization. Legacy machine fields remain authoritative for their own
meaning: ``resolution.status == "accepted"`` still means deterministic
acceptance, and legacy ``review``/``needs_review``/``HUMAN_REVIEW`` records are
readable as uncertainty. Nothing here rewrites a saved artifact.

The module is pure and shared by the canonical producer, bounded reader,
API summary and report model so those consumers cannot drift into parallel
uncertainty vocabularies.
"""
from __future__ import annotations

from typing import Any, Iterable

SUPPORTED = "supported"
UNCERTAIN = "uncertain"
NON_SEMANTIC = "non_semantic"
RESULT_STATES = (SUPPORTED, UNCERTAIN, NON_SEMANTIC)

SUPPORTED_STATUSES = frozenset({"accepted"})
NON_SEMANTIC_STATUSES = frozenset({
    "non_semantic",
    "suppressed_artifact",
    "structural_only",
})
UNCERTAIN_ROUTES = frozenset({"UNCERTAIN_RESULT", "HUMAN_REVIEW"})


def derive_result_state(status: Any, needs_review: Any = None) -> str:
    """Derive one normalized state from legacy resolution fields."""
    value = str(status or "").strip()
    if value in SUPPORTED_STATUSES and not bool(needs_review):
        return SUPPORTED
    if value in NON_SEMANTIC_STATUSES:
        return NON_SEMANTIC
    return UNCERTAIN


def resolution_result_state(resolution: Any) -> str:
    """Return a conservative normalized state for one resolution.

    The legacy deterministic fields stay authoritative. An explicit
    ``result_state`` is accepted only when it agrees with the state derived from
    ``status``/``needs_review``; a stale or contradictory explicit value can
    never promote a review/unknown record to ``supported``.
    """
    if not isinstance(resolution, dict):
        return UNCERTAIN
    derived = derive_result_state(
        resolution.get("status"), resolution.get("needs_review")
    )
    explicit = resolution.get("result_state")
    if explicit in RESULT_STATES and explicit == derived:
        return str(explicit)
    return derived


def record_result_state(record: Any, *, kind: str | None = None) -> str:
    """Return the normalized state of a change/table/review record.

    A review record is always uncertain even if a legacy producer omitted
    ``needs_review`` or wrote an inconsistent status.
    """
    if kind == "review":
        return UNCERTAIN
    if not isinstance(record, dict):
        return UNCERTAIN
    return resolution_result_state(record.get("resolution"))


def is_supported(record: Any, *, kind: str | None = None) -> bool:
    return record_result_state(record, kind=kind) == SUPPORTED


def uncertainty_key(record: Any, *, kind: str | None = None) -> tuple[str, str]:
    """Stable identity for one uncertainty case.

    A review linked to a change/table uses the linked record identity so the
    same uncertainty is never counted twice. Evidence-only reviews use their
    own evidence/candidate/review identity.
    """
    if not isinstance(record, dict):
        return ("unknown", "")
    # A residual proposal group is one canonical uncertainty identity.  Both
    # member changes and their grouped explanation resolve to the same key so
    # the user sees one explanation, never concatenated queue aliases.
    proposal_group = record.get("proposal_group_id")
    if not isinstance(proposal_group, str) or not proposal_group:
        resolution = record.get("resolution")
        if isinstance(resolution, dict):
            value = resolution.get("proposal_group_id")
            if isinstance(value, str) and value:
                proposal_group = value
    if isinstance(proposal_group, str) and proposal_group:
        return ("proposal_group", proposal_group)
    if kind == "review":
        change_id = record.get("change_id")
        if isinstance(change_id, str) and change_id:
            return ("change", change_id)
        table_id = record.get("table_change_id")
        if isinstance(table_id, str) and table_id:
            return ("table", table_id)
        for key in ("source_evidence_id", "source_candidate_id", "review_id"):
            value = record.get(key)
            if isinstance(value, str) and value:
                return ("review", value)
        return ("review", "")
    if kind == "table":
        value = record.get("table_change_id")
        return ("table", value if isinstance(value, str) else "")
    value = record.get("change_id")
    return ("change", value if isinstance(value, str) else "")


def result_state_counts(
    changes: Iterable[Any],
    tables: Iterable[Any],
    reviews: Iterable[Any],
    non_semantic_events: Iterable[Any] = (),
) -> dict[str, int]:
    """Count distinct canonical records by normalized state.

    Linked review records do not double-count their change/table. Evidence-only
    uncertainty is counted once by its own identity.
    """
    counts = {state: 0 for state in RESULT_STATES}
    seen_uncertain: set[tuple[str, str]] = set()
    for item in changes:
        state = record_result_state(item, kind="change")
        key = uncertainty_key(item, kind="change")
        if state == UNCERTAIN and key in seen_uncertain:
            continue
        counts[state] += 1
        if state == UNCERTAIN:
            seen_uncertain.add(key)
    for item in tables:
        state = record_result_state(item, kind="table")
        key = uncertainty_key(item, kind="table")
        if state == UNCERTAIN and key in seen_uncertain:
            continue
        counts[state] += 1
        if state == UNCERTAIN:
            seen_uncertain.add(key)
    for item in non_semantic_events:
        counts[NON_SEMANTIC] += 1
    for item in reviews:
        key = uncertainty_key(item, kind="review")
        if key in seen_uncertain:
            continue
        seen_uncertain.add(key)
        counts[UNCERTAIN] += 1
    return counts


def canonical_uncertain_records(
    changes: Iterable[dict[str, Any]],
    tables: Iterable[dict[str, Any]],
    reviews: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one clean uncertainty list, preserving source records.

    Each item is ``{"kind", "record", "linked"}`` where ``linked`` names a
    canonical change/table when the review carries that reference. No review is
    dropped and no canonical uncertain change is duplicated by a linked review.
    """
    changes_by_id = {
        item.get("change_id"): item
        for item in changes
        if isinstance(item, dict) and isinstance(item.get("change_id"), str)
    }
    tables_by_id = {
        item.get("table_change_id"): item
        for item in tables
        if isinstance(item, dict) and isinstance(item.get("table_change_id"), str)
    }
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for review in reviews:
        if not isinstance(review, dict):
            continue
        key = uncertainty_key(review, kind="review")
        if key in seen:
            continue
        seen.add(key)
        linked = None
        change_id = review.get("change_id")
        table_id = review.get("table_change_id")
        if isinstance(change_id, str) and change_id in changes_by_id:
            linked = changes_by_id[change_id]
        elif isinstance(table_id, str) and table_id in tables_by_id:
            linked = tables_by_id[table_id]
        records.append({"kind": "review", "record": review, "linked": linked})
    for change in changes:
        if not isinstance(change, dict):
            continue
        if record_result_state(change, kind="change") != UNCERTAIN:
            continue
        key = uncertainty_key(change, kind="change")
        if key in seen:
            continue
        seen.add(key)
        records.append({"kind": "change", "record": change, "linked": change})
    for table in tables:
        if not isinstance(table, dict):
            continue
        if record_result_state(table, kind="table") != UNCERTAIN:
            continue
        key = uncertainty_key(table, kind="table")
        if key in seen:
            continue
        seen.add(key)
        records.append({"kind": "table", "record": table, "linked": table})
    return records
