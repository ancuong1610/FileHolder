"""Stage 09: movement and structural reconciliation evidence from Step 8.

Input  : ``<root>/output/comparison_candidates.json`` (Step 8 candidate
         assembly result).

Work   : Reconciles residual relocations and paragraph splits/merges, detects
         relative-order moves among established pairs, and retains ambiguous
         structural evidence for review.

Output : ``<root>/output/comparison_candidates_with_movement.json`` plus
         console summaries.

The module is import-safe: importing it only defines the pure movement
helpers and never resolves a workspace, reads or writes pipeline files,
prints, or mutates the environment. File orchestration lives in
:func:`main`, which the ``__name__ == "__main__"`` guard runs for the CLI
stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.contracts.validation import extraction_validation_state, require

# ---- imports and configuration constants ----

import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from document_compare.phase1.matching.element_types import matching_family, type_change_fields
from document_compare.phase1.matching.impact import editorial_tokens, glyph_fold
from document_compare.phase1.matching.moves import detect_moves, preceding_heading_order
from document_compare.phase1.matching.reconciliation import reconcile_residuals
from document_compare.phase1.matching.residual_proposals import (
    build_residual_proposals,
)
from document_compare.phase1.matching.semantic_evidence import reassess_residual

STEP9_VERSION = "2.1.0-block-movement-conservative"
INPUT_STAGE = "comparison_candidate_assembly_and_multi_representation_token_diff"
OUTPUT_STAGE = "movement_and_structural_reconciliation_evidence"

COMPARISON_CANDIDATES_FILENAME = "comparison_candidates.json"
MOVEMENT_OUTPUT_FILENAME = "comparison_candidates_with_movement.json"

STRUCTURAL_TYPES = {"heading", "title"}
BODY_PARAGRAPH_TYPES = {"paragraph", "list_item"}
SPACE_PATTERN = re.compile(r"\s+", re.UNICODE)
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)

MIN_BLOCK_SIZE = 3
MAX_BLOCK_SIZE = 12
MIN_BLOCK_POSITION_SHIFT = 8
MIN_EXTERNAL_CROSSINGS = 3

# ---- I/O helpers and input validation ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
    return value


def save_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def validate_input(value: dict[str, Any]) -> None:
    if value.get("comparison_stage") != INPUT_STAGE:
        raise ValueError(
            "Step 9 v2 requires Step 8 stage "
            f"{INPUT_STAGE!r}; got "
            f"{value.get('comparison_stage')!r}."
        )
    for key in (
        "unchanged",
        "modified",
        "modified_review",
        "duplicate_exact_review",
        "deleted",
        "added",
    ):
        if not isinstance(value.get(key), list):
            raise ValueError(f"Missing Step-8 list field {key!r}.")


# ---- element keys and candidate group collection ----
def element_text(element: dict[str, Any] | None) -> str:
    if not element:
        return ""
    for field in ("comparison_text", "normalized_text", "text"):
        value = element.get(field)
        if isinstance(value, str) and value.strip():
            return SPACE_PATTERN.sub(" ", value).strip()
    return ""


def element_key(
    element: dict[str, Any] | None,
) -> tuple[str, str] | None:
    if not element:
        return None
    element_type = element.get("element_type")
    text = element_text(element)
    if not element_type or not text:
        return None
    return matching_family(element), text


def reading_order(element: dict[str, Any] | None) -> int | None:
    if not element:
        return None
    value = element.get("reading_order")
    return value if isinstance(value, int) else None


def page_number(element: dict[str, Any] | None) -> int | None:
    if not element:
        return None
    for field in ("page_number", "page"):
        value = element.get(field)
        if isinstance(value, int):
            return value
    location = element.get("location") or {}
    value = location.get("page")
    return value if isinstance(value, int) else None


def token_count(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text or ""))


def all_candidate_groups(
    value: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    return [
        value["unchanged"],
        value["modified"],
        value["modified_review"],
        value["duplicate_exact_review"],
        value["deleted"],
        value["added"],
    ]


def collect_document_elements(
    value: dict[str, Any],
    side: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for group in all_candidate_groups(value):
        for candidate in group:
            element = candidate.get(side)
            if not isinstance(element, dict):
                continue
            element_id = element.get("element_id")
            if element_id:
                output[str(element_id)] = element
    return output


def global_key_counts(
    elements: dict[str, dict[str, Any]],
) -> Counter:
    return Counter(
        key
        for element in elements.values()
        if (key := element_key(element)) is not None
    )


def build_residual_index(
    candidates: list[dict[str, Any]],
    side: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        key = element_key(candidate.get(side))
        if key is not None:
            index[key].append(candidate)
    return index


def paired_structural_candidates(
    value: dict[str, Any],
) -> list[dict[str, Any]]:
    groups = (
        value["unchanged"]
        + value["modified"]
        + value["modified_review"]
        + value["duplicate_exact_review"]
    )

    output = []
    for candidate in groups:
        old = candidate.get("old")
        new = candidate.get("new")

        if not isinstance(old, dict) or not isinstance(new, dict):
            continue

        if old.get("element_type") not in STRUCTURAL_TYPES:
            continue
        if new.get("element_type") not in STRUCTURAL_TYPES:
            continue

        if reading_order(old) is None or reading_order(new) is None:
            continue

        output.append(candidate)

    return output


def block_internal_order_preserved(block: list[dict[str, Any]]) -> bool:
    new_ranks = [item["new_rank"] for item in block]
    return all(
        left < right
        for left, right in zip(new_ranks, new_ranks[1:])
    )


def block_is_contiguous_in_new(block: list[dict[str, Any]]) -> bool:
    new_ranks = [item["new_rank"] for item in block]
    return (
        max(new_ranks) - min(new_ranks) + 1
        == len(new_ranks)
    )


def external_crossing_count(
    block: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
) -> int:
    block_ids = {item["candidate_id"] for item in block}

    old_min = min(item["old_rank"] for item in block)
    old_max = max(item["old_rank"] for item in block)
    new_min = min(item["new_rank"] for item in block)
    new_max = max(item["new_rank"] for item in block)

    crossings = 0

    for other in all_records:
        if other["candidate_id"] in block_ids:
            continue

        old_rank = other["old_rank"]
        new_rank = other["new_rank"]

        # Outside before in OLD, but after block in NEW.
        before_to_after = (
            old_rank < old_min
            and new_rank > new_max
        )

        # Outside after in OLD, but before block in NEW.
        after_to_before = (
            old_rank > old_max
            and new_rank < new_min
        )

        if before_to_after or after_to_before:
            crossings += 1

    return crossings


def is_structural_candidate(
    candidate: dict[str, Any],
    side: str,
) -> bool:
    """True when the candidate's element on ``side`` is a structural element.

    Structural residuals are owned by exact structural split/merge discovery,
    so the generic 1:1 resegmentation reconciler must leave them alone.
    """
    element = candidate.get(side)
    return (
        isinstance(element, dict)
        and element.get("element_type") in STRUCTURAL_TYPES
    )


def ordered_residual_structural(
    candidates: list[dict[str, Any]],
    side: str,
) -> list[dict[str, Any]]:
    output = [
        candidate
        for candidate in candidates
        if isinstance(candidate.get(side), dict)
        and candidate[side].get("element_type") in STRUCTURAL_TYPES
        and reading_order(candidate[side]) is not None
    ]
    return sorted(output, key=lambda c: reading_order(c[side]))


def nearby_pair(
    first: dict[str, Any],
    second: dict[str, Any],
    side: str,
) -> bool:
    first_element = first[side]
    second_element = second[side]
    first_order = reading_order(first_element)
    second_order = reading_order(second_element)

    if first_order is None or second_order is None:
        return False

    # Exact split/merge evidence only among very nearby source elements.
    if second_order - first_order > 3:
        return False

    first_page = page_number(first_element)
    second_page = page_number(second_element)
    if (
        first_page is not None
        and second_page is not None
        and abs(second_page - first_page) > 1
    ):
        return False

    return True


def joined_text(
    first: dict[str, Any],
    second: dict[str, Any],
    side: str,
) -> str:
    return SPACE_PATTERN.sub(
        " ",
        f"{element_text(first[side])} {element_text(second[side])}"
    ).strip()


def structural_boundary_evidence(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    old = candidate.get("old")
    new = candidate.get("new")

    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    if old.get("element_type") not in STRUCTURAL_TYPES:
        return None
    if new.get("element_type") not in STRUCTURAL_TYPES:
        return None

    old_text = element_text(old)
    new_text = element_text(new)

    if not old_text or not new_text or old_text == new_text:
        return None

    longer_side = None
    fragment = None

    if old_text.endswith(" " + new_text):
        fragment = old_text[: -(len(new_text) + 1)].strip()
        longer_side = "old"
    elif new_text.endswith(" " + old_text):
        fragment = new_text[: -(len(old_text) + 1)].strip()
        longer_side = "new"
    elif old_text.startswith(new_text + " "):
        fragment = old_text[len(new_text):].strip()
        longer_side = "old"
    elif new_text.startswith(old_text + " "):
        fragment = new_text[len(old_text):].strip()
        longer_side = "new"
    else:
        return None

    # Only a short boundary fragment is accepted as structural evidence.
    fragment_tokens = TOKEN_PATTERN.findall(fragment)
    if not fragment_tokens or len(fragment_tokens) > 4:
        return None

    return {
        "evidence_type": "short_exact_structural_boundary_fragment",
        "longer_side": longer_side,
        "boundary_fragment": fragment,
        "boundary_fragment_token_count": len(fragment_tokens),
    }


def ids_from_records(
    records: list[dict[str, Any]],
    side: str,
) -> list[str]:
    output = []
    for record in records:
        element = record.get(side)
        if isinstance(element, dict) and element.get("element_id"):
            output.append(str(element["element_id"]))
    return output


def load_structure_context(
    output_dir: Path, side: str, elements: dict[str, dict[str, Any]],
) -> tuple[set[str], dict[int, str]]:
    """Reuse Stage 05 layout membership and provisional headings when available."""
    path = output_dir / f"{side}_document_structured.json"
    if not path.is_file():
        # Also support Stage 09 replay from a standalone Stage 08 artifact.
        return set(), {
            order: element["text"]
            for element in elements.values()
            if element.get("element_type") in STRUCTURAL_TYPES and element.get("text")
            and (order := reading_order(element)) is not None
        }
    document = load_json(path)["document"]
    evidence = document.get("structure_evidence") or {}
    membership = (evidence.get("membership") or {}).get("by_element") or {}
    furniture = (evidence.get("furniture") or {}).get("by_element") or {}
    # Recurrence-only evidence is provisional. Stage 06's comparable set
    # reflects the actual pair, including replays of older Stage 05 artifacts.
    comparable_recurrence_ids = {
        element_id for element_id in elements
        if furniture.get(element_id, {}).get("evidence_path") == "recurrence_edge_only"
    }
    layout_ids = (set(furniture) | {
        element_id for element_id, member in membership.items()
        if member["category"] == "layout"
    }) - comparable_recurrence_ids
    anchors = {
        anchor["reading_order"]: anchor["text"]
        for anchor in document.get("provisional_anchors") or []
        if anchor.get("text") and reading_order(anchor) is not None
        and anchor["element_id"] not in layout_ids
    }
    for element in document["elements"]:
        if (
            element.get("element_type") in STRUCTURAL_TYPES and element.get("text")
            and (order := reading_order(element)) is not None
            and element["element_id"] not in layout_ids
        ):
            anchors.setdefault(order, element["text"])
    return layout_ids, anchors


def detect_paragraph_resegmentation(
    candidates: list[dict[str, Any]], layout_ids: dict[str, set[str]],
    anchors: dict[str, dict[int, str]] | None = None,
) -> list[dict[str, Any]]:
    """Find unambiguous 1:2–4 groups within one section and with usable extraction."""
    by_order = {side: {} for side in ("old", "new")}
    headings = {side: dict((anchors or {}).get(side, {})) for side in by_order}
    for candidate in candidates:
        for side in by_order:
            element = candidate.get(side) or {}
            order = reading_order(element)
            if order is None or not (element.get("text") or "").strip():
                continue
            if element["element_id"] in layout_ids[side]:
                continue
            if element.get("element_type") in STRUCTURAL_TYPES:
                headings[side].setdefault(order, element["text"])
            elif (
                element.get("element_type") in BODY_PARAGRAPH_TYPES
                and extraction_validation_state(element) == "ok"
            ):
                by_order[side][order] = candidate

    heading_orders = {side: sorted(headings[side]) for side in by_order}
    heading_keys = {side: {} for side in by_order}
    for side, orders in by_order.items():
        for order, candidate in orders.items():
            preceding = preceding_heading_order(heading_orders[side], order)
            text = headings[side].get(preceding)
            if not heading_orders[side]:
                context = candidate[side].get("provisional_anchor_context") or {}
                text = context.get("nearest_anchor_key")
            heading_keys[side][order] = glyph_fold(text).casefold() if text else None

    proposals = []
    ambiguous_ids = set()
    for whole_side, part_side, kind in (
        ("old", "new", "paragraph_split"),
        ("new", "old", "paragraph_merge"),
    ):
        lone_wholes = defaultdict(list)
        whole_keys = {}
        for order, candidate in by_order[whole_side].items():
            tokens = tuple(editorial_tokens(candidate[whole_side]["text"]))
            key = (tokens, heading_keys[whole_side][order])
            whole_keys[candidate["candidate_id"]] = key
            if not candidate.get(part_side):
                lone_wholes[key].append(candidate)

        # Keep at most one proposal per bucket. A second compatible assignment
        # makes the bucket ambiguous and leaves all its sources available for review.
        by_key = {}
        blocked_whole_buckets = set()
        for start, first in sorted(by_order[part_side].items()):
            heading = heading_keys[part_side][start]
            parts = [first]
            first_page = first[part_side].get("primary_page")
            for order in range(start + 1, start + 4):
                part = by_order[part_side].get(order)
                if part is None or heading_keys[part_side][order] != heading:
                    break
                page = part[part_side].get("primary_page")
                if (
                    isinstance(first_page, int)
                    and isinstance(page, int)
                    and abs(page - first_page) > 1
                ):
                    break
                parts.append(part)
                part_elements = [item[part_side] for item in parts]
                joined = " ".join(element["text"] for element in part_elements)
                key = (tuple(editorial_tokens(joined)), heading)
                if not key[0]:
                    continue

                paired_parts = [item for item in parts if item.get(whole_side)]
                if paired_parts:
                    # Releasing a prior pair must account for both of its
                    # elements, and its part must be at an end of the group.
                    partner = paired_parts[0]
                    if (
                        len(paired_parts) != 1 or (partner is not parts[0] and partner is not parts[-1])
                        or whole_keys.get(partner["candidate_id"]) != key
                    ):
                        continue
                    wholes = [partner]
                else:
                    wholes = lone_wholes.get(key)
                    if not wholes:
                        continue

                ids = {item["candidate_id"] for item in parts}
                if key in by_key or len(wholes) > 1:
                    previous = by_key.get(key)
                    if previous is not None:
                        ambiguous_ids.update(previous["source_candidate_ids"])
                    by_key[key] = None
                    ambiguous_ids.update(ids)
                    if paired_parts:
                        ambiguous_ids.add(wholes[0]["candidate_id"])
                    elif key not in blocked_whole_buckets:
                        ambiguous_ids.update(whole["candidate_id"] for whole in wholes)
                        blocked_whole_buckets.add(key)
                    continue

                whole = wholes[0]
                elements = {whole_side: [whole[whole_side]], part_side: part_elements}
                by_key[key] = {
                    "kind": kind,
                    "old_elements": elements["old"],
                    "new_elements": elements["new"],
                    "source_candidate_ids": sorted({whole["candidate_id"], *ids}),
                    "impact_level": (
                        "non_content" if glyph_fold(whole[whole_side]["text"]) == glyph_fold(joined)
                        else "editorial"
                    ),
                    "nearest_heading_keys": {
                        side: [heading] * len(items) for side, items in elements.items()
                    },
                }
        proposals.extend(item for item in by_key.values() if item is not None)

    # Exact glyph equality takes precedence over editorial equivalence when
    # otherwise unambiguous proposals overlap across different buckets.
    groups = []
    consumed = ambiguous_ids
    for level in ("non_content", "editorial"):
        eligible = [
            item for item in proposals if item["impact_level"] == level
            and consumed.isdisjoint(item["source_candidate_ids"])
        ]
        counts = Counter(cid for item in eligible for cid in item["source_candidate_ids"])
        for item in eligible:
            if all(counts[cid] == 1 for cid in item["source_candidate_ids"]):
                groups.append({
                    **item,
                    "candidate_id": f"PARAGRAPH-RESEG-{len(groups) + 1:06d}",
                })
                consumed.update(item["source_candidate_ids"])
    return groups


def annotate_residuals(
    remaining_deleted: list[dict[str, Any]],
    remaining_added: list[dict[str, Any]],
    matched_pairs: list[dict[str, Any]],
    previous_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach bounded proposal evidence without consuming candidates."""
    proposal_result = build_residual_proposals(
        remaining_deleted,
        remaining_added,
        matched_pairs=matched_pairs,
    )
    previous_ids = {
        (tuple(group["deleted_candidate_ids"]), tuple(group["added_candidate_ids"])): group["group_id"]
        for group in previous_groups
    }
    if previous_ids:
        next_group_number = len(previous_ids) + 1
        group_ids = {}
        for group in proposal_result["groups"]:
            members = (tuple(group["deleted_candidate_ids"]), tuple(group["added_candidate_ids"]))
            group_id = previous_ids.get(members)
            if group_id is None:
                group_id = f"RESIDUAL-GROUP-{next_group_number:06d}"
                next_group_number += 1
            group_ids[group["group_id"]] = group_id
            group["group_id"] = group_id
        proposal_result["candidate_groups"] = {
            candidate_id: group_ids[group_id]
            for candidate_id, group_id in proposal_result["candidate_groups"].items()
        }

    proposals_by_deleted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    proposals_by_added: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in proposal_result["proposals"]:
        proposals_by_deleted[proposal["deleted_candidate_id"]].append(proposal)
        proposals_by_added[proposal["added_candidate_id"]].append(proposal)

    for side, candidates, proposal_index in (
        ("deleted", remaining_deleted, proposals_by_deleted),
        ("added", remaining_added, proposals_by_added),
    ):
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            coverage = proposal_result["candidate_coverage"].get(
                candidate_id,
                {
                    "status": "no_opposite",
                    "examined": 0,
                    "unsearched": 0,
                    "opposite_residuals": 0,
                    "budget_exhausted": False,
                    "limitations": [],
                },
            )
            proposals = sorted(
                proposal_index.get(candidate_id, []),
                key=lambda item: item["proposal_id"],
            )
            candidate["residual_counterpart_proposals"] = proposals
            candidate["residual_proposal_coverage"] = coverage
            group_id = proposal_result["candidate_groups"].get(candidate_id)
            if group_id:
                candidate["residual_proposal_group_id"] = group_id
            if proposals:
                correspondence = "proposed"
            elif coverage["status"] == "partial":
                correspondence = "unresolved"
            else:
                correspondence = "not_applicable"
            element = candidate.get("old") if side == "deleted" else candidate.get("new")
            candidate["semantic_assessment"] = reassess_residual(
                element,
                side="old" if side == "deleted" else "new",
                correspondence=correspondence,
                limitations=list(coverage.get("limitations") or []),
            )
    return proposal_result


