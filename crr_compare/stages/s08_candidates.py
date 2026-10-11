"""Stage 08: assemble comparison candidates from Step 6/7 matches.

Input  : ``<root>/output/exact_element_matches.json`` (Step 6) and
         ``<root>/output/modified_element_matches.json`` (Step 7), the
         current provisional-context match results.

Work   : Assembles UNCHANGED, MODIFIED_CANDIDATE, MODIFIED_REVIEW,
         DUPLICATE_EXACT_REVIEW, DELETED_CANDIDATE and ADDED_CANDIDATE
         records with multi-representation token diffs (source,
         normalized, comparison), then validates OLD/NEW conservation and
         the token-equivalence / duplicate-exact review invariants.

Output : ``<root>/output/comparison_candidates.json`` plus console
         summaries.

Review : No review gate here; ``needs_review`` candidates are carried
         forward to Step 9 for movement/structural reconciliation and are
         routed to human review by later stages.

The module is import-safe: importing it only defines the pure candidate
assembly helpers and never resolves a workspace, reads or writes pipeline
files, prints, or mutates the environment. File orchestration lives in
:func:`main`, which the ``__name__ == "__main__"`` guard runs for the CLI
stage runner.
"""
from crr_compare.common import find_project_root, get_page_number, set_page_number
from crr_compare.validation import require

# ---- imports and configuration constants ----

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

EXACT_MATCHES_FILENAME = "exact_element_matches.json"
MODIFIED_MATCHES_FILENAME = "modified_element_matches.json"
COMPARISON_CANDIDATES_FILENAME = "comparison_candidates.json"

TOKEN_PATTERN = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž_][A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9_]*"
    r"(?:[./:-][A-Za-zÀ-ÖØ-öø-ÿĀ-ž0-9_]+)*"
    r"|\d+(?:[.,/]\d+)*(?:[-/]\d+)*"
    r"|==|!=|<=|>=|->|=>|:=|\+\+|--"
    r"|\w+"
    r"|[^\w\s]",
    re.UNICODE,
)

# ---- I/O helpers ----
def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)


# ---- tokenization and multi-representation token diffs ----
def token_records(text: str) -> list[dict[str, Any]]:
    return [
        {"text": match.group(0), "char_start": match.start(), "char_end": match.end()}
        for match in TOKEN_PATTERN.finditer(text or "")
    ]


def token_text(records: list[dict[str, Any]], start: int, end: int) -> list[str]:
    return [record["text"] for record in records[start:end]]


def char_boundary(records: list[dict[str, Any]], token_index: int, text_length: int) -> int:
    if token_index < len(records):
        return records[token_index]["char_start"]
    return text_length


def exact_segment(
    text: str,
    records: list[dict[str, Any]],
    start: int,
    end: int,
) -> tuple[int, int, str]:
    if start == end:
        boundary = char_boundary(records, start, len(text))
        return boundary, boundary, ""
    char_start = records[start]["char_start"]
    char_end = records[end - 1]["char_end"]
    return char_start, char_end, text[char_start:char_end]


def build_token_diff(old_text: str, new_text: str, *, context_tokens: int = 6) -> dict[str, Any]:
    old_records = token_records(old_text)
    new_records = token_records(new_text)
    old_tokens = token_text(old_records, 0, len(old_records))
    new_tokens = token_text(new_records, 0, len(new_records))

    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    changes: list[dict[str, Any]] = []
    changed_old_count = 0
    changed_new_count = 0

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_char_start, old_char_end, old_segment = exact_segment(
            old_text, old_records, old_start, old_end
        )
        new_char_start, new_char_end, new_segment = exact_segment(
            new_text, new_records, new_start, new_end
        )
        changed_old_count += old_end - old_start
        changed_new_count += new_end - new_start
        changes.append(
            {
                "operation": tag,
                "old_token_start": old_start,
                "old_token_end": old_end,
                "new_token_start": new_start,
                "new_token_end": new_end,
                "old_char_start": old_char_start,
                "old_char_end": old_char_end,
                "new_char_start": new_char_start,
                "new_char_end": new_char_end,
                "old_tokens": old_tokens[old_start:old_end],
                "new_tokens": new_tokens[new_start:new_end],
                "old_text": old_segment,
                "new_text": new_segment,
                "old_context_before": old_tokens[max(0, old_start - context_tokens):old_start],
                "old_context_after": old_tokens[old_end:old_end + context_tokens],
                "new_context_before": new_tokens[max(0, new_start - context_tokens):new_start],
                "new_context_after": new_tokens[new_end:new_end + context_tokens],
            }
        )

    return {
        "old_token_count": len(old_tokens),
        "new_token_count": len(new_tokens),
        "changed_old_token_count": changed_old_count,
        "changed_new_token_count": changed_new_count,
        "change_operation_count": len(changes),
        "token_sequence_ratio": matcher.ratio(),
        "changes": changes,
    }



