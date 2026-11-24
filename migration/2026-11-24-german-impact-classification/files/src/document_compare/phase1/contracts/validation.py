"""Runtime validation that survives ``python -O``.

Python removes ``assert`` statements when the interpreter runs with ``-O`` or
``-OO``, which silently disables integrity checks in production. ``require``
raises ``ValueError`` unconditionally, so critical pipeline invariants stay
enforced regardless of optimization flags.
"""
from __future__ import annotations

from typing import Any


def require(condition: bool, message: str) -> None:
    """Raise ``ValueError(message)`` unless ``condition`` is truthy.

    Use for critical runtime data-integrity checks that must not disappear
    under ``python -O``.
    """
    if not condition:
        raise ValueError(message)


def extraction_validation_state(element: dict[str, Any] | None) -> str:
    """Classify an element's extraction metadata: 'requires_review', 'unknown', or 'ok'.

    A review flag requires review even when the boolean verdict is absent.
    An explicit false verdict with no review flags is required for 'ok'.
    """
    if not isinstance(element, dict):
        return "unknown"
    validation = element.get("extraction_validation")
    if not isinstance(validation, dict):
        return "unknown"
    if validation.get("extraction_review_flags") or validation.get("review_flags"):
        return "requires_review"
    flag = validation.get("requires_extraction_review")
    if not isinstance(flag, bool):
        return "unknown"
    return "requires_review" if flag else "ok"
