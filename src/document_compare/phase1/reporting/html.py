"""Inline HTML redline renderer (self-contained, print-to-PDF friendly).

Renders the OLD document in reading order with inline change markers:
  <del>old</del><ins>new</ins> for MODIFIED / RESEGMENTATION pairs,
  struck-through blocks for DELETED, inserted blocks for ADDED (placed after
  the nearest preceding matched old position), and changed-table blocks.
Each change carries a comment box: id, type, status and proof.
"""
from __future__ import annotations

import bisect
import html
import json
from pathlib import Path

from document_compare.shared.paths import find_project_root
from document_compare.phase1.reporting.annotations import annotate_change
from document_compare.phase1.reporting.report_model import (
    build_report_model,
    plain_reasons,
)
from document_compare.phase1.contracts.result_state import record_result_state

MAX_INLINE_CHARS = 220

_CSS = """
body{font-family:Georgia,'Times New Roman',serif;margin:0;background:#f4f1ec;color:#222}
header.bar{background:#1f2d3d;color:#fff;padding:14px 28px}
header.bar h1{margin:0;font-size:20px}header.bar p{margin:4px 0 0;font-size:12px;opacity:.85}
main{max-width:1240px;margin:18px auto 60px;background:#fff;padding:34px 40px;box-shadow:0 1px 6px rgba(0,0,0,.15)}
.bucket{font-size:15px;font-weight:bold;color:#1a4d8f;border-bottom:2px solid #1a4d8f;margin:26px 0 10px;padding-bottom:3px}
.row{display:grid;grid-template-columns:minmax(0,2fr) minmax(240px,1fr);gap:18px;align-items:start;margin:8px 0}
.flow p.el{margin:0;line-height:1.6;font-size:14.5px}
p.el.solo{margin:7px 0;line-height:1.55;font-size:14.5px}
p.el.heading{font-weight:bold}
del{color:#c00000;text-decoration:line-through}
ins{color:#c00000;text-decoration:underline}
.cmark{font-family:Menlo,Consolas,monospace;font-size:10.5px;color:#666;background:#f0f0f0;
 border-radius:8px;padding:1px 7px;margin-left:8px;white-space:nowrap}
aside.note{border-top:1.5px solid #333;border-bottom:1px solid #bbb;padding:6px 2px;font-size:11.5px;color:#333}
aside.note h5{margin:0 0 5px;font-size:12px;font-weight:bold;text-decoration:underline;color:#111}
aside.note table{border-collapse:collapse;width:100%}
aside.note td{padding:1px 8px 1px 0;font-size:11px;vertical-align:top}
td.k{color:#777;white-space:nowrap}
details.comment summary{cursor:pointer;color:#1a4d8f;font-weight:bold;font-size:11px}
.tableblock{border:1px solid #ccc;border-radius:4px;margin:12px 0;padding:10px 14px;background:#fbfbf8;font-size:13px}
.tableblock h4{margin:0 0 8px;font-size:12.5px;color:#333}
.cellrow{margin:3px 0;line-height:1.45}
.summary{border:1px solid #d5d0c7;border-radius:6px;padding:14px 18px;background:#faf8f3;margin:8px 0 18px}
.summary h2{margin:0 0 8px;font-size:16px;color:#1f2d3d}
.summary .documents{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:0 0 10px}
.summary .doc{border-left:3px solid #1a4d8f;padding-left:10px}
.summary .doc b{display:block;font-size:14px}
.summary .counts{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}
.summary .count{background:#fff;border:1px solid #ccc;border-radius:12px;padding:2px 10px;font-size:12px}
.summary .limitations{margin:8px 0 0;padding-left:18px;font-size:12px;color:#6a4c00}
.diffcard{border:1px solid #d8d3ca;border-radius:5px;margin:12px 0;padding:12px 16px;background:#fffdf9}
.diffcard h3{margin:0 0 8px;font-size:14px;color:#1f2d3d}
.diffcard.uncertain{border-left:4px solid #8a6d00}
.diffcard.supported{border-left:4px solid #1a4d8f}
.beforeafter{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.beforeafter .side{border-top:2px solid #ccc;padding-top:6px}
.beforeafter .side b{font-size:11.5px;color:#555;text-transform:uppercase;letter-spacing:.03em}
.beforeafter p{margin:5px 0 0;font-size:14px;line-height:1.55;white-space:pre-wrap}
.uncertain-reason{margin:4px 0;font-size:13px}
.evidence-refs{margin:6px 0;padding-left:18px;font-size:12px;color:#444}
table.report-table{border-collapse:collapse;width:100%;margin:8px 0;font-size:12.5px}
table.report-table th,table.report-table td{border:1px solid #ccc;padding:5px 8px;vertical-align:top;text-align:left}
table.report-table th{background:#f0ede6;color:#333}
.nochange{font-size:14px;background:#f7f4ed;border-left:4px solid #666;padding:10px 14px;margin:10px 0}
footer.note{max-width:1240px;margin:10px auto 40px;font-size:11px;color:#666;text-align:center}
@media (max-width:900px){.row{grid-template-columns:1fr}}
@media print{body{background:#fff}main{box-shadow:none;margin:0;max-width:none;padding:10mm 12mm}}
"""

