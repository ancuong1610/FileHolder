"""Evaluation suite: identity test + synthetic mutation scoring.

Usage:
    python -m crr_compare eval [--seed 42] [--skip-identity]

Two cases run in isolated workspace roots (workspace/eval/<case>/):
  identity - old extraction vs itself: must produce ZERO changes; any
             detected change is a false positive. Duplicate-assignment and
             resegmentation review entries are reported but not failures.
  mutation - old extraction vs a synthetically mutated copy with known
             ground truth: scores precision/recall/F1 per change category.
             Each category also reports support (tp/fp/fn/truth_count/
             prediction_count/status) and a coverage block. Undefined values
             are null (precision with no predictions, recall with no truth,
             F1 when either is undefined); a category with no injected truth
             is ``not_applicable`` rather than a vacuous perfect score, and an
             empty-truth category with false positives is a tested failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from crr_compare import cli
from crr_compare.mutations import generate_mutations


@contextmanager
def _mock_env():
    """Force offline mock evaluation for the duration of the wrapped block."""
    prev = {k: os.environ.get(k) for k in ("CRR_RUN_LLM", "CRR_DOMAIN_MODE")}
    os.environ["CRR_RUN_LLM"] = "mock"
    os.environ["CRR_DOMAIN_MODE"] = "generic"
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _prepare_case_root(case_root: Path, both_from_old: bool) -> None:
    fixture_dir = Path(__file__).resolve().parents[1] / "test_data" / "fixtures_general" / "crr3"
    if case_root.exists():
        shutil.rmtree(case_root)
    out = case_root / "output"
    out.mkdir(parents=True)
    shutil.copy(fixture_dir / "old_di_raw.json", out / "old_di_raw.json")
    new_source = fixture_dir / "old_di_raw.json" if both_from_old else fixture_dir / "new_di_raw.json"
    shutil.copy(new_source, out / "new_di_raw.json")


def _run_stages(start: str, end: str, root: Path) -> None:
    class Args:
        pass

    args = Args()
    args.from_stage = start
    args.to_stage = end
    cli.run_stages(cli.parse_stage_range(args), root)


def _load_canonical(root: Path) -> dict:
    return json.loads((root / "output" / "comparison_result.json").read_text(encoding="utf-8"))


def _review_composition(canonical: dict) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(item.get("source_kind") for item in canonical.get("review_queue") or []))


def run_identity(workspace_root: Path) -> dict:
    case_root = workspace_root / "eval" / "identity"
    print("\n=== IDENTITY CASE (old vs old): expecting zero changes ===")
    with _mock_env():
        _prepare_case_root(case_root, both_from_old=True)
        started = time.perf_counter()
        _run_stages("02", "15", case_root)
        elapsed = round(time.perf_counter() - started, 1)

        canonical = _load_canonical(case_root)
        stats = canonical.get("statistics") or {}
        changes = stats.get("changes", -1)
        table_changes = stats.get("table_changes", -1)
        passed = changes == 0 and table_changes == 0
        result = {
            "case": "identity",
            "passed": passed,
            "elapsed_seconds": elapsed,
            "element_changes": changes,
            "table_changes": table_changes,
            "review_queue_composition": _review_composition(canonical),
        }
        print(json.dumps(result, indent=2))
        if not passed:
            samples = [
                {"id": c["change_id"], "type": c["change_type"], "old": ((c.get("old") or {}).get("text") or "")[:90]}
                for c in canonical.get("changes", [])[:10]
            ]
            print("IDENTITY FAILURE — first false-positive changes:")
            print(json.dumps(samples, indent=2, ensure_ascii=False))
    return result


def run_mutation(workspace_root: Path, seed: int) -> dict:
    case_root = workspace_root / "eval" / "mutation"
    print(f"\n=== MUTATION CASE (old vs mutated old), seed={seed} ===")
    with _mock_env():
        _prepare_case_root(case_root, both_from_old=True)

        _run_stages("02", "03", case_root)

        old_model_path = case_root / "output" / "old_document.json"
        model = json.loads(old_model_path.read_text(encoding="utf-8"))
        mutated_model, truth = generate_mutations(model, seed=seed)
        (case_root / "output" / "new_document.json").write_text(
            json.dumps(mutated_model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (case_root / "output" / "eval_mutation_truth.json").write_text(
            json.dumps({"seed": seed, "truth": truth}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        counts = {}
        for entry in truth:
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
        print("Injected ground-truth mutations:", json.dumps(counts))

        started = time.perf_counter()
        _run_stages("04", "15", case_root)
        elapsed = round(time.perf_counter() - started, 1)

        canonical = _load_canonical(case_root)
        score = _score_mutation_case(canonical, truth)
        score.update({"case": "mutation", "seed": seed, "elapsed_seconds": elapsed})
        print(json.dumps(score["summary"], indent=2))
        coverage = score.get("coverage") or {}
        if coverage.get("uncovered"):
            print(
                "Uncovered conditions (no ground truth injected; not scored):",
                json.dumps(
                    {
                        name: coverage["categories"][name]
                        for name in coverage["uncovered"]
                    },
                    indent=2,
                ),
            )
        if score["false_positives"]:
            print("False positives (cosmetic noise) — first 8:")
            print(json.dumps(score["false_positives"][:8], indent=2, ensure_ascii=False))
    return score


_EVALUATED = "evaluated"
_NOT_APPLICABLE = "not_applicable"
_FALSE_POSITIVE = "false_positive"


def _metric_block(
    tp: int,
    fp: int | None,
    fn: int,
    *,
    truth_count: int | None = None,
    prediction_count: int | None = None,
    precision_measurable: bool | None = None,
) -> dict:
    """Metric + support block for one benchmark category.

    ``precision``, ``recall`` and ``f1`` are ``None`` when mathematically
    undefined: precision without predictions, recall without truth, and F1
    whenever either of its two inputs is undefined.  ``fp`` may be ``None``
    when the harness has no independent negative-prediction stream (for
    condition categories such as move/table), which makes precision undefined.
    """
    truth_count = tp + fn if truth_count is None else truth_count
    if precision_measurable is None:
        precision_measurable = fp is not None
    if prediction_count is None:
        prediction_count = tp + (fp or 0)

    precision = None
    if precision_measurable and prediction_count:
        precision = tp / prediction_count
    recall = tp / truth_count if truth_count else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    if truth_count == 0:
        status = _FALSE_POSITIVE if (fp or 0) > 0 else _NOT_APPLICABLE
    else:
        status = _EVALUATED

    return {
        "precision": None if precision is None else round(precision, 3),
        "recall": None if recall is None else round(recall, 3),
        "f1": None if f1 is None else round(f1, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "truth_count": truth_count,
        "prediction_count": prediction_count,
        "status": status,
    }


def _score_mutation_case(canonical: dict, truth: list[dict]) -> dict:
    changes = canonical.get("changes") or []
    modified_old, deleted_old, added_new = set(), set(), set()
    fp_candidates: list[dict] = []

    gt_modify = {e["element_id"] for e in truth if e["type"] == "modify"}
    gt_delete = {e["element_id"] for e in truth if e["type"] == "delete"}
    gt_insert = {e["element_id"] for e in truth if e["type"] == "insert"}
    gt_move_ids = {
        eid
        for e in truth
        if e["type"] == "move_block"
        for eid in e["element_ids"]
    }
    gt_cells = {e["cell_id"] for e in truth if e["type"] == "table_cell"}
    gt_noise = {
        e["element_id"] for e in truth if e["type"] == "noise_hyphenation"
    }

    reseg_old, reseg_new = set(), set()
    for item in canonical.get("review_queue") or []:
        if item.get("source_kind") != "resegmentation_pair":
            continue
        evidence = item.get("evidence") or {}
        reseg_old.update(evidence.get("old_element_ids") or [])
        reseg_new.update(evidence.get("new_element_ids") or [])

    detected_modified_pairs = []
    for change in changes:
        ctype = change.get("change_type")
        old_id = (change.get("old") or {}).get("element_id")
        new_id = (change.get("new") or {}).get("element_id")
        if ctype == "MODIFIED":
            modified_old.add(old_id)
            detected_modified_pairs.append(change)
        elif ctype == "DELETED":
            deleted_old.add(old_id)
        elif ctype == "ADDED":
            added_new.add(new_id)

    # --- recall per category ---
    # A ground-truth change consumed into a resegmentation review pair is
    # ABSENT from canonical changes: it counts as a miss, not a hit.
    modify_hits = gt_modify & modified_old
    modify_degraded = (gt_modify & deleted_old) | (gt_modify & reseg_old)
    delete_hits = gt_delete & deleted_old
    delete_consumed = gt_delete & reseg_old
    insert_hits = gt_insert & added_new
    insert_consumed = gt_insert & reseg_new
    moved_kept = gt_move_ids - deleted_old - added_new - reseg_old - reseg_new

    # Noise elements (hyphenation wrap) must never surface as changes.
    noise_leaks = [
        c
        for c in changes
        if (c.get("old") or {}).get("element_id") in gt_noise
        or (
            c.get("change_type") == "ADDED"
            and (c.get("new") or {}).get("element_id") in gt_noise
        )
    ]

    # Table condition: score per unique ground-truth cell. A non-UNCHANGED cell
    # change counts the injected cell(s) it surfaces; repeated records and a
    # shared old/new cell id collapse into one, unrelated cells get no credit.
    detected_cells: set[str] = set()
    for record in canonical.get("table_changes") or []:
        for cell in record.get("cell_changes") or []:
            if cell.get("classification") == "UNCHANGED":
                continue
            old_cell = (cell.get("old") or {}).get("cell_id")
            new_cell = (cell.get("new") or {}).get("cell_id")
            if old_cell in gt_cells:
                detected_cells.add(old_cell)
            if new_cell in gt_cells:
                detected_cells.add(new_cell)

    # Legacy any-hit bool kept for compatibility; the pass gate below requires
    # every supported cell to be detected, not merely one.
    cell_hit = bool(detected_cells)

    # --- precision: every detection must trace back to a GT mutation ---
    known_old = gt_modify | gt_delete | gt_move_ids
    known_new = gt_insert | gt_move_ids
    fp_modified = [c for c in detected_modified_pairs
                   if (c["old"].get("element_id") not in gt_modify)]
    fp_deleted = [c for c in changes
                  if c.get("change_type") == "DELETED"
                  and (c["old"].get("element_id") not in gt_delete)
                  and (c["old"].get("element_id") not in gt_modify)]
    fp_added = [c for c in changes
                if c.get("change_type") == "ADDED"
                and (c["new"].get("element_id") not in gt_insert)]

    modify_block = _metric_block(
        len(modify_hits), len(fp_modified), len(gt_modify) - len(modify_hits)
    )
    modify_block["degraded_to_delete_or_reseg"] = len(modify_degraded)

    delete_block = _metric_block(
        len(delete_hits), len(fp_deleted), len(gt_delete) - len(delete_hits)
    )
    delete_block["consumed_by_reseg_review"] = len(delete_consumed)

    insert_block = _metric_block(
        len(insert_hits), len(fp_added), len(gt_insert) - len(insert_hits)
    )
    insert_block["consumed_by_reseg_review"] = len(insert_consumed)

    # move/table are condition checks, not precision/recall detection streams:
    # there is no independent set of predicted moves/cells, so precision is
    # undefined (fp is None) while recall of the known condition is measured.
    move_block = _metric_block(
        len(moved_kept),
        None,
        len(gt_move_ids) - len(moved_kept),
        truth_count=len(gt_move_ids),
    )
    move_block["moved_elements"] = len(gt_move_ids)
    move_block["kept_paired_not_add_delete"] = len(moved_kept)

    cell_tp = len(detected_cells)
    table_block = _metric_block(
        cell_tp, None, len(gt_cells) - cell_tp, truth_count=len(gt_cells)
    )
    table_block["hit"] = cell_hit

    # Noise is a suppression condition: every injected wrap is a truth item
    # that must stay quiet, and any surfaced change is a false positive.
    injected_noise = len(gt_noise)
    leaked_noise = len(noise_leaks)
    noise_block = _metric_block(
        max(0, injected_noise - leaked_noise),
        leaked_noise,
        0,
        truth_count=injected_noise,
        prediction_count=injected_noise,
    )
    noise_block["injected"] = injected_noise
    noise_block["leaked_into_changes"] = leaked_noise

    summary = {
        "modify": modify_block,
        "delete": delete_block,
        "insert": insert_block,
        "move_block": move_block,
        "table_cell": table_block,
        "noise_hyphenation": noise_block,
        "totals": {
            "detected_element_changes": len(changes),
            "false_positives": len(fp_modified) + len(fp_deleted) + len(fp_added),
        },
    }

    category_blocks = {
        "modify": modify_block,
        "delete": delete_block,
        "insert": insert_block,
        "move_block": move_block,
        "table_cell": table_block,
        "noise_hyphenation": noise_block,
    }
    supported = [name for name, block in category_blocks.items() if block["truth_count"] > 0]
    uncovered = [name for name, block in category_blocks.items() if block["truth_count"] == 0]
    false_positive_categories = [
        name for name, block in category_blocks.items() if block["status"] == _FALSE_POSITIVE
    ]
    coverage = {
        "categories": {
            name: {
                "truth_count": block["truth_count"],
                "prediction_count": block["prediction_count"],
                "status": block["status"],
                "covered": block["truth_count"] > 0,
            }
            for name, block in category_blocks.items()
        },
        "supported": supported,
        "uncovered": uncovered,
        "false_positive": false_positive_categories,
        "fully_covered": not uncovered,
    }

    def brief(change: dict) -> dict:
        side = change.get("old") if change.get("change_type") != "ADDED" else change.get("new")
        return {
            "id": change.get("change_id"),
            "type": change.get("change_type"),
            "text": (side.get("text") or "")[:80],
        }

    false_positives = [brief(c) for c in (fp_modified + fp_deleted + fp_added)[:20]]

    def _supported_ok(block: dict, condition: bool) -> bool:
        """A coverage gap is named, never a silent pass and never a failure."""
        if block["truth_count"] == 0:
            return True
        return condition

    passed = (
        modify_block["degraded_to_delete_or_reseg"] == 0
        and (
            modify_block["truth_count"] == 0
            or (modify_block["recall"] is not None and modify_block["recall"] >= 0.75)
        )
        and _supported_ok(delete_block, len(delete_hits) >= len(gt_delete))
        and _supported_ok(insert_block, len(insert_hits) >= len(gt_insert))
        and _supported_ok(move_block, len(moved_kept) == len(gt_move_ids))
        and _supported_ok(table_block, cell_tp >= len(gt_cells))
        and _supported_ok(noise_block, leaked_noise == 0)
        and summary["totals"]["false_positives"] == 0
        and not false_positive_categories
    )
    return {
        "summary": summary,
        "passed": passed,
        "false_positives": false_positives,
        "coverage": coverage,
    }


def evaluate_command(root: Path, seed: int, skip_identity: bool) -> None:
    results = []
    if not skip_identity:
        results.append(run_identity(root))
    results.append(run_mutation(root, seed))

    report_path = root / "eval" / "eval_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved eval report: {report_path}")

    overall_passed = all(result.get("passed") for result in results)
    print(f"\nEVALUATION {'PASSED' if overall_passed else 'FAILED'}")
    for result in results:
        coverage = result.get("coverage")
        if not coverage:
            continue
        if coverage.get("uncovered"):
            print(
                "COVERAGE GAP — benchmark conditions with no ground truth "
                f"(excluded from the pass gate): {', '.join(coverage['uncovered'])}"
            )
        else:
            print("COVERAGE — every scored condition had injected ground truth.")
    if not overall_passed:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Matching-core fingerprint (design spec section 12.1)
#
# An exact SHA-256 over canonical JSON of the matching core: the content and
# pre-routing classification that must not change when the standard
# generalizes. Routing/resolution counts, authorization/domain statuses, and
# downstream metadata are deliberately excluded.
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object.")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _element_content(element: Any) -> dict[str, Any] | None:
    """Stable content of a candidate element; ids/geometry are excluded."""
    if not isinstance(element, dict):
        return None
    return {
        "element_type": element.get("element_type"),
        "comparison_text": element.get("comparison_text")
        or element.get("normalized_text")
        or element.get("text"),
    }


def _candidate_content(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_type": candidate.get("candidate_type"),
        "old": _element_content(candidate.get("old")),
        "new": _element_content(candidate.get("new")),
    }


def _is_change(candidate: dict[str, Any]) -> bool:
    return candidate.get("candidate_type") not in ("UNCHANGED", "LAYOUT_EQUIVALENT")


def _stable_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate[key]
        for key in (
            "similarity_evidence",
            "movement_evidence",
            "token_evidence",
            "matching_evidence",
        )
        if isinstance(candidate.get(key), dict)
    }


def _classification_record(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_fingerprint": _sha256(_candidate_content(candidate)),
        "classification": candidate.get("candidate_type"),
        "needs_review": bool(candidate.get("needs_review")),
        "review_reasons": sorted(set(candidate.get("review_reasons") or [])),
        "evidence": _stable_evidence(candidate),
    }


def _candidate_records(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    groups = (
        candidates.get("unchanged") or [],
        candidates.get("modified") or [],
        candidates.get("modified_review") or [],
        candidates.get("duplicate_exact_review") or [],
        candidates.get("relocated") or [],
        candidates.get("resegmentation_review") or [],
        candidates.get("deleted") or [],
        candidates.get("added") or [],
    )
    return [candidate for group in groups for candidate in group]


def _stable_logical_table(table: Any) -> dict[str, Any] | None:
    if not isinstance(table, dict):
        return None
    return {
        "source_fragment_count": table.get("source_fragment_count"),
        "source_pages": table.get("source_pages") or [],
        "first_reading_order": table.get("first_reading_order"),
        "provisional_context_keys": table.get("provisional_context_keys") or [],
        "nearest_provisional_context_key": table.get("nearest_provisional_context_key"),
        "row_count": table.get("row_count"),
        "column_count": table.get("column_count"),
        "header_signature": table.get("header_signature") or [],
        "omitted_repeated_header_rows": table.get("omitted_repeated_header_rows"),
        "omitted_repeated_header_cells": table.get("omitted_repeated_header_cells"),
        "requires_extraction_review": bool(table.get("requires_extraction_review")),
        "extraction_warning_flags": sorted(table.get("extraction_warning_flags") or []),
        "extraction_review_flags": sorted(table.get("extraction_review_flags") or []),
    }


def _stable_cell(cell: Any) -> dict[str, Any] | None:
    if not isinstance(cell, dict):
        return None
    return {
        "kind": cell.get("kind"),
        "text": cell.get("text"),
        "normalized_text": cell.get("normalized_text"),
        "row_span": cell.get("row_span"),
        "column_span": cell.get("column_span"),
        "row_index": cell.get("row_index"),
        "column_index": cell.get("column_index"),
    }


def _stable_cell_change(change: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": change.get("classification"),
        "old": _stable_cell(change.get("old")),
        "new": _stable_cell(change.get("new")),
        "structure_changed": bool(change.get("structure_changed")),
        "needs_review": bool(change.get("needs_review")),
        "review_reasons": sorted(set(change.get("review_reasons") or [])),
        "old_structure": change.get("old_structure"),
        "new_structure": change.get("new_structure"),
        "old_row_index": change.get("old_row_index"),
        "new_row_index": change.get("new_row_index"),
        "column_index": change.get("column_index"),
        "row_match_type": change.get("row_match_type"),
        "row_similarity": change.get("row_similarity"),
        "token_diff": change.get("token_diff"),
    }


def _stable_table_match(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": match.get("classification"),
        "match_method": match.get("match_method"),
        "matching_evidence": match.get("matching_evidence"),
        "needs_review": bool(match.get("needs_review")),
        "review_reasons": sorted(set(match.get("review_reasons") or [])),
        "statistics": match.get("statistics"),
        "old": _stable_logical_table(match.get("old")),
        "new": _stable_logical_table(match.get("new")),
        "cell_changes": [
            _stable_cell_change(change)
            for change in match.get("cell_changes") or []
        ],
    }


def _table_evidence(tables: dict[str, Any]) -> dict[str, Any]:
    return {
        "table_matches": [
            _stable_table_match(match)
            for match in tables.get("table_matches") or []
        ],
        "unresolved_table_groups": [
            {
                "provisional_context_key": group.get("provisional_context_key"),
                "reason": group.get("reason"),
                "old_tables": [
                    _stable_logical_table(table)
                    for table in group.get("old_tables") or []
                ],
                "new_tables": [
                    _stable_logical_table(table)
                    for table in group.get("new_tables") or []
                ],
            }
            for group in tables.get("unresolved_table_groups") or []
        ],
        "deleted_table_candidates": [
            _stable_logical_table(table)
            for table in tables.get("deleted_table_candidates") or []
        ],
        "added_table_candidates": [
            _stable_logical_table(table)
            for table in tables.get("added_table_candidates") or []
        ],
    }


def _movement_evidence(candidates: dict[str, Any]) -> dict[str, Any]:
    return {
        "relocated": [
            {
                "movement_classification": record.get("movement_classification"),
                "needs_review": bool(record.get("needs_review")),
                "review_reasons": sorted(set(record.get("review_reasons") or [])),
                "movement_evidence": record.get("movement_evidence"),
                "old": _element_content(record.get("old")),
                "new": _element_content(record.get("new")),
            }
            for record in candidates.get("relocated") or []
        ],
        "resegmentation_review": [
            {
                "similarity": record.get("similarity"),
                "needs_review": bool(record.get("needs_review")),
                "review_reasons": sorted(set(record.get("review_reasons") or [])),
                "token_evidence": record.get("token_evidence"),
                "old": _element_content(record.get("old")),
                "new": _element_content(record.get("new")),
            }
            for record in candidates.get("resegmentation_review") or []
        ],
        "movement_review": [
            {
                "movement_classification": record.get("movement_classification"),
                "needs_review": bool(record.get("needs_review")),
                "review_reasons": sorted(set(record.get("review_reasons") or [])),
                "movement_evidence": record.get("movement_evidence"),
                "old_text_preview": record.get("old_text_preview") or [],
                "new_text_preview": record.get("new_text_preview") or [],
            }
            for record in candidates.get("movement_review") or []
        ],
        "structural_split_merge_review": [
            {
                "evidence_type": record.get("evidence_type"),
                "classification": record.get("classification"),
                "needs_review": bool(record.get("needs_review")),
                "review_reasons": sorted(set(record.get("review_reasons") or [])),
                "old_text": record.get("old_text"),
                "new_text": record.get("new_text"),
            }
            for record in candidates.get("structural_split_merge_review") or []
        ],
        "structural_boundary_review": [
            {
                "classification": record.get("classification"),
                "needs_review": bool(record.get("needs_review")),
                "review_reasons": sorted(set(record.get("review_reasons") or [])),
                "old_text": record.get("old_text"),
                "new_text": record.get("new_text"),
                "evidence": record.get("evidence"),
            }
            for record in candidates.get("structural_boundary_review") or []
        ],
    }


def _stable_document_identity(document: Any) -> dict[str, Any]:
    """Stable input identity: only labels/names/document IDs that exist."""
    if not isinstance(document, dict):
        return {}
    identity: dict[str, Any] = {}
    for key in ("document_id", "label", "name"):
        value = document.get(key)
        if isinstance(value, str):
            identity[key] = value
    return identity


def build_matching_core_payload(root: Path) -> dict[str, Any]:
    """Canonical matching-core payload from the s06-s10 artifact root.

    Reads the s08 candidate set (comparison_candidates.json, the candidate
    set and its content) and the s09 post-movement/pre-routing candidate set
    (comparison_candidates_with_movement.json) for movement evidence and
    stable post-movement classifications. The s10 table result supplies table
    evidence. Routing, resolution, authorization/domain statuses and
    downstream metadata are excluded.
    """
    output = root / "output"
    s08_candidates = _load_json(output / "comparison_candidates.json")
    s09_candidates = _load_json(output / "comparison_candidates_with_movement.json")
    tables = _load_json(output / "table_comparison_result.json")

    s08_records = _candidate_records(s08_candidates)
    candidate_fingerprints = sorted(
        _sha256(_candidate_content(candidate)) for candidate in s08_records
    )
    change_fingerprints = sorted(
        _sha256(
            {
                **_candidate_content(candidate),
                "classification": candidate.get("candidate_type"),
            }
        )
        for candidate in s08_records
        if _is_change(candidate)
    )

    s09_records = _candidate_records(s09_candidates)
    pre_routing_classifications = sorted(
        (_classification_record(candidate) for candidate in s09_records),
        key=lambda record: record["candidate_fingerprint"],
    )

    return {
        "documents": {
            "old_document": _stable_document_identity(s08_candidates.get("old_document")),
            "new_document": _stable_document_identity(s08_candidates.get("new_document")),
        },
        "candidate_fingerprints": candidate_fingerprints,
        "change_fingerprints": change_fingerprints,
        "pre_routing_classifications": pre_routing_classifications,
        "table_evidence": _table_evidence(tables),
        "movement_evidence": _movement_evidence(s09_candidates),
    }


def matching_core_fingerprint(root: Path) -> str:
    """Exact SHA-256 of the canonical matching-core payload."""
    return _sha256(build_matching_core_payload(root))


# ---------------------------------------------------------------------------
# Outcome migration report (design spec section 12.2)
#
# Non-exact evidence documenting how generic route/resolution counts and CRR
# domain-status counts moved between the baseline and the authoritative run.
# The exact matching-core fingerprint guarantees the candidate set and
# per-change content are identical, so only routing/resolution and
# verification statuses may differ.
# ---------------------------------------------------------------------------


def build_outcome_migration(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Map baseline authorization statuses to current domain statuses and
    account for every routing/resolution/route-count delta.

    Explanations are deterministic and describe the mechanism, not individual
    items: generic routing/resolution no longer gates on domain verification;
    generic review is separate from domain verification; the ``authorized``
    status is renamed ``verified`` while semantics carry over.  The report is
    evidence, never a pass/fail gate.
    """
    _as_int = lambda v: v if isinstance(v, (int, float)) else 0
    _as_dict = lambda v: v if isinstance(v, dict) else {}

    explanations = {
        "authorized": (
            "authorized renamed to verified while semantics carry over; "
            "generic resolution no longer gates on domain verification."
        ),
        "ambiguous": (
            "ambiguous status preserved under generic domain verification; "
            "generic review queue is separate from domain verification."
        ),
        "unsupported": (
            "unsupported status preserved under generic domain verification; "
            "generic review queue is separate from domain verification."
        ),
        "not_applicable": (
            "not_applicable status preserved under generic domain verification; "
            "generic resolution is independent of domain verification."
        ),
        "resolution_status_counts": (
            "resolution status count changed because generic routing/resolution "
            "no longer gates on domain verification and deterministic rules "
            "resolve proven non-semantic cases."
        ),
        "review_queue": (
            "review queue length changed because generic review is separate "
            "from domain verification and deterministic rules resolve proven "
            "non-semantic cases."
        ),
        "route_counts": (
            "route count changed because generic routing is independent of "
            "domain verification; counts reflect the routing distribution over "
            "the unchanged candidate set."
        ),
    }

    legacy = baseline.get("authorization_status_counts") or {}
    modern = current.get("domain_status_counts") or {}

    mapped = {"authorized": "verified"}
    carried = {status: status for status in ("ambiguous", "unsupported", "not_applicable")}

    verification_status_deltas: dict[str, Any] = {}
    for legacy_status, new_status in {**mapped, **carried}.items():
        old_count = legacy.get(legacy_status, 0)
        new_count = modern.get(new_status, 0)
        verification_status_deltas[legacy_status] = {
            "mapped_to": new_status,
            "baseline_count": old_count,
            "current_count": new_count,
            "delta": new_count - old_count,
            "explanation": explanations[legacy_status],
        }

    routing_resolution_deltas: dict[str, Any] = {}
    for key in ("resolution_status_counts", "review_queue"):
        old_value = _as_dict(baseline.get(key)) if key == "resolution_status_counts" else _as_int(baseline.get(key))
        new_value = _as_dict(current.get(key)) if key == "resolution_status_counts" else _as_int(current.get(key))
        if key == "resolution_status_counts":
            for status in sorted(set(old_value) | set(new_value)):
                routing_resolution_deltas[f"{key}.{status}"] = {
                    "baseline_count": old_value.get(status, 0),
                    "current_count": new_value.get(status, 0),
                    "delta": new_value.get(status, 0) - old_value.get(status, 0),
                    "explanation": explanations[key],
                }
        else:
            routing_resolution_deltas[key] = {
                "baseline_count": old_value,
                "current_count": new_value,
                "delta": new_value - old_value,
                "explanation": explanations[key],
            }

    route_counts: dict[str, Any] = {}
    old_routes = _as_dict(baseline.get("route_counts"))
    new_routes = _as_dict(current.get("route_counts"))
    for route in sorted(set(old_routes) | set(new_routes)):
        route_counts[route] = {
            "baseline_count": old_routes.get(route, 0),
            "current_count": new_routes.get(route, 0),
            "delta": new_routes.get(route, 0) - old_routes.get(route, 0),
            "explanation": explanations["route_counts"],
        }

    return {
        "mapped": mapped,
        "carried_over": carried,
        "verification_status_deltas": verification_status_deltas,
        "routing_resolution_deltas": routing_resolution_deltas,
        "route_counts": route_counts,
    }
