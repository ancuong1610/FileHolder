"""Stage 01 - Document Intelligence extraction (offline-first, chunked).

Reuses cached DI chunks under cache/di_chunks/<doc>/ and merges them into
output/{old,new}_di_raw.json. Azure is contacted only when a required chunk
is missing (requires .env with TIA_API_KEY/TIA_AZURE_ENDPOINT + source PDF).

When a source PDF is configured (CRR_OLD_PDF / CRR_NEW_PDF, defaulting to
<CRR_PROJECT_ROOT>/input/document/{old,new}.pdf), the document is extracted
directly from it: the full page count is read from the PDF (or limited to a
debug first-page range via CRR_DEBUG_PAGE_RANGE='1-N'), extraction is split
into <=100-page contiguous chunks, and chunks are cached under
cache/di_chunks/<pdf-sha256>/. Existing matching chunks are reused unless
CRR_FORCE_REEXTRACT=true/1/yes; each chunk is saved immediately so a failed
later chunk never deletes earlier successes. Without a PDF, the legacy
document-name cache is used unchanged.

Converted from 01_test_document_intelligence_chunked.ipynb with these fixes:
- pageNumber casing: DI returns camelCase pageNumber; page-number shifting,
  canonicalization and validation now handle both casings (was snake_case-only).
- Offline mode: page counts fall back to chunk-cache metadata when the source
  PDF is not present locally; credentials are only touched when extraction
  would actually run.
"""
import base64
import hashlib
import json
import os
import random
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from crr_compare.common import (
    find_project_root,
    get_page_number,
    set_page_number,
    output_dir,
    chunk_dir,
    save_json as common_save_json,
)
from crr_compare.validation import require

FORCE_CHUNK_EXTRACTION = False
FORCE_MERGE = True
MAX_EXTRACTION_ATTEMPTS = 1
INITIAL_RETRY_DELAY_SECONDS = 20
CHUNK_SIZE = 100

PROJECT_ROOT = find_project_root()
OUTPUT_DIR = output_dir(PROJECT_ROOT)
ENV_PATH = PROJECT_ROOT / ".env"

DOCUMENTS = [
    {
        "name": "old_crr_2024_01_09",
        "label": "old",
        "final_output": OUTPUT_DIR / "old_di_raw.json",
    },
    {
        "name": "new_crr_2024_07_09",
        "label": "new",
        "final_output": OUTPUT_DIR / "new_di_raw.json",
    },
]


def env_flag(name: str) -> bool:
    """True when an env var is set to a truthy value (true/1/yes, case-insensitive)."""
    return os.getenv(name, "").strip().lower() in {"true", "1", "yes"}


def configured_pdf_path(label: str) -> Path:
    """Resolve the source PDF for a document label.

    Uses CRR_OLD_PDF / CRR_NEW_PDF when set, otherwise the default
    <CRR_PROJECT_ROOT>/input/document/{old,new}.pdf.
    """
    key = "CRR_OLD_PDF" if label == "old" else "CRR_NEW_PDF"
    value = os.getenv(key, "").strip()
    if value:
        return Path(value).expanduser()
    return PROJECT_ROOT / "input" / "document" / f"{label}.pdf"


def pdf_sha256(pdf_path: Path) -> str:
    """Full-file SHA-256 used to key the extraction cache directory."""
    digest = hashlib.sha256()
    with pdf_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_ranges(total_pages: int) -> list[tuple[int, int]]:
    """Split 1..total_pages into contiguous <=CHUNK_SIZE page ranges."""
    ranges = []
    for start in range(1, total_pages + 1, CHUNK_SIZE):
        ranges.append((start, min(start + CHUNK_SIZE - 1, total_pages)))
    return ranges


