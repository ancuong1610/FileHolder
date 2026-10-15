"""Inline one-flow comparison PDF (report-only, additive artifact).

This module is a NEW, ADDITIVE presentation path. It does not replace or change
``crr_compare.redline_pdf`` or ``comparison_redline.pdf``; it produces a second
artifact (``comparison_inline.pdf``) that shows each change as a single
one-flow paragraph: unchanged context once, deleted old text struck red,
inserted new text colored blue. Every item keeps its stable change id, source
page/ref and review resolution.

Safety contract (approved policy):

* The canonical result, raw DI, matching, routing, and every source string are
  never modified. This is a pure re-presentation of ``comparison_result.json``.
* GERMAN END-OF-LINE HYPHENATIONS are cleaned ONLY with hard source proof:
  - a soft discretionary hyphen (U+00AD), which is inherently discretionary, or
  - an explicit newline hyphen whose exact joined word is corroborated by the
    opposite side (the joined word appears as a word on the other document
    side, including a differently wrapped form of the same word).
* A ``possible_wrap`` label alone is NEVER treated as verified proof.
* Permanent hyphens (``Kosten-Nutzen``, ``EU-Verordnung``, ``re-sign``),
  meaningful word boundaries (``now here`` / ``nowhere``), case, numbers, signs,
  percent, punctuation and negation (``muss`` / ``kann``) are preserved.
* An unproven one-sided join, and any missing/unverified alignment, stay fully
  visible with an explicit WARNING; the original PDF remains the raw evidence.
* No semantic-correctness claim is made and no LLM, dictionary or download is
  used.

Limitations (also written to the artifact cover and the optional
``inline_pdf_audit.json``):

* Inline alignment is token-based and covers the whitespace between tokens, so
  both sides reconstruct exactly and a whitespace-only difference is shown as
  its own segment; it is never used to join or drop content.
* A whole layout-only change is omitted from the flow ONLY when its cleaned old
  and new text are identical, the stored alignment is verified against the
  source text, and the change is not under review; a change under review stays
  visible.
* Unchanged table cell text is omitted from the table body ONLY when the raw old
  and new cell text are identical; a cell labelled unchanged whose text differs
  stays visible with a warning.
"""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from crr_compare.diff_annotations import reliable_operation_ranges
from crr_compare.render_spans import TOKEN_PATTERN

DELETED_COLOR = "#b00020"
INSERTED_COLOR = "#0b6ecc"
SOFT_HYPHEN = "\u00ad"
_WARN_COLOR = "#8a6d00"

NO_DIFFERENCES_MESSAGE = "No differences were found between the two documents."

WARNING_MISSING_ALIGNMENT = (
    "Stored diff offsets were not verified against the source text; the flow was "
    "realigned conservatively and every difference is shown. Inspect the original PDF."
)
WARNING_UNPROVEN_JOIN = (
    "One or more hyphenated words were not corroborated by the opposite side and were "
    "left visible. Inspect the original PDF."
)
WARNING_CELL_CLASSIFICATION_MISMATCH = (
    "A table cell labelled UNCHANGED has different old and new text; the labels are "
    "not trusted and the cell is shown in full. Inspect the original table."
)

# A visible newline hyphen (soft hyphen U+00AD is handled separately). Only
# alphabetic word fragments are joined: digits, arithmetic fragments and the
# non-breaking hyphen (U+2011) are never a German line wrap and stay visible, so
# a numeric/arithmetic join is never invented from the opposite side.
_ALPHA_CLASS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ž"
_NEWLINE_HYPHEN_CLASS = r"[\-\u2010]"
_SOFT_LINE_RE = re.compile(
    r"([" + _ALPHA_CLASS + r"]+)\u00ad[ \t]*\n[ \t]*([" + _ALPHA_CLASS + r"]+)")
_SOFT_PLAIN_RE = re.compile(
    r"([" + _ALPHA_CLASS + r"]+)\u00ad([" + _ALPHA_CLASS + r"]+)")
_NEWLINE_HYPHEN_RE = re.compile(
    r"([" + _ALPHA_CLASS + r"]+)" + _NEWLINE_HYPHEN_CLASS
    + r"[ \t]*\n[ \t]*([" + _ALPHA_CLASS + r"]+)")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9_]+")