def anchor_label_map(model: dict) -> dict[str, str]:
    """anchor_id -> display text from the Step-5 provisional anchors."""
    labels = {}
    for anchor in (model.get("document") or {}).get("provisional_anchors") or []:
        anchor_id = anchor.get("anchor_id")
        text = str(anchor.get("text") or anchor.get("normalized_text") or "").strip()
        if anchor_id and text:
            labels[str(anchor_id)] = text[:80]
    return labels


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _short(text, limit: int = 90) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _inline_diff_html(change: dict) -> str | None:
    """Token-level inline diff using char offsets from comparison_token_diff."""
    diff = (change.get("token_diff") or {}).get("comparison_token_diff") or {}
    ops = diff.get("changes") or []
    if not ops:
        return None
    old_text = ((change.get("old") or {}).get("comparison_text")) or ""
    new_text = ((change.get("new") or {}).get("comparison_text")) or ""
    if not old_text or not new_text:
        return None
    parts: list[str] = []
    cursor = 0
    for op in ops:
        s = op.get("old_char_start")
        e = op.get("old_char_end")
        ns = op.get("new_char_start")
        ne = op.get("new_char_end")
        if not all(isinstance(x, int) for x in (s, e, ns, ne)):
            return None
        if s < cursor or e > len(old_text) or ns < 0 or ne > len(new_text):
            return None
        parts.append(_esc(old_text[cursor:s]))
        operation = op.get("operation", "replace")
        old_span = _esc(old_text[s:e])
        new_span = _esc(new_text[ns:ne])
        if operation == "delete":
            parts.append(f"<del>{old_span}</del>")
        elif operation == "insert":
            parts.append(f"<ins>{new_span}</ins>")
        else:
            parts.append(f"<del>{old_span}</del><ins>{new_span}</ins>")
        cursor = e
    parts.append(_esc(old_text[cursor:]))
    return "".join(parts)


def _annotation_for(change: dict) -> dict:
    """Reporting-only operation grouping; never changes the comparison content."""
    old = change.get("old") or {}
    new = change.get("new") or {}
    old_text = old.get("comparison_text") or old.get("text") or ""
    new_text = new.get("comparison_text") or new.get("text") or ""
    return annotate_change(
        old_text,
        new_text,
        change.get("token_diff"),
        source_old=old.get("text"),
        source_new=new.get("text"),
    )


def _evidence_html(change: dict, annotation: dict | None = None) -> str:
    """Populate the proof details panel for a change record."""
    old = change.get("old") or {}
    new = change.get("new") or {}
    rows: list[tuple[str, str]] = []
    if old.get("element_id"):
        rows.append(("Old element", f"{_esc(old['element_id'])} (p.{old.get('primary_page')})"))
    if new.get("element_id"):
        rows.append(("New element", f"{_esc(new['element_id'])} (p.{new.get('primary_page')})"))
    if change.get("similarity") is not None:
        rows.append(("Similarity", f"{change['similarity']:.3f}"))
    reason = (change.get("resolution") or {}).get("reason")
    if reason:
        rows.append(("Reason", _esc(reason)))
    # Token op summary. The original operations are retained verbatim; the
    # grouping label is additive reporting metadata only.
    diff = (change.get("token_diff") or {}).get("comparison_token_diff") or {}
    ops = diff.get("changes") or []
    if ops:
        op_annotations = (annotation or {}).get("operations") or []
        summaries: list[str] = []
        for index, op in enumerate(ops[:6]):
            annotation_op = (
                op_annotations[index] if index < len(op_annotations) else {}
            )
            tag = op.get("operation", "?")
            ot = _short(
                annotation_op.get("old_text") or op.get("old_text") or "", 40
            )
            nt = _short(
                annotation_op.get("new_text") or op.get("new_text") or "", 40
            )
            classification = annotation_op.get("classification")
            if classification == "possible_wrap":
                label = " [possible wrap]"
            elif classification == "content":
                label = " [content]"
            else:
                label = ""
            summaries.append(f"{tag}: {_esc(ot)} → {_esc(nt)}{label}")
        if len(ops) > 6:
            summaries.append(f"... +{len(ops)-6} more")
        rows.append(("Changes", "<br>".join(summaries)))
    return "".join(f"<tr><td class='k'>{k}:</td><td>{v}</td></tr>" for k, v in rows)


