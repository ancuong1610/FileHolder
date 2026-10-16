"""s12 - bounded document-neutral review packets for SPECIAL_CASE_LLM_OPTIONAL routes.

Auto-converted from 12_build_special_case_packets_v2.ipynb (code cells only).

Packets are document-neutral: they carry old/new text plus matching/structural
evidence, never domain verification. CRR-looking source text is legitimate
document content and is preserved verbatim; no vocabulary filtering and no
verification joins are applied.

Imports are side-effect free: all file access happens inside ``main()`` or
lazily inside ``payload_for_route`` for real s11 routes.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from document_compare.shared.paths import find_project_root
from document_compare.phase1.contracts.validation import require
from document_compare.phase1.contracts.review_identity import structural_route_identity

STEP12_VERSION = "3.0.0-generic-packets"
MAX_TEXT_CHARS = 6000
# Longest packet_id that main() assigns: CASE-999999 (9 + 6 digits).
_MAX_PACKET_ID = "CASE-999999"
GENERIC_OUTCOMES = ["REAL_CHANGE", "ARTIFACT", "UNCERTAIN"]
# Initial per-field cap on the review text fields so old/new content survives
# the whole-packet bound; the serialized ``text`` is hard-bounded regardless.
FIELD_TEXT_CAP = 1200

# Keys whose string values are identifiers/types and must not be truncated.
_IDENTIFIER_KEYS = frozenset({
    "route_id", "candidate_id", "element_id", "old_element_id", "new_element_id",
    "table_match_id", "logical_table_id", "source_table_ids", "review_group_id",
    "packet_id", "input_hash", "case_type", "source_kind", "source_group",
    "classification", "status", "outcome", "article", "source_nodes",
    "allowed_outcomes", "source_candidate_id",
})
# Long lists that can be deterministically truncated during compaction.
_LIST_LIMITS = {
    "changes": 20,
    "shared_provisional_anchor_keys": 20,
    "recent_anchor_keys": 10,
    "changed_cells": 40,
    "source_locations": 50,
}

# lazy movement/tables indexes, keyed by resolved project root path
_INDEXES: dict[str, Any] = {}


def _output_paths() -> dict[str, Path]:
    out = find_project_root() / "output"
    return {
        "routing": out / "review_routing.json",
        "movement": out / "comparison_candidates_with_movement.json",
        "tables": out / "table_comparison_result.json",
        "packets": out / "llm_review_packets.json",
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(value: dict[str, Any], path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _build_indexes(movement: dict[str, Any], tables: dict[str, Any]) -> dict[str, Any]:
    non_table = {}
    for group in ("modified", "modified_review", "duplicate_exact_review", "relocated", "deleted", "added"):
        for item in movement.get(group) or []:
            cid = item.get("candidate_id")
            if cid:
                non_table[cid] = (group, item)

    structural = {}
    for group in ("movement_review", "structural_split_merge_review", "structural_boundary_review", "resegmentation_review"):
        for item in movement.get(group) or []:
            key = structural_route_identity(item)["candidate_id"]
            structural[key] = (group, item)

    table_match = {
        x.get("table_match_id"): x
        for x in tables.get("table_matches") or []
        if x.get("table_match_id")
    }
    table_residual = {}
    for group in ("deleted_table_candidates", "added_table_candidates"):
        for x in tables.get(group) or []:
            if x.get("logical_table_id"):
                table_residual[x["logical_table_id"]] = (group, x)
    table_review = {
        x.get("review_group_id"): x
        for x in tables.get("unresolved_table_groups") or []
        if x.get("review_group_id")
    }
    return {
        "non_table": non_table,
        "structural": structural,
        "table_match": table_match,
        "table_residual": table_residual,
        "table_review": table_review,
    }


def _indexes() -> dict[str, Any]:
    """Lazily load and memoize the movement/table indexes by project root."""
    root = str(find_project_root())
    if root not in _INDEXES:
        paths = _output_paths()
        movement = load_json(paths["movement"])
        tables = load_json(paths["tables"])
        _INDEXES[root] = _build_indexes(movement, tables)
    return _INDEXES[root]


def compact_element(element: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(element, dict):
        return None
    context = element.get("provisional_anchor_context") or {}
    if isinstance(context, dict) and "recent_anchor_keys" in context:
        context = {
            **context,
            "recent_anchor_keys": (context.get("recent_anchor_keys") or [])[:10],
        }
    return {
        "element_id": element.get("element_id"),
        "element_type": element.get("element_type"),
        "text": (element.get("text") or "")[:FIELD_TEXT_CAP],
        "normalized_text": (element.get("normalized_text") or "")[:FIELD_TEXT_CAP],
        "comparison_text": (element.get("comparison_text") or "")[:FIELD_TEXT_CAP],
        "primary_page": element.get("primary_page"),
        "reading_order": element.get("reading_order"),
        "provisional_anchor_context": context,
        "extraction_validation": element.get("extraction_validation"),
    }


def _compact_changes(changes: Any) -> list[dict[str, Any]]:
    """Keep a small deterministic summary of token diff changes."""
    if not isinstance(changes, list):
        return []
    compacted = []
    for ch in changes[:10]:
        if not isinstance(ch, dict):
            continue
        compacted.append({
            "operation": ch.get("operation"),
            "old_token_start": ch.get("old_token_start"),
            "old_token_end": ch.get("old_token_end"),
            "new_token_start": ch.get("new_token_start"),
            "new_token_end": ch.get("new_token_end"),
            "old_text": ch.get("old_text"),
            "new_text": ch.get("new_text"),
        })
    return compacted


def _compact_token_diff(token_diff: Any) -> Any:
    if not isinstance(token_diff, dict):
        return token_diff
    result: dict[str, Any] = {}
    for key in ("source_token_diff", "normalized_token_diff", "comparison_token_diff"):
        sub = token_diff.get(key)
        if isinstance(sub, dict):
            result[key] = {
                "old_token_count": sub.get("old_token_count"),
                "new_token_count": sub.get("new_token_count"),
                "changed_old_token_count": sub.get("changed_old_token_count"),
                "changed_new_token_count": sub.get("changed_new_token_count"),
                "change_operation_count": sub.get("change_operation_count"),
                "token_sequence_ratio": sub.get("token_sequence_ratio"),
                "changes": _compact_changes(sub.get("changes")),
            }
    return result


def _compact_similarity_evidence(evidence: Any) -> Any:
    if not isinstance(evidence, dict):
        return evidence
    return {
        "score": evidence.get("score"),
        "char_ratio": evidence.get("char_ratio"),
        "token_f1": evidence.get("token_f1"),
        "length_ratio": evidence.get("length_ratio"),
        "same_element_type": evidence.get("same_element_type"),
        "reciprocal_best": evidence.get("reciprocal_best"),
        "minimum_best_margin": evidence.get("minimum_best_margin"),
        "context_used_as_similarity_boost": evidence.get("context_used_as_similarity_boost"),
    }


def _compact_matching_evidence(evidence: Any) -> Any:
    if not isinstance(evidence, dict):
        return evidence
    return {
        "combined_similarity": evidence.get("combined_similarity"),
        "source_assignment_ambiguous": evidence.get("source_assignment_ambiguous"),
    }


def compact_table(table: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(table, dict):
        return None
    return {
        "logical_table_id": table.get("logical_table_id"),
        "source_table_ids": table.get("source_table_ids"),
        "row_count": table.get("row_count"),
        "column_count": table.get("column_count"),
        "header_signature": table.get("header_signature"),
        "first_reading_order": table.get("first_reading_order"),
        "requires_extraction_review": table.get("requires_extraction_review"),
    }


def compact_structural(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "candidate_id": item.get("candidate_id"),
        "review_id": item.get("review_id"),
        "review_reasons": item.get("review_reasons") or [],
        "similarity": item.get("similarity"),
        "case_type": item.get("case_type"),
    }


def compact_table_review(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    old_tables = item.get("old_tables") or []
    new_tables = item.get("new_tables") or []
    return {
        "review_group_id": item.get("review_group_id"),
        "reason": item.get("reason"),
        "old_table_ids": [t.get("logical_table_id") for t in old_tables if isinstance(t, dict)],
        "new_table_ids": [t.get("logical_table_id") for t in new_tables if isinstance(t, dict)],
    }


def _build_payload(route: dict[str, Any]) -> dict[str, Any]:
    """Evidence body for a real s11 route or a direct candidate dict."""
    source_kind = route.get("source_kind")
    if source_kind is None:
        # Direct candidate contract (plan example): old_text/new_text/evidence.
        return {
            "source_kind": None,
            "old_text": str(route.get("old_text") or ""),
            "new_text": str(route.get("new_text") or ""),
            "evidence": route.get("evidence"),
        }

    indexes = _indexes()
    if source_kind == "non_table":
        group, item = indexes["non_table"][route["candidate_id"]]
        return {
            "source_kind": "non_table",
            "source_group": group,
            "old": compact_element(item.get("old")),
            "new": compact_element(item.get("new")),
            "similarity": item.get("similarity"),
            "similarity_evidence": _compact_similarity_evidence(item.get("similarity_evidence")),
            "token_diff": _compact_token_diff(item.get("token_diff")),
            "upstream_review_reasons": item.get("review_reasons") or [],
        }

    if source_kind == "structural_evidence":
        group, item = indexes["structural"][route["candidate_id"]]
        return {
            "source_kind": "structural_evidence",
            "source_group": group,
            "evidence": compact_structural(item),
        }

    if source_kind == "table_match":
        item = indexes["table_match"][route["candidate_id"]]
        changed_cells = [
            c for c in item.get("cell_changes") or []
            if c.get("classification") != "UNCHANGED"
        ]
        return {
            "source_kind": "table_match",
            "source_group": "table_matches",
            "table_match_id": route["candidate_id"],
            "classification": item.get("classification"),
            "old": compact_table(item.get("old")),
            "new": compact_table(item.get("new")),
            "matching_evidence": _compact_matching_evidence(item.get("matching_evidence")),
            "context_relation": item.get("context_relation"),
            "review_reasons": item.get("review_reasons") or [],
            "changed_cells": changed_cells[:80],
        }

    if source_kind == "table_residual":
        group, item = indexes["table_residual"][route["candidate_id"]]
        return {
            "source_kind": "table_residual",
            "source_group": group,
            "table": compact_table(item),
        }

    if source_kind == "table_review_group":
        return {
            "source_kind": "table_review_group",
            "source_group": "unresolved_table_groups",
            "evidence": compact_table_review(indexes["table_review"][route["candidate_id"]]),
        }

    raise KeyError(f"Unsupported route source_kind={source_kind!r}")


def _review_reasons(
    route: dict[str, Any], domain_profile: dict[str, Any] | None
) -> list[str]:
    """Route review reasons, preserved verbatim.

    ``domain_profile`` is accepted for signature compatibility only. Review
    reasons and source text are never vocabulary-filtered: words such as
    ``authorization`` or ``legal verification`` are legitimate document
    content.
    """
    return list(route.get("review_reasons") or [])


def _build_body(
    route: dict[str, Any], domain_profile: dict[str, Any] | None
) -> dict[str, Any]:
    """Full review body: generic evidence + identity metadata only."""
    body = _build_payload(route)
    body.update({
        "route_id": route.get("route_id"),
        "candidate_id": route.get("candidate_id"),
        "case_type": route.get("case_type"),
        "review_reasons": _review_reasons(route, domain_profile),
        "allowed_outcomes": list(GENERIC_OUTCOMES),
    })
    if route.get("source_candidate_id") is not None:
        body["source_candidate_id"] = route["source_candidate_id"]
    return body


def _serialize(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _cap_text_fields(obj: Any, cap: int) -> Any:
    """Recursively cap string values that are not identifier fields."""
    if isinstance(obj, dict):
        return {
            k: (
                v[:cap]
                if isinstance(v, str) and k not in _IDENTIFIER_KEYS
                else _cap_text_fields(v, cap)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_cap_text_fields(v, cap) for v in obj]
    return obj


def _limit_lists(obj: Any) -> Any:
    """Recursively truncate long lists configured in ``_LIST_LIMITS``."""
    if isinstance(obj, dict):
        return {
            k: (
                v[:_LIST_LIMITS[k]]
                if isinstance(v, list) and k in _LIST_LIMITS
                else _limit_lists(v)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_limit_lists(v) for v in obj]
    return obj


def _compact_body(body: dict[str, Any], cap: int) -> dict[str, Any]:
    """Apply deterministic compaction: cap text, then truncate long lists."""
    compact = _cap_text_fields(body, cap)
    compact = _limit_lists(compact)
    return compact


def _minimal_side(side: Any) -> Any:
    if isinstance(side, dict):
        result = {}
        for key in ("element_id", "element_type", "logical_table_id", "text"):
            if key in side:
                result[key] = side[key]
                if key == "text" and isinstance(result[key], str):
                    result[key] = result[key][:40]
        return result
    if isinstance(side, str):
        return side[:40]
    return side


def _minimal_body(body: dict[str, Any]) -> dict[str, Any]:
    """Last-resort body preserving identity, old/new, and key classification."""
    result: dict[str, Any] = {
        "route_id": body.get("route_id"),
        "candidate_id": body.get("candidate_id"),
        "case_type": body.get("case_type"),
        "review_reasons": body.get("review_reasons"),
        "allowed_outcomes": body.get("allowed_outcomes"),
        "source_kind": body.get("source_kind"),
        "source_group": body.get("source_group"),
    }
    if body.get("source_candidate_id") is not None:
        result["source_candidate_id"] = body["source_candidate_id"]
    # Direct candidate contract uses old_text/new_text; s11 routes use old/new dicts.
    if "old_text" in body or "new_text" in body:
        result["old_text"] = _minimal_side(body.get("old_text"))
        result["new_text"] = _minimal_side(body.get("new_text"))
    else:
        result["old"] = _minimal_side(body.get("old"))
        result["new"] = _minimal_side(body.get("new"))
    # Preserve table/structural evidence when old/new are not present.
    for key in ("table", "evidence"):
        if key in body:
            result[key] = _minimal_side(body.get(key))
    similarity = body.get("similarity")
    if isinstance(similarity, (int, float)):
        result["similarity"] = similarity
    classification = body.get("classification")
    if classification is not None:
        result["classification"] = classification
    return result


def _packet_envelope(
    text: str, route: dict[str, Any], domain_profile: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "route_id": route.get("route_id"),
        "candidate_id": route.get("candidate_id"),
        "case_type": route.get("case_type"),
        "review_reasons": _review_reasons(route, domain_profile),
        "allowed_outcomes": list(GENERIC_OUTCOMES),
        "text": text,
    }


def payload_for_route(
    route: dict[str, Any], domain_profile: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build one bounded document-neutral review packet for a route.

    ``route`` is either a real s11 route (``source_kind`` + ``candidate_id``)
    or a direct candidate carrying ``old_text``/``new_text``/``evidence``.
    The returned packet contains envelope metadata plus a single ``text``
    field whose value is a valid, canonical, deterministic JSON string
    bounded so that the full packet serializes to <= ``MAX_TEXT_CHARS``.
    No domain verification or evidence is added.
    """
    domain_profile = domain_profile or {"mode": "generic"}
    body = _build_body(route, domain_profile)

    # Deterministic compaction: shrink text caps until the full packet fits.
    caps = [FIELD_TEXT_CAP, 600, 300, 150, 80, 40]
    for cap in caps:
        compact = _compact_body(body, cap)
        text = _serialize(compact)
        envelope = _packet_envelope(text, route, domain_profile)
        packet = {"packet_id": _MAX_PACKET_ID, "input_hash": "0" * 64, **envelope}
        if len(_serialize(packet)) <= MAX_TEXT_CHARS:
            input_hash = hashlib.sha256(_serialize(envelope).encode("utf-8")).hexdigest()
            return {"packet_id": None, "input_hash": input_hash, **envelope}

    # Last resort: keep only identity, old/new, and key classification.
    compact = _minimal_body(body)
    text = _serialize(compact)
    envelope = _packet_envelope(text, route, domain_profile)
    packet = {"packet_id": _MAX_PACKET_ID, "input_hash": "0" * 64, **envelope}
    if len(_serialize(packet)) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Cannot bound packet for route {route.get('route_id')!r} "
            f"(final size {len(_serialize(packet))} > {MAX_TEXT_CHARS})."
        )
    input_hash = hashlib.sha256(_serialize(envelope).encode("utf-8")).hexdigest()
    return {"packet_id": None, "input_hash": input_hash, **envelope}