def previous_candidate_view(movement: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the prior routing inputs for identity allocation only."""
    replaced = movement.get("candidate_identity_history") or {}
    prior = {
        **movement,
        "paragraph_resegmentation": [],
        "candidate_identity_history": {},
    }
    for group, history in replaced.items():
        candidates = {item["candidate_id"]: item for item in movement.get(group) or []}
        candidates.update(history["replaced"])
        prior[group] = [candidates[candidate_id] for candidate_id in history["order"]]
    return prior


# ---- stage entry point ----
def main() -> None:
    """Run Stage 09 against the workspace and save the movement evidence."""
    project_root = find_project_root()
    output_dir = project_root / "output"
    input_path = output_dir / COMPARISON_CANDIDATES_FILENAME
    output_path = output_dir / MOVEMENT_OUTPUT_FILENAME

    if not input_path.exists():
        raise FileNotFoundError(
            f"Missing {input_path}. Run Step 8 first."
        )

    print("Step 9 version:", STEP9_VERSION)
    print("Input:", input_path)
    print("Output:", output_path)

    comparison_candidates = load_json(input_path)
    validate_input(comparison_candidates)

    print("Loaded compatible Step-8 result.")
    print(json.dumps(comparison_candidates.get("statistics") or {}, indent=2))

    all_old = collect_document_elements(comparison_candidates, "old")
    all_new = collect_document_elements(comparison_candidates, "new")

    print("Collected comparable elements:", len(all_old), "OLD /", len(all_new), "NEW")

    layout_ids, anchors = {}, {}
    for side, elements in (("old", all_old), ("new", all_new)):
        layout_ids[side], anchors[side] = load_structure_context(output_dir, side, elements)

    old_global_counts = global_key_counts(all_old)
    new_global_counts = global_key_counts(all_new)

    deleted_index = build_residual_index(comparison_candidates["deleted"], "old")
    added_index = build_residual_index(comparison_candidates["added"], "new")

    relocated: list[dict[str, Any]] = []
    consumed_deleted_ids: set[str] = set()
    consumed_added_ids: set[str] = set()

    for key in sorted(set(deleted_index).intersection(added_index)):
        old_candidates = deleted_index[key]
        new_candidates = added_index[key]

        # Hard safety rule:
        # The family/text key must be globally unique on both documents.
        if old_global_counts[key] != 1 or new_global_counts[key] != 1:
            continue

        if len(old_candidates) != 1 or len(new_candidates) != 1:
            continue

        old_candidate = old_candidates[0]
        new_candidate = new_candidates[0]
        old = old_candidate["old"]
        new = new_candidate["new"]

        old_order = reading_order(old)
        new_order = reading_order(new)
        old_page = page_number(old)
        new_page = page_number(new)

        relocation = {
            "candidate_id": f"RELOCATED-{len(relocated) + 1:06d}",
            "candidate_type": "RELOCATED_UNCHANGED",
            "old": old,
            "new": new,
            **type_change_fields(old, new),
            "needs_review": False,
            "review_reasons": [],
            "movement_classification": "RELOCATED_UNCHANGED",
            "movement_evidence": {
                "evidence_type": "globally_unique_exact_residual_relocation",
                "identical_element_type": old.get("element_type") == new.get("element_type"),
                "identical_comparison_text": True,
                "globally_unique_old": True,
                "globally_unique_new": True,
                "absolute_reading_order_shift": (
                    new_order - old_order
                    if old_order is not None and new_order is not None
                    else None
                ),
                "absolute_page_shift": (
                    new_page - old_page
                    if old_page is not None and new_page is not None
                    else None
                ),
                "absolute_shift_is_not_primary_movement_evidence": True,
                "context_is_authoritative_hierarchy": False,
            },
            "source_deleted_candidate_id": old_candidate["candidate_id"],
            "source_added_candidate_id": new_candidate["candidate_id"],
            "token_diff": None,
        }
        relocated.append(relocation)
        consumed_deleted_ids.add(old_candidate["candidate_id"])
        consumed_added_ids.add(new_candidate["candidate_id"])

    remaining_deleted = [
        candidate
        for candidate in comparison_candidates["deleted"]
        if candidate["candidate_id"] not in consumed_deleted_ids
    ]
    remaining_added = [
        candidate
        for candidate in comparison_candidates["added"]
        if candidate["candidate_id"] not in consumed_added_ids
    ]

    print("Relocated exact residuals:", len(relocated))
    print("Remaining deleted:", len(remaining_deleted))
    print("Remaining added:", len(remaining_added))

    # ---- cosmetic-noise suppression: resegmentation reconciliation ----
    # Pair leftover delete/add candidates that are near-twins (DI paragraph
    # splits, list-marker prefixes, hyphenation artifacts) so they surface as a
    # single RESEGMENTATION_REVIEW item instead of noisy ADD+DELETE pairs.
    #
    # Structural residuals are excluded from this generic 1:1 reconciler and
    # stay owned by the exact structural split/merge discovery below.  Without
    # that ownership split, a deleted structural element next to two added
    # structural fragments (e.g. "Alpha beta gamma" vs "Alpha beta" + "gamma")
    # is consumed here as one near-twin pair, hiding the exact 1:2 (or 2:1)
    # relationship; and a structural numeric edit (e.g. "Alpha beta 21" vs
    # "Alpha beta" + "99") is swallowed as an equivalence because the tokenizer
    # drops short numeric tokens.  Structural split/merge evidence is
    # review-only: it never consumes a candidate.
    paragraph_groups = detect_paragraph_resegmentation(
        remaining_deleted + remaining_added
        + comparison_candidates["modified"] + comparison_candidates["modified_review"],
        layout_ids,
        anchors,
    )

    protected_deleted_ids = {
        candidate["candidate_id"]
        for candidate in remaining_deleted
        if is_structural_candidate(candidate, "old")
    }
    protected_added_ids = {
        candidate["candidate_id"]
        for candidate in remaining_added
        if is_structural_candidate(candidate, "new")
    }

    (
        resegmentation_pairs,
        reconciled_leftover_deleted,
        reconciled_leftover_added,
    ) = reconcile_residuals(
        [
            candidate
            for candidate in remaining_deleted
            if candidate["candidate_id"] not in protected_deleted_ids
        ],
        [
            candidate
            for candidate in remaining_added
            if candidate["candidate_id"] not in protected_added_ids
        ],
    )

    # Recombine in the original input order.  Protected structural residuals are
    # never consumed; the reconciler only owns non-structural candidates, so
    # the two owners stay non-overlapping and exactly-once accounting holds.
    original_deleted_ids = protected_deleted_ids | {
        candidate["candidate_id"] for candidate in reconciled_leftover_deleted
    }
    original_added_ids = protected_added_ids | {
        candidate["candidate_id"] for candidate in reconciled_leftover_added
    }
    kept_deleted_ids = original_deleted_ids.copy()
    kept_added_ids = original_added_ids.copy()

    # Keep legacy candidate identities and slots for unaffected records. The
    # approximate reconciliation is provisional: exact paragraph groups can
    # release both its candidates or return an unconsumed partner to residuals.
    modified = comparison_candidates["modified"]
    modified_review = comparison_candidates["modified_review"]
    previous = {}
    if paragraph_groups:
        previous = {
            "modified": modified,
            "modified_review": modified_review,
            "deleted": [item.copy() for item in remaining_deleted
                        if item["candidate_id"] in original_deleted_ids],
            "added": [item.copy() for item in remaining_added
                      if item["candidate_id"] in original_added_ids],
            "resegmentation_review": resegmentation_pairs,
        }
        consumed = {cid for group in paragraph_groups for cid in group["source_candidate_ids"]}
        modified = [item for item in modified if item["candidate_id"] not in consumed]
        modified_review = [item for item in modified_review if item["candidate_id"] not in consumed]

        kept_pairs = []
        for pair in resegmentation_pairs:
            deleted_id = pair["source_deleted_candidate_id"]
            added_id = pair["source_added_candidate_id"]
            if consumed.isdisjoint((deleted_id, added_id)):
                kept_pairs.append(pair)
            else:
                kept_deleted_ids.add(deleted_id)
                kept_added_ids.add(added_id)
        resegmentation_pairs = kept_pairs
        kept_deleted_ids.difference_update(consumed)
        kept_added_ids.difference_update(consumed)

    remaining_deleted = [
        item for item in remaining_deleted if item["candidate_id"] in kept_deleted_ids
    ]
    remaining_added = [
        item for item in remaining_added if item["candidate_id"] in kept_added_ids
    ]
    print("Residual pairs reconciled as RESEGMENTATION_REVIEW:", len(resegmentation_pairs))

    # Issue counterpart proposals against the candidates that remain. When
    # replacing a group, the old bounded proposal view is also needed to keep
    # existing route and canonical IDs stable.
    prior_groups = []
    if previous:
        prior_groups = annotate_residuals(
            previous["deleted"], previous["added"],
            comparison_candidates["unchanged"] + previous["modified"]
            + previous["modified_review"] + relocated + previous["resegmentation_review"],
            [],
        )["groups"]
    proposal_result = annotate_residuals(
        remaining_deleted, remaining_added,
        comparison_candidates["unchanged"] + modified + modified_review
        + relocated + resegmentation_pairs,
        prior_groups,
    )

    print("Residual counterpart proposals:", len(proposal_result["proposals"]))
    print("Residual proposal groups:", len(proposal_result["groups"]))
    print("Residual proposal pair checks:", proposal_result["coverage"]["pair_checks"])

    moves = detect_moves(
        comparison_candidates["unchanged"] + modified + modified_review + relocated,
        modified_ids={item["candidate_id"] for item in modified + modified_review},
        layout_ids=layout_ids,
        anchors=anchors,
    )

    structural_pairs = paired_structural_candidates(comparison_candidates)
    structural_pairs.sort(key=lambda candidate: reading_order(candidate["old"]))

    # ------------------------------------------------------------------
    # Block movement logic
    # ------------------------------------------------------------------
    #
    # Goal:
    # detect contiguous blocks whose internal order is preserved but whose
    # position relative to surrounding structural elements changed.
    #
    # We deliberately avoid:
    # - single pair inversions,
    # - absolute page shift alone,
    # - authoritative hierarchy assumptions.
    #
    # This is evidence-only -> MOVEMENT_REVIEW.
    # ------------------------------------------------------------------

    records = []

    for old_rank, candidate in enumerate(structural_pairs):
        records.append({
            "candidate": candidate,
            "candidate_id": candidate["candidate_id"],
            "old_rank": old_rank,
            "old_order": reading_order(candidate["old"]),
            "new_order": reading_order(candidate["new"]),
        })

    # NEW ranks among the exact same paired structural records.
    records_by_new = sorted(records, key=lambda item: item["new_order"])
    new_rank_by_id = {
        item["candidate_id"]: new_rank
        for new_rank, item in enumerate(records_by_new)
    }

    for item in records:
        item["new_rank"] = new_rank_by_id[item["candidate_id"]]
        item["rank_shift"] = item["new_rank"] - item["old_rank"]

    raw_block_candidates = []

    for start in range(len(records)):
        for size in range(MIN_BLOCK_SIZE, MAX_BLOCK_SIZE + 1):
            end = start + size
            if end > len(records):
                break

            block = records[start:end]

            if not block_internal_order_preserved(block):
                continue

            if not block_is_contiguous_in_new(block):
                continue

            shifts = [item["rank_shift"] for item in block]

            # A moved block should shift in one coherent direction.
            if not (
                all(shift >= MIN_BLOCK_POSITION_SHIFT for shift in shifts)
                or all(shift <= -MIN_BLOCK_POSITION_SHIFT for shift in shifts)
            ):
                continue

            crossings = external_crossing_count(block, records)
            if crossings < MIN_EXTERNAL_CROSSINGS:
                continue

            raw_block_candidates.append({
                "start": start,
                "end": end,
                "size": size,
                "block": block,
                "crossings": crossings,
                "mean_rank_shift":
                    sum(shifts) / len(shifts),
                "min_rank_shift": min(shifts),
                "max_rank_shift": max(shifts),
            })

    # Deduplicate nested overlapping detections.
    #
    # Keep the strongest/largest block first.
    raw_block_candidates.sort(
        key=lambda item: (
            -item["size"],
            -item["crossings"],
            -abs(item["mean_rank_shift"]),
            item["start"],
        )
    )

    selected_blocks = []
    used_candidate_ids: set[str] = set()

    for block_info in raw_block_candidates:
        block_ids = {
            item["candidate_id"]
            for item in block_info["block"]
        }

        # Avoid flooding output with nested versions of the same moved block.
        if block_ids & used_candidate_ids:
            continue

        selected_blocks.append(block_info)
        used_candidate_ids.update(block_ids)

    movement_review: list[dict[str, Any]] = []

    for block_index, block_info in enumerate(selected_blocks, start=1):
        block = block_info["block"]

        movement_review.append({
            "review_id": f"MOVEMENT-BLOCK-REVIEW-{block_index:06d}",
            "movement_classification": "MOVEMENT_BLOCK_REVIEW",
            "needs_review": True,
            "review_reasons": [
                "contiguous_structural_block_relative_order_relocation"
            ],
            "candidate_ids": [
                item["candidate_id"]
                for item in block
            ],
            "old_element_ids": [
                item["candidate"]["old"]["element_id"]
                for item in block
            ],
            "new_element_ids": [
                item["candidate"]["new"]["element_id"]
                for item in block
            ],
            "old_text_preview": [
                element_text(item["candidate"]["old"])[:160]
                for item in block
            ],
            "new_text_preview": [
                element_text(item["candidate"]["new"])[:160]
                for item in block
            ],
            "movement_evidence": {
                "evidence_type": "contiguous_block_relative_order_relocation",
                "block_size": block_info["size"],
                "external_crossing_count": block_info["crossings"],
                "mean_structural_rank_shift":
                    round(block_info["mean_rank_shift"], 3),
                "min_structural_rank_shift":
                    block_info["min_rank_shift"],
                "max_structural_rank_shift":
                    block_info["max_rank_shift"],
                "internal_order_preserved": True,
                "contiguous_in_old": True,
                "contiguous_in_new": True,
                "absolute_shift_is_not_primary_movement_evidence": True,
                "context_is_authoritative_hierarchy": False,
            },
        })

    print("Paired structural elements checked:", len(structural_pairs))
    print("Raw block movement candidates:", len(raw_block_candidates))
    print("Selected movement block reviews:", len(movement_review))

    residual_old_structural = ordered_residual_structural(
        remaining_deleted, "old"
    )
    residual_new_structural = ordered_residual_structural(
        remaining_added, "new"
    )

    split_merge_review: list[dict[str, Any]] = []
    seen_evidence: set[tuple] = set()

    # OLD one element -> NEW two nearby elements.
    for old_candidate in residual_old_structural:
        old_text = element_text(old_candidate["old"])
        if not old_text:
            continue

        for index in range(len(residual_new_structural) - 1):
            first = residual_new_structural[index]
            second = residual_new_structural[index + 1]
            if not nearby_pair(first, second, "new"):
                continue

            if joined_text(first, second, "new") != old_text:
                continue

            evidence_key = (
                "one_old_to_two_new",
                old_candidate["candidate_id"],
                first["candidate_id"],
                second["candidate_id"],
            )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)

            split_merge_review.append({
                "review_id":
                    f"STRUCTURAL-SPLIT-MERGE-{len(split_merge_review) + 1:06d}",
                "evidence_type": "one_old_to_two_new_exact_concatenation",
                "classification": "STRUCTURAL_SPLIT_MERGE_REVIEW",
                "needs_review": True,
                "review_reasons": ["exact_structural_split_candidate"],
                "old_candidate_ids": [old_candidate["candidate_id"]],
                "new_candidate_ids": [
                    first["candidate_id"],
                    second["candidate_id"],
                ],
                "old_element_ids": [old_candidate["old"]["element_id"]],
                "new_element_ids": [
                    first["new"]["element_id"],
                    second["new"]["element_id"],
                ],
                "old_text": old_text,
                "new_text":
                    joined_text(first, second, "new"),
                "consumes_candidates": False,
            })

    # OLD two nearby elements -> NEW one element.
    for new_candidate in residual_new_structural:
        new_text = element_text(new_candidate["new"])
        if not new_text:
            continue

        for index in range(len(residual_old_structural) - 1):
            first = residual_old_structural[index]
            second = residual_old_structural[index + 1]
            if not nearby_pair(first, second, "old"):
                continue

            if joined_text(first, second, "old") != new_text:
                continue

            evidence_key = (
                "two_old_to_one_new",
                first["candidate_id"],
                second["candidate_id"],
                new_candidate["candidate_id"],
            )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)

            split_merge_review.append({
                "review_id":
                    f"STRUCTURAL-SPLIT-MERGE-{len(split_merge_review) + 1:06d}",
                "evidence_type": "two_old_to_one_new_exact_concatenation",
                "classification": "STRUCTURAL_SPLIT_MERGE_REVIEW",
                "needs_review": True,
                "review_reasons": ["exact_structural_merge_candidate"],
                "old_candidate_ids": [
                    first["candidate_id"],
                    second["candidate_id"],
                ],
                "new_candidate_ids": [new_candidate["candidate_id"]],
                "old_element_ids": [
                    first["old"]["element_id"],
                    second["old"]["element_id"],
                ],
                "new_element_ids": [new_candidate["new"]["element_id"]],
                "old_text":
                    joined_text(first, second, "old"),
                "new_text": new_text,
                "consumes_candidates": False,
            })

    print("Exact residual split/merge review candidates:", len(split_merge_review))

    structural_boundary_review: list[dict[str, Any]] = []

    for candidate in (
        comparison_candidates["modified"]
        + comparison_candidates["modified_review"]
    ):
        evidence = structural_boundary_evidence(candidate)
        if evidence is None:
            continue

        structural_boundary_review.append({
            "review_id":
                f"STRUCTURAL-BOUNDARY-{len(structural_boundary_review) + 1:06d}",
            "candidate_id": candidate["candidate_id"],
            "classification": "STRUCTURAL_BOUNDARY_REVIEW",
            "needs_review": True,
            "review_reasons": ["possible_structural_split_merge_boundary"],
            "old_element_id": candidate["old"]["element_id"],
            "new_element_id": candidate["new"]["element_id"],
            "old_text": element_text(candidate["old"]),
            "new_text": element_text(candidate["new"]),
            "evidence": evidence,
            "reclassifies_candidate": False,
        })

    print("Paired structural boundary review candidates:", len(structural_boundary_review))

    result = {
        "schema_version": "0.3.0",
        "comparison_stage": OUTPUT_STAGE,
        "source_files": {
            "comparison_candidates": COMPARISON_CANDIDATES_FILENAME,
            "comparison_candidates_schema_version":
                comparison_candidates.get("schema_version"),
        },
        "old_document": comparison_candidates.get("old_document"),
        "new_document": comparison_candidates.get("new_document"),
        "processing": {
            "version": STEP9_VERSION,
            "authoritative_hierarchy_used": False,
            "absolute_shift_used_as_primary_movement_evidence": False,
            "residual_exact_relocation_enabled": True,
            "resegmentation_reconciliation_enabled": True,
            "residual_relocation_requires_global_uniqueness": True,
            "relative_structural_order_inversion_review_enabled": False,
            "contiguous_block_movement_review_enabled": True,
            "movement_min_block_size": 3,
            "movement_max_block_size": 12,
            "movement_min_rank_shift": 8,
            "movement_min_external_crossings": 3,
            "exact_split_merge_review_enabled": True,
            "structural_boundary_review_enabled": True,
            "fuzzy_split_merge_enabled": False,
            "deterministic_relative_order_moves_enabled": True,
            "paragraph_resegmentation_enabled": True,
            "final_movement_classification_performed": True,
            "final_split_merge_resolution_performed": False,
            "residual_proposal_search_enabled": True,
            "residual_proposal_max_per_element": 5,
            "residual_proposal_max_pair_checks": 1000,
            "residual_proposal_consumes_candidates": False,
        },
        "statistics": {
            "input_unchanged": len(comparison_candidates["unchanged"]),
            "input_modified": len(comparison_candidates["modified"]),
            "input_modified_review": len(comparison_candidates["modified_review"]),
            "input_duplicate_exact_review":
                len(comparison_candidates["duplicate_exact_review"]),
            "input_deleted": len(comparison_candidates["deleted"]),
            "input_added": len(comparison_candidates["added"]),
            "relocated_unchanged": len(relocated),
            "resegmentation_review_pairs": len(resegmentation_pairs),
            "remaining_deleted_candidates": len(remaining_deleted),
            "remaining_added_candidates": len(remaining_added),
            "movement_block_review_candidates": len(movement_review),
            "structural_split_merge_review_candidates":
                len(split_merge_review),
            "structural_boundary_review_candidates":
                len(structural_boundary_review),
            "residual_counterpart_proposals":
                len(proposal_result["proposals"]),
            "residual_proposal_groups":
                len(proposal_result["groups"]),
            "residual_proposal_pair_checks":
                proposal_result["coverage"]["pair_checks"],
            "residual_proposal_budget_exhausted":
                proposal_result["coverage"]["budget_exhausted"],
        },
        "unchanged": copy.deepcopy(comparison_candidates["unchanged"]),
        "modified": copy.deepcopy(modified),
        "modified_review": copy.deepcopy(modified_review),
        "duplicate_exact_review":
            copy.deepcopy(comparison_candidates["duplicate_exact_review"]),
        # Exact residual relocation consumes only the two corresponding
        # provisional add/delete candidates.
        "relocated": relocated,
        "resegmentation_review": resegmentation_pairs,
        "paragraph_resegmentation": paragraph_groups,
        "moves": moves,
        "deleted": copy.deepcopy(remaining_deleted),
        "added": copy.deepcopy(remaining_added),
        # Evidence-only residual counterpart proposals and explicit coverage.
        "residual_proposals": proposal_result["proposals"],
        "residual_proposal_groups": proposal_result["groups"],
        "residual_proposal_coverage": proposal_result["coverage"],
        # Evidence-only review queues.
        "movement_review": movement_review,
        "structural_split_merge_review": split_merge_review,
        "structural_boundary_review": structural_boundary_review,
    }

    if previous:
        # Persist only the ordered IDs and changed inputs. These are historical
        # routing inputs, outside the current element conservation partition.
        history = {}
        for group, candidates in previous.items():
            if candidates == result[group]:
                continue
            current = {item["candidate_id"]: item for item in result[group]}
            history[group] = {
                "order": [item["candidate_id"] for item in candidates],
                "replaced": {
                    item["candidate_id"]: item for item in candidates
                    if item != current.get(item["candidate_id"])
                },
            }
        if history:
            result["candidate_identity_history"] = history

    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))

    paired_forward = (
        result["unchanged"]
        + result["modified"]
        + result["modified_review"]
        + result["duplicate_exact_review"]
        + result["relocated"]
        + result["resegmentation_review"]
    )

    old_ids = ids_from_records(paired_forward + result["deleted"], "old")
    new_ids = ids_from_records(paired_forward + result["added"], "new")

    for group in paragraph_groups:
        old_ids.extend(element["element_id"] for element in group["old_elements"])
        new_ids.extend(element["element_id"] for element in group["new_elements"])

    require(len(old_ids) == len(all_old), "s09: OLD conservation count failed")
    require(len(new_ids) == len(all_new), "s09: NEW conservation count failed")

    require(len(old_ids) == len(set(old_ids)), "s09: OLD element consumed more than once")
    require(len(new_ids) == len(set(new_ids)), "s09: NEW element consumed more than once")

    require(set(old_ids) == set(all_old), "s09: OLD conservation identity failed")
    require(set(new_ids) == set(all_new), "s09: NEW conservation identity failed")

    for item in result["relocated"]:
        require(element_key(item["old"]) == element_key(item["new"]), "s09: relocated key mismatch")
        require(
            item.get("type_changed") == type_change_fields(item["old"], item["new"]).get("type_changed"),
            "s09: relocation type-change evidence does not match source types",
        )
        key = element_key(item["old"])
        require(key is not None, "s09: relocated item has no element key")
        require(old_global_counts[key] == 1, "s09: relocated OLD key is not globally unique")
        require(new_global_counts[key] == 1, "s09: relocated NEW key is not globally unique")
        require(item["movement_classification"] == "RELOCATED_UNCHANGED", "s09: invalid relocation classification")

    # Review evidence must not itself consume or delete candidates.
    for item in result["structural_split_merge_review"]:
        require(item["consumes_candidates"] is False, "s09: split/merge review consumed candidates")

    for item in result["structural_boundary_review"]:
        require(item["reclassifies_candidate"] is False, "s09: boundary review reclassified candidate")

    require(result["processing"]["authoritative_hierarchy_used"] is False, "s09: authoritative hierarchy used")
    require(
        result["processing"]["absolute_shift_used_as_primary_movement_evidence"]
        is False,
        "s09: absolute shift used as primary movement evidence",
    )

    print(f"OLD conservation: {len(old_ids):,}/{len(all_old):,}")
    print(f"NEW conservation: {len(new_ids):,}/{len(all_new):,}")
    print("Step-9 movement/structural safety invariants passed.")

    print("\n=== RELOCATED UNCHANGED ===")
    for item in result["relocated"][:15]:
        print(
            item["candidate_id"],
            "|",
            repr(element_text(item["old"])[:120]),
            "| old/new order:",
            reading_order(item["old"]),
            "->",
            reading_order(item["new"]),
        )

    print("\n=== MOVEMENT BLOCK REVIEW ===")
    for item in result["movement_review"][:15]:
        evidence = item["movement_evidence"]
        print(
            item["review_id"],
            "| block_size=",
            evidence["block_size"],
            "| crossings=",
            evidence["external_crossing_count"],
            "| mean_shift=",
            evidence["mean_structural_rank_shift"],
        )
        for preview in item["old_text_preview"][:5]:
            print("   -", repr(preview))

    print("\n=== STRUCTURAL SPLIT/MERGE REVIEW ===")
    for item in result["structural_split_merge_review"][:15]:
        print(
            item["review_id"],
            "|",
            item["evidence_type"],
            "|",
            repr(item["old_text"][:100]),
            "->",
            repr(item["new_text"][:100]),
        )

    print("\n=== STRUCTURAL BOUNDARY REVIEW ===")
    for item in result["structural_boundary_review"][:15]:
        print(
            item["review_id"],
            "| fragment=",
            repr(item["evidence"]["boundary_fragment"]),
            "|",
            repr(item["old_text"][:100]),
            "->",
            repr(item["new_text"][:100]),
        )

    save_json(result, output_path)
    require(output_path.exists(), "s09: movement output was not saved")
    print("Saved:", output_path)


if __name__ == "__main__":
    main()