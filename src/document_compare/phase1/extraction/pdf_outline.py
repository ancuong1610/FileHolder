"""Versioned native PDF-outline prepass evidence (outline-first slice).

This module reads embedded PDF bookmarks with the existing PyMuPDF dependency
*before* any Document Intelligence request. It never substitutes an embedded
outline for a printed contents page, never invents source element ids, and never
changes extraction text or matching. Sidecars are optional evidence: a missing,
unreadable, stale or invalid outline falls back to the existing reconstruction
path instead of aborting valid DI extraction.

Source binding is byte-level. A sidecar stores the source PDF SHA-256, physical
page count and acquisition status. Stage 5 may consume entries only when the
extraction's own identity still matches; a filename is never sufficient.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pymupdf as fitz

OUTLINE_SIDECAR_VERSION = "1.0.0"
OUTLINE_SIDECAR_NAMES = {
    "old": "old_pdf_outline.json",
    "new": "new_pdf_outline.json",
}
VALID_STATUSES = ("available", "partial", "absent", "invalid", "read_failure")

LIMITATIONS = (
    "Embedded bookmarks are not a printed table of contents.",
    "Outline entries are evidence-bound hypotheses; they are not authoritative hierarchy.",
    "External and out-of-document destinations are diagnostics, never headings.",
    "A page label is preserved separately from the physical page index.",
    "Missing or stale outline evidence falls back to reconstruction.",
)


def sidecar_name(side: str) -> str:
    if side not in OUTLINE_SIDECAR_NAMES:
        raise ValueError(f"Unknown outline side {side!r}; expected 'old' or 'new'.")
    return OUTLINE_SIDECAR_NAMES[side]


def pdf_sha256(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with Path(pdf_path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _destination(dest: Any) -> dict[str, Any] | None:
    if not isinstance(dest, dict):
        return None
    point = dest.get("to")
    if point is not None and not isinstance(point, (list, tuple)):
        try:
            point = [float(point.x), float(point.y)]
        except (AttributeError, TypeError, ValueError):
            point = None
    elif isinstance(point, tuple):
        point = list(point)
    return {
        "kind": dest.get("kind"),
        "xref": dest.get("xref"),
        "page": dest.get("page"),
        "to": point,
        "zoom": dest.get("zoom"),
    }


def _destination_type(dest: dict[str, Any] | None) -> str:
    if not isinstance(dest, dict):
        return "unknown"
    if dest.get("to") is not None:
        return "xyz"
    if dest.get("page") is not None and dest.get("kind") in (1, None):
        return "fit"
    return "unknown"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_local_destination(destination: Any) -> tuple[bool, str | None]:
    """Validate one native bookmark destination.

    A local outline heading needs PyMuPDF's GoTo destination (``kind == 1``) or
    a stored destination that already normalized to a page without a kind. URI,
    launch, embedded-file and unknown link kinds are never reinterpreted as
    local headings, even when a parser supplied a positive page value.
    """
    if destination is None:
        return True, None
    if not isinstance(destination, dict):
        return False, "malformed_destination"
    kind = destination.get("kind")
    page = destination.get("page")
    if kind is not None and kind != 1:
        if isinstance(page, int) and not isinstance(page, bool) and page < 0:
            return False, "external_destination"
        return False, "nonlocal_destination"
    if page is not None and (not isinstance(page, int) or isinstance(page, bool)):
        return False, "malformed_destination"
    coordinates = destination.get("to")
    if coordinates is not None:
        if (
            not isinstance(coordinates, (list, tuple))
            or len(coordinates) != 2
            or not all(_is_number(value) and math.isfinite(value)
                       for value in coordinates)
        ):
            return False, "invalid_destination_coordinates"
    return True, None


def _entry_diagnostic(
    *, source_order: int, title: Any, reason: str, raw_page: Any = None,
    destination: Any = None,
) -> dict[str, Any]:
    diagnostic = {
        "source_order": source_order,
        "title": title if isinstance(title, str) else None,
        "reason": reason,
    }
    if raw_page is not None:
        diagnostic["page"] = raw_page
    if destination is not None:
        diagnostic["destination"] = destination
    return diagnostic


def validate_outline_entries(
    raw_entries: Any,
    *,
    side: str,
    source: dict[str, Any],
    page_labels: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Normalize raw bookmark rows into validated, page-bounded entries.

    ``raw_entries`` accepts PyMuPDF ``get_toc(simple=False)`` rows or already
    normalized mappings. Invalid/external destinations are diagnostics; only
    supported entries are returned as headings.
    """
    page_count = source.get("page_count")
    if not isinstance(page_count, int) or page_count < 1:
        raise ValueError("outline source page_count must be a positive integer.")
    page_labels = page_labels or {}
    entries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    rows = raw_entries if isinstance(raw_entries, list) else []
    for index, row in enumerate(rows):
        raw_page: Any = None
        destination: dict[str, Any] | None = None
        source_order = index
        stored_entry_id: str | None = None
        if isinstance(row, dict):
            candidate_order = row.get("source_order")
            if (
                isinstance(candidate_order, int)
                and not isinstance(candidate_order, bool)
                and candidate_order >= 0
            ):
                source_order = candidate_order
            candidate_id = row.get("entry_id")
            if isinstance(candidate_id, str):
                match = re.fullmatch(
                    rf"{re.escape(side.upper())}-OUTLINE-(\d{{4,}})",
                    candidate_id,
                )
                if match and int(match.group(1)) == source_order + 1:
                    stored_entry_id = candidate_id
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            level, title, raw_page = row[0], row[1], row[2]
            destination = _destination(row[3] if len(row) > 3 else None)
        elif isinstance(row, dict):
            level = row.get("level")
            title = row.get("title")
            raw_page = row.get("physical_page", row.get("page"))
            destination = row.get("destination")
        else:
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=None, reason="malformed_entry"))
            continue

        if not isinstance(level, int) or isinstance(level, bool) or level < 1:
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title, reason="invalid_level",
                raw_page=raw_page))
            continue
        if not isinstance(title, str) or not title.strip():
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title, reason="empty_title",
                raw_page=raw_page))
            continue

        local_ok, local_reason = _valid_local_destination(destination)
        if not local_ok:
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title, reason=local_reason,
                raw_page=raw_page, destination=destination))
            continue

        dest_page = destination.get("page") if isinstance(destination, dict) else None
        if isinstance(dest_page, bool) or not isinstance(dest_page, int):
            dest_page = None
        page = raw_page
        if isinstance(page, bool) or not isinstance(page, int):
            page = dest_page + 1 if dest_page is not None and dest_page >= 0 else None
        elif page == 0 and dest_page == 0:
            # PyMuPDF's simple rows are 1-based; a stored 0 with a 0-based
            # destination is normalized to the first physical page.
            page = 1
        if (
            isinstance(raw_page, int)
            and not isinstance(raw_page, bool)
            and raw_page >= 1
            and dest_page is not None
            and dest_page >= 0
            and dest_page + 1 != raw_page
        ):
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title,
                reason="destination_page_mismatch",
                raw_page=raw_page, destination=destination))
            continue

        if page is None or page < 1:
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title, reason="external_destination",
                raw_page=raw_page, destination=destination))
            continue
        if page > page_count:
            diagnostics.append(_entry_diagnostic(
                source_order=source_order, title=title, reason="destination_out_of_bounds",
                raw_page=raw_page, destination=destination))
            continue

        entries.append({
            "entry_id": stored_entry_id or f"{side.upper()}-OUTLINE-{source_order + 1:04d}",
            "source_order": source_order,
            "level": level,
            "title": title.strip(),
            "physical_page": page,
            "page_label": page_labels.get(page),
            "destination_type": _destination_type(destination),
            "destination": destination,
            "validated": True,
        })

    if not rows:
        status = "absent"
    elif not entries:
        status = "invalid"
    elif diagnostics:
        status = "partial"
    else:
        status = "available"
    return {
        "schema_version": OUTLINE_SIDECAR_VERSION,
        "side": side,
        "status": status,
        "source": source,
        "entries": entries,
        "diagnostics": diagnostics,
        "limitations": list(LIMITATIONS),
    }


