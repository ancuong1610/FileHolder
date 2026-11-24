"""Deterministic review routing (s11): generic-only routing.

Auto-converted from 11_review_routing_special_cases_v2_1_selective_llm.ipynb (code cells only).

Generic routing is pure and mode-independent: ``build_generic_routing`` never
imports authorization and never reads CRR evidence. Domain verification is
retired: the compatibility metadata this stage emits is always
``not_applicable`` and no verifier is ever invoked. CRR-looking source text is
ordinary document content and never activates a specialized domain.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.contracts.validation import extraction_validation_state, require
from document_compare.phase1.pipeline.mode import parse_domain_mode, resolve_domain_profile
from document_compare.phase1.matching.impact import (
    STATE_MARKERS,
    classify_impact,
    glyph_fold,
    raw_glyph_signature,
)
from document_compare.phase1.matching.language import SUSPENDED_HYPHEN_STOPWORDS
from document_compare.phase1.matching.semantic_evidence import (
    independent_correspondence_verified,
)
from document_compare.phase1.contracts.semantic_assessment import (
    is_deterministic_material_proof,
)
from document_compare.phase1.contracts.review_identity import structural_route_identity
from document_compare.phase1.stages.s09_movement import previous_candidate_view

# ---- cell 1 ----
import json, os, re
from collections import Counter
from pathlib import Path
from typing import Any

STEP11_VERSION = "2.3.0-generic-routing"
EXPECTED_STEP9_STAGE = "movement_and_structural_reconciliation_evidence"
EXPECTED_STEP10_STAGE = "logical_table_comparison_with_provisional_context"

NON_TABLE_GROUPS = (
    "modified",
    "modified_review",
    "duplicate_exact_review",
    "relocated",
    "deleted",
    "added",
)
MARKER_RE = re.compile(r"^(?:[A-Z]{0,3}\d+[A-Z0-9]*|[A-Z]\d+)$", re.I)
# A changed token starting with a digit (optionally signed) carries a
# numeric/value signal: amounts, dates, percentages and digit-prefixed
# references ("30", "2026", "20%", "12a"). Letter-prefixed margin markers
# ("M5", "VC2", "C12") are not values. This inspects only the actual old/new
# token difference -- no document domain and no hard-coded article list.
NUMERIC_SIGNAL_RE = re.compile(r"^[+\-]?\d")
ARTICLE_PREFIX_RE = re.compile(r"^(?:Artikel|Article)\s+\d+[A-Za-z]*\b", re.I)
LIST_LABEL_RE = re.compile(
    r"^[\(\[]?(?:\d{1,3}|[ivxlcdm]{1,5}|[a-z])(?:[\)\].,;:]|\])?$", re.I
)
# How far past the last old<->new aligned element a residual candidate may
# sit before "deleted/added here" is unsupported (the other extraction may
# simply end earlier). Reading-order slots.
FRONTIER_MARGIN = 12

def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
    return value

def save_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)

# ---- cell 3 ----
def text_of(element: dict[str, Any] | None) -> str:
    if not isinstance(element, dict):
        return ""
    for key in ("comparison_text", "normalized_text", "text"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

def token_changes(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    diff = candidate.get("token_diff") or {}
    comparison = diff.get("comparison_token_diff") or {}
    return comparison.get("changes") or []



# Reasons emitted by the hyphen/wrap rule. A hyphen/space-only diff may be
# suppressed as a deterministic artifact ONLY when the raw source proves a real
# line break; otherwise the ambiguous boundary must stay in review.
WRAP_POSITION_REASON = "hyphenation_wrap_position_only"
WRAP_SOURCE_PROVEN_REASON = "hyphenation_wrap_source_line_break_proven"
WRAP_UNPROVEN_REASON = "hyphenation_wrap_unproven_source"
WRAP_SEMANTIC_SIGNAL_REASON = "hyphenation_wrap_with_semantic_signal"

_WRAP_STRIP_RE = re.compile(r"[-\u00ad\u2010\u2011\s]+")
_HYPHEN_RE = re.compile(r"[-\u00ad\u2010\u2011]")
# A visible hyphen directly before a real line break, e.g. ``ge-\nhandhabt``.
_SOURCE_LINE_BREAK_HYPHEN_RE = re.compile(
    r"(\w+)[-\u00ad\u2010\u2011][ \t]*\n[ \t]*(\w+)"
)


def word_split_like(candidate: dict[str, Any]) -> bool:
    changes = token_changes(candidate)
    if len(changes) != 1:
        return False
    change = changes[0]
    old_tokens = change.get("old_tokens") or []
    new_tokens = change.get("new_tokens") or []
    # Case is preserved on purpose: case folding would treat a case-only
    # difference (``BaFin`` vs ``Bafin``) as a word split and silently
    # suppress a real semantic difference.
    old_join = "".join(old_tokens)
    new_join = "".join(new_tokens)
    return bool(old_join and old_join == new_join and old_tokens != new_tokens)


def stripped_wrap_sides(candidate: dict[str, Any]) -> tuple[str, str]:
    """Changed-token joins with only hyphen variants and whitespace removed.

    Case is preserved: case folding would mask a case-only semantic
    difference (``BaFin`` vs ``Bafin``) as a wrap artifact.
    """
    changes = token_changes(candidate)
    old_join = "".join(
        token for change in changes for token in (change.get("old_tokens") or [])
    )
    new_join = "".join(
        token for change in changes for token in (change.get("new_tokens") or [])
    )
    return _WRAP_STRIP_RE.sub("", old_join), _WRAP_STRIP_RE.sub("", new_join)


def wrap_artifact_like(candidate: dict[str, Any]) -> bool:
    """True when the whole diff is hyphenation/wrap-position only (a candidate).

    Joins ALL changed tokens on each side across every diff operation and
    removes ONLY hyphen variants and whitespace before comparing (case
    preserved): the sides must then be character-identical. Other punctuation
    (citations, "(4)", "=") still counts as a difference, so substantive edits
    can never qualify. Catches multi-operation cases that word_split_like
    (single-op) misses.

    This is only a candidate: the same characters can hide a semantic hyphen
    (``re-sign`` vs ``resign``) or a word boundary (``now here`` vs
    ``nowhere``). Suppression additionally requires source-proven line-break
    evidence; see ``source_proven_line_wrap``.
    """
    old_join, new_join = stripped_wrap_sides(candidate)
    return bool(old_join) and old_join == new_join


def _wrap_join(match: re.Match) -> str | None:
    left, right = match.group(1), match.group(2)
    if right[:1].islower() and right.casefold() not in SUSPENDED_HYPHEN_STOPWORDS:
        return left + right
    return None


def _line_break_hyphen_joins(raw: str) -> set[str]:
    """Joined words from real lowercase, non-stopword ``-\\n`` breaks in raw.

    Mirrors the approved s04 rule: only a lowercase continuation that is not a
    suspended-hyphen function word is a real line-wrap. Uppercase/digit
    continuations and stopwords are never proof.
    """
    return {
        joined for match in _SOURCE_LINE_BREAK_HYPHEN_RE.finditer(raw)
        if (joined := _wrap_join(match)) is not None
    }


def source_proven_line_wrap(candidate: dict[str, Any]) -> bool:
    """True only when EVERY changed boundary is proven by a source line break.

    A candidate can mix a real line-wrap boundary with an ambiguous same-line
    hyphen or word boundary (``re-sign``/``resign``, ``now here``/``nowhere``):
    one matching source break must not hide the others. Each comparison change
    is checked independently and must (a) be hyphen/space-only, (b) contain a
    hyphen, and (c) have a raw-source ``-\\n`` break whose joined word EXACTLY
    equals that change's stripped content. Any unproven change keeps the whole
    diff in review (conservative false negative preferred over broader proof).
    """
    changes = token_changes(candidate)
    if not changes:
        return False
    joined_words: set[str] = set()
    for element in (candidate.get("old"), candidate.get("new")):
        raw = (element or {}).get("text")
        if isinstance(raw, str) and "\n" in raw:
            joined_words |= _line_break_hyphen_joins(raw)
    if not joined_words:
        return False
    for change in changes:
        old_tokens = change.get("old_tokens") or []
        new_tokens = change.get("new_tokens") or []
        old_content = _WRAP_STRIP_RE.sub("", "".join(old_tokens))
        new_content = _WRAP_STRIP_RE.sub("", "".join(new_tokens))
        if not old_content or old_content != new_content:
            return False
        if not _HYPHEN_RE.search("".join(old_tokens) + "".join(new_tokens)):
            return False
        if old_content not in joined_words:
            return False
    return True

def numeric_signal_tokens(candidate: dict[str, Any]) -> list[str]:
    """Changed tokens that carry a numeric/value signal.

    A token beginning with a digit (optionally signed) is an amount, date,
    percentage or digit-prefixed reference and must never be dismissed as a
    layout marker / list label / OCR glyph. Letter-prefixed margin markers are
    not values. Inspects only the real token difference, not document domain.
    """
    changes = token_changes(candidate)
    tokens = [
        str(token).strip()
        for change in changes
        for token in (change.get("old_tokens") or []) + (change.get("new_tokens") or [])
        if str(token).strip()
    ]
    return [token for token in tokens if NUMERIC_SIGNAL_RE.match(token)]


def representation_token_changes(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Changed tokens across every representation diff.

    ``token_changes`` reads only the comparison representation. A detected
    margin-marker prefix or a normalization coalescence can make that
    representation token-equivalent while the source/normalized
    representation still carries the real difference.
    """
    diff = candidate.get("token_diff") or {}
    changes: list[dict[str, Any]] = []
    for key in ("source_token_diff", "normalized_token_diff", "comparison_token_diff"):
        representation = diff.get(key) or {}
        changes.extend(representation.get("changes") or [])
    return changes


