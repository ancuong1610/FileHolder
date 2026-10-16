"""Document Intelligence page-field helpers owned by Phase 1 extraction.

These helpers read/set the DI page number regardless of camelCase or
snake_case serialization. They are Phase-1 extraction concerns, not generic
shared utilities.
"""
from __future__ import annotations


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