def representation_text(element: dict[str, Any], field: str) -> str:
    value = element.get(field)
    return value if isinstance(value, str) else ""


def build_representation_diffs(
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    source_old = representation_text(old, "text")
    source_new = representation_text(new, "text")
    normalized_old = representation_text(old, "normalized_text")
    normalized_new = representation_text(new, "normalized_text")
    comparison_old = representation_text(old, "comparison_text") or normalized_old
    comparison_new = representation_text(new, "comparison_text") or normalized_new

    return {
        "source_token_diff": build_token_diff(source_old, source_new),
        "normalized_token_diff": build_token_diff(normalized_old, normalized_new),
        "comparison_token_diff": build_token_diff(comparison_old, comparison_new),
    }

# ---- candidate builders ----
def matching_similarity(match: dict[str, Any]) -> float | None:
    evidence = match.get("similarity_evidence") or {}
    value = evidence.get("score")
    if isinstance(value, (int, float)):
        return float(value)
    value = match.get("similarity")
    return float(value) if isinstance(value, (int, float)) else None


def inherited_review(match: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = list(match.get("review_reasons") or [])
    return bool(match.get("needs_review") or reasons), sorted(set(reasons))


def paired_candidate(
    candidate_id: str,
    candidate_type: str,
    match: dict[str, Any],
    *,
    include_diff: bool,
    force_review: bool = False,
) -> dict[str, Any]:
    old = match.get("old") or {}
    new = match.get("new") or {}
    needs_review, review_reasons = inherited_review(match)

    token_diff = build_representation_diffs(old, new) if include_diff else None

    comparison_token_equivalent = (
        bool(token_diff)
        and token_diff["comparison_token_diff"]["change_operation_count"] == 0
    )

    if comparison_token_equivalent:
        needs_review = True
        review_reasons = sorted(set(
            review_reasons
            + ["comparison_token_equivalent_despite_text_difference"]
        ))

    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "source_match_id": match.get("match_id"),
        "match_type": match.get("match_type"),
        "match_method": match.get("match_method"),
        "similarity": matching_similarity(match),
        "similarity_evidence": match.get("similarity_evidence"),
        "candidate_retrieval_method": match.get("candidate_retrieval_method"),
        "old": old,
        "new": new,
        "needs_review": True if force_review else needs_review,
        "review_reasons": review_reasons,
        "context_is_authoritative_hierarchy": False,
        "movement_classification": None,
        "token_diff": token_diff,
        "comparison_token_equivalent": comparison_token_equivalent,
    }


def single_sided_candidate(
    candidate_id: str,
    candidate_type: str,
    element: dict[str, Any],
    side: str,
) -> dict[str, Any]:
    reasons = []
    extraction = element.get("extraction_validation")
    if isinstance(extraction, dict) and isinstance(
        extraction.get("requires_extraction_review"), bool
    ):
        if extraction["requires_extraction_review"]:
            reasons.append("source_extraction_requires_review")
    else:
        # Absent/malformed metadata means unknown quality, never safe.
        reasons.append("source_extraction_validation_unknown")

    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "source_unmatched_reason":
            element.get("modified_unmatched_reason")
            or element.get("exact_unmatched_reason"),
        "old": element if side == "old" else None,
        "new": element if side == "new" else None,
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "movement_classification": None,
        "token_diff": None,
    }

