"""Stage 10: logical table comparison with provisional context.

Input  : ``<root>/output/old_document_structured.json`` and
         ``<root>/output/new_document_structured.json`` (Step 3/5 neutral
         structured documents, schema 0.3.0) and
         ``<root>/output/comparison_candidates_with_movement.json`` (Step 9
         movement/structural reconciliation evidence).

Work   : Groups physical table elements into logical tables across
         adjacent-page continuations (same columns, repeated header, with
         optional shared provisional-context support), matches logical
         tables exactly then by reciprocal similarity with provisional
         context support, aligns rows, compares cells with conservative
         layout normalization (aggressive artifact normalization is
         review-only), and assembles a complete table-comparison result
         with UNCHANGED / LAYOUT_EQUIVALENT / EXTRACTION_ARTIFACT_REVIEW /
         STRUCTURE_CHANGED_REVIEW / MODIFIED classifications, unresolved
         same-context residual review groups, and deleted/added table
         candidates with full physical/logical conservation checks.

Output : ``<root>/output/table_comparison_result.json`` plus console
         summaries.

Review : Aggressive artifact normalization never produces automatic
         equivalence; provisional-context differences are never movement
         proof. Unresolved same-context residual groups, structure
         changes, artifact-equivalent cells and near-threshold matches are
         routed to human review.

The module is import-safe: importing it only defines the pure table
helpers and never resolves a workspace, reads or writes pipeline files,
prints, or mutates the environment. File orchestration lives in
:func:`main`, which the ``__name__ == "__main__"`` guard runs for the CLI
stage runner.

Auto-converted from 10_compare_tables_v2_current_contract.ipynb (code
cells only).
"""
from crr_compare.common import find_project_root, get_page_number, set_page_number
from crr_compare.validation import require

# ---- imports and configuration constants ----

import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

STEP10_VERSION = "2.0.0-current-provisional-context"
EXPECTED_STEP9_STAGE = "movement_and_structural_reconciliation_evidence"


class ContractError(ValueError):
    """Malformed table input that violates the Stage-10 input contract.

    Raised instead of silently repairing an ambiguous table. Current cases:
    two cells sharing one ``(row_index, column_index)`` coordinate, two cells
    sharing one ``cell_id``, and any cell-accounting mismatch where a cell
    would otherwise be dropped from the matched/deleted/added comparison.
    The message carries stage, table, row, column and cell identifiers so the
    failure can be located in the source extraction artifacts.
    """


# ---- cell 4 ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
    return value


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(path)



# ---- cell 6 ----
LINE_WRAP_HYPHEN_PATTERN = re.compile(r"(?<=\w)-\s+(?=\w)", re.UNICODE)
WORD_HYPHEN_PATTERN = re.compile(r"(?<=\w)-(?=\w)", re.UNICODE)
DIGIT_RANGE_SPACE_PATTERN = re.compile(r"(?<=\d)\s*-\s*(?=\d)", re.UNICODE)
PAREN_SPACE_BEFORE_PATTERN = re.compile(r"\s+(?=[(\[])", re.UNICODE)
PAREN_SPACE_INSIDE_PATTERN = re.compile(r"(?<=[(\[])\s+|\s+(?=[)\]])", re.UNICODE)
SELECTION_MARKER_PATTERN = re.compile(r":(?:un)?selected:", re.IGNORECASE)
SELECTION_STATE_PATTERN = re.compile(r":(unselected|selected):", re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r"\s+", re.UNICODE)
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def source_text(value: str | None) -> str:
    return WHITESPACE_PATTERN.sub(" ", value or "").strip()


def remove_aggressive_artifacts(value: str | None) -> str:
    text = SELECTION_MARKER_PATTERN.sub("", value or "")
    # A single regex substitution does not fully repair chained wraps such as
    # ``Be- ta-Fak- tor`` because each removal creates a new adjacent match.
    while True:
        repaired = LINE_WRAP_HYPHEN_PATTERN.sub("", text)
        if repaired == text:
            return repaired
        text = repaired


def layout_text(value: str | None) -> str:
    """Conservative normalization used for automatic table decisions."""
    text = value or ""
    text = DIGIT_RANGE_SPACE_PATTERN.sub("-", text)
    text = PAREN_SPACE_BEFORE_PATTERN.sub("", text)
    text = PAREN_SPACE_INSIDE_PATTERN.sub("", text)
    return WHITESPACE_PATTERN.sub(" ", text).strip().casefold()


def artifact_tolerant_text(value: str | None) -> str:
    """Aggressive comparison used only to route uncertain cases to review."""
    text = remove_aggressive_artifacts(value)
    text = WORD_HYPHEN_PATTERN.sub("", text)
    text = DIGIT_RANGE_SPACE_PATTERN.sub("-", text)
    text = PAREN_SPACE_BEFORE_PATTERN.sub("", text)
    text = PAREN_SPACE_INSIDE_PATTERN.sub("", text)
    text = text.replace("¡", "i")
    return WHITESPACE_PATTERN.sub(" ", text).strip().casefold()


def selection_states(value: str | None) -> tuple[str, ...]:
    return tuple(match.casefold() for match in SELECTION_STATE_PATTERN.findall(value or ""))


def alphanumeric_skeleton(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())



def compact_identifier_text(value: str | None) -> str | None:
    text = layout_text(value)
    if len(text) > 40 or not text:
        return None
    compact = WHITESPACE_PATTERN.sub("", text)
    return compact if re.fullmatch(r"[\w&]+", compact, re.UNICODE) else None


def anchor_key_map(document: dict[str, Any]) -> dict[str, str]:
    result = {}
    for anchor in document.get("provisional_anchors") or []:
        anchor_id = anchor.get("anchor_id")
        anchor_key = anchor.get("anchor_key")
        if isinstance(anchor_id, str) and isinstance(anchor_key, str):
            result[anchor_id] = anchor_key
    return result



def same_document_context_support(
    previous: dict[str, Any],
    current: dict[str, Any],
    key_map: dict[str, str],
) -> dict[str, Any]:
    previous_keys = provisional_context_keys(previous, key_map)
    current_keys = provisional_context_keys(current, key_map)
    shared = tuple(key for key in previous_keys if key in set(current_keys))

    return {
        "previous_context_keys": previous_keys,
        "current_context_keys": current_keys,
        "shared_context_keys": shared,
        "has_shared_context": bool(shared),
        "context_available": bool(previous_keys and current_keys),
        "context_is_authoritative_hierarchy": False,
    }


def table_cells(table_element: dict[str, Any]) -> list[dict[str, Any]]:
    return list((table_element.get("table") or {}).get("cells") or [])


def cell_text(cell: dict[str, Any]) -> str:
    return cell.get("normalized_text") or cell.get("text") or ""


def header_row_indexes(table_element: dict[str, Any]) -> set[int]:
    return {
        int(cell["row_index"])
        for cell in table_cells(table_element)
        if cell.get("kind") == "columnHeader" and cell.get("row_index") is not None
    }


def header_signature(table_element: dict[str, Any]) -> tuple[str, ...]:
    headers = [
        (
            int(cell.get("row_index", 0)),
            int(cell.get("column_index", 0)),
            int(cell.get("row_span") or 1),
            int(cell.get("column_span") or 1),
            layout_text(cell_text(cell)),
        )
        for cell in table_cells(table_element)
        if cell.get("kind") == "columnHeader"
    ]
    headers.sort()
    return tuple(f"{row}:{column}:{row_span}:{column_span}:{text}" for row, column, row_span, column_span, text in headers)


def header_similarity(old_header: tuple[str, ...], new_header: tuple[str, ...]) -> float:
    if not old_header or not new_header:
        return 0.0
    return SequenceMatcher(None, old_header, new_header, autojunk=False).ratio()


