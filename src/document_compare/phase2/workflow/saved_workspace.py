"""Read-only local ReaderGateway over one explicitly selected Phase-1 workspace.

No API registration, copying, extraction, provider call or source-file write.
Filtering and bounded projections belong to result_reader, not this adapter.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from document_compare.phase2.reader import result_reader as reader


class SavedWorkspaceGateway:
    def __init__(self, workspace: Path) -> None:
        self.root = Path(workspace).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Selected workspace does not exist; choose a completed Phase-1 workspace")
        self._limitations: list[str] = []
        status = self._load_json("job_status.json", required=False)
        if status is not None and status.get("state") != "done":
            raise ValueError("job_status.json: workspace is not done; finish or diagnose Phase 1 first")
        self.job_id = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        if status is not None and "job_id" in status:
            try:
                self.job_id = reader.validate_job_id(status["job_id"])
            except reader.ReaderError as exc:
                raise ValueError("job_status.json: invalid job identity") from exc

        self._canonical = self._load_json("output/comparison_result.json")
        required = {"changes": list, "table_changes": list, "review_queue": list, "statistics": dict}
        if (self._canonical.get("comparison_stage") != "phase_1_final_comparison"
                or any(not isinstance(self._canonical.get(key), shape) for key, shape in required.items())
                or not reader.summarize(self.job_id, "done", self._canonical)["complete"]):
            raise ValueError("comparison_result.json: invalid or incomplete canonical result; complete Phase 1 first")

        self._elements: dict[str, list[dict] | None] = {}
        for side in ("old", "new"):
            name = f"output/{side}_document_structured.json"
            doc = self._load_json(name, required=False)
            if doc is None:
                self._elements[side] = None
                self._limitations.append(f"{side}_document_context_missing")
            else:
                document = doc.get("document")
                elements = document.get("elements") if isinstance(document, dict) else None
                if not isinstance(elements, list) or any(not isinstance(e, dict) for e in elements):
                    raise ValueError(f"{name}: elements must be a list of objects")
                self._elements[side] = elements
            validation = self._load_json(f"output/{side}_extraction_validation.json", required=False)
            if validation is None:
                self._limitations.append(f"{side}_extraction_validation_missing")
            elif validation.get("requires_review"):
                self._limitations.append(f"{side}_extraction_requires_review")

    def _load_json(self, name: str, *, required: bool = True) -> dict[str, Any] | None:
        path = self.root / name
        # Fixed filenames only; a saved artifact must not redirect a local
        # reader into unrelated private files through a symlink.
        if not path.resolve().is_relative_to(self.root):
            raise ValueError(f"{name}: artifact escapes the selected workspace")
        if not path.exists() and not required:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{name}: missing or unreadable JSON; select a completed Phase-1 workspace") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{name}: expected a JSON object")
        return value

    def _check_job(self, job_id: str) -> None:
        if job_id != self.job_id:
            raise reader.ReaderError("not_found", "job does not belong to this saved workspace")

    def summarize(self, job_id: str) -> dict[str, Any]:
        self._check_job(job_id)
        return reader.summarize(job_id, "done", self._canonical, limitations=self._limitations)

    def search(self, job_id: str, **filters: Any) -> dict[str, Any]:
        self._check_job(job_id)
        return reader.search(job_id, self._canonical, **filters)

    def details(self, job_id: str, kind: str, record_id: str, **options: Any) -> dict[str, Any]:
        self._check_job(job_id)
        return reader.details(job_id, self._canonical, kind=kind, record_id=record_id,
                              old_elements=self._elements["old"], new_elements=self._elements["new"],
                              **options)

    def check(self) -> dict[str, Any]:
        """Exercise real projections, returning counts/notices rather than text."""
        summary = self.summarize(self.job_id)
        result = self.search(self.job_id, limit=1)
        if result["returned"]:
            first = result["returned"][0]
            self.details(self.job_id, first["kind"], first["record_id"])
        return {"reader_check": "passed", "summary": summary,
                "details_check": "passed" if result["returned"] else "skipped_no_records"}