# ---- candidate assembly ----
def build_comparison_candidates(
    exact_result: dict[str, Any],
    modified_result: dict[str, Any],
) -> dict[str, Any]:
    unchanged = [
        paired_candidate(f"UNCHANGED-{i:06d}", "UNCHANGED", m, include_diff=False)
        for i, m in enumerate(exact_result.get("matches") or [], 1)
    ]
    modified = [
        paired_candidate(f"MODIFIED-CANDIDATE-{i:06d}", "MODIFIED_CANDIDATE", m, include_diff=True)
        for i, m in enumerate(modified_result.get("modified_matches") or [], 1)
    ]
    modified_review = [
        paired_candidate(f"MODIFIED-REVIEW-{i:06d}", "MODIFIED_REVIEW", m, include_diff=True, force_review=True)
        for i, m in enumerate(modified_result.get("review_pairs") or [], 1)
    ]
    duplicate_exact_review = [
        paired_candidate(f"DUPLICATE-EXACT-REVIEW-{i:06d}", "DUPLICATE_EXACT_REVIEW", m, include_diff=False, force_review=True)
        for i, m in enumerate(modified_result.get("duplicate_exact_review_pairs") or [], 1)
    ]
    deleted = [
        single_sided_candidate(f"DELETED-CANDIDATE-{i:06d}", "DELETED_CANDIDATE", e, "old")
        for i, e in enumerate(modified_result.get("unmatched_old") or [], 1)
    ]
    added = [
        single_sided_candidate(f"ADDED-CANDIDATE-{i:06d}", "ADDED_CANDIDATE", e, "new")
        for i, e in enumerate(modified_result.get("unmatched_new") or [], 1)
    ]

    all_records = unchanged + modified + modified_review + duplicate_exact_review + deleted + added

    return {
        "schema_version": "0.2.0",
        "comparison_stage": "comparison_candidate_assembly_and_multi_representation_token_diff",
        "source_files": {
            "exact_matches": EXACT_MATCHES_FILENAME,
            "modified_matches": MODIFIED_MATCHES_FILENAME,
        },
        "old_document": modified_result.get("old_document"),
        "new_document": modified_result.get("new_document"),
        "processing": {
            "version": "2.0.0-current-step7-contract",
            "token_diff_library": "stdlib.difflib.SequenceMatcher",
            "source_normalized_comparison_diffs_created": True,
            "new_normalization_policy_introduced": False,
            "tables_included": False,
            "movement_classification_performed": False,
            "final_add_delete_classification_performed": False,
            "final_change_classification_performed": False,
        },
        "statistics": {
            "unchanged_candidates": len(unchanged),
            "modified_candidates": len(modified),
            "modified_review_candidates": len(modified_review),
            "duplicate_exact_review_candidates": len(duplicate_exact_review),
            "deleted_candidates": len(deleted),
            "added_candidates": len(added),
            "total_candidate_records": len(all_records),
            "candidates_needing_review": sum(x["needs_review"] for x in all_records),
            "modified_comparison_diff_operations": sum(
                x["token_diff"]["comparison_token_diff"]["change_operation_count"]
                for x in modified
            ),
            "modified_with_empty_comparison_token_diff": sum(
                x["token_diff"]["comparison_token_diff"]["change_operation_count"] == 0
                for x in modified
            ),
            "modified_review_with_empty_comparison_token_diff": sum(
                x["token_diff"]["comparison_token_diff"]["change_operation_count"] == 0
                for x in modified_review
            ),
        },
        "unchanged": unchanged,
        "modified": modified,
        "modified_review": modified_review,
        "duplicate_exact_review": duplicate_exact_review,
        "deleted": deleted,
        "added": added,
    }

# ---- conservation helpers ----
def element_ids(records: list[dict[str, Any]], side: str) -> list[str]:
    return [
        record[side]["element_id"]
        for record in records
        if record.get(side) is not None
    ]

