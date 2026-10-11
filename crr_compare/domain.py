"""Conservative domain profile detection for the comparison pipeline.

Resolves the domain mode (``auto|generic|crr3``) as metadata only. CRR3 is
resolved only when both documents independently carry strong, consistent
evidence identifying Regulation (EU) No 575/2013 specifically: its CELEX
code ``32013R0575`` (or equivalent separators/casing) or the
consolidated-regulation citation. The CRR3 amendment Regulation (EU)
2024/1623 may support but can never replace that base evidence. Everything
else falls back to ``generic`` with ``confidence: uncertain``.

Metadata-only invariant: this module performs no matching, routing,
acceptance, authorization, environment, network, or filesystem access;
stdlib ``re`` only.
"""
from __future__ import annotations

import re

VALID_MODES = ("auto", "generic", "crr3")

CRR3_CELEX = "32013R0575"
CRR3_INSTRUMENT = "Regulation (EU) No 575/2013"

# CELEX regulation-code shape 3ddddRdddd with flexible separators/casing;
# a match counts as base evidence only when it normalizes to CRR3_CELEX.
_CELEX_RE = re.compile(r"\b3\d{4}[-\s]*R[-\s]*\d{4}\b", re.IGNORECASE)
# Consolidated-regulation citation; the "(EU)" marker is required so bare
# "575/2013" or "Regulation No 575/2013" stays ambiguous.
_CITATION_RE = re.compile(
    r"Regulation\s*\(EU\)\s*(?:No\.?\s*)?575\s*/\s*2013\b", re.IGNORECASE
)


def parse_domain_mode(value) -> str:
    """Normalize a domain mode value; blank/absent defaults to ``auto``."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "auto"
    if normalized not in VALID_MODES:
        raise ValueError(
            f"Invalid domain mode {value!r}; expected one of: {', '.join(VALID_MODES)}."
        )
    return normalized


def _iter_strings(value):
    """Yield every string value in a nested dict/list document model."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _has_base_evidence(model) -> bool:
    """True when the document carries qualifying Regulation (EU) No 575/2013 evidence."""
    for text in _iter_strings(model):
        if _CITATION_RE.search(text):
            return True
        for match in _CELEX_RE.finditer(text):
            if re.sub(r"[^A-Z0-9]", "", match.group(0).upper()) == CRR3_CELEX:
                return True
    return False


def detect_crr3(old_model, new_model) -> dict:
    """Auto-detect the domain profile from the two internal-schema documents."""
    if _has_base_evidence(old_model) and _has_base_evidence(new_model):
        return {
            "mode": "crr3",
            "source": "auto",
            "confidence": "confident",
            "instrument": CRR3_INSTRUMENT,
        }
    return {"mode": "generic", "source": "auto", "confidence": "uncertain"}


def resolve_domain_profile(mode, old, new) -> dict:
    """Resolve the final domain profile; an explicit mode always wins."""
    resolved = parse_domain_mode(mode)
    if resolved == "auto":
        return detect_crr3(old, new)
    if resolved == "crr3":
        return {
            "mode": "crr3",
            "source": "explicit",
            "confidence": "confident",
            "instrument": CRR3_INSTRUMENT,
        }
    return {"mode": "generic", "source": "explicit", "confidence": "confident"}