def load_outputs(root: Path):
    out = root / "output"
    comparison = json.loads((out / "comparison_result.json").read_text(encoding="utf-8"))
    old_doc = json.loads((out / "old_document_structured.json").read_text(encoding="utf-8"))
    new_doc = json.loads((out / "new_document_structured.json").read_text(encoding="utf-8"))
    return comparison, old_doc, new_doc


def resolve_added_anchors(comparison: dict, movement_path: Path, exact_path: Path) -> dict[str, int]:
    """new element_id -> reading_order of nearest preceding matched old element."""
    anchors: list[tuple[int, int]] = []

    def collect(records):
        for rec in records:
            old = rec.get("old") or {}
            new = rec.get("new") or {}
            oro, nro = old.get("reading_order"), new.get("reading_order")
            if isinstance(oro, int) and isinstance(nro, int):
                anchors.append((nro, oro))

    try:
        exact = json.loads(exact_path.read_text(encoding="utf-8"))
        collect(exact.get("matches") or [])
    except FileNotFoundError:
        pass
    try:
        movement = json.loads(movement_path.read_text(encoding="utf-8"))
        for group in ("modified", "modified_review", "duplicate_exact_review", "relocated"):
            collect(movement.get(group) or [])
        collect(movement.get("resegmentation_review") or [])
    except FileNotFoundError:
        pass

    anchors.sort()
    new_ros = [nro for nro, _ in anchors]

    resolved: dict[str, int] = {}
    for change in comparison.get("changes") or []:
        if change.get("change_type") != "ADDED":
            continue
        new = change.get("new") or {}
        nro = new.get("reading_order")
        if not isinstance(nro, int):
            continue
        idx = bisect.bisect_left(new_ros, nro)
        resolved[new.get("element_id")] = anchors[idx - 1][1] if idx > 0 else -1
    return resolved


def comment_rows(item: dict, extra_rows: list[tuple[str, str]] | None = None) -> str:
    resolution = item.get("resolution") or {}
    status = resolution.get("status", "?")
    needs = resolution.get("needs_review")
    if needs is None:
        needs = item.get("needs_review")
    rows: list[tuple[str, str]] = [
        ("ID", _esc(item.get("change_id") or item.get("candidate_id") or "-")),
        ("Type", _esc(item.get("change_type") or "-")),
        ("Status", f"{_esc(status)} ({'Review' if needs else 'automatic'})"),
    ]
    if item.get("similarity") is not None:
        rows.append(("Similarity", round(item["similarity"], 4)))
    reasons = item.get("review_reasons") or []
    if reasons:
        rows.append(("Reasons", _esc(", ".join(reasons))))
    rows.extend((k, v) for k, v in (extra_rows or []))
    return "".join(f"<tr><td class='k'>{k}:</td><td>{v}</td></tr>" for k, v in rows)


def note_box(item: dict, heading: str, extra_rows: list[tuple[str, str]] | None = None, proof_html: str | None = None) -> str:
    """BU-style right-column comment box with underlined heading."""
    body = comment_rows(item, extra_rows)
    details_inner = f"<table>{proof_html}</table>" if proof_html else "<p style='color:#999'>(no proof)</p>"
    return (
        f"<aside class='note'><h5>{_esc(heading)}</h5><table>{body}</table>"
        f"<details class='comment'><summary>Show proof</summary>{details_inner}</details></aside>"
    )


def change_row(flow_html: str, note_html: str | None) -> str:
    if note_html:
        return f"<div class='row'><div class='flow'>{flow_html}</div>{note_html}</div>"
    return f"<div class='row'><div class='flow'>{flow_html}</div><span></span></div>"


def _text_html(text) -> str:
    return _esc(text).replace("\n", "<br/>")


