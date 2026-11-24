"""s14 - canonical generic comparison result (generic only).

Auto-converted from 14_build_final_comparison_result_v2.ipynb (code cells only),
generalized: generic acceptance is domain-agnostic and byte-for-byte
independent of any domain metadata. CRR verification is retired; no
verification records are joined or attached, and the compatibility domain
review list is always empty.

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from document_compare.shared.paths import REPO_ROOT, find_project_root, output_dir
from document_compare.phase1.pipeline.mode import resolve_domain_profile
from document_compare.phase1.contracts.outcomes import build_outcome_migration
from document_compare.phase1.contracts.result_state import (
    derive_result_state,
    result_state_counts,
)
from document_compare.phase1.contracts.review_identity import structural_route_identity
from document_compare.phase1.contracts.semantic_assessment import (
    is_deterministic_material_proof,
    normalize_assessment,
    validate_semantic_assessment,
)
from document_compare.phase1.contracts.validation import require
from document_compare.phase1.matching.element_types import type_change_fields
from document_compare.phase1.matching.impact import IMPACT_VERSION, classify_impact, raw_glyph_signature
from document_compare.phase1.matching.semantic_evidence import (
    independent_correspondence_verified,
    recompute_material_proof,
)

from document_compare.phase1.stages.s08_candidates import build_representation_diffs
from document_compare.phase1.stages.s09_movement import (
    collect_document_elements,
    load_structure_context,
    previous_candidate_view,
    reading_order,
    STRUCTURAL_TYPES,
)
from document_compare.phase1.structure.reconstruction import is_layout

STEP14_VERSION = "3.0.0-generic-canonical-result"

EXPECTED_STEP9_STAGE = "movement_and_structural_reconciliation_evidence"
EXPECTED_STEP10_STAGE = "logical_table_comparison_with_provisional_context"
EXPECTED_STEP11_STAGE = "deterministic_review_routing"
EXPECTED_STEP13_STAGE = "optional_llm_special_case_evaluation"

# Step-11 deterministic case types whose diffs are proven non-semantic.
DETERMINISTIC_SUPPRESSED = {
    "glyph_or_invisible_char_artifact",
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
    "paragraph_split",
    "paragraph_merge",
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

# Only the existing short-text review reason may be cleared by a validated
# deterministic material witness.  Every other reason stays in review.
SEMANTIC_SHORT_PAIR_REASONS = frozenset({"short_text_similarity_pair"})
SEMANTIC_ACCEPT_CASE_TYPE = "semantic_material_change"

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
            "result_state": "uncertain",
        }

    route_name = route["route"]
    if route_name == "ACCEPT_DETERMINISTIC":
        status, needs_review = "accepted", False
    elif route_name == "NON_SEMANTIC":
        status, needs_review = "non_semantic", False
    elif route_name == "SPECIAL_CASE_RULE":
        # Deterministic Step-11 rules proved these case types carry no semantic
        # content (glyph fold, word split, tokenization-neutral, or wrap). A
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
        "result_state": derive_result_state(status, needs_review),
        "review_reasons": route.get("review_reasons") or [],
    }
    decision = decisions_by_route.get(route["route_id"])
    if decision is not None:
        resolution["method"] = "llm_advisory"
        resolution["packet_decision"] = decision
    return resolution


def _validated_semantic_proof(
    item: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any] | None:
    """Independently revalidate a deterministic short-pair material proof.

    The serialized route, a forged assessment, a stale source hash, an offset
    mismatch, a model advisory or any additional review reason cannot accept a
    pair.  A validated proof is returned as a safe copy; ``None`` means the
    deterministic decision remains review.
    """
    if resolution.get("case_type") != SEMANTIC_ACCEPT_CASE_TYPE:
        return None
    if resolution.get("status") != "accepted" or resolution.get("needs_review"):
        return None
    if set(item.get("review_reasons") or []) != SEMANTIC_SHORT_PAIR_REASONS:
        return None
    old = item.get("old")
    new = item.get("new")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    assessment = item.get("semantic_assessment")
    validation = validate_semantic_assessment(
        assessment, old_element=old, new_element=new
    )
    if not validation["valid"]:
        return None
    validated = validation["assessment"]
    if not is_deterministic_material_proof(validated):
        return None
    if validated.get("correspondence") != "verified":
        return None
    if not independent_correspondence_verified(item):
        return None
    if not recompute_material_proof(validated, old, new):
        return None
    return validated


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
                  "duplicate_exact_review", "relocated", "paragraph_resegmentation"):
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


# ---------------------------------------------------------------------------
# Canonical build
# ---------------------------------------------------------------------------

def _impact_text(element: dict[str, Any] | None) -> str | None:
    element = element or {}
    return element.get("normalized_text") or element.get("text")


def _table_impact(record: dict[str, Any]) -> dict[str, Any]:
    cells = record.get("cell_changes") or []
    classification = record.get("classification")
    old, new = record.get("old") or {}, record.get("new") or {}
    row_alignment = record.get("row_alignment") or {}
    structure_changed = (
        classification == "STRUCTURE_CHANGED_REVIEW"
        or bool(row_alignment.get("added_rows") or row_alignment.get("deleted_rows"))
        or any(
            old.get(field) is not None and new.get(field) is not None and old[field] != new[field]
            for field in ("row_count", "column_count")
        )
        or any(
            cell.get("structure_changed")
            or cell.get("classification") in {"STRUCTURE_CHANGED_REVIEW", "ADDED", "DELETED"}
            for cell in cells
        )
    )
    if not structure_changed and classification in {"UNCHANGED", "LAYOUT_EQUIVALENT"}:
        level, priority, kinds, signals = "non_content", "low", ["table_layout_equivalent"], []
    else:
        impacts = []
        for cell in cells:
            if cell.get("classification") == "UNCHANGED":
                continue
            old_text = _impact_text(cell.get("old"))
            new_text = _impact_text(cell.get("new"))
            if old_text is not None or new_text is not None:
                impacts.append(classify_impact(old_text, new_text))
        signals = sorted({signal for impact in impacts for signal in impact["signals"]})
        if structure_changed:
            level = "content"
            priority = "high" if any(impact["priority"] == "high" for impact in impacts) else "normal"
            kinds = ["table_structure_changed"]
        elif impacts:
            levels = ("non_content", "editorial", "content")
            strongest = max(
                impacts,
                key=lambda impact: (levels.index(impact["level"]), impact["priority"] == "high"),
            )
            level, priority = strongest["level"], strongest["priority"]
            kinds = sorted({kind for impact in impacts for kind in impact["kinds"]})
        else:
            level, priority, kinds = "content", "normal", ["table_change_unclassified"]

    return {
        "version": IMPACT_VERSION,
        "level": level,
        "priority": priority,
        "kinds": kinds,
        "signals": signals,
        "basis": "deterministic_rule",
        "language_profile": "de-en",
    }


def _paragraph_records(
    movement: dict[str, Any], routes_by_candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve grouped display records and independently verify source conservation."""
    groups = movement.get("paragraph_resegmentation") or []
    if not groups:
        return []
    seen = {
        side: {
            element["element_id"]
            for group in ("unchanged", "modified", "modified_review", "deleted", "added",
                          "duplicate_exact_review", "relocated", "resegmentation_review")
            for record in movement.get(group) or []
            if (element := record.get(side)) and element.get("element_id")
        }
        for side in ("old", "new")
    }
    records = []
    for group in groups:
        cid = group["candidate_id"]
        sizes = (len(group["old_elements"]), len(group["new_elements"]))
        kind = "paragraph_split" if sizes[0] == 1 else "paragraph_merge"
        require(
            (sizes[0] == 1 and 2 <= sizes[1] <= 4)
            or (sizes[1] == 1 and 2 <= sizes[0] <= 4),
            "s14: invalid paragraph resegmentation cardinality",
        )
        record = {
            "source_candidate_id": cid,
            "source_group": "paragraph_resegmentation",
            "change_type": "MODIFIED",
            "provenance": {
                "step9_candidate_id": cid,
                "source_candidate_ids": group["source_candidate_ids"],
                "matching_not_recomputed": True,
            },
        }
        for side in ("old", "new"):
            elements = [compact_element(item) for item in group[f"{side}_elements"]]
            ids = [element["element_id"] for element in elements]
            for element_id in ids:
                require(element_id and element_id not in seen[side],
                        f"s14: {side.upper()} paragraph element consumed more than once or missing")
                seen[side].add(element_id)
            record[f"{side}_elements"] = elements
            record[f"{side}_element_ids"] = ids
            # Use the first element as the display location. The source records
            # and their individual raw texts are kept in the element list.
            record[side] = {
                **elements[0],
                **{
                    field: " ".join(element.get(field) or element["text"] for element in elements)
                    for field in ("text", "normalized_text", "comparison_text")
                },
            }

        impact = classify_impact(record["old"]["text"], record["new"]["text"])
        resolution = _resolve(cid, routes_by_candidate, {})
        require(
            kind == group["kind"] and resolution["case_type"] == kind,
            "s14: paragraph resegmentation evidence disagrees with its route",
        )
        if not resolution["needs_review"]:
            status = "structural_only" if impact["level"] == "non_content" else "accepted"
            require(
                impact["level"] in {"non_content", "editorial"}
                and impact["level"] == group["impact_level"] and resolution["status"] == status,
                "s14: paragraph resegmentation evidence disagrees with its route",
            )
        if resolution["status"] == "structural_only":
            impact["kinds"] = [kind]
        else:
            impact["kinds"] = sorted({kind, *impact["kinds"]})
            record["token_diff"] = build_representation_diffs(record["old"], record["new"])
        record["resolution"] = resolution
        record["impact"] = impact
        record["change_fingerprint"] = change_fingerprint("MODIFIED", record["old"], record["new"])
        records.append(record)
    return records


