"""Stage 07: match Step-6 residual elements as modified pairs.

The module is import-safe: importing it only defines the pure matching
functions (:func:`build_result`, :func:`validate_result`) and never resolves a
workspace, reads or writes pipeline files, prints, or mutates the environment.
File orchestration lives in :func:`main`, which the ``__name__ == "__main__"``
guard runs for the CLI stage runner.
"""
from document_compare.shared.paths import find_project_root
from document_compare.phase1.extraction.di_fields import get_page_number, set_page_number
from document_compare.phase1.contracts.validation import require
from document_compare.phase1.matching.semantic_evidence import assess_pair

# ---- imports and configuration constants ----

import json
import re
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

STEP7_VERSION = "4.2.0-fast-safe-identical-guard"
INPUT_SCHEMA_VERSION = "0.2.0"
RESULT_SCHEMA_VERSION = "0.3.0"
INPUT_STAGE = "exact_element_matching_with_provisional_context"
MIN_TOKEN_LENGTH = 3
MAX_TOKEN_DOCUMENT_FRACTION = 0.03
MIN_REVIEW_SCORE = 0.72
MIN_AUTOMATIC_SCORE = 0.86
MIN_AUTOMATIC_MARGIN = 0.10
SHORT_TEXT_REVIEW_CHARACTERS = 40
MAX_CONTEXT_RETRIEVAL_CANDIDATES = 40
MAX_TOKEN_FALLBACK_CANDIDATES = 30
MAX_EXPENSIVE_CANDIDATES_PER_OLD = 12
CHEAP_TOKEN_WEIGHT = 0.80
CHEAP_LENGTH_WEIGHT = 0.20
TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)

# ---- I/O helpers and input validation ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle: value = json.load(handle)
    if not isinstance(value, dict): raise TypeError(f"{path} must be a JSON object.")
    return value

def save_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2); handle.write("\n")
    temporary.replace(path)

def validate_input(value: dict[str, Any]) -> None:
    if value.get("schema_version") != INPUT_SCHEMA_VERSION or value.get("comparison_stage") != INPUT_STAGE:
        raise ValueError("Step 7 v3 requires the Step-6 v3 exact-match result.")
    processing = value.get("processing") or {}
    if processing.get("authoritative_hierarchy_used") is not False:
        raise ValueError("Step 7 v3 must not consume a result based on inferred hierarchy.")
    for field in ("unmatched_old", "unmatched_new", "old_document", "new_document"):
        if not isinstance(value.get(field), list if field.startswith("unmatched") else dict):
            raise ValueError(f"Missing or invalid {field!r}.")

# ---- text similarity and candidate retrieval ----
def matching_text(item: dict[str, Any]) -> str:
    value = item.get("comparison_text")
    if isinstance(value, str):
        return value
    value = item.get("normalized_text")
    if isinstance(value, str):
        return value
    return str(item.get("text") or "")


def tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_PATTERN.findall(text)
        if len(token) >= MIN_TOKEN_LENGTH
    }


def context_keys(item: dict[str, Any]) -> set[str]:
    return set(
        (item.get("provisional_anchor_context") or {}).get("recent_anchor_keys")
        or []
    )


def token_f1(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left.intersection(right))
    return 2 * overlap / (len(left) + len(right))


def length_ratio_text(left_text: str, right_text: str) -> float:
    return min(len(left_text), len(right_text)) / max(
        len(left_text),
        len(right_text),
        1,
    )


def cheap_similarity(old: dict[str, Any], new: dict[str, Any]) -> float:
    """
    Cheap pre-ranking only.
    No SequenceMatcher and no context boost.
    """
    old_text = matching_text(old)
    new_text = matching_text(new)

    f1 = token_f1(tokens(old_text), tokens(new_text))
    length_ratio = length_ratio_text(old_text, new_text)

    return (
        CHEAP_TOKEN_WEIGHT * f1
        + CHEAP_LENGTH_WEIGHT * length_ratio
    )