LIMITATIONS = (
    "Report-only: this artifact never changes the canonical result, routing, "
    "matching, raw DI, or any source text.",
    "Hyphenation cleanup is limited to source proof: a soft discretionary hyphen, "
    "or an explicit newline hyphen whose exact joined word is corroborated by the "
    "opposite side.",
    "A possible-wrap label alone is never treated as verified proof.",
    "Unproven joins and missing/unverified alignment stay visible with a warning; "
    "the original PDF is the raw evidence.",
    "No semantic-correctness claim is made; no LLM, dictionary, or download is used.",
    "Legacy table content that is genuinely missing is named, never silently shown "
    "as empty.",
    "Inline alignment covers the whitespace between tokens, so both sides "
    "reconstruct exactly and every difference (including a whitespace-only one) "
    "is shown.",
    "A whole layout-only change is omitted only when its cleaned old and new text "
    "are identical, the stored alignment is verified against the source text, and "
    "it is not under review; a change under review stays visible.",
    "Unchanged table cell text is omitted only when the raw old and new text are "
    "identical; a cell labelled unchanged whose text differs stays visible with a "
    "warning.",
)

_COVER_NOTE = (
    "Inline one-flow view of the canonical comparison. Old text is struck red, "
    "inserted text is colored blue. Report only; nothing here changes the canonical "
    "result. See inline_pdf_audit.json for omission/join counts and limitations."
)

_BASE = ParagraphStyle(
    "Inline", parent=getSampleStyleSheet()["BodyText"], fontSize=10,
    leading=15, alignment=TA_LEFT, spaceAfter=6)
_TITLE = ParagraphStyle(
    "InlineTitle", parent=_BASE, fontName="Helvetica-Bold", fontSize=16, spaceAfter=4)
_HEADING = ParagraphStyle(
    "InlineHeading", parent=_BASE, fontName="Helvetica-Bold", fontSize=12,
    spaceBefore=10, spaceAfter=4)
_ID = ParagraphStyle("InlineId", parent=_BASE, fontName="Helvetica-Bold", spaceAfter=2)
_NOTE = ParagraphStyle(
    "InlineNote", parent=_BASE, fontSize=8, leading=11, textColor="#555555")
_WARN = ParagraphStyle(
    "InlineWarn", parent=_BASE, fontSize=9, leading=12, textColor=_WARN_COLOR)


def _escape(text: Any) -> str:
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _para(markup: str, style: ParagraphStyle = _BASE) -> Paragraph:
    return Paragraph(markup, style)


# ---------------------------------------------------------------------------
# Presentation-only, source-proven hyphenation cleanup
# ---------------------------------------------------------------------------

def _joined_word_set(text: Any) -> set[str]:
    """Words of one side as written, plus words obtained by joining its wraps.

    Used as the "opposite side" corroboration set: a candidate join in the other
    side is accepted only when its exact joined word appears here. Including the
    joined form of the opposite side is what makes a two-sided compound wrap
    (``Positi-\\nonen`` / ``Positio-\\nnen``) corroborated on both sides.
    """
    if not isinstance(text, str) or not text:
        return set()
    relaxed = _SOFT_LINE_RE.sub(
        lambda m: (m.group(1) or "") + (m.group(2) or ""), text
    )
    relaxed = _SOFT_PLAIN_RE.sub(
        lambda m: (m.group(1) or "") + (m.group(2) or ""), relaxed
    )
    words = set(_WORD_RE.findall(relaxed))
    for match in _NEWLINE_HYPHEN_RE.finditer(relaxed):
        words.add(match.group(1) + match.group(2))
    return words


