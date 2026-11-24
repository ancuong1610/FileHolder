"""Deterministic relative-order changes for established element pairs."""

from bisect import bisect_left
from typing import Any

from document_compare.phase1.matching.impact import IMPACT_VERSION, classify_impact
from document_compare.phase1.structure.reconstruction import is_layout

HEADING_TYPES = {"heading", "title"}


def _increasing_subsequence(orders: list[int]) -> set[int]:
    """Return indices in an LIS, preferring the smallest tail at each length."""
    tails: list[int] = []
    tail_indices: list[int] = []
    predecessors: list[int] = []
    for index, order in enumerate(orders):
        length = bisect_left(tails, order)
        predecessors.append(tail_indices[length - 1] if length else -1)
        if length == len(tails):
            tails.append(order)
            tail_indices.append(index)
        elif order < tails[length]:
            tails[length] = order
            tail_indices[length] = index

    kept = set()
    index = tail_indices[-1] if tail_indices else -1
    while index != -1:
        kept.add(index)
        index = predecessors[index]
    return kept


def _compact(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": element.get("text"),
        "page": element.get("primary_page") or element.get("page_number") or element.get("page"),
        "reading_order": element["reading_order"],
        "element_type": element.get("element_type"),
    }


def preceding_heading_order(orders: list[int], order: int) -> int | None:
    index = bisect_left(orders, order)
    return orders[index - 1] if index else None


def detect_moves(
    pairs: list[dict[str, Any]],
    *,
    modified_ids: set[str],
    layout_ids: dict[str, set[str]],
    anchors: dict[str, dict[int, str]],
) -> list[dict[str, Any]]:
    """Find moved pairs and group adjacent runs in both source orders."""
    def ordered_source(pair: dict[str, Any], side: str) -> bool:
        element = pair.get(side) or {}
        return (
            isinstance(element.get("reading_order"), int)
            and element["element_id"] not in layout_ids[side]
            and not is_layout(element)
        )

    ordered = sorted(
        (pair for pair in pairs if all(ordered_source(pair, side) for side in ("old", "new"))),
        key=lambda pair: (pair["old"]["reading_order"], pair["old"]["element_id"]),
    )
    sections = {side: dict(anchors[side]) for side in ("old", "new")}
    for pair in ordered:
        for side in sections:
            element = pair[side]
            if element.get("element_type") in HEADING_TYPES:
                sections[side].setdefault(element["reading_order"], element.get("text"))
    section_orders = {side: sorted(orders) for side, orders in sections.items()}

    def is_heading(pair: dict[str, Any]) -> bool:
        return any(pair[side]["reading_order"] in sections[side] for side in sections)

    matched_sections = {(None, None)}
    matched_heading_orders: dict[str, set[int]] = {"old": set(), "new": set()}
    for pair in ordered:
        old_order, new_order = pair["old"]["reading_order"], pair["new"]["reading_order"]
        if old_order in sections["old"] and new_order in sections["new"]:
            matched_sections.add((old_order, new_order))
            matched_heading_orders["old"].add(old_order)
            matched_heading_orders["new"].add(new_order)

    def crosses_matched_sections(pair: dict[str, Any]) -> bool:
        # A body pair changed section only when it left one surviving heading
        # for another surviving heading. If its old heading was deleted or
        # renamed (unmatched), or its new heading is new, the section itself
        # changed, not the paragraph's position; the heading change is already
        # reported on its own, so do not report every paragraph under it.
        old_section, new_section = preceding_sections(pair)
        return (
            (old_section, new_section) not in matched_sections
            and old_section in matched_heading_orders["old"]
            and new_section in matched_heading_orders["new"]
        )

    def preceding_sections(pair: dict[str, Any]) -> tuple[int | None, int | None]:
        return (
            preceding_heading_order(section_orders["old"], pair["old"]["reading_order"]),
            preceding_heading_order(section_orders["new"], pair["new"]["reading_order"]),
        )

    stable = _increasing_subsequence([pair["new"]["reading_order"] for pair in ordered])
    blocks: list[list[dict[str, Any]]] = []
    for index, pair in enumerate(ordered):
        crosses_section = not is_heading(pair) and crosses_matched_sections(pair)
        if index in stable and not crosses_section:
            continue
        if blocks and all(
            pair[side]["reading_order"] == blocks[-1][-1][side]["reading_order"] + 1
            for side in sections
        ):
            blocks[-1].append(pair)
        else:
            blocks.append([pair])

    moves = []
    for index, block in enumerate(blocks, 1):
        modified = [pair for pair in block if pair["candidate_id"] in modified_ids]
        high_impact = any(
            classify_impact(
                pair["old"].get("normalized_text") or pair["old"].get("text"),
                pair["new"].get("normalized_text") or pair["new"].get("text"),
            )["priority"] == "high"
            for pair in modified
        )
        old_section, new_section = preceding_sections(block[0])
        from_anchor = sections["old"].get(old_section)
        to_anchor = sections["new"].get(new_section)
        heading_block = is_heading(block[0])
        # A single body element that stays under the same heading is most often
        # reading-order jitter from extraction (columns, boxes); keep it visible
        # but separate it from real moves so reviewers can filter it.
        local = (
            len(block) == 1 and not heading_block and not modified
            and (old_section, new_section) in matched_sections
        )
        if local:
            kinds, priority = ["local_reorder"], "low"
        else:
            kinds = ["section_move" if heading_block else "paragraph_move"]
            priority = "high" if high_impact or any(map(is_heading, block)) else "normal"
        moves.append({
            "move_id": f"MOVE-{index:06d}",
            "old_element_ids": [pair["old"]["element_id"] for pair in block],
            "new_element_ids": [pair["new"]["element_id"] for pair in block],
            "old_elements": [_compact(pair["old"]) for pair in block],
            "new_elements": [_compact(pair["new"]) for pair in block],
            "from_anchor": from_anchor,
            "to_anchor": to_anchor,
            "element_count": len(block),
            "contains_modified": bool(modified),
            "impact": {
                "version": IMPACT_VERSION,
                "level": "moved",
                "priority": priority,
                "kinds": kinds,
                "signals": [],
                "basis": "deterministic_rule",
                "language_profile": "de-en",
            },
        })
    return moves