def representation_semantic_signal_tokens(candidate: dict[str, Any]) -> list[str]:
    """Value-signal tokens hidden anywhere in the representation diffs.

    Reuses ``NUMERIC_SIGNAL_RE`` so an amount, date, percentage or
    digit-prefixed reference that was stripped from the comparison
    representation (e.g. a detected margin marker like ``12a``) is still
    detected in the source/normalized representation.
    """
    tokens = [
        str(token).strip()
        for change in representation_token_changes(candidate)
        for token in (change.get("old_tokens") or []) + (change.get("new_tokens") or [])
        if str(token).strip()
    ]
    return [token for token in tokens if NUMERIC_SIGNAL_RE.match(token)]


def marker_only_like(candidate: dict[str, Any]) -> bool:
    if numeric_signal_tokens(candidate):
        return False
    changes = token_changes(candidate)
    if not changes or len(changes) > 2:
        return False
    changed = []
    for ch in changes:
        changed += list(ch.get("old_tokens") or [])
        changed += list(ch.get("new_tokens") or [])
    changed = [t for t in changed if str(t).strip()]
    return bool(changed) and all(MARKER_RE.match(str(t)) for t in changed)

def heading_boundary_like(candidate: dict[str, Any]) -> bool:
    old = candidate.get("old") or {}
    new = candidate.get("new") or {}
    if old.get("element_type") not in {"heading", "title"}:
        return False
    a, b = text_of(old), text_of(new)
    if not a or not b or a == b:
        return False
    shorter, longer = (a,b) if len(a) < len(b) else (b,a)
    if shorter in longer:
        fragment = longer.replace(shorter, "", 1).strip(" -–—:;,")
        return bool(fragment) and (
            ARTICLE_PREFIX_RE.match(fragment) is not None
            or len(fragment.split()) <= 4
        )
    return False
