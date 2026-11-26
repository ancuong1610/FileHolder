"""Offline stand-in for ``ApiClient`` used by the Streamlit AppTest smoke tests."""
from __future__ import annotations

import uuid
from typing import Any

import base64

# A real 1x1 PNG so st.image can decode it.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")


def item(kind, record_id, change_type, label, path, page_old=1, page_new=2, context_old=False, context_new=False):
    pages = {"old": None if context_old else page_old, "new": None if context_new else page_new}
    pref = ("old", "new") if change_type in ("DELETED", "TABLE_DELETED") else ("new", "old")
    page_side = next((side for side in pref if pages[side] is not None), None)
    return {"kind": kind, "record_id": record_id, "change_type": change_type, "label": label,
            "page_old": page_old, "page_new": page_new, "context_old": context_old, "context_new": context_new,
            "page_side": page_side, "anchor_old": False, "anchor_new": False, "section_path": path}


def default_outline() -> dict:
    """Section A (1 change, child B with 1 change), empty section C, one flagged item."""
    child = {"section_id": "S2", "title": "Section B", "side": "new", "level": 2, "change_count": 1,
             "children": [], "items": [item("change", "CHANGE-000002", "ADDED", "added text", ["Section A", "Section B"], 1, 2, True, False)]}
    top = {"section_id": "S1", "title": "Section A", "side": "new", "level": 1, "change_count": 2,
           "children": [child], "items": [item("change", "CHANGE-000001", "MODIFIED", "supported snippet", ["Section A"])]}
    empty = {"section_id": "S3", "title": "Section C", "side": "new", "level": 1, "change_count": 0,
             "children": [], "items": []}
    return {"documents": {"old": {"page_count": 3}, "new": {"page_count": 4}},
            "sections": [top, empty], "unplaced": [],
            "review": [item("review", "REVIEW-000001", None, "plausible unresolved counterpart", [], None, None)],
            "total": 3}


class StubClient:
    """Records calls; every response can be overridden through attributes."""

    def __init__(self, **overrides: Any) -> None:
        from document_compare.phase2.ui.api_client import ApiError as RealApiError

        self._err = RealApiError
        self.calls: list[tuple] = []
        self.cache_key = uuid.uuid4().hex  # keeps st.cache_data entries apart between tests
        self.status_value: Any = {"job_id": "job1", "state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)],
                             "old_name": "old_policy.pdf", "new_name": "new_policy.pdf"}
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
        self.outline_value: Any = default_outline()
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

    def outline(self, job_id):
        self.calls.append(("outline", job_id))
        self._maybe_raise("outline")
        return self.outline_value

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
        if kind == "block":
            return {"kind": kind, "record_id": record_id,
                    "block": {"change_type": "DELETED", "contains_heading": True, "members": []}}
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

    def chat(self, job_id, question, *, history=None, context=None):
        self.calls.append(("chat", job_id, question, history, context))
        self._maybe_raise("chat")
        return self.chat_value

    def make_error(self, status, code, message):
        return self._err(status, code, message)