def _technical_details(entry: dict) -> str:
    resolution = entry.get("resolution") or {}
    rows = [
        ("Record", entry.get("record_id") or entry.get("review_id") or "-"),
        ("Kind", entry.get("kind") or "-"),
        ("Change type", entry.get("change_type") or "-"),
        ("Result state", entry.get("result_state") or "-"),
        ("Legacy status", resolution.get("status") or "-"),
        ("Route", resolution.get("route") or "-"),
        ("Case type", entry.get("case_type") or "-"),
    ]
    if entry.get("source_candidate_id"):
        rows.append(("Source candidate", entry.get("source_candidate_id")))
    if entry.get("old_element_id"):
        rows.append(("Old element", f"{entry.get('old_element_id')} (p.{entry.get('old_page')})"))
    if entry.get("new_element_id"):
        rows.append(("New element", f"{entry.get('new_element_id')} (p.{entry.get('new_page')})"))
    if entry.get("similarity") is not None:
        rows.append(("Similarity", f"{entry['similarity']:.3f}"))
    if entry.get("linked_record_id"):
        rows.append(("Linked record", f"{entry.get('linked_kind')}:{entry.get('linked_record_id')}"))
    body = "".join(
        f"<tr><td class='k'>{_esc(key)}:</td><td>{_esc(value)}</td></tr>"
        for key, value in rows
    )
    reasons = entry.get("reasons") or []
    reason_html = (
        "<p><b>Reasons:</b> " + _esc(", ".join(str(reason) for reason in reasons)) + "</p>"
        if reasons else ""
    )
    return (
        "<details class='technical'><summary>Technical details</summary>"
        f"<table>{body}</table>{reason_html}</details>"
    )


def _evidence_refs(entry: dict) -> str:
    evidence = entry.get("evidence") or {}
    bits: list[str] = []
    for key in ("old_element_ids", "new_element_ids"):
        for item in evidence.get(key) or []:
            bits.append(f"{key}: {item}")
    for key in ("old_element_id", "new_element_id"):
        if evidence.get(key):
            bits.append(f"{key}: {evidence.get(key)}")
    for key in ("candidate_ids", "review_reasons"):
        for item in evidence.get(key) or []:
            bits.append(f"{key}: {item}")
    if not bits:
        return ""
    return "<ul class='evidence-refs'>" + "".join(
        f"<li>{_esc(item)}</li>" for item in bits
    ) + "</ul>"


def _before_after_html(entry: dict) -> str:
    before = entry.get("before") or ""
    after = entry.get("after") or ""
    old_page = entry.get("old_page")
    new_page = entry.get("new_page")
    # Each primary column shows its own exact source side. The combined inline
    # diff remains available in the full-document view below; using it here
    # would place new-only text inside the Before column.
    old_label = "Before / old" + (f" (page {old_page})" if old_page is not None else "")
    new_label = "After / new" + (f" (page {new_page})" if new_page is not None else "")
    return (
        "<div class='beforeafter'>"
        f"<div class='side'><b>{_esc(old_label)}</b><p>{_text_html(before)}</p></div>"
        f"<div class='side'><b>{_esc(new_label)}</b><p>{_text_html(after)}</p></div>"
        "</div>"
    )


def _assessment_html(entry: dict) -> str:
    """Source support and material meaning stay visibly separate."""
    notice = entry.get("assessment_notice")
    correspondence = entry.get("source_correspondence")
    relation = entry.get("material_relation")
    if not notice and not correspondence:
        return ""
    bits = []
    if correspondence:
        bits.append(f"source correspondence: {correspondence}")
    if relation:
        bits.append(f"material meaning: {relation}")
    sentence = "; ".join(bits)
    return (
        "<p class='uncertain-reason'>"
        f"{_esc(notice or sentence)}"
        + (f" ({_esc(sentence)})" if sentence and notice else "")
        + "</p>"
    )


def _unpaired_sources_html(entry: dict) -> str:
    sources = entry.get("unpaired_sources") or {}
    old = sources.get("old") or []
    new = sources.get("new") or []
    if not old and not new:
        return ""
    def side(label: str, items: list[dict]) -> str:
        if not items:
            return f"<div class='side'><b>{_esc(label)}</b><p>None.</p></div>"
        rows = "".join(
            "<li>"
            + (
                f"<span class='meta'>page {_esc(item.get('page'))} &middot; "
                f"{_esc(item.get('element_id'))}</span> "
                if item.get("page") is not None or item.get("element_id") else ""
            )
            + f"{_text_html(item.get('text') or '')}</li>"
            for item in items
        )
        return f"<div class='side'><b>{_esc(label)}</b><ul>{rows}</ul></div>"
    return (
        "<p class='uncertain-reason'>"
        f"{_esc(sources.get('notice') or 'These old and new passages are unpaired evidence.')}"
        "</p>"
        "<div class='beforeafter'>"
        + side("Old source passages (unpaired)", old)
        + side("New source passages (unpaired)", new)
        + "</div>"
    )