def debug_page_range() -> int | None:
    """Return the debug first-page count from CRR_DEBUG_PAGE_RANGE ('1-N'), else None."""
    value = os.getenv("CRR_DEBUG_PAGE_RANGE", "").strip()
    if not value:
        return None
    match = re.fullmatch(r"1-(\d+)", value)
    if not match or int(match.group(1)) < 1:
        raise RuntimeError(
            f"CRR_DEBUG_PAGE_RANGE must be formatted '1-N' with N>=1, got {value!r}"
        )
    return int(match.group(1))

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def get_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code
    response = getattr(exc, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    return None


def is_retryable_azure_error(exc: Exception) -> bool:
    from azure.core.exceptions import (
        HttpResponseError,
        ServiceRequestError,
        ServiceResponseError,
    )

    if isinstance(exc, (ServiceRequestError, ServiceResponseError)):
        return True
    if isinstance(exc, HttpResponseError):
        return get_status_code(exc) in RETRYABLE_STATUS_CODES
    return False


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data: dict[str, Any], path: Path) -> None:
    common_save_json(data, path)


def extraction_stats(raw: dict[str, Any]) -> dict[str, int]:
    return {
        "pages": len(raw.get("pages") or []),
        "paragraphs": len(raw.get("paragraphs") or []),
        "tables": len(raw.get("tables") or []),
        "styles": len(raw.get("styles") or []),
    }


def chunk_files(document_name: str) -> list[Path]:
    directory = chunk_dir(PROJECT_ROOT, document_name)
    files = []
    for path in sorted(directory.glob("chunk_*_pages_*.json")):
        match = re.fullmatch(
            r"chunk_\d+_pages_(\d+)_(\d+)\.json", path.name
        )
        if match:
            files.append(path)
    return files


def detect_total_pages_from_chunks(document_name: str) -> int | None:
    ranges = []
    for path in chunk_files(document_name):
        match = re.fullmatch(
            r"chunk_\d+_pages_(\d+)_(\d+)\.json", path.name
        )
        if not match:
            continue
        ranges.append((int(match.group(1)), int(match.group(2))))
    if not ranges or sorted(ranges) != ranges:
        return None
    expected_start = 1
    total = 0
    for start, end in ranges:
        if start != expected_start:
            return None
        total = end
        expected_start = end + 1
    return total


def pdf_page_count(pdf_path: Path) -> int:
    import fitz

    with fitz.open(pdf_path) as document:
        return document.page_count


def ensure_client():
    """Create the DI client lazily; only needed for real extraction."""
    global _client
    if _client is not None:
        return _client

    try:
        from dotenv import load_dotenv
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK not installed; cannot extract missing chunks."
        ) from exc

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    api_key = os.getenv("TIA_API_KEY", "").strip()
    endpoint = os.getenv("TIA_AZURE_ENDPOINT", "").strip()
    if not api_key or not endpoint:
        raise RuntimeError(
            "Missing TIA_API_KEY / TIA_AZURE_ENDPOINT; cannot fetch missing chunks."
        )
    _client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential("."),
        headers={"api_key": api_key},
    )
    return _client


_client = None


def extract_pdf_range(pdf_path: Path, *, pages: str) -> dict[str, Any]:
    client = ensure_client()
    encoded = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
    print(f"Extracting {pdf_path.name} pages {pages} via Document Intelligence...")
    poller = client.begin_analyze_document(
        "prebuilt-layout",
        # AnalyzeDocumentRequest imported lazily alongside the client.
        __import__(
            "azure.ai.documentintelligence.models", fromlist=["AnalyzeDocumentRequest"]
        ).AnalyzeDocumentRequest(bytes_source=encoded),
        pages=pages,
    )
    result = poller.result()
    if hasattr(result, "as_dict"):
        return result.as_dict()
    if hasattr(result, "to_dict"):
        return result.to_dict()
    raise TypeError(f"Unsupported result type {type(result)!r}")


