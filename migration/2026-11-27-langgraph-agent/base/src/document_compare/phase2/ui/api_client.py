"""Small httpx client for the local comparison API. Never logs bodies or bytes."""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

import httpx

DEFAULT_API_BASE = "http://127.0.0.1:8321"
TIMEOUT_SECONDS = 60.0
CHAT_TIMEOUT_SECONDS = 300.0
_JOB_ID = re.compile(r"[0-9a-f]{12}")


class ApiError(Exception):
    """Any non-2xx answer or transport failure. ``status`` is None for transport failures."""

    def __init__(self, status: int | None, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


def base_from_env() -> str:
    return os.environ.get("CRR_API_BASE", DEFAULT_API_BASE).strip()


def validate_base(raw: str) -> str:
    """Loopback http only, no path/query/fragment (mirrors local_reviewer._api_base)."""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "http":
        raise ValueError(f"CRR_API_BASE must use http scheme: {raw!r}")
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"CRR_API_BASE must be loopback: {raw!r}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"CRR_API_BASE must not contain a path, query, or fragment: {raw!r}")
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8321}"


def _job(job_id: str) -> str:
    """Return job_id if it is a 12-char lowercase hex id, else refuse before any request."""
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ApiError(None, "invalid_arguments", "Job ID must be 12 lowercase hex characters.")
    return job_id


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None and v != ""}


def _error_from(response: httpx.Response) -> ApiError:
    status = response.status_code
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        return ApiError(status, str(detail.get("error") or "error"),
                        str(detail.get("message") or f"HTTP {status}"))
    if isinstance(detail, list):
        first = detail[0] if detail else {}
        message = first.get("msg") if isinstance(first, dict) else None
        return ApiError(status, "invalid_arguments", str(message or "invalid arguments"))
    if isinstance(detail, str) and detail:
        return ApiError(status, "error", detail)
    return ApiError(status, "error", f"HTTP {status}")


class ApiClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = validate_base(base_url)
        self._http = httpx.Client(base_url=self.base_url, transport=transport, timeout=TIMEOUT_SECONDS)

    @property
    def cache_key(self) -> str:
        return self.base_url

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise ApiError(None, "timeout", "The API did not answer in time; it may still be working.") from None
        except httpx.HTTPError:
            raise ApiError(None, "api_unreachable", f"Could not reach the API at {self.base_url}.") from None
        if response.status_code >= 400:
            raise _error_from(response)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self._request(method, path, **kwargs)
        try:
            data = response.json()
        except ValueError:
            raise ApiError(response.status_code, "error", "The API returned an unreadable answer.") from None
        return data if isinstance(data, dict) else {}

    def submit(self, old_name: str, old_bytes: bytes, new_name: str, new_bytes: bytes, session_id: str) -> dict:
        return self._json(
            "POST", "/jobs",
            files={"old_pdf": (old_name, old_bytes, "application/pdf"),
                   "new_pdf": (new_name, new_bytes, "application/pdf")},
            data={"session_id": session_id},
        )

    def status(self, job_id: str) -> dict:
        return self._json("GET", f"/jobs/{_job(job_id)}/status")

    def summary(self, job_id: str) -> dict:
        return self._json("GET", f"/jobs/{_job(job_id)}/read/summary")

    def search(self, job_id: str, **filters: Any) -> dict:
        return self._json("GET", f"/jobs/{_job(job_id)}/read/search", params=_clean(filters))

    def details(self, job_id: str, kind: str, record_id: str, **options: Any) -> dict:
        return self._json("GET", f"/jobs/{_job(job_id)}/read/details",
                          params=_clean({"kind": kind, "record_id": record_id, **options}))

    def locations(self, job_id: str, kind: str, record_id: str) -> dict:
        return self._json("GET", f"/jobs/{_job(job_id)}/view/locations", params={"kind": kind, "record_id": record_id})

    def outline(self, job_id: str, *, expand_blocks: bool = False) -> dict:
        path = f"/jobs/{_job(job_id)}/view/outline"
        if expand_blocks:
            return self._json("GET", path, params={"expand_blocks": True})
        return self._json("GET", path)

    def page_png(self, job_id: str, side: str, page: int, kind: str | None = None,
                 record_id: str | None = None, uncertain: bool = False) -> bytes | None:
        params: dict[str, Any] = {}
        if kind is not None and record_id is not None:
            params["kind"], params["record_id"] = kind, record_id
            if uncertain:
                params["uncertain"] = "true"
        safe_side = urllib.parse.quote(str(side), safe="")
        try:
            return self._request("GET", f"/jobs/{_job(job_id)}/view/pages/{safe_side}/{int(page)}.png", params=params).content
        except ApiError as exc:
            if exc.code == "source_pdf_unavailable":
                return None
            raise

    def chat(
        self, job_id: str, question: str, *, history: list[dict[str, str]] | None = None,
        context: dict[str, str] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"question": question}
        if history is not None:
            body["history"] = history
        if context is not None:
            body["context"] = context
        return self._json("POST", f"/jobs/{_job(job_id)}/chat", json=body, timeout=CHAT_TIMEOUT_SECONDS)
