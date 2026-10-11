"""Stage 04: normalize element and table-cell text for comparison.

Input  : ``<root>/output/old_document.json`` and ``new_document.json``
         (Step-3 neutral internal models, schema 0.2.0).

Work   : Applies the conservative, idempotent rule set to every element and
         table-cell text: Unicode NFC, soft-hyphen removal, quote/dash
         canonicalization, true end-of-line word-wrap joining (lowercase
         continuation only, suspended-hyphen stopwords excluded), and
         whitespace collapse. Raw ``text`` and all Step-3 evidence are never
         rewritten.

Output : ``<root>/output/old_document_normalized.json`` and
         ``new_document_normalized.json`` plus console statistics.

Review : Ambigous boundaries (uppercase continuation, digit/reference
         continuation, same-line ``- ``) keep the visible hyphen so they
         stay reviewable downstream; ``normalization_flags`` record exactly
         what changed.

The module is import-safe: importing it only defines the pure normalization
helpers and never resolves a workspace, reads files, prints, or mutates the
environment. File orchestration lives in :func:`main`, which the
``__name__ == "__main__"`` guard runs for the CLI stage runner.
"""
from crr_compare.common import find_project_root, get_page_number, set_page_number
from crr_compare.lang_profile import SUSPENDED_HYPHEN_STOPWORDS
from crr_compare.validation import require

# ---- imports and configuration constants ----

import copy
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

NORMALIZATION_VERSION = "1.5"

QUOTE_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u00ab": '"', "\u00bb": '"',
})
DASH_TRANSLATION = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
})

LINE_BREAK_HYPHEN = re.compile(r"(\w+)-[ \t]*\n[ \t]*(\w+)")

WHITESPACE = re.compile(r"\s+")


# ---- workspace configuration ----
def document_config(project_root: Path) -> dict[str, dict[str, Path]]:
    """Per-document input/output paths for one pipeline workspace root."""
    output_dir = project_root / "output"
    return {
        "old": {
            "input": output_dir / "old_document.json",
            "output": output_dir / "old_document_normalized.json",
        },
        "new": {
            "input": output_dir / "new_document.json",
            "output": output_dir / "new_document_normalized.json",
        },
    }


# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def get_document(model: dict[str, Any]) -> dict[str, Any]:
    document = model.get("document")
    if not isinstance(document, dict):
        raise ValueError("Incompatible input: expected model['document'] from Step 3.")
    elements = document.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError(
            "Incompatible or empty input: expected non-empty "
            "model['document']['elements']."
        )
    return document


# ---- normalization rules ----
def normalize_text_with_flags(text: str) -> tuple[str, list[str]]:
    """Conservative, idempotent normalization for arbitrary documents.

    Mandatory rule set only: Unicode NFC, soft-hyphen (U+00AD) removal,
    quote/dash canonicalization, true end-of-line word-wrap joining, and
    whitespace collapse. Flattened hyphenation, semantic hyphenation,
    single-letter splits, and list-marker punctuation are never rewritten.
    """
    flags: list[str] = []

    value = unicodedata.normalize("NFC", text)
    if value != text:
        flags.append("unicode_nfc_normalized")

    if "\u00ad" in value:
        value = value.replace("\u00ad", "")
        flags.append("soft_hyphen_removed")

    if "\u00a0" in value:
        value = value.replace("\u00a0", " ")
        flags.append("non_breaking_space_normalized")

    # Normalize line endings first so a CRLF/CR wrap is still a true wrap.
    normalized_line_endings = value.replace("\r\n", "\n").replace("\r", "\n")
    if normalized_line_endings != value:
        flags.append("line_endings_normalized")
    value = normalized_line_endings

    translated = value.translate(QUOTE_TRANSLATION).translate(DASH_TRANSLATION)
    if translated != value:
        flags.append("quote_or_dash_variant_standardized")
    value = translated

    # True end-of-line word-wrap joining: a hyphen immediately before a line
    # break between word characters whose continuation begins lowercase. This
    # never touches a semantic hyphen ("risk-based"), which is not followed by
    # a newline, and it leaves an uppercase continuation ("Kredit-\nInstitut")
    # or a suspended compound hyphen ("Risiko-\nund ...") as a visible
    # "hyphen + space" so the boundary stays reviewable rather than silently
    # joined.
    line_break_joined = False
    def _replace_line_break_hyphen(match: re.Match) -> str:
        nonlocal line_break_joined
        left, right = match.group(1), match.group(2)
        # Join only a clear end-of-line word wrap: the continuation must begin
        # lowercase. An uppercase continuation (e.g. the German compound
        # "Kredit-\nInstitut"), a digit/reference continuation, or a suspended
        # function word keeps the visible hyphen so the boundary stays
        # reviewable instead of being silently joined.
        if right[:1].islower() and right.casefold() not in SUSPENDED_HYPHEN_STOPWORDS:
            line_break_joined = True
            return f"{left}{right}"
        return f"{left}- {right}"
    value = LINE_BREAK_HYPHEN.sub(_replace_line_break_hyphen, value)
    if line_break_joined:
        flags.append("line_break_hyphenation_joined")

    collapsed = WHITESPACE.sub(" ", value)
    if collapsed != value:
        flags.append("whitespace_collapsed")
    value = collapsed.strip()

    return value, list(dict.fromkeys(flags))


