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

from crr_compare.diff_annotations import annotate_change
from crr_compare.render_spans import build_diff_spans

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


def render_pdf(comparison: dict[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_path), pagesize=letter,
                            rightMargin=0.75*inch, leftMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    story: list[Any] = [
        _para("Document Comparison Redline", _ID_STYLE),
        _para(_LEGEND, _LABEL_STYLE),
        Spacer(1, 0.2*inch),
    ]

    for change in comparison.get("changes", []):
        flow = change_flow_markup(change)
        story.append(_para(flow["header"], _ID_STYLE))
        story.append(_para(f"old: {flow['old']}", _STYLE))
        story.append(_para(f"new: {flow['new']}", _STYLE))
        if flow["content_count"] or flow["wrap_count"] or flow["warning"]:
            story.append(_para(flow["summary"], _STYLE))
        proof = _proof_line(change)
        if proof:
            story.append(_para(proof, _STYLE))
        story.append(Spacer(1, 0.1*inch))

    for table in comparison.get("table_changes", []):
        cid = table.get("table_change_id", "?")
        ctype = table.get("change_type", "CHANGED")
        classification = table.get("classification", "")
        old_meta = table.get("old") or {}
        new_meta = table.get("new") or {}
        dims = (
            f"old={old_meta.get('row_count','?')}x{old_meta.get('column_count','?')} "
            f"new={new_meta.get('row_count','?')}x{new_meta.get('column_count','?')}"
        )
        header = (
            f"[{_escape(cid)}] table {_escape(ctype)} "
            f"{_escape(classification)} {_escape(dims)}"
        )
        pages = _table_pages(old_meta, new_meta)
        if pages:
            header += f" | {_escape(pages)}"
        story.append(_para(header, _ID_STYLE))
        for cell in table.get("cell_changes", []):
            old_cell_meta = cell.get("old") or {}
            new_cell_meta = cell.get("new") or {}
            old_cell = old_cell_meta.get("text", "")
            new_cell = new_cell_meta.get("text", "")
            cell_classification = cell.get("classification", "")
            cell_reasons = cell.get("review_reasons") or []
            if cell_classification:
                reason_mark = (
                    f" reasons={'; '.join(str(r) for r in cell_reasons)}"
                    if cell_reasons
                    else ""
                )
                # Structural/cell-kind warning stays separate and visible, even
                # when every text difference is a possible wrap.
                label = f"cell {_escape(cell_classification)}"
                ids = _cell_ids(old_cell_meta, new_cell_meta)
                if ids:
                    label += f" | {_escape(ids)}"
                label += _escape(reason_mark)
                story.append(_para(label, _STYLE))
            flow = _flow_markup(old_cell, new_cell, cell.get("token_diff"))
            story.append(_para(f"old cell: {flow['old']}", _STYLE))
            story.append(_para(f"new cell: {flow['new']}", _STYLE))
            if flow["content_count"] or flow["wrap_count"] or flow["warning"]:
                story.append(_para(flow["summary"], _STYLE))
        proof = _proof_line(table)
        if proof:
            story.append(_para(proof, _STYLE))
        story.append(Spacer(1, 0.1*inch))

    for item in comparison.get("review_queue", []):
        review_status = (item.get("resolution") or {}).get("status", "review")
        story.append(_para(f"[{_escape(item.get('review_id', '?'))}] review: {_escape(review_status)}", _STYLE))

    doc.build(story)
    return output_path