def short_text(candidate: dict[str, Any]) -> bool:
    texts = [text_of(candidate.get("old")), text_of(candidate.get("new"))]
    nonempty = [t for t in texts if t]
    return bool(nonempty) and min(len(t) for t in nonempty) < 40


def norm_text_of(element: dict[str, Any] | None) -> str:
    for key in ("normalized_text", "comparison_text", "text"):
        value = (element or {}).get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split()).casefold()
    return ""


def list_label_only_like(candidate: dict[str, Any]) -> bool:
    """All changed tokens on both sides are list/paragraph labels ((2), g, iii)."""
    if numeric_signal_tokens(candidate):
        return False
    changes = token_changes(candidate)
    if not changes:
        return False
    changed = [
        str(t)
        for ch in changes
        for t in (ch.get("old_tokens") or []) + (ch.get("new_tokens") or [])
        if str(t).strip()
    ]
    return bool(changed) and all(LIST_LABEL_RE.match(t) for t in changed)


def single_glyph_like(candidate: dict[str, Any]) -> bool:
    """Whole diff is one substituted / inserted / deleted character.

    Catches OCR/font-extraction defects (November->Novemmber, B=->P=,
    aRWA¡->aRWA;, (1)->l)) that similarity thresholds accept. Real legal
    edits are essentially never one character; routed to review, never
    suppressed, so nothing is lost. Numeric value changes are exempt: a
    one-character difference in a value ("2026"->"2027") is semantic and must
    not be dismissed as OCR noise.
    """
    if numeric_signal_tokens(candidate):
        return False
    changes = token_changes(candidate)
    if not changes or len(changes) > 3:
        return False
    strip = lambda value: re.sub(r"[-\s]+", "", value)  # noqa: E731
    old_join = strip("".join(t for ch in changes for t in (ch.get("old_tokens") or [])))
    new_join = strip("".join(t for ch in changes for t in (ch.get("new_tokens") or [])))
    if not old_join or not new_join or old_join == new_join:
        return False
    if len(old_join) == len(new_join):
        return sum(1 for a, b in zip(old_join, new_join) if a != b) == 1
    if abs(len(old_join) - len(new_join)) == 1:
        short_side, long_side = (
            (old_join, new_join) if len(old_join) < len(new_join) else (new_join, old_join)
        )
        return any(
            long_side[:i] + long_side[i + 1:] == short_side
            for i in range(len(long_side))
        )
    return False


SEMANTIC_SHORT_PAIR_REASON = "short_text_similarity_pair"


def residual_counterpart_uncertain_reason(candidate: dict[str, Any]) -> str | None:
    """Return why an unmatched residual cannot be accepted as one-sided.

    A plausible proposal is enough to block independent add/delete acceptance.
    Incomplete/unsearched coverage (including budget exhaustion) also blocks it,
    because absent lexical evidence is not proof of absence.
    """
    if candidate.get("residual_counterpart_proposals"):
        return "plausible_unresolved_counterpart"
    coverage = candidate.get("residual_proposal_coverage")
    if isinstance(coverage, dict):
        if coverage.get("unretained_plausible_counterparts"):
            # A plausible indexed counterpart existed but the symmetric cap
            # retained none: pruning is not proof of absence.
            return "residual_counterpart_pruned"
        if coverage.get("status") in {"partial", "unsearched"}:
            return "residual_search_incomplete"
        if coverage.get("budget_exhausted") or coverage.get("unsearched"):
            return "residual_search_incomplete"
    return None


