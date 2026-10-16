"""Production outcome-migration helper (design spec section 12.2).

This is production code, not evaluation tooling: Stage 14 records how
generic route/resolution counts and CRR domain-status counts moved between the
baseline and the authoritative run. It is a pure function of two dicts with no
imports beyond ``typing``, so no runtime engine module depends on the optional
evaluation suite.
"""
from __future__ import annotations

from typing import Any


def build_outcome_migration(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Map baseline authorization statuses to current domain statuses and
    account for every routing/resolution/route-count delta.

    Explanations are deterministic and describe the mechanism, not individual
    items: generic routing/resolution no longer gates on domain verification;
    generic review is separate from domain verification; the ``authorized``
    status is renamed ``verified`` while semantics carry over.  The report is
    evidence, never a pass/fail gate.
    """
    _as_int = lambda v: v if isinstance(v, (int, float)) else 0
    _as_dict = lambda v: v if isinstance(v, dict) else {}

    explanations = {
        "authorized": (
            "authorized renamed to verified while semantics carry over; "
            "generic resolution no longer gates on domain verification."
        ),
        "ambiguous": (
            "ambiguous status preserved under generic domain verification; "
            "generic review queue is separate from domain verification."
        ),
        "unsupported": (
            "unsupported status preserved under generic domain verification; "
            "generic review queue is separate from domain verification."
        ),
        "not_applicable": (
            "not_applicable status preserved under generic domain verification; "
            "generic resolution is independent of domain verification."
        ),
        "resolution_status_counts": (
            "resolution status count changed because generic routing/resolution "
            "no longer gates on domain verification and deterministic rules "
            "resolve proven non-semantic cases."
        ),
        "review_queue": (
            "review queue length changed because generic review is separate "
            "from domain verification and deterministic rules resolve proven "
            "non-semantic cases."
        ),
        "route_counts": (
            "route count changed because generic routing is independent of "
            "domain verification; counts reflect the routing distribution over "
            "the unchanged candidate set."
        ),
    }

    legacy = baseline.get("authorization_status_counts") or {}
    modern = current.get("domain_status_counts") or {}

    mapped = {"authorized": "verified"}
    carried = {status: status for status in ("ambiguous", "unsupported", "not_applicable")}

    verification_status_deltas: dict[str, Any] = {}
    for legacy_status, new_status in {**mapped, **carried}.items():
        old_count = legacy.get(legacy_status, 0)
        new_count = modern.get(new_status, 0)
        verification_status_deltas[legacy_status] = {
            "mapped_to": new_status,
            "baseline_count": old_count,
            "current_count": new_count,
            "delta": new_count - old_count,
            "explanation": explanations[legacy_status],
        }

    routing_resolution_deltas: dict[str, Any] = {}
    for key in ("resolution_status_counts", "review_queue"):
        old_value = _as_dict(baseline.get(key)) if key == "resolution_status_counts" else _as_int(baseline.get(key))
        new_value = _as_dict(current.get(key)) if key == "resolution_status_counts" else _as_int(current.get(key))
        if key == "resolution_status_counts":
            for status in sorted(set(old_value) | set(new_value)):
                routing_resolution_deltas[f"{key}.{status}"] = {
                    "baseline_count": old_value.get(status, 0),
                    "current_count": new_value.get(status, 0),
                    "delta": new_value.get(status, 0) - old_value.get(status, 0),
                    "explanation": explanations[key],
                }
        else:
            routing_resolution_deltas[key] = {
                "baseline_count": old_value,
                "current_count": new_value,
                "delta": new_value - old_value,
                "explanation": explanations[key],
            }

    route_counts: dict[str, Any] = {}
    old_routes = _as_dict(baseline.get("route_counts"))
    new_routes = _as_dict(current.get("route_counts"))
    for route in sorted(set(old_routes) | set(new_routes)):
        route_counts[route] = {
            "baseline_count": old_routes.get(route, 0),
            "current_count": new_routes.get(route, 0),
            "delta": new_routes.get(route, 0) - old_routes.get(route, 0),
            "explanation": explanations["route_counts"],
        }

    return {
        "mapped": mapped,
        "carried_over": carried,
        "verification_status_deltas": verification_status_deltas,
        "routing_resolution_deltas": routing_resolution_deltas,
        "route_counts": route_counts,
    }