def _is_table_entry(entry: dict) -> bool:
    """Uncertain tables are projected as reviews but keep table shape."""
    return "old_table" in entry or "new_table" in entry


def _table_card(
    entry: dict,
    *,
    reason_html: str = "",
    data_record_id: str | None = None,
    review_id: str = "",
) -> str:
    rows = []
    for cell in entry.get("cell_changes") or []:
        if not isinstance(cell, dict):
            continue
        classification = str(cell.get("classification") or "changed")
        old_cell = cell.get("old") if isinstance(cell.get("old"), dict) else {}
        new_cell = cell.get("new") if isinstance(cell.get("new"), dict) else {}
        old_text = old_cell.get("text")
        new_text = new_cell.get("text")
        if classification.upper() == "UNCHANGED" and old_text == new_text:
            continue
        row_index = cell.get("old_row_index")
        if row_index is None:
            row_index = cell.get("new_row_index")
        column_index = cell.get("column_index")
        location = f"row {row_index if row_index is not None else '?'} / column {column_index if column_index is not None else '?'}"
        rows.append(
            "<tr>"
            f"<td>{_esc(location)}</td>"
            f"<td>{_text_html(old_text if old_text is not None else '—')}</td>"
            f"<td>{_text_html(new_text if new_text is not None else '—')}</td>"
            f"<td>{_esc(classification)}</td>"
            "</tr>"
        )
    old_t = entry.get("old_table") or {}
    new_t = entry.get("new_table") or {}
    shape = (
        f"old {old_t.get('row_count', '?')}x{old_t.get('column_count', '?')} / "
        f"new {new_t.get('row_count', '?')}x{new_t.get('column_count', '?')}"
    )
    pages = (
        f"old pages {old_t.get('source_pages') or old_t.get('pages') or []} / "
        f"new pages {new_t.get('source_pages') or new_t.get('pages') or []}"
    )
    if not rows:
        rows.append(
            "<tr><td colspan='4'>No cell-level text changes are available; "
            "the table record itself remains listed.</td></tr>"
        )
    title = entry.get("record_id") or "Table change"
    data_id = data_record_id or title
    return (
        f"<article class='diffcard {_esc(entry.get('result_state') or '')}' "
        f"data-record-id='{_esc(data_id)}' "
        f"data-review-id='{_esc(review_id)}'>"
        f"<h3>{_esc(title)} &middot; {_esc(entry.get('change_type') or 'TABLE_CHANGE')} "
        f"&middot; {_esc(entry.get('group_title') or 'Unassigned')}</h3>"
        f"<p class='uncertain-reason'>{_esc(shape)} &middot; {_esc(pages)}</p>"
        f"{reason_html}"
        "<table class='report-table'><thead><tr><th>Row / column</th>"
        "<th>Old value</th><th>New value</th><th>Change</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f"{_technical_details(entry)}</article>"
    )


def _entry_card(entry: dict) -> str:
    state = entry.get("result_state") or "uncertain"
    reasons = plain_reasons(entry.get("reasons") or []) if state == "uncertain" else []
    reason_html = (
        "<p class='uncertain-reason'>Why this remains uncertain: "
        f"{_esc(' '.join(reasons))}</p>"
        if reasons else ""
    )
    review_id = entry.get("review_id") or ""
    if _is_table_entry(entry):
        # An uncertain table is projected as kind=review with old_table/new_table.
        # Keep the table cells and its plain reason; preserve the review typed id
        # when a review record is the source of this uncertainty.
        data_id = review_id or entry.get("record_id")
        return _table_card(
            entry,
            reason_html=reason_html,
            data_record_id=data_id,
            review_id=review_id,
        )
    record_id = review_id or entry.get("record_id") or "record"
    if entry.get("kind") == "review" and not entry.get("linked_kind"):
        heading = f"Uncertain \u00b7 {entry.get('case_type') or 'unresolved case'}"
        body = (
            reason_html
            or "<p class='uncertain-reason'>This case remains uncertain; "
            "no further reason was recorded.</p>"
        ) + _assessment_html(entry) + _unpaired_sources_html(entry) + _evidence_refs(entry)
    else:
        heading = (
            f"{entry.get('change_type') or 'Change'} \u00b7 "
            f"{entry.get('group_title') or 'Unassigned'}"
        )
        body = reason_html + _before_after_html(entry) + _assessment_html(entry)
    return (
        f"<article class='diffcard {_esc(state)}' data-record-id='{_esc(record_id)}' "
        f"data-review-id='{_esc(entry.get('review_id') or '')}'>"
        f"<h3>{_esc(heading)}</h3>{body}{_technical_details(entry)}</article>"
    )