def normalize_text(text: str) -> str:
    return normalize_text_with_flags(text)[0]


def normalize_document(source_model: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(source_model)
    document = get_document(result)
    elements = document["elements"]

    normalized_cells = 0
    changed_elements = 0

    for element in elements:
        original_text = element.get("text")
        if not isinstance(original_text, str):
            raise TypeError(f"Element {element.get('element_id')} has non-string text.")

        normalized_text, flags = normalize_text_with_flags(original_text)
        element["normalized_text"] = normalized_text
        element["normalization_flags"] = flags

        if normalized_text != original_text:
            changed_elements += 1

        table = element.get("table")
        if table is not None:
            if element.get("element_type") != "table" or not isinstance(table, dict):
                raise ValueError(
                    f"Element {element.get('element_id')} has an invalid table payload."
                )

            for cell in table.get("cells") or []:
                cell_text = cell.get("text")
                if not isinstance(cell_text, str):
                    raise TypeError(f"Table cell {cell.get('cell_id')} has non-string text.")

                normalized_cell_text, cell_flags = normalize_text_with_flags(cell_text)
                cell["normalized_text"] = normalized_cell_text
                cell["normalization_flags"] = cell_flags

                normalized_cells += 1

    processing = result.setdefault("processing", {})
    processing["normalization"] = {
        "version": NORMALIZATION_VERSION,
        "unicode_form": "NFC",
        "soft_hyphens_removed": True,
        "line_break_hyphenation_joined": True,
        "flattened_hyphenation_repair": False,
        "single_letter_split_repair": False,
        "leading_list_marker_rewrite": False,
        "normalization_flags_recorded": True,
        "quote_variants_standardized": True,
        "dash_variants_standardized": True,
        "whitespace_collapsed": True,
        "case_changed": False,
        "numbers_changed": False,
        "punctuation_removed": False,
    }

    statistics = document.setdefault("statistics", {})
    statistics["normalized_elements"] = len(elements)
    statistics["normalized_table_cells"] = normalized_cells
    statistics["elements_changed_by_normalization"] = changed_elements

    return result


# ---- console summary ----
def show_changes(model: dict[str, Any], limit: int = 25) -> None:
    elements = get_document(model)["elements"]
    changed = [
        element for element in elements
        if element["text"] != element["normalized_text"]
    ]

    print(f"Elements changed by normalization: {len(changed):,}")

    for element in changed[:limit]:
        print("=" * 90)
        print(
            f"{element['element_id']} type={element['element_type']} "
            f"page={element.get('primary_page')}"
        )
        print("FLAGS     :", element.get("normalization_flags", []))
        print("ORIGINAL  :", repr(element["text"][:500]))
        print("NORMALIZED:", repr(element["normalized_text"][:500]))

    if len(changed) > limit:
        print(f"... {len(changed) - limit:,} additional changes omitted")


# ---- normalization validation ----
def validate_normalized_model(
    source: dict[str, Any],
    normalized: dict[str, Any],
) -> None:
    source_document = get_document(source)
    normalized_document = get_document(normalized)
    source_elements = source_document["elements"]
    normalized_elements = normalized_document["elements"]

    require(
        len(source_elements) == len(normalized_elements) > 0,
        "s04: source and normalized element counts must be equal and non-zero",
    )
    require(
        source.get("schema_version") == normalized.get("schema_version"),
        "s04: normalized schema_version differs from source",
    )
    require(
        normalized["processing"]["normalization"]["version"] == NORMALIZATION_VERSION,
        "s04: normalization processing version mismatch",
    )

    immutable_fields = (
        "element_id", "element_type", "text", "spans", "bounding_regions",
        "primary_page", "primary_polygon", "reading_order", "section_path",
        "structure_heading", "comparison_eligible", "exclusion_reasons",
        "extraction_validation", "represented_by_table_id",
    )

    normalized_cell_count = 0

    for before, after in zip(source_elements, normalized_elements):
        for field in immutable_fields:
            if before.get(field) != after.get(field):
                raise ValueError(
                    f"s04: changed immutable field {field!r} for "
                    f"{before.get('element_id')}"
                )

        if not isinstance(after.get("normalized_text"), str):
            raise ValueError(
                f"s04: normalized_text is not a string for {after.get('element_id')}"
            )
        if not isinstance(after.get("normalization_flags"), list):
            raise ValueError(
                f"s04: normalization_flags is not a list for {after.get('element_id')}"
            )

        # Idempotence: running normalization again must not change normalized text.
        if normalize_text(after["normalized_text"]) != after["normalized_text"]:
            raise ValueError(
                f"s04: normalization is not idempotent for {after.get('element_id')}"
            )

        before_table = before.get("table")
        after_table = after.get("table")

        if before_table is None:
            if after_table is not None:
                raise ValueError(
                    f"s04: normalization introduced a table for {after.get('element_id')}"
                )
            continue

        if before_table.get("row_count") != after_table.get("row_count"):
            raise ValueError(
                f"s04: table row_count changed for {after.get('element_id')}"
            )
        if before_table.get("column_count") != after_table.get("column_count"):
            raise ValueError(
                f"s04: table column_count changed for {after.get('element_id')}"
            )

        before_cells = before_table.get("cells") or []
        after_cells = after_table.get("cells") or []
        if len(before_cells) != len(after_cells):
            raise ValueError(
                f"s04: table cell count changed for {after.get('element_id')}"
            )

        for cell_index, (before_cell, after_cell) in enumerate(zip(before_cells, after_cells)):
            for cell_field in ("text", "spans", "bounding_regions"):
                if before_cell.get(cell_field) != after_cell.get(cell_field):
                    raise ValueError(
                        f"s04: changed table cell field {cell_field!r} for "
                        f"{after.get('element_id')} cell {cell_index}"
                    )
            if not isinstance(after_cell.get("normalized_text"), str):
                raise ValueError(
                    f"s04: table cell normalized_text is not a string for "
                    f"{after.get('element_id')} cell {cell_index}"
                )
            if not isinstance(after_cell.get("normalization_flags"), list):
                raise ValueError(
                    f"s04: table cell normalization_flags is not a list for "
                    f"{after.get('element_id')} cell {cell_index}"
                )
            if normalize_text(after_cell["normalized_text"]) != after_cell["normalized_text"]:
                raise ValueError(
                    f"s04: table cell normalization is not idempotent for "
                    f"{after.get('element_id')} cell {cell_index}"
                )
            normalized_cell_count += 1

    stats = normalized_document["statistics"]
    require(
        stats["normalized_elements"] == len(normalized_elements),
        "s04: statistics.normalized_elements does not match the element count",
    )
    require(
        stats["normalized_table_cells"] == normalized_cell_count,
        "s04: statistics.normalized_table_cells does not match the cell count",
    )
    require(
        [e["reading_order"] for e in normalized_elements] == list(range(len(normalized_elements))),
        "s04: reading_order is not the complete ordered range",
    )

    print(
        f"Passed: {len(normalized_elements):,} elements and "
        f"{normalized_cell_count:,} table cells; raw evidence and Step-3 decisions unchanged."
    )


# ---- stage entry point ----
def main() -> None:
    """Run Stage 04 against the workspace and save both normalized models."""
    project_root = find_project_root()
    documents = document_config(project_root)

    for label, paths in documents.items():
        if not paths["input"].exists():
            raise FileNotFoundError(
                f"Missing {paths['input']}. Run the verified Step-3 notebook first."
            )

    print(f"Project root: {project_root}")
    for label, paths in documents.items():
        print(f"{label}: {paths['input']}")

    source_models = {
        label: load_json(paths["input"])
        for label, paths in documents.items()
    }

    for label, model in source_models.items():
        document = get_document(model)
        print(f"{label}: loaded {len(document['elements']):,} Step-3 elements")

    normalized_models = {
        label: normalize_document(model)
        for label, model in source_models.items()
    }

    for label, model in normalized_models.items():
        stats = model["document"]["statistics"]
        print(
            f"{label}: normalized {stats['normalized_elements']:,} elements and "
            f"{stats['normalized_table_cells']:,} table cells"
        )
        print(f"  changed elements: {stats['elements_changed_by_normalization']:,}")

    for label, model in normalized_models.items():
        print(f"\n{label.upper()} — ALL NORMALIZATION CHANGES")
        show_changes(model)

    for label in documents:
        print(label.upper())
        validate_normalized_model(source_models[label], normalized_models[label])

    for label, paths in documents.items():
        save_json(normalized_models[label], paths["output"])
        if not paths["output"].exists():
            raise ValueError(f"s04: expected saved normalized document at {paths['output']}")
        print(f"Saved: {paths['output']}")


if __name__ == "__main__":
    main()