def semantic_short_pair_proof(candidate: dict[str, Any]) -> bool:
    """Only the existing short-text-only review reason may be cleared here.

    Stage 14 independently revalidates the same witness against the actual
    source, so this route is a narrow nomination, not an authority transfer.
    Every other review reason, extraction problem, duplicate/conflict guard
    and missing strong reciprocal correspondence keeps the pair in review.
    """
    reasons = set(candidate.get("review_reasons") or [])
    if reasons != {SEMANTIC_SHORT_PAIR_REASON}:
        return False
    assessment = candidate.get("semantic_assessment")
    if not is_deterministic_material_proof(assessment):
        return False
    if (assessment or {}).get("correspondence") != "verified":
        return False
    return independent_correspondence_verified(candidate)


def route_non_table(
    group: str,
    candidate: dict[str, Any],
    *,
    opposite_norm_texts: frozenset[str] = frozenset(),
    aligned_frontier: int | None = None,
) -> tuple[str, str, list[str]]:
    reasons = list(candidate.get("review_reasons") or [])
    old = candidate.get("old")
    new = candidate.get("new")

    # Only elements that exist on a side are classified: one-sided candidates
    # have a None opposite side, which must not count as unknown metadata.
    present = [element for element in (old, new) if isinstance(element, dict)]
    states = [extraction_validation_state(element) for element in present]
    source_extraction_review = "requires_review" in states
    if source_extraction_review:
        reasons.append("source_extraction_requires_review")
    elif "unknown" in states:
        reasons.append("source_extraction_validation_unknown")

    # ------------------------------------------------------------
    # Non-semantic / assignment-only cases
    # ------------------------------------------------------------

    if group == "relocated":
        return (
            "NON_SEMANTIC",
            "relocated_unchanged",
            sorted(set(reasons)),
        )

    if group == "duplicate_exact_review":
        reasons.append("duplicate_exact_assignment_ambiguous")
        return (
            "UNCERTAIN_RESULT",
            "duplicate_exact_assignment",
            sorted(set(reasons)),
        )

    # ------------------------------------------------------------
    # Missing, malformed, or explicitly review-required extraction
    # evidence must take precedence over artifact/structural suppression.
    # ------------------------------------------------------------
    extraction_forces_review = (
        source_extraction_review
        or "source_extraction_validation_unknown" in reasons
    )

    # ------------------------------------------------------------
    # Deterministic special-case rules
    # ------------------------------------------------------------

    raw_texts = [element.get("text") or "" for element in present]
    raw_signatures = [raw_glyph_signature(text) for text in raw_texts]
    raw_glyphs_agree = len(present) != 2 or raw_signatures[0] == raw_signatures[1]
    states_changed = len(present) == 2 and (
        [char for char in raw_signatures[0] if char in STATE_MARKERS]
        != [char for char in raw_signatures[1] if char in STATE_MARKERS]
    )

    if not extraction_forces_review and not states_changed:
        # Hyphenation/wrap-position-only differences are artifact nominations;
        # they are checked before the single-op word-split rule because this
        # covers multi-operation diffs.
        if wrap_artifact_like(candidate):
            reasons.append(WRAP_POSITION_REASON)
            # ``wrap_artifact_like`` only shows the changed tokens become
            # character-identical once hyphen variants and whitespace are
            # removed. That is NOT proof of a line-wrap artifact: the same
            # characters can hide a semantic hyphen (``re-sign``/``resign``) or
            # a word boundary (``now here``/``nowhere``). A value/reference
            # signal in the source/normalized representation must also stay in
            # review. Only when every changed boundary is individually proven
            # by a raw-source line break may the diff be suppressed.
            if representation_semantic_signal_tokens(candidate):
                reasons.append(WRAP_SEMANTIC_SIGNAL_REASON)
                return (
                    "UNCERTAIN_RESULT",
                    "hyphenation_wrap_artifact",
                    sorted(set(reasons)),
                )
            if source_proven_line_wrap(candidate):
                reasons.append(WRAP_SOURCE_PROVEN_REASON)
                return (
                    "SPECIAL_CASE_RULE",
                    "hyphenation_wrap_artifact",
                    sorted(set(reasons)),
                )
            reasons.append(WRAP_UNPROVEN_REASON)
            return (
                "UNCERTAIN_RESULT",
                "hyphenation_wrap_artifact",
                sorted(set(reasons)),
            )

        if word_split_like(candidate):
            reasons.append("possible_extraction_word_split")
            return (
                "SPECIAL_CASE_RULE",
                "possible_extraction_word_split",
                sorted(set(reasons)),
            )

        if (
            len(present) == 2 and all(raw_texts) and raw_glyphs_agree
            and glyph_fold(raw_texts[0]) == glyph_fold(raw_texts[1])
            and not representation_semantic_signal_tokens(candidate)
        ):
            old_text = old.get("normalized_text") or old.get("text")
            new_text = new.get("normalized_text") or new.get("text")
            if classify_impact(old_text, new_text)["level"] == "non_content":
                reasons.append("impact_non_content_glyph_fold_equal")
                return (
                    "SPECIAL_CASE_RULE",
                    "glyph_or_invisible_char_artifact",
                    sorted(set(reasons)),
                )

        if marker_only_like(candidate):
            reasons.append("possible_marker_only_difference")
            return (
                "SPECIAL_CASE_RULE",
                "possible_marker_or_version_marker_difference",
                sorted(set(reasons)),
            )

        # Heading/title boundary differences such as
        #   "Artikel 19 <title>"  <->  "<title>"
        # are structural nominations; without source-backed proof they must not
        # be deterministically classified as structural-only.
        if heading_boundary_like(candidate):
            reasons.append("possible_structural_heading_boundary")
            # Heading/title boundary differences are nominations, not proven
            # structural-only changes: without source-backed proof they must
            # not be deterministically classified. Route to human review.
            return (
                "UNCERTAIN_RESULT",
                "structural_heading_boundary",
                sorted(set(reasons)),
            )

        if candidate.get("comparison_token_equivalent"):
            # The comparison representation carries no token change, but that
            # only proves tokenization/layout neutrality. A value/reference
            # signal (e.g. a stripped margin marker like ``12a``->``15b``) can
            # still live in the source/normalized representation; routing it
            # to review is required because suppression would silently drop
            # it from changes, export and redline.
            # Genuine source line wraps can erase a hyphen in the comparison
            # representation. Require the rest of the raw text to agree.
            raw_wraps_only = not raw_glyphs_agree and len({
                _SOURCE_LINE_BREAK_HYPHEN_RE.sub(
                    lambda match: _wrap_join(match) or match[0], raw
                ) for raw in raw_texts
            }) == 1
            if (
                (not raw_glyphs_agree and not raw_wraps_only)
                or representation_semantic_signal_tokens(candidate)
            ):
                reasons.append(
                    "comparison_token_equivalent_with_semantic_signal"
                )
                return (
                    "UNCERTAIN_RESULT",
                    "tokenization_neutral_with_semantic_signal",
                    sorted(set(reasons)),
                )
            reasons.append(
                "comparison_token_equivalent_despite_text_difference"
            )
            return (
                "SPECIAL_CASE_RULE",
                "tokenization_neutral_difference",
                sorted(set(reasons)),
            )

        if list_label_only_like(candidate):
            reasons.append("only_list_label_tokens_differ")
            return (
                "SPECIAL_CASE_RULE",
                "list_label_only_difference",
                sorted(set(reasons)),
            )

        if single_glyph_like(candidate):
            reasons.append("single_character_difference_probable_ocr_noise")
            return (
                "SPECIAL_CASE_RULE",
                "single_glyph_difference",
                sorted(set(reasons)),
            )

    # ------------------------------------------------------------
    # Modified-review candidates
    # ------------------------------------------------------------
    #
    # These already survived matching and were explicitly withheld
    # from automatic acceptance upstream. Semantic evaluation can
    # genuinely help here.
    # ------------------------------------------------------------

    if group == "modified_review":
        if semantic_short_pair_proof(candidate):
            reasons.append("deterministic_material_witness_verified")
            return (
                "ACCEPT_DETERMINISTIC",
                "semantic_material_change",
                sorted(set(reasons)),
            )
        return (
            "SPECIAL_CASE_LLM_OPTIONAL",
            "ambiguous_modified_pair",
            sorted(set(reasons)),
        )

    # ------------------------------------------------------------
    # One-sided ADD / DELETE candidates
    # ------------------------------------------------------------
    #
    # v2.0 over-routed every short/structural one-sided element.
    # That created 669 unnecessary LLM packets.
    #
    # Short text or heading status alone is NOT ambiguity evidence.
    # A heading such as "Artikel 105a" may simply be a real addition.
    #
    # Only positive review evidence routes a one-sided candidate to
    # optional LLM evaluation.
    # ------------------------------------------------------------

    if group in {"deleted", "added"}:
        element = old if group == "deleted" else new
        norm = norm_text_of(element)
        # Identical text exists as a residual on the OPPOSITE side: this is
        # an unresolved duplicate/relocation, never a provable add/delete.
        if norm and norm in opposite_norm_texts:
            reasons.append("identical_text_exists_on_opposite_residual_side")
            return (
                "UNCERTAIN_RESULT",
                "duplicate_exact_residual",
                sorted(set(reasons)),
            )
        # Beyond the aligned coverage frontier the other extraction cannot
        # confirm absence/presence; claiming deletion/addition is unsupported.
        reading_order = (element or {}).get("reading_order")
        if aligned_frontier is not None and isinstance(reading_order, int):
            if reading_order > aligned_frontier + FRONTIER_MARGIN:
                reasons.append("beyond_aligned_coverage_frontier")
                return (
                    "UNCERTAIN_RESULT",
                    "beyond_aligned_coverage",
                    sorted(set(reasons)),
                )
        residual_reason = residual_counterpart_uncertain_reason(candidate)
        if residual_reason:
            reasons.append(residual_reason)
            return (
                "UNCERTAIN_RESULT",
                "unresolved_residual_counterpart",
                sorted(set(reasons)),
            )

        if source_extraction_review or reasons:
            reasons.append(
                "one_sided_candidate_has_positive_review_evidence"
            )
            return (
                "SPECIAL_CASE_LLM_OPTIONAL",
                f"{group}_candidate_with_review_evidence",
                sorted(set(reasons)),
            )

        return (
            "ACCEPT_DETERMINISTIC",
            f"{group}_candidate",
            sorted(set(reasons)),
        )

    # ------------------------------------------------------------
    # Automatically accepted modified candidates
    # ------------------------------------------------------------

    if group == "modified":
        # If upstream attached explicit review evidence despite the
        # candidate living in modified, preserve that ambiguity.
        if source_extraction_review or reasons:
            return (
                "SPECIAL_CASE_LLM_OPTIONAL",
                "modified_with_review_evidence",
                sorted(set(reasons)),
            )

        return (
            "ACCEPT_DETERMINISTIC",
            "substantive_modified_candidate",
            sorted(set(reasons)),
        )

    return (
        "UNCERTAIN_RESULT",
        "unclassified_non_table_case",
        sorted(set(reasons)),
    )

# ---- cell 4 ----
def _frontier(movement: dict[str, Any], side_key: str) -> int | None:
    """Last reading_order on a side that participated in an old<->new match."""
    orders: list[int] = []
    for group in (
        "modified", "modified_review", "duplicate_exact_review", "relocated",
        "resegmentation_review",
    ):
        for c in movement.get(group) or []:
            value = (c.get(side_key) or {}).get("reading_order")
            if isinstance(value, int):
                orders.append(value)
    return max(orders) if orders else None


def route_table_match(match: dict[str, Any]) -> tuple[str,str,list[str]]:
    cls = match.get("classification")
    reasons = list(match.get("review_reasons") or [])
    # An equivalent cell/table representation is non-semantic only when there
    # is no independent extraction or assignment uncertainty.  Keeping this
    # distinction here prevents Stage 14 from suppressing visible review work.
    if cls in {"UNCHANGED", "LAYOUT_EQUIVALENT"}:
        if match.get("needs_review") or reasons:
            return (
                "UNCERTAIN_RESULT",
                "table_non_semantic_with_review_evidence",
                reasons,
            )
        return "NON_SEMANTIC", "table_non_semantic", reasons
    if cls == "EXTRACTION_ARTIFACT_REVIEW":
        return "SPECIAL_CASE_RULE", "table_extraction_artifact", reasons
    if cls == "STRUCTURE_CHANGED_REVIEW":
        return "UNCERTAIN_RESULT", "table_structure_change", reasons
    if cls == "MODIFIED":
        if match.get("needs_review"):
            return "SPECIAL_CASE_LLM_OPTIONAL", "table_modified_with_review_evidence", reasons
        return "ACCEPT_DETERMINISTIC", "table_modified", reasons
    return "UNCERTAIN_RESULT", "unclassified_table_match", reasons


