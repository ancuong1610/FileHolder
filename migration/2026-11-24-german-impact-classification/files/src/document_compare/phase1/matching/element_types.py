"""Pairing compatibility and evidence for original element types."""

from typing import Any

TEXT_ELEMENT_TYPES = frozenset({"paragraph", "heading", "list_item", "footnote", "title"})


def matching_family(element: dict[str, Any] | None) -> str:
    element_type = str((element or {}).get("element_type") or "")
    return "text" if element_type in TEXT_ELEMENT_TYPES else element_type


def type_change_fields(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    old_type = (old or {}).get("element_type")
    new_type = (new or {}).get("element_type")
    if old_type and new_type and old_type != new_type:
        return {"type_changed": {"old": old_type, "new": new_type}}
    return {}
