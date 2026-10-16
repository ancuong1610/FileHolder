"""Self-contained, read-only HTML rendering for the evidence review bundle.

The renderer is a pure function of the assembled context. Every value that
originates in a document or artifact is HTML-escaped; no external scripts,
stylesheets, fonts or images are referenced, so ``index.html`` opens offline
from the local filesystem. It presents raw DI roles separately from the
derived structure hypotheses, keeps the extraction/structure/comparison
metrics visibly separate, shows raw text separately from normalized text, and
resolves table-represented paragraphs to their source table instead of
duplicating body text.
"""
from __future__ import annotations

import html
from typing import Any

CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 1.5rem; color: #1c1c1c; }
h1, h2, h3 { color: #10305a; }
table { border-collapse: collapse; margin: 0.5rem 0 1rem; }
th, td { border: 1px solid #c8ccd4; padding: 0.25rem 0.5rem; font-size: 0.85rem; vertical-align: top; }
th { background: #eef2f8; text-align: left; }
details { margin: 0.4rem 0; }
summary { cursor: pointer; font-weight: 600; }
.muted { color: #5b6472; }
.status-not-scored { color: #8a6d00; font-weight: 600; }
.status-scored { color: #1a6b2a; font-weight: 600; }
.status-false-positive { color: #a11; font-weight: 600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }
.section { border: 1px solid #d7dbe2; border-radius: 6px; padding: 0.75rem 1rem; margin: 1rem 0; }
.banner { background: #fff7e0; border: 1px solid #e0c060; padding: 0.5rem 0.75rem; border-radius: 6px; }
.raw { background: #f6f6f6; }
"""


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _status(value: Any) -> str:
    text = _esc(value)
    if value == "scored":
        return f'<span class="status-scored">{text}</span>'
    if value == "false_positive":
        return f'<span class="status-false-positive">{text}</span>'
    if value in ("not_scored", "not_applicable"):
        return f'<span class="status-not-scored">{text}</span>'
    return text


METRIC_HEADER = (
    "<tr><th>block</th><th>tp</th><th>fp</th><th>fn</th><th>support</th>"
    "<th>predictions</th><th>precision</th><th>recall</th><th>F1</th>"
    "<th>global precision</th><th>coverage complete</th><th>status</th></tr>"
)


def _metric_row(name: str, block: dict[str, Any] | None) -> str:
    if not block:
        return f"<tr><td>{_esc(name)}</td><td colspan='11' class='muted'>not scored</td></tr>"
    return (
        "<tr>"
        f"<td>{_esc(name)}</td>"
        f"<td>{_esc(block.get('tp'))}</td>"
        f"<td>{_esc(block.get('fp'))}</td>"
        f"<td>{_esc(block.get('fn'))}</td>"
        f"<td>{_esc(block.get('support'))}</td>"
        f"<td>{_esc(block.get('prediction_count'))}</td>"
        f"<td>{_esc(block.get('precision'))}</td>"
        f"<td>{_esc(block.get('recall'))}</td>"
        f"<td>{_esc(block.get('f1'))}</td>"
        f"<td>{_esc(block.get('global_precision'))}</td>"
        f"<td>{_esc(block.get('coverage_complete'))}</td>"
        f"<td>{_status(block.get('status'))}</td>"
        "</tr>"
    )


def _table_grid(table: dict[str, Any]) -> str:
    cells = (table.get("table") or {}).get("cells") or []
    row_count = (table.get("table") or {}).get("row_count") or 0
    column_count = (table.get("table") or {}).get("column_count") or 0
    if not cells or not row_count or not column_count:
        return "<p class='muted'>no cell grid</p>"
    anchors: dict[tuple[int, int], dict[str, Any]] = {}
    covered: set[tuple[int, int]] = set()
    for cell in cells:
        row = cell.get("row_index")
        column = cell.get("column_index")
        if not isinstance(row, int) or not isinstance(column, int):
            continue
        row_span = cell.get("row_span") or 1
        column_span = cell.get("column_span") or 1
        anchors[(row, column)] = cell
        for row_offset in range(row_span):
            for column_offset in range(column_span):
                covered.add((row + row_offset, column + column_offset))
    rows = []
    for row in range(row_count):
        parts = []
        for column in range(column_count):
            coordinate = (row, column)
            cell = anchors.get(coordinate)
            if cell is None:
                if coordinate in covered:
                    # Covered by a rowspan/colspan anchor: emit no cell at all.
                    continue
                parts.append("<td class='muted'>·</td>")
                continue
            row_span = cell.get("row_span") or 1
            column_span = cell.get("column_span") or 1
            span = ""
            if row_span > 1:
                span += f" rowspan='{int(row_span)}'"
            if column_span > 1:
                span += f" colspan='{int(column_span)}'"
            parts.append(
                f"<td{span}>"
                f"<div>normalized: {_esc(cell.get('normalized_text') or cell.get('text'))}</div>"
                f"<div class='mono raw'>raw: {_esc(cell.get('text'))}</div>"
                f"<div class='mono muted'>{_esc(cell.get('cell_id'))} "
                f"spans={_esc(cell.get('spans'))} page={_esc(cell.get('primary_page'))}</div>"
                "</td>"
            )
        rows.append(f"<tr>{''.join(parts)}</tr>")
    return f"<table>{''.join(rows)}</table>"


def _element_body(element: dict[str, Any], record: dict[str, Any], heading_evidence: dict[str, Any]) -> str:
    body = [
        f"<div class='mono'>type={_esc(element.get('element_type'))} "
        f"role={_esc(element.get('original_role'))} page={_esc(element.get('primary_page'))} "
        f"order={_esc(element.get('reading_order'))}</div>",
        f"<div class='mono'>spans={_esc(element.get('spans'))}</div>",
        f"<div class='mono'>primary_polygon={_esc(element.get('primary_polygon'))}</div>",
        f"<div class='mono'>bounding_regions={_esc(element.get('bounding_regions'))}</div>",
        f"<div class='mono'>extraction_validation={_esc(element.get('extraction_validation'))}</div>",
        f"<div class='mono'>di_status={_esc(heading_evidence.get('status'))} "
        f"di_heading_like={_esc(heading_evidence.get('di_role_is_heading_like'))}</div>",
        f"<div class='mono'>derived: category={_esc(record.get('category'))} "
        f"section={_esc(record.get('section_id'))}</div>",
    ]
    raw_text = element.get("text")
    if element.get("element_type") == "table":
        body.append(_table_grid(element))
    elif element.get("inside_table") or element.get("represented_by_table_id"):
        # Do not duplicate body text for a table-represented paragraph; resolve
        # to its source table and keep the raw evidence accessible.
        body.append(
            f"<div class='muted'>table-represented paragraph "
            f"(source table {_esc(element.get('represented_by_table_id'))}); "
            f"normalized body text suppressed to avoid duplication.</div>"
        )
        body.append(f"<div class='mono raw'>raw: <pre>{_esc(raw_text)}</pre></div>")
    else:
        body.append(f"<div class='mono raw'>raw: <pre>{_esc(raw_text)}</pre></div>")
        body.append(
            f"<div class='mono'>normalized: "
            f"<pre>{_esc(element.get('normalized_text'))}</pre></div>"
        )
    return "".join(body)


def _elements_details(side: dict[str, Any]) -> str:
    evidence = side["structure_evidence"]
    membership = evidence["membership"]["by_element"]
    blocks = []
    for element in side["document"]["elements"]:
        element_id = str(element.get("element_id"))
        record = membership.get(element_id, {})
        furniture = element.get("element_type") in {"header", "footer", "page_number"}
        role_class = "furniture" if furniture else "body"
        heading_evidence = element.get("heading_evidence") or {}
        blocks.append(
            f"<details><summary class='mono'>{_esc(element_id)} "
            f"({_esc(element.get('element_type'))}, {_esc(role_class)})</summary>"
            f"{_element_body(element, record, heading_evidence)}</details>"
        )
    return "".join(blocks)


def _failures_table(scoring: dict[str, Any]) -> str:
    failures = scoring.get("failures") or []
    if not failures:
        return "<p class='muted'>no mismatches in declared coverage</p>"
    rows = "".join(
        f"<tr><td class='mono'>{_esc(item.get('id'))}</td>"
        f"<td class='mono'>{_esc(item.get('expected'))}</td>"
        f"<td class='mono'>{_esc(item.get('predicted'))}</td></tr>"
        for item in failures
    )
    return (
        "<table><tr><th>id</th><th>expected</th><th>predicted</th></tr>"
        f"{rows}</table>"
    )


def _structure_side(label: str, side: dict[str, Any], scoring: dict[str, Any]) -> str:
    evidence = side["structure_evidence"]
    toc = evidence["toc"]
    entry_rows = "".join(
        "<tr>"
        f"<td class='mono'>{_esc(entry['entry_id'])}</td>"
        f"<td>{_esc(entry['source_kind'])}</td>"
        f"<td>{_esc(entry['label'])}</td>"
        f"<td>{_esc(entry['title'])}</td>"
        f"<td>{_esc(entry['printed_page'])}</td>"
        f"<td>{_esc(entry['status'])}</td>"
        f"<td class='mono'>{_esc(entry['body_element_id'])}</td>"
        f"<td class='mono'>{_esc(', '.join(entry['candidate_body_element_ids']))}</td>"
        "</tr>"
        for entry in toc["entries"]
    ) or "<tr><td colspan='8' class='muted'>no TOC entries</td></tr>"
    section_rows = "".join(
        "<tr>"
        f"<td class='mono'>{_esc(section['section_id'])}</td>"
        f"<td>{_esc(section['title'])}</td>"
        f"<td>{_esc(section['level'])}</td>"
        f"<td>{_esc(section['level_known'])}</td>"
        f"<td class='mono'>{_esc(section['parent_section_id'])}</td>"
        f"<td>{_esc(section['basis'])}</td>"
        f"<td class='mono'>{_esc(', '.join(section['uncertainty']))}</td>"
        "</tr>"
        for section in evidence["sections"]
    ) or "<tr><td colspan='7' class='muted'>no sections</td></tr>"
    counts = evidence["membership"]["counts"]
    link_block = scoring.get("links") if scoring else None
    membership_block = scoring.get("membership") if scoring else None
    return f"""
<div class="section">
  <h3>{_esc(label.upper())} structure ({_esc(side['document_id'])})</h3>
  <p class="muted">structure evidence source: {_esc(side['structure_evidence_source'])}
     — raw DI roles vs derived hypotheses are shown separately below.</p>
  <p>TOC detected: <b>{_esc(toc['detected'])}</b>; usable: <b>{_esc(toc['usable'])}</b>;
     entries: {_esc(toc['entry_count'])}; linked: {_esc(toc['linked_count'])};
     ambiguous: {_esc(toc['ambiguous_count'])}; unmatched: {_esc(toc['unmatched_count'])}.</p>
  <p>Membership counts: {_esc(counts)}</p>
  <h4>TOC entries and links</h4>
  <table>
    <tr><th>entry_id</th><th>source</th><th>label</th><th>title</th><th>printed page</th>
        <th>status</th><th>body element</th><th>candidates</th></tr>
    {entry_rows}
  </table>
  <h4>Section hypotheses</h4>
  <table>
    <tr><th>section_id</th><th>title</th><th>level</th><th>level known</th>
        <th>parent</th><th>basis</th><th>uncertainty</th></tr>
    {section_rows}
  </table>
  <h4>Structure scoring over declared coverage</h4>
  <p>status: {_status(scoring.get('status'))} {_esc(scoring.get('reason'))}</p>
  <table>
    {METRIC_HEADER}
    {_metric_row('links', link_block)}
    {_metric_row('membership', membership_block)}
  </table>
  <h4>Mismatches in declared coverage</h4>
  {_failures_table(scoring)}
  <details><summary>Elements: raw vs normalized text, roles, ids, pages, spans, polygons, extraction validation, derived membership</summary>
    {_elements_details(side)}
  </details>
</div>
"""


def _extraction_side(label: str, record: dict[str, Any]) -> str:
    role_counts = record.get("original_role_counts") or {}
    warning_flags = record.get("extraction_warning_flags") or {}
    review_flags = record.get("extraction_review_flags") or {}
    return f"""
  <h4>{_esc(label.upper())} diagnostics (not accuracy)</h4>
  <table>
    <tr><th>elements</th><th>pages</th><th>missing element spans</th><th>missing element locations</th>
        <th>missing cell spans</th><th>missing cell locations</th></tr>
    <tr><td>{_esc(record.get('elements'))}</td><td>{_esc(record.get('pages'))}</td>
        <td>{_esc(record.get('missing_span_elements'))}</td>
        <td>{_esc(record.get('missing_location_elements'))}</td>
        <td>{_esc(record.get('missing_span_cells'))}</td>
        <td>{_esc(record.get('missing_location_cells'))}</td></tr>
  </table>
  <div class="mono">original_role counts: {_esc(role_counts)}</div>
  <div class="mono">extraction_warning_flags: {_esc(warning_flags)}</div>
  <div class="mono">extraction_review_flags: {_esc(review_flags)}</div>
"""


def render_html(context: dict[str, Any]) -> str:
    metrics = context["metrics"]
    provenance = context["provenance"]
    reference = metrics["reference"]
    extraction = metrics["layers"]["extraction"]
    comparison = metrics["layers"]["comparison"]

    side_sections = "".join(
        _structure_side(label, context["sides"][label], metrics["layers"]["structure"][label]["scoring"])
        for label in ("old", "new")
    )

    failures = metrics.get("failures") or []
    limitations = "".join(f"<li>{_esc(item)}</li>" for item in metrics.get("limitations", []))
    extraction_sides = "".join(
        _extraction_side(label, record) for label, record in extraction["inventory"].items()
    )
    failure_rows = "".join(
        f"<li>{_esc(item.get('side'))} / {_esc(item.get('block'))}: "
        f"{_esc(item.get('false_positives'))} false positive(s)</li>"
        for item in failures
    ) or "<li class='muted'>none</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Docling-informed evidence review (reference-only)</title>
<style>{CSS}</style>
</head>
<body>
<h1>Docling-informed evidence review — reference-only</h1>
<div class="banner">
  This is <b>evidence visualization, not original-page visual truth</b>.
  No source PDFs are present, so there are no page screenshots and no claimed
  source verification. It is not Docling output and not an upstream metric.
</div>

<div class="section">
  <h2>Bundle and reference status</h2>
  <p>Bundle version: {_esc(metrics['bundle_version'])}; structure evidence version:
     {_esc(metrics['structure_evidence_version'])}; Step-5 version:
     {_esc(metrics['step5_version'])}; output schema: {_esc(metrics['output_schema_version'])}.</p>
  <p>Reference provided: <b>{_esc(reference.get('provided'))}</b>;
     status: {_status(reference.get('status'))};
     kind: {_esc(reference.get('kind'))}; verified: {_esc(reference.get('verified'))};
     synthetic: {_esc(reference.get('synthetic'))}.
     {_esc(reference.get('reason') or '')}</p>
  <p class="muted">Artifacts: {_esc(provenance.get('artifacts_dir'))}
     {('; missing: ' + _esc(', '.join(provenance.get('missing') or []))) if provenance.get('missing') else ''}</p>
</div>

<div class="section">
  <h2>Extraction layer (diagnostics only)</h2>
  <p>status: {_status(extraction.get('status'))} — {_esc(extraction.get('reason'))}.
     These are extraction diagnostics, not accuracy: text/location accuracy is
     {_esc(extraction.get('text_accuracy'))} (null = unscored, never perfect).</p>
  {extraction_sides}
</div>

{side_sections}

<div class="section">
  <h2>Comparison layer</h2>
  <p>available: {_esc(comparison.get('available'))}; changes: {_esc(comparison.get('changes'))};
     table changes: {_esc(comparison.get('table_changes'))};
     review queue: {_esc(comparison.get('review_queue'))}.</p>
  <p>semantic accuracy: {_esc(comparison.get('semantic_accuracy'))}
     ({_status(comparison.get('semantic_accuracy_status'))} —
     {_esc(comparison.get('semantic_accuracy_reason'))}). Review counts are not accuracy.</p>
</div>

<div class="section">
  <h2>Failures, skips and limitations</h2>
  <p>failures: {_esc(len(failures))}; errors: {_esc(len(metrics.get('errors') or []))}.</p>
  <ul>{failure_rows}</ul>
  <ul>{''.join(f"<li>{_esc(skip.get('layer'))}: {_esc(skip.get('reason'))}</li>" for skip in metrics.get('skips', []))}</ul>
  <ul>{limitations}</ul>
</div>
</body>
</html>
"""