def similarity(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """
    Expensive content similarity.

    Provisional context is recorded as evidence, but contributes 0.0 to
    the semantic similarity score.
    """
    old_text = matching_text(old)
    new_text = matching_text(new)

    old_tokens = tokens(old_text)
    new_tokens = tokens(new_text)

    char_ratio = SequenceMatcher(
        None,
        old_text,
        new_text,
        autojunk=False,
    ).ratio()

    f1 = token_f1(old_tokens, new_tokens)
    length_ratio = length_ratio_text(old_text, new_text)

    shared_context = sorted(
        context_keys(old).intersection(context_keys(new))
    )

    score = (
        0.65 * char_ratio
        + 0.25 * f1
        + 0.10 * length_ratio
    )

    return {
        "score": round(score, 6),
        "char_ratio": round(char_ratio, 6),
        "token_f1": round(f1, 6),
        "length_ratio": round(length_ratio, 6),
        "shared_provisional_anchor_keys": shared_context,
        "context_used_as_similarity_boost": False,
        "same_element_type": old.get("element_type")
        == new.get("element_type"),
    }


def token_candidate_index(
    items: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], set[int]], set[tuple[str, str]]]:
    """
    Rare-token fallback index, partitioned by element_type.
    """
    postings: dict[tuple[str, str], set[int]] = defaultdict(set)

    for index, item in enumerate(items):
        element_type = str(item.get("element_type") or "")
        for token in tokens(matching_text(item)):
            postings[(element_type, token)].add(index)

    maximum = max(1, int(len(items) * MAX_TOKEN_DOCUMENT_FRACTION))

    informative = {
        key
        for key, locations in postings.items()
        if len(locations) <= maximum
    }

    return postings, informative


def provisional_context_index(
    items: list[dict[str, Any]],
) -> dict[tuple[str, str], set[int]]:
    """
    Search accelerator only.

    Each recent provisional anchor key points to NEW elements of the same
    element type. This does not make the anchor authoritative.
    """
    postings: dict[tuple[str, str], set[int]] = defaultdict(set)

    for index, item in enumerate(items):
        element_type = str(item.get("element_type") or "")
        for key in context_keys(item):
            postings[(element_type, key)].add(index)

    return postings


def context_candidate_locations(
    old: dict[str, Any],
    postings: dict[tuple[str, str], set[int]],
) -> list[int]:
    """
    Collect NEW elements sharing provisional context, ranked by how many
    recent anchor keys they share with OLD.
    """
    element_type = str(old.get("element_type") or "")
    old_keys = context_keys(old)

    if not old_keys:
        return []

    votes: Counter[int] = Counter()

    for key in old_keys:
        for location in postings.get((element_type, key), set()):
            votes[location] += 1

    return [
        index
        for index, _ in votes.most_common(
            MAX_CONTEXT_RETRIEVAL_CANDIDATES
        )
    ]


def token_candidate_locations(
    old: dict[str, Any],
    postings: dict[tuple[str, str], set[int]],
    informative: set[tuple[str, str]],
) -> list[int]:
    """
    Rare-token fallback used only when provisional context does not produce
    usable candidates.
    """
    element_type = str(old.get("element_type") or "")
    votes: Counter[int] = Counter()

    useful_tokens = []
    for token in tokens(matching_text(old)):
        key = (element_type, token)
        if key in informative:
            useful_tokens.append(
                (len(postings.get(key, set())), token)
            )

    # Rarest useful terms first.
    useful_tokens.sort()

    for _, token in useful_tokens[:8]:
        key = (element_type, token)
        for location in postings.get(key, set()):
            votes[location] += 1

    return [
        index
        for index, _ in votes.most_common(
            MAX_TOKEN_FALLBACK_CANDIDATES
        )
    ]


def pre_rank_candidates(
    old: dict[str, Any],
    news: list[dict[str, Any]],
    locations: list[int],
) -> list[int]:
    """
    Bound expensive SequenceMatcher work to a very small top-K.
    """
    ranked = []

    for new_index in locations:
        new = news[new_index]

        if old.get("element_type") != new.get("element_type"):
            continue

        ranked.append(
            (
                cheap_similarity(old, new),
                new_index,
            )
        )

    ranked.sort(
        key=lambda item: (-item[0], item[1])
    )

    return [
        new_index
        for _, new_index in ranked[
            :MAX_EXPENSIVE_CANDIDATES_PER_OLD
        ]
    ]