def _summary_html(report: dict) -> str:
    documents = report.get("documents") or {}
    old = documents.get("old") or {}
    new = documents.get("new") or {}
    summary = report.get("summary") or {}

    def doc_block(label: str, doc: dict) -> str:
        pages = doc.get("page_count")
        elements = doc.get("source_element_count")
        bits = []
        if pages is not None:
            bits.append(f"{pages} page(s)")
        if elements is not None:
            bits.append(f"{elements} source element(s)")
        return (
            f"<div class='doc'><b>{_esc(label)}: {_esc(doc.get('label') or 'Document')}</b>"
            f"<span>{_esc(' · '.join(bits))}</span></div>"
        )

    counts = (
        f"<span class='count'>{summary.get('supported_changes', 0)} supported element change(s)</span>"
        f"<span class='count'>{summary.get('supported_table_changes', 0)} supported table change(s)</span>"
        f"<span class='count'>{summary.get('uncertain_items', 0)} uncertain item(s)</span>"
        f"<span class='count'>{summary.get('non_semantic_events', 0)} non-semantic event(s)</span>"
    )
    limitations = summary.get("limitations") or []
    limitation_html = ""
    if limitations:
        limitation_html = "<ul class='limitations'>" + "".join(
            f"<li>{_esc(item)}</li>" for item in limitations
        ) + "</ul>"
    message = report.get("message")
    message_html = f"<p class='nochange'>{_esc(message)}</p>" if message else ""
    return (
        "<section class='summary'><h2>Comparison summary</h2>"
        f"<div class='documents'>{doc_block('Old', old)}{doc_block('New', new)}</div>"
        f"<div class='counts'>{counts}</div>{message_html}{limitation_html}</section>"
    )


def _cards_for(report: dict, state: str) -> str:
    groups = report.get("groups") or []
    if state == "supported":
        blocks = []
        for group in groups:
            entries = group.get("supported") or []
            if not entries:
                continue
            blocks.append(f"<div class='bucket'>{_esc(group.get('title'))}</div>")
            blocks.extend(_entry_card(entry) for entry in entries)
        return "".join(blocks)
    blocks = []
    for group in groups:
        entries = group.get("uncertain") or []
        if not entries:
            continue
        blocks.append(f"<div class='bucket'>{_esc(group.get('title'))}</div>")
        blocks.extend(_entry_card(entry) for entry in entries)
    return "".join(blocks)


def render_change_block(change: dict, cls: str) -> str:
    old_text = ((change.get("old") or {}).get("text")) or ""
    new_text = ((change.get("new") or {}).get("text")) or ""
    ctype = change.get("change_type")
    cid = _esc(change.get("change_id"))
    pages = change.get("source_pages")
    annotation = _annotation_for(change)
    extra: list[tuple[str, str]] = []
    if isinstance(pages, dict):
        extra.append(("Pages", f"old {pages.get('old')} / new {pages.get('new')}"))
    if annotation.get("operations"):
        # Additive reporting only: possible wrap vs remaining content.
        extra.append(("Operations", _esc(annotation["summary"])))
    proof = _evidence_html(change, annotation)
    if ctype == "DELETED":
        flow = f"<p class='el'><del>{_esc(old_text)}</del><span class='cmark'>{cid}</span></p>"
        heading = "Deleted"
    elif ctype == "ADDED":
        flow = f"<p class='el'><ins>{_esc(new_text)}</ins><span class='cmark'>{cid}</span></p>"
        heading = "Added"
    else:
        inline = _inline_diff_html(change)
        if inline:
            flow = f"<p class='el'>{inline}<span class='cmark'>{cid}</span></p>"
        else:
            sep = " " if max(len(old_text), len(new_text)) <= MAX_INLINE_CHARS else "<br/>"
            flow = (
                f"<p class='el'><del>{_esc(old_text)}</del>{sep}<ins>{_esc(new_text)}</ins>"
                f"<span class='cmark'>{cid}</span></p>"
            )
        heading = "Modified"
    return change_row(flow, note_box(change, heading, extra or None, proof_html=proof))