def extract_pdf_range_with_retry(
    pdf_path: Path,
    *,
    pages: str,
    max_attempts: int = 3,
    initial_delay_seconds: float = 2.0,
) -> dict[str, Any]:
    """Extract one page range, retrying only transient Azure failures.

    Makes at most ``max_attempts`` total calls, sleeping
    ``initial_delay_seconds`` then doubling the delay between attempts (2s then
    4s by default). No sleep happens after the final failure or for a
    non-retryable error (auth/config/4xx/value), which fails after one call.
    The raised context names the document and page range but never credential
    values; the original error is chained as ``__cause__``.
    """
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError(
            f"max_attempts must be a positive integer, got {max_attempts!r}."
        )

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return extract_pdf_range(pdf_path, pages=pages)
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts or not is_retryable_azure_error(exc):
                break
            time.sleep(initial_delay_seconds * (2 ** (attempt - 1)))

    pdf_name = getattr(pdf_path, "name", None) or str(pdf_path)
    raise RuntimeError(
        f"Extraction failed for {pdf_name} pages {pages} after {attempt} "
        f"attempt(s) ({type(last_error).__name__})."
    ) from last_error


MERGE_LIST_FIELDS = {
    "pages",
    "paragraphs",
    "tables",
    "styles",
    "figures",
    "key_value_pairs",
    "sections",
    "documents",
}


def get_string_index_type(raw: dict[str, Any]) -> str:
    index_type = raw.get("string_index_type") or raw.get("stringIndexType")
    if not isinstance(index_type, str) or not index_type.strip():
        raise RuntimeError(
            "Document Intelligence response does not declare "
            "string_index_type; span offsets cannot be merged safely."
        )
    return index_type.strip()


def indexed_text_length(text: str, index_type: str) -> int:
    normalized = index_type.replace("_", "").replace("-", "").lower()
    if normalized == "textelements":
        import regex

        return len(regex.findall(r"\X", text))
    if normalized == "unicodecodepoint":
        return len(text)
    if normalized == "utf16codeunit":
        return len(text.encode("utf-16-le")) // 2
    raise ValueError(f"Unsupported DI string index type: {index_type!r}")


def shift_page_numbers(value: Any, offset: int) -> Any:
    """Shift page numbers in both camelCase and snake_case spellings."""
    if offset == 0:
        return value
    if isinstance(value, dict):
        shifted: dict[str, Any] = {}
        for key, item in value.items():
            if key in ("page_number", "pageNumber") and isinstance(item, int):
                shifted[key] = item + offset
            else:
                shifted[key] = shift_page_numbers(item, offset)
        return shifted
    if isinstance(value, list):
        return [shift_page_numbers(item, offset) for item in value]
    return value


def shift_span_offsets(value: Any, offset: int) -> Any:
    if offset == 0:
        return value
    if isinstance(value, dict):
        shifted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "offset" and isinstance(item, int):
                shifted[key] = item + offset
            else:
                shifted[key] = shift_span_offsets(item, offset)
        return shifted
    if isinstance(value, list):
        return [shift_span_offsets(item, offset) for item in value]
    return value