STRUCTURAL_ROUTES = (
    ("resegmentation_review", "resegmentation_suspect", "SPECIAL_CASE_RULE", True),
    ("movement_review", "movement_block_review", "UNCERTAIN_RESULT", False),
    ("structural_split_merge_review", "structural_split_merge", "SPECIAL_CASE_RULE", False),
    ("structural_boundary_review", "structural_boundary", "SPECIAL_CASE_RULE", False),
)


def _build_generic_routing(
    movement: dict[str, Any], tables: dict[str, Any]
) -> dict[str, Any]:
    """Pure deterministic generic routing; no authorization, no file access.

    Returns ``routes`` (non-table + table), ``generic_review_list`` (routes
    needing human/optional/rule review), and the ``non_table_routes`` /
    ``table_routes`` compatibility aliases. Byte-for-byte independent of
    domain mode.
    """
    # Opposite-side residual texts: a one-sided candidate whose normalized
    # text also exists unmatched on the other side is an ambiguous duplicate,
    # not a provable add/delete.
    deleted_norm_texts = frozenset(
        t for t in (norm_text_of(c.get("old")) for c in movement.get("deleted") or []) if t
    )
    added_norm_texts = frozenset(
        t for t in (norm_text_of(c.get("new")) for c in movement.get("added") or []) if t
    )
    old_frontier = _frontier(movement, "old")
    new_frontier = _frontier(movement, "new")

    non_table_routes = []
    for group in NON_TABLE_GROUPS:
        for candidate in movement.get(group) or []:
            if group == "deleted":
                opposite, frontier = added_norm_texts, old_frontier
            elif group == "added":
                opposite, frontier = deleted_norm_texts, new_frontier
            else:
                opposite, frontier = frozenset(), None
            route, case_type, reasons = route_non_table(
                group,
                candidate,
                opposite_norm_texts=opposite,
                aligned_frontier=frontier,
            )
            non_table_routes.append({
                "route_id": f"NT-{len(non_table_routes)+1:06d}",
                "source_kind": "non_table",
                "source_group": group,
                "candidate_id": candidate.get("candidate_id"),
                "route": route,
                "case_type": case_type,
                "review_reasons": reasons,
                "old_element_id": (candidate.get("old") or {}).get("element_id"),
                "new_element_id": (candidate.get("new") or {}).get("element_id"),
                "similarity": candidate.get("similarity"),
            })

    # Evidence-only structural queues become explicit human/LLM review routes.
    # Each evidence record carries its own identity (``review_id`` preferred);
    # the original candidate link, when present, is preserved separately so
    # evidence never collides with the candidate route it refers to.
    for group, case_type, route, include_similarity in STRUCTURAL_ROUTES:
        for item in movement.get(group) or []:
            identity = structural_route_identity(item)
            evidence_route = {
                "route_id": f"NT-{len(non_table_routes)+1:06d}",
                "source_kind": "structural_evidence",
                "source_group": group,
                "candidate_id": identity["candidate_id"],
                "route": route,
                "case_type": case_type,
                "review_reasons": list(item.get("review_reasons") or []),
            }
            if include_similarity:
                evidence_route["similarity"] = item.get("similarity")
            if identity.get("source_candidate_id") is not None:
                evidence_route["source_candidate_id"] = identity["source_candidate_id"]
            non_table_routes.append(evidence_route)

    for item in movement.get("paragraph_resegmentation") or []:
        members = {side: item[f"{side}_elements"] for side in ("old", "new")}
        reasons = []
        states = {extraction_validation_state(member) for group in members.values() for member in group}
        if "requires_review" in states:
            reasons.append("source_extraction_requires_review")
        if "unknown" in states:
            reasons.append("source_extraction_validation_unknown")
        stored_keys = item.get("nearest_heading_keys")
        heading_keys = stored_keys if isinstance(stored_keys, dict) else {}
        old_keys, new_keys = heading_keys.get("old"), heading_keys.get("new")
        if not (
            isinstance(old_keys, list) and old_keys and len(old_keys) == len(members["old"])
            and isinstance(new_keys, list) and len(new_keys) == len(members["new"])
            and all(key == old_keys[0] for key in old_keys + new_keys)
        ):
            reasons.append("paragraph_resegmentation_heading_context_unverified")
        non_table_routes.append({
            "route_id": f"NT-{len(non_table_routes)+1:06d}",
            "source_kind": "non_table",
            "source_group": "paragraph_resegmentation",
            "candidate_id": item["candidate_id"],
            "route": (
                "UNCERTAIN_RESULT" if reasons else
                "SPECIAL_CASE_RULE" if item["impact_level"] == "non_content" else
                "ACCEPT_DETERMINISTIC"
            ),
            "case_type": item["kind"],
            "review_reasons": reasons,
        })

    table_routes = []
    for match in tables.get("table_matches") or []:
        route, case_type, reasons = route_table_match(match)
        table_routes.append({
            "route_id": f"TB-{len(table_routes)+1:06d}",
            "source_kind": "table_match",
            "source_group": "table_matches",
            "candidate_id": match.get("table_match_id"),
            "route": route,
            "case_type": case_type,
            "review_reasons": sorted(set(reasons)),
            "classification": match.get("classification"),
        })

    for group, case_type in (
        ("deleted_table_candidates", "deleted_table_candidate"),
        ("added_table_candidates", "added_table_candidate"),
    ):
        for table in tables.get(group) or []:
            table_routes.append({
                "route_id": f"TB-{len(table_routes)+1:06d}",
                "source_kind": "table_residual",
                "source_group": group,
                "candidate_id": table.get("logical_table_id"),
                "route": "SPECIAL_CASE_LLM_OPTIONAL",
                "case_type": case_type,
                "review_reasons": ["one_sided_table_candidate"],
            })

    for item in tables.get("unresolved_table_groups") or []:
        table_routes.append({
            "route_id": f"TB-{len(table_routes)+1:06d}",
            "source_kind": "table_review_group",
            "source_group": "unresolved_table_groups",
            "candidate_id": item.get("review_group_id"),
            "route": "UNCERTAIN_RESULT",
            "case_type": "unresolved_table_group",
            "review_reasons": [item.get("reason") or "unresolved_table_group"],
        })

    routes = non_table_routes + table_routes
    # The generic review queue contains only unresolved work.  Deterministic
    # SPECIAL_CASE_RULE decisions remain in ``routes`` for traceability but do
    # not consume review bandwidth.
    generic_review_list = [
        route for route in routes
        if route["route"] in {"UNCERTAIN_RESULT", "HUMAN_REVIEW", "SPECIAL_CASE_LLM_OPTIONAL"}
    ]
    return {
        "routes": routes,
        "generic_review_list": generic_review_list,
        "non_table_routes": non_table_routes,
        "table_routes": table_routes,
    }

