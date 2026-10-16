"""Stage 05: reconstruct provisional anchors and comparison text.

Input  : ``<root>/output/old_document_normalized.json`` and
         ``new_document_normalized.json`` (Step-4 normalized models).

Work   : Detects a repeated left-margin marker cohort, then creates
         provisional anchor records from DI/lexical heading evidence and
         attaches to every element a rolling window of recent anchor IDs.
         Detected margin markers are stripped from ``comparison_text``.
         No authoritative hierarchy is inferred.

Output : ``<root>/output/old_document_structured.json`` and
         ``new_document_structured.json`` plus console summaries.

Review : Elements that look like margin markers are rejected as
         ``marker_like_heading_needs_review``; weak numbered prefixes stay
         non-anchors. ``context_is_authoritative_hierarchy`` stays
         ``False`` everywhere so later stages cannot treat the context as
         hierarchy.

The module is import-safe: importing it only defines the pure anchor
helpers and never resolves a workspace, reads files, prints, or mutates the
environment. File orchestration lives in :func:`main`, which the
``__name__ == "__main__"`` guard runs for the CLI stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.structure.furniture import classify_derived_furniture
from document_compare.phase1.structure.reconstruction import (
    STRUCTURE_EVIDENCE_VERSION,
    build_structure_evidence,
    validate_structure_evidence,
)
from document_compare.phase1.contracts.validation import require

# ---- imports and configuration constants ----

import copy
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

STEP5_VERSION = "4.1.0-generic-structural-anchors"
INPUT_SCHEMA_VERSION = "0.2.0"
OUTPUT_SCHEMA_VERSION = "0.3.0"

RECENT_ANCHOR_WINDOW = 6

STRUCTURAL_ELEMENT_TYPES = {"heading", "title"}
LAYOUT_ELEMENT_TYPES = {"header", "footer", "page_number"}

# Generic margin-marker morphology, e.g. M8, VC2, C9.
MARKER_PATTERN = re.compile(
    r"^(?=.{1,12}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+$"
)
MARKER_MARGIN_RATIO = 0.16
MIN_MARKER_CANDIDATES = 5
MIN_MARKER_PAGES = 3

# Generic leading structural labels (no legal/domain vocabulary):
# Arabic "1", dotted "1.1"/"1.2.3", Roman "I", alphabetic "A".
# A separator ('.', ')') or whitespace may follow the label; standalone
# Roman/alphabetic labels are also permitted.
GENERIC_LABEL = re.compile(
    r"^\s*(?P<label>"
    r"\d+(?:\.\d+)*"
    r"|[IVXLCDM]{1,7}"
    r"|[A-Z]"
    r")(?P<tail>(?:[.)]\s*|\s+)(?P<rest>\S.*)?)?$"
)

# Guarded fallback limits: only short, heading-like lines may become anchors.
MAX_NUMBERED_HEADING_WORDS = 6
MAX_NUMBERED_HEADING_CHARS = 100

KEY_WHITESPACE = re.compile(r"\s+")

KEY_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
})


# ---- workspace configuration ----
def document_config(project_root: Path) -> dict[str, dict[str, Path]]:
    """Per-document input/output paths for one pipeline workspace root."""
    output_dir = project_root / "output"
    return {
        "old": {
            "input": output_dir / "old_document_normalized.json",
            "output": output_dir / "old_document_structured.json",
        },
        "new": {
            "input": output_dir / "new_document_normalized.json",
            "output": output_dir / "new_document_structured.json",
        },
    }


# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
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
    if not isinstance(document, dict):
        raise ValueError("Expected model['document'].")
    elements = document.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError("Expected non-empty document.elements.")
    return document


def validate_step4_model(model: dict[str, Any], path: Path) -> None:
    if model.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueError(
            f"Expected schema {INPUT_SCHEMA_VERSION!r}; "
            f"got {model.get('schema_version')!r} in {path}."
        )

    document = document_from(model)
    processing = model.get("processing") or {}
    internal = processing.get("internal_schema") or {}
    normalization = processing.get("normalization") or {}

    if internal.get("version") != "3.0.0":
        raise ValueError(
            "Step 5 requires the neutral Step-3 v3 evidence model."
        )

    if not normalization:
        raise ValueError("Step 5 requires Step-4 normalized input.")

    for element in document["elements"]:
        if not isinstance(element.get("normalized_text"), str):
            raise ValueError(
                f"Missing normalized_text for {element.get('element_id')}."
            )
        if not isinstance(element.get("reading_order"), int):
            raise ValueError(
                f"Missing reading_order for {element.get('element_id')}."
            )


# ---- canonical keys and margin-marker cohort ----
def canonical_key_text(text: str) -> str:
    value = unicodedata.normalize("NFC", text)
    value = value.translate(KEY_TRANSLATION)
    value = KEY_WHITESPACE.sub(" ", value)
    return value.strip().casefold()


def page_width_map(document: dict[str, Any]) -> dict[int, float]:
    result = {}
    for page in document.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_no = page.get("page_number")
        width = page.get("width")
        if isinstance(page_no, int) and isinstance(width, (int, float)):
            result[page_no] = float(width)
    return result


def left_margin_ratio(
    element: dict[str, Any],
    widths: dict[int, float],
) -> float | None:
    polygon = element.get("primary_polygon")
    page = element.get("primary_page")
    width = widths.get(page)

    if (
        not isinstance(polygon, list)
        or not isinstance(width, (int, float))
        or width <= 0
    ):
        return None

    xs = polygon[0::2]
    if not xs or not all(isinstance(x, (int, float)) for x in xs):
        return None

    return min(float(x) for x in xs) / width


def detect_repeated_margin_marker_cohort(
    document: dict[str, Any],
) -> tuple[set[str], set[str], dict[str, Any]]:
    widths = page_width_map(document)
    candidates = []

    for element in document["elements"]:
        if not element.get("comparison_eligible"):
            continue
        if element.get("element_type") == "table":
            continue

        text = str(
            element.get("normalized_text")
            or element.get("text")
            or ""
        ).strip()

        ratio = left_margin_ratio(element, widths)

        if (
            MARKER_PATTERN.fullmatch(text)
            and ratio is not None
            and ratio <= MARKER_MARGIN_RATIO
        ):
            candidates.append(element)

    pages = {
        element.get("primary_page")
        for element in candidates
        if element.get("primary_page") is not None
    }

    enabled = (
        len(candidates) >= MIN_MARKER_CANDIDATES
        and len(pages) >= MIN_MARKER_PAGES
    )

    marker_ids = (
        {str(element["element_id"]) for element in candidates}
        if enabled else set()
    )

    marker_texts = (
        {
            str(
                element.get("normalized_text")
                or element.get("text")
                or ""
            ).strip()
            for element in candidates
        }
        if enabled else set()
    )
    marker_texts.discard("")

    profile = {
        "enabled": enabled,
        "candidate_count": len(candidates),
        "distinct_pages": len(pages),
        "pattern": MARKER_PATTERN.pattern,
        "margin_ratio_max": MARKER_MARGIN_RATIO,
        "marker_text_count": len(marker_texts),
    }

    return marker_ids, marker_texts, profile


# ---- lexical label and anchor evidence ----
def lexical_label_metadata(text: str) -> dict[str, str] | None:
    match = GENERIC_LABEL.match(text)
    if not match:
        return None

    label = match.group("label")
    tail = match.group("tail") or ""
    rest = match.group("rest") or ""

    if re.fullmatch(r"\d+(?:\.\d+)*", label):
        kind = "arabic"
    elif re.fullmatch(r"[IVXLCDM]{1,7}", label):
        kind = "roman"
    else:
        kind = "alphabetic"

    separator = tail[:1] if tail.startswith((".", ")")) else ""

    return {
        "kind": kind,
        "label": label + separator,
        "separator": separator,
        "rest": rest,
        "key": f"number:{canonical_key_text(label + separator)}",
    }


def _valid_polygon(polygon: Any) -> bool:
    return (
        isinstance(polygon, list)
        and len(polygon) >= 6
        and len(polygon) % 2 == 0
        and all(isinstance(value, (int, float)) for value in polygon)
    )


def has_valid_geometry(element: dict[str, Any]) -> bool:
    if _valid_polygon(element.get("primary_polygon")):
        return True
    for region in element.get("bounding_regions") or []:
        if isinstance(region, dict) and _valid_polygon(region.get("polygon")):
            return True
    return False


def anchor_evidence(
    element: dict[str, Any],
    marker_ids: set[str],
    furniture_ids: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not element.get("comparison_eligible"):
        return None, "comparison_ineligible_or_layout_element"

    if element.get("element_type") in LAYOUT_ELEMENT_TYPES:
        return None, "comparison_ineligible_or_layout_element"

    if element.get("element_type") == "table":
        return None, "no_anchor_evidence"

    if str(element.get("element_id")) in marker_ids:
        return None, "marker_like_heading_needs_review"

    if furniture_ids and str(element.get("element_id")) in furniture_ids:
        # Recurring page furniture derived from combined evidence is not a
        # provisional anchor even when a single DI role labels it a heading.
        # Raw roles, text and eligibility are untouched.
        return None, "derived_recurring_furniture_needs_review"

    text = str(
        element.get("normalized_text")
        or element.get("text")
        or ""
    ).strip()

    if not text:
        return None, "no_anchor_evidence"

    role = element.get("original_role")
    element_type = element.get("element_type")
    di_heading = (
        role in {"title", "sectionHeading"}
        or element_type in STRUCTURAL_ELEMENT_TYPES
    )

    # DI role / neutral structural heading: strongest provisional evidence,
    # independent of any leading label.
    if di_heading:
        return {
            "anchor_type": "di_heading",
            "confidence": (
                "high" if role in {"title", "sectionHeading"} else "medium"
            ),
            "anchor_key": f"heading:{canonical_key_text(text)}",
        }, None

    # Guarded generic leading-label fallback (Arabic, dotted, Roman,
    # alphabetic). Promoted only when short, heading-like, and geometric.
    metadata = lexical_label_metadata(text)
    if metadata is None:
        return None, "no_anchor_evidence"

    rest = metadata["rest"]

    # Roman/alphabetic labels with following title text require explicit
    # heading punctuation so ordinary uppercase prose is not promoted.
    if (
        metadata["kind"] in {"roman", "alphabetic"}
        and rest
        and not metadata["separator"]
    ):
        return None, "weak_numbered_prefix"

    # Arabic/dotted numeric labels must introduce title text; standalone
    # digits are not approved heading patterns.
    if metadata["kind"] == "arabic" and not rest:
        return None, "weak_numbered_prefix"

    # Non-standalone labels must begin with an uppercase heading-like word.
    if rest and not (rest[0].isupper() and rest[0].isalpha()):
        return None, "weak_numbered_prefix"

    if (
        len(text.split()) > MAX_NUMBERED_HEADING_WORDS
        or len(text) > MAX_NUMBERED_HEADING_CHARS
    ):
        return None, "weak_numbered_prefix"

    if not has_valid_geometry(element):
        return None, "numbered_prefix_missing_geometry"

    return {
        "anchor_type": "numbered_heading",
        "confidence": "low",
        "anchor_key": f"heading:{canonical_key_text(text)}",
    }, None


def strip_detected_marker_prefix(
    text: str,
    marker_texts: set[str],
) -> tuple[str, str | None]:
    value = text.strip()

    if not value or not marker_texts:
        return value, None

    # Longest known detected marker first.
    for marker in sorted(marker_texts, key=len, reverse=True):
        if not marker:
            continue

        prefix = marker + " "
        if value.startswith(prefix):
            stripped = value[len(prefix):].lstrip()
            if stripped:
                return stripped, marker

    return value, None


# ---- provisional context reconstruction ----
def reconstruct_provisional_context(
    source_model: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(source_model)
    result["schema_version"] = OUTPUT_SCHEMA_VERSION

    document = document_from(result)
    elements = document["elements"]

    marker_ids, marker_texts, marker_profile = (
        detect_repeated_margin_marker_cohort(document)
    )

    # Classify recurring page furniture exactly once from combined evidence
    # (recurrence + supporting layout-role cohort + page-edge geometry) and
    # reuse it for both anchor rejection and the additive structure evidence.
    derived_furniture = classify_derived_furniture(document)
    furniture_ids = set(derived_furniture["by_element"])

    anchors = []
    rejected = Counter()

    # First pass: create anchor records.
    for element in elements:
        evidence, rejected_reason = anchor_evidence(
            element,
            marker_ids,
            furniture_ids,
        )

        if evidence is None:
            if rejected_reason:
                rejected[rejected_reason] += 1
            continue

        anchor_id = (
            f"{document.get('document_id')}-"
            f"A{len(anchors) + 1:05d}"
        )

        anchor = {
            "anchor_id": anchor_id,
            "element_id": element["element_id"],
            "reading_order": element.get("reading_order"),
            "primary_page": element.get("primary_page"),
            "anchor_type": evidence["anchor_type"],
            "confidence": evidence["confidence"],
            "anchor_key": evidence["anchor_key"],
            "text": element.get("text"),
            "normalized_text": element.get("normalized_text"),
            "context_is_authoritative_hierarchy": False,
        }
        anchors.append(anchor)

    anchor_by_order = {
        int(anchor["reading_order"]): anchor
        for anchor in anchors
        if isinstance(anchor.get("reading_order"), int)
    }

    # Second pass: each element receives a rolling window of recent
    # provisional anchors. No hierarchy is inferred.
    recent_anchor_ids: list[str] = []
    marker_prefix_removed = 0

    for element in elements:
        reading_order = int(element["reading_order"])

        # Anchor element itself becomes current before context is attached.
        anchor_here = anchor_by_order.get(reading_order)
        if anchor_here is not None:
            recent_anchor_ids.append(anchor_here["anchor_id"])
            recent_anchor_ids = recent_anchor_ids[-RECENT_ANCHOR_WINDOW:]

        nearest_id = (
            recent_anchor_ids[-1]
            if recent_anchor_ids
            else None
        )

        nearest_anchor = None
        if nearest_id is not None:
            # Anchor count is small enough for a one-time map.
            nearest_anchor = next(
                (
                    anchor
                    for anchor in anchors
                    if anchor["anchor_id"] == nearest_id
                ),
                None,
            )

        element["provisional_anchor_context"] = {
            "nearest_anchor_id": nearest_id,
            "nearest_anchor_confidence": (
                nearest_anchor.get("confidence")
                if nearest_anchor else None
            ),
            "recent_anchor_ids": list(recent_anchor_ids),
            "context_is_authoritative_hierarchy": False,
        }

        base_text = str(
            element.get("normalized_text")
            or element.get("text")
            or ""
        )

        comparison_text, stripped_marker = (
            strip_detected_marker_prefix(
                base_text,
                marker_texts,
            )
        )

        element["comparison_text"] = comparison_text
        element["comparison_text_flags"] = (
            ["detected_margin_marker_prefix_removed"]
            if stripped_marker else []
        )
        element["comparison_marker_prefix_removed"] = stripped_marker

        if stripped_marker:
            marker_prefix_removed += 1

    document["provisional_anchors"] = anchors

    statistics = document.setdefault("statistics", {})
    statistics["provisional_anchors"] = len(anchors)
    statistics["comparison_text_marker_prefixes_removed"] = (
        marker_prefix_removed
    )
    statistics["provisional_anchor_rejected_evidence"] = dict(
        sorted(rejected.items())
    )

    processing = result.setdefault("processing", {})
    processing["section_reconstruction"] = {
        "version": STEP5_VERSION,
        "strategy": "provisional_anchor_evidence",
        "authoritative_hierarchy_created": False,
        "provisional_context_only": True,
        "recent_anchor_window": RECENT_ANCHOR_WINDOW,
        "comparison_text_created": True,
        "comparison_text_marker_prefix_filtering": True,
        "margin_marker_profile": marker_profile,
    }

    # Additive, versioned TOC evidence layer. It never mutates raw evidence,
    # eligibility, order or table accounting, and it does not create an
    # authoritative hierarchy; the strategy above is preserved unchanged.
    structure_evidence = build_structure_evidence(
        document,
        derived_furniture=derived_furniture,
    )
    document["structure_evidence"] = structure_evidence
    statistics["structure_evidence_version"] = STRUCTURE_EVIDENCE_VERSION
    statistics["structure_evidence_toc_entries"] = structure_evidence["toc"]["entry_count"]
    statistics["structure_evidence_linked_entries"] = structure_evidence["toc"]["linked_count"]
    statistics["structure_evidence_sections"] = len(structure_evidence["sections"])
    processing["section_reconstruction"]["structure_evidence_version"] = (
        STRUCTURE_EVIDENCE_VERSION
    )
    processing["section_reconstruction"]["structure_evidence_additive"] = True
    processing["section_reconstruction"]["structure_evidence_authoritative"] = False

    return result


# ---- structured-model validation ----
def validate_structured_model(
    source: dict[str, Any],
    result: dict[str, Any],
) -> None:
    source_document = document_from(source)
    result_document = document_from(result)

    require(
        result.get("schema_version") == OUTPUT_SCHEMA_VERSION,
        "s05: structured output schema_version mismatch",
    )

    reconstruction = (
        (result.get("processing") or {})
        .get("section_reconstruction") or {}
    )

    require(
        reconstruction.get("strategy") == "provisional_anchor_evidence",
        "s05: unexpected section reconstruction strategy",
    )
    require(
        reconstruction.get("authoritative_hierarchy_created") is False,
        "s05: step 5 must not create an authoritative hierarchy",
    )
    require(
        reconstruction.get("comparison_text_created") is True,
        "s05: step 5 must create comparison text",
    )

    before = source_document["elements"]
    after = result_document["elements"]

    require(
        len(before) == len(after) > 0,
        "s05: source and structured element counts must be equal and non-zero",
    )

    immutable_fields = (
        "element_id",
        "element_type",
        "text",
        "normalized_text",
        "spans",
        "bounding_regions",
        "primary_page",
        "primary_polygon",
        "reading_order",
        "comparison_eligible",
        "exclusion_reasons",
        "extraction_validation",
        "represented_by_table_id",
        "table",
    )

    for old, new in zip(before, after):
        for field in immutable_fields:
            if old.get(field) != new.get(field):
                raise ValueError(
                    f"s05: changed immutable field {field!r} for "
                    f"{old.get('element_id')}"
                )

        context = new.get("provisional_anchor_context")
        if not isinstance(context, dict):
            raise ValueError(
                f"s05: provisional_anchor_context is not a mapping for "
                f"{new.get('element_id')}"
            )
        if not isinstance(context.get("recent_anchor_ids"), list):
            raise ValueError(
                f"s05: recent_anchor_ids is not a list for {new.get('element_id')}"
            )
        if context.get("context_is_authoritative_hierarchy") is not False:
            raise ValueError(
                f"s05: provisional context was marked authoritative for "
                f"{new.get('element_id')}"
            )

        if not isinstance(new.get("comparison_text"), str):
            raise ValueError(
                f"s05: comparison_text is not a string for {new.get('element_id')}"
            )
        if not isinstance(new.get("comparison_text_flags"), list):
            raise ValueError(
                f"s05: comparison_text_flags is not a list for {new.get('element_id')}"
            )

    anchors = result_document.get("provisional_anchors")
    if not isinstance(anchors, list):
        raise ValueError("s05: provisional_anchors is not a list")

    anchor_ids = [a["anchor_id"] for a in anchors]
    require(
        len(anchor_ids) == len(set(anchor_ids)),
        "s05: duplicate anchor_id in provisional anchors",
    )

    valid_anchor_ids = set(anchor_ids)

    for element in after:
        context = element["provisional_anchor_context"]
        for anchor_id in context["recent_anchor_ids"]:
            if anchor_id not in valid_anchor_ids:
                raise ValueError(
                    f"s05: element {element.get('element_id')} references unknown "
                    f"anchor_id {anchor_id!r}"
                )

    structure_evidence = result_document.get("structure_evidence")
    if not isinstance(structure_evidence, dict):
        raise ValueError("s05: structure_evidence layer is missing")
    if structure_evidence.get("version") != STRUCTURE_EVIDENCE_VERSION:
        raise ValueError("s05: unexpected structure evidence version")
    validate_structure_evidence(result_document, structure_evidence)

    print(
        f"Passed: {len(after):,} elements, "
        f"{len(anchors):,} provisional anchors."
    )


# ---- stage entry point ----
def main() -> None:
    """Run Stage 05 against the workspace and save both structured models."""
    project_root = find_project_root()
    documents = document_config(project_root)

    print("Step 5 version:", STEP5_VERSION)
    for label, paths in documents.items():
        print(f"{label.upper()}: {paths['input']} -> {paths['output']}")

    source_models = {
        label: load_json(paths["input"])
        for label, paths in documents.items()
    }

    for label, model in source_models.items():
        validate_step4_model(model, documents[label]["input"])
        print(
            f"{label.upper()}: "
            f"{len(document_from(model)['elements']):,} elements loaded"
        )

    structured_models = {
        label: reconstruct_provisional_context(model)
        for label, model in source_models.items()
    }

    for label in documents:
        print(label.upper())
        validate_structured_model(
            source_models[label],
            structured_models[label],
        )

    for label, paths in documents.items():
        save_json(
            structured_models[label],
            paths["output"],
        )

        document = document_from(structured_models[label])
        anchors = document.get("provisional_anchors") or []
        rejected = (
            document.get("statistics", {})
            .get("provisional_anchor_rejected_evidence", {})
        )

        print()
        print(
            f"{document.get('document_id')}: "
            f"{len(anchors):,} provisional anchors"
        )

        for anchor in anchors[:15]:
            print({
                "id": anchor["anchor_id"],
                "page": anchor.get("primary_page"),
                "type": anchor["anchor_type"],
                "confidence": anchor["confidence"],
                "text": str(anchor.get("normalized_text") or "")[:180],
                "key": anchor["anchor_key"],
            })

        print("Rejected evidence:", rejected)
        print("Saved:", paths["output"])


if __name__ == "__main__":
    main()