# ---- diagnostic tracing and reciprocal-best matching ----
def _diagnostic_record(
    *,
    stage: str,
    old: dict[str, Any],
    new: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Build one element-ID-only diagnostic record (never carries text)."""
    record: dict[str, Any] = {
        "stage": stage,
        "old_element_id": old.get("element_id"),
        "old_element_type": old.get("element_type"),
        "new_element_id": None if new is None else new.get("element_id"),
        "new_element_type": None if new is None else new.get("element_type"),
    }
    record.update(fields)
    return record


def _record_diagnostic(
    diagnostics: list[dict[str, Any]] | None,
    *,
    stage: str,
    old: dict[str, Any],
    new: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """Append a trace record; a no-op when diagnostics are disabled."""
    if diagnostics is None:
        return
    diagnostics.append(_diagnostic_record(stage=stage, old=old, new=new, **fields))


def compact_pair(
    old: dict[str, Any],
    new: dict[str, Any],
    evidence: dict[str, Any],
    *,
    classification: str,
    reasons: list[str],
    retrieval_method: str,
) -> dict[str, Any]:
    return {
        "match_id": None,
        "match_type": classification,
        "match_method": "reciprocal_similarity_alignment",
        "candidate_retrieval_method": retrieval_method,
        "similarity": evidence["score"],
        "similarity_evidence": evidence,
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "context_is_authoritative_hierarchy": False,
        "normalization_evidence": {
            "old_flags": old.get("normalization_flags", []),
            "new_flags": new.get("normalization_flags", []),
        },
        "old": old,
        "new": new,
    }


def build_result(
    exact: dict[str, Any],
    *,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Match Step-6 residuals.

    ``diagnostics`` is an optional opt-in collector. When a list is passed it
    receives element-ID-only records for the real candidate path: retrieved
    (primary context or rare-token retrieval, plus the fallback retry),
    shortlisted after the expensive-work cap, every expensive score including
    those below the review floor, the selected reciprocal-best pair, and the
    final disposition. It never receives element text and never changes the
    matching result.
    """
    olds = list(exact["unmatched_old"])
    news = list(exact["unmatched_new"])

    token_postings, informative_tokens = token_candidate_index(news)
    context_postings = provisional_context_index(news)

    old_rankings: dict[
        int,
        list[tuple[int, dict[str, Any]]]
    ] = {}

    new_rankings: dict[
        int,
        list[tuple[int, dict[str, Any]]]
    ] = defaultdict(list)

    retrieval_methods: dict[int, str] = {}

    expensive_pairs_scored = 0
    context_retrievals = 0
    token_fallback_retrievals = 0
    olds_without_candidates = 0

    for old_index, old in enumerate(olds):
        # 1) Prefer bounded candidates sharing provisional structural evidence.
        candidate_locations = context_candidate_locations(
            old,
            context_postings,
        )

        if candidate_locations:
            retrieval_method = "shared_provisional_context"
            context_retrievals += 1
        else:
            # 2) Fallback: rare informative tokens.
            candidate_locations = token_candidate_locations(
                old,
                token_postings,
                informative_tokens,
            )
            retrieval_method = "rare_token_fallback"
            token_fallback_retrievals += 1

        for location in candidate_locations:
            _record_diagnostic(
                diagnostics,
                stage="retrieved",
                old=old,
                new=news[location],
                retrieval_method=retrieval_method,
                candidate_phase="primary",
            )

        candidate_locations = pre_rank_candidates(
            old,
            news,
            candidate_locations,
        )

        for location in candidate_locations:
            _record_diagnostic(
                diagnostics,
                stage="shortlisted",
                old=old,
                new=news[location],
                retrieval_method=retrieval_method,
                candidate_phase="primary",
            )

        retrieval_methods[old_index] = retrieval_method

        if not candidate_locations:
            olds_without_candidates += 1
            old_rankings[old_index] = []
            continue

        expensive_pairs_scored += len(candidate_locations)

        ranked = []

        for new_index in candidate_locations:
            new = news[new_index]

            evidence = similarity(old, new)

            _record_diagnostic(
                diagnostics,
                stage="scored",
                old=old,
                new=new,
                retrieval_method=retrieval_method,
                candidate_phase="primary",
                score=evidence["score"],
                passed_review_floor=evidence["score"] >= MIN_REVIEW_SCORE,
            )

            if evidence["score"] < MIN_REVIEW_SCORE:
                continue

            ranked.append(
                (new_index, evidence)
            )
            new_rankings[new_index].append(
                (old_index, evidence)
            )

        old_rankings[old_index] = sorted(
            ranked,
            key=lambda item: (
                -item[1]["score"],
                item[0],
            ),
        )

        # Context is only a bounded accelerator, never authoritative: when it
        # produced candidates but none scored, retry with rare-token retrieval
        # so the true counterpart cannot be lost to an ADD+DELETE split.
        if not old_rankings[old_index] and retrieval_method == "shared_provisional_context":
            fallback_locations = token_candidate_locations(
                old,
                token_postings,
                informative_tokens,
            )
            if fallback_locations:
                for location in fallback_locations:
                    _record_diagnostic(
                        diagnostics,
                        stage="retrieved",
                        old=old,
                        new=news[location],
                        retrieval_method="rare_token_fallback",
                        candidate_phase="fallback",
                    )
                fallback_locations = pre_rank_candidates(old, news, fallback_locations)
                token_fallback_retrievals += 1
                expensive_pairs_scored += len(fallback_locations)
                for location in fallback_locations:
                    _record_diagnostic(
                        diagnostics,
                        stage="shortlisted",
                        old=old,
                        new=news[location],
                        retrieval_method="rare_token_fallback",
                        candidate_phase="fallback",
                    )
                ranked = []
                for new_index in fallback_locations:
                    new = news[new_index]
                    evidence = similarity(old, new)
                    _record_diagnostic(
                        diagnostics,
                        stage="scored",
                        old=old,
                        new=new,
                        retrieval_method="rare_token_fallback",
                        candidate_phase="fallback",
                        score=evidence["score"],
                        passed_review_floor=evidence["score"] >= MIN_REVIEW_SCORE,
                    )
                    if evidence["score"] < MIN_REVIEW_SCORE:
                        continue
                    ranked.append((new_index, evidence))
                    new_rankings[new_index].append((old_index, evidence))
                old_rankings[old_index] = sorted(
                    ranked,
                    key=lambda item: (-item[1]["score"], item[0]),
                )

    for new_index in new_rankings:
        new_rankings[new_index].sort(
            key=lambda item: (
                -item[1]["score"],
                item[0],
            )
        )

    accepted = []
    review_pairs = []
    duplicate_exact_review_pairs = []
    matched_old = set()
    matched_new = set()

    for old_index, ranked in old_rankings.items():
        if not ranked:
            continue

        new_index, evidence = ranked[0]

        reverse = new_rankings.get(
            new_index,
            [],
        )

        if not reverse or reverse[0][0] != old_index:
            continue

        if old_index in matched_old or new_index in matched_new:
            continue

        second_old = (
            ranked[1][1]["score"]
            if len(ranked) > 1
            else 0.0
        )

        second_new = (
            reverse[1][1]["score"]
            if len(reverse) > 1
            else 0.0
        )

        margin = min(
            evidence["score"] - second_old,
            evidence["score"] - second_new,
        )

        evidence = dict(evidence)
        evidence["reciprocal_best"] = True
        evidence["minimum_best_margin"] = round(
            margin,
            6,
        )

        old = olds[old_index]
        new = news[new_index]

        _record_diagnostic(
            diagnostics,
            stage="selected",
            old=old,
            new=new,
            retrieval_method=retrieval_methods.get(old_index),
            score=evidence["score"],
            minimum_best_margin=evidence["minimum_best_margin"],
        )

        # Hard semantic invariant:
        # identical comparison representations are never modifications.
        # Step 6 may leave duplicate exact texts unresolved because source
        # assignment is ambiguous. Step 7 must preserve that ambiguity
        # instead of relabeling the pair as "modified".
        if matching_text(old) == matching_text(new):
            duplicate_exact_review_pairs.append({
                "match_id": None,
                "match_type": "duplicate_exact_review",
                "match_method": "reciprocal_similarity_alignment_identical_text_guard",
                "similarity": evidence["score"],
                "similarity_evidence": {
                    **evidence,
                    "identical_comparison_text": True,
                    "reciprocal_best": True,
                },
                "needs_review": True,
                "review_reasons": [
                    "identical_comparison_text_but_step6_assignment_ambiguous"
                ],
                "context_is_authoritative_hierarchy": False,
                "candidate_retrieval_method": retrieval_methods.get(old_index),
                "old": old,
                "new": new,
            })

            _record_diagnostic(
                diagnostics,
                stage="classified",
                old=old,
                new=new,
                retrieval_method=retrieval_methods.get(old_index),
                disposition="duplicate_exact_review",
                needs_review=True,
                review_reasons=[
                    "identical_comparison_text_but_step6_assignment_ambiguous"
                ],
            )

            matched_old.add(old_index)
            matched_new.add(new_index)
            continue

        short = (
            min(
                len(matching_text(old)),
                len(matching_text(new)),
            )
            < SHORT_TEXT_REVIEW_CHARACTERS
        )

        reasons = []

        if short:
            reasons.append(
                "short_text_similarity_pair"
            )

        if not evidence[
            "shared_provisional_anchor_keys"
        ]:
            reasons.append(
                "no_shared_provisional_anchor_evidence"
            )

        automatic = (
            evidence["score"] >= MIN_AUTOMATIC_SCORE
            and margin >= MIN_AUTOMATIC_MARGIN
            and not short
        )

        pair = compact_pair(
            old,
            new,
            evidence,
            classification=(
                "modified_alignment_candidate"
                if automatic
                else "modified_alignment_review"
            ),
            reasons=(
                reasons
                if automatic
                else (
                    reasons
                    or [
                        "similarity_or_margin_below_automatic_threshold"
                    ]
                )
            ),
            retrieval_method=retrieval_methods[
                old_index
            ],
        )

        # Additive source-bound evidence: only a complete deterministic
        # witness is attached.  A missing witness is legacy ``not_assessed``
        # at every consumer and never weakens the existing review rules.
        assessment = assess_pair(
            old,
            new,
            correspondence=(
                "verified"
                if margin >= MIN_AUTOMATIC_MARGIN
                else "proposed"
            ),
        )
        if assessment.get("basis") == "deterministic_witness":
            pair["semantic_assessment"] = assessment

        if automatic:
            accepted.append(pair)
        else:
            review_pairs.append(pair)

        _record_diagnostic(
            diagnostics,
            stage="classified",
            old=old,
            new=new,
            retrieval_method=retrieval_methods.get(old_index),
            disposition=pair["match_type"],
            needs_review=pair["needs_review"],
            review_reasons=list(pair["review_reasons"]),
        )

        matched_old.add(old_index)
        matched_new.add(new_index)

    accepted.sort(
        key=lambda x: x["old"].get(
            "reading_order",
            0,
        )
    )
    review_pairs.sort(
        key=lambda x: x["old"].get(
            "reading_order",
            0,
        )
    )

    for index, item in enumerate(
        accepted,
        1,
    ):
        item["match_id"] = (
            f"MODIFIED-{index:06d}"
        )

    for index, item in enumerate(
        review_pairs,
        1,
    ):
        item["match_id"] = (
            f"MODIFIED-REVIEW-{index:06d}"
        )

    duplicate_exact_review_pairs.sort(
        key=lambda x: x["old"].get("reading_order", 0)
    )
    for index, item in enumerate(
        duplicate_exact_review_pairs,
        1,
    ):
        item["match_id"] = (
            f"DUPLICATE-EXACT-REVIEW-{index:06d}"
        )

    residual_old = [
        item
        for index, item in enumerate(olds)
        if index not in matched_old
    ]

    residual_new = [
        item
        for index, item in enumerate(news)
        if index not in matched_new
    ]

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "comparison_stage":
            "modified_element_matching_with_provisional_context",
        "processing": {
            "version": STEP7_VERSION,
            "di_text_rewritten": False,
            "layout_hyphen_repair_performed": False,
            "authoritative_hierarchy_used": False,
            "provisional_context_used_as_support_only": True,
            "provisional_context_used_for_candidate_retrieval": True,
            "provisional_context_used_as_similarity_boost": False,
        },
        "old_document": exact["old_document"],
        "new_document": exact["new_document"],
        "matching_policy": {
            "candidate_generation":
                "shared_provisional_context_then_rare_token_fallback",
            "max_expensive_candidates_per_old":
                MAX_EXPENSIVE_CANDIDATES_PER_OLD,
            "reciprocal_best_required": True,
            "minimum_review_score":
                MIN_REVIEW_SCORE,
            "minimum_automatic_score":
                MIN_AUTOMATIC_SCORE,
            "minimum_automatic_margin":
                MIN_AUTOMATIC_MARGIN,
            "short_text_requires_review": True,
            "context_is_authoritative_hierarchy": False,
        },
        "statistics": {
            "step6_unmatched_old": len(olds),
            "step6_unmatched_new": len(news),
            "expensive_candidate_pairs_scored":
                expensive_pairs_scored,
            "context_retrievals":
                context_retrievals,
            "token_fallback_retrievals":
                token_fallback_retrievals,
            "old_elements_without_candidates":
                olds_without_candidates,
            "accepted_modified_alignments":
                len(accepted),
            "review_modified_alignments":
                len(review_pairs),
            "duplicate_exact_review_pairs":
                len(duplicate_exact_review_pairs),
            "residual_unmatched_old":
                len(residual_old),
            "residual_unmatched_new":
                len(residual_new),
            "accepted_review_reason_counts":
                dict(
                    sorted(
                        Counter(
                            reason
                            for item
                            in accepted + review_pairs
                            for reason
                            in item["review_reasons"]
                        ).items()
                    )
                ),
        },
        "modified_matches": accepted,
        "review_pairs": review_pairs,
        "duplicate_exact_review_pairs": duplicate_exact_review_pairs,
        "unmatched_old": residual_old,
        "unmatched_new": residual_new,
    }