def _clean_side(
    text: str, side: str, opposite_words: set[str]
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    joins: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []

    def soft_line(match: "re.Match[str]") -> str:
        joined = (match.group(1) or "") + (match.group(2) or "")
        joins.append({"side": side, "proof": "soft_discretionary", "joined": joined})
        return joined

    cleaned = _SOFT_LINE_RE.sub(soft_line, text)

    def soft_plain(match: "re.Match[str]") -> str:
        joined = (match.group(1) or "") + (match.group(2) or "")
        joins.append({"side": side, "proof": "soft_discretionary", "joined": joined})
        return joined

    cleaned = _SOFT_PLAIN_RE.sub(soft_plain, cleaned)

    def newline_hyphen(match: "re.Match[str]") -> str:
        left, right = match.group(1), match.group(2)
        if not right[:1].islower():
            # Uppercase/digit continuations are never a German wrap.
            return match.group(0)
        joined = left + right
        if joined in opposite_words:
            joins.append({"side": side, "proof": "newline_hyphen", "joined": joined})
            return joined
        uncertain.append({"side": side, "joined": joined})
        return match.group(0)

    cleaned = _NEWLINE_HYPHEN_RE.sub(newline_hyphen, cleaned)
    return cleaned, joins, uncertain


def clean_verified_joins(old_text: Any, new_text: Any) -> dict[str, Any]:
    """Return display copies with only source-proven hyphen joins applied.

    ``old``/``new`` are cleaned copies (never the originals). ``joins`` lists the
    applied joins (soft discretionary or newline hyphen corroborated by the
    opposite side). ``uncertain`` lists one-sided newline hyphens that were NOT
    corroborated and are therefore left visible in the text.
    """
    old_text = old_text if isinstance(old_text, str) else ""
    new_text = new_text if isinstance(new_text, str) else ""
    cleaned_old, joins_old, uncertain_old = _clean_side(
        old_text, "old", _joined_word_set(new_text)
    )
    cleaned_new, joins_new, uncertain_new = _clean_side(
        new_text, "new", _joined_word_set(old_text)
    )
    return {
        "old": cleaned_old,
        "new": cleaned_new,
        "joins": joins_old + joins_new,
        "uncertain": uncertain_old + uncertain_new,
    }


# ---------------------------------------------------------------------------
# One-flow inline alignment and markup
# ---------------------------------------------------------------------------

def _covering_token_spans(text: str) -> list[tuple[int, int]]:
    """Token spans plus the whitespace gaps between them, covering ``text``.

    The spans tile ``text`` exactly, so ``"".join(text[s:e] for s, e in spans)``
    reconstructs it. Aligning these covering spans (instead of bare tokens) is
    what keeps the whitespace around an insertion or replacement.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in TOKEN_PATTERN.finditer(text):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        spans.append((match.start(), match.end()))
        cursor = match.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


def _segment(text: str, spans: list[tuple[int, int]], start: int, end: int) -> str:
    if start >= end:
        return ""
    return text[spans[start][0]:spans[end - 1][1]]


def _strike(inner: str) -> str:
    return f'<strike><font color="{DELETED_COLOR}">{inner}</font></strike>'


def _insert(inner: str) -> str:
    return f'<u><font color="{INSERTED_COLOR}">{inner}</font></u>'


def _inline_markup(old_text: str, new_text: str) -> str:
    """Single-flow markup: shared context plain, old struck, new colored.

    Alignment runs over fully covering tokens (tokens plus whitespace gaps), so
    the old side (shared context + struck segments) and the new side (shared
    context + inserted segments) reconstruct exactly and no whitespace is lost.
    """
    if old_text == new_text:
        return _escape(old_text)
    old_spans = _covering_token_spans(old_text)
    new_spans = _covering_token_spans(new_text)
    old_tokens = [old_text[start:end] for start, end in old_spans]
    new_tokens = [new_text[start:end] for start, end in new_spans]
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Equal tokens have identical text on both sides; render once.
            parts.append(_escape(_segment(old_text, old_spans, i1, i2)))
            continue
        old_segment = _segment(old_text, old_spans, i1, i2)
        new_segment = _segment(new_text, new_spans, j1, j2)
        if old_segment:
            parts.append(_strike(_escape(old_segment)))
        if new_segment:
            parts.append(_insert(_escape(new_segment)))
    return "".join(parts)


def _flow(old_text: str, new_text: str, token_diff: Any) -> dict[str, Any]:
    clean = clean_verified_joins(old_text, new_text)
    reliable = reliable_operation_ranges(old_text, new_text, token_diff) is not None
    warnings: list[str] = []
    if not reliable and old_text != new_text:
        warnings.append(WARNING_MISSING_ALIGNMENT)
    if clean["uncertain"]:
        warnings.append(WARNING_UNPROVEN_JOIN)
    return {
        "inline": _inline_markup(clean["old"], clean["new"]),
        "warning": " ".join(warnings) if warnings else None,
        "reliable": reliable,
        "joins": len(clean["joins"]),
        "joins_detail": clean["joins"],
        "uncertain": clean["uncertain"],
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


def inline_change_markup(change: dict[str, Any]) -> dict[str, Any]:
    """Pure, testable markup for one change record (never mutates ``change``)."""
    old_text = (change.get("old") or {}).get("text") or ""
    new_text = (change.get("new") or {}).get("text") or ""
    result = _flow(old_text, new_text, change.get("token_diff"))
    result["header"] = _change_header(change)
    result["resolution"] = (change.get("resolution") or {}).get("status") or "review"
    return result


# ---------------------------------------------------------------------------
# Audit (omission/join counts and limitations)
# ---------------------------------------------------------------------------

def _side_text(record: Any, side: str) -> str:
    side_value = record.get(side) if isinstance(record, dict) else None
    if isinstance(side_value, dict):
        text = side_value.get("text")
        if isinstance(text, str):
            return text
    return ""


def _change_review_required(change: dict[str, Any]) -> bool:
    resolution = change.get("resolution") or {}
    if resolution.get("needs_review"):
        return True
    return (resolution.get("status") or "") == "review"


def _layout_only_verified(change: dict[str, Any]) -> bool:
    """True only for a safe, source-proven, layout-only hyphenation change."""
    old_text = _side_text(change, "old")
    new_text = _side_text(change, "new")
    if old_text == new_text:
        return False
    clean = clean_verified_joins(old_text, new_text)
    if not clean["joins"] or clean["old"] != clean["new"]:
        return False
    return reliable_operation_ranges(
        old_text, new_text, change.get("token_diff")) is not None


def _change_omittable(change: dict[str, Any]) -> bool:
    """A whole change may leave the flow only under the strict omission policy."""
    if _change_review_required(change):
        return False
    return _layout_only_verified(change)


def _cell_omittable(cell: dict[str, Any]) -> bool:
    """A cell is omitted only when labelled unchanged AND the raw text matches.

    The ``UNCHANGED`` classification is never trusted on its own: a cell whose
    old and new raw text differ is a source inconsistency that stays visible.
    """
    classified_unchanged = str(cell.get("classification") or "").upper() == "UNCHANGED"
    return classified_unchanged and _side_text(cell, "old") == _side_text(cell, "new")


def build_audit(comparison: dict[str, Any]) -> dict[str, Any]:
    """Deterministic report of joins/omissions; never mutates ``comparison``.

    Omission counts match what the renderer actually omits: a whole change only
    when :func:`_change_omittable` holds, and a cell only when
    :func:`_cell_omittable` holds.
    """
    comparison = comparison if isinstance(comparison, dict) else {}
    changes = comparison.get("changes") or []
    tables = comparison.get("table_changes") or []
    review = comparison.get("review_queue") or []

    join_records: list[dict[str, Any]] = []
    cell_join_records: list[dict[str, Any]] = []
    verified_change_ids: list[str] = []
    omitted_ids: list[str] = []
    unproven = 0
    for change in changes:
        old_text = _side_text(change, "old")
        new_text = _side_text(change, "new")
        clean = clean_verified_joins(old_text, new_text)
        for join in clean["joins"]:
            join_records.append({"change_id": change.get("change_id"), **join})
        unproven += len(clean["uncertain"])
        if clean["joins"]:
            verified_change_ids.append(change.get("change_id"))
        if _change_omittable(change):
            omitted_ids.append(change.get("change_id"))

    unchanged_cells_omitted = 0
    unchanged_cell_mismatches = 0
    for table in tables:
        table_id = table.get("table_change_id")
        for cell in table.get("cell_changes") or []:
            old_cell = _side_text(cell, "old")
            new_cell = _side_text(cell, "new")
            classified_unchanged = (
                str(cell.get("classification") or "").upper() == "UNCHANGED")
            if classified_unchanged and old_cell == new_cell:
                unchanged_cells_omitted += 1
                continue
            if classified_unchanged:
                # Labelled unchanged but the raw text differs: keep it visible.
                unchanged_cell_mismatches += 1
            clean = clean_verified_joins(old_cell, new_cell)
            for join in clean["joins"]:
                old_meta = cell.get("old") if isinstance(cell.get("old"), dict) else {}
                cell_join_records.append({
                    "table_change_id": table_id,
                    "cell_id": old_meta.get("cell_id"),
                    **join,
                })
            unproven += len(clean["uncertain"])

    totals = {
        "changes": len(changes),
        "table_changes": len(tables),
        "review_items": len(review),
        "verified_joins": len(join_records) + len(cell_join_records),
        "cell_verified_joins": len(cell_join_records),
        "layout_only_differences_omitted": len(omitted_ids),
        "layout_only_differences_omitted_ids": omitted_ids,
        "unchanged_cells_omitted": unchanged_cells_omitted,
        "unchanged_cell_mismatches": unchanged_cell_mismatches,
        "unproven_joins_left_visible": unproven,
        "verified_joins_change_ids": verified_change_ids,
    }
    return {
        "schema_version": "1.0.0",
        "artifact": "comparison_inline.pdf",
        "source_comparison_schema": comparison.get("schema_version"),
        "method": (
            "Report-only inline presentation with presentation-only, "
            "source-proven hyphenation cleanup."
        ),
        "totals": totals,
        "joins": join_records,
        "cell_joins": cell_join_records,
        "unresolved_review": {
            "count": len(review),
            "by_case_type": dict(Counter(str(item.get("case_type")) for item in review)),
            "csv": "review_queue_export.csv",
            "items": [
                {
                    "review_id": item.get("review_id"),
                    "case_type": item.get("case_type"),
                    "status": (item.get("resolution") or {}).get("status"),
                    "change_id": item.get("change_id"),
                }
                for item in review
            ],
        },
        "limitations": list(LIMITATIONS),
    }


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def _stats_block(audit: dict[str, Any]) -> list[Paragraph]:
    totals = audit["totals"]
    lines = (
        f"Changes: {totals['changes']}",
        f"Table changes: {totals['table_changes']}",
        f"Verified hyphen joins recorded: {totals['verified_joins']}",
        f"Layout-only changes omitted: {totals['layout_only_differences_omitted']}",
        f"Unchanged cell text omitted: {totals['unchanged_cells_omitted']}",
        f"Unproven hyphen joins left visible: {totals['unproven_joins_left_visible']}",
        f"Unresolved review items: {totals['review_items']}",
    )
    return [_para(line, _BASE) for line in lines]


def _render_change(change: dict[str, Any], story: list[Any]) -> None:
    flow = inline_change_markup(change)
    story.append(_para(flow["header"], _ID))
    story.append(_para(f"inline: {flow['inline']}", _BASE))
    if flow["joins"]:
        story.append(_para(
            f"Omitted {flow['joins']} verified layout-only hyphen join(s) here "
            "(see inline_pdf_audit.json).", _NOTE))
    if flow["warning"]:
        story.append(_para(f"WARNING: {_escape(flow['warning'])}", _WARN))


def _render_table(table: dict[str, Any], story: list[Any]) -> None:
    cid = table.get("table_change_id", "?")
    ctype = table.get("change_type", "CHANGED")
    classification = table.get("classification", "")
    old_meta = table.get("old") or {}
    new_meta = table.get("new") or {}
    dims = (
        f"old={old_meta.get('row_count', '?')}x{old_meta.get('column_count', '?')} "
        f"new={new_meta.get('row_count', '?')}x{new_meta.get('column_count', '?')}"
    )
    header = (
        f"[{_escape(cid)}] table {_escape(ctype)} "
        f"{_escape(classification)} {_escape(dims)}"
    )
    old_pages = old_meta.get("source_pages") or []
    new_pages = new_meta.get("source_pages") or []
    if old_pages or new_pages:
        header += f" | old pages {list(old_pages)} / new pages {list(new_pages)}"
    story.append(_para(header, _ID))

    one_sided = ctype in {"TABLE_DELETED", "TABLE_ADDED"}
    dimensions = (
        (old_meta.get("row_count") or 0) * (old_meta.get("column_count") or 0)
        + (new_meta.get("row_count") or 0) * (new_meta.get("column_count") or 0)
    )
    content_unavailable = (
        one_sided
        and dimensions > 0
        and (
            table.get("cell_content_status") == "unavailable"
            or "cell_changes" not in table
        )
    )
    if content_unavailable:
        story.append(_para(
            "Table cell content unavailable (legacy artifact); inspect source table.",
            _BASE))

    omitted_unchanged = 0
    mismatch_cells = 0
    for cell in table.get("cell_changes") or []:
        if _cell_omittable(cell):
            omitted_unchanged += 1
            continue
        old_cell_meta = cell.get("old") or {}
        new_cell_meta = cell.get("new") or {}
        old_cell = old_cell_meta.get("text", "") or ""
        new_cell = new_cell_meta.get("text", "") or ""
        cell_classification = cell.get("classification", "")
        classified_unchanged = str(cell_classification or "").upper() == "UNCHANGED"
        if classified_unchanged:
            # Labelled unchanged but the raw text differs: never trust the label.
            mismatch_cells += 1
            story.append(_para(
                f"WARNING: {_escape(WARNING_CELL_CLASSIFICATION_MISMATCH)}", _WARN))
        if cell_classification:
            label = f"cell {_escape(cell_classification)}"
            bits = []
            if old_cell_meta.get("cell_id"):
                bits.append(f"old {_escape(old_cell_meta['cell_id'])}"
                            + (f" p.{old_cell_meta.get('primary_page')}"
                               if old_cell_meta.get("primary_page") is not None else ""))
            if new_cell_meta.get("cell_id"):
                bits.append(f"new {_escape(new_cell_meta['cell_id'])}"
                            + (f" p.{new_cell_meta.get('primary_page')}"
                               if new_cell_meta.get("primary_page") is not None else ""))
            if bits:
                label += " | " + " / ".join(bits)
            story.append(_para(label, _BASE))
        flow = _flow(old_cell, new_cell, cell.get("token_diff"))
        story.append(_para(f"inline: {flow['inline']}", _BASE))
        if flow["warning"]:
            story.append(_para(f"WARNING: {_escape(flow['warning'])}", _WARN))
    if omitted_unchanged:
        story.append(_para(
            f"Omitted {omitted_unchanged} unchanged cell(s).", _NOTE))
    if mismatch_cells:
        story.append(_para(
            f"{mismatch_cells} cell(s) labelled UNCHANGED differ and were shown.",
            _NOTE))
    resolution = (table.get("resolution") or {}).get("status")
    if resolution:
        story.append(_para(f"status={_escape(resolution)}", _NOTE))


def _render_review(review: list[dict[str, Any]], story: list[Any]) -> None:
    story.append(_para("Unresolved review items", _HEADING))
    case_counts = Counter(str(item.get("case_type")) for item in review)
    summary = ", ".join(f"{name}={count}" for name, count in sorted(case_counts.items()))
    story.append(_para(
        f"{len(review)} open item(s). Full triage export: review_queue_export.csv. "
        f"By case type: {_escape(summary)}.", _NOTE))
    for item in review:
        rid = _escape(item.get("review_id", "?"))
        case_type = _escape(item.get("case_type", "unknown"))
        status = _escape(((item.get("resolution") or {}).get("status")) or "review")
        line = f"[{rid}] case_type={case_type} status={status}"
        if item.get("change_id"):
            line += f" change={_escape(item['change_id'])}"
        candidate = item.get("source_candidate_id")
        if candidate:
            line += f" candidate={_escape(candidate)}"
        story.append(_para(line, _BASE))


def render_pdf(comparison: dict[str, Any], output_path: Path) -> Path:
    """Render the inline one-flow PDF for ``comparison`` (report-only)."""
    comparison = comparison if isinstance(comparison, dict) else {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    changes = comparison.get("changes") or []
    tables = comparison.get("table_changes") or []
    review = comparison.get("review_queue") or []
    audit = build_audit(comparison)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        rightMargin=0.75 * inch, leftMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    story: list[Any] = [
        _para("Inline Comparison Redline", _TITLE),
        _para(_COVER_NOTE, _NOTE),
        Spacer(1, 0.1 * inch),
    ]
    story.extend(_stats_block(audit))
    story.append(_para("Limitations", _HEADING))
    for limitation in audit["limitations"]:
        story.append(_para(f"&bull; {_escape(limitation)}", _NOTE))
    story.append(Spacer(1, 0.2 * inch))

    if not changes and not tables and not review:
        story.append(_para(NO_DIFFERENCES_MESSAGE, _BASE))
        doc.build(story)
        return output_path

    if changes:
        story.append(_para("Changes", _HEADING))
        omitted_changes = 0
        for change in changes:
            if _change_omittable(change):
                omitted_changes += 1
                continue
            _render_change(change, story)
            story.append(Spacer(1, 0.1 * inch))
        if omitted_changes:
            story.append(_para(
                f"{omitted_changes} layout-only change(s) were verified hyphenation "
                "joins and are omitted from the flow (see inline_pdf_audit.json).",
                _NOTE))
    if tables:
        story.append(_para("Table changes", _HEADING))
        for table in tables:
            _render_table(table, story)
            story.append(Spacer(1, 0.1 * inch))
    if review:
        _render_review(review, story)

    doc.build(story)
    return output_path
