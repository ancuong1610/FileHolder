"""Report-only character spans for the redline PDF.

Pure and side-effect free: this module never changes the canonical result, the
routing, the schemas, or any source text. It only turns the token operations
already present on a change/cell into character ranges the PDF redline can
style.

The stored offsets are applied only when they validate against the source
strings (via :func:`diff_annotations.reliable_operation_ranges`). On any
mismatch the whole diff falls back to a conservative ``SequenceMatcher``
alignment over the source text and everything is reported as a content
difference (red) with an explicit warning; a possible-wrap classification is
never attached to an unverified range.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from document_compare.phase1.reporting.annotations import reliable_operation_ranges

# Mirrors document_compare.phase1.stages.s08_candidates.TOKEN_PATTERN. That stage module has
# import-time file side effects, so the pattern is copied here instead of
# imported.
TOKEN_PATTERN = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž_][A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9_]*"
    r"(?:[./:-][A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9_]+)*"
    r"|\d+(?:[.,/]\d+)*(?:[-/]\d+)*"
    r"|==|!=|<=|>=|->|=>|:=|\+\+|--"
    r"|\w+"
    r"|[^\w\s]",
    re.UNICODE,
)

FALLBACK_WARNING = (
    "Alignment against the source text was not verified; all differences are "
    "shown in red."
)


def _token_spans(text: str) -> list[dict[str, int]]:
    return [
        {"start": match.start(), "end": match.end()}
        for match in TOKEN_PATTERN.finditer(text or "")
    ]


def _token_range(
    text: str, spans: list[dict[str, int]], start: int, end: int
) -> tuple[int, int]:
    if start == end:
        boundary = spans[start]["start"] if start < len(spans) else len(text)
        return (boundary, boundary)
    return (spans[start]["start"], spans[end - 1]["end"])


def _fallback_ranges(old_text: str, new_text: str) -> list[dict[str, Any]]:
    old_spans = _token_spans(old_text)
    new_spans = _token_spans(new_text)
    old_tokens = [old_text[item["start"]:item["end"]] for item in old_spans]
    new_tokens = [new_text[item["start"]:item["end"]] for item in new_spans]
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    ranges: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        ranges.append({
            "operation": tag,
            "old": _token_range(old_text, old_spans, i1, i2),
            "new": _token_range(new_text, new_spans, j1, j2),
        })
    return ranges


def build_diff_spans(
    old_text: Any,
    new_text: Any,
    token_diff: Any,
    classifications: Any = None,
) -> dict[str, Any]:
    """Character spans (and counts) for one change/cell.

    ``classifications`` is the per-operation classification list from
    :func:`diff_annotations.annotate_change`; a ``possible_wrap`` entry is only
    applied to a range that validated against the source. On any mismatch the
    whole diff falls back to a conservative token alignment and is reported as
    content (red) with a warning.
    """
    old_text = old_text if isinstance(old_text, str) else ""
    new_text = new_text if isinstance(new_text, str) else ""
    classification_list = (
        list(classifications) if isinstance(classifications, (list, tuple)) else []
    )

    result: dict[str, Any] = {
        "reliable": False,
        "alignment": "none",
        "warning": None,
        "old_spans": [],
        "new_spans": [],
        "content_count": 0,
        "wrap_count": 0,
    }
    if old_text == new_text:
        result["reliable"] = True
        return result

    ranges = reliable_operation_ranges(old_text, new_text, token_diff)
    if ranges is not None:
        result["reliable"] = True
        result["alignment"] = "offsets"
        for item in ranges:
            index = item["index"]
            kind = (
                "wrap"
                if index < len(classification_list)
                and classification_list[index] == "possible_wrap"
                else "content"
            )
            old_start, old_end = item["old"]
            new_start, new_end = item["new"]
            if old_end > old_start:
                result["old_spans"].append({
                    "start": old_start, "end": old_end, "kind": kind,
                    "operation": item["operation"],
                })
            if new_end > new_start:
                result["new_spans"].append({
                    "start": new_start, "end": new_end, "kind": kind,
                    "operation": item["operation"],
                })
            if kind == "wrap":
                result["wrap_count"] += 1
            else:
                result["content_count"] += 1
        return result

    # Offsets missing/mismatched: conservative report-only source alignment.
    result["alignment"] = "fallback"
    result["warning"] = FALLBACK_WARNING
    fallback = _fallback_ranges(old_text, new_text)
    for item in fallback:
        old_start, old_end = item["old"]
        new_start, new_end = item["new"]
        if old_end > old_start:
            result["old_spans"].append(
                {"start": old_start, "end": old_end, "kind": "content"}
            )
        if new_end > new_start:
            result["new_spans"].append(
                {"start": new_start, "end": new_end, "kind": "content"}
            )
        result["content_count"] += 1
    if not fallback:
        # Whitespace-only or otherwise unlocalizable difference: never hide it.
        result["old_spans"].append(
            {"start": 0, "end": len(old_text), "kind": "content"}
        )
        result["new_spans"].append(
            {"start": 0, "end": len(new_text), "kind": "content"}
        )
        result["content_count"] = 1
    return result