# ---- result validation ----
def validate_result(
    value: dict[str, Any],
) -> None:
    modified_pairs = (
        value["modified_matches"]
        + value["review_pairs"]
    )
    duplicate_exact_pairs = value.get(
        "duplicate_exact_review_pairs",
        [],
    )

    old_ids = [
        item["old"]["element_id"]
        for item in modified_pairs + duplicate_exact_pairs
    ]
    new_ids = [
        item["new"]["element_id"]
        for item in modified_pairs + duplicate_exact_pairs
    ]

    require(
        len(old_ids) == len(set(old_ids)),
        "s07: an OLD element was matched more than once",
    )
    require(
        len(new_ids) == len(set(new_ids)),
        "s07: a NEW element was matched more than once",
    )

    for item in modified_pairs:
        require(
            item["old"]["element_type"]
            == item["new"]["element_type"],
            "s07: modified match crosses element types",
        )
        require(
            item["similarity_evidence"]["reciprocal_best"]
            is True,
            "s07: modified match is not reciprocal-best",
        )
        require(
            item["context_is_authoritative_hierarchy"]
            is False,
            "s07: provisional context treated as authoritative",
        )

        # Critical invariant:
        # a modified pair must actually differ in comparison representation.
        require(
            matching_text(item["old"]) != matching_text(item["new"]),
            "s07: modified pair is text-identical",
        )

    for item in duplicate_exact_pairs:
        require(
            item["old"]["element_type"]
            == item["new"]["element_type"],
            "s07: duplicate-exact pair crosses element types",
        )
        require(
            matching_text(item["old"])
            == matching_text(item["new"]),
            "s07: duplicate-exact pair text differs",
        )
        require(
            item["match_type"] == "duplicate_exact_review",
            "s07: unexpected duplicate-exact match_type",
        )
        require(
            item["needs_review"] is True,
            "s07: duplicate-exact pair must require review",
        )


