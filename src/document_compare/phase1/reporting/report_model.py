"""One human-facing report model for Stage 15 HTML and PDF.

The model is a pure projection over the canonical result plus the saved Stage-5
structured documents. It never re-runs matching, rewrites source strings, or
turns uncertainty into accepted truth. HTML and PDF renderers consume this same
model so counts, grouping and uncertainty identity cannot diverge.

Grouping uses supported section/outline titles when available, then physical
page groups. Inferred titles remain hints. Evidence-only uncertainty is shown
without an invented old/new pair.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from document_compare.phase1.contracts.result_state import (
    UNCERTAIN,
    canonical_uncertain_records,
    record_result_state,
)
from document_compare.phase1.contracts.semantic_assessment import (
    is_deterministic_material_proof,
    normalize_assessment,
)

REPORT_MODEL_VERSION = "1.0.0"
NO_DIFFERENCES_MESSAGE = "No differences were found between the two documents."

# User-facing explanations for deterministic uncertainty reason codes. Unknown
# codes still become readable words; the raw code stays in technical details.
_PLAIN_REASONS = {
    "ambiguous_modified_pair": (
        "The old and new passages resemble each other, but the pairing is ambiguous."
    ),
    "contiguous_block_relative_order_relocation": (
        "A contiguous block appears to have moved relative to the surrounding text."
    ),
    "duplicate_exact_assignment_ambiguous": (
        "Identical text occurs more than once, so the correct pairing is ambiguous."
    ),
    "duplicate_exact_residual": (
        "Identical text remains on both sides, so this match cannot be confirmed."
    ),
    "beyond_aligned_coverage_frontier": (
        "This passage lies beyond the reliably aligned part of the documents."
    ),
    "merged_cell_or_header_kind_changed": (
        "Table cell structure or header type changed, so cell alignment is uncertain."
    ),
    "source_extraction_requires_review": (
        "The source extraction itself is flagged for review, so equality cannot be concluded."
    ),
    "source_extraction_validation_unknown": (
        "Source extraction validation is incomplete, so equality cannot be concluded."
    ),
    "hyphenation_wrap_unproven_source": (
        "A difference looks like a line-wrap hyphenation, but no source line break proves it."
    ),
    "possible_marker_only_difference": (
        "This may be a marker or version identifier rather than a substantive change."
    ),
    "unresolved_table_group": (
        "The table rows or columns could not be aligned reliably."
    ),
    "one_sided_table_candidate": (
        "Only one side has a matching table, so the correspondence is uncertain."
    ),
}


def plain_reason(reason: Any) -> str:
    """Readable explanation for one uncertainty reason code."""
    text = str(reason or "").strip()
    if not text:
        return ""
    mapped = _PLAIN_REASONS.get(text)
    if mapped:
        return mapped
    return text.replace("_", " ").strip().capitalize() + "."


def plain_reasons(reasons: Any) -> list[str]:
    """Deduplicated readable reasons, preserving source order."""
    values = reasons if isinstance(reasons, (list, tuple)) else []
    return list(dict.fromkeys(
        plain_reason(reason) for reason in values if str(reason or "").strip()
    ))


def _document(document: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    value = document.get("document")
    return value if isinstance(value, dict) else document


def document_identity(document: dict[str, Any] | None) -> dict[str, Any]:
    doc = _document(document)
    validation = doc.get("validation_summary") or {}
    basename = validation.get("source_pdf_file")
    if isinstance(basename, str) and basename:
        # Basenames only; never expose a server path in the report.
        basename = basename.replace("\\", "/").rsplit("/", 1)[-1]
    else:
        basename = None
    return {
        "document_id": doc.get("document_id"),
        "label": basename or doc.get("document_id") or "Document",
        "page_count": doc.get("page_count"),
        "source_element_count": doc.get("source_element_count")
        or (doc.get("statistics") or {}).get("elements"),
        "extraction_requires_review": bool(validation.get("requires_review")),
        "extraction_flags": list(validation.get("document_flags") or []),
    }


def _element_text(side: dict[str, Any] | None) -> str:
    if not isinstance(side, dict):
        return ""
    text = side.get("text")
    return str(text) if isinstance(text, str) else ""


def _semantic_fields(
    record: dict[str, Any],
    *,
    old_element: dict[str, Any] | None = None,
    new_element: dict[str, Any] | None = None,
    source_state: str,
) -> dict[str, Any]:
    """Expose assessment axes without claiming that source text is proof.

    Legacy/supported records without an assessment read ``not_assessed``; they
    are never relabelled as semantically verified.
    """
    assessment = normalize_assessment(
        record.get("semantic_assessment"),
        old_element=old_element or None,
        new_element=new_element or None,
    )
    basis = assessment.get("basis")
    relation = assessment.get("relation")
    correspondence = assessment.get("correspondence")
    material_proof = is_deterministic_material_proof(assessment)
    if material_proof:
        notice = "Source-bound deterministic material-change witness."
    elif source_state == "supported":
        notice = (
            "Source-supported text only; material meaning was not assessed "
            "as a semantic proof."
        )
    elif correspondence in {"proposed", "unresolved"}:
        notice = (
            "Possible counterpart evidence only; old and new passages are "
            "unpaired and are not a verified pair."
        )
    else:
        notice = "Material meaning remains undetermined."
    return {
        "semantic_assessment": assessment,
        "source_correspondence": correspondence,
        "material_relation": relation,
        "material_basis": basis,
        "material_proof": material_proof,
        "assessment_notice": notice,
    }


def _unpaired_sources(
    evidence: dict[str, Any],
    changes_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """All group member source passages, separated by side and pages.

    The report model never pairs these passages.  A proposal group is one
    uncertainty explanation, but every member's raw passage remains visible.
    """
    old: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []
    for change_id in evidence.get("change_ids") or []:
        change = changes_by_id.get(change_id)
        if not isinstance(change, dict):
            continue
        for side, bucket in (("old", old), ("new", new)):
            element = change.get(side)
            text = _element_text(element)
            if not text:
                continue
            bucket.append({
                "change_id": change.get("change_id"),
                "source_candidate_id": change.get("source_candidate_id"),
                "element_id": (element or {}).get("element_id"),
                "page": (element or {}).get("primary_page"),
                "text": text,
            })
    return {
        "old": old,
        "new": new,
        "paired": False,
        "notice": (
            "Old and new passages are listed separately as unpaired proposal "
            "evidence, not as a fabricated paired change."
        ),
    }


def _side_section_map(document: dict[str, Any] | None) -> dict[str, str]:
    doc = _document(document)
    evidence = doc.get("structure_evidence") or {}
    sections = {
        str(section.get("section_id")): str(section.get("title") or "")
        for section in evidence.get("sections") or []
        if isinstance(section, dict)
    }
    membership = (evidence.get("membership") or {}).get("by_element") or {}
    return {
        str(element_id): sections.get(str(record.get("section_id")), "")
        for element_id, record in membership.items()
        if isinstance(record, dict) and record.get("section_id")
    }


def _section_for_side(
    element_ids: list[Any], document: dict[str, Any] | None
) -> str | None:
    if not element_ids:
        return None
    sections = _side_section_map(document)
    for element_id in element_ids:
        if not isinstance(element_id, str):
            continue
        title = sections.get(element_id)
        if title:
            return title
    return None


def _section_for(
    old_element_ids: list[Any],
    new_element_ids: list[Any],
    old_document: dict[str, Any] | None,
    new_document: dict[str, Any] | None,
) -> str | None:
    """Resolve ids against their owning document only.

    Source element/table ids are local to one document, so the same string can
    name different content on the old and new side. Old-side ids never consult
    the new map (and vice versa); a paired record prefers its old orientation.
    """
    return _section_for_side(old_element_ids, old_document) or _section_for_side(
        new_element_ids, new_document
    )


def _group(page: Any, title: str | None) -> tuple[str, str]:
    if title:
        return f"section:{title.casefold()}", title
    if isinstance(page, int) and not isinstance(page, bool):
        return f"page:{page}", f"Page {page}"
    return "unassigned", "Unassigned content"


def _side_element_ids(record: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    sides: tuple[list[Any], list[Any]] = ([], [])
    for index, side in enumerate(("old", "new")):
        value = record.get(side)
        if not isinstance(value, dict):
            continue
        if value.get("element_id"):
            sides[index].append(value.get("element_id"))
        for key in ("source_table_ids", "table_ids"):
            for item in value.get(key) or []:
                sides[index].append(item)
        if value.get("logical_table_id"):
            sides[index].append(value.get("logical_table_id"))
    return sides


def _change_entry(
    record: dict[str, Any],
    *,
    old_document: dict[str, Any] | None,
    new_document: dict[str, Any] | None,
) -> dict[str, Any]:
    old = record.get("old") if isinstance(record.get("old"), dict) else {}
    new = record.get("new") if isinstance(record.get("new"), dict) else {}
    old_page = old.get("primary_page")
    new_page = new.get("primary_page")
    section = _section_for(
        [old.get("element_id")],
        [new.get("element_id")],
        old_document,
        new_document,
    )
    group_key, group_title = _group(
        old_page if isinstance(old_page, int) else new_page, section
    )
    resolution = record.get("resolution") or {}
    return {
        "kind": "change",
        "record_id": record.get("change_id"),
        "change_type": record.get("change_type"),
        "result_state": record_result_state(record, kind="change"),
        "group_key": group_key,
        "group_title": group_title,
        "before": _element_text(old),
        "after": _element_text(new),
        "old_page": old_page,
        "new_page": new_page,
        "old_element_id": old.get("element_id"),
        "new_element_id": new.get("element_id"),
        "resolution": resolution,
        "reasons": list(resolution.get("review_reasons") or []),
        "case_type": resolution.get("case_type"),
        "similarity": record.get("similarity"),
        "source_candidate_id": record.get("source_candidate_id"),
        "token_diff": record.get("token_diff"),
        "cell_changes": [],
        "evidence": None,
        "unpaired_sources": None,
        "linked_kind": None,
        "linked_record_id": None,
        **_semantic_fields(
            record,
            old_element=old,
            new_element=new,
            source_state=record_result_state(record, kind="change"),
        ),
    }


def _table_entry(
    record: dict[str, Any],
    *,
    old_document: dict[str, Any] | None,
    new_document: dict[str, Any] | None,
) -> dict[str, Any]:
    old = record.get("old") if isinstance(record.get("old"), dict) else {}
    new = record.get("new") if isinstance(record.get("new"), dict) else {}
    old_pages = list(old.get("source_pages") or old.get("pages") or [])
    new_pages = list(new.get("source_pages") or new.get("pages") or [])
    old_page = next((item for item in old_pages if isinstance(item, int)), None)
    new_page = next((item for item in new_pages if isinstance(item, int)), None)
    page = old_page if old_page is not None else new_page
    old_ids, new_ids = _side_element_ids(record)
    section = _section_for(old_ids, new_ids, old_document, new_document)
    group_key, group_title = _group(page, section)
    resolution = record.get("resolution") or {}
    return {
        "kind": "table",
        "record_id": record.get("table_change_id"),
        "change_type": record.get("change_type"),
        "classification": record.get("classification"),
        "result_state": record_result_state(record, kind="table"),
        "group_key": group_key,
        "group_title": group_title,
        "before": "",
        "after": "",
        "old_page": old_page,
        "new_page": new_page,
        "old_element_id": None,
        "new_element_id": None,
        "resolution": resolution,
        "reasons": list(record.get("review_reasons") or resolution.get("review_reasons") or []),
        "case_type": resolution.get("case_type"),
        "similarity": None,
        "source_candidate_id": record.get("source_table_match_id"),
        "token_diff": None,
        "old_table": old,
        "new_table": new,
        "cell_changes": list(record.get("cell_changes") or []),
        "cell_changes_present": isinstance(record.get("cell_changes"), list),
        "cell_content_status": record.get("cell_content_status"),
        "evidence": None,
        "unpaired_sources": None,
        "linked_kind": None,
        "linked_record_id": None,
        **_semantic_fields(
            record,
            old_element=old,
            new_element=new,
            source_state=record_result_state(record, kind="table"),
        ),
    }


def _review_entry(
    item: dict[str, Any],
    *,
    linked: dict[str, Any] | None,
    old_document: dict[str, Any] | None,
    new_document: dict[str, Any] | None,
    changes_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review = item.get("record") or {}
    evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    linked_kind = None
    linked_id = None
    if isinstance(linked, dict):
        if linked.get("change_id"):
            linked_kind, linked_id = "change", linked.get("change_id")
        elif linked.get("table_change_id"):
            linked_kind, linked_id = "table", linked.get("table_change_id")
    if linked_kind == "change":
        base = _change_entry(linked, old_document=old_document,
                             new_document=new_document)
    elif linked_kind == "table":
        base = _table_entry(linked, old_document=old_document,
                            new_document=new_document)
    else:
        old_element_ids = list(evidence.get("old_element_ids") or [])
        new_element_ids = list(evidence.get("new_element_ids") or [])
        if evidence.get("old_element_id"):
            old_element_ids.append(evidence.get("old_element_id"))
        if evidence.get("new_element_id"):
            new_element_ids.append(evidence.get("new_element_id"))
        section = _section_for(
            old_element_ids, new_element_ids, old_document, new_document
        )
        group_key, group_title = _group(None, section)
        base = {
            "kind": "review",
            "record_id": review.get("review_id"),
            "change_type": None,
            "result_state": UNCERTAIN,
            "group_key": group_key,
            "group_title": group_title,
            "before": "",
            "after": "",
            "old_page": None,
            "new_page": None,
            "old_element_id": None,
            "new_element_id": None,
            "resolution": review.get("resolution") or {},
            "reasons": list(
                (review.get("resolution") or {}).get("review_reasons")
                or evidence.get("review_reasons")
                or []
            ),
            "case_type": review.get("case_type"),
            "similarity": evidence.get("similarity"),
            "source_candidate_id": review.get("source_candidate_id"),
            "token_diff": None,
            "cell_changes": [],
            "evidence": evidence,
            "linked_kind": None,
            "linked_record_id": None,
        }
    base.update({
        "kind": "review",
        "review_id": review.get("review_id"),
        "source_kind": review.get("source_kind"),
        "case_type": review.get("case_type") or base.get("case_type"),
        "result_state": UNCERTAIN,
        "source_candidate_id": review.get("source_candidate_id"),
        "linked_kind": linked_kind,
        "linked_record_id": linked_id,
        "evidence": evidence,
        "resolution": review.get("resolution") or {},
        "unpaired_sources": (
            _unpaired_sources(evidence, changes_by_id or {})
            if evidence.get("change_ids") else base.get("unpaired_sources")
        ),
        "reasons": list(
            (review.get("resolution") or {}).get("review_reasons")
            or evidence.get("review_reasons")
            or base.get("reasons")
            or []
        ),
    })
    if "semantic_assessment" not in base:
        base.update(_semantic_fields(
            review, old_element=None, new_element=None, source_state=UNCERTAIN
        ))
    if evidence.get("correspondence") in {"proposed", "unresolved"}:
        base["source_correspondence"] = evidence["correspondence"]
        base["assessment_notice"] = (
            "Possible counterpart evidence only; old and new passages are "
            "unpaired and are not a verified pair."
        )
    return base


def _extraction_limitations(
    old_document: dict[str, Any] | None,
    new_document: dict[str, Any] | None,
) -> list[str]:
    limitations: list[str] = []
    for side, document in (("Old", old_document), ("New", new_document)):
        doc = _document(document)
        validation = doc.get("validation_summary") or {}
        if not validation:
            limitations.append(f"{side} document extraction evidence is unavailable.")
            continue
        if validation.get("requires_review"):
            limitations.append(
                f"{side} document extraction requires review; equality cannot be concluded."
            )
        elif validation.get("document_flags"):
            limitations.append(
                f"{side} document extraction has {len(validation['document_flags'])} warning flag(s)."
            )
    return limitations


def _no_differences_message(summary: dict[str, Any], limitations: list[str]) -> str | None:
    if summary["supported_changes"] or summary["supported_table_changes"]:
        return None
    if summary["uncertain_items"]:
        return (
            "No supported differences were found; "
            f"{summary['uncertain_items']} uncertain item(s) remain to be checked."
        )
    if summary["non_semantic_events"]:
        return (
            "No supported differences were found; "
            f"{summary['non_semantic_events']} non-semantic layout/artifact event(s) were recorded."
        )
    if limitations:
        return (
            "No supported differences were found, but extraction limitations mean "
            "equality cannot be concluded."
        )
    return NO_DIFFERENCES_MESSAGE


def build_report_model(
    comparison: dict[str, Any],
    *,
    old_document: dict[str, Any] | None = None,
    new_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    comparison = comparison if isinstance(comparison, dict) else {}
    changes = [item for item in comparison.get("changes") or [] if isinstance(item, dict)]
    tables = [item for item in comparison.get("table_changes") or [] if isinstance(item, dict)]
    reviews = [item for item in comparison.get("review_queue") or [] if isinstance(item, dict)]
    non_semantic = [
        item for item in comparison.get("non_semantic_events") or [] if isinstance(item, dict)
    ]

    supported = [
        _change_entry(item, old_document=old_document, new_document=new_document)
        for item in changes
        if record_result_state(item, kind="change") == "supported"
    ]
    supported_tables = [
        _table_entry(item, old_document=old_document, new_document=new_document)
        for item in tables
        if record_result_state(item, kind="table") == "supported"
    ]
    changes_by_id = {
        item.get("change_id"): item
        for item in changes
        if isinstance(item.get("change_id"), str)
    }
    uncertain = [
        _review_entry(
            item,
            linked=item.get("linked"),
            old_document=old_document,
            new_document=new_document,
            changes_by_id=changes_by_id,
        )
        for item in canonical_uncertain_records(changes, tables, reviews)
    ]

    limitations = _extraction_limitations(old_document, new_document)
    if not old_document and not new_document:
        # A deliberately empty comparison carries no document context to
        # assess; do not invent extraction limitations for it.
        limitations = []
    elif not old_document or not new_document:
        limitations.append("Saved structured section context is incomplete.")
    summary = {
        "supported_changes": len(supported),
        "supported_table_changes": len(supported_tables),
        "uncertain_items": len(uncertain),
        "non_semantic_events": len(non_semantic),
        "result_state_counts": {
            "supported": len(supported) + len(supported_tables),
            "uncertain": len(uncertain),
            "non_semantic": len(non_semantic),
        },
        "limitations": list(dict.fromkeys(limitations)),
    }
    message = _no_differences_message(summary, summary["limitations"])
    if message:
        summary["message"] = message

    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for entry in supported + supported_tables + uncertain:
        key = entry["group_key"]
        group = groups.setdefault(
            key, {"key": key, "title": entry["group_title"], "supported": [], "tables": [], "uncertain": []}
        )
        if entry["kind"] == "table":
            group["tables"].append(entry)
        elif entry["kind"] == "review":
            group["uncertain"].append(entry)
        else:
            group["supported"].append(entry)

    return {
        "schema_version": REPORT_MODEL_VERSION,
        "documents": {
            "old": document_identity(old_document),
            "new": document_identity(new_document),
        },
        "summary": summary,
        "groups": list(groups.values()),
        "supported": supported,
        "supported_tables": supported_tables,
        "uncertain": uncertain,
        "non_semantic_events": non_semantic,
        "message": message,
    }