def validate_fragment_cell_contract(
    side: str,
    logical_index: int,
    fragment: dict[str, Any],
) -> None:
    """Reject malformed physical-table cells before any row/column pairing.

    The Stage-10 contract requires every cell of one *physical* table
    fragment to occupy a unique ``(row_index, column_index)`` coordinate and,
    when a ``cell_id`` is present, to carry a unique ``cell_id``. Merged cells
    are expressed through valid ``row_span``/``column_span`` values and still
    occupy exactly one coordinate, so they never collide here. Repeated-page
    continuation fragments are *separate* physical tables and are validated
    independently, so a repeated continuation header never looks like a
    duplicate coordinate.
    """
    element_id = fragment.get("element_id")
    logical_table_id = f"{side.upper()}-LT{logical_index:04}"
    coordinates: dict[tuple[int, int], dict[str, Any]] = {}
    cell_ids: dict[str, dict[str, Any]] = {}

    for cell in table_cells(fragment):
        row_index = int(cell.get("row_index", 0))
        column_index = int(cell.get("column_index", 0))
        coordinate = (row_index, column_index)

        previous = coordinates.get(coordinate)
        if previous is not None:
            raise ContractError(
                "s10: duplicate cell coordinate is not a valid table pairing "
                f"(side={side}, logical_table={logical_table_id}, "
                f"source_table={element_id!r}, row={row_index}, "
                f"column={column_index}): cells "
                f"{previous.get('cell_id')!r} and {cell.get('cell_id')!r} "
                "share one coordinate; refusing to silently drop one"
            )
        coordinates[coordinate] = cell

        cell_id = cell.get("cell_id")
        if isinstance(cell_id, str) and cell_id:
            previous_id = cell_ids.get(cell_id)
            if previous_id is not None:
                raise ContractError(
                    "s10: duplicate cell_id is not a valid input identity "
                    f"(side={side}, logical_table={logical_table_id}, "
                    f"source_table={element_id!r}, cell_id={cell_id!r}): rows "
                    f"{int(previous_id.get('row_index', 0))} and {row_index} "
                    "share one cell_id; refusing to silently drop one"
                )
            cell_ids[cell_id] = cell


def compact_cell(cell: dict[str, Any] | None) -> dict[str, Any] | None:
    if cell is None:
        return None
    return {
        "cell_id": cell.get("cell_id"),
        "source_table_id": cell.get("_source_table_id"),
        "source_row_index": cell.get("_source_row_index", cell.get("row_index")),
        "row_index": cell.get("_logical_row_index"),
        "column_index": cell.get("column_index"),
        "row_span": cell.get("row_span") or 1,
        "column_span": cell.get("column_span") or 1,
        "kind": cell.get("kind"),
        "text": cell.get("text"),
        "normalized_text": cell.get("normalized_text"),
        "primary_page": cell.get("primary_page"),
        "primary_polygon": cell.get("primary_polygon"),
        "spans": cell.get("spans") or [],
    }


def shared_provisional_context_keys(
    old_element: dict[str, Any],
    new_element: dict[str, Any],
    old_key_map: dict[str, str],
    new_key_map: dict[str, str],
) -> tuple[str, ...]:
    old_keys = provisional_context_keys(old_element, old_key_map)
    new_keys = provisional_context_keys(new_element, new_key_map)
    new_set = set(new_keys)
    return tuple(key for key in old_keys if key in new_set)


def provisional_context_keys(
    element: dict[str, Any],
    key_map: dict[str, str],
) -> tuple[str, ...]:
    context = element.get("provisional_anchor_context") or {}
    ids = context.get("recent_anchor_ids") or []
    keys = []
    for anchor_id in ids:
        key = key_map.get(str(anchor_id))
        if key:
            keys.append(key)
    return tuple(keys)


def nearest_provisional_context_key(
    element: dict[str, Any],
    key_map: dict[str, str],
) -> str | None:
    keys = provisional_context_keys(element, key_map)
    return keys[-1] if keys else None




# ---- cell 8 ----
def can_continue_logical_table(
    previous: dict[str, Any],
    current: dict[str, Any],
    key_map: dict[str, str],
) -> tuple[bool, dict[str, Any]]:
    previous_page = previous.get("primary_page")
    current_page = current.get("primary_page")

    evidence: dict[str, Any] = {
        "adjacent_pages": False,
        "same_column_count": False,
        "header_similarity": 0.0,
        "context_support": None,
        "context_is_authoritative_hierarchy": False,
    }

    if not isinstance(previous_page, int) or not isinstance(current_page, int):
        return False, evidence

    evidence["adjacent_pages"] = current_page == previous_page + 1
    if not evidence["adjacent_pages"]:
        return False, evidence

    previous_columns = (previous.get("table") or {}).get("column_count")
    current_columns = (current.get("table") or {}).get("column_count")
    evidence["same_column_count"] = (
        bool(previous_columns)
        and previous_columns == current_columns
    )
    if not evidence["same_column_count"]:
        return False, evidence

    previous_header = header_signature(previous)
    current_header = header_signature(current)
    similarity = (
        header_similarity(previous_header, current_header)
        if previous_header and current_header
        else 0.0
    )
    evidence["header_similarity"] = similarity

    if not previous_header or not current_header:
        return False, evidence

    context_support = same_document_context_support(
        previous,
        current,
        key_map,
    )
    evidence["context_support"] = context_support

    # Normal continuation threshold.
    if similarity >= 0.80 and context_support["has_shared_context"]:
        return True, evidence

    # Context may be missing or may differ because headings themselves changed.
    # In that case require a stronger repeated-header match.
    if similarity >= 0.92:
        return True, evidence

    return False, evidence


