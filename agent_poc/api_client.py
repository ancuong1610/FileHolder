from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx


class ComparisonApiClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError(f"Expected JSON object from {path}.")
        return value

    def healthcheck(self) -> dict[str, Any]:
        # FastAPI's schema endpoint is sufficient for the current service.
        return self._json("GET", "/openapi.json")

    def submit(self, old_pdf: Path, new_pdf: Path, session_id: str) -> dict[str, Any]:
        for path in (old_pdf, new_pdf):
            if not path.is_file() or path.suffix.casefold() != ".pdf":
                raise ValueError(f"Not an existing PDF: {path}")
        with old_pdf.open("rb") as old_handle, new_pdf.open("rb") as new_handle:
            return self._json(
                "POST",
                "/jobs",
                files={
                    "old_pdf": (old_pdf.name, old_handle, "application/pdf"),
                    "new_pdf": (new_pdf.name, new_handle, "application/pdf"),
                },
                data={"session_id": session_id},
            )

    def status(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}/status")

    def wait(self, job_id: str, poll_seconds: float = 3.0, timeout_seconds: float = 7200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.status(job_id)
            if status.get("state") in {"done", "failed"}:
                return status
            time.sleep(poll_seconds)
        raise TimeoutError(f"Job {job_id} did not finish within {timeout_seconds}s.")

    def report(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}/report")

    def changes(self, job_id: str, **filters: Any) -> dict[str, Any]:
        params = {key: value for key, value in filters.items() if value is not None}
        return self._json("GET", f"/jobs/{job_id}/changes", params=params)

    def change(self, job_id: str, change_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}/changes/{change_id}")

    def review_queue(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}/review-queue")

    def artifacts(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/jobs/{job_id}/artifacts")

    def artifact_url(self, job_id: str, name: str) -> str:
        return f"{self.base_url}/jobs/{job_id}/artifacts/{name}"

