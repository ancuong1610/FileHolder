"""Direct ReportLab redline PDF.

The report shows each change/cell with its full source old/new strings. Common
context is left unstyled; only the actually different source spans are marked:

* removed/replaced source span -> strike-through + red,
* inserted/replaced span -> underline + red,
* possible line-wrap span -> subdued amber + explicit "Possible line wrap —
  not verified" label (never asserted as noise or as verified formatting).

Review statuses stay visible. Accepted is an engine status, not proof of human
review or verified truth. When stored offsets cannot be validated against source
text the diff is aligned conservatively and all differences are shown in red
with an explicit warning. Nothing here changes the canonical result.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.units import inch

from document_compare.phase1.reporting.annotations import annotate_change
from document_compare.phase1.reporting.report_model import (
    NO_DIFFERENCES_MESSAGE,
    build_report_model,
    plain_reasons,
)
from document_compare.phase1.reporting.spans import build_diff_spans
from document_compare.phase1.contracts.result_state import record_result_state

_RED = "#b00020"
_AMBER = "#8a6d00"
WRAP_LABEL = "Possible line wrap \u2014 not verified"
_LEGEND = (
    "strike + red = removed/replaced source text &middot; "
    "underline + red = inserted/replaced text &middot; "
    "amber possible wrap = Possible line wrap \u2014 not verified. "
    "Each item keeps its review status; accepted items are not treated as "
    "verified truth."
)
_STYLE = ParagraphStyle(
    "Redline", parent=getSampleStyleSheet()["BodyText"], fontSize=10,
    leading=14, alignment=TA_LEFT, spaceAfter=6)
_ID_STYLE = ParagraphStyle(
    "Id", parent=_STYLE, fontName="Helvetica-Bold", spaceAfter=2)
_LABEL_STYLE = ParagraphStyle(
    "Legend", parent=_STYLE, fontSize=8, leading=11, textColor="#555555")
_TITLE_STYLE = ParagraphStyle(
    "ReportTitle", parent=_STYLE, fontName="Helvetica-Bold", fontSize=16,
    leading=20, spaceAfter=6)
_SECTION_STYLE = ParagraphStyle(
    "Section", parent=_STYLE, fontName="Helvetica-Bold", fontSize=13,
    leading=17, spaceBefore=12, spaceAfter=6)
_GROUP_STYLE = ParagraphStyle(
    "Group", parent=_STYLE, fontName="Helvetica-Bold", fontSize=11,
    leading=14, spaceBefore=8, spaceAfter=3, textColor="#1a4d8f")


def _escape(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _para(markup: str, style: ParagraphStyle = _STYLE) -> Paragraph:
    return Paragraph(markup, style)


def _side_markup(text: str, spans: list[dict[str, Any]], *, inserted: bool) -> str:
    """Inline markup for one side, styling only the changed spans."""
    text = text if isinstance(text, str) else ""
    ordered = sorted(
        (
            span
            for span in spans
            if isinstance(span.get("start"), int)
            and isinstance(span.get("end"), int)
        ),
        key=lambda span: (span["start"], span["end"]),
    )
    parts: list[str] = []
    cursor = 0
    labelled = False
    for span in ordered:
        start, end = span["start"], span["end"]
        if start < cursor or end <= start or end > len(text):
            continue
        parts.append(_escape(text[cursor:start]))
        inner = _escape(text[start:end])
        if span.get("kind") == "wrap":
            parts.append(f'<font color="{_AMBER}">{inner}</font>')
            if not labelled:
                parts.append(
                    f'<font size="7" color="{_AMBER}"> [{_escape(WRAP_LABEL)}]</font>'
                )
                labelled = True
        elif inserted:
            parts.append(f'<u><font color="{_RED}">{inner}</font></u>')
        else:
            parts.append(f'<strike><font color="{_RED}">{inner}</font></strike>')
        cursor = end
    parts.append(_escape(text[cursor:]))
    return "".join(parts)


def _summary_line(spans: dict[str, Any], *, ambiguous: bool) -> str:
    if ambiguous:
        base = (
            "Content differences: not classified; Possible line-wrap "
            "differences: not classified (not verified)"
        )
    else:
        base = (
            f"Content differences: {spans['content_count']}; "
            f"Possible line-wrap differences: {spans['wrap_count']} (not verified)"
        )
    if spans.get("warning"):
        base = f"{base}. {spans['warning']}"
    elif ambiguous:
        base = (
            f"{base}. Operation grouping unavailable for this item; "
            "differences are shown in red."
        )
    return base


def _flow_markup(
    old_text: str, new_text: str, token_diff: Any
) -> dict[str, Any]:
    """Classify and style one change/cell without touching any input."""
    annotation = annotate_change(
        old_text, new_text, token_diff, source_old=old_text, source_new=new_text
    )
    classifications = [
        operation.get("classification")
        for operation in annotation.get("operations") or []
    ]
    spans = build_diff_spans(old_text, new_text, token_diff, classifications)
    return {
        "old": _side_markup(old_text, spans["old_spans"], inserted=False),
        "new": _side_markup(new_text, spans["new_spans"], inserted=True),
        "summary": _summary_line(
            spans, ambiguous=bool(annotation.get("ambiguous"))
        ),
        "reliable": spans["reliable"],
        "warning": spans["warning"],
        "content_count": spans["content_count"],
        "wrap_count": spans["wrap_count"],
    }


def _change_header(change: dict[str, Any]) -> str:
    cid = _escape(change.get("change_id", "?"))
    ctype = _escape(change.get("change_type", "CHANGED"))
    status = _escape(((change.get("resolution") or {}).get("status")) or "review")
    parts = [f"[{cid}] {ctype} ({status})"]
    old = change.get("old") or {}
    new = change.get("new") or {}
    old_page, new_page = old.get("primary_page"), new.get("primary_page")
    if old_page is not None or new_page is not None:
        parts.append(
            f"old p.{old_page if old_page is not None else '?'} / "
            f"new p.{new_page if new_page is not None else '?'}"
        )
    candidate = change.get("source_candidate_id")
    if candidate:
        parts.append(f"candidate {_escape(candidate)}")
    return " | ".join(parts)


def change_flow_markup(change: dict[str, Any]) -> dict[str, Any]:
    """Pure, testable markup for one change record (does not mutate ``change``)."""
    old_text = (change.get("old") or {}).get("text", "")
    new_text = (change.get("new") or {}).get("text", "")
    result = _flow_markup(old_text, new_text, change.get("token_diff"))
    result["header"] = _change_header(change)
    return result


def _proof_line(change: dict[str, Any]) -> str:
    resolution = change.get("resolution") or {}
    parts: list[str] = []
    status = resolution.get("status")
    if status:
        parts.append(f"status={_escape(status)}")
    if resolution.get("needs_review"):
        parts.append("needs_review=True")
    reasons = change.get("review_reasons")
    if isinstance(reasons, list) and reasons:
        parts.append(f"reasons={_escape('|'.join(str(r) for r in reasons[:3]))}")
    elif reasons:
        parts.append(f"reasons={_escape(str(reasons))}")
    evidence = change.get("evidence")
    if evidence:
        parts.append("evidence=yes")
    return " ".join(parts)


def _cell_ids(old_meta: dict[str, Any], new_meta: dict[str, Any]) -> str:
    bits: list[str] = []
    old_id = old_meta.get("cell_id")
    if old_id:
        page = old_meta.get("primary_page")
        bits.append(f"old {old_id}" + (f" p.{page}" if page is not None else ""))
    new_id = new_meta.get("cell_id")
    if new_id:
        page = new_meta.get("primary_page")
        bits.append(f"new {new_id}" + (f" p.{page}" if page is not None else ""))
    return " / ".join(bits)


def _table_pages(old_meta: dict[str, Any], new_meta: dict[str, Any]) -> str:
    old_pages = old_meta.get("source_pages") or []
    new_pages = new_meta.get("source_pages") or []
    if not old_pages and not new_pages:
        return ""
    return f"old pages {list(old_pages)} / new pages {list(new_pages)}"


def _entry_change(entry: dict[str, Any], change_id: str | None = None) -> dict[str, Any]:
    """Adapt one report entry to the established flow-markup input."""
    return {
        "change_id": change_id or entry.get("record_id") or entry.get("review_id"),
        "change_type": entry.get("change_type") or "CHANGED",
        "old": {
            "text": entry.get("before") or "",
            "comparison_text": entry.get("before") or "",
            "primary_page": entry.get("old_page"),
            "element_id": entry.get("old_element_id"),
        },
        "new": {
            "text": entry.get("after") or "",
            "comparison_text": entry.get("after") or "",
            "primary_page": entry.get("new_page"),
            "element_id": entry.get("new_element_id"),
        },
        "token_diff": entry.get("token_diff"),
        "resolution": entry.get("resolution") or {},
        "source_candidate_id": entry.get("source_candidate_id"),
        "similarity": entry.get("similarity"),
    }


def _evidence_lines(entry: dict[str, Any]) -> list[str]:
    evidence = entry.get("evidence") or {}
    lines = []
    for key in ("old_element_ids", "new_element_ids", "candidate_ids"):
        values = evidence.get(key) or []
        if values:
            lines.append(f"{key}: {', '.join(str(value) for value in values)}")
    for key in ("old_element_id", "new_element_id"):
        if evidence.get(key):
            lines.append(f"{key}: {evidence.get(key)}")
    if not lines:
        lines.append("No source ids were attached to this evidence-only case.")
    return lines


def _technical_details(entry: dict[str, Any]) -> list[str]:
    """Compact technical identifiers, kept out of the human summary."""
    resolution = entry.get("resolution") or {}
    record_id = entry.get("record_id") or entry.get("review_id") or "-"
    parts = [
        f"record={record_id}",
        f"kind={entry.get('kind') or '-'}",
        f"change_type={entry.get('change_type') or '-'}",
        f"result_state={entry.get('result_state') or '-'}",
        f"legacy_status={resolution.get('status') or '-'}",
    ]
    for key, label in (
        ("route", "route"),
        ("case_type", "case_type"),
        ("source_candidate_id", "source_candidate"),
    ):
        value = resolution.get(key) if key in {"route"} else entry.get(key)
        if value:
            parts.append(f"{label}={value}")
    if entry.get("similarity") is not None:
        parts.append(f"similarity={entry.get('similarity')}")
    if entry.get("old_element_id"):
        parts.append(f"old_element={entry.get('old_element_id')}")
    if entry.get("new_element_id"):
        parts.append(f"new_element={entry.get('new_element_id')}")
    lines = [" | ".join(_escape(part) for part in parts)]
    reasons = entry.get("reasons") or []
    if reasons:
        lines.append(
            "reason_codes=" + _escape("|".join(str(reason) for reason in reasons))
        )
    if entry.get("evidence"):
        lines.append(_escape("; ".join(_evidence_lines(entry))))
    return lines


def _human_reasons(entry: dict[str, Any]) -> list[str]:
    reasons = plain_reasons(entry.get("reasons") or [])
    return reasons or ["The available evidence does not support calling this a confirmed change."]


def _render_change(entry: dict[str, Any], story: list[Any], *, suffix: str = "") -> None:
    change = _entry_change(entry)
    flow = change_flow_markup(change)
    story.append(_para(flow["header"] + suffix, _ID_STYLE))
    # The labels say both "Before/After" and "old/new" so a reader recognises
    # the orientation while the established extraction contract stays stable.
    story.append(_para(f"Before \u2014 old: {flow['old']}", _STYLE))
    story.append(_para(f"After \u2014 new: {flow['new']}", _STYLE))
    if flow["content_count"] or flow["wrap_count"] or flow["warning"]:
        story.append(_para(flow["summary"], _STYLE))
    proof = _proof_line(change)
    if proof:
        story.append(_para(proof, _STYLE))


def _render_table_entry(entry: dict[str, Any], story: list[Any]) -> None:
    cid = entry.get("record_id") or "?"
    ctype = entry.get("change_type") or "CHANGED"
    classification = entry.get("classification") or ""
    old_meta = entry.get("old_table") or {}
    new_meta = entry.get("new_table") or {}
    dims = (
        f"rows {old_meta.get('row_count','?')} / columns {old_meta.get('column_count','?')} "
        f"-> rows {new_meta.get('row_count','?')} / columns {new_meta.get('column_count','?')}"
    )
    header = (
        f"[{_escape(cid)}] table {_escape(ctype)} "
        f"{_escape(classification)}"
    )
    pages = _table_pages(old_meta, new_meta)
    if pages:
        header += f" | {_escape(pages)}"
    story.append(_para(header, _ID_STYLE))
    story.append(_para(_escape(dims), _LABEL_STYLE))
    one_sided = ctype in {"TABLE_DELETED", "TABLE_ADDED"}
    table_dimensions = (
        (old_meta.get("row_count") or 0) * (old_meta.get("column_count") or 0)
        + (new_meta.get("row_count") or 0) * (new_meta.get("column_count") or 0)
    )
    content_unavailable = (
        one_sided
        and table_dimensions > 0
        and (
            entry.get("cell_content_status") == "unavailable"
            or not entry.get("cell_changes_present", True)
        )
    )
    if content_unavailable:
        story.append(_para(
            "Table cell content unavailable (legacy artifact); inspect source table.",
            _STYLE,
        ))
    shown = 0
    for cell in entry.get("cell_changes") or []:
        old_cell_meta = cell.get("old") or {}
        new_cell_meta = cell.get("new") or {}
        old_cell = old_cell_meta.get("text", "")
        new_cell = new_cell_meta.get("text", "")
        if (
            str(cell.get("classification") or "").upper() == "UNCHANGED"
            and old_cell == new_cell
        ):
            continue
        cell_classification = cell.get("classification", "")
        cell_reasons = cell.get("review_reasons") or []
        location_bits = []
        row_index = cell.get("old_row_index")
        if row_index is None:
            row_index = cell.get("new_row_index")
        if row_index is not None:
            location_bits.append(f"row {row_index}")
        if cell.get("column_index") is not None:
            location_bits.append(f"column {cell.get('column_index')}")
        if cell_classification:
            reason_mark = (
                f" reasons={'; '.join(str(r) for r in cell_reasons)}"
                if cell_reasons
                else ""
            )
            label = f"cell {_escape(cell_classification)}"
            ids = _cell_ids(old_cell_meta, new_cell_meta)
            if ids:
                label += f" | {_escape(ids)}"
            if location_bits:
                label += f" | {_escape(', '.join(location_bits))}"
            label += _escape(reason_mark)
            story.append(_para(label, _STYLE))
        flow = _flow_markup(old_cell, new_cell, cell.get("token_diff"))
        story.append(_para(f"old cell: {flow['old']}", _STYLE))
        story.append(_para(f"new cell: {flow['new']}", _STYLE))
        if flow["content_count"] or flow["wrap_count"] or flow["warning"]:
            story.append(_para(flow["summary"], _STYLE))
        shown += 1
    if not shown:
        story.append(_para(
            "No changed cell text is available; the table record remains listed.",
            _STYLE,
        ))
    proof = _proof_line({
        "resolution": entry.get("resolution") or {},
        "review_reasons": entry.get("reasons") or [],
    })
    if proof:
        story.append(_para(proof, _STYLE))


def _render_evidence_review(entry: dict[str, Any], story: list[Any]) -> None:
    """Evidence-only uncertainty: reasons and references, never a fake pair."""
    review_id = entry.get("review_id") or entry.get("record_id") or "review"
    case_type = entry.get("case_type") or "unresolved case"
    story.append(_para(
        f"[{_escape(review_id)}] uncertain case: {_escape(case_type)}", _ID_STYLE
    ))
    story.append(_para(
        "Why this remains uncertain: " + _escape(" ".join(_human_reasons(entry))),
        _STYLE,
    ))
    story.append(_para(
        _escape("Evidence references: " + "; ".join(_evidence_lines(entry))),
        _STYLE,
    ))


def _is_table_entry(entry: dict[str, Any]) -> bool:
    return "old_table" in entry or "new_table" in entry


def _render_entry(entry: dict[str, Any], story: list[Any]) -> None:
    if _is_table_entry(entry):
        _render_table_entry(entry, story)
    elif (
        entry.get("kind") == "review"
        and not entry.get("linked_kind")
        and not entry.get("before")
        and not entry.get("after")
    ):
        _render_evidence_review(entry, story)
    else:
        _render_change(entry, story)


def _summary_story(report: dict[str, Any]) -> list[Any]:
    story: list[Any] = [
        _para("Document comparison report", _TITLE_STYLE),
        _para(_LEGEND, _LABEL_STYLE),
        Spacer(1, 0.12 * inch),
    ]
    documents = report.get("documents") or {}
    for side, label in (("old", "Old document"), ("new", "New document")):
        identity = documents.get(side) or {}
        bits = [f"{label}: {identity.get('label') or 'Document'}"]
        if identity.get("page_count") is not None:
            bits.append(f"{identity.get('page_count')} page(s)")
        if identity.get("source_element_count") is not None:
            bits.append(f"{identity.get('source_element_count')} source element(s)")
        story.append(_para(_escape(" | ".join(bits)), _STYLE))
    summary = report.get("summary") or {}
    story.append(_para(
        _escape(
            f"Supported changes: {summary.get('supported_changes', 0)} element(s) "
            f"and {summary.get('supported_table_changes', 0)} table(s). "
            f"Uncertain differences: {summary.get('uncertain_items', 0)}. "
            f"Non-semantic layout/artifact events: "
            f"{summary.get('non_semantic_events', 0)}."
        ),
        _STYLE,
    ))
    if report.get("message"):
        story.append(_para(_escape(report["message"]), _STYLE))
    limitations = summary.get("limitations") or []
    if limitations:
        story.append(_para("Limitations", _ID_STYLE))
        for limitation in limitations:
            story.append(_para("\u2022 " + _escape(limitation), _STYLE))
    return story


def _section_story(
    title: str,
    report: dict[str, Any],
    *,
    supported: bool,
) -> list[Any]:
    story: list[Any] = [_para(title, _SECTION_STYLE)]
    wrote = False
    for group in report.get("groups") or []:
        if supported:
            entries = list(group.get("supported") or []) + list(
                group.get("tables") or []
            )
        else:
            entries = list(group.get("uncertain") or [])
        if not entries:
            continue
        wrote = True
        story.append(_para(
            _escape(group.get("title") or "Unassigned content"), _GROUP_STYLE
        ))
        for entry in entries:
            _render_entry(entry, story)
            story.append(Spacer(1, 0.08 * inch))
    if not wrote and supported:
        story.append(_para(
            "No supported element or table changes.", _STYLE
        ))
    return story


def _appendix_story(report: dict[str, Any]) -> list[Any]:
    entries = (
        list(report.get("supported") or [])
        + list(report.get("supported_tables") or [])
        + list(report.get("uncertain") or [])
    )
    if not entries:
        return []
    story: list[Any] = [_para("Technical appendix", _SECTION_STYLE)]
    for entry in entries:
        for line in _technical_details(entry):
            story.append(_para(line, _LABEL_STYLE))
        story.append(Spacer(1, 0.05 * inch))
    return story


def render_pdf(
    comparison: dict[str, Any],
    output_path: Path | str,
    old_document: dict[str, Any] | None = None,
    new_document: dict[str, Any] | None = None,
) -> Path:
    """Render the complete human report (supported, uncertain, appendix).

    ``old_document``/``new_document`` are optional so existing callers keep the
    two-argument contract. They only add section identities, page labels and
    grouping; the canonical comparison remains the sole source of changes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_report_model(
        comparison,
        old_document=old_document,
        new_document=new_document,
    )
    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        rightMargin=0.75 * inch, leftMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )
    story: list[Any] = []
    story.extend(_summary_story(report))
    story.extend(_section_story("Supported changes", report, supported=True))
    story.extend(_section_story("Uncertain differences", report, supported=False))
    story.extend(_appendix_story(report))
    doc.build(story)
    return output_path
