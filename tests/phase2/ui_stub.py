"""Offline stand-in for ``ApiClient`` used by the Streamlit AppTest smoke tests."""
from __future__ import annotations

import uuid
from typing import Any

import base64

# A real 1x1 PNG so st.image can decode it.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")


class StubClient:
    """Records calls; every response can be overridden through attributes."""

    def __init__(self, **overrides: Any) -> None:
        from document_compare.phase2.ui.api_client import ApiError as RealApiError

        self._err = RealApiError
        self.calls: list[tuple] = []
        self.cache_key = uuid.uuid4().hex  # keeps st.cache_data entries apart between tests
        self.status_value: Any = {"job_id": "job1", "state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)]}
        self.summary_value: Any = {
            "job_id": "job1", "state": "done", "complete": True,
            "counts": {"changes": 2, "table_changes": 0, "review_open": 1,
                       "change_type_counts": {"MODIFIED": 1, "ADDED": 1},
                       "result_state_counts": {"supported": 1, "uncertain": 1, "non_semantic": 0}},
            "documents": {"old": {"available": True, "document_id": "old.pdf", "page_count": 3},
                          "new": {"available": True, "document_id": "new.pdf", "page_count": 4}},
            "limitations": ["review_open"],
            "ground_truth_notice": "Engine acceptance is not legal or semantic ground truth.",
        }
        self.rows: list[dict] = [
            {"kind": "change", "record_id": "CHANGE-000001", "change_type": "MODIFIED", "status": "accepted",
             "result_state": "supported", "needs_review": False, "confirmed": True, "pages": [1],
             "snippet": "supported snippet", "snippet_source": "new_text"},
            {"kind": "change", "record_id": "CHANGE-000002", "change_type": "ADDED", "status": "review",
             "result_state": "uncertain", "needs_review": True, "confirmed": False, "pages": [2],
             "snippet": "uncertain snippet", "snippet_source": "new_text"},
        ]
        self.next_cursors: list[str | None] = []
        self.locations_value: Any = None
        self.png: Any = PNG
        self.chat_value: Any = {"job_id": "job1", "answer": "stub answer", "limitations": []}
        self.raise_on: dict[str, Exception] = {}
        for key, value in overrides.items():
            setattr(self, key, value)

    def _maybe_raise(self, name: str) -> None:
        if name in self.raise_on:
            raise self.raise_on[name]

    def submit(self, old_name, old_bytes, new_name, new_bytes, session_id):
        self.calls.append(("submit", old_name, new_name))
        self._maybe_raise("submit")
        return {"job_id": "job1", "state": "queued", "stages_completed": []}

    def status(self, job_id):
        self.calls.append(("status", job_id))
        self._maybe_raise("status")
        return self.status_value

    def summary(self, job_id):
        self.calls.append(("summary", job_id))
        self._maybe_raise("summary")
        return self.summary_value

    def search(self, job_id, **filters):
        self.calls.append(("search", job_id, dict(filters)))
        self._maybe_raise("search")
        cursor = filters.get("cursor")
        if cursor:
            rows = [dict(self.rows[-1], record_id=f"MORE-{cursor}")]
            return {"returned": rows, "total_matches": len(self.rows) + 1, "next_cursor": None}
        nxt = self.next_cursors[0] if self.next_cursors else None
        return {"returned": list(self.rows), "total_matches": len(self.rows) + (1 if nxt else 0),
                "next_cursor": nxt}

    def details(self, job_id, kind, record_id):
        self.calls.append(("details", job_id, kind, record_id))
        self._maybe_raise("details")
        uncertain = record_id.endswith("2")
        change = {"change_id": record_id, "change_type": "ADDED" if uncertain else "MODIFIED",
                  "result_state": "uncertain" if uncertain else "supported",
                  "old": {"text": "old text here", "primary_page": 1},
                  "new": {"text": "new text here", "primary_page": 2},
                  "resolution": {"review_reasons": ["low similarity"] if uncertain else []}}
        if kind == "review":
            return {"kind": "review", "record_id": record_id,
                    "review": {"review_id": record_id, "result_state": "uncertain",
                               "resolution": {"review_reasons": ["needs a human"]},
                               "evidence": {}}}
        return {"kind": kind, "record_id": record_id, "change": change}

    def locations(self, job_id, kind, record_id):
        self.calls.append(("locations", job_id, kind, record_id))
        self._maybe_raise("locations")
        if self.locations_value is not None:
            return self.locations_value
        side = {"counterpart": "present", "location": "polygon", "page_count": 3, "primary_page": 1, "regions": []}
        return {"kind": kind, "record_id": record_id, "change_type": "MODIFIED",
                "sides": {"old": dict(side), "new": dict(side, page_count=4, primary_page=2)}}

    def page_png(self, job_id, side, page, kind=None, record_id=None, uncertain=False):
        self.calls.append(("page_png", job_id, side, page, kind, record_id, uncertain))
        self._maybe_raise("page_png")
        return self.png

    def chat(self, job_id, question):
        self.calls.append(("chat", job_id, question))
        self._maybe_raise("chat")
        return self.chat_value

    def make_error(self, status, code, message):
        return self._err(status, code, message)
