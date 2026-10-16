"""Repository/package and runtime-workspace path helpers (dependency-free).

This module knows where the checkout/package lives and where a runtime job
root lives; it never writes jobs into ``src/`` or site-packages. It depends on
neither Phase 1 nor Phase 2.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ``src/document_compare/shared/paths.py`` -> document_compare package, src, repo.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = SRC_ROOT.parent


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


def fail(message: str) -> None:
    print(f"STAGE FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)
