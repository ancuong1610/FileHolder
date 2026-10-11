"""s14 - canonical generic comparison result with optional crr3 domain metadata.

Auto-converted from 14_build_final_comparison_result_v2.ipynb (code cells only),
generalized: generic acceptance is domain-agnostic and byte-for-byte
independent of mode. crr3 verification is additive metadata attached from the
s11 ``domain_verification.records``; it never alters a generic change,
resolution, or review entry.

s13 LLM decisions are advisory recommendations preserved for traceability
only; deterministic route rules decide every resolution. No LLM outcome can
accept, suppress, or mark structural-only in the BU-facing canonical result.

Imports are side-effect free: no file reads and no pipeline execution outside
``main()``/``build_comparison_result()``.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from crr_compare.common import REPO_ROOT, find_project_root, output_dir
from crr_compare.evaluate import build_outcome_migration
from crr_compare.route_identity import structural_route_identity
from crr_compare.validation import require

STEP14_VERSION = "3.0.0-generic-canonical-result"

EXPECTED_STEP9_STAGE = "movement_and_structural_reconciliation_evidence"
EXPECTED_STEP10_STAGE = "logical_table_comparison_with_provisional_context"
EXPECTED_STEP11_STAGE = "deterministic_review_routing"
EXPECTED_STEP13_STAGE = "optional_llm_special_case_evaluation"

# Step-11 deterministic case types whose diffs are proven non-semantic.
DETERMINISTIC_SUPPRESSED = {
    "possible_extraction_word_split",
    "tokenization_neutral_difference",
    # A hyphen/whitespace-position-only diff is a formatting/OCR line-wrap
    # artifact ONLY when the raw source proves a real line break. The wrap
    # route must therefore also carry the source-provenance reason; a stale or
    # unproven route of this case type stays in review (see _resolve).
    "hyphenation_wrap_artifact",
}
# Structural/metadata-only: real difference, but not legal-text meaning
# (amendment margin markers like M5/C12).
DETERMINISTIC_STRUCTURAL = {
    "possible_marker_or_version_marker_difference",
}

# Review reasons that mark a tokenization-equivalent diff as carrying a hidden
# semantic signal (values, dates, percentages, digit-prefixed references).
# These must stay in review even if a stale route still labels the case type
# suppressible, so uncertainty always reaches review/export/redline.
SEMANTIC_SIGNAL_REVIEW_REASONS = frozenset({
    "comparison_token_equivalent_with_semantic_signal",
    "hyphenation_wrap_with_semantic_signal",
})

# ``hyphenation_wrap_artifact`` may only be suppressed when s11 proved a real
# line break in the raw source. A route without this reason (an older/stale
# artifact, or an ambiguous same-line hyphen) must never be labelled
# deterministic suppressed.
PROVEN_WRAP_REVIEW_REASONS = frozenset({
    "hyphenation_wrap_source_line_break_proven",
})

# Additive domain metadata stripped by ``generic_projection``; never part of
# the generic comparison content.  ``authorization`` is deliberately excluded:
# the canonical root must not carry pipeline authorization metadata, but a
# legitimate nested source/evidence key with that name is generic content and
# must survive projection.
_DOMAIN_METADATA_KEYS = frozenset({
    "domain_profile",
    "domain_verification",
    "domain_verification_review",
    "domain_status_counts",
    "outcome_migration",
    "generic_projection",
})

# Public resolution_for() reads these indexes; build_comparison_result() and
# main() populate them so the (candidate_id) -> resolution contract holds.
_INDEXES: dict[str, Any] = {
    "routes_by_candidate": {},
    "decisions_by_route": {},
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
    return value


def save_json(value: dict[str, Any], path: Path) -> None:
    """Atomic write: never leave a partial canonical result on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def compact_element(element: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(element, dict):
        return None
    return {
        "element_id": element.get("element_id"),
        "element_type": element.get("element_type"),
        "text": element.get("text"),
        "normalized_text": element.get("normalized_text"),
        "comparison_text": element.get("comparison_text"),
        "primary_page": element.get("primary_page"),
        "reading_order": element.get("reading_order"),
        "provisional_anchor_context": element.get("provisional_anchor_context"),
    }


def _resolve(
    candidate_id: str,
    routes_by_candidate: dict[str, Any],
    decisions_by_route: dict[str, Any],
) -> dict[str, Any]:
    """Pure resolution: deterministic route rules decide; s13 decisions are
    advisory recommendations preserved for traceability only.

    No authorization gate exists and no LLM outcome can accept, suppress, or
    mark structural-only: ACCEPT_DETERMINISTIC accepts, NON_SEMANTIC and
    proven SPECIAL_CASE_RULE case types resolve without review, and every
    other route (SPECIAL_CASE_LLM_OPTIONAL, HUMAN_REVIEW, unknown) stays
    review. A present decision is preserved verbatim as ``packet_decision``
    with ``method`` ``llm_advisory``.
    """
    route = routes_by_candidate.get(candidate_id)
    if not route:
        return {
            "route": "UNROUTED",
            "case_type": "unrouted",
            "method": "deterministic",
            "status": "review",
            "needs_review": True,
        }

    route_name = route["route"]
    if route_name == "ACCEPT_DETERMINISTIC":
        status, needs_review = "accepted", False
    elif route_name == "NON_SEMANTIC":
        status, needs_review = "non_semantic", False
    elif route_name == "SPECIAL_CASE_RULE":
        # Deterministic Step-11 rules proved these case types carry no semantic
        # content (word split, tokenization-neutral, hyphen/wrap-only). A
        # stale route with a hidden semantic-signal reason is still kept in
        # review rather than suppressed.
        case_type = route["case_type"]
        route_reasons = set(route.get("review_reasons") or [])
        if case_type in DETERMINISTIC_SUPPRESSED:
            if SEMANTIC_SIGNAL_REVIEW_REASONS & route_reasons:
                status, needs_review = "review", True
            elif (
                case_type == "hyphenation_wrap_artifact"
                and not (PROVEN_WRAP_REVIEW_REASONS & route_reasons)
            ):
                # A hyphen/whitespace-only diff is not proven non-semantic
                # without source line-break provenance. A stale/unsafe route
                # that omits it (or an ambiguous same-line hyphen) must stay
                # review, never be labelled deterministic suppressed.
                status, needs_review = "review", True
            else:
                status, needs_review = "suppressed_artifact", False
        elif case_type in DETERMINISTIC_STRUCTURAL:
            status, needs_review = "structural_only", False
        else:
            status, needs_review = "review", True
    else:
        # SPECIAL_CASE_LLM_OPTIONAL, HUMAN_REVIEW, and unknown routes stay
        # review; an s13 decision is a recommendation, never an acceptance.
        status, needs_review = "review", True

    resolution = {
        "route": route_name,
        "case_type": route["case_type"],
        "method": "deterministic",
        "status": status,
        "needs_review": needs_review,
        "review_reasons": route.get("review_reasons") or [],
    }
    decision = decisions_by_route.get(route["route_id"])
    if decision is not None:
        resolution["method"] = "llm_advisory"
        resolution["packet_decision"] = decision
    return resolution


def resolution_for(candidate_id: str) -> dict[str, Any]:
    """Public resolution contract; reads the indexes set by the last build.

    ``build_comparison_result()`` overwrites these indexes on each call, so
    ``resolution_for()`` reflects only the most recent build.  Stages are not
    concurrent and this last-build contract is intentional: callers that need
    a stable resolution re-run ``build_comparison_result()`` with the inputs
    they care about.
    """
    return _resolve(
        candidate_id,
        _INDEXES["routes_by_candidate"],
        _INDEXES["decisions_by_route"],
    )


def change_type_for_group(group: str) -> str:
    return {
        "modified": "MODIFIED",
        "modified_review": "MODIFIED",
        "deleted": "DELETED",
        "added": "ADDED",
    }[group]


def change_fingerprint(
    change_type: str, old: dict[str, Any] | None, new: dict[str, Any] | None
) -> str:
    evidence = {
        "change_type": change_type,
        "old": (old or {}).get("comparison_text"),
        "new": (new or {}).get("comparison_text"),
    }
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Validation: incomplete pipeline state raises before any output is produced.
# ---------------------------------------------------------------------------

def _validate_inputs(
    movement: dict[str, Any],
    tables: dict[str, Any],
    routing: dict[str, Any],
    decisions: dict[str, Any],
) -> None:
    if movement.get("comparison_stage") != EXPECTED_STEP9_STAGE:
        raise ValueError(
            f"s14 requires Step 9 stage {EXPECTED_STEP9_STAGE!r}; "
            f"got {movement.get('comparison_stage')!r}."
        )
    if tables.get("comparison_stage") != EXPECTED_STEP10_STAGE:
        raise ValueError(
            f"s14 requires Step 10 stage {EXPECTED_STEP10_STAGE!r}; "
            f"got {tables.get('comparison_stage')!r}."
        )
    if routing.get("comparison_stage") != EXPECTED_STEP11_STAGE:
        raise ValueError(
            f"s14 requires Step 11 stage {EXPECTED_STEP11_STAGE!r}; "
            f"got {routing.get('comparison_stage')!r}."
        )
    if decisions and decisions.get("comparison_stage") not in (
        None, EXPECTED_STEP13_STAGE,
    ):
        raise ValueError(
            f"s14 requires Step 13 stage {EXPECTED_STEP13_STAGE!r}; "
            f"got {decisions.get('comparison_stage')!r}."
        )


def _canonical_routes(routing: dict[str, Any]) -> list[dict[str, Any]]:
    routes = routing.get("routes")
    if routes is None:
        routes = (routing.get("non_table_routes") or []) + (
            routing.get("table_routes") or []
        )
    return routes


def _validate_route_uniqueness(routes: list[dict[str, Any]]) -> None:
    route_ids = [route.get("route_id") for route in routes]
    if any(route_id is None for route_id in route_ids):
        raise ValueError("Every s11 route must carry a route_id.")
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("s11 routes contain duplicate route_ids.")


def _validate_candidate_uniqueness(routes: list[dict[str, Any]]) -> None:
    candidate_ids = [
        route.get("candidate_id") for route in routes if route.get("candidate_id")
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("s11 routes contain duplicate candidate_ids.")


def _consumed_candidate_ids(
    movement: dict[str, Any], tables: dict[str, Any]
) -> list[str]:
    """Every candidate/review id this stage resolves, in consumption order."""
    consumed: list[str] = []
    for group in ("modified", "modified_review", "deleted", "added",
                  "duplicate_exact_review", "relocated"):
        for item in movement.get(group) or []:
            cid = item.get("candidate_id")
            if cid:
                consumed.append(cid)
    for group in ("movement_review", "structural_split_merge_review",
                  "structural_boundary_review", "resegmentation_review"):
        for item in movement.get(group) or []:
            consumed.append(structural_route_identity(item)["candidate_id"])
    for match in tables.get("table_matches") or []:
        cid = match.get("table_match_id")
        if cid:
            consumed.append(cid)
    for group in ("deleted_table_candidates", "added_table_candidates"):
        for table in tables.get(group) or []:
            cid = table.get("logical_table_id")
            if cid:
                consumed.append(cid)
    for group in tables.get("unresolved_table_groups") or []:
        cid = group.get("review_group_id")
        if cid:
            consumed.append(cid)
    return consumed


def _validate_consumed_routed(
    movement: dict[str, Any],
    tables: dict[str, Any],
    routes_by_candidate: dict[str, Any],
) -> None:
    missing = sorted({
        cid for cid in _consumed_candidate_ids(movement, tables)
        if cid not in routes_by_candidate
    })
    if missing:
        raise ValueError(
            "Consumed change/table candidates have no s11 route: "
            + ", ".join(missing[:10])
        )


def _domain_records_by_route(
    routing: dict[str, Any], routes: list[dict[str, Any]]
) -> dict[str, Any]:
    """One domain-verification record per route, keyed by route_id."""
    records = (routing.get("domain_verification") or {}).get("records") or []
    record_ids = [record.get("route_id") for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("domain_verification.records contain duplicate route_ids.")
    route_ids = {route.get("route_id") for route in routes}
    record_id_set = set(record_ids)
    if record_id_set != route_ids:
        missing = sorted(route_ids - record_id_set)
        extra = sorted(record_id_set - route_ids)
        raise ValueError(
            "domain_verification.records must cover every route exactly once; "
            f"missing={missing[:5]} extra={extra[:5]}"
        )
    return {record.get("route_id"): record for record in records}


# ---------------------------------------------------------------------------
# Canonical build
# ---------------------------------------------------------------------------

def _build_changes(
    movement: dict[str, Any],
    tables: dict[str, Any],
    routes_by_candidate: dict[str, Any],
    decisions_by_route: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    non_semantic_events: list[dict[str, Any]] = []
    unresolved_table_reviews: list[dict[str, Any]] = []
    seen_review_keys: set[tuple[str, str]] = set()

    def queue_review(entry: dict[str, Any]) -> None:
        """Append unless the same candidate/case pair is already queued.

        Step 9 evidence can reach Step 14 through two paths (change resolution
        and evidence-only queues); BU must see each case exactly once.
        """
        key = (
            str(entry.get("source_evidence_id") or entry.get("source_candidate_id")),
            str(entry.get("case_type")),
        )
        if key in seen_review_keys:
            return
        seen_review_keys.add(key)
        review_queue.append(entry)

    for group in ("modified", "modified_review", "deleted", "added"):
        for item in movement.get(group) or []:
            cid = item["candidate_id"]
            resolution = _resolve(cid, routes_by_candidate, decisions_by_route)
            record = {
                "change_id": f"CHANGE-{len(changes)+1:06d}",
                "source_candidate_id": cid,
                "source_group": group,
                "change_type": change_type_for_group(group),
                "old": compact_element(item.get("old")),
                "new": compact_element(item.get("new")),
                "token_diff": item.get("token_diff"),
                "similarity": item.get("similarity"),
                "resolution": resolution,
                "provenance": {
                    "step9_candidate_id": cid,
                    "matching_not_recomputed": True,
                },
            }
            record["change_fingerprint"] = change_fingerprint(
                record["change_type"], record["old"], record["new"]
            )

            if resolution["status"] == "suppressed_artifact":
                non_semantic_events.append({**record, "event_type": "SUPPRESSED_ARTIFACT"})
                continue
            if resolution["status"] == "structural_only":
                non_semantic_events.append({**record, "event_type": "STRUCTURAL_ONLY"})
                continue

            changes.append(record)
            if resolution["needs_review"]:
                queue_review({
                    "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                    "source_kind": "non_table_change",
                    "change_id": record["change_id"],
                    "source_candidate_id": cid,
                    "case_type": resolution["case_type"],
                    "resolution": resolution,
                })

    # Duplicate exact assignment is never a substantive change.
    for item in movement.get("duplicate_exact_review") or []:
        cid = item["candidate_id"]
        queue_review({
            "review_id": f"REVIEW-{len(review_queue)+1:06d}",
            "source_kind": "duplicate_exact_assignment",
            "source_candidate_id": cid,
            "case_type": "duplicate_exact_assignment",
            "resolution": _resolve(cid, routes_by_candidate, decisions_by_route),
        })

    for item in movement.get("relocated") or []:
        non_semantic_events.append({
            "event_id": f"EVENT-{len(non_semantic_events)+1:06d}",
            "event_type": "RELOCATED_UNCHANGED",
            "source_candidate_id": item.get("candidate_id"),
            "old": compact_element(item.get("old")),
            "new": compact_element(item.get("new")),
        })

    # Evidence-only review queues routed in Step 11 must reach the canonical
    # result; previously they were silently dropped.
    for source_kind, group_name, case_type in (
        ("movement_block", "movement_review", "movement_block_review"),
        ("structural_split_merge", "structural_split_merge_review", "structural_split_merge"),
        ("structural_boundary", "structural_boundary_review", "structural_boundary"),
    ):
        for item in movement.get(group_name) or []:
            identity = structural_route_identity(item)
            evidence_id = identity["candidate_id"]
            queue_review({
                "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                "source_kind": source_kind,
                "source_evidence_id": evidence_id,
                "source_candidate_id": (
                    identity.get("source_candidate_id") or evidence_id
                ),
                "case_type": case_type,
                "resolution": _resolve(evidence_id, routes_by_candidate, decisions_by_route),
                "evidence": item,
            })

    table_changes: list[dict[str, Any]] = []
    for match in tables.get("table_matches") or []:
        cid = match["table_match_id"]
        resolution = _resolve(cid, routes_by_candidate, decisions_by_route)
        cls = match.get("classification")

        if cls in {"UNCHANGED", "LAYOUT_EQUIVALENT"}:
            continue

        record = {
            "table_change_id": f"TABLE-CHANGE-{len(table_changes)+1:05d}",
            "source_table_match_id": cid,
            "change_type": "TABLE_MODIFIED",
            "classification": cls,
            "old": match.get("old"),
            "new": match.get("new"),
            "cell_changes": match.get("cell_changes") or [],
            "resolution": resolution,
            "review_reasons": match.get("review_reasons") or [],
        }

        if resolution["status"] in {"suppressed_artifact", "structural_only"}:
            non_semantic_events.append({**record, "event_type": resolution["status"].upper()})
            continue

        table_changes.append(record)
        if resolution["needs_review"]:
            queue_review({
                "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                "source_kind": "table_change",
                "table_change_id": record["table_change_id"],
                "source_candidate_id": cid,
                "case_type": resolution["case_type"],
                "resolution": resolution,
            })

    for group, ctype in (
        ("deleted_table_candidates", "TABLE_DELETED"),
        ("added_table_candidates", "TABLE_ADDED"),
    ):
        for table in tables.get(group) or []:
            cid = table["logical_table_id"]
            resolution = _resolve(cid, routes_by_candidate, decisions_by_route)
            record = {
                "table_change_id": f"TABLE-CHANGE-{len(table_changes)+1:05d}",
                "source_table_match_id": cid,
                "change_type": ctype,
                "old": table if group.startswith("deleted") else None,
                "new": table if group.startswith("added") else None,
                "cell_changes": [],
                "resolution": resolution,
            }

            if resolution["status"] in {"suppressed_artifact", "structural_only"}:
                non_semantic_events.append({**record, "event_type": resolution["status"].upper()})
                continue

            table_changes.append(record)
            if resolution["needs_review"]:
                queue_review({
                    "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                    "source_kind": "table_change",
                    "table_change_id": record["table_change_id"],
                    "source_candidate_id": cid,
                    "case_type": resolution["case_type"],
                    "resolution": resolution,
                })

    for item in movement.get("resegmentation_review") or []:
        evidence_id = structural_route_identity(item)["candidate_id"]
        queue_review({
            "review_id": f"REVIEW-{len(review_queue)+1:06d}",
            "source_kind": "resegmentation_pair",
            "source_evidence_id": evidence_id,
            "source_candidate_id": evidence_id,
            "case_type": "resegmentation_suspect",
            "resolution": _resolve(evidence_id, routes_by_candidate, decisions_by_route),
            "evidence": {
                "old_element_ids": [
                    e["element_id"] for e in [item.get("old")]
                    if isinstance(e, dict) and e.get("element_id")
                ],
                "new_element_ids": [
                    e["element_id"] for e in [item.get("new")]
                    if isinstance(e, dict) and e.get("element_id")
                ],
                "similarity": item.get("similarity"),
                "review_reasons": item.get("review_reasons") or [],
            },
        })

    # Unresolved table groups (same-context residual tables) are HUMAN_REVIEW
    # items; the underlying tables never become add/delete candidates, so they
    # must be surfaced here or they vanish from the canonical result.
    for group in tables.get("unresolved_table_groups") or []:
        group_id = group.get("review_group_id")
        if not group_id:
            continue
        entry = {
            "review_id": f"REVIEW-{len(review_queue)+1:06d}",
            "source_kind": "unresolved_table_group",
            "source_candidate_id": group_id,
            "case_type": "unresolved_table_group",
            "resolution": _resolve(group_id, routes_by_candidate, decisions_by_route),
            "evidence": group,
        }
        queue_review(entry)
        if entry in review_queue and entry not in unresolved_table_reviews:
            unresolved_table_reviews.append(entry)

    return changes, table_changes, review_queue, non_semantic_events, unresolved_table_reviews


def _attach_domain_verification(
    record: dict[str, Any],
    routes_by_candidate: dict[str, Any],
    records_by_route_id: dict[str, Any],
) -> dict[str, Any]:
    """Attach the route's s11 domain-verification status; never alters resolution."""
    cid = record.get("source_candidate_id") or record.get("source_table_match_id")
    route = routes_by_candidate.get(cid)
    if route is None:
        return {**record, "domain_verification": {"status": "not_applicable"}}
    record_for_route = records_by_route_id.get(route["route_id"])
    verification = (record_for_route or {}).get("domain_verification") or {
        "status": "not_applicable"
    }
    return {**record, "domain_verification": dict(verification)}


def generic_projection(
    result: dict[str, Any], *, _in_packet_decision: bool = False
) -> dict[str, Any]:
    """Strip all additive domain metadata, preserving generic content.

    Removes domain profile, per-change verification, the separate domain
    review list, domain status counts, the outcome-migration evidence, the
    embedded projection itself, and any residual authorization key.

    Also strips the mode-derived ``input_hash`` that s12 computes over the
    bounded packet envelope, because crr3 envelopes include additive
    ``domain_verification`` evidence.  The full canonical result keeps this
    hash inside ``resolution.packet_decision``; only the generic projection
    omits it.  Unrelated ``input_hash`` keys elsewhere are generic content
    and survive, mirroring the treatment of nested ``authorization``.
    """
    if isinstance(result, dict):
        projected: dict[str, Any] = {}
        for key, value in result.items():
            if key in _DOMAIN_METADATA_KEYS:
                continue
            if _in_packet_decision and key == "input_hash":
                continue
            projected[key] = generic_projection(
                value, _in_packet_decision=(key == "packet_decision")
            )
        return projected
    if isinstance(result, list):
        return [
            generic_projection(item, _in_packet_decision=_in_packet_decision)
            for item in result
        ]
    return result


def build_comparison_result(
    movement: dict[str, Any],
    tables: dict[str, Any],
    routing: dict[str, Any],
    decisions: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical generic result; crr3 metadata is additive and never gates.

    ``decisions`` is the s13 output (or {} when the LLM stage did not run);
    ``baseline`` carries the frozen Phase 0 metrics for the outcome-migration
    evidence report ({} when unavailable). Raises ValueError on incomplete
    pipeline state before any output is produced.
    """
    decisions = decisions or {}
    _validate_inputs(movement, tables, routing, decisions)

    routes = _canonical_routes(routing)
    _validate_route_uniqueness(routes)
    _validate_candidate_uniqueness(routes)
    routes_by_candidate = {
        route["candidate_id"]: route
        for route in routes
        if route.get("candidate_id")
    }
    decisions_by_route = {
        decision["route_id"]: decision
        for decision in (decisions.get("decisions") or [])
        if decision.get("route_id")
    }
    records_by_route_id = _domain_records_by_route(routing, routes)
    _validate_consumed_routed(movement, tables, routes_by_candidate)

    # Public resolution_for() reads these indexes.
    _INDEXES["routes_by_candidate"] = routes_by_candidate
    _INDEXES["decisions_by_route"] = decisions_by_route

    changes, table_changes, review_queue, non_semantic_events, unresolved_table_reviews = (
        _build_changes(movement, tables, routes_by_candidate, decisions_by_route)
    )

    changes = [
        _attach_domain_verification(change, routes_by_candidate, records_by_route_id)
        for change in changes
    ]
    table_changes = [
        _attach_domain_verification(change, routes_by_candidate, records_by_route_id)
        for change in table_changes
    ]

    canonical_items = changes + table_changes
    resolution_status_counts = dict(
        Counter(item["resolution"]["status"] for item in canonical_items)
    )
    domain_status_counts = dict(Counter(
        (item.get("domain_verification") or {}).get("status", "not_applicable")
        for item in canonical_items
    ))

    migration = build_outcome_migration(
        baseline or {},
        {
            "domain_status_counts": domain_status_counts,
            "resolution_status_counts": resolution_status_counts,
            "review_queue": len(review_queue),
            "route_counts": (routing.get("statistics") or {}).get("route_counts") or {},
        },
    )

    # Map domain-verification review entries to the canonical IDs of retained
    # changes/table changes so the list is actionable; keep it separate from the
    # generic review queue.  Only ambiguous/unsupported statuses are relevant,
    # and routes that produced only non-semantic events have no canonical item.
    route_to_canonical: dict[str, str] = {}
    for change in changes:
        route = routes_by_candidate.get(change["source_candidate_id"])
        if route:
            route_to_canonical[route["route_id"]] = change["change_id"]
    for table_change in table_changes:
        route = routes_by_candidate.get(table_change["source_table_match_id"])
        if route:
            route_to_canonical[route["route_id"]] = table_change["table_change_id"]

    domain_verification_review: list[dict[str, Any]] = []
    for entry in (routing.get("domain_verification_review") or []):
        status = (entry.get("domain_verification") or {}).get("status")
        if status not in {"ambiguous", "unsupported"}:
            continue
        canonical_id = route_to_canonical.get(entry.get("route_id"))
        if canonical_id is None:
            continue
        domain_verification_review.append({**entry, "canonical_id": canonical_id})

    final = {
        "schema_version": "3.0.0",
        "comparison_stage": "phase_1_final_comparison",
        "old_document": movement.get("old_document"),
        "new_document": movement.get("new_document"),
        "processing": {
            "version": STEP14_VERSION,
            "canonical_backend_contract": True,
            "matching_recomputed": False,
            "llm_optional": True,
            "uncertainty_preserved": True,
        },
        "statistics": {
            "changes": len(changes),
            "table_changes": len(table_changes),
            "review_queue": len(review_queue),
            "non_semantic_events": len(non_semantic_events),
            "change_type_counts": dict(Counter(x["change_type"] for x in changes)),
            "resolution_status_counts": resolution_status_counts,
            "domain_status_counts": domain_status_counts,
        },
        "changes": changes,
        "table_changes": table_changes,
        "review_queue": review_queue,
        "non_semantic_events": non_semantic_events,
        "unresolved_table_reviews": unresolved_table_reviews,
        "domain_profile": routing.get("domain_profile"),
        "domain_verification_review": domain_verification_review,
        "outcome_migration": migration,
    }
    final["generic_projection"] = generic_projection(final)

    # Stable uniqueness and the accepted/no-review invariant.
    require(
        len({x["change_id"] for x in changes}) == len(changes),
        "s14 changes contain duplicate change_id values.",
    )
    require(
        len({x["table_change_id"] for x in table_changes}) == len(table_changes),
        "s14 table_changes contain duplicate table_change_id values.",
    )
    require(
        all(
            x["resolution"]["status"] != "accepted" or not x["resolution"]["needs_review"]
            for x in changes + table_changes
        ),
        "s14 produced an accepted resolution that still needs review.",
    )
    return final


def _load_baseline_metrics() -> dict[str, Any]:
    """Frozen Phase 0 local-output snapshot metrics; {} when unavailable."""
    manifest_path = REPO_ROOT / "baseline" / "phase0" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    snapshot = manifest.get("local_output_snapshot") or {}
    return snapshot.get("metrics") or {}


def main() -> None:
    root = find_project_root()
    out = output_dir(root)
    movement = load_json(out / "comparison_candidates_with_movement.json")
    tables = load_json(out / "table_comparison_result.json")
    routing = load_json(out / "review_routing.json")
    decisions_path = out / "llm_review_decisions.json"
    decisions = load_json(decisions_path) if decisions_path.exists() else {}

    result = build_comparison_result(
        movement, tables, routing, decisions, baseline=_load_baseline_metrics()
    )

    final_path = out / "comparison_result.json"
    save_json(result, final_path)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", final_path)
    print("Outcome migration (evidence, not a gate):")
    print(json.dumps(result["outcome_migration"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()