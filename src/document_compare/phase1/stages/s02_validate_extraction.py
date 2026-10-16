"""Stage 02: validate Document Intelligence extraction before schema build.

Input  : ``<root>/output/old_di_raw.json`` and ``new_di_raw.json`` (Step-1
         already-extracted DI results); optional source PDFs selected by
         ``CRR_OLD_PDF`` / ``CRR_NEW_PDF`` for a native-text cross-check.

Work   : Validates span, page-sequence, polygon, table, and confidence
         integrity; inspects paragraph structure and recurring margin
         candidates; and compares DI text against the native PDF text when
         a PDF is available (skipped otherwise).

Output : ``<root>/output/old_extraction_validation.json`` and
         ``new_extraction_validation.json`` plus console diagnostics.

Review : ``requires_review`` is set for probable missing DI text and
         page-count mismatches; the report is consumed as Step-3 evidence
         and never gates the pipeline automatically.

The module is import-safe: importing it only defines the pure validation
helpers and never resolves a workspace, reads files, prints, or mutates the
environment. File orchestration lives in :func:`main`, which the
``__name__ == "__main__"`` guard runs for the CLI stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number

# ---- imports and configuration constants ----

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable

try:
    import pymupdf
except ImportError:  # Compatibility with older PyMuPDF installations
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None  # native-PDF cross-check is optional; skipped when None

SHOW_TEXT_SAMPLES = False
MAX_TEXT_SAMPLES = 20

LOW_CONFIDENCE_THRESHOLD = 0.97

# Diagnostic warnings: visible in the report, but not automatic review gates.
NATIVE_COVERAGE_WARNING_THRESHOLD = 0.95
TOKEN_COUNT_DELTA_WARNING_THRESHOLD = 0.10

# Manual-review gate: both signals must indicate probable missing DI text.
NATIVE_REVIEW_COVERAGE_THRESHOLD = 0.80
NATIVE_DI_DEFICIT_THRESHOLD = 0.20

HEADER_ZONE_RATIO = 0.12
FOOTER_ZONE_RATIO = 0.12
MIN_RECURRING_PAGES = 5
MIN_RECURRING_PAGE_RATIO = 0.05


# ---- workspace configuration ----
def configured_pdf_path(label: str) -> Path:
    """Resolve the source PDF for a document label (CRR_OLD_PDF / CRR_NEW_PDF)."""
    key = "CRR_OLD_PDF" if label == "old" else "CRR_NEW_PDF"
    value = os.getenv(key, "").strip()
    if value:
        return Path(value).expanduser()
    return find_project_root() / "input" / "document" / f"{label}.pdf"


def document_config(project_root: Path) -> dict[str, dict[str, Path]]:
    """Per-document input/output paths for one pipeline workspace root."""
    output_dir = project_root / "output"
    return {
        "old": {
            "raw": output_dir / "old_di_raw.json",
            "pdf": configured_pdf_path("old"),
            "report": output_dir / "old_extraction_validation.json",
        },
        "new": {
            "raw": output_dir / "new_di_raw.json",
            "pdf": configured_pdf_path("new"),
            "report": output_dir / "new_extraction_validation.json",
        },
    }


# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    # Accept direct analyze results and the common REST wrapper without changing either file.
    if isinstance(payload.get("analyzeResult"), dict):
        return payload["analyzeResult"]
    return payload


# ---- document-level diagnostics ----
def document_summary(raw: dict[str, Any]) -> dict[str, Any]:
    pages = raw.get("pages") or []
    return {
        "api_version": raw.get("apiVersion"),
        "model_id": raw.get("modelId"),
        "string_index_type": raw.get("stringIndexType"),
        "content_characters": len(raw.get("content") or ""),
        "pages": len(pages),
        "paragraphs": len(raw.get("paragraphs") or []),
        "tables": len(raw.get("tables") or []),
        "styles": len(raw.get("styles") or []),
        "page_units": sorted({p.get("unit") for p in pages if p.get("unit")}),
    }


# ---- span integrity ----
def make_span_slicer(content: str, index_type: str | None) -> tuple[Callable[[int, int], str] | None, int | None]:
    if index_type in (None, "unicodeCodePoint"):
        return (lambda offset, length: content[offset : offset + length]), len(content)

    if index_type == "utf16CodeUnit":
        encoded = content.encode("utf-16-le")

        def utf16_slice(offset: int, length: int) -> str:
            start = offset * 2
            end = (offset + length) * 2
            return encoded[start:end].decode("utf-16-le", errors="replace")

        return utf16_slice, len(encoded) // 2

    if index_type == "textElements":
        try:
            import regex
        except ImportError:
            return None, None
        elements = regex.findall(r"\X", content)
        return (lambda offset, length: "".join(elements[offset : offset + length])), len(elements)

    return None, None


def iter_nodes(value: Any, path: str = "root"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_nodes(child, f"{path}[{index}]")


def validate_spans(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content") or ""
    slicer, indexed_length = make_span_slicer(content, raw.get("stringIndexType"))
    stats = Counter()
    issue_paths: list[str] = []

    for path, node in iter_nodes(raw):
        spans = node.get("spans")
        if not isinstance(spans, list):
            continue

        for span_index, span in enumerate(spans):
            stats["spans_checked"] += 1
            offset = span.get("offset") if isinstance(span, dict) else None
            length = span.get("length") if isinstance(span, dict) else None
            span_path = f"{path}.spans[{span_index}]"

            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 0:
                stats["invalid_span_values"] += 1
                issue_paths.append(span_path)
                continue

            if indexed_length is not None and offset + length > indexed_length:
                stats["out_of_bounds_spans"] += 1
                issue_paths.append(span_path)
                continue

            expected = node.get("content")
            if slicer is not None and isinstance(expected, str) and len(spans) == 1:
                stats["exact_content_checks"] += 1
                if slicer(offset, length) != expected:
                    stats["content_mismatches"] += 1
                    issue_paths.append(path)
            elif isinstance(expected, str):
                stats["exact_content_checks_skipped"] += 1

    stats["issue_count"] = len(issue_paths)
    return {"counts": dict(stats), "sample_issue_paths": issue_paths[:50]}


def validate_page_sequence(raw: dict[str, Any]) -> dict[str, Any]:
    numbers = [p.get("pageNumber") for p in (raw.get("pages") or [])]
    expected = list(range(1, len(numbers) + 1))
    return {
        "page_numbers": numbers,
        "sequential_from_one": numbers == expected,
        "duplicates": sorted(n for n, count in Counter(numbers).items() if count > 1),
        "missing": sorted(set(expected) - set(numbers)),
    }


# ---- polygon integrity ----
def polygon_points(polygon: Any) -> list[tuple[float, float]] | None:
    if not isinstance(polygon, list) or len(polygon) < 6 or len(polygon) % 2:
        return None
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in polygon):
        return None
    return list(zip(polygon[::2], polygon[1::2]))


def iter_nodes_with_page(value: Any, path: str = "root", inherited_page: int | None = None):
    if isinstance(value, dict):
        current_page = value.get("pageNumber", inherited_page)
        yield path, value, current_page
        for key, child in value.items():
            yield from iter_nodes_with_page(child, f"{path}.{key}", current_page)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_nodes_with_page(child, f"{path}[{index}]", inherited_page)


def validate_polygons(raw: dict[str, Any], tolerance: float = 1e-6) -> dict[str, Any]:
    page_by_number = {p.get("pageNumber"): p for p in (raw.get("pages") or [])}
    stats = Counter()
    issue_paths: list[str] = []

    for path, node, inherited_page in iter_nodes_with_page(raw):
        polygon = node.get("polygon")
        if polygon is None:
            continue
        stats["polygons_checked"] += 1
        points = polygon_points(polygon)
        if points is None:
            stats["invalid_polygon_shape"] += 1
            issue_paths.append(path)
            continue

        page_number = node.get("pageNumber", inherited_page)
        if page_number is None:
            regions = node.get("boundingRegions") or []
            page_number = regions[0].get("pageNumber") if regions else None
        page = page_by_number.get(page_number)

        if not page:
            stats["unknown_page"] += 1
            issue_paths.append(path)
            continue

        width, height = page.get("width"), page.get("height")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            stats["missing_page_dimensions"] += 1
            continue

        if any(x < -tolerance or y < -tolerance or x > width + tolerance or y > height + tolerance for x, y in points):
            stats["out_of_page_bounds"] += 1
            issue_paths.append(path)

    stats["issue_count"] = len(issue_paths)
    return {"counts": dict(stats), "sample_issue_paths": issue_paths[:50]}


# ---- structure inspection ----
STRUCTURE_PATTERNS = {
    # Neutral structure statistics only. Legal/regulatory heading vocabulary
    # (Artikel/Article, Kapitel/Chapter, Abschnitt/Section, Teil/Part,
    # Anhang/Anlage/Annex) is deliberately excluded: extraction validation is
    # document-kind agnostic, and DI paragraph roles (title, sectionHeading,
    # ...) already carry generic structure identity.
    "numbered_paragraph": re.compile(
        r"^\s*(?:\(\d+[a-z]?\)|\d+(?:\.\d+)*[.)])\s+",
        re.IGNORECASE,
    ),
    "toc_line": re.compile(r".{3,}(?:\.{3,}|\s{3,})\s*\d+\s*$"),
    # Require at least two uppercase letters; this removes pure dates/numbers.
    "all_caps_heading": re.compile(
        r"^(?=(?:.*[A-ZÄÖÜ]){2})[^a-zäöüß]{3,120}$"
    ),
}


def page_of(node: dict[str, Any]) -> int | None:
    regions = node.get("boundingRegions") or []
    return regions[0].get("pageNumber") if regions else node.get("pageNumber")


def safe_sample(text: str) -> str:
    if SHOW_TEXT_SAMPLES:
        return re.sub(r"\s+", " ", text).strip()[:220]
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def inspect_structure(raw: dict[str, Any]) -> dict[str, Any]:
    role_counts = Counter()
    pattern_counts = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for paragraph in raw.get("paragraphs") or []:
        text = paragraph.get("content") or ""
        role = paragraph.get("role") or "<none>"
        role_counts[role] += 1
        for name, pattern in STRUCTURE_PATTERNS.items():
            if pattern.search(text):
                pattern_counts[name] += 1
                if len(samples[name]) < MAX_TEXT_SAMPLES:
                    samples[name].append({"page": page_of(paragraph), "sample": safe_sample(text)})

    return {
        "role_counts": dict(role_counts),
        "pattern_counts": dict(pattern_counts),
        "samples": dict(samples),
    }


# ---- recurring margin analysis ----
def normalize_recurring_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().casefold()
    text = re.sub(r"\b(?:page\s+)?\d+\s*(?:of|/)\s*\d+\b", "<page>", text)
    text = re.sub(r"\bpage\s+\d+\b", "<page>", text)
    text = re.sub(r"\s+", " ", text)
    return text


def vertical_zone(line: dict[str, Any], page: dict[str, Any]) -> str | None:
    points = polygon_points(line.get("polygon"))
    height = page.get("height")
    if not points or not isinstance(height, (int, float)) or height <= 0:
        return None
    center_y = mean(y for _, y in points)
    if center_y <= height * HEADER_ZONE_RATIO:
        return "header"
    if center_y >= height * (1 - FOOTER_ZONE_RATIO):
        return "footer"
    return None


def recurring_margin_candidates(raw: dict[str, Any]) -> dict[str, Any]:
    pages_in_document = len(raw.get("pages") or [])
    minimum_occurrences = max(
        MIN_RECURRING_PAGES,
        math.ceil(pages_in_document * MIN_RECURRING_PAGE_RATIO),
    )

    occurrences: dict[tuple[str, str], set[int]] = defaultdict(set)
    for page in raw.get("pages") or []:
        page_number = page.get("pageNumber")
        for line in page.get("lines") or []:
            zone = vertical_zone(line, page)
            text = normalize_recurring_text(line.get("content") or "")
            if zone and text:
                occurrences[(zone, text)].add(page_number)

    candidates = []
    for (zone, text), pages in occurrences.items():
        if len(pages) >= minimum_occurrences:
            candidates.append({
                "zone": zone,
                "page_count": len(pages),
                "pages": sorted(pages),
                "sample": safe_sample(text),
            })
    return {
        "minimum_occurrences": minimum_occurrences,
        "candidate_count": len(candidates),
        "candidates": sorted(candidates, key=lambda row: (-row["page_count"], row["zone"])),
    }


# ---- table integrity ----
def validate_tables(raw: dict[str, Any]) -> dict[str, Any]:
    stats = Counter()
    issues: list[dict[str, Any]] = []

    for table_index, table in enumerate(raw.get("tables") or []):
        stats["tables_checked"] += 1
        rows = table.get("rowCount")
        columns = table.get("columnCount")
        occupied: dict[tuple[int, int], int] = {}
        table_issues: set[str] = set()

        if not isinstance(rows, int) or rows <= 0 or not isinstance(columns, int) or columns <= 0:
            table_issues.add("invalid_dimensions")
        if not table.get("boundingRegions"):
            table_issues.add("missing_location")

        for cell_index, cell in enumerate(table.get("cells") or []):
            stats["cells_checked"] += 1
            row = cell.get("rowIndex")
            column = cell.get("columnIndex")
            row_span = cell.get("rowSpan", 1)
            column_span = cell.get("columnSpan", 1)

            if not all(isinstance(v, int) for v in (row, column, row_span, column_span)) or row_span < 1 or column_span < 1:
                table_issues.add("invalid_cell_indices_or_spans")
                continue
            if isinstance(rows, int) and isinstance(columns, int) and (row < 0 or column < 0 or row + row_span > rows or column + column_span > columns):
                table_issues.add("cell_out_of_bounds")
                continue

            for r in range(row, row + row_span):
                for c in range(column, column + column_span):
                    if (r, c) in occupied:
                        table_issues.add("overlapping_cells")
                    occupied[(r, c)] = cell_index

        if table_issues:
            stats["tables_with_issues"] += 1
            issues.append({
                "table_index": table_index,
                "page": page_of(table),
                "issues": sorted(table_issues),
            })

    return {"counts": dict(stats), "issues": issues[:100]}


# ---- confidence and scale ----
def confidence_and_scale(raw: dict[str, Any]) -> dict[str, Any]:
    page_rows = []
    all_confidences: list[float] = []

    for page in raw.get("pages") or []:
        values = [
            float(word["confidence"])
            for word in (page.get("words") or [])
            if word.get("confidence") is not None
        ]
        all_confidences.extend(values)
        page_rows.append({
            "page": page.get("pageNumber"),
            "words": len(page.get("words") or []),
            "lines": len(page.get("lines") or []),
            "mean_word_confidence": mean(values) if values else None,
            "low_confidence_words": sum(v < LOW_CONFIDENCE_THRESHOLD for v in values),
        })

    return {
        "pages": len(page_rows),
        "words": sum(row["words"] for row in page_rows),
        "lines": sum(row["lines"] for row in page_rows),
        "mean_word_confidence": mean(all_confidences) if all_confidences else None,
        "minimum_word_confidence": min(all_confidences) if all_confidences else None,
        "low_confidence_words": sum(v < LOW_CONFIDENCE_THRESHOLD for v in all_confidences),
        "page_metrics": page_rows,
    }


# ---- native-text cross-check ----
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def normalized_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return TOKEN_PATTERN.findall(text)


def di_page_text(page: dict[str, Any]) -> str:
    lines = page.get("lines") or []
    if lines:
        return "\n".join(line.get("content") or "" for line in lines)
    return " ".join(word.get("content") or "" for word in (page.get("words") or []))


def token_overlap(di_tokens: list[str], native_tokens: list[str]) -> tuple[float | None, float | None]:
    if not native_tokens and not di_tokens:
        return 1.0, 1.0
    shared = sum((Counter(di_tokens) & Counter(native_tokens)).values())
    native_coverage = shared / len(native_tokens) if native_tokens else None
    di_coverage = shared / len(di_tokens) if di_tokens else None
    return native_coverage, di_coverage


def validate_native_text(raw: dict[str, Any], pdf_path: Path) -> dict[str, Any]:
    if not pdf_path.exists():
        return {"available": False, "reason": "pdf_not_found", "pages": []}

    di_pages = {p.get("pageNumber"): p for p in (raw.get("pages") or [])}
    page_results = []

    with pymupdf.open(pdf_path) as pdf:
        for page_index in range(len(pdf)):
            page_number = page_index + 1
            native_text = pdf[page_index].get_text("text") or ""
            di_text = di_page_text(di_pages.get(page_number, {}))
            native_tokens = normalized_tokens(native_text)
            di_tokens = normalized_tokens(di_text)
            native_coverage, di_coverage = token_overlap(di_tokens, native_tokens)
            delta_denominator = max(len(native_tokens), 1)
            token_count_delta = abs(len(di_tokens) - len(native_tokens)) / delta_denominator

            warning_flags = []
            review_flags = []

            di_token_deficit_ratio = (
                max(len(native_tokens) - len(di_tokens), 0) / len(native_tokens)
                if native_tokens
                else None
            )

            # Serious extraction problem
            if native_tokens and not di_tokens:
                review_flags.append("di_text_missing_on_native_text_page")

            # Diagnostic differences — retained for analysis
            if (
                native_tokens
                and native_coverage is not None
                and native_coverage < NATIVE_COVERAGE_WARNING_THRESHOLD
            ):
                warning_flags.append("low_native_text_coverage")

            if (
                native_tokens
                and token_count_delta > TOKEN_COUNT_DELTA_WARNING_THRESHOLD
            ):
                warning_flags.append("large_token_count_delta")

            # Manual review only when both signals indicate likely missing DI text
            if (
                native_tokens
                and di_tokens
                and native_coverage is not None
                and native_coverage < NATIVE_REVIEW_COVERAGE_THRESHOLD
                and di_token_deficit_ratio is not None
                and di_token_deficit_ratio > NATIVE_DI_DEFICIT_THRESHOLD
            ):
                review_flags.append("probable_missing_di_text")

            page_results.append({
                "page": page_number,
                "native_text_available": bool(native_tokens),
                "di_token_count": len(di_tokens),
                "pymupdf_token_count": len(native_tokens),
                "native_text_coverage": native_coverage,
                "di_text_coverage": di_coverage,
                "token_count_delta_ratio": token_count_delta if native_tokens else None,
                "di_token_deficit_ratio": di_token_deficit_ratio,
                "warning_flags": warning_flags,
                "review_flags": review_flags,
                "anomaly_flags": warning_flags + review_flags,
                "requires_review": bool(review_flags),
            })

        page_count_match = len(pdf) == len(di_pages)

    return {
        "available": True,
        "pdf_page_count": len(page_results),
        "di_page_count": len(di_pages),
        "page_count_match": page_count_match,
        "pages_requiring_review": sum(row["requires_review"] for row in page_results),
        "pages": page_results,
    }


# ---- validation report ----
def build_validation_report(label: str, raw: dict[str, Any], *, native: dict[str, Any] | None = None, documents: dict[str, dict[str, Path]] | None = None) -> dict[str, Any]:
    native = validate_native_text(raw, configured_pdf_path(label)) if native is None else native
    documents = document_config(find_project_root()) if documents is None else documents

    page_check = validate_page_sequence(raw)
    span_check = validate_spans(raw)
    polygon_check = validate_polygons(raw)
    table_check = validate_tables(raw)
    structure = inspect_structure(raw)
    margins = recurring_margin_candidates(raw)
    scale = confidence_and_scale(raw)

    document_flags = []
    if not page_check["sequential_from_one"]:
        document_flags.append("non_sequential_or_duplicate_pages")
    if span_check["counts"].get("issue_count", 0):
        document_flags.append("span_integrity_issues")
    if polygon_check["counts"].get("issue_count", 0):
        document_flags.append("polygon_integrity_issues")
    if table_check["counts"].get("tables_with_issues", 0):
        document_flags.append("table_integrity_issues")
    if native.get("available") and not native.get("page_count_match"):
        document_flags.append("pdf_di_page_count_mismatch")
    if native.get("pages_requiring_review", 0):
        document_flags.append("native_text_review_pages")

    # Deliberately exclude structure samples and all document text.
    return {
        "document": label,
        "source_raw_file": documents[label]["raw"].name,
        "source_pdf_file": documents[label]["pdf"].name,
        "thresholds": {
            "low_confidence": LOW_CONFIDENCE_THRESHOLD,
            "native_coverage_warning": NATIVE_COVERAGE_WARNING_THRESHOLD,
            "token_count_delta_warning": TOKEN_COUNT_DELTA_WARNING_THRESHOLD,
            "native_review_coverage": NATIVE_REVIEW_COVERAGE_THRESHOLD,
            "native_di_deficit_review": NATIVE_DI_DEFICIT_THRESHOLD,
            "header_zone_ratio": HEADER_ZONE_RATIO,
            "footer_zone_ratio": FOOTER_ZONE_RATIO,
            "minimum_recurring_pages": MIN_RECURRING_PAGES,
            "minimum_recurring_page_ratio": MIN_RECURRING_PAGE_RATIO,
        },
        "summary": document_summary(raw),
        "page_integrity": page_check,
        "span_integrity": span_check,
        "polygon_integrity": polygon_check,
        "table_integrity": table_check,
        "structure_counts": {
            "role_counts": structure["role_counts"],
            "pattern_counts": structure["pattern_counts"],
            "recurring_margin_minimum_occurrences": margins["minimum_occurrences"],
            "recurring_margin_candidate_count": margins["candidate_count"],
        },
        "confidence_and_scale": scale,
        "native_text_validation": native,
        "document_flags": document_flags,
        "requires_review": bool(document_flags),
    }


# ---- stage entry point ----
def main() -> None:
    """Run Stage 02 against the workspace and save both validation reports."""
    project_root = find_project_root()
    documents = document_config(project_root)

    for label, paths in documents.items():
        if not paths["raw"].exists():
            raise FileNotFoundError(
                f"Missing {paths['raw']}. Run 01_test_document_intelligence_chunked_v2.ipynb first."
            )
        if not paths["pdf"].exists():
            print(f"{label.upper()}: PDF not found at {paths['pdf']}; PyMuPDF cross-check will be skipped.")

    print(f"Project root: {project_root}")

    raw_documents = {
        label: load_json(paths["raw"])
        for label, paths in documents.items()
    }

    for label, raw in raw_documents.items():
        print(label.upper(), document_summary(raw))

    for label, raw in raw_documents.items():
        print(label.upper(), "pages", validate_page_sequence(raw))
        print(label.upper(), "spans", validate_spans(raw)["counts"])

    for label, raw in raw_documents.items():
        print(label.upper(), validate_polygons(raw)["counts"])

    for label, raw in raw_documents.items():
        result = inspect_structure(raw)
        print(label.upper(), "roles", result["role_counts"])
        print(label.upper(), "patterns", result["pattern_counts"])
        if SHOW_TEXT_SAMPLES:
            print(result["samples"])

    for label, raw in raw_documents.items():
        result = recurring_margin_candidates(raw)
        print(
            label.upper(),
            "recurring margin candidates:",
            result["candidate_count"],
            "minimum occurrences:",
            result["minimum_occurrences"],
        )
        if SHOW_TEXT_SAMPLES:
            for candidate in result["candidates"]:
                print(candidate)

    for label, raw in raw_documents.items():
        print(label.upper(), validate_tables(raw)["counts"])

    for label, raw in raw_documents.items():
        result = confidence_and_scale(raw)
        print(label.upper(), {k: v for k, v in result.items() if k != "page_metrics"})

    native_validation = {
        label: validate_native_text(raw_documents[label], paths["pdf"])
        for label, paths in documents.items()
    }

    for label, result in native_validation.items():
        print(label.upper(), {k: v for k, v in result.items() if k != "pages"})

    reports = {
        label: build_validation_report(label, raw, native=native_validation[label], documents=documents)
        for label, raw in raw_documents.items()
    }

    for label, report in reports.items():
        report_path = documents[label]["report"]
        with report_path.open("w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(f"{label.upper()}: wrote {report_path.name}; flags={report['document_flags']}")


if __name__ == "__main__":
    main()