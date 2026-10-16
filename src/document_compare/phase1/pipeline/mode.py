"""Domain profile resolution for the generic-only comparison pipeline.

The production pipeline is generic only. ``generic`` (and the legacy
absent/blank/``auto`` spellings) always resolve to a generic profile without
inspecting document content, so CRR-looking source text is ordinary content,
never a trigger for legal verification. The retired CRR3 mode fails with an
actionable configuration error instead of silently implying verification.

Metadata-only invariant: this module performs no matching, routing,
acceptance, authorization, environment, network, or filesystem access.
"""
from __future__ import annotations

GENERIC_MODE = "generic"

# Legacy values that used to select the generic profile; ``auto`` no longer
# inspects document content and is treated as generic.
_LEGACY_GENERIC_VALUES = frozenset({"", "auto", "generic"})
_RETIRED_VALUES = frozenset({"crr3"})


def parse_domain_mode(value) -> str:
    """Normalize a domain mode value to ``generic`` or fail actionably.

    Absent/blank, legacy ``auto``, and ``generic`` all resolve to
    ``generic``. Explicit retired CRR values and unknown values raise
    ``ValueError`` so a misconfiguration never silently implies legal
    verification.
    """
    normalized = str(value or "").strip().lower()
    if normalized in _LEGACY_GENERIC_VALUES:
        return GENERIC_MODE
    if normalized in _RETIRED_VALUES:
        raise ValueError(
            f"Domain mode {value!r} is retired: this build supports generic "
            "comparison only. Unset CRR_DOMAIN_MODE or set it to 'generic'; "
            "CRR amendment verification is no longer a production capability."
        )
    raise ValueError(
        f"Invalid domain mode {value!r}; expected 'generic' (or unset). "
        "CRR-specific domain modes are retired."
    )


def resolve_domain_profile(mode, old=None, new=None) -> dict:
    """Resolve the final domain profile; always generic.

    ``old``/``new`` are accepted for signature compatibility only and are
    never inspected: no document content can activate a specialized domain.
    """
    parse_domain_mode(mode)
    return {
        "mode": GENERIC_MODE,
        "source": "explicit",
        "confidence": "confident",
    }
