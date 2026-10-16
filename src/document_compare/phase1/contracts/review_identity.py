"""Structural-evidence route identity shared by Stages 11, 12, and 14.

Stage 9 emits evidence-only review records (``movement_review``,
``structural_split_merge_review``, ``structural_boundary_review``,
``resegmentation_review``). Some of these records refer back to an original
comparison candidate through their ``candidate_id`` field. That source
candidate and the evidence must never share a routing identity, or Stage 14's
duplicate-identity validation stops the pipeline.

This helper is the single source of truth for that split:

- The route identity is the evidence's own ``review_id`` when present.
- Legacy evidence-only records without a ``review_id`` fall back to their own
  ``candidate_id``.
- The original comparison-candidate link is preserved separately as
  ``source_candidate_id`` when the record carries one distinct from the
  evidence identity.
- A record with neither field cannot produce a usable route and raises
  ``ValueError`` instead of silently yielding ``None``.
"""
from __future__ import annotations

from typing import Any


def structural_route_identity(item: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"candidate_id": ..., "source_candidate_id": ...}`` for one
    structural evidence record.

    ``candidate_id`` is the evidence route identity; ``source_candidate_id`` is
    the original candidate link, or ``None`` when there is no separate source.
    """
    if not isinstance(item, dict):
        raise ValueError(
            "Structural evidence record must be a mapping; "
            f"got {type(item).__name__}."
        )

    review_id = item.get("review_id")
    evidence_candidate_id = item.get("candidate_id")

    if review_id:
        return {
            "candidate_id": str(review_id),
            "source_candidate_id": (
                str(evidence_candidate_id) if evidence_candidate_id else None
            ),
        }
    if evidence_candidate_id:
        return {
            "candidate_id": str(evidence_candidate_id),
            "source_candidate_id": None,
        }

    raise ValueError(
        "Structural evidence record has neither review_id nor candidate_id; "
        "cannot build a usable route identity."
    )