# ---- stage entry point ----
def main() -> None:
    """Run Stage 07 against the workspace and save the modified-match result."""
    project_root = find_project_root()
    output_dir = project_root / "output"
    input_path = output_dir / "exact_element_matches.json"
    output_path = output_dir / "modified_element_matches.json"

    print("Step 7 version:", STEP7_VERSION)
    print("Input:", input_path)

    exact = load_json(input_path)
    validate_input(exact)

    started = time.perf_counter()

    result = build_result(exact)

    validate_result(result)

    result["processing"]["elapsed_seconds"] = round(
        time.perf_counter() - started,
        3,
    )

    save_json(
        result,
        output_path,
    )

    print(
        json.dumps(
            result["statistics"],
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "Elapsed:",
        result["processing"]["elapsed_seconds"],
        "seconds",
    )

    print(
        "Saved:",
        output_path,
    )

    for title, records in (
        ("ACCEPTED", result["modified_matches"]),
        ("REVIEW", result["review_pairs"]),
    ):
        print(f"\nFirst 15 {title} modified alignments:")
        for item in records[:15]:
            print(
                item["match_id"],
                "| score=",
                item["similarity"],
                "| reasons=",
                item["review_reasons"],
                "| old=",
                repr(item["old"]["normalized_text"][:100]),
                "| new=",
                repr(item["new"]["normalized_text"][:100]),
            )


if __name__ == "__main__":
    main()