def render_table_change(record: dict) -> str:
    classification = record.get("classification") or record.get("change_type")
    old_t, new_t = record.get("old") or {}, record.get("new") or {}
    head = (
        f"<h4>{_esc(record.get('table_change_id'))} &middot; {_esc(classification)} &middot; "
        f"old pages {(old_t.get('source_pages') or old_t.get('pages') or [])} &rarr; "
        f"new pages {(new_t.get('source_pages') or new_t.get('pages') or [])}</h4>"
    )
    lines = []
    shown = 0
    for cell in record.get("cell_changes") or []:
        cls = cell.get("classification")
        if cls == "UNCHANGED":
            continue
        ot = ((cell.get("old") or {}).get("text")) or ""
        nt = ((cell.get("new") or {}).get("text")) or ""
        ref = f"[r{_short(cell.get('old_row_index'), 4)}/c{_short(cell.get('column_index'), 3)}]"
        if cls == "ADDED":
            lines.append(f"<div class='cellrow'>{ref} <ins>+ {_esc(_short(nt, 160))}</ins></div>")
        elif cls == "DELETED":
            lines.append(f"<div class='cellrow'>{ref} <del>- {_esc(_short(ot, 160))}</del></div>")
        else:
            cell_reasons = cell.get("review_reasons") or []
            reason_mark = (
                f" - {_esc(', '.join(str(r) for r in cell_reasons))}"
                if cell_reasons
                else ""
            )
            lines.append(
                f"<div class='cellrow'>{ref} <del>{_esc(_short(ot, 140))}</del> "
                f"<ins>{_esc(_short(nt, 140))}</ins> "
                f"<span style='color:#888'>({_esc(cls)}){reason_mark}</span></div>"
            )
        cell_annotation = annotate_change(
            ot, nt, cell.get("token_diff"),
            source_old=ot, source_new=nt,
        )
        if cell_annotation.get("operations"):
            lines.append(
                "<div class='cellrow' style='color:#666;font-size:11px'>"
                f"Operations: {_esc(cell_annotation['summary'])}</div>"
            )
        shown += 1
        if shown >= 80:
            lines.append("<div class='cellrow'>... further cells omitted</div>")
            break
    if not lines:
        lines.append("<div class='cellrow'>(structure/layout-only differences - see index)</div>")
    fake = {
        "change_id": record.get("table_change_id"),
        "change_type": record.get("change_type"),
        "needs_review": record.get("needs_review"),
        "review_reasons": record.get("review_reasons") or [],
        "resolution": record.get("resolution") or {},
    }
    extra = [
        ("Tables", f"old={','.join(old_t.get('source_table_ids') or old_t.get('table_ids') or []) or '-'} "
                   f"new={','.join(new_t.get('source_table_ids') or new_t.get('table_ids') or []) or '-'}"),
        ("Shape", f"old {old_t.get('row_count') if old_t.get('row_count') is not None else old_t.get('rows')}"
                  f"x{old_t.get('column_count') if old_t.get('column_count') is not None else old_t.get('columns')} / "
                  f"new {new_t.get('row_count') if new_t.get('row_count') is not None else new_t.get('rows')}"
                  f"x{new_t.get('column_count') if new_t.get('column_count') is not None else new_t.get('columns')}"),
    ]
    return f"<div class='tableblock'>{head}{''.join(lines)}{note_box(fake, 'Table change', extra)}</div>"