def _canonical_moves(
    movement: dict[str, Any], changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    moves = movement.get("moves") or []
    if not moves:
        return []
    pairs = {
        (item["old"]["element_id"], item["new"]["element_id"]): item["candidate_id"]
        for group in ("unchanged", "modified", "modified_review", "relocated")
        for item in movement.get(group) or []
        if item.get("old") and item.get("new")
    }
    moves_by_candidate = {}
    seen_moves = set()
    for move in moves:
        move_id = move["move_id"]
        require(move_id not in seen_moves, "s14: duplicate move_id")
        seen_moves.add(move_id)
        old_ids, new_ids = move["old_element_ids"], move["new_element_ids"]
        require(len(old_ids) == len(new_ids) == move["element_count"] > 0,
                "s14: move cardinality mismatch")
        for old_id, new_id in zip(old_ids, new_ids):
            cid = pairs.get((old_id, new_id))
            require(cid is not None and cid not in moves_by_candidate,
                    "s14: move must refer to a distinct matched pair")
            moves_by_candidate[cid] = move_id

    for change in changes:
        move_id = moves_by_candidate.get(change["source_candidate_id"])
        if move_id:
            change["movement"] = {"move_id": move_id}
    return moves


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

    proposal_groups_by_id = {
        str(group.get("group_id")): group
        for group in movement.get("residual_proposal_groups") or []
        if isinstance(group, dict) and group.get("group_id")
    }
    queued_proposal_groups: dict[str, dict[str, Any]] = {}

    for group in ("modified", "modified_review", "deleted", "added"):
        for item in movement.get(group) or []:
            cid = item["candidate_id"]
            resolution = _resolve(cid, routes_by_candidate, decisions_by_route)
            if (
                resolution["status"] == "suppressed_artifact"
                and resolution["case_type"] == "glyph_or_invisible_char_artifact"
                and raw_glyph_signature((item.get("old") or {}).get("text") or "")
                != raw_glyph_signature((item.get("new") or {}).get("text") or "")
            ):
                resolution.update(
                    status="review",
                    needs_review=True,
                    result_state="uncertain",
                    review_reasons=sorted({
                        *resolution["review_reasons"], "raw_glyph_signature_changed",
                    }),
                )

            # Stage 14 is the final deterministic owner: a semantic acceptance
            # nomination is only kept when the actual source revalidates it.
            validated_assessment: dict[str, Any] | None = None
            if resolution.get("case_type") == SEMANTIC_ACCEPT_CASE_TYPE:
                validated_assessment = _validated_semantic_proof(item, resolution)
                if validated_assessment is None:
                    resolution = dict(resolution)
                    resolution.update({
                        "status": "review",
                        "needs_review": True,
                        "result_state": "uncertain",
                        "review_reasons": list(dict.fromkeys(
                            list(resolution.get("review_reasons") or [])
                            + ["semantic_witness_not_independently_valid"]
                        )),
                    })

            proposal_group_id = item.get("residual_proposal_group_id")
            if (
                group in ("deleted", "added")
                and isinstance(proposal_group_id, str)
                and proposal_group_id
            ):
                resolution = dict(resolution)
                resolution["proposal_group_id"] = proposal_group_id

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
            if validated_assessment is not None:
                record["semantic_assessment"] = validated_assessment
            elif item.get("semantic_assessment") is not None:
                # Never project a forged/invalid assessment as authority.
                record["semantic_assessment"] = normalize_assessment(
                    item.get("semantic_assessment"),
                    old_element=item.get("old"),
                    new_element=item.get("new"),
                )
            record["change_fingerprint"] = change_fingerprint(
                record["change_type"], record["old"], record["new"]
            )

            impact = classify_impact(_impact_text(record["old"]), _impact_text(record["new"]))
            if (
                resolution["status"] in {"suppressed_artifact", "structural_only"}
                and impact["level"] != "non_content"
            ):
                impact.update(
                    level="non_content",
                    priority="low",
                    kinds=sorted({*impact["kinds"], "deterministic_artifact_rule"}),
                )
            type_evidence = type_change_fields(record["old"], record["new"])
            if type_evidence:
                record.update(type_evidence)
                impact["kinds"] = sorted({*impact["kinds"], "element_type_changed"})
            record["impact"] = impact

            if resolution["status"] == "suppressed_artifact":
                non_semantic_events.append({**record, "event_type": "SUPPRESSED_ARTIFACT"})
                continue
            if resolution["status"] == "structural_only":
                non_semantic_events.append({**record, "event_type": "STRUCTURAL_ONLY"})
                continue

            changes.append(record)
            if resolution["needs_review"]:
                if (
                    group in ("deleted", "added")
                    and isinstance(proposal_group_id, str)
                    and proposal_group_id
                ):
                    queued_proposal_groups.setdefault(
                        proposal_group_id,
                        {
                            "group": proposal_groups_by_id.get(proposal_group_id, {}),
                            "candidate_ids": [],
                            "old_element_ids": [],
                            "new_element_ids": [],
                            "change_ids": [],
                        },
                    )
                    entry = queued_proposal_groups[proposal_group_id]
                    entry["candidate_ids"].append(cid)
                    entry["change_ids"].append(record["change_id"])
                    if isinstance(item.get("old"), dict):
                        element_id = item["old"].get("element_id")
                        if element_id:
                            entry["old_element_ids"].append(element_id)
                    if isinstance(item.get("new"), dict):
                        element_id = item["new"].get("element_id")
                        if element_id:
                            entry["new_element_ids"].append(element_id)
                    continue
                queue_review({
                    "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                    "source_kind": "non_table_change",
                    "change_id": record["change_id"],
                    "source_candidate_id": cid,
                    "case_type": resolution["case_type"],
                    "resolution": resolution,
                })

    # One grouped user-facing uncertainty entry per residual proposal group.
    # The original candidate/change records are preserved; this entry only
    # provides one canonical explanation and typed source references.
    for group_id in sorted(queued_proposal_groups):
        entry = queued_proposal_groups[group_id]
        group = entry.get("group") or {}
        proposal_ids = sorted({
            str(proposal.get("proposal_id"))
            for proposal in group.get("proposals") or []
            if isinstance(proposal, dict) and proposal.get("proposal_id")
        })
        correspondence = group.get("correspondence") or "proposed"
        evidence = {
            "proposal_group_id": group_id,
            "candidate_ids": sorted(set(entry["candidate_ids"])),
            "change_ids": sorted(set(entry["change_ids"])),
            "old_element_ids": sorted(set(entry["old_element_ids"])),
            "new_element_ids": sorted(set(entry["new_element_ids"])),
            "proposal_ids": proposal_ids,
            "correspondence": correspondence,
            "consumes_candidates": False,
        }
        queue_review({
            "review_id": f"REVIEW-{len(review_queue)+1:06d}",
            "source_kind": "residual_proposal_group",
            "source_evidence_id": group_id,
            "source_candidate_id": None,
            "proposal_group_id": group_id,
            "case_type": "unresolved_residual_counterpart",
            "resolution": {
                "route": "UNCERTAIN_RESULT",
                "case_type": "unresolved_residual_counterpart",
                "method": "deterministic",
                "status": "review",
                "needs_review": True,
                "result_state": "uncertain",
                "review_reasons": ["plausible_unresolved_counterpart"],
                "proposal_group_id": group_id,
            },
            "evidence": evidence,
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
            **type_change_fields(item.get("old"), item.get("new")),
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
        impact = _table_impact(match)

        # Clean equivalent tables are intentionally omitted from canonical
        # changes.  Equivalent tables with routed uncertainty remain visible:
        # their review evidence is the change requiring user attention.
        if (
            cls in {"UNCHANGED", "LAYOUT_EQUIVALENT"}
            and impact["level"] == "non_content"
            and resolution["status"] == "non_semantic"
            and not resolution["needs_review"]
        ):
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
        record["impact"] = impact

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
            # One-sided table cell content is carried exactly once in
            # ``cell_changes``; the old/new metadata copy excludes the nested
            # per-cell detail (and its status) so it is never duplicated.
            metadata = {
                key: value
                for key, value in table.items()
                if key not in ("cell_changes", "cell_content_status")
            }
            record = {
                "table_change_id": f"TABLE-CHANGE-{len(table_changes)+1:05d}",
                "source_table_match_id": cid,
                "change_type": ctype,
                "old": metadata if group.startswith("deleted") else None,
                "new": metadata if group.startswith("added") else None,
                "cell_changes": table.get("cell_changes") or [],
                "review_reasons": table.get("review_reasons") or [],
                "cell_content_status":
                    table.get("cell_content_status") or "unavailable",
                "resolution": resolution,
            }
            record["impact"] = _table_impact(record)

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

    for record in _paragraph_records(movement, routes_by_candidate):
        if record["resolution"]["status"] == "structural_only":
            non_semantic_events.append({
                **record,
                "event_id": f"EVENT-{len(non_semantic_events)+1:06d}",
                "event_type": "STRUCTURAL_ONLY",
            })
        else:
            record["change_id"] = f"CHANGE-{len(changes)+1:06d}"
            changes.append(record)
            resolution = record["resolution"]
            if resolution["needs_review"]:
                queue_review({
                    "review_id": f"REVIEW-{len(review_queue)+1:06d}",
                    "source_kind": "non_table_change",
                    "change_id": record["change_id"],
                    "source_candidate_id": record["source_candidate_id"],
                    "case_type": resolution["case_type"],
                    "resolution": resolution,
                })

    return changes, table_changes, review_queue, non_semantic_events, unresolved_table_reviews


def _keep_previous_ids(
    current: list[dict[str, Any]], previous: list[dict[str, Any]],
    field: str, prefix: str, key: Callable[[dict[str, Any]], Any],
) -> dict[str, str]:
    previous_ids = {key(record): record[field] for record in previous if field in record}
    slot = max((int(value.rsplit("-", 1)[1]) for value in previous_ids.values()), default=0) + 1
    replacements = {}
    for record in current:
        if field not in record:
            continue
        record_id = previous_ids.get(key(record))
        if record_id is None:
            record_id = f"{prefix}-{slot:06d}"
            slot += 1
        replacements[record[field]] = record_id
        record[field] = record_id
    return replacements


def _preserve_previous_identities(
    movement: dict[str, Any], tables: dict[str, Any], routing: dict[str, Any],
    changes: list[dict[str, Any]], reviews: list[dict[str, Any]], events: list[dict[str, Any]],
) -> None:
    if not movement.get("candidate_identity_history"):
        return
    previous_non_table_routes = routing.get("previous_non_table_routes")
    require(isinstance(previous_non_table_routes, list),
            "s14: paragraph identity history requires its Stage 11 routes")
    previous_routes = previous_non_table_routes + [
        route for route in _canonical_routes(routing)
        if route["source_kind"] in {"table_match", "table_residual", "table_review_group"}
    ]
    previous_changes, _, previous_reviews, previous_events, _ = _build_changes(
        previous_candidate_view(movement), tables,
        {route["candidate_id"]: route for route in previous_routes}, {},
    )

    def candidate(record):
        return record.get("source_candidate_id")

    def review_identity(record):
        return (
            record["source_kind"], record["case_type"],
            record.get("source_evidence_id") or candidate(record),
        )

    # Legacy artifact events may share an ordinal with a visible change. Only
    # the visible change IDs can be targets of review links.
    _keep_previous_ids(
        events, previous_changes + previous_events, "change_id", "CHANGE", candidate,
    )
    replacements = _keep_previous_ids(
        changes, previous_changes + previous_events, "change_id", "CHANGE", candidate,
    )
    _keep_previous_ids(reviews, previous_reviews, "review_id", "REVIEW", review_identity)
    _keep_previous_ids(events, previous_events, "event_id", "EVENT", candidate)
    for review in reviews:
        if "change_id" in review:
            review["change_id"] = replacements[review["change_id"]]
        evidence = review.get("evidence") or {}
        if review["source_kind"] == "residual_proposal_group":
            evidence["change_ids"] = sorted(replacements[cid] for cid in evidence["change_ids"])


def _block_source(movement: dict[str, Any], side: str, structure_dir: Path | None):
    comparable = collect_document_elements({
        group: movement.get(group) or []
        for group in ("unchanged", "modified", "modified_review", "duplicate_exact_review", "deleted", "added")
    }, side)
    for group in ("relocated", "resegmentation_review"):
        for candidate in movement.get(group) or []:
            element = candidate.get(side) or {}
            if element.get("element_id"):
                comparable[element["element_id"]] = element
    for group in movement.get("paragraph_resegmentation") or []:
        for element in group.get(f"{side}_elements") or []:
            comparable[element["element_id"]] = element

    if structure_dir is not None:
        path = structure_dir / f"{side}_document_structured.json"
        if path.is_file():
            elements = load_json(path)["document"]["elements"]
            layout, anchors = load_structure_context(structure_dir, side, comparable)
            return elements, layout, anchors
    return comparable.values(), set(), {
        order: element["text"]
        for element in comparable.values()
        if element.get("element_type") in STRUCTURAL_TYPES and element.get("text")
        and not is_layout(element) and (order := reading_order(element)) is not None
    }


def _build_change_blocks(
    changes: list[dict[str, Any]], movement: dict[str, Any], structure_dir: Path | None,
) -> list[dict[str, Any]]:
    blocks = []

    def add_block(run, anchor, contains_heading, side, change_type):
        if len(run) < 3:
            return
        block_id = f"BLOCK-{len(blocks)+1:06d}"
        noun = "section" if contains_heading else "block"
        suffix = "deleted" if change_type == "DELETED" else "added"
        blocks.append({
            "block_id": block_id,
            "change_type": change_type,
            "side": side,
            "change_ids": [item["change_id"] for item in run],
            "element_count": len(run),
            "pages": sorted({
                page for item in run if isinstance(page := item[side].get("primary_page"), int)
            }),
            "anchor": anchor,
            "contains_heading": contains_heading,
            "impact": {
                "version": IMPACT_VERSION,
                "level": "content",
                "priority": "normal",
                "kinds": [f"{noun}_{suffix}"],
                "signals": sorted({signal for item in run for signal in item["impact"]["signals"]}),
                "basis": "deterministic_rule",
                "language_profile": "de-en",
            },
        })
        for item in run:
            item["block_id"] = block_id
            item["impact"].update(
                priority="normal", kinds=sorted({*item["impact"]["kinds"], "block_member"}),
            )

    for side, change_type in (("old", "DELETED"), ("new", "ADDED")):
        by_element = {
            item[side]["element_id"]: item
            for item in changes if item["change_type"] == change_type and item.get(side)
        }
        if len(by_element) < 3:
            continue
        elements, layout, headings = _block_source(movement, side, structure_dir)
        ordered = sorted(
            (element for element in elements if reading_order(element) is not None),
            key=reading_order,
        )
        run = []
        heading = anchor = None
        contains_heading = False
        for element in ordered:
            element_id = element["element_id"]
            if element_id in layout or is_layout(element):
                continue
            heading_text = headings.get(reading_order(element))
            item = by_element.get(element_id)
            if item is None or element.get("element_type") == "table":
                add_block(run, anchor, contains_heading, side, change_type)
                run = []
                contains_heading = False
            else:
                if not run:
                    anchor = heading
                run.append(item)
                contains_heading |= bool(heading_text) or element.get("element_type") in STRUCTURAL_TYPES
            heading = heading_text or heading
        add_block(run, anchor, contains_heading, side, change_type)
    return blocks


def _collapse_block_reviews(
    reviews: list[dict[str, Any]], blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not blocks:
        return reviews
    by_change = {
        review["change_id"]: review for review in reviews
        if review["source_kind"] == "non_table_change"
    }
    block_by_change = {cid: block["block_id"] for block in blocks for cid in block["change_ids"]}
    # A residual group with no counterpart proposal and all members inside one
    # block says only "no counterpart found" for each member; the block entry
    # carries the same uncertainty once. Groups with proposals stay separate.
    absorbed_groups: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        evidence = review.get("evidence") or {}
        member_ids = evidence.get("change_ids") or []
        owners = {block_by_change.get(cid) for cid in member_ids}
        if (
            review["source_kind"] == "residual_proposal_group"
            and member_ids and not evidence.get("proposal_ids")
            and len(owners) == 1 and None not in owners
        ):
            absorbed_groups.setdefault(owners.pop(), []).append(review)
    replacements = {}
    for block in blocks:
        block_id = block["block_id"]
        members = [by_change[cid] for cid in block["change_ids"] if cid in by_change]
        group_reviews = absorbed_groups.get(block_id, [])
        for review in group_reviews:
            members.append({
                **review,
                "change_id": None,
                "_group_change_ids": review["evidence"]["change_ids"],
            })
        if not members:
            continue
        case_types = {review["case_type"] for review in members}
        case_type = members[0]["case_type"] if len(case_types) == 1 else "change_block"
        entry = {
            "source_kind": "change_block",
            "block_id": block_id,
            "change_ids": [
                cid for review in members
                for cid in (review.get("_group_change_ids") or [review["change_id"]])
            ],
            "proposal_group_ids": [review["proposal_group_id"] for review in group_reviews],
            "case_type": case_type,
            "resolution": {
                "route": "UNCERTAIN_RESULT",
                "case_type": case_type,
                "method": "deterministic",
                "status": "review",
                "needs_review": True,
                "result_state": "uncertain",
                "review_reasons": sorted({
                    reason for review in members
                    for reason in review["resolution"].get("review_reasons") or []
                }),
            },
        }
        replacements.update(
            (("change", review["change_id"]), entry) for review in members if review["change_id"]
        )
        replacements.update(
            (("group", review["proposal_group_id"]), entry) for review in group_reviews
        )
    queue = []
    queued = set()
    for review in reviews:
        key = (
            ("group", review.get("proposal_group_id"))
            if review["source_kind"] == "residual_proposal_group"
            else ("change", review.get("change_id"))
        )
        entry = replacements.get(key, review)
        block_id = entry.get("block_id")
        if block_id:
            if block_id in queued:
                continue
            queued.add(block_id)
        entry["review_id"] = f"REVIEW-{len(queue)+1:06d}"
        queue.append(entry)
    return queue


def generic_projection(
    result: dict[str, Any], *, _in_packet_decision: bool = False
) -> dict[str, Any]:
    """Strip all additive domain metadata, preserving generic content.

    Removes the domain profile, per-change verification, the separate domain
    review list, domain status counts, the outcome-migration evidence, the
    embedded projection itself, and any residual authorization key.

    Also strips the mode-derived ``input_hash`` that s12 computes over the
    bounded packet envelope.  The full canonical result keeps this hash inside
    ``resolution.packet_decision``; only the generic projection omits it.
    Unrelated ``input_hash`` keys elsewhere are generic content and survive,
    mirroring the treatment of a nested ``authorization`` source key.
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
    *,
    structure_dir: Path | None = None,
) -> dict[str, Any]:
    """Canonical generic result; domain verification is retired and never gates.

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
    _validate_consumed_routed(movement, tables, routes_by_candidate)

    # Public resolution_for() reads these indexes.
    _INDEXES["routes_by_candidate"] = routes_by_candidate
    _INDEXES["decisions_by_route"] = decisions_by_route

    changes, table_changes, review_queue, non_semantic_events, unresolved_table_reviews = (
        _build_changes(movement, tables, routes_by_candidate, decisions_by_route)
    )
    _preserve_previous_identities(movement, tables, routing, changes, review_queue, non_semantic_events)
    moves = _canonical_moves(movement, changes)
    change_blocks = _build_change_blocks(changes, movement, structure_dir)
    review_queue = _collapse_block_reviews(review_queue, change_blocks)
    change_ids = {item["change_id"] for item in changes}
    block_member_ids = [cid for block in change_blocks for cid in block["change_ids"]]
    require(
        len(set(block_member_ids)) == len(block_member_ids) and set(block_member_ids) <= change_ids,
        "s14: change block members must be unique and refer to visible changes.",
    )

    canonical_items = changes + table_changes
    resolution_status_counts = dict(
        Counter(item["resolution"]["status"] for item in canonical_items)
    )
    result_states = result_state_counts(
        changes, table_changes, review_queue, non_semantic_events
    )
    # Verification is retired; every canonical item is implicitly
    # ``not_applicable`` and no verification records are joined or attached.
    domain_status_counts = {"not_applicable": len(canonical_items)} if canonical_items else {}

    migration = build_outcome_migration(
        baseline or {},
        {
            "domain_status_counts": domain_status_counts,
            "resolution_status_counts": resolution_status_counts,
            "review_queue": len(review_queue),
            "route_counts": (routing.get("statistics") or {}).get("route_counts") or {},
        },
    )

    # Domain verification is retired: the compatibility review list is always
    # empty, so legacy routing enrichment can never surface as live review.
    domain_verification_review: list[dict[str, Any]] = []

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
            "moves": len(moves),
            "change_blocks": len(change_blocks),
            "changes_in_blocks": len(block_member_ids),
            "table_changes": len(table_changes),
            "review_queue": len(review_queue),
            "non_semantic_events": len(non_semantic_events),
            "change_type_counts": dict(Counter(x["change_type"] for x in changes)),
            "impact_level_counts": dict(Counter(x["impact"]["level"] for x in changes)),
            "impact_priority_counts": dict(Counter(x["impact"]["priority"] for x in changes)),
            "table_impact_level_counts": dict(
                Counter(x["impact"]["level"] for x in table_changes)
            ),
            "resolution_status_counts": resolution_status_counts,
            "result_state_counts": result_states,
            "domain_status_counts": domain_status_counts,
        },
        "changes": changes,
        "moves": moves,
        "change_blocks": change_blocks,
        "table_changes": table_changes,
        "review_queue": review_queue,
        "non_semantic_events": non_semantic_events,
        "unresolved_table_reviews": unresolved_table_reviews,
        "domain_profile": resolve_domain_profile(None),
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
    require(
        all(
            x["resolution"].get("result_state")
            == derive_result_state(
                x["resolution"].get("status"),
                x["resolution"].get("needs_review"),
            )
            for x in changes + table_changes
        ),
        "s14 produced an inconsistent normalized result_state.",
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
        movement, tables, routing, decisions, baseline=_load_baseline_metrics(), structure_dir=out
    )

    final_path = out / "comparison_result.json"
    save_json(result, final_path)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", final_path)
    print("Outcome migration (evidence, not a gate):")
    print(json.dumps(result["outcome_migration"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
