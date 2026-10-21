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

from document_compare.shared.paths import find_project_root, output_dir, chunk_dir
from document_compare.shared.json_io import save_json as common_save_json
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.extraction.pdf_outline import collect_outline
from document_compare.phase1.contracts.validation import require

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


# Bumped whenever the element-reference rebasing semantics in
# merge_document_intelligence_chunks change. A cached merged artifact carrying a
# different (or absent) version is treated as legacy and remerged locally from
# the saved chunks rather than reused with stale chunk-local references.
REFERENCE_REBASING_VERSION = 1

_REFERENCE_POINTER_RE = re.compile(
    r"^/(?P<collection>[A-Za-z_][A-Za-z0-9_]*)/(?P<index>0|[1-9][0-9]*)(?P<suffix>(?:/.*)?)$"
)

_JSON_POINTER_INDEX_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")


def iter_reference_lists(value: Any, path: str = "$"):
    """Yield ``(json_path, elements_list)`` for every structured reference list.

    Document Intelligence stores element references as JSON pointers inside an
    ``elements`` list (table cells, figures, sections, key-value pairs and
    documents). Only those documented lists are visited; raw ``content``/text
    strings are never inspected, even when they look like pointers. A present
    ``elements`` key whose value is not a list is diagnosed rather than skipped.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key == "elements":
                if not isinstance(item, list):
                    raise RuntimeError(
                        f"Unsupported 'elements' value at {child}: expected a "
                        f"list of JSON-pointer strings, got "
                        f"{type(item).__name__} ({item!r})."
                    )
                yield child, item
            else:
                yield from iter_reference_lists(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_reference_lists(item, f"{path}[{index}]")


def decode_pointer_token(token: str, *, path: str, reference: Any) -> str:
    """Decode one RFC 6901 reference token (``~1`` -> ``/``, ``~0`` -> ``~``).

    An invalid escape (``~`` not followed by ``0`` or ``1``) is a diagnostic,
    never silently passed through.
    """
    if "~" not in token:
        return token
    decoded: list[str] = []
    position = 0
    while position < len(token):
        char = token[position]
        if char != "~":
            decoded.append(char)
            position += 1
            continue
        if position + 1 >= len(token) or token[position + 1] not in ("0", "1"):
            raise RuntimeError(
                f"Invalid JSON-pointer escape in reference {reference!r} at "
                f"{path}: token {token!r}; '~' must be followed by '0' or '1'."
            )
        decoded.append("~" if token[position + 1] == "0" else "/")
        position += 2
    return "".join(decoded)


def validate_reference_suffix(
    target: Any,
    suffix: str,
    *,
    reference: Any,
    path: str,
    scope: str,
) -> None:
    """Resolve a reference's JSON-pointer suffix against its target element.

    ``target`` is the item addressed by ``/collection/index``. An empty suffix
    addresses the item itself. Each remaining token is resolved as an object
    property (with ``~0``/``~1`` unescaping) or a valid non-negative array index;
    a missing property, an out-of-range or malformed index, an invalid escape, or
    descending into a scalar is a diagnostic naming ``scope``, the reference and
    the offending token. The suffix is never rewritten.
    """
    if suffix == "":
        return
    node = target
    travelled = ""
    for token in suffix.split("/")[1:]:
        decoded = decode_pointer_token(token, path=path, reference=reference)
        travelled = f"{travelled}/{token}"
        if isinstance(node, dict):
            if decoded not in node:
                raise RuntimeError(
                    f"{scope}: reference {reference!r} at {path} does not "
                    f"resolve: object has no property {decoded!r} (token "
                    f"{travelled!r})."
                )
            node = node[decoded]
        elif isinstance(node, list):
            if not _JSON_POINTER_INDEX_RE.match(decoded):
                raise RuntimeError(
                    f"{scope}: reference {reference!r} at {path} does not "
                    f"resolve: {decoded!r} in token {travelled!r} is not a valid "
                    f"array index."
                )
            index = int(decoded)
            if index >= len(node):
                raise RuntimeError(
                    f"{scope}: reference {reference!r} at {path} does not "
                    f"resolve: array index {index} is out of range for "
                    f"{len(node)} item(s) (token {travelled!r})."
                )
            node = node[index]
        else:
            raise RuntimeError(
                f"{scope}: reference {reference!r} at {path} does not resolve: "
                f"cannot descend into {type(node).__name__} at token "
                f"{travelled!r}."
            )



def parse_element_reference(reference: Any, *, path: str) -> tuple[str, int, str]:
    """Split ``/collection/index[/suffix...]`` into collection, index and suffix.

    Raises ``RuntimeError`` naming the JSON path and offending value for a
    non-string entry, a malformed pointer, or an unsupported target collection.
    It never guesses a target.
    """
    if not isinstance(reference, str):
        raise RuntimeError(
            f"Unsupported element reference at {path}: expected a JSON-pointer "
            f"string, got {type(reference).__name__} ({reference!r})."
        )
    match = _REFERENCE_POINTER_RE.match(reference)
    if not match:
        raise RuntimeError(
            f"Malformed element reference at {path}: {reference!r} is not a "
            f"'/<collection>/<index>' JSON pointer."
        )
    collection = match.group("collection")
    if collection not in MERGE_LIST_FIELDS:
        case_hint = ""
        if collection.lower() in {field.lower() for field in MERGE_LIST_FIELDS}:
            case_hint = " (check the collection name's letter case)"
        raise RuntimeError(
            f"Unsupported element reference target at {path}: {reference!r} "
            f"references {collection!r}, which is not one of the supported "
            f"collections {sorted(MERGE_LIST_FIELDS)}{case_hint}."
        )
    return collection, int(match.group("index")), match.group("suffix")


def rebase_chunk_element_references(
    chunk: dict[str, Any],
    *,
    before_lengths: dict[str, int],
    chunk_label: str,
) -> None:
    """Rebase one chunk's element references into the merged collections.

    Every pointer is validated against the chunk's own collections before any
    index is shifted, so a chunk-local reference that does not resolve is
    rejected instead of silently re-pointed. ``before_lengths`` holds the number
    of items already merged for each target collection; the first chunk has all
    zero offsets and therefore keeps its references byte-identical.
    """
    for path, references in iter_reference_lists(chunk):
        for position, reference in enumerate(references):
            reference_path = f"{path}[{position}]"
            collection, index, suffix = parse_element_reference(
                reference, path=reference_path
            )
            local_items = chunk.get(collection)
            local_length = len(local_items) if isinstance(local_items, list) else 0
            if index >= local_length:
                raise RuntimeError(
                    f"{chunk_label}: element reference {reference!r} at "
                    f"{reference_path} does not resolve in this chunk "
                    f"({collection} has {local_length} item(s))."
                )
            validate_reference_suffix(
                local_items[index],
                suffix,
                reference=reference,
                path=reference_path,
                scope=f"{chunk_label} (local target)",
            )
            offset = before_lengths[collection]
            if offset == 0:
                continue
            references[position] = f"/{collection}/{index + offset}{suffix}"


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

        # Element references are chunk-local, so rebase them against the
        # collections already merged before this chunk is appended. Each target
        # collection uses its own cumulative length.
        rebase_chunk_element_references(
            chunk,
            before_lengths={
                field: len(merged.get(field) or []) for field in MERGE_LIST_FIELDS
            },
            chunk_label=f"chunk {chunk_info['page_range']}",
        )

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
        "element_references_rebased": True,
        "reference_rebasing": True,
        "reference_rebasing_version": REFERENCE_REBASING_VERSION,
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

    reference_count = validate_element_references(raw, document_name=document_name)

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
        f"{document_name}: validated {expected_pages} unique ordered pages, "
        f"{len(list(iter_span_ranges(raw)))} in-bounds spans and "
        f"{reference_count} element references using {string_index_type} indexing."
    )


def validate_element_references(raw: dict[str, Any], *, document_name: str) -> int:
    """Verify every structured element reference resolves in the merged document.

    Returns the number of references checked. Malformed/unsupported pointers and
    out-of-range indices are collected as an ``AssertionError`` diagnostic rather
    than rewritten.
    """
    problems: list[str] = []
    checked = 0
    for path, references in iter_reference_lists(raw):
        for position, reference in enumerate(references):
            reference_path = f"{path}[{position}]"
            try:
                collection, index, suffix = parse_element_reference(
                    reference, path=reference_path
                )
            except RuntimeError as exc:
                problems.append(str(exc))
                continue
            items = raw.get(collection)
            length = len(items) if isinstance(items, list) else 0
            if index >= length:
                problems.append(
                    f"element reference {reference!r} at {reference_path} does "
                    f"not resolve in the merged document "
                    f"({collection} has {length} item(s))."
                )
                continue
            try:
                validate_reference_suffix(
                    items[index],
                    suffix,
                    reference=reference,
                    path=reference_path,
                    scope="merged document",
                )
            except RuntimeError as exc:
                problems.append(str(exc))
            checked += 1
    if problems:
        raise AssertionError(
            f"{document_name}: {len(problems)} invalid element reference(s). "
            f"First issues: {problems[:10]}"
        )
    return checked


def merged_has_element_references(raw: dict[str, Any]) -> bool:
    """True when the artifact contains at least one structured element reference."""
    return any(references for _path, references in iter_reference_lists(raw))


def cached_merge_reuse_blocker(merged: dict[str, Any]) -> str | None:
    """Return ``None`` when a cached merged artifact may be reused as-is.

    A cache is reusable when it holds no structured references (nothing could be
    stale) or when it carries this merger's reference-rebasing metadata. A cache
    with references but missing/legacy metadata may still hold chunk-local
    pointers and must be remerged from the saved chunks instead of reused.
    """
    if not merged_has_element_references(merged):
        return None
    metadata = merged.get("chunked_extraction")
    if not isinstance(metadata, dict):
        return "missing chunked_extraction reference-rebasing metadata"
    if metadata.get("reference_rebasing") is not True:
        return "legacy chunked_extraction metadata without reference rebasing"
    version = metadata.get("reference_rebasing_version")
    if version != REFERENCE_REBASING_VERSION:
        return (
            f"reference rebasing version {version!r} is not "
            f"{REFERENCE_REBASING_VERSION}"
        )
    return None


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
        blocker = cached_merge_reuse_blocker(merged)
        if blocker is None:
            validate_merged_extraction(
                merged, expected_pages=total_pages, document_name=document_name
            )
            print(f"{document_name}: reused compatible cached merge {final_output_path}")
            return merged
        # A legacy/stale final may hold chunk-local references. Rebuild it from
        # the already-saved chunks; this is local and issues no provider request.
        print(
            f"{document_name}: cached merge is not reusable ({blocker}); "
            "remerging from saved chunks (no provider request)."
        )

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

    debug_limit = debug_page_range()
    full_page_count = pdf_page_count(pdf_path)
    total_pages = debug_limit if debug_limit is not None else full_page_count

    sha = pdf_sha256(pdf_path)
    # Native outline evidence is optional and is collected before any DI
    # request, including when every chunk is already cached. A read failure is
    # recorded in the versioned sidecar and never aborts valid extraction.
    # Outline identity always describes the full source PDF, so a legacy debug
    # page subset keeps working without rebinding the sidecar to a truncated file.
    outline = collect_outline(
        pdf_path,
        side=label,
        output_dir=final_output_path.parent,
        expected_sha256=sha,
        expected_page_count=full_page_count,
    )
    if outline.get("identity_mismatch"):
        raise RuntimeError(
            f"{document_name}: source PDF bytes changed while collecting the "
            "outline prepass; refusing to bind stale outline evidence."
        )
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
    # Extraction identity is source-bound and travels with the merged model so
    # Stage 3 can carry it to Stage 5 without requiring the source PDF to remain
    # present. It is not a filename-based binding.
    merged["source_binding"] = {
        "format": "pdf",
        "sha256": sha,
        "page_count": full_page_count,
        "basename": pdf_path.name,
    }
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
