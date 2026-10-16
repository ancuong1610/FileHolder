"""Comparison job service (FastAPI).

Run:
    .venv/bin/uvicorn document_compare.phase2.api.service:app --port 8321

Endpoints:
  POST /jobs            upload old+new PDFs + session_id -> job id (202)
  GET  /jobs/{id}       status + stage progress (alias of /status)
  GET  /jobs/{id}/status                job status
  GET  /jobs/{id}/summary               derived current-run summary
  GET  /jobs/{id}/report                complete canonical comparison result
  GET  /jobs/{id}/artifacts            artifact list + relative paths
  GET  /jobs/{id}/artifacts/{name}     download an artifact (redline html, index, ...)
  GET  /jobs/{id}/changes              canonical changes, optional filters
  GET  /jobs/{id}/review-queue.csv    BU triage export
  GET  /jobs/{id}/read/summary         bounded read summary (safe for unfinished jobs)
  GET  /jobs/{id}/read/search          typed literal search + cursor pagination
  GET  /jobs/{id}/read/details         canonical values + bounded document context

Jobs run in a background thread via `python -m document_compare run` inside an
isolated job workspace (workspace/jobs/<id>/), so a job can never corrupt
the interactive workspace.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse

from document_compare.phase2.reader import result_reader as reader
from document_compare.shared.paths import REPO_ROOT
from document_compare.phase2.api.summary import build_summary

app = FastAPI(title="Document Comparison API", version="0.1.0")

JOBS_ROOT = REPO_ROOT / "workspace" / "jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

ARTIFACT_NAMES = {
    "comparison_result.json",
    "phase2_change_index.json",
    "bu_comparison_redline.html",
    "review_queue_export.csv",
    "table_comparison_result.json",
    "llm_review_decisions.json",
    "release_audit.json",
    "release_audit_packet.json",
    "comparison_redline.pdf",
    "comparison_inline.pdf",
    "inline_pdf_audit.json",
    "bu_comparison_review_manifest.json",
}

STAGE_ORDER = [f"{i:02d}" for i in range(1, 16)]

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _validate_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(
            400, "session_id must be 1-128 characters using only [A-Za-z0-9_-]"
        )


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(404, "Unknown job")
    return JOBS_ROOT / job_id


def _read_job(job_id: str) -> dict:
    with _jobs_lock:
        if job_id in _jobs:
            return _jobs[job_id]
    status_path = _job_dir(job_id) / "job_status.json"
    if status_path.exists():
        return json.loads(status_path.read_text(encoding="utf-8"))
    raise HTTPException(404, f"Unknown job {job_id}")


def _persist(job_id: str, status: dict) -> None:
    path = _job_dir(job_id) / "job_status.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for attempt in range(3):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.1)
                
    with _jobs_lock:
        _jobs[job_id] = status


def _run_pipeline(job_id: str) -> None:
    root = _job_dir(job_id)
    status = _read_job(job_id)
    status["state"] = "running"
    status["started_at"] = datetime.now(timezone.utc).isoformat()
    started_perf = time.perf_counter()
    _persist(job_id, status)

    lines = []
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "document_compare", "run", "--root", str(root)],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            lines = lines[-100:]
            match = re.search(r"--- stage (\d{2}) finished", line)
            if match:
                status = _read_job(job_id)
                status["stages_completed"] = [*status.get("stages_completed", []), match.group(1)]
                _persist(job_id, status)
        returncode = process.wait()
        status = _read_job(job_id)
        status["returncode"] = returncode
        if returncode == 0:
            _load_canonical(job_id)
            status["state"] = "done"
        else:
            status["state"] = "failed"
            status["error_tail"] = "".join(lines)[-4000:]
    except Exception as exc:
        status = _read_job(job_id)
        status["state"] = "failed"
        status["error_tail"] = f"{type(exc).__name__}: {exc}"
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    status["duration_seconds"] = round(time.perf_counter() - started_perf, 3)
    _persist(job_id, status)


def _store_pdf(target: Path, upload: UploadFile) -> None:
    size = 0
    first_bytes = b""
    with target.open("wb") as handle:
        while chunk := upload.file.read(1024 * 1024):
            if not first_bytes:
                first_bytes = chunk[:5]
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_BYTES} bytes")
            handle.write(chunk)
    if first_bytes != b"%PDF-":
        raise HTTPException(400, "Upload is not a PDF (missing %PDF- signature)")


def _load_canonical(job_id: str) -> dict:
    path = _job_dir(job_id) / "output" / "comparison_result.json"
    if not path.exists():
        raise HTTPException(404, "Job not finished or failed")
    try:
        canonical = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Comparison result is malformed") from exc
    if not isinstance(canonical, dict) or canonical.get("comparison_stage") != "phase_1_final_comparison":
        raise HTTPException(409, "Comparison result is not a complete canonical result")
    required = {
        "changes": list,
        "table_changes": list,
        "review_queue": list,
        "statistics": dict,
    }
    for key, expected_type in required.items():
        if not isinstance(canonical.get(key), expected_type):
            raise HTTPException(409, "Comparison result is not a complete canonical result")
    return canonical


@app.post("/jobs", status_code=202)
async def create_job(
    old_pdf: UploadFile = File(...),
    new_pdf: UploadFile = File(...),
    session_id: str = Form(""),
) -> dict:
    """Create a comparison job from two PDF uploads."""
    _validate_session_id(session_id)
    for upload in (old_pdf, new_pdf):
        if not upload.filename or Path(upload.filename).suffix.casefold() != ".pdf":
            raise HTTPException(400, "Uploads must be PDF files with a .pdf suffix")
    job_id = uuid.uuid4().hex[:12]
    root = _job_dir(job_id)
    document_dir = root / "input" / "document"
    document_dir.mkdir(parents=True)

    try:
        _store_pdf(document_dir / "old.pdf", old_pdf)
        _store_pdf(document_dir / "new.pdf", new_pdf)
    except HTTPException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise HTTPException(400, f"Upload is not a valid PDF: {exc}") from exc

    status = {
        "job_id": job_id,
        "session_id": session_id,
        "state": "queued",
        "stages_completed": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist(job_id, status)
    threading.Thread(target=_run_pipeline, args=(job_id,), daemon=True).start()
    return status


@app.get("/jobs/{job_id}")
@app.get("/jobs/{job_id}/status")
def job_status(job_id: str) -> dict:
    return _read_job(job_id)


@app.get("/jobs/{job_id}/summary")
def job_summary(job_id: str) -> dict[str, Any]:
    status = _read_job(job_id)
    return build_summary(_job_dir(job_id), status)


@app.get("/jobs/{job_id}/report")
def get_report(job_id: str) -> dict:
    canonical = _load_canonical(job_id)
    return {"job_id": job_id, "report": canonical}


@app.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str) -> dict:
    _read_job(job_id)
    out = _job_dir(job_id) / "output"
    files = sorted(p.name for p in out.iterdir() if p.is_file()) if out.exists() else []
    return {"job_id": job_id, "artifacts": files}


@app.get("/jobs/{job_id}/artifacts/{name}")
def get_artifact(job_id: str, name: str):
    if name not in ARTIFACT_NAMES:
        raise HTTPException(404, f"Unknown artifact {name}")
    path = _job_dir(job_id) / "output" / name
    if not path.exists():
        raise HTTPException(404, f"Artifact not produced (yet): {name}")
    media = "text/html" if name.endswith(".html") else (
        "text/csv" if name.endswith(".csv") else (
            "application/pdf" if name.endswith(".pdf") else "application/json"
        )
    )
    if name.endswith(".csv"):
        return PlainTextResponse(path.read_text(encoding="utf-8-sig"), media_type=media)
    return FileResponse(path, media_type=media, filename=name)


@app.get("/jobs/{job_id}/changes")
def get_changes(
    job_id: str,
    change_type: str | None = None,
    needs_review: bool | None = None,
    status: str | None = None,
) -> dict:
    canonical = _load_canonical(job_id)
    changes = canonical.get("changes") or []
    if status:
        changes = [c for c in changes if (c.get("resolution") or {}).get("status") == status]
    if change_type:
        changes = [c for c in changes if c.get("change_type") == change_type.upper()]
    if needs_review is not None:
        changes = [
            c for c in changes
            if bool((c.get("resolution") or {}).get("needs_review")) == needs_review
        ]
    return {"count": len(changes), "changes": changes}


@app.get("/jobs/{job_id}/changes/{change_id}")
def get_change(job_id: str, change_id: str) -> dict:
    canonical = _load_canonical(job_id)
    for change in canonical.get("changes") or []:
        if change.get("change_id") == change_id:
            return change
    raise HTTPException(404, f"Unknown change {change_id}")


@app.get("/jobs/{job_id}/review-queue")
def get_review_queue(job_id: str) -> dict:
    canonical = _load_canonical(job_id)
    queue = canonical.get("review_queue") or []
    return {"count": len(queue), "review_queue": queue}


@app.get("/jobs/{job_id}/review-queue.csv")
def review_queue_csv(job_id: str):
    return get_artifact(job_id, "review_queue_export.csv")


# ---------------------------------------------------------------------------
# Reader read endpoints: one reusable projection over the saved canonical result.
# ---------------------------------------------------------------------------

_READER_HTTP_STATUS = {
    "not_found": 404,
    "not_ready": 409,
    "invalid_arguments": 422,
    "invalid_cursor": 400,
    "stale_cursor": 409,
    "invalid_artifact": 409,
    "response_too_large": 413,
}


def _reader_http(exc: reader.ReaderError) -> HTTPException:
    return HTTPException(_READER_HTTP_STATUS.get(exc.code, 400), detail=exc.as_dict())


def _validate_read_job_id(job_id: str) -> str:
    try:
        return reader.validate_job_id(job_id)
    except reader.ReaderError as exc:
        raise _reader_http(exc) from exc


def _read_status(job_id: str) -> dict:
    if not JOB_ID_RE.fullmatch(job_id):
        raise _reader_http(reader.ReaderError("invalid_arguments", "invalid job_id"))
    try:
        status = _read_job(job_id)
    except HTTPException as exc:
        code = "not_found" if exc.status_code == 404 else "invalid_artifact"
        raise _reader_http(reader.ReaderError(code, "unknown or unreadable job")) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _reader_http(reader.ReaderError("invalid_artifact", "job status is unreadable")) from exc
    if not isinstance(status, dict):
        raise _reader_http(reader.ReaderError("invalid_artifact", "job status is not an object"))
    stored_job_id = status.get("job_id")
    if stored_job_id is not None and stored_job_id != job_id:
        raise _reader_http(reader.ReaderError("invalid_artifact", "job status does not match this job"))
    state = status.get("state")
    if state is not None and not isinstance(state, str):
        raise _reader_http(reader.ReaderError("invalid_artifact", "job state is malformed"))
    return status


def _reader_canonical(job_id: str, status: dict) -> dict:
    if status.get("state") != "done":
        raise _reader_http(reader.ReaderError("not_ready", "job is not done"))
    try:
        return _load_canonical(job_id)
    except HTTPException as exc:
        raise _reader_http(reader.ReaderError("invalid_artifact", "comparison result is not readable")) from exc


def _available_artifacts(job_id: str) -> list[str]:
    out = _job_dir(job_id) / "output"
    if not out.exists():
        return []
    return sorted(p.name for p in out.iterdir() if p.is_file() and p.name in reader.ARTIFACT_LINK_NAMES)


def _extraction_limitations(job_id: str) -> list[str]:
    limitations: list[str] = []
    out = _job_dir(job_id) / "output"
    for side in ("old", "new"):
        path = out / f"{side}_extraction_validation.json"
        if not path.is_file():
            limitations.append(f"{side}_extraction_validation_missing")
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            limitations.append(f"{side}_extraction_validation_unreadable")
            continue
        if isinstance(report, dict) and report.get("requires_review"):
            limitations.append(f"{side}_extraction_requires_review")
    return limitations


def _structured_elements(job_id: str, side: str) -> list[dict[str, Any]] | None:
    path = _job_dir(job_id) / "output" / f"{side}_document_structured.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    document = data.get("document") if isinstance(data, dict) else None
    elements = document.get("elements") if isinstance(document, dict) else None
    return elements if isinstance(elements, list) else None


@app.get("/jobs/{job_id}/read/summary")
def read_summary(job_id: str) -> dict:
    _validate_read_job_id(job_id)
    status = _read_status(job_id)
    state = str(status.get("state") or "queued")
    if state != "done":
        return reader.summarize(job_id, state)
    canonical = _reader_canonical(job_id, status)
    return reader.summarize(
        job_id,
        state,
        canonical,
        artifacts=_available_artifacts(job_id),
        limitations=_extraction_limitations(job_id),
    )


@app.get("/jobs/{job_id}/read/search")
def read_search(
    job_id: str,
    query: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    change_type: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict:
    _validate_read_job_id(job_id)
    job_status = _read_status(job_id)
    canonical = _reader_canonical(job_id, job_status)
    try:
        return reader.search(
            job_id,
            canonical,
            query=query,
            kind=kind,
            status=status,
            change_type=change_type,
            cursor=cursor,
            limit=limit,
        )
    except reader.ReaderError as exc:
        raise _reader_http(exc) from exc


@app.get("/jobs/{job_id}/read/details")
def read_details(
    job_id: str,
    kind: str,
    record_id: str,
    context_window: int = 1,
    cell_offset: int = 0,
    cell_limit: int = reader.DEFAULT_CELL_LIMIT,
    text_offset: int = 0,
    text_limit: int = reader.DEFAULT_TEXT_LIMIT,
    context_source_offset: int = 0,
) -> dict:
    _validate_read_job_id(job_id)
    job_status = _read_status(job_id)
    canonical = _reader_canonical(job_id, job_status)
    try:
        return reader.details(
            job_id,
            canonical,
            kind=kind,
            record_id=record_id,
            context_window=context_window,
            cell_offset=cell_offset,
            cell_limit=cell_limit,
            text_offset=text_offset,
            text_limit=text_limit,
            context_source_offset=context_source_offset,
            old_elements=_structured_elements(job_id, "old"),
            new_elements=_structured_elements(job_id, "new"),
        )
    except reader.ReaderError as exc:
        raise _reader_http(exc) from exc