def iter_span_ranges(value: Any, path: str = "$"):
    if isinstance(value, dict):
        offset = value.get("offset")
        length = value.get("length")
        if isinstance(offset, int) and isinstance(length, int):
            yield path, offset, length
        for key, item in value.items():
            yield from iter_span_ranges(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_span_ranges(item, f"{path}[{index}]")


def merge_document_intelligence_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        raise ValueError("No chunk results supplied.")

    index_types = [get_string_index_type(c["result"]) for c in chunks]
    normalized_index_types = {v.replace("_", "").replace("-", "").lower() for v in index_types}
    if len(normalized_index_types) != 1:
        raise RuntimeError(f"Chunks use different string index types: {sorted(set(index_types))}")

    string_index_type = index_types[0]
    separator = "\n"
    merged = deepcopy(chunks[0]["result"])

    for field in MERGE_LIST_FIELDS:
        merged[field] = []
    merged["content"] = ""
    current_content_offset = 0

    for chunk_info in chunks:
        chunk = deepcopy(chunk_info["result"])
        start_page = chunk_info["start_page"]
        end_page = chunk_info["end_page"]
        chunk_pages = chunk.get("pages") or []

        if not chunk_pages:
            raise RuntimeError(f"Chunk {chunk_info['page_range']} contains no page objects.")
        expected_chunk_pages = end_page - start_page + 1
        if len(chunk_pages) != expected_chunk_pages:
            raise RuntimeError(
                f"Chunk {chunk_info['page_range']} contains {len(chunk_pages)} page objects; "
                f"expected {expected_chunk_pages}."
            )

        returned_numbers = [
            n for n in (get_page_number(page) for page in chunk_pages) if n is not None
        ]

        # Some SDK/proxy responses restart nested page numbering at 1 per chunk.
        uses_local_page_numbers = (
            start_page != 1 and bool(returned_numbers) and min(returned_numbers) == 1
        )
        if uses_local_page_numbers:
            chunk = shift_page_numbers(chunk, start_page - 1)

        # Canonicalize top-level page numbers from the page range itself.
        for page_index, page in enumerate(chunk.get("pages") or []):
            set_page_number(page, start_page + page_index)

        chunk_content = chunk.get("content") or ""

        if merged["content"] and chunk_content:
            merged["content"] += separator
            current_content_offset += indexed_text_length(separator, string_index_type)

        chunk = shift_span_offsets(chunk, current_content_offset)

        for field in MERGE_LIST_FIELDS:
            merged.setdefault(field, [])
            merged[field].extend(chunk.get(field) or [])

        merged["content"] += chunk_content
        current_content_offset += indexed_text_length(chunk_content, string_index_type)

    merged["chunked_extraction"] = {
        "enabled": True,
        "chunk_count": len(chunks),
        "page_ranges": [c["page_range"] for c in chunks],
        "string_index_type": string_index_type,
        "span_offsets_rebased": True,
        "content_separator": separator,
    }
    return merged


def validate_merged_extraction(
    raw: dict[str, Any],
    *,
    expected_pages: int,
    document_name: str,
) -> None:
    pages = raw.get("pages") or []
    string_index_type = get_string_index_type(raw)
    content_index_length = indexed_text_length(raw.get("content") or "", string_index_type)

    invalid_spans = [
        (path, offset, length)
        for path, offset, length in iter_span_ranges(raw)
        if offset < 0 or length < 0 or offset + length > content_index_length
    ]
    if invalid_spans:
        raise AssertionError(
            f"{document_name}: {len(invalid_spans)} span(s) outside merged content. "
            f"First issues: {invalid_spans[:10]}"
        )

    page_numbers = [get_page_number(page) for page in pages]
    if len(pages) != expected_pages:
        raise AssertionError(
            f"{document_name}: merged result has {len(pages)} pages; expected {expected_pages}."
        )
    if any(number is None for number in page_numbers):
        raise AssertionError(f"{document_name}: some pages have no page number.")
    if len(page_numbers) != len(set(page_numbers)):
        duplicates = sorted({n for n in page_numbers if page_numbers.count(n) > 1})
        raise AssertionError(f"{document_name}: duplicate page numbers found: {duplicates[:20]}")
    if page_numbers != list(range(1, expected_pages + 1)):
        raise AssertionError(
            f"{document_name}: page numbers are not the complete ordered range "
            f"1-{expected_pages}. First values: {page_numbers[:10]}"
        )
    print(
        f"{document_name}: validated {expected_pages} unique ordered pages and "
        f"{len(list(iter_span_ranges(raw)))} in-bounds spans using {string_index_type} indexing."
    )


def extract_merge_or_load_document(doc: dict[str, Any]) -> dict[str, Any]:
    document_name = doc["name"]
    final_output_path = doc["final_output"]
    directory = chunk_dir(PROJECT_ROOT, document_name)
    files = chunk_files(document_name)

    if not files:
        raise RuntimeError(
            f"No cached DI chunks for '{document_name}' under {directory}. "
            "Provide cached chunks or run with credentials + source PDF."
        )

    total_pages = detect_total_pages_from_chunks(document_name)
    if total_pages is None:
        raise RuntimeError(
            f"Cached chunks for '{document_name}' do not form a contiguous range "
            "starting at page 1."
        )

    chunks = []
    for path in files:
        match = re.fullmatch(r"chunk_(\d+)_pages_(\d+)_(\d+)\.json", path.name)
        chunk_number, start_page, end_page = map(int, match.groups())
        result = load_json(path)
        stats = extraction_stats(result)
        expected = end_page - start_page + 1
        if stats["pages"] != expected:
            raise RuntimeError(
                f"Cached chunk {path.name} holds {stats['pages']} pages; expected {expected}."
            )
        chunks.append(
            {
                "chunk_number": chunk_number,
                "start_page": start_page,
                "end_page": end_page,
                "page_range": f"{start_page}-{end_page}",
                "path": str(path),
                "result": result,
            }
        )
        print(f"{document_name}: loaded cached chunk {path.name} ({stats['pages']} pages)")

    if final_output_path.exists() and not FORCE_MERGE:
        merged = load_json(final_output_path)
        validate_merged_extraction(merged, expected_pages=total_pages, document_name=document_name)
        return merged

    merged = merge_document_intelligence_chunks(chunks)
    validate_merged_extraction(merged, expected_pages=total_pages, document_name=document_name)
    save_json(merged, final_output_path)
    print(f"Saved merged extraction: {final_output_path}")
    return merged


def extract_merge_or_load_document_from_pdf(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract a document from its configured source PDF, chunked and cached.

    The cache directory is keyed by the full PDF SHA-256. Existing matching
    chunks are reused unless CRR_FORCE_REEXTRACT is set. Each chunk is saved
    immediately, so a failed later chunk never deletes earlier successes.
    """
    document_name = doc["name"]
    label = doc["label"]
    final_output_path = doc["final_output"]
    pdf_path = configured_pdf_path(label)

    if not pdf_path.exists():
        raise RuntimeError(
            f"{document_name}: source PDF not found at {pdf_path}. "
            "Set CRR_OLD_PDF / CRR_NEW_PDF or place the PDF at the default path."
        )

    total_pages = debug_page_range()
    if total_pages is None:
        total_pages = pdf_page_count(pdf_path)

    sha = pdf_sha256(pdf_path)
    directory = chunk_dir(PROJECT_ROOT, sha)
    force = env_flag("CRR_FORCE_REEXTRACT")
    ranges = chunk_ranges(total_pages)

    chunks = []
    for chunk_number, (start_page, end_page) in enumerate(ranges, start=1):
        page_range = f"{start_page}-{end_page}"
        path = directory / f"chunk_{chunk_number:03d}_pages_{start_page:04d}_{end_page:04d}.json"
        if path.exists() and not force:
            result = load_json(path)
            stats = extraction_stats(result)
            expected = end_page - start_page + 1
            if stats["pages"] != expected:
                raise RuntimeError(
                    f"{document_name}: cached chunk {path.name} holds {stats['pages']} "
                    f"pages; expected {expected}."
                )
            print(f"{document_name}: loaded cached chunk {path.name} ({stats['pages']} pages)")
        else:
            try:
                result = extract_pdf_range_with_retry(pdf_path, pages=page_range)
            except Exception as exc:
                raise RuntimeError(
                    f"{document_name}: failed to extract {pdf_path.name} pages "
                    f"{page_range}: {exc}"
                ) from exc
            save_json(result, path)
            print(f"{document_name}: saved chunk {path.name} ({page_range})")
        chunks.append(
            {
                "chunk_number": chunk_number,
                "start_page": start_page,
                "end_page": end_page,
                "page_range": page_range,
                "path": str(path),
                "result": result,
            }
        )

    merged = merge_document_intelligence_chunks(chunks)
    validate_merged_extraction(merged, expected_pages=total_pages, document_name=document_name)
    save_json(merged, final_output_path)
    print(f"Saved merged extraction: {final_output_path}")
    return merged


def run() -> None:
    results = {}
    for doc in DOCUMENTS:
        pdf_path = configured_pdf_path(doc["label"])
        if pdf_path.exists():
            results[doc["label"]] = extract_merge_or_load_document_from_pdf(doc)
        else:
            results[doc["label"]] = extract_merge_or_load_document(doc)

    for label, raw in results.items():
        stats = extraction_stats(raw)
        print(label.upper(), stats)
        require(
            stats["pages"] >= 1,
            f"{label.upper()}: extraction produced no pages ({stats}).",
        )

    print("Stage 01 complete.")


if __name__ == "__main__":
    run()
