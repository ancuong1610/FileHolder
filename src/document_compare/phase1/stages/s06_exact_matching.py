"""Stage 06: exact-match Step-5 elements with provisional context.

Input  : ``<root>/output/old_document_structured.json`` and
         ``new_document_structured.json`` (Step-5 structured models).

Work   : Extracts comparable elements, filters repeated left-margin markers,
         and pairs elements whose (element type, comparison text) are
         identical when the pairing is globally unique or when equal
         nonempty provisional anchor context resolves duplicates by
         occurrence order.

Output : ``<root>/output/exact_element_matches.json`` plus console
         statistics and previews.

Review : Duplicate groups without resolvable context are left unmatched and
         reported under ``ambiguous_duplicate_groups`` with explicit element
         IDs, never silently paired. Provisional context is support-only and
         never treated as authoritative hierarchy.

The module is import-safe: importing it only defines the pure matching
helpers and never resolves a workspace, reads files, prints, or mutates the
environment. File orchestration lives in :func:`main`, which the
``__name__ == "__main__"`` guard runs for the CLI stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.contracts.validation import require

# ---- imports and configuration constants ----

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

STEP6_VERSION = "3.4.0"
INPUT_SCHEMA_VERSION = "0.3.0"
RESULT_SCHEMA_VERSION = "0.2.0"
TABLE_ELEMENT_TYPE = "table"
MARKER_PATTERN = re.compile(r"^(?=.{1,12}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+$")
MARKER_MARGIN_RATIO = 0.16
MIN_MARKER_CANDIDATES = 5
MIN_MARKER_PAGES = 3


# ---- workspace configuration ----
def result_paths(project_root: Path) -> dict[str, Path]:
    """Input and output paths for one pipeline workspace root."""
    output_dir = project_root / "output"
    return {
        "old": output_dir / "old_document_structured.json",
        "new": output_dir / "new_document_structured.json",
        "result": output_dir / "exact_element_matches.json",
    }


# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a JSON object.")
    return value


def save_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def document_from(model: dict[str, Any]) -> dict[str, Any]:
    document = model.get("document")
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise ValueError("Expected document.elements from Step 5.")
    return document


def validate_step5_model(model: dict[str, Any], path: Path) -> None:
    if model.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueError(
            f"Expected Step-5 schema {INPUT_SCHEMA_VERSION!r}; "
            f"got {model.get('schema_version')!r} in {path}."
        )

    processing = model.get("processing") or {}
    internal = processing.get("internal_schema") or {}
    reconstruction = processing.get("section_reconstruction") or {}

    if internal.get("version") != "3.0.0":
        raise ValueError(
            "Step 6 requires the neutral Step-3 v3 evidence model."
        )

    if reconstruction.get("strategy") != "provisional_anchor_evidence":
        raise ValueError(
            "Step 6 v3.4 requires Step 5 provisional_anchor_evidence output."
        )

    if reconstruction.get("authoritative_hierarchy_created") is not False:
        raise ValueError(
            "This pipeline must not use inferred hierarchy as an exact-match boundary."
        )

    if reconstruction.get("comparison_text_created") is not True:
        raise ValueError(
            "Step 6 v3.4 requires Step 5 comparison_text output."
        )

    document = document_from(model)

    anchors = document.get("provisional_anchors")
    if not isinstance(anchors, list):
        raise ValueError(
            "Step 6 v3.4 requires document.provisional_anchors."
        )

    for element in document["elements"]:
        if not isinstance(element.get("comparison_text"), str):
            raise ValueError(
                f"Missing comparison_text for element "
                f"{element.get('element_id')}."
            )

        context = element.get("provisional_anchor_context")
        if not isinstance(context, dict):
            raise ValueError(
                f"Missing provisional_anchor_context for element "
                f"{element.get('element_id')}."
            )

        if not isinstance(context.get("recent_anchor_ids"), list):
            raise ValueError(
                f"Missing recent_anchor_ids for element "
                f"{element.get('element_id')}."
            )


# ---- candidate extraction ----
def detect_repeated_margin_markers(document: dict[str, Any]) -> tuple[set[str], dict[str, Any]]:
    page_widths = {page.get("page_number"): page.get("width") for page in document.get("pages") or [] if isinstance(page, dict)}
    candidates = []
    for element in document["elements"]:
        if not element.get("comparison_eligible") or element.get("element_type") == TABLE_ELEMENT_TYPE:
            continue
        text = str(element.get("normalized_text") or "").strip()
        polygon, width = element.get("primary_polygon"), page_widths.get(element.get("primary_page"))
        if not MARKER_PATTERN.fullmatch(text) or not isinstance(polygon, list) or not isinstance(width, (int, float)) or width <= 0:
            continue
        xs = polygon[0::2]
        if xs and all(isinstance(x, (int, float)) for x in xs) and min(xs) / width <= MARKER_MARGIN_RATIO:
            candidates.append(element)
    pages = {item.get("primary_page") for item in candidates if item.get("primary_page") is not None}
    enabled = len(candidates) >= MIN_MARKER_CANDIDATES and len(pages) >= MIN_MARKER_PAGES
    return (
        {item["element_id"] for item in candidates} if enabled else set(),
        {
            "enabled": enabled,
            "candidate_count": len(candidates),
            "distinct_pages": len(pages),
            "pattern": MARKER_PATTERN.pattern,
            "margin_ratio_max": MARKER_MARGIN_RATIO,
        },
    )


def anchor_key_map(document: dict[str, Any]) -> dict[str, str]:
    return {str(anchor["anchor_id"]): str(anchor["anchor_key"])
            for anchor in document.get("provisional_anchors") or []
            if isinstance(anchor, dict) and anchor.get("anchor_id") and anchor.get("anchor_key")}


def compact(element: dict[str, Any], keys_by_id: dict[str, str]) -> dict[str, Any]:
    context = element.get("provisional_anchor_context") or {}
    recent_ids = context.get("recent_anchor_ids") or []
    recent_keys = [keys_by_id[anchor_id] for anchor_id in recent_ids if anchor_id in keys_by_id]
    return {
        "element_id": element["element_id"],
        "element_type": element.get("element_type"),
        "text": element.get("text"),
        "normalized_text": str(element.get("normalized_text") or "").strip(),
        "comparison_text": str(
            element.get("comparison_text")
            or element.get("normalized_text")
            or ""
        ).strip(),
        "original_role": element.get("original_role"),
        "primary_page": element.get("primary_page"),
        "reading_order": element.get("reading_order"),
        "source_reference": element.get("source_reference"),
        "primary_polygon": element.get("primary_polygon"),
        "confidence": element.get("confidence"),
        "extraction_validation": element.get("extraction_validation"),
        "provisional_anchor_context": {
            "nearest_anchor_key": keys_by_id.get(context.get("nearest_anchor_id")),
            "nearest_anchor_confidence": context.get("nearest_anchor_confidence"),
            "recent_anchor_keys": recent_keys,
            "context_is_authoritative_hierarchy": False,
        },
    }


def extract_candidates(model: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    document = document_from(model)
    ignored_ids, marker_profile = detect_repeated_margin_markers(document)
    keys_by_id = anchor_key_map(document)
    candidates, ignored = [], []
    for element in document["elements"]:
        if not element.get("comparison_eligible") or element.get("element_type") == TABLE_ELEMENT_TYPE:
            continue
        text = str(element.get("normalized_text") or "").strip()
        if not text:
            continue
        if element.get("element_id") in ignored_ids:
            ignored.append({
                "element_id": element["element_id"],
                "normalized_text": text,
                "primary_page": element.get("primary_page"),
                "reason": "repeated_left_margin_marker",
            })
        else:
            candidates.append(compact(element, keys_by_id))
    candidates.sort(key=lambda item: int(item.get("reading_order") or 0))
    metadata = {
        "document_id": document.get("document_id"),
        "page_count": document.get("page_count"),
        "source_element_count": len(document["elements"]),
        "provisional_anchor_count": len(document.get("provisional_anchors") or []),
    }
    return metadata, candidates, ignored, marker_profile


# ---- exact matching ----
def matching_text(item: dict[str, Any]) -> str:
    value = item.get("comparison_text")
    if isinstance(value, str):
        return value
    return str(item.get("normalized_text") or item.get("text") or "")


def content_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item["element_type"]), matching_text(item)


def context_keys(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item.get("provisional_anchor_context", {}).get("recent_anchor_keys") or [])


def context_relation(old: dict[str, Any], new: dict[str, Any]) -> str:
    old_keys, new_keys = set(context_keys(old)), set(context_keys(new))
    if old_keys and new_keys and old_keys.intersection(new_keys):
        return "shared_provisional_anchor_evidence"
    if old_keys or new_keys:
        return "different_or_one_sided_provisional_context"
    return "no_provisional_anchor_evidence"


def make_match(old: dict[str, Any], new: dict[str, Any], method: str, *, duplicate_size: int) -> dict[str, Any]:
    return {
        "match_id": None,
        "match_type": "exact",
        "match_method": method,
        "confidence": 1.0 if duplicate_size == 1 else 0.95,
        "exact_content_match": True,
        "duplicate_group_size": duplicate_size,
        "source_assignment_ambiguous": duplicate_size > 1,
        "context_relation": context_relation(old, new),
        "context_is_authoritative_hierarchy": False,
        "page_changed": old.get("primary_page") != new.get("primary_page"),
        "absolute_order_index_changed": old.get("reading_order") != new.get("reading_order"),
        "old": old,
        "new": new,
    }


def group(items: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        result[content_key(item)].append(item)
    for values in result.values():
        values.sort(key=lambda item: int(item.get("reading_order") or 0))
    return dict(result)


def build_result(old_meta: dict[str, Any], new_meta: dict[str, Any], old_items: list[dict[str, Any]], new_items: list[dict[str, Any]], old_ignored: list[dict[str, Any]], new_ignored: list[dict[str, Any]], old_profile: dict[str, Any], new_profile: dict[str, Any]) -> dict[str, Any]:
    old_groups, new_groups = group(old_items), group(new_items)
    matched_old, matched_new, matches, ambiguous = set(), set(), [], []
    for key in sorted(set(old_groups).intersection(new_groups), key=repr):
        olds, news = old_groups[key], new_groups[key]
        if len(olds) == len(news) == 1:
            matches.append(make_match(olds[0], news[0], "globally_unique_type_text", duplicate_size=1))
            matched_old.add(olds[0]["element_id"])
            matched_new.add(news[0]["element_id"])
            continue
        old_by_context, new_by_context = defaultdict(list), defaultdict(list)
        for item in olds:
            old_by_context[context_keys(item)].append(item)
        for item in news:
            new_by_context[context_keys(item)].append(item)
        unresolved_old, unresolved_new = [], []
        for context in set(old_by_context).union(new_by_context):
            left, right = old_by_context.get(context, []), new_by_context.get(context, [])
            if context and len(left) == len(right) and left:
                for old, new in zip(left, right):
                    matches.append(make_match(old, new, "duplicate_shared_provisional_context_occurrence_order", duplicate_size=len(olds)))
                    matched_old.add(old["element_id"])
                    matched_new.add(new["element_id"])
            else:
                unresolved_old.extend(left)
                unresolved_new.extend(right)
        if unresolved_old or unresolved_new:
            ambiguous.append({
                "group_id": None,
                "reason": "duplicate_requires_non_authoritative_context_or_review",
                "element_type": key[0],
                "normalized_text": key[1],
                "old_element_ids": [item["element_id"] for item in unresolved_old],
                "new_element_ids": [item["element_id"] for item in unresolved_new],
                "old_context_keys": [list(context_keys(item)) for item in unresolved_old],
                "new_context_keys": [list(context_keys(item)) for item in unresolved_new],
            })
    matches.sort(key=lambda item: (int(item["old"].get("reading_order") or 0), int(item["new"].get("reading_order") or 0)))
    for index, item in enumerate(matches, 1):
        item["match_id"] = f"EXACT-{index:06d}"
    for index, item in enumerate(ambiguous, 1):
        item["group_id"] = f"EXACT-AMBIGUOUS-{index:05d}"
    unmatched_old, unmatched_new = [], []
    ambiguous_old = {x for group_item in ambiguous for x in group_item["old_element_ids"]}
    ambiguous_new = {x for group_item in ambiguous for x in group_item["new_element_ids"]}
    for item in old_items:
        if item["element_id"] not in matched_old:
            item = dict(item)
            item["exact_unmatched_reason"] = "duplicate_context_ambiguous" if item["element_id"] in ambiguous_old else "no_exact_counterpart"
            unmatched_old.append(item)
    for item in new_items:
        if item["element_id"] not in matched_new:
            item = dict(item)
            item["exact_unmatched_reason"] = "duplicate_context_ambiguous" if item["element_id"] in ambiguous_new else "no_exact_counterpart"
            unmatched_new.append(item)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "comparison_stage": "exact_element_matching_with_provisional_context",
        "processing": {
            "version": STEP6_VERSION,
            "fuzzy_matching_performed": False,
            "tables_included": False,
            "authoritative_hierarchy_used": False,
            "provisional_context_used_as_support_only": True,
            "repeated_margin_marker_filter": {"old": old_profile, "new": new_profile},
        },
        "old_document": old_meta,
        "new_document": new_meta,
        "matching_rules": {
            "exact_content_key": ["element_type", "comparison_text"],
            "unique_exact_matches_are_global": True,
            "duplicate_matches_require_equal_nonempty_provisional_context": True,
            "duplicate_context_conflicts_left_unmatched": True,
        },
        "statistics": {
            "old_comparable_elements": len(old_items),
            "new_comparable_elements": len(new_items),
            "exact_matches": len(matches),
            "ambiguous_duplicate_groups": len(ambiguous),
            "unmatched_old_elements": len(unmatched_old),
            "unmatched_new_elements": len(unmatched_new),
            "ignored_old_nonsemantic_margin_markers": len(old_ignored),
            "ignored_new_nonsemantic_margin_markers": len(new_ignored),
            "match_methods": dict(sorted(Counter(x["match_method"] for x in matches).items())),
            "context_relations": dict(sorted(Counter(x["context_relation"] for x in matches).items())),
        },
        "matches": matches,
        "ambiguous_duplicate_groups": ambiguous,
        "ignored_old_nonsemantic_margin_markers": old_ignored,
        "ignored_new_nonsemantic_margin_markers": new_ignored,
        "unmatched_old": unmatched_old,
        "unmatched_new": unmatched_new,
    }


# ---- result validation ----
def validate_result(result: dict[str, Any], old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> None:
    matches = result["matches"]
    old_ids = [item["old"]["element_id"] for item in matches]
    new_ids = [item["new"]["element_id"] for item in matches]
    require(
        len(old_ids) == len(set(old_ids)),
        "s06: an OLD element was matched more than once",
    )
    require(
        len(new_ids) == len(set(new_ids)),
        "s06: a NEW element was matched more than once",
    )
    for item in matches:
        require(
            item["old"]["element_type"] == item["new"]["element_type"],
            "s06: exact match crosses element types",
        )
        require(
            matching_text(item["old"]) == matching_text(item["new"]),
            "s06: exact match text does not match",
        )
        require(
            item["context_is_authoritative_hierarchy"] is False,
            "s06: provisional context treated as authoritative",
        )
    all_old = {item["element_id"] for item in old_items}
    all_new = {item["element_id"] for item in new_items}
    unmatched_old = {item["element_id"] for item in result["unmatched_old"]}
    unmatched_new = {item["element_id"] for item in result["unmatched_new"]}
    require(
        set(old_ids).isdisjoint(unmatched_old),
        "s06: a matched OLD element is also reported unmatched",
    )
    require(
        set(new_ids).isdisjoint(unmatched_new),
        "s06: a matched NEW element is also reported unmatched",
    )
    require(
        set(old_ids).union(unmatched_old) == all_old,
        "s06: OLD exact-match conservation failed",
    )
    require(
        set(new_ids).union(unmatched_new) == all_new,
        "s06: NEW exact-match conservation failed",
    )


# ---- stage entry point ----
def main() -> None:
    """Run Stage 06 against the workspace and save the exact-match result."""
    project_root = find_project_root()
    paths = result_paths(project_root)
    old_path, new_path, result_path = paths["old"], paths["new"], paths["result"]

    print("Step 6 version:", STEP6_VERSION)
    print("Input:", old_path, new_path, sep="\n")

    old_model, new_model = load_json(old_path), load_json(new_path)
    validate_step5_model(old_model, old_path)
    validate_step5_model(new_model, new_path)

    started = time.perf_counter()

    old_meta, old_items, old_ignored, old_profile = extract_candidates(old_model)
    new_meta, new_items, new_ignored, new_profile = extract_candidates(new_model)
    result = build_result(old_meta, new_meta, old_items, new_items, old_ignored, new_ignored, old_profile, new_profile)
    validate_result(result, old_items, new_items)

    result["processing"]["elapsed_seconds"] = round(time.perf_counter() - started, 3)

    save_json(result, result_path)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", result_path)

    print("\nFirst 15 exact matches:")
    for match in result["matches"][:15]:
        print(match["match_id"], "|", match["match_method"], "|", match["context_relation"], "|", repr(match["old"]["normalized_text"][:120]))

    print("\nFirst 15 ambiguous duplicate groups:")
    for group_item in result["ambiguous_duplicate_groups"][:15]:
        print(group_item["group_id"], "|", group_item["reason"], "|", repr(group_item["normalized_text"][:120]))


if __name__ == "__main__":
    main()