# ---- stage entry point ----
def main() -> None:
    """Run Stage 08 against the workspace and save the candidate result."""
    project_root = find_project_root()
    output_dir = project_root / "output"
    exact_path = output_dir / EXACT_MATCHES_FILENAME
    modified_path = output_dir / MODIFIED_MATCHES_FILENAME
    output_path = output_dir / COMPARISON_CANDIDATES_FILENAME

    for path in (exact_path, modified_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Run the preceding comparison step first.")

    print(f"Project root: {project_root}")
    print(f"Exact matches: {exact_path}")
    print(f"Modified matches: {modified_path}")

    exact_result = load_json(exact_path)
    modified_result = load_json(modified_path)

    if exact_result.get("comparison_stage") != "exact_element_matching_with_provisional_context":
        raise ValueError("Step 8 v2 requires the current Step-6 provisional-context result.")

    if modified_result.get("comparison_stage") != "modified_element_matching_with_provisional_context":
        raise ValueError("Step 8 v2 requires the current Step-7 provisional-context result.")

    for side_key in ("old_document", "new_document"):
        exact_doc = exact_result.get(side_key) or {}
        modified_doc = modified_result.get(side_key) or {}
        if exact_doc.get("document_id") != modified_doc.get("document_id"):
            raise ValueError(f"Step 6/7 document mismatch for {side_key}.")

    for key in (
        "modified_matches",
        "review_pairs",
        "duplicate_exact_review_pairs",
        "unmatched_old",
        "unmatched_new",
    ):
        if not isinstance(modified_result.get(key), list):
            raise ValueError(f"Missing Step-7 list field: {key}")

    print("Loaded compatible current Step 6 and Step 7 outputs.")

    comparison_result = build_comparison_candidates(exact_result, modified_result)
    print(json.dumps(comparison_result["statistics"], indent=2))

    paired = (
        comparison_result["modified"]
        + comparison_result["modified_review"]
        + comparison_result["duplicate_exact_review"]
    )

    old_ids = element_ids(
        comparison_result["unchanged"] + paired + comparison_result["deleted"], "old"
    )
    new_ids = element_ids(
        comparison_result["unchanged"] + paired + comparison_result["added"], "new"
    )

    expected_old = exact_result["statistics"]["old_comparable_elements"]
    expected_new = exact_result["statistics"]["new_comparable_elements"]

    if len(old_ids) != expected_old:
        raise ValueError(
            "s08: OLD conservation failed "
            f"(assembled={len(old_ids)}, expected={expected_old})"
        )
    if len(new_ids) != expected_new:
        raise ValueError(
            "s08: NEW conservation failed "
            f"(assembled={len(new_ids)}, expected={expected_new})"
        )
    if len(old_ids) != len(set(old_ids)):
        raise ValueError("s08: an OLD element was used more than once.")
    if len(new_ids) != len(set(new_ids)):
        raise ValueError("s08: a NEW element was used more than once.")

    step6_old = {x["element_id"] for x in exact_result.get("unmatched_old") or []}
    step6_new = {x["element_id"] for x in exact_result.get("unmatched_new") or []}

    missing_old = sorted(set(element_ids(paired + comparison_result["deleted"], "old")) ^ step6_old)
    if missing_old:
        raise ValueError(
            "s08: paired/deleted OLD ids do not match Step 6 residuals: "
            + ", ".join(missing_old[:10])
        )
    missing_new = sorted(set(element_ids(paired + comparison_result["added"], "new")) ^ step6_new)
    if missing_new:
        raise ValueError(
            "s08: paired/added NEW ids do not match Step 6 residuals: "
            + ", ".join(missing_new[:10])
        )

    for item in comparison_result["modified"] + comparison_result["modified_review"]:
        diff = item["token_diff"]["comparison_token_diff"]

        # Token-equivalent does NOT mean text-identical.
        # Examples include whitespace-only or tokenization-neutral differences.
        # These are preserved and forced to review instead of crashing Step 8.
        if diff["change_operation_count"] == 0:
            require(
                item["comparison_token_equivalent"] is True,
                "s08: empty comparison diff without comparison_token_equivalent",
            )
            require(
                item["needs_review"] is True,
                "s08: empty comparison diff must require review",
            )
            require(
                "comparison_token_equivalent_despite_text_difference"
                in item["review_reasons"],
                "s08: empty comparison diff missing review reason",
            )

    for item in comparison_result["duplicate_exact_review"]:
        old_text = item["old"].get("comparison_text") or item["old"].get("normalized_text") or ""
        new_text = item["new"].get("comparison_text") or item["new"].get("normalized_text") or ""
        require(
            old_text == new_text,
            "s08: duplicate-exact review pair text differs",
        )
        require(
            item["needs_review"] is True,
            "s08: duplicate-exact review pair must require review",
        )

    print(f"OLD conservation: {len(old_ids):,}/{expected_old:,}")
    print(f"NEW conservation: {len(new_ids):,}/{expected_new:,}")
    print("Candidate assembly invariants passed.")

    for candidate in comparison_result["modified"][:10]:
        diff = candidate["token_diff"]["comparison_token_diff"]
        print("=" * 90)
        print(
            candidate["candidate_id"],
            candidate["old"].get("element_type"),
            f"similarity={candidate.get('similarity')}",
            f"review={candidate['needs_review']}",
        )
        print(
            "ops:",
            "source=", candidate["token_diff"]["source_token_diff"]["change_operation_count"],
            "normalized=", candidate["token_diff"]["normalized_token_diff"]["change_operation_count"],
            "comparison=", diff["change_operation_count"],
        )
        for change in diff["changes"][:10]:
            print(f"  {change['operation'].upper()}: {change['old_text']!r} -> {change['new_text']!r}")

    print(f"Displayed {min(10, len(comparison_result['modified']))} modified candidates.")

    save_json(comparison_result, output_path)
    if not output_path.exists():
        raise ValueError(f"s08: expected saved candidates at {output_path}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()