def build_outline_evidence(pdf_path: Path, *, side: str) -> dict[str, Any]:
    """Read native bookmarks and return versioned evidence; never raise on read failure."""
    pdf_path = Path(pdf_path)
    source: dict[str, Any] = {
        "format": "pdf",
        "basename": pdf_path.name,
        "sha256": None,
        "page_count": None,
    }
    try:
        source["sha256"] = pdf_sha256(pdf_path)
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
            source["page_count"] = page_count
            page_labels = {index + 1: document.load_page(index).get_label()
                           for index in range(page_count)}
            raw_entries = document.get_toc(simple=False)
    except Exception as exc:  # optional evidence must not abort extraction
        return {
            "schema_version": OUTLINE_SIDECAR_VERSION,
            "side": side,
            "status": "read_failure",
            "source": source,
            "entries": [],
            "diagnostics": [{
                "reason": "outline_read_failure",
                "error_type": type(exc).__name__,
            }],
            "limitations": list(LIMITATIONS),
        }
    return validate_outline_entries(
        raw_entries,
        side=side,
        source=source,
        page_labels=page_labels,
    )


def save_outline_sidecar(evidence: dict[str, Any], output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / sidecar_name(evidence["side"])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def collect_outline(
    pdf_path: Path,
    *,
    side: str,
    output_dir: Path,
    expected_sha256: str | None = None,
    expected_page_count: int | None = None,
) -> dict[str, Any]:
    """Acquire and persist one optional sidecar. Sidecar write errors are non-fatal.

    When Stage 1 supplies the source identity it already computed, a second hash
    or page count that no longer matches is recorded as an identity mismatch so
    the sidecar can never be consumed against different PDF bytes. Stage 1 then
    fails the extraction loudly instead of binding stale outline evidence.
    """
    evidence = build_outline_evidence(pdf_path, side=side)
    source = evidence.get("source") or {}
    mismatch = (
        (expected_sha256 is not None and source.get("sha256") is not None
         and source.get("sha256") != expected_sha256)
        or (expected_page_count is not None
            and source.get("page_count") is not None
            and source.get("page_count") != expected_page_count)
    )
    if mismatch:
        evidence = dict(evidence)
        evidence["status"] = "read_failure"
        evidence["entries"] = []
        evidence["identity_mismatch"] = True
        evidence["diagnostics"] = list(evidence.get("diagnostics") or []) + [{
            "reason": "source_identity_mismatch_during_outline_prepass",
            "expected_sha256": expected_sha256,
            "stored_sha256": source.get("sha256"),
            "expected_page_count": expected_page_count,
            "stored_page_count": source.get("page_count"),
        }]
    try:
        save_outline_sidecar(evidence, output_dir)
    except OSError as exc:
        evidence = dict(evidence)
        evidence["diagnostics"] = list(evidence.get("diagnostics") or []) + [{
            "reason": "outline_sidecar_write_failure",
            "error_type": type(exc).__name__,
        }]
    return evidence


def load_outline_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _extraction_identity(document: dict[str, Any]) -> dict[str, Any] | None:
    source = (document.get("source") or {}) if isinstance(document, dict) else {}
    identity = source.get("extraction_source")
    if not isinstance(identity, dict):
        return None
    sha = identity.get("sha256")
    page_count = identity.get("page_count")
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(character not in "0123456789abcdef" for character in sha.lower())
        or not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
    ):
        return None
    return identity


def _unavailable(reason: str, *, side: str, diagnostics: list | None = None) -> dict[str, Any]:
    return {
        "schema_version": OUTLINE_SIDECAR_VERSION,
        "side": side,
        "status": "unavailable",
        "source": None,
        "entries": [],
        "diagnostics": list(diagnostics or []),
        "reason": reason,
        "limitations": list(LIMITATIONS),
    }


def resolve_outline_evidence(
    document: dict[str, Any], sidecar_path: Path
) -> dict[str, Any]:
    """Return usable sidecar evidence only for a matching extraction identity.

    A stale sidecar, a missing identity, or a malformed/absent file is never
    trusted. The returned entries are revalidated against the stored source; a
    sidecar is not authoritative merely because it exists in the workspace.
    """
    identity = _extraction_identity(document)
    if identity is None:
        return _unavailable("extraction_identity_unavailable", side=str(
            (document.get("source") or {}).get("side") or "old"))
    side = str(identity.get("side") or "")
    if side not in OUTLINE_SIDECAR_NAMES:
        side = "old" if Path(sidecar_path).name.startswith("old_") else "new"
    evidence = load_outline_sidecar(sidecar_path)
    if evidence is None:
        return _unavailable("outline_sidecar_missing", side=side)
    if evidence.get("schema_version") != OUTLINE_SIDECAR_VERSION:
        return _unavailable("outline_sidecar_unsupported_version", side=side)
    if evidence.get("side") not in (None, side):
        return _unavailable("outline_sidecar_side_mismatch", side=side)
    source = evidence.get("source")
    if not isinstance(source, dict):
        return _unavailable("outline_sidecar_unreadable", side=side)
    if (
        source.get("sha256") != identity.get("sha256")
        or source.get("page_count") != identity.get("page_count")
    ):
        return {
            "schema_version": OUTLINE_SIDECAR_VERSION,
            "side": side,
            "status": "stale",
            "source": source,
            "entries": [],
            "diagnostics": [{
                "reason": "outline_source_identity_mismatch",
                "expected_sha256": identity.get("sha256"),
                "stored_sha256": source.get("sha256"),
                "expected_page_count": identity.get("page_count"),
                "stored_page_count": source.get("page_count"),
            }],
            "reason": "outline_source_identity_mismatch",
            "limitations": list(LIMITATIONS),
        }
    status = evidence.get("status")
    if status not in VALID_STATUSES:
        return _unavailable("outline_sidecar_unreadable", side=side)
    if status in {"absent", "invalid", "read_failure"}:
        return {
            **evidence,
            "side": side,
            "entries": [],
            "reason": f"outline_{status}",
        }
    page_labels = {
        int(entry["physical_page"]): entry.get("page_label")
        for entry in evidence.get("entries") or []
        if isinstance(entry, dict) and isinstance(entry.get("physical_page"), int)
    }
    revalidated = validate_outline_entries(
        evidence.get("entries") or [],
        side=side,
        source=source,
        page_labels=page_labels,
    )
    if revalidated["diagnostics"] or (
        evidence.get("status") == "partial" and revalidated["entries"]
    ):
        revalidated["status"] = "partial"
    revalidated["diagnostics"] = list(evidence.get("diagnostics") or []) + list(
        revalidated["diagnostics"]
    )
    return revalidated
