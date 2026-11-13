"""Turn an existing comparison workspace into a job the browser UI can open.

One command, no provider or network calls. The workspace is only read:
its ``output/`` folder and the two source PDFs are copied into a new
``workspace/jobs/<job_id>/`` folder and the job ID is printed.

    python -m document_compare.phase2.viewer.import_workspace
    python -m document_compare.phase2.viewer.import_workspace --workspace workspace/EBA_STEP2 \
        --old "input/old version.pdf" --new "input/new version.pdf"

Missing arguments are asked for interactively. When ``--old``/``--new`` are
omitted, ``<workspace>/input/document/{old,new}.pdf`` is used if present.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from document_compare.shared.paths import REPO_ROOT

REQUIRED = (
    "comparison_result.json",
    "old_document.json",
    "new_document.json",
    "old_document_structured.json",
    "new_document_structured.json",
)


class ImportError_(ValueError):
    """A workspace or PDF that cannot be imported; the message says why."""


def _clean(raw: str) -> Path:
    return Path(raw.strip().strip('"').strip("'")).expanduser()


def import_workspace(workspace: Path, old_pdf: Path, new_pdf: Path, jobs_root: Path) -> str:
    """Copy ``workspace/output`` and both PDFs into a new done job; return its ID."""
    workspace, old_pdf, new_pdf = Path(workspace), Path(old_pdf), Path(new_pdf)
    output = workspace / "output"
    if not output.is_dir():
        raise ImportError_(f"No output folder in {workspace} (run Phase 1 for it first).")
    missing = [name for name in REQUIRED if not (output / name).is_file()]
    if missing:
        raise ImportError_(f"{output} is missing: {', '.join(missing)}")
    for label, pdf in (("old", old_pdf), ("new", new_pdf)):
        if not pdf.is_file() or pdf.suffix.casefold() != ".pdf":
            raise ImportError_(f"{label} PDF not found or not a .pdf: {pdf}")
    if old_pdf.resolve() == new_pdf.resolve():
        raise ImportError_("old and new PDF are the same file")

    jobs_root = Path(jobs_root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    while True:
        job_id = secrets.token_hex(6)
        job_dir = jobs_root / job_id
        if not job_dir.exists():
            break
    try:
        shutil.copytree(output, job_dir / "output")
        document_dir = job_dir / "input" / "document"
        document_dir.mkdir(parents=True)
        shutil.copy2(old_pdf, document_dir / "old.pdf")
        shutil.copy2(new_pdf, document_dir / "new.pdf")
        status = {
            "job_id": job_id,
            "session_id": job_id,
            "state": "done",
            "stages_completed": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "old_name": old_pdf.name,
            "new_name": new_pdf.name,
            "imported_from": workspace.name,
        }
        (job_dir / "job_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    return job_id


def _ask(prompt: str, default: Path | None = None) -> Path:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if not answer and default:
        return default
    if not answer:
        raise ImportError_(f"{prompt} is required")
    return _clean(answer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make a browser-UI job from an existing workspace (copies, read-only).")
    parser.add_argument("--workspace", help="workspace folder that has output/comparison_result.json")
    parser.add_argument("--old", help="OLD source PDF used for that run")
    parser.add_argument("--new", help="NEW source PDF used for that run")
    parser.add_argument("--jobs-root", default=str(REPO_ROOT / "workspace" / "jobs"))
    args = parser.parse_args(argv)
    try:
        workspace = _clean(args.workspace) if args.workspace else _ask("Workspace folder (e.g. workspace/EBA_STEP2)")
        default_old = workspace / "input" / "document" / "old.pdf"
        default_new = workspace / "input" / "document" / "new.pdf"
        old_pdf = _clean(args.old) if args.old else _ask("OLD PDF", default_old if default_old.is_file() else None)
        new_pdf = _clean(args.new) if args.new else _ask("NEW PDF", default_new if default_new.is_file() else None)
        job_id = import_workspace(workspace, old_pdf, new_pdf, Path(args.jobs_root))
    except (ImportError_, OSError) as exc:
        print(f"Not imported: {exc}", file=sys.stderr)
        return 1
    print(f"Created job {job_id} from {workspace.name}. Open it in the UI (Open job tab):")
    print(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