def build_generic_routing(
    movement: dict[str, Any], tables: dict[str, Any],
) -> dict[str, Any]:
    """Route current candidates while preserving earlier route identities."""
    routing = _build_generic_routing(movement, tables)
    if movement.get("candidate_identity_history"):
        previous = _build_generic_routing(previous_candidate_view(movement), tables)
        previous_routes = previous["non_table_routes"]
        previous_by_candidate = {route["candidate_id"]: route["route_id"] for route in previous_routes}
        next_slot = len(previous_routes) + 1
        for route in routing["non_table_routes"]:
            route_id = previous_by_candidate.get(route["candidate_id"])
            if route_id is None:
                route_id = f"NT-{next_slot:06d}"
                next_slot += 1
            route["route_id"] = route_id
        routing["previous_non_table_routes"] = previous_routes
    return routing


# ---- cell 5 ----
def build_domain_verification_records(
    routes: list[dict[str, Any]],
    profile: dict[str, Any],
    verifier,
) -> list[dict[str, Any]]:
    """Return a not-applicable verification record for every route.

    Domain verification is retired: the compatibility record always carries
    ``status == "not_applicable"`` and the ``verifier`` argument is accepted
    only for signature compatibility; it is never called. Route identity is
    preserved in each record.
    """
    return [
        {**route, "domain_verification": {"status": "not_applicable"}}
        for route in routes
    ]