def main() -> None:
    paths = _output_paths()
    routing = load_json(paths["routing"])
    if routing.get("comparison_stage") != "deterministic_review_routing":
        raise ValueError("Step 12 requires Step 11 routing output.")

    # Canonical s11 ``routes``; the aliases are only a compatibility fallback.
    routes = routing.get("routes")
    if routes is None:
        routes = (routing.get("non_table_routes") or []) + (routing.get("table_routes") or [])

    domain_profile = routing.get("domain_profile") or {"mode": "generic"}

    # Packets never carry legacy domain metadata, even if a stale routing
    # artifact attached verification/authorization fields to a route object.
    routes = [
        {k: v for k, v in route.items() if k not in ("domain_verification", "authorization")}
        for route in routes
    ]

    eligible = [
        route for route in routes
        if route.get("route") == "SPECIAL_CASE_LLM_OPTIONAL"
    ]

    packets = []
    for route in eligible:
        packet = payload_for_route(route, domain_profile=domain_profile)
        packet["packet_id"] = f"CASE-{len(packets)+1:06d}"
        packets.append(packet)

    result = {
        "schema_version": "1.0.0",
        "comparison_stage": "special_case_packet_generation",
        "processing": {
            "version": STEP12_VERSION,
            "llm_called": False,
            "one_case_per_packet": True,
        },
        "statistics": {
            "eligible_routes": len(eligible),
            "packets": len(packets),
            "case_type_counts": dict(Counter(p["case_type"] for p in packets)),
        },
        "packets": packets,
    }

    require(len({p["packet_id"] for p in packets}) == len(packets), "s12: duplicate packet_id")
    require(len({p["route_id"] for p in packets}) == len(packets), "s12: duplicate route_id")

    save_json(result, paths["packets"])
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", paths["packets"])


if __name__ == "__main__":
    main()