def render(root: Path) -> Path:
    comparison, old_doc, new_doc = load_outputs(root)
    out_dir = root / "output"
    report = build_report_model(
        comparison, old_document=old_doc, new_document=new_doc
    )

    elements = sorted(
        (old_doc.get("document") or {}).get("elements") or [],
        key=lambda e: e.get("reading_order") if isinstance(e.get("reading_order"), int) else 10**12,
    )
    elements_by_id = {e.get("element_id"): e for e in elements}
    # Map actual reading_order values to their sorted positions; added changes
    # are anchored by reading_order but emitted using enumerate() indices.
    ro_to_index = {
        e.get("reading_order"): i
        for i, e in enumerate(elements)
        if isinstance(e.get("reading_order"), int)
    }

    del_mod_by_old: dict[str, dict] = {}
    added_by_new: dict[str, dict] = {}
    # Only deterministically ACCEPTED changes belong in the BU inline redline.
    # Uncertain items are shown in the primary report above; only supported
    # changes are marked inline. Proven artifacts (hyphenation, markers,
    # structural) never reach canonical `changes`.
    for change in comparison.get("changes") or []:
        if record_result_state(change, kind="change") != "supported":
            continue
        if change.get("change_type") == "ADDED":
            added_by_new[(change.get("new") or {}).get("element_id")] = change
        else:
            del_mod_by_old[(change.get("old") or {}).get("element_id")] = change

    new_elements = (new_doc.get("document") or {}).get("elements") or []
    new_by_id = {e.get("element_id"): e for e in new_elements}

    added_anchor_ro = resolve_added_anchors(
        comparison,
        out_dir / "comparison_candidates_with_movement.json",
        out_dir / "exact_element_matches.json",
    )
    pending_adds: dict[int, list[dict]] = {}
    for elem_id, change in added_by_new.items():
        anchor_ro = added_anchor_ro.get(elem_id, -1)
        # Convert the matched old reading_order to its sorted element index so
        # additions are inserted after the correct old element even when
        # reading_order values are non-contiguous.
        anchor_idx = ro_to_index.get(anchor_ro, -1)
        pending_adds.setdefault(anchor_idx, []).append(change)
    emitted_added: set[str] = set()

    supported_cards = _cards_for(report, "supported")
    uncertain_cards = _cards_for(report, "uncertain")
    table_cards = "".join(
        _entry_card(entry) for entry in report.get("supported_tables") or []
    )
    story = ["<main>", _summary_html(report)]
    if supported_cards:
        story.append("<h2 style='font-size:17px;color:#1f2d3d'>Supported changes</h2>")
        story.append(supported_cards)
    if uncertain_cards:
        story.append("<h2 style='font-size:17px;color:#1f2d3d'>Uncertain differences</h2>")
        story.append(uncertain_cards)
    if table_cards:
        story.append("<h2 style='font-size:17px;color:#1f2d3d'>Table changes</h2>")
        story.append(table_cards)
    story.extend([
             "<h2 style='font-size:16px;color:#555'>Full document view &ndash; old version with change markers</h2>",
             "<p style='font-size:12px;color:#777'>"
             "<span style='color:#c00000;text-decoration:line-through'>Report-generated strike-through</span> = old text removed by this comparison &middot; "
             "<span style='color:#c00000;text-decoration:underline'>Underlined</span> = new text &middot; "
             "These marks are generated by the report; they are not a claim about formatting marks in the source PDF. "
             "Only supported changes are marked inline; uncertain items are listed above and remain unresolved.</p>"])
    anchor_labels = anchor_label_map(old_doc)
    current_bucket = None
    for order, element in enumerate(elements):
        label = None
        text = (element.get("text") or "").strip()
        context = element.get("provisional_anchor_context") or {}
        nearest_id = context.get("nearest_anchor_id")
        if nearest_id and str(nearest_id) in anchor_labels:
            label = anchor_labels[str(nearest_id)]
        if label and label != current_bucket:
            current_bucket = label
            story.append(f"<div class='bucket'>{_esc(label)}</div>")

        elem_id = element.get("element_id")

        for change in pending_adds.get(order - 1, []):
            nid = (change.get("new") or {}).get("element_id")
            emitted_added.add(nid)
            story.append(render_change_block(change, ""))

        if elem_id in del_mod_by_old:
            change = del_mod_by_old[elem_id]
            story.append(render_change_block(change, ""))
            continue

        etype = element.get("element_type")
        if etype in {"header", "footer", "page_number"}:
            continue
        if etype == "table":
            story.append(
                f"<p class='el' style='color:#999'>[{_esc(elem_id)} - unchanged table]</p>"
            )
            continue
        if not text:
            continue
        css_class = "el heading" if etype in {"heading", "title"} else "el solo"
        story.append(f"<p class='{css_class}'>{_esc(text)}</p>")

    for elem_id, change in sorted(
        added_by_new.items(),
        key=lambda kv: (kv[1].get("new") or {}).get("reading_order") or 0,
    ):
        if elem_id in emitted_added:
            continue
        story.append(render_change_block(change, ""))

    story.append("</main>")
    summary = report.get("summary") or {}
    html_doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'/>"
        "<title>Document Comparison</title><style>" + _CSS + "</style></head><body>"
        "<header class='bar'><h1>Document comparison</h1>"
        f"<p>{_esc(summary.get('supported_changes', 0))} supported element change(s) &middot; "
        f"{_esc(summary.get('supported_table_changes', 0))} supported table change(s) &middot; "
        f"{_esc(summary.get('uncertain_items', 0))} uncertain item(s) &middot; "
        f"{_esc(summary.get('non_semantic_events', 0))} non-semantic event(s)</p></header>"
        + "".join(story)
        + "<footer class='note'>Generated by document_compare - deterministic Phase-1 pipeline "
        "(LLM evaluation optional/disabled). Engine support is not legal/semantic verification.</footer></body></html>"
    )

    target = out_dir / "bu_comparison_redline.html"
    target.write_text(html_doc, encoding="utf-8")
    print(f"Saved redline: {target}")
    print(f"Redline size: {target.stat().st_size / 1024:.0f} KB")
    return target


if __name__ == "__main__":
    render(find_project_root())
