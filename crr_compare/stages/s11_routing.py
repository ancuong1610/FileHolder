"""Deterministic review routing (s11): generic-only routing.

Auto-converted from 11_review_routing_special_cases_v2_1_selective_llm.ipynb (code cells only).

Generic routing is pure and mode-independent: ``build_generic_routing`` never
imports authorization and never reads CRR evidence. Domain verification is
retired: the compatibility metadata this stage emits is always
``not_applicable`` and no verifier is ever invoked. CRR-looking source text is
ordinary document content and never activates a specialized domain.
"""
from crr_compare.common import find_project_root, get_page_number, set_page_number
from crr_compare.validation import require
from crr_compare.domain import parse_domain_mode, resolve_domain_profile
from crr_compare.lang_profile import SUSPENDED_HYPHEN_STOPWORDS
from crr_compare.route_identity import structural_route_identity

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

# ---- cell 2 ----
PROJECT_ROOT = find_project_root()
OUTPUT_DIR = PROJECT_ROOT / "output"

STEP9_PATH = OUTPUT_DIR / "comparison_candidates_with_movement.json"
STEP10_PATH = OUTPUT_DIR / "table_comparison_result.json"
OUTPUT_PATH = OUTPUT_DIR / "review_routing.json"


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


def extraction_validation_state(element: dict[str, Any] | None) -> str:
    """Classify an element's extraction metadata: 'requires_review', 'unknown', or 'ok'.

    Absent or malformed metadata is 'unknown' (never safe): only an explicit
    boolean ``requires_extraction_review`` counts as a valid verdict.
    """
    if not isinstance(element, dict):
        return "unknown"
    validation = element.get("extraction_validation")
    if not isinstance(validation, dict):
        return "unknown"
    flag = validation.get("requires_extraction_review")
    if not isinstance(flag, bool):
        return "unknown"
    return "requires_review" if flag else "ok"

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


def _line_break_hyphen_joins(raw: str) -> set[str]:
    """Joined words from real lowercase, non-stopword ``-\\n`` breaks in raw.

    Mirrors the approved s04 rule: only a lowercase continuation that is not a
    suspended-hyphen function word is a real line-wrap. Uppercase/digit
    continuations and stopwords are never proof.
    """
    joined: set[str] = set()
    for match in _SOURCE_LINE_BREAK_HYPHEN_RE.finditer(raw):
        left, right = match.group(1), match.group(2)
        if not right[:1].islower() or right.casefold() in SUSPENDED_HYPHEN_STOPWORDS:
            continue
        joined.add(left + right)
    return joined


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
            "HUMAN_REVIEW",
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

    if not extraction_forces_review:
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
                    "HUMAN_REVIEW",
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
                "HUMAN_REVIEW",
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
                "HUMAN_REVIEW",
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
            if representation_semantic_signal_tokens(candidate):
                reasons.append(
                    "comparison_token_equivalent_with_semantic_signal"
                )
                return (
                    "HUMAN_REVIEW",
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
                "HUMAN_REVIEW",
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
                    "HUMAN_REVIEW",
                    "beyond_aligned_coverage",
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
        "HUMAN_REVIEW",
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
    if cls in {"UNCHANGED", "LAYOUT_EQUIVALENT"}:
        return "NON_SEMANTIC", "table_non_semantic", reasons
    if cls == "EXTRACTION_ARTIFACT_REVIEW":
        return "SPECIAL_CASE_RULE", "table_extraction_artifact", reasons
    if cls == "STRUCTURE_CHANGED_REVIEW":
        return "HUMAN_REVIEW", "table_structure_change", reasons
    if cls == "MODIFIED":
        if match.get("needs_review"):
            return "SPECIAL_CASE_LLM_OPTIONAL", "table_modified_with_review_evidence", reasons
        return "ACCEPT_DETERMINISTIC", "table_modified", reasons
    return "HUMAN_REVIEW", "unclassified_table_match", reasons


def build_generic_routing(
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
    for group, case_type, route, include_similarity in (
        ("resegmentation_review", "resegmentation_suspect", "SPECIAL_CASE_RULE", True),
        ("movement_review", "movement_block_review", "HUMAN_REVIEW", False),
        ("structural_split_merge_review", "structural_split_merge", "SPECIAL_CASE_RULE", False),
        ("structural_boundary_review", "structural_boundary", "SPECIAL_CASE_RULE", False),
    ):
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
            "route": "HUMAN_REVIEW",
            "case_type": "unresolved_table_group",
            "review_reasons": [item.get("reason") or "unresolved_table_group"],
        })

    routes = non_table_routes + table_routes
    # The generic review queue contains only unresolved work.  Deterministic
    # SPECIAL_CASE_RULE decisions remain in ``routes`` for traceability but do
    # not consume review bandwidth.
    generic_review_list = [
        route for route in routes
        if route["route"] in {"HUMAN_REVIEW", "SPECIAL_CASE_LLM_OPTIONAL"}
    ]
    return {
        "routes": routes,
        "generic_review_list": generic_review_list,
        "non_table_routes": non_table_routes,
        "table_routes": table_routes,
    }

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
    movement = load_json(STEP9_PATH)
    tables = load_json(STEP10_PATH)

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
            "step9": STEP9_PATH.name,
            "step10": STEP10_PATH.name,
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
    }

    # Every route_id must be unique.
    route_ids = [x["route_id"] for x in routes]
    require(len(route_ids) == len(set(route_ids)), "s11: duplicate route_id")

    save_json(result, OUTPUT_PATH)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print("Saved:", OUTPUT_PATH)

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
