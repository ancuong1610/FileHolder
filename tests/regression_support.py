"""Task 4 test-only support: declarative DI fixtures + isolated pipeline runner.

This module is test infrastructure only. It never imports a stage algorithm
directly (the brief forbids importing s10) and never touches the network,
credentials, or a real provider. The pipeline is executed through the public
``crr_compare.cli`` runner in an isolated temporary workspace root with
``CRR_RUN_LLM=mock`` and ``CRR_DOMAIN_MODE=generic``.

Two committed fixture pairs are built from declarative specs:

- ``text_values``  -- wording/number changes that must survive to canonical
  output and redline text (30->60 days, a date, a percentage, a reference,
  an inserted/removed ``not``, and ``shall``->``may``).
- ``table_values`` -- table cases that must stay reviewable (changed cell,
  inserted/deleted/reordered row, changed column, repeated-header split,
  merged cells, extraction-uncertain artifact cell).
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from crr_compare import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "test_data" / "fixtures_regression"

FIXTURE_NAMES = ("text_values", "table_values")


# ---------------------------------------------------------------------------
# DI prebuilt-layout JSON construction
# ---------------------------------------------------------------------------

def _page(number: int) -> dict:
    return {
        "pageNumber": number,
        "angle": 0,
        "width": 8.5,
        "height": 11.0,
        "unit": "inch",
    }


def _polygon(top: float, bottom: float) -> list[float]:
    return [0.8, top, 7.7, top, 7.7, bottom, 0.8, bottom]


def _paragraph(content: str, role, page: int, top: float, offset: int) -> dict:
    return {
        "content": content,
        "role": role,
        "boundingRegions": [
            {"pageNumber": page, "polygon": _polygon(top, top + 0.3)}
        ],
        "spans": [{"offset": offset, "length": len(content)}],
    }


def _cell(
    row: int,
    col: int,
    text: str,
    page: int,
    top: float,
    *,
    kind: str | None = None,
    row_span: int = 1,
    col_span: int = 1,
) -> dict:
    return {
        "rowIndex": row,
        "columnIndex": col,
        "rowSpan": row_span,
        "columnSpan": col_span,
        "kind": kind,
        "content": text,
        "boundingRegions": [
            {"pageNumber": page, "polygon": _polygon(top, top + 0.3 * row_span)}
        ],
    }


def _table(page: int, header: list[str], rows: list[list[str]], *, top: float = 0.6) -> dict:
    cells = [
        _cell(0, col, text, page, top, kind="columnHeader")
        for col, text in enumerate(header)
    ]
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            cells.append(_cell(row_index, col, text, page, top + 0.4 * row_index))
    return {
        "rowCount": len(rows) + 1,
        "columnCount": len(header),
        "cells": cells,
        "boundingRegions": [
            {
                "pageNumber": page,
                "polygon": _polygon(top, top + 0.4 * (len(rows) + 1)),
            }
        ],
    }


# ---------------------------------------------------------------------------
# Text fixture
# ---------------------------------------------------------------------------

_RETENTION = (
    "Backups of critical systems shall be retained for a minimum of 30 days before rotation.",
    "Backups of critical systems shall be retained for a minimum of 60 days before rotation.",
)
_DATE = (
    "This policy takes effect on 15 March 2026 and is reviewed annually thereafter.",
    "This policy takes effect on 15 March 2027 and is reviewed annually thereafter.",
)
_PERCENTAGE = (
    "Each business unit shall allocate 20 percent of its operating budget to resilience measures.",
    "Each business unit shall allocate 35 percent of its operating budget to resilience measures.",
)
_REFERENCE = (
    "Personnel shall follow the requirements of Article 12 when handling restricted records.",
    "Personnel shall follow the requirements of Article 15 when handling restricted records.",
)
_NOT_ADDED = (
    "Records shall be released to third parties without prior review.",
    "Records shall not be released to third parties without prior review.",
)
_NOT_REMOVED = (
    "Temporary staff must not enter the server room unescorted.",
    "Temporary staff must enter the server room unescorted.",
)
_SHALL_TO_MAY = (
    "The board shall amend this policy following an incident review.",
    "The board may amend this policy following an incident review.",
)

# (name, old literal paragraph, new literal paragraph)
TEXT_CASES = (
    ("retention_days", _RETENTION[0], _RETENTION[1]),
    ("effective_date", _DATE[0], _DATE[1]),
    ("percentage", _PERCENTAGE[0], _PERCENTAGE[1]),
    ("reference", _REFERENCE[0], _REFERENCE[1]),
    ("not_added", _NOT_ADDED[0], _NOT_ADDED[1]),
    ("not_removed", _NOT_REMOVED[0], _NOT_REMOVED[1]),
    ("shall_to_may", _SHALL_TO_MAY[0], _SHALL_TO_MAY[1]),
)

# (role, old text, new text) in reading order
TEXT_PARAGRAPHS = (
    ("title", "Synthetic Regression Policy (offline)", "Synthetic Regression Policy (offline)"),
    ("sectionHeading", "Section 1: Retention", "Section 1: Retention"),
    (None, _RETENTION[0], _RETENTION[1]),
    ("sectionHeading", "Section 2: Effective Date", "Section 2: Effective Date"),
    (None, _DATE[0], _DATE[1]),
    ("sectionHeading", "Section 3: Allocation", "Section 3: Allocation"),
    (None, _PERCENTAGE[0], _PERCENTAGE[1]),
    ("sectionHeading", "Section 4: References", "Section 4: References"),
    (None, _REFERENCE[0], _REFERENCE[1]),
    ("sectionHeading", "Section 5: Access", "Section 5: Access"),
    (None, _NOT_ADDED[0], _NOT_ADDED[1]),
    (None, _NOT_REMOVED[0], _NOT_REMOVED[1]),
    ("sectionHeading", "Section 6: Amendments", "Section 6: Amendments"),
    (None, _SHALL_TO_MAY[0], _SHALL_TO_MAY[1]),
)


def _build_text_document(side: str) -> dict:
    content_parts: list[str] = []
    blocks: list[dict] = []
    offset = 0
    for index, (role, old_text, new_text) in enumerate(TEXT_PARAGRAPHS):
        text = old_text if side == "old" else new_text
        blocks.append(_paragraph(text, role, 1, 0.5 + 0.55 * index, offset))
        content_parts.append(text)
        offset += len(text)
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "stringIndexType": "unicodeCodePoint",
        "content": "".join(content_parts),
        "pages": [_page(1)],
        "paragraphs": blocks,
        "tables": [],
    }


# ---------------------------------------------------------------------------
# Table fixture
# ---------------------------------------------------------------------------

# Case metadata used by tests: (name, page, old column count, new column count)
TABLE_CASE_PAGES = {
    "changed_cell": 1,
    "inserted_row": 2,
    "deleted_row": 3,
    "reordered_row": 4,
    "changed_column": 5,
    "repeated_header_split": 6,
    "merged_cells": 9,
    "extraction_uncertain": 10,
}


def _table_cells_changed_cell(side: str) -> dict:
    value = "100 percent" if side == "old" else "150 percent"
    return _table(
        1,
        ["Asset class", "Risk weight", "Notes"],
        [["Sovereigns", "0 percent", "Standard"], ["Corporates", value, "Standard"]],
    )


def _table_cells_inserted_row(side: str) -> dict:
    rows = [["Wholesale", "100", "Approved"]]
    if side == "new":
        rows.append(["SME", "75", "Pending"])
    rows.append(["Retail", "50", "Approved"])
    return _table(2, ["Exposure", "Limit", "Comment"], rows)


def _table_cells_deleted_row(side: str) -> dict:
    rows = [["Gold", "10", "Active"]]
    if side == "old":
        rows.append(["Silver", "20", "Active"])
    rows.append(["Bronze", "30", "Retired"])
    return _table(3, ["Segment", "Threshold", "Status"], rows)


def _table_cells_reordered_row(side: str) -> dict:
    if side == "old":
        rows = [["Intake", "Alice", "Day 1"], ["Review", "Bob", "Day 5"], ["Publish", "Carol", "Day 9"]]
    else:
        rows = [["Publish", "Carol", "Day 9"], ["Intake", "Alice", "Day 1"], ["Review", "Bob", "Day 5"]]
    return _table(4, ["Stage", "Owner", "Deadline"], rows)


def _table_cells_changed_column(side: str) -> dict:
    if side == "old":
        return _table(5, ["Control", "Frequency", "Evidence"], [["Backup", "Daily", "Log"]])
    return _table(5, ["Control", "Frequency", "Currency", "Evidence"], [["Backup", "Daily", "EUR", "Log"]])


def _table_cells_repeated_header_split(side: str, page: int) -> list[dict]:
    if side == "old":
        return [_table(page, ["Region", "Volume"], [["North", "10"], ["South", "20"]])]
    return [
        _table(page, ["Region", "Volume"], [["North", "10"]]),
        _table(page + 1, ["Region", "Volume"], [["South", "20"]]),
    ]


def _table_cells_merged_cells(side: str) -> dict:
    page, top = 9, 0.6
    # Old merges row 1 into row 2 (rowSpan 2); new splits it into two cells.
    merged_span = 2 if side == "old" else 1
    cells = [
        _cell(0, 0, "Group", page, top, kind="columnHeader"),
        _cell(0, 1, "Value", page, top, kind="columnHeader"),
        _cell(1, 0, "Merged", page, top + 0.4, row_span=merged_span),
        _cell(1, 1, "Alpha", page, top + 0.4),
        _cell(3, 0, "Other", page, top + 1.2),
        _cell(3, 1, "Gamma", page, top + 1.2),
    ]
    if side == "new":
        cells.append(_cell(2, 0, "Merged", page, top + 0.8))
    cells.append(_cell(2, 1, "Beta", page, top + 0.8))
    return {
        "rowCount": 4,
        "columnCount": 2,
        "cells": cells,
        "boundingRegions": [{"pageNumber": page, "polygon": _polygon(top, top + 1.6)}],
    }


def _table_cells_extraction_uncertain(side: str) -> dict:
    description = (
        "Co-operating units are counted separately"
        if side == "old"
        else "Cooperating units are counted separately"
    )
    return _table(10, ["Item", "Description"], [["Widget", description]])


def _build_table_document(side: str) -> dict:
    tables = [
        _table_cells_changed_cell(side),
        _table_cells_inserted_row(side),
        _table_cells_deleted_row(side),
        _table_cells_reordered_row(side),
        _table_cells_changed_column(side),
        *_table_cells_repeated_header_split(side, TABLE_CASE_PAGES["repeated_header_split"]),
        _table_cells_merged_cells(side),
        _table_cells_extraction_uncertain(side),
    ]
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "stringIndexType": "unicodeCodePoint",
        "content": "",
        "pages": [_page(number) for number in range(1, 11)],
        "paragraphs": [],
        "tables": tables,
    }


def documents(kind: str) -> tuple[dict, dict]:
    if kind == "text_values":
        return _build_text_document("old"), _build_text_document("new")
    if kind == "table_values":
        return _build_table_document("old"), _build_table_document("new")
    raise ValueError(f"unknown fixture kind {kind!r}")


def write_all_fixtures() -> list[Path]:
    """Write every fixture pair to ``test_data/fixtures_regression/``."""
    written: list[Path] = []
    for kind in FIXTURE_NAMES:
        old, new = documents(kind)
        target = FIXTURE_ROOT / kind
        target.mkdir(parents=True, exist_ok=True)
        for side, doc in (("old", old), ("new", new)):
            path = target / f"{side}_di_raw.json"
            path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            written.append(path)
    return written


# ---------------------------------------------------------------------------
# Isolated offline pipeline runner
# ---------------------------------------------------------------------------

_STAGE_NUMBERS = [f"{number:02d}" for number in range(2, 16)]


@contextlib.contextmanager
def _offline_pipeline_env():
    keys = ("CRR_RUN_LLM", "CRR_DOMAIN_MODE", "CRR_PROJECT_ROOT")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["CRR_RUN_LLM"] = "mock"
    os.environ["CRR_DOMAIN_MODE"] = "generic"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _redline_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as document:
        raw = "\n".join(page.get_text() for page in document)
    return re.sub(r"\s+", " ", raw)


def _load_artifacts(root: Path) -> dict:
    out = root / "output"
    with (out / "review_queue_export.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle, delimiter=";"))
    return {
        "root": out,
        "canonical": json.loads((out / "comparison_result.json").read_text(encoding="utf-8")),
        "tables": json.loads((out / "table_comparison_result.json").read_text(encoding="utf-8")),
        "routing": json.loads((out / "review_routing.json").read_text(encoding="utf-8")),
        "manifest": json.loads(
            (out / "bu_comparison_review_manifest.json").read_text(encoding="utf-8")
        ),
        "phase2": json.loads((out / "phase2_change_index.json").read_text(encoding="utf-8")),
        "csv_rows": csv_rows,
        "redline": _redline_text(out / "comparison_redline.pdf"),
    }


def run_fixture(name: str) -> dict:
    """Run one committed fixture pair through stages 02-15 in a temp root."""
    fixture_dir = FIXTURE_ROOT / name
    with tempfile.TemporaryDirectory(prefix=f"crr-regression-{name}-") as directory:
        root = Path(directory) / "root"
        out = root / "output"
        out.mkdir(parents=True)
        for side in ("old", "new"):
            source = fixture_dir / f"{side}_di_raw.json"
            if not source.is_file():
                raise FileNotFoundError(
                    f"Missing committed regression fixture {source}. "
                    "Run tests.regression_support.write_all_fixtures()."
                )
            shutil.copyfile(source, out / f"{side}_di_raw.json")
        with _offline_pipeline_env(), contextlib.redirect_stdout(io.StringIO()):
            cli.run_stages(_STAGE_NUMBERS, root)
        return _load_artifacts(root)