def group_physical_tables(
    tables: list[dict[str, Any]],
    key_map: dict[str, str],
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    ordered = sorted(
        tables,
        key=lambda table: (
            table.get("reading_order")
            if isinstance(table.get("reading_order"), int)
            else 10**12,
            table.get("element_id") or "",
        ),
    )

    groups: list[list[dict[str, Any]]] = []
    join_audit: list[dict[str, Any]] = []

    for table in ordered:
        if groups:
            previous = groups[-1][-1]
            allowed, evidence = can_continue_logical_table(
                previous,
                table,
                key_map,
            )
            if allowed:
                groups[-1].append(table)
                join_audit.append({
                    "previous_table_id": previous.get("element_id"),
                    "current_table_id": table.get("element_id"),
                    "joined": True,
                    "evidence": evidence,
                })
                continue

        groups.append([table])

    return groups, join_audit


def build_logical_table(
    side: str,
    index: int,
    fragments: list[dict[str, Any]],
    key_map: dict[str, str],
) -> dict[str, Any]:
    logical_rows: list[dict[str, Any]] = []
    omitted_header_rows = 0
    omitted_header_cells = 0
    first_header = header_signature(fragments[0])

    for fragment_index, fragment in enumerate(fragments):
        validate_fragment_cell_contract(side, index, fragment)

        header_rows = header_row_indexes(fragment)
        repeated_header = (
            fragment_index > 0
            and bool(first_header)
            and header_similarity(
                first_header,
                header_signature(fragment),
            ) >= 0.80
        )

        fragment_cells = table_cells(fragment)
        cells_by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for original_cell in fragment_cells:
            cells_by_row[int(original_cell.get("row_index", 0))].append(
                original_cell
            )

        placed_cells = 0
        fragment_omitted_cells = 0
        for source_row_index in range(
            int((fragment.get("table") or {}).get("row_count") or 0)
        ):
            row_cells = sorted(
                cells_by_row.get(source_row_index, []),
                key=lambda cell: int(cell.get("column_index", 0)),
            )

            if repeated_header and source_row_index in header_rows:
                omitted_header_rows += 1
                omitted_header_cells += len(row_cells)
                fragment_omitted_cells += len(row_cells)
                continue

            logical_row_index = len(logical_rows)
            copied_cells = []

            for original_cell in row_cells:
                copied = dict(original_cell)
                copied["_source_table_id"] = fragment.get("element_id")
                copied["_source_row_index"] = source_row_index
                copied["_logical_row_index"] = logical_row_index
                copied_cells.append(copied)

            placed_cells += len(copied_cells)
            logical_rows.append({
                "logical_row_index": logical_row_index,
                "source_table_id": fragment.get("element_id"),
                "source_row_index": source_row_index,
                "is_header": source_row_index in header_rows,
                "cells": copied_cells,
            })

        # Every physical cell must be preserved either in a logical row or as
        # a documented repeated-continuation-header omission.
        require(
            placed_cells + fragment_omitted_cells == len(fragment_cells),
            "s10: physical cell accounting failed for physical table "
            f"{fragment.get('element_id')!r} (side={side}): "
            f"source_cells={len(fragment_cells)}, placed_cells={placed_cells}, "
            f"omitted_repeated_header_cells={fragment_omitted_cells}",
        )

    first = fragments[0]
    extraction_validations = [
        fragment.get("extraction_validation") or {}
        for fragment in fragments
    ]

    context_keys = provisional_context_keys(first, key_map)

    return {
        "logical_table_id": f"{side.upper()}-LT{index:04}",
        "side": side,
        "source_table_ids": [
            fragment.get("element_id")
            for fragment in fragments
        ],
        "source_fragment_count": len(fragments),
        "source_pages": [
            fragment.get("primary_page")
            for fragment in fragments
        ],
        "first_reading_order": first.get("reading_order"),
        "provisional_context_keys": list(context_keys),
        "nearest_provisional_context_key":
            context_keys[-1] if context_keys else None,
        "context_is_authoritative_hierarchy": False,
        "requires_extraction_review": any(
            validation.get("requires_extraction_review", False)
            for validation in extraction_validations
        ),
        "extraction_warning_flags": sorted({
            flag
            for validation in extraction_validations
            for flag in validation.get("extraction_warning_flags") or []
        }),
        "extraction_review_flags": sorted({
            flag
            for validation in extraction_validations
            for flag in validation.get("extraction_review_flags") or []
        }),
        "column_count":
            int((first.get("table") or {}).get("column_count") or 0),
        "row_count": len(logical_rows),
        "header_signature": list(first_header),
        "rows": logical_rows,
        "omitted_repeated_header_rows": omitted_header_rows,
        "omitted_repeated_header_cells": omitted_header_cells,
    }


def make_logical_tables(
    side: str,
    tables: list[dict[str, Any]],
    key_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups, join_audit = group_physical_tables(tables, key_map)

    logical = [
        build_logical_table(
            side,
            index,
            fragments,
            key_map,
        )
        for index, fragments in enumerate(groups, start=1)
    ]

    return logical, join_audit


# ---- cell 10 ----
def row_layout_text(row: dict[str, Any]) -> str:
    return " | ".join(
        layout_text(cell_text(cell))
        for cell in sorted(row.get("cells") or [], key=lambda cell: int(cell.get("column_index", 0)))
    )


def row_source_text(row: dict[str, Any]) -> str:
    return " | ".join(
        source_text(cell_text(cell))
        for cell in sorted(row.get("cells") or [], key=lambda cell: int(cell.get("column_index", 0)))
    )


def logical_text_signature(table: dict[str, Any]) -> tuple[str, ...]:
    return tuple(row_layout_text(row) for row in table.get("rows") or [])


def logical_full_text(table: dict[str, Any]) -> str:
    return " || ".join(logical_text_signature(table))


def logical_header_text(table: dict[str, Any]) -> str:
    return " | ".join(table.get("header_signature") or [])


def table_similarity(old: dict[str, Any], new: dict[str, Any]) -> dict[str, float]:
    old_text = logical_full_text(old)
    new_text = logical_full_text(new)
    content = SequenceMatcher(None, old_text, new_text, autojunk=False).ratio() if old_text or new_text else 0.0
    old_header = logical_header_text(old)
    new_header = logical_header_text(new)
    header = SequenceMatcher(None, old_header, new_header, autojunk=False).ratio() if old_header or new_header else 0.0
    old_columns = old.get("column_count") or 0
    new_columns = new.get("column_count") or 0
    column_shape = min(old_columns, new_columns) / max(old_columns, new_columns) if old_columns and new_columns else 0.0
    old_rows = old.get("row_count") or 0
    new_rows = new.get("row_count") or 0
    row_shape = min(old_rows, new_rows) / max(old_rows, new_rows) if old_rows and new_rows else 0.0
    combined = 0.65 * content + 0.20 * header + 0.10 * column_shape + 0.05 * row_shape
    return {
        "combined_similarity": combined,
        "content_similarity": content,
        "header_similarity": header,
        "column_shape_similarity": column_shape,
        "row_shape_similarity": row_shape,
    }


def table_context_relation(
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    old_keys = tuple(old.get("provisional_context_keys") or [])
    new_keys = tuple(new.get("provisional_context_keys") or [])
    new_set = set(new_keys)
    shared = tuple(key for key in old_keys if key in new_set)

    if shared:
        relation = "shared_provisional_context"
    elif old_keys and new_keys:
        relation = "different_provisional_context"
    else:
        relation = "missing_or_one_sided_provisional_context"

    return {
        "relation": relation,
        "shared_context_keys": list(shared),
        "old_context_keys": list(old_keys),
        "new_context_keys": list(new_keys),
        "context_is_authoritative_hierarchy": False,
    }


def match_logical_tables(
    old_tables: list[dict[str, Any]],
    new_tables: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    matches: list[dict[str, Any]] = []
    matched_old: set[str] = set()
    matched_new: set[str] = set()

    old_exact: dict[
        tuple[int, tuple[str, ...]],
        list[dict[str, Any]],
    ] = defaultdict(list)
    new_exact: dict[
        tuple[int, tuple[str, ...]],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for table in old_tables:
        old_exact[
            (table["column_count"], logical_text_signature(table))
        ].append(table)

    for table in new_tables:
        new_exact[
            (table["column_count"], logical_text_signature(table))
        ].append(table)

    ambiguous_exact_groups = 0

    for key in sorted(
        set(old_exact).intersection(new_exact),
        key=repr,
    ):
        old_group = old_exact[key]
        new_group = new_exact[key]

        if len(old_group) == len(new_group) == 1:
            pairs = [
                (
                    old_group[0],
                    new_group[0],
                    False,
                )
            ]
        else:
            # Resolve duplicate exact tables only where comparable
            # provisional context is shared. Context is not authoritative;
            # it is only an assignment aid.
            candidate_pairs = []
            for old in old_group:
                for new in new_group:
                    relation = table_context_relation(old, new)
                    if relation["relation"] != "shared_provisional_context":
                        continue
                    candidate_pairs.append((old, new, relation))

            by_old = defaultdict(list)
            by_new = defaultdict(list)
            for old, new, relation in candidate_pairs:
                by_old[old["logical_table_id"]].append(
                    (old, new, relation)
                )
                by_new[new["logical_table_id"]].append(
                    (old, new, relation)
                )

            pairs = []
            for old, new, relation in candidate_pairs:
                if len(by_old[old["logical_table_id"]]) != 1:
                    continue
                if len(by_new[new["logical_table_id"]]) != 1:
                    continue
                pairs.append((old, new, True))

            if not pairs:
                ambiguous_exact_groups += 1

        for old, new, ambiguous in pairs:
            if (
                old["logical_table_id"] in matched_old
                or new["logical_table_id"] in matched_new
            ):
                continue

            relation = table_context_relation(old, new)

            matches.append({
                "old": old,
                "new": new,
                "match_method": "exact_logical_table_content",
                "matching_evidence": {
                    "combined_similarity": 1.0,
                    "source_assignment_ambiguous": ambiguous,
                    "provisional_context": relation,
                },
            })
            matched_old.add(old["logical_table_id"])
            matched_new.add(new["logical_table_id"])

    remaining_old = [
        table
        for table in old_tables
        if table["logical_table_id"] not in matched_old
    ]
    remaining_new = [
        table
        for table in new_tables
        if table["logical_table_id"] not in matched_new
    ]

    candidate_pairs: list[dict[str, Any]] = []

    for old in remaining_old:
        for new in remaining_new:
            evidence = table_similarity(old, new)
            context = table_context_relation(old, new)

            # Content/header similarity drives retrieval.
            # Shared provisional context can support weaker candidates;
            # different context requires stronger content evidence.
            if context["relation"] == "shared_provisional_context":
                candidate_allowed = (
                    evidence["content_similarity"] >= 0.35
                    or evidence["header_similarity"] >= 0.70
                )
            else:
                candidate_allowed = (
                    evidence["content_similarity"] >= 0.70
                    or (
                        evidence["content_similarity"] >= 0.55
                        and evidence["header_similarity"] >= 0.90
                    )
                )

            if not candidate_allowed:
                continue

            evidence = dict(evidence)
            evidence["provisional_context"] = context

            candidate_pairs.append({
                "old": old,
                "new": new,
                "evidence": evidence,
            })

    by_old: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_new: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for pair in candidate_pairs:
        by_old[pair["old"]["logical_table_id"]].append(pair)
        by_new[pair["new"]["logical_table_id"]].append(pair)

    fuzzy_selected = 0

    for pair in candidate_pairs:
        old_id = pair["old"]["logical_table_id"]
        new_id = pair["new"]["logical_table_id"]

        old_ranked = sorted(
            by_old[old_id],
            key=lambda item: item["evidence"]["combined_similarity"],
            reverse=True,
        )
        new_ranked = sorted(
            by_new[new_id],
            key=lambda item: item["evidence"]["combined_similarity"],
            reverse=True,
        )

        if old_ranked[0]["new"]["logical_table_id"] != new_id:
            continue
        if new_ranked[0]["old"]["logical_table_id"] != old_id:
            continue

        score = pair["evidence"]["combined_similarity"]

        old_margin = score - (
            old_ranked[1]["evidence"]["combined_similarity"]
            if len(old_ranked) > 1 else 0.0
        )
        new_margin = score - (
            new_ranked[1]["evidence"]["combined_similarity"]
            if len(new_ranked) > 1 else 0.0
        )

        if score < 0.62 or old_margin < 0.03 or new_margin < 0.03:
            continue

        if old_id in matched_old or new_id in matched_new:
            continue

        evidence = dict(pair["evidence"])
        evidence.update({
            "threshold": 0.62,
            "old_ambiguity_margin": old_margin,
            "new_ambiguity_margin": new_margin,
        })

        matches.append({
            "old": pair["old"],
            "new": pair["new"],
            "match_method":
                "reciprocal_table_similarity_with_provisional_context_support",
            "matching_evidence": evidence,
        })

        matched_old.add(old_id)
        matched_new.add(new_id)
        fuzzy_selected += 1

    matches.sort(
        key=lambda match: (
            match["old"].get("first_reading_order") or 0,
            match["new"].get("first_reading_order") or 0,
        )
    )

    unmatched_old = [
        table
        for table in old_tables
        if table["logical_table_id"] not in matched_old
    ]
    unmatched_new = [
        table
        for table in new_tables
        if table["logical_table_id"] not in matched_new
    ]

    return matches, unmatched_old, unmatched_new, {
        "exact_matches": sum(
            match["match_method"] == "exact_logical_table_content"
            for match in matches
        ),
        "fuzzy_matches": fuzzy_selected,
        "ambiguous_exact_groups": ambiguous_exact_groups,
        "fuzzy_candidate_pairs": len(candidate_pairs),
    }


# ---- cell 12 ----
def fuzzy_align_replace_block(
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    if not old_rows or not new_rows:
        return [], list(range(len(old_rows))), list(range(len(new_rows)))

    product = len(old_rows) * len(new_rows)
    if product > 20_000:
        pair_count = min(len(old_rows), len(new_rows))
        matched = []
        for index in range(pair_count):
            similarity = SequenceMatcher(
                None, row_layout_text(old_rows[index]), row_layout_text(new_rows[index]), autojunk=False
            ).ratio()
            if similarity >= 0.45:
                matched.append({"old_offset": index, "new_offset": index, "similarity": similarity, "method": "large_block_position"})
        used_old = {item["old_offset"] for item in matched}
        used_new = {item["new_offset"] for item in matched}
        return matched, [i for i in range(len(old_rows)) if i not in used_old], [i for i in range(len(new_rows)) if i not in used_new]

    candidates = []
    for old_index, old_row in enumerate(old_rows):
        old_text = row_layout_text(old_row)
        for new_index, new_row in enumerate(new_rows):
            new_text = row_layout_text(new_row)
            similarity = SequenceMatcher(None, old_text, new_text, autojunk=False).ratio()
            if similarity >= 0.45:
                candidates.append((similarity, old_index, new_index))

    by_old: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    by_new: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    for candidate in candidates:
        by_old[candidate[1]].append(candidate)
        by_new[candidate[2]].append(candidate)

    selected = []
    for similarity, old_index, new_index in candidates:
        old_ranked = sorted(by_old[old_index], reverse=True)
        new_ranked = sorted(by_new[new_index], reverse=True)
        if old_ranked[0][2] != new_index or new_ranked[0][1] != old_index:
            continue
        old_margin = similarity - (old_ranked[1][0] if len(old_ranked) > 1 else 0.0)
        new_margin = similarity - (new_ranked[1][0] if len(new_ranked) > 1 else 0.0)
        if old_margin >= 0.03 and new_margin >= 0.03:
            selected.append((old_index, new_index, similarity))

    selected.sort()
    monotonic = []
    last_new = -1
    for old_index, new_index, similarity in selected:
        if new_index > last_new:
            monotonic.append(
                {"old_offset": old_index, "new_offset": new_index, "similarity": similarity, "method": "replace_block_reciprocal_similarity"}
            )
            last_new = new_index
    used_old = {item["old_offset"] for item in monotonic}
    used_new = {item["new_offset"] for item in monotonic}
    return monotonic, [i for i in range(len(old_rows)) if i not in used_old], [i for i in range(len(new_rows)) if i not in used_new]


def align_rows(old_table: dict[str, Any], new_table: dict[str, Any]) -> dict[str, Any]:
    old_rows = old_table.get("rows") or []
    new_rows = new_table.get("rows") or []
    old_signatures = [row_layout_text(row) for row in old_rows]
    new_signatures = [row_layout_text(row) for row in new_rows]
    matcher = SequenceMatcher(None, old_signatures, new_signatures, autojunk=False)

    matched_rows = []
    deleted_rows = []
    added_rows = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(old_end - old_start):
                matched_rows.append(
                    {
                        "old_row_index": old_start + offset,
                        "new_row_index": new_start + offset,
                        "row_match_type": "layout_exact_order_alignment",
                        "row_similarity": 1.0,
                    }
                )
        elif tag == "delete":
            deleted_rows.extend(range(old_start, old_end))
        elif tag == "insert":
            added_rows.extend(range(new_start, new_end))
        else:
            fuzzy, remaining_old, remaining_new = fuzzy_align_replace_block(
                old_rows[old_start:old_end], new_rows[new_start:new_end]
            )
            for item in fuzzy:
                matched_rows.append(
                    {
                        "old_row_index": old_start + item["old_offset"],
                        "new_row_index": new_start + item["new_offset"],
                        "row_match_type": item["method"],
                        "row_similarity": item["similarity"],
                    }
                )
            deleted_rows.extend(old_start + offset for offset in remaining_old)
            added_rows.extend(new_start + offset for offset in remaining_new)

    matched_rows.sort(key=lambda item: (item["old_row_index"], item["new_row_index"]))
    return {
        "matched_rows": matched_rows,
        "deleted_rows": sorted(deleted_rows),
        "added_rows": sorted(added_rows),
    }


# ---- cell 14 ----
def compact_token_diff(old_text: str, new_text: str) -> dict[str, Any]:
    old_tokens = TOKEN_PATTERN.findall(old_text or "")
    new_tokens = TOKEN_PATTERN.findall(new_text or "")
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    changes = [
        {
            "operation": tag,
            "old_token_start": old_start,
            "old_token_end": old_end,
            "new_token_start": new_start,
            "new_token_end": new_end,
            "old_tokens": old_tokens[old_start:old_end],
            "new_tokens": new_tokens[new_start:new_end],
        }
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes()
        if tag != "equal"
    ]
    return {
        "old_token_count": len(old_tokens),
        "new_token_count": len(new_tokens),
        "change_operation_count": len(changes),
        "token_sequence_ratio": matcher.ratio(),
        "changes": changes,
    }


def cell_structure(cell: dict[str, Any]) -> tuple[int, int, str | None]:
    return (
        int(cell.get("row_span") or 1),
        int(cell.get("column_span") or 1),
        cell.get("kind"),
    )


def compare_cell(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    if old is None:
        return {"classification": "ADDED", "old": None, "new": compact_cell(new), "token_diff": None, "structure_changed": False, "needs_review": False, "review_reasons": []}
    if new is None:
        return {"classification": "DELETED", "old": compact_cell(old), "new": None, "token_diff": None, "structure_changed": False, "needs_review": False, "review_reasons": []}

    old_raw = source_text(cell_text(old))
    new_raw = source_text(cell_text(new))
    old_layout = layout_text(cell_text(old))
    new_layout = layout_text(cell_text(new))
    old_artifact = artifact_tolerant_text(cell_text(old))
    new_artifact = artifact_tolerant_text(cell_text(new))
    old_selection_states = selection_states(cell_text(old))
    new_selection_states = selection_states(cell_text(new))
    old_identifier = compact_identifier_text(cell_text(old))
    new_identifier = compact_identifier_text(cell_text(new))
    identifier_equivalent = bool(old_identifier and old_identifier == new_identifier)
    structure_changed = cell_structure(old) != cell_structure(new)
    selection_state_changed = bool(
        old_selection_states
        and new_selection_states
        and old_selection_states != new_selection_states
    )
    artifact_equivalent = bool(
        old_artifact == new_artifact
        or alphanumeric_skeleton(old_raw) == alphanumeric_skeleton(new_raw)
    )
    review_reasons: list[str] = []
    if old_raw == new_raw and not structure_changed:
        classification = "UNCHANGED"
        token_diff = None
    elif old_layout == new_layout and not structure_changed:
        classification = "LAYOUT_EQUIVALENT"
        token_diff = None
    elif old_layout == new_layout and structure_changed:
        classification = "STRUCTURE_CHANGED_REVIEW"
        token_diff = None
        review_reasons.append("merged_cell_or_header_kind_changed")
    elif selection_state_changed:
        classification = "MODIFIED"
        review_reasons.append("selection_state_changed")
        token_diff = {
            "source_token_diff": compact_token_diff(old_raw, new_raw),
            "layout_normalized_token_diff": compact_token_diff(old_layout, new_layout),
        }
    elif artifact_equivalent or identifier_equivalent:
        classification = "STRUCTURE_CHANGED_REVIEW" if structure_changed else "EXTRACTION_ARTIFACT_REVIEW"
        token_diff = {
            "source_token_diff": compact_token_diff(old_raw, new_raw),
            "conservative_layout_token_diff": compact_token_diff(old_layout, new_layout),
        }
        if old_selection_states != new_selection_states:
            review_reasons.append("selection_marker_extraction_difference")
        else:
            review_reasons.append("hyphenation_spacing_or_ocr_artifact")
        if structure_changed:
            review_reasons.append("merged_cell_or_header_kind_changed")
    else:
        classification = "MODIFIED"
        token_diff = {
            "source_token_diff": compact_token_diff(old_raw, new_raw),
            "layout_normalized_token_diff": compact_token_diff(old_layout, new_layout),
        }
    return {
        "classification": classification,
        "old": compact_cell(old),
        "new": compact_cell(new),
        "token_diff": token_diff,
        "structure_changed": structure_changed,
        "needs_review": bool(review_reasons),
        "review_reasons": sorted(set(review_reasons)),
        "old_structure": list(cell_structure(old)),
        "new_structure": list(cell_structure(new)),
    }


def cells_by_column(row: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Map one logical row's cells by column index, rejecting duplicates.

    A plain ``{column_index: cell}`` comprehension silently keeps only the
    last cell when two share a column, dropping the other from the comparison
    (for example a changed ``21 -> 99`` cell hidden behind an unchanged
    duplicate). Malformed input is rejected with actionable identifiers
    instead of fabricating a pairing.
    """
    cells: dict[int, dict[str, Any]] = {}
    for cell in row.get("cells") or []:
        column = int(cell.get("column_index", 0))
        previous = cells.get(column)
        if previous is not None:
            raise ContractError(
                "s10: duplicate cell coordinate in logical row is not a valid "
                "pairing (source_table="
                f"{row.get('source_table_id')!r}, "
                f"source_row={row.get('source_row_index')!r}, column={column}): "
                f"cells {previous.get('cell_id')!r} and {cell.get('cell_id')!r} "
                "share one coordinate; refusing to silently drop one"
            )
        cells[column] = cell
    return cells


def cell_accounting_identity(cell: dict[str, Any]) -> tuple[Any, Any, int]:
    """A stable identity for exactly-once cell accounting.

    Works on both logical-row cells (``_source_table_id``/
    ``_source_row_index``) and their compacted comparison form
    (``source_table_id``/``source_row_index``), so a cell can be traced from
    the logical table into the matched/deleted/added change list.
    """
    return (
        cell.get("_source_table_id", cell.get("source_table_id")),
        cell.get(
            "_source_row_index",
            cell.get("source_row_index", cell.get("row_index")),
        ),
        int(cell.get("column_index", 0)),
    )


def require_exactly_once_cell_accounting(
    side: str,
    table: dict[str, Any],
    accounted: list[dict[str, Any] | None],
    index: int,
) -> int:
    """Require every logical-row cell to appear exactly once when accounted.

    Compared by cell identity, not by a bare summary count, so a dropped or
    duplicated cell is named in the raised :class:`ContractError` rather than
    hidden. Returns the number of logical-row cells that were accounted.
    """
    expected = Counter(
        cell_accounting_identity(cell)
        for row in table.get("rows") or []
        for cell in row.get("cells") or []
    )
    observed = Counter(
        cell_accounting_identity(cell)
        for cell in accounted
        if cell is not None
    )
    if expected != observed:
        missing = sorted(expected - observed, key=repr)
        extra = sorted(observed - expected, key=repr)
        raise ContractError(
            "s10: exactly-once cell accounting failed for "
            f"{table.get('logical_table_id')!r} "
            f"(side={side}, TABLE-MATCH-{index:04}): "
            f"cells_in_logical_rows={sum(expected.values())}, "
            f"cells_accounted={sum(observed.values())}, "
            f"missing={missing}, unexpectedly_accounted={extra}"
        )
    return sum(expected.values())


# ---- cell 16 ----
def compact_logical_table(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "logical_table_id": table.get("logical_table_id"),
        "source_table_ids": table.get("source_table_ids") or [],
        "source_fragment_count": table.get("source_fragment_count"),
        "source_pages": table.get("source_pages") or [],
        "first_reading_order": table.get("first_reading_order"),
        "provisional_context_keys":
            table.get("provisional_context_keys") or [],
        "nearest_provisional_context_key":
            table.get("nearest_provisional_context_key"),
        "context_is_authoritative_hierarchy": False,
        "requires_extraction_review":
            bool(table.get("requires_extraction_review")),
        "extraction_warning_flags":
            table.get("extraction_warning_flags") or [],
        "extraction_review_flags":
            table.get("extraction_review_flags") or [],
        "row_count": table.get("row_count"),
        "column_count": table.get("column_count"),
        "header_signature": table.get("header_signature") or [],
        "omitted_repeated_header_rows":
            table.get("omitted_repeated_header_rows"),
        "omitted_repeated_header_cells":
            table.get("omitted_repeated_header_cells"),
    }


def compare_table_match(match: dict[str, Any], index: int) -> dict[str, Any]:
    old = match["old"]
    new = match["new"]
    alignment = align_rows(old, new)
    cell_changes = []

    for row_match in alignment["matched_rows"]:
        old_row = old["rows"][row_match["old_row_index"]]
        new_row = new["rows"][row_match["new_row_index"]]
        old_cells = cells_by_column(old_row)
        new_cells = cells_by_column(new_row)
        for column in sorted(set(old_cells).union(new_cells)):
            comparison = compare_cell(old_cells.get(column), new_cells.get(column))
            comparison.update(
                {
                    "change_id": f"TABLE-{index:04}-R{row_match['old_row_index']:05}-C{column:03}",
                    "old_row_index": row_match["old_row_index"],
                    "new_row_index": row_match["new_row_index"],
                    "column_index": column,
                    "row_match_type": row_match["row_match_type"],
                    "row_similarity": row_match["row_similarity"],
                }
            )
            cell_changes.append(comparison)

    for old_row_index in alignment["deleted_rows"]:
        for column, old_cell in sorted(cells_by_column(old["rows"][old_row_index]).items()):
            comparison = compare_cell(old_cell, None)
            comparison.update(
                {
                    "change_id": f"TABLE-{index:04}-OLD-R{old_row_index:05}-C{column:03}",
                    "old_row_index": old_row_index,
                    "new_row_index": None,
                    "column_index": column,
                    "row_match_type": "deleted_row",
                    "row_similarity": None,
                }
            )
            cell_changes.append(comparison)

    for new_row_index in alignment["added_rows"]:
        for column, new_cell in sorted(cells_by_column(new["rows"][new_row_index]).items()):
            comparison = compare_cell(None, new_cell)
            comparison.update(
                {
                    "change_id": f"TABLE-{index:04}-NEW-R{new_row_index:05}-C{column:03}",
                    "old_row_index": None,
                    "new_row_index": new_row_index,
                    "column_index": column,
                    "row_match_type": "added_row",
                    "row_similarity": None,
                }
            )
            cell_changes.append(comparison)

    old_cells_accounted = require_exactly_once_cell_accounting(
        "old",
        old,
        [change.get("old") for change in cell_changes],
        index,
    )
    new_cells_accounted = require_exactly_once_cell_accounting(
        "new",
        new,
        [change.get("new") for change in cell_changes],
        index,
    )

    counts = Counter(change["classification"] for change in cell_changes)
    if counts["MODIFIED"] or counts["ADDED"] or counts["DELETED"]:
        classification = "MODIFIED"
    elif counts["STRUCTURE_CHANGED_REVIEW"]:
        classification = "STRUCTURE_CHANGED_REVIEW"
    elif counts["EXTRACTION_ARTIFACT_REVIEW"]:
        classification = "EXTRACTION_ARTIFACT_REVIEW"
    elif counts["LAYOUT_EQUIVALENT"]:
        classification = "LAYOUT_EQUIVALENT"
    else:
        classification = "UNCHANGED"

    context_relation = table_context_relation(old, new)
    movement_classification = None
    review_reasons = []
    evidence = match.get("matching_evidence") or {}
    review_reasons.extend(
        reason
        for change in cell_changes
        for reason in change.get("review_reasons") or []
    )
    if evidence.get("source_assignment_ambiguous"):
        review_reasons.append("ambiguous_duplicate_table_assignment")
    if match["match_method"] != "exact_logical_table_content" and evidence.get("combined_similarity", 1.0) < 0.75:
        review_reasons.append("table_similarity_near_threshold")
    if old.get("column_count") != new.get("column_count"):
        review_reasons.append("column_count_changed")
    if counts["STRUCTURE_CHANGED_REVIEW"]:
        review_reasons.append("merged_cell_or_header_kind_changed")
    if counts["EXTRACTION_ARTIFACT_REVIEW"]:
        review_reasons.append("cell_difference_only_matches_aggressive_artifact_normalization")
    if counts["ADDED"] or counts["DELETED"]:
        review_reasons.append("cell_or_row_boundary_changed")
    if context_relation["relation"] == "different_provisional_context":
        review_reasons.append("table_provisional_context_differs")
    if old.get("requires_extraction_review") or new.get("requires_extraction_review"):
        review_reasons.append("source_table_requires_extraction_review")
    if any((item.get("row_similarity") or 1.0) < 0.65 for item in alignment["matched_rows"]):
        review_reasons.append("low_row_alignment_similarity")

    return {
        "table_match_id": f"TABLE-MATCH-{index:04}",
        "classification": classification,
        "movement_classification": movement_classification,
        "context_relation": context_relation,
        "match_method": match["match_method"],
        "matching_evidence": evidence,
        "old": compact_logical_table(old),
        "new": compact_logical_table(new),
        "needs_review": bool(review_reasons),
        "review_reasons": sorted(set(review_reasons)),
        "row_alignment": alignment,
        "statistics": {
            "unchanged_cells": counts["UNCHANGED"],
            "layout_equivalent_cells": counts["LAYOUT_EQUIVALENT"],
            "extraction_artifact_review_cells": counts["EXTRACTION_ARTIFACT_REVIEW"],
            "structure_changed_review_cells": counts["STRUCTURE_CHANGED_REVIEW"],
            "modified_cells": counts["MODIFIED"],
            "added_cells": counts["ADDED"],
            "deleted_cells": counts["DELETED"],
            "matched_rows": len(alignment["matched_rows"]),
            "added_rows": len(alignment["added_rows"]),
            "deleted_rows": len(alignment["deleted_rows"]),
        },
        "cell_accounting": {
            "basis":
                "cell identity (source_table_id, source_row_index, column_index)",
            "old_cells_in_logical_rows": old_cells_accounted,
            "old_cells_accounted": old_cells_accounted,
            "new_cells_in_logical_rows": new_cells_accounted,
            "new_cells_accounted": new_cells_accounted,
            "old_omitted_repeated_header_cells":
                old.get("omitted_repeated_header_cells"),
            "new_omitted_repeated_header_cells":
                new.get("omitted_repeated_header_cells"),
        },
        "cell_changes": cell_changes,
    }


def compact_unmatched_table(
    table: dict[str, Any],
    classification: str,
) -> dict[str, Any]:
    """Compact a one-sided (deleted/added) logical table with its cell content.

    A deleted/added table used to be serialized as metadata only, so its real
    physical cell text and provenance were dropped before the redline even when
    the source table carried full content. Every logical-row cell becomes
    exactly one one-sided cell change through the same ``compare_cell`` path and
    the same exactly-once accounting used for matched tables, so no cell is
    silently lost and the old/new orientation is preserved.

    ``classification`` is ``DELETED`` (old-only) or ``ADDED`` (new-only). The
    compacted result keeps ``compact_logical_table``'s metadata shape and adds
    ``cell_changes`` plus an explicit ``cell_content_status`` of ``available``.
    """
    compact = compact_logical_table(table)
    deleted = classification == "DELETED"
    side = "old" if deleted else "new"
    logical_table_id = table.get("logical_table_id")
    cell_changes: list[dict[str, Any]] = []

    for row in table.get("rows") or []:
        logical_row_index = row.get("logical_row_index")
        for column, cell in sorted(cells_by_column(row).items()):
            comparison = compare_cell(cell, None) if deleted else compare_cell(None, cell)
            comparison.update({
                "change_id":
                    f"TABLE-{classification}-{logical_table_id}"
                    f"-R{logical_row_index:05}-C{column:03}",
                "old_row_index": logical_row_index if deleted else None,
                "new_row_index": None if deleted else logical_row_index,
                "column_index": column,
                "row_match_type": "deleted_table" if deleted else "added_table",
                "row_similarity": None,
            })
            cell_changes.append(comparison)

    require_exactly_once_cell_accounting(
        side,
        table,
        [
            change.get("old") if deleted else change.get("new")
            for change in cell_changes
        ],
        0,
    )

    compact["cell_changes"] = cell_changes
    compact["cell_content_status"] = "available"
    return compact


def residual_context_key(
    table: dict[str, Any],
) -> str | None:
    return table.get("nearest_provisional_context_key")


def main() -> None:
    """Run Stage 10 against the workspace and save the table result."""


    # ---- cell 3 ----
    project_root = find_project_root()
    output_dir = project_root / "output"

    old_structured_path = output_dir / "old_document_structured.json"
    new_structured_path = output_dir / "new_document_structured.json"
    movement_path = output_dir / "comparison_candidates_with_movement.json"
    table_result_path = output_dir / "table_comparison_result.json"

    for path in (old_structured_path, new_structured_path, movement_path):
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Run the preceding pipeline steps first."
            )

    print("Step 10 version:", STEP10_VERSION)
    print("Project root:", project_root)



    old_document = load_json(old_structured_path)
    new_document = load_json(new_structured_path)
    movement_result = load_json(movement_path)

    if (
        old_document.get("schema_version") != "0.3.0"
        or new_document.get("schema_version") != "0.3.0"
    ):
        raise ValueError(
            "Step 10 v2 requires Step-5 structured-document schema 0.3.0."
        )

    if movement_result.get("comparison_stage") != EXPECTED_STEP9_STAGE:
        raise ValueError(
            "Step 10 v2 requires the current Step-9 movement/structural evidence result. "
            f"Got {movement_result.get('comparison_stage')!r}."
        )

    old_meta = old_document.get("document") or {}
    new_meta = new_document.get("document") or {}

    # Step 9 carries compact document metadata under old_document/new_document.
    for side, document, step9_document in (
        ("old", old_meta, movement_result.get("old_document") or {}),
        ("new", new_meta, movement_result.get("new_document") or {}),
    ):
        if step9_document:
            if (
                step9_document.get("document_id") is not None
                and document.get("document_id") != step9_document.get("document_id")
            ):
                raise ValueError(
                    f"Structured/Step-9 document mismatch for {side}.document_id."
                )

    old_physical_tables = [
        element
        for element in old_meta.get("elements") or []
        if element.get("element_type") == "table"
        and isinstance(element.get("table"), dict)
    ]
    new_physical_tables = [
        element
        for element in new_meta.get("elements") or []
        if element.get("element_type") == "table"
        and isinstance(element.get("table"), dict)
    ]

    print(f"OLD physical tables: {len(old_physical_tables)}")
    print(f"NEW physical tables: {len(new_physical_tables)}")



    assert layout_text("Waren- und") != layout_text("Warenund")
    assert layout_text("Beta-Faktor") != layout_text("BetaFaktor")
    assert selection_states(":selected:") != selection_states(":unselected:")
    assert alphanumeric_skeleton("Beta- Faktor") == alphanumeric_skeleton("BetaFaktor")

    # ---- cell 6: per-side anchor key maps ----

    old_anchor_keys = anchor_key_map(old_meta)
    new_anchor_keys = anchor_key_map(new_meta)



    old_tables, old_join_audit = make_logical_tables(
        "old",
        old_physical_tables,
        old_anchor_keys,
    )
    new_tables, new_join_audit = make_logical_tables(
        "new",
        new_physical_tables,
        new_anchor_keys,
    )

    print(f"OLD logical tables: {len(old_tables)}")
    print(f"NEW logical tables: {len(new_tables)}")
    print(
        "OLD continuation groups:",
        sum(table["source_fragment_count"] > 1 for table in old_tables),
    )
    print(
        "NEW continuation groups:",
        sum(table["source_fragment_count"] > 1 for table in new_tables),
    )



    matched_tables, unmatched_old_tables, unmatched_new_tables, matching_audit = (
        match_logical_tables(old_tables, new_tables)
    )

    print(f"Matched logical tables: {len(matched_tables)}")
    print(f"Unmatched OLD logical tables: {len(unmatched_old_tables)}")
    print(f"Unmatched NEW logical tables: {len(unmatched_new_tables)}")
    print(json.dumps(matching_audit, indent=2))



    table_matches = [
        compare_table_match(match, index)
        for index, match in enumerate(matched_tables, start=1)
    ]
    print("Compared matched logical tables.")



    # ---- cell 18 ----
    table_classifications = Counter(
        match["classification"]
        for match in table_matches
    )
    cell_classifications = Counter(
        change["classification"]
        for match in table_matches
        for change in match["cell_changes"]
    )



    # Residual tables that remain on both sides under the same provisional
    # context are not safe to finalize as add/delete yet.
    old_residual_by_context: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)
    new_residual_by_context: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for table in unmatched_old_tables:
        key = residual_context_key(table)
        if key:
            old_residual_by_context[key].append(table)

    for table in unmatched_new_tables:
        key = residual_context_key(table)
        if key:
            new_residual_by_context[key].append(table)

    unresolved_table_groups = []
    unresolved_old_ids: set[str] = set()
    unresolved_new_ids: set[str] = set()

    for group_index, context_key in enumerate(
        sorted(
            set(old_residual_by_context)
            .intersection(new_residual_by_context)
        ),
        start=1,
    ):
        old_group = old_residual_by_context[context_key]
        new_group = new_residual_by_context[context_key]

        unresolved_old_ids.update(
            table["logical_table_id"]
            for table in old_group
        )
        unresolved_new_ids.update(
            table["logical_table_id"]
            for table in new_group
        )

        unresolved_table_groups.append({
            "review_group_id":
                f"TABLE-REVIEW-{group_index:04}",
            "provisional_context_key": context_key,
            "context_is_authoritative_hierarchy": False,
            "reason":
                "same_provisional_context_residual_group_requires_review",
            "old_tables": [
                compact_logical_table(table)
                for table in old_group
            ],
            "new_tables": [
                compact_logical_table(table)
                for table in new_group
            ],
        })

    deleted_table_candidates = [
        table
        for table in unmatched_old_tables
        if table["logical_table_id"] not in unresolved_old_ids
    ]
    added_table_candidates = [
        table
        for table in unmatched_new_tables
        if table["logical_table_id"] not in unresolved_new_ids
    ]



    result = {
        "schema_version": "0.4.0",
        "comparison_stage":
            "logical_table_comparison_with_provisional_context",
        "source_files": {
            "old_structured": old_structured_path.name,
            "new_structured": new_structured_path.name,
            "movement": movement_path.name,
        },
        "old_document": movement_result.get("old_document"),
        "new_document": movement_result.get("new_document"),
        "processing": {
            "version": STEP10_VERSION,
            "physical_table_source":
                "document.elements[element_type=table]",
            "page_continuation_joining_performed": True,
            "page_continuation_evidence":
                "adjacent_page_same_columns_repeated_header_with_optional_provisional_context_support",
            "repeated_continuation_headers_omitted": True,
            "authoritative_hierarchy_used": False,
            "automatic_anchor_repairs_used": False,
            "table_matching":
                "exact_content_then_reciprocal_similarity_with_provisional_context_support",
            "row_matching":
                "order_preserving_exact_then_reciprocal_similarity",
            "automatic_equivalence_normalization":
                "conservative_whitespace_punctuation_spacing_only",
            "aggressive_artifact_normalization_used_for_automatic_equivalence":
                False,
            "aggressive_artifact_normalization_is_review_only":
                True,
            "provisional_context_difference_is_movement_proof":
                False,
            "absolute_page_or_reading_order_used_as_movement_proof":
                False,
            "final_add_delete_classification_performed":
                False,
            "final_document_merge_performed": False,
            "final_confidence_classification_performed": False,
        },
        "audit": {
            **matching_audit,
            "old_continuation_joins": old_join_audit,
            "new_continuation_joins": new_join_audit,
        },
        "statistics": {
            "old_physical_tables": len(old_physical_tables),
            "new_physical_tables": len(new_physical_tables),
            "old_logical_tables": len(old_tables),
            "new_logical_tables": len(new_tables),
            "matched_logical_tables": len(table_matches),
            "unchanged_tables":
                table_classifications["UNCHANGED"],
            "layout_equivalent_tables":
                table_classifications["LAYOUT_EQUIVALENT"],
            "extraction_artifact_review_tables":
                table_classifications["EXTRACTION_ARTIFACT_REVIEW"],
            "structure_changed_review_tables":
                table_classifications["STRUCTURE_CHANGED_REVIEW"],
            "modified_tables":
                table_classifications["MODIFIED"],
            "table_context_difference_review_matches":
                sum(
                    match.get("context_relation", {}).get("relation")
                    == "different_provisional_context"
                    for match in table_matches
                ),
            "tables_needing_review":
                sum(
                    match.get("needs_review", False)
                    for match in table_matches
                )
                + len(unresolved_table_groups),
            "unresolved_table_review_groups":
                len(unresolved_table_groups),
            "unresolved_old_logical_tables":
                len(unresolved_old_ids),
            "unresolved_new_logical_tables":
                len(unresolved_new_ids),
            "deleted_table_candidates":
                len(deleted_table_candidates),
            "added_table_candidates":
                len(added_table_candidates),
            "unchanged_cells":
                cell_classifications["UNCHANGED"],
            "layout_equivalent_cells":
                cell_classifications["LAYOUT_EQUIVALENT"],
            "extraction_artifact_review_cells":
                cell_classifications["EXTRACTION_ARTIFACT_REVIEW"],
            "structure_changed_review_cells":
                cell_classifications["STRUCTURE_CHANGED_REVIEW"],
            "modified_cells":
                cell_classifications["MODIFIED"],
            "added_cells":
                cell_classifications["ADDED"],
            "deleted_cells":
                cell_classifications["DELETED"],
            "omitted_old_repeated_header_rows":
                sum(
                    table["omitted_repeated_header_rows"]
                    for table in old_tables
                ),
            "omitted_new_repeated_header_rows":
                sum(
                    table["omitted_repeated_header_rows"]
                    for table in new_tables
                ),
            "omitted_old_repeated_header_cells":
                sum(
                    table["omitted_repeated_header_cells"]
                    for table in old_tables
                ),
            "omitted_new_repeated_header_cells":
                sum(
                    table["omitted_repeated_header_cells"]
                    for table in new_tables
                ),
            "matched_old_cells_accounted":
                sum(
                    match["cell_accounting"]["old_cells_accounted"]
                    for match in table_matches
                ),
            "matched_new_cells_accounted":
                sum(
                    match["cell_accounting"]["new_cells_accounted"]
                    for match in table_matches
                ),
        },
        "table_matches": table_matches,
        "unresolved_table_groups": unresolved_table_groups,
        "deleted_table_candidates": [
            compact_unmatched_table(table, "DELETED")
            for table in deleted_table_candidates
        ],
        "added_table_candidates": [
            compact_unmatched_table(table, "ADDED")
            for table in added_table_candidates
        ],
    }



    # Physical-table conservation.
    old_physical_ids = [
        table.get("element_id")
        for table in old_physical_tables
    ]
    new_physical_ids = [
        table.get("element_id")
        for table in new_physical_tables
    ]

    old_grouped_ids = [
        source_id
        for table in old_tables
        for source_id in table["source_table_ids"]
    ]
    new_grouped_ids = [
        source_id
        for table in new_tables
        for source_id in table["source_table_ids"]
    ]

    require(len(old_grouped_ids) == len(set(old_grouped_ids)) == len(old_physical_ids), "s10: OLD physical table conservation failed")
    require(len(new_grouped_ids) == len(set(new_grouped_ids)) == len(new_physical_ids), "s10: NEW physical table conservation failed")
    require(set(old_grouped_ids) == set(old_physical_ids), "s10: OLD physical table provenance failed")
    require(set(new_grouped_ids) == set(new_physical_ids), "s10: NEW physical table provenance failed")

    # Logical-table conservation.
    matched_old_logical_ids = [
        match["old"]["logical_table_id"]
        for match in table_matches
    ]
    matched_new_logical_ids = [
        match["new"]["logical_table_id"]
        for match in table_matches
    ]

    accounted_old = (
        matched_old_logical_ids
        + [
            table["logical_table_id"]
            for table in deleted_table_candidates
        ]
        + sorted(unresolved_old_ids)
    )
    accounted_new = (
        matched_new_logical_ids
        + [
            table["logical_table_id"]
            for table in added_table_candidates
        ]
        + sorted(unresolved_new_ids)
    )

    require(len(accounted_old) == len(set(accounted_old)) == len(old_tables), "s10: OLD logical table conservation failed")
    require(len(accounted_new) == len(set(accounted_new)) == len(new_tables), "s10: NEW logical table conservation failed")

    # Provenance must remain available for changed cells.
    require(all(
        cell.get("primary_page") is not None
        for match in table_matches
        for change in match["cell_changes"]
        for cell in (change.get("old"), change.get("new"))
        if cell is not None
    ), "s10: changed table cell is missing provenance page")

    # Current architecture invariants.
    require(result["processing"]["authoritative_hierarchy_used"] is False, "s10: authoritative hierarchy used")
    require(result["processing"]["automatic_anchor_repairs_used"] is False, "s10: automatic anchor repairs used")
    require(
        result["processing"][
            "provisional_context_difference_is_movement_proof"
        ]
        is False,
        "s10: provisional context treated as movement proof",
    )
    require(all(
        match.get("movement_classification") is None
        for match in table_matches
    ), "s10: table match has movement classification")

    print(json.dumps(result["statistics"], indent=2))
    print(
        "Physical/logical table conservation and "
        "non-authoritative-context invariants passed."
    )



    # ---- cell 20 ----
    changed_matches = [
        match
        for match in table_matches
        if match["classification"] != "UNCHANGED"
        or match.get("needs_review")
    ]

    print("=== CHANGED / REVIEW TABLE MATCHES ===")
    for match in changed_matches[:10]:
        print("=" * 90)
        print(
            match["table_match_id"],
            match["classification"],
            f"old_pages={match['old']['source_pages']}",
            f"new_pages={match['new']['source_pages']}",
            f"review={match['needs_review']}",
        )
        print("reasons:", match["review_reasons"])
        print(
            "context:",
            match.get("context_relation", {}).get("relation"),
        )
        for change in [
            item
            for item in match["cell_changes"]
            if item["classification"] != "UNCHANGED"
        ][:8]:
            print(
                " ",
                change["classification"],
                "|",
                repr((change.get("old") or {}).get("text")),
                "->",
                repr((change.get("new") or {}).get("text")),
            )

    print("\n=== UNRESOLVED TABLE REVIEW GROUPS ===")
    for group in result["unresolved_table_groups"][:10]:
        print(
            group["review_group_id"],
            "| context=",
            group["provisional_context_key"],
            "| old/new=",
            len(group["old_tables"]),
            "/",
            len(group["new_tables"]),
        )

    print("\n=== DELETED TABLE CANDIDATES ===")
    for table in result["deleted_table_candidates"][:10]:
        print(
            table["logical_table_id"],
            "| pages=",
            table["source_pages"],
            "| rows/cols=",
            table["row_count"],
            "/",
            table["column_count"],
        )

    print("\n=== ADDED TABLE CANDIDATES ===")
    for table in result["added_table_candidates"][:10]:
        print(
            table["logical_table_id"],
            "| pages=",
            table["source_pages"],
            "| rows/cols=",
            table["row_count"],
            "/",
            table["column_count"],
        )



    # ---- cell 22 ----
    save_json(result, table_result_path)
    require(table_result_path.exists(), "s10: table result was not saved")
    print(f"Saved: {table_result_path}")



if __name__ == "__main__":
    main()
