"""Runtime validation that survives ``python -O``.

Python removes ``assert`` statements when the interpreter runs with ``-O`` or
``-OO``, which silently disables integrity checks in production. ``require``
raises ``ValueError`` unconditionally, so critical pipeline invariants stay
enforced regardless of optimization flags.
"""
from __future__ import annotations


def require(condition: bool, message: str) -> None:
    """Raise ``ValueError(message)`` unless ``condition`` is truthy.

    Use for critical runtime data-integrity checks that must not disappear
    under ``python -O``.
    """
    if not condition:
        raise ValueError(message)
