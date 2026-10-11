"""Shared helpers for the CRR comparison pipeline stages."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent


def find_project_root(start: Path | None = None) -> Path:
    """Resolve the pipeline workspace root.

    Priority: CRR_PROJECT_ROOT env var, then --root passed via cli (stored in
    CRR_PROJECT_ROOT by the runner), then <repo>/workspace.
    """
    env = os.environ.get("CRR_PROJECT_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = REPO_ROOT / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def input_dir(root: Path) -> Path:
    path = root / "input" / "document"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir(root: Path) -> Path:
    path = root / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chunk_dir(root: Path, document_name: str) -> Path:
    path = root / "cache" / "di_chunks" / document_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def page_number_of(page: dict):
    """DI page number regardless of camelCase/snake_case serialization."""
    value = page.get("pageNumber")
    if not isinstance(value, int):
        value = page.get("page_number")
    return value if isinstance(value, int) else None


def set_page_number(page: dict, value: int) -> None:
    page["pageNumber"] = value
    page["page_number"] = value


def get_page_number(item: dict) -> int | None:
    """Read a page number from any DI-ish object written by this pipeline."""
    for key in ("pageNumber", "page_number"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return None


def fail(message: str) -> None:
    print(f"STAGE FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)