def build_domain_verification_review(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the verification records needing review.

    Domain verification is retired, so this is always empty; the input is
    never mutated.
    """
    return []


def main() -> None:
    output_dir = find_project_root() / "output"
    step9_path = output_dir / "comparison_candidates_with_movement.json"
    step10_path = output_dir / "table_comparison_result.json"
    output_path = output_dir / "review_routing.json"
    movement = load_json(step9_path)
    tables = load_json(step10_path)

    if movement.get("comparison_stage") != EXPECTED_STEP9_STAGE:
        raise ValueError("Step 11 v2 requires the current Step 9 result.")
    if tables.get("comparison_stage") != EXPECTED_STEP10_STAGE:
        raise ValueError("Step 11 v2 requires the current Step 10 result.")

    print("Loaded Step 9 + Step 10.")

    # Orchestration boundary: resolve the generic domain profile. No document
    # content is inspected and no CRR evidence is read.
    mode = parse_domain_mode(os.getenv("CRR_DOMAIN_MODE"))
    profile = resolve_domain_profile(mode)

    generic = build_generic_routing(movement, tables)
    routes = generic["routes"]

    # Exactly one compatibility verification record per route; verification is
    # retired, so every record is ``not_applicable`` and no verifier runs.
    domain_verification_records = build_domain_verification_records(
        routes, profile, None
    )
    domain_verification_review = build_domain_verification_review(
        domain_verification_records
    )

    status_counts = Counter()
    for record in domain_verification_records:
        status_counts[
            (record.get("domain_verification") or {}).get("status", "not_applicable")
        ] += 1
    domain_verification = {
        "enabled": False,
        "records": domain_verification_records,
        "status_counts": dict(status_counts),
    }

    route_counts = Counter(item["route"] for item in routes)
    case_counts = Counter(item["case_type"] for item in routes)

    result = {
        "schema_version": "1.0.0",
        "comparison_stage": "deterministic_review_routing",
        "source_files": {
            "step9": step9_path.name,
            "step10": step10_path.name,
        },
        "processing": {
            "version": STEP11_VERSION,
            "llm_called": False,
            "matching_recomputed": False,
            "source_text_rewritten": False,
            "authoritative_hierarchy_used": False,
        },
        "statistics": {
            "route_counts": dict(route_counts),
            "case_type_counts": dict(case_counts),
            "total_routes": len(routes),
            "llm_optional_cases": route_counts["SPECIAL_CASE_LLM_OPTIONAL"],
            "human_review_cases": route_counts["HUMAN_REVIEW"],
            "uncertain_result_cases": route_counts["UNCERTAIN_RESULT"],
            "llm_optional_fraction": round(
                route_counts["SPECIAL_CASE_LLM_OPTIONAL"] / max(1, len(routes)), 4
            ),
        },
        "routes": routes,
        "generic_review_list": generic["generic_review_list"],
        "domain_profile": profile,
        "domain_verification": domain_verification,
        "domain_verification_review": domain_verification_review,
        "non_table_routes": generic["non_table_routes"],
        "table_routes": generic["table_routes"],
        **({"previous_non_table_routes": generic["previous_non_table_routes"]}
           if "previous_non_table_routes" in generic else {}),
    }

    # Every route_id must be unique.
    route_ids = [x["route_id"] for x in routes]
    require(len(route_ids) == len(set(route_ids)), "s11: duplicate route_id")

    save_json(result, output_path)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", output_path)

    # ---- cell 6 ----
    print("\n=== LLM-OPTIONAL CASE TYPES ===")
    llm_case_counts = Counter(
        item["case_type"]
        for item in routes
        if item["route"] == "SPECIAL_CASE_LLM_OPTIONAL"
    )
    print(json.dumps(dict(llm_case_counts), ensure_ascii=False, indent=2))

    print("\n=== FIRST 25 LLM-OPTIONAL ROUTES ===")
    for item in [
        x for x in routes
        if x["route"] == "SPECIAL_CASE_LLM_OPTIONAL"
    ][:25]:
        print(
            item["route_id"],
            "|",
            item["case_type"],
            "|",
            item.get("candidate_id"),
            "| reasons=",
            item.get("review_reasons"),
        )


if __name__ == "__main__":
    main()
