"""Score pipeline output against a BU-provided ground-truth benchmark.

Usage:
    python -m crr_compare eval-gt [--gt PATH_TO_GroundTruth.json]

Cases outside the page coverage of the processed documents are reported as
out-of-scope (not misses). A case counts as detected when any element-level
change matches its article label or text; cases consumed by resegmentation /
duplicate reviews are counted separately (found-but-flagged).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from crr_compare.common import REPO_ROOT, find_project_root

ARTICLE_TOKEN_RE = re.compile(r"artikel\s+(\d+[a-z]*(?:\s+bis\s+\S+)?)", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[^\W_]{4,}", str(text or "").casefold())}


def _squash(text: str) -> str:
    """Whitespace/hyphenation-insensitive form for substring presence tests."""
    value = str(text or "").casefold()
    value = re.sub(r"-\s*\n\s*", "", value)
    return re.sub(r"\s+", "", value)


def load_changes(root: Path):
    out = root / "output"
    canonical = json.loads((out / "comparison_result.json").read_text(encoding="utf-8"))

    entries = []  # (kind, match_text, page, ids)
    for change in canonical.get("changes") or []:
        old = change.get("old") or {}
        new = change.get("new") or {}
        entries.append(
            {
                "id": change["change_id"],
                "type": change.get("change_type"),
                "old_id": old.get("element_id"),
                "new_id": new.get("element_id"),
                "old_text": old.get("text") or "",
                "new_text": new.get("text") or "",
                "new_page": new.get("primary_page"),
            }
        )

    flagged_old, flagged_new = set(), set()
    for item in canonical.get("review_queue") or []:
        evidence = item.get("evidence") or {}
        if item.get("source_kind") in {"resegmentation_pair", "duplicate_exact_assignment"}:
            flagged_old.update(evidence.get("old_element_ids") or [])
            flagged_new.update(evidence.get("new_element_ids") or [])

    raw_squashed = {}
    for side in ("old", "new"):
        path = out / f"{side}_di_raw.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw_squashed[side] = _squash(raw.get("content"))
    return entries, flagged_old, flagged_new, raw_squashed


def case_in_scope(case: dict, raw_squashed: dict) -> tuple[bool, str]:
    """In scope iff the change's distinctive text physically exists in the
    extracted side of the pair (BU page numbers reference the amending
    regulation, so they cannot delimit consolidated-document coverage)."""
    if not raw_squashed:
        return True, ""
    needle_new = _squash(case.get("new_text"))[:60]
    needle_old = _squash(case.get("old_text"))[:60]
    needle = needle_new if case.get("change_type") != "deleted" else needle_old
    if not needle:
        return True, ""
    side = "new" if case.get("change_type") != "deleted" else "old"
    content = raw_squashed.get(side, "")
    if needle in content:
        return True, ""
    # Fallback: article number somewhere + heading text somewhere.
    article_field = str(
        (case.get("new_location") or {}).get("article")
        or (case.get("old_location") or {}).get("article")
        or ""
    )
    number_needles = {f"artikel{m.group(1)}" for m in ARTICLE_TOKEN_RE.finditer(article_field)}
    heading_needle = _squash(re.sub(r"^\s*Artikel\s+\S+", "", str(case.get("new_text") or ""), count=1))[:40]
    if number_needles and all(n in content for n in number_needles) and heading_needle in content:
        return True, ""
    return False, f"article not present in extracted {side}-side pages"


def match_case(case: dict, entries: list[dict], flagged_old: set, flagged_new: set):
    """Return (status, matched_ids) with status in detected/flagged/missed."""
    article_field = str(
        (case.get("new_location") or {}).get("article")
        or (case.get("old_location") or {}).get("article")
        or ""
    )
    article_tokens = {
        f"artikel {m.group(1).lower().split(' ')[0]}"
        for m in ARTICLE_TOKEN_RE.finditer(article_field)
    }
    gt_new_tokens = _tokens(case.get("new_text"))
    gt_old_tokens = _tokens(case.get("old_text"))
    side = "new" if case.get("change_type") != "deleted" else "old"

    detected, flagged = [], []
    for entry in entries:
        text = entry["new_text"] if side == "new" else entry["old_text"]
        text_tokens = _tokens(text)
        eid = entry["new_id"] if side == "new" else entry["old_id"]

        hit = False
        lowered = str(text).casefold()
        for article_key in article_tokens:
            if article_key in lowered:
                hit = True
                break
        if not hit:
            gt_tokens = gt_new_tokens if side == "new" else gt_old_tokens
            if gt_tokens and text_tokens:
                # containment: how much of the GT text appears in this entry
                if len(gt_tokens & text_tokens) / max(1, len(gt_tokens)) >= 0.6:
                    # When GT names an article, require that number in the
                    # entry too — otherwise unrelated wording matches.
                    if article_tokens:
                        hit = any(k in _squash(text) for k in article_tokens)
                    else:
                        hit = True
        if not hit:
            continue

        if entry["type"] == "ADDED" and case["change_type"] == "added":
            pass
        elif entry["type"] == "DELETED" and case["change_type"] == "deleted":
            pass
        elif entry["type"] == "MODIFIED":
            pass
        else:
            continue

        if side == "new" and eid in flagged_new or side == "old" and eid in flagged_old:
            flagged.append(entry["id"])
        else:
            detected.append(entry["id"])

    if detected:
        return "detected", detected[:3]
    if flagged:
        return "flagged", flagged[:3]
    return "missed", []


def run(root: Path, gt_path: Path) -> int:
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    entries, flagged_old, flagged_new, raw_squashed = load_changes(root)

    rows = []
    for case in gt["cases"]:
        in_scope, reason = case_in_scope(case, raw_squashed)
        if not in_scope:
            rows.append(
                {
                    "case": case["case_id"],
                    "status": "out_of_scope",
                    "article": (case.get("new_location") or {}).get("article"),
                    "reason": reason,
                    "matched": [],
                }
            )
            continue
        status, matched = match_case(case, entries, flagged_old, flagged_new)
        rows.append(
            {
                "case": case["case_id"],
                "status": status,
                "article": (case.get("new_location") or {}).get("article"),
                "matched": matched,
            }
        )

    in_scope_rows = [r for r in rows if r["status"] != "out_of_scope"]
    detected = sum(1 for r in in_scope_rows if r["status"] == "detected")
    flagged_count = sum(1 for r in in_scope_rows if r["status"] == "flagged")
    missed = [r for r in in_scope_rows if r["status"] == "missed"]

    print(f"Benchmark: {gt['benchmark_metadata']['source']} v{gt['benchmark_metadata']['benchmark_version']}")
    print(f"Scoping by extracted source text: {len(rows) - len(in_scope_rows)} of "
          f"{len(rows)} cases reference content outside the extracted page range")
    print()
    for row in rows:
        marker = {"detected": "OK ", "flagged": "FLAG", "missed": "MISS", "out_of_scope": "--"}[row["status"]]
        extra = f" ({row['reason']})" if row["status"] == "out_of_scope" and row.get("reason") else ""
        print(f"[{marker}] {row['case']} {(row.get('article') or ''):30} -> {', '.join(row['matched']) or ''}{extra}")

    summary = {
        "cases_total": len(rows),
        "in_scope": len(in_scope_rows),
        "detected": detected,
        "found_but_flagged_for_review": flagged_count,
        "missed": len(missed),
        "recall_clean": round(detected / max(1, len(in_scope_rows)), 3),
        "recall_including_flagged": round((detected + flagged_count) / max(1, len(in_scope_rows)), 3),
    }

    report_path = root / "output" / "bu_benchmark_report.json"
    report_path.write_text(
        json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\n" + json.dumps(summary, indent=2))
    print(f"Saved: {report_path}")

    if missed:
        print(f"\n{len(missed)} MISSED case(s):")
        for row in missed:
            case = next(c for c in gt["cases"] if c["case_id"] == row["case"])
            print(f"- {row['case']}: {(case.get('new_location') or {}).get('article')} — {case.get('change_summary', '')[:120]}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="crr_compare eval-gt")
    parser.add_argument("--gt", type=Path, default=REPO_ROOT / "test_data" / "GroundTruth.json")
    parser.add_argument("--root", type=Path, default=find_project_root())
    args = parser.parse_args()
    raise SystemExit(run(args.root.expanduser().resolve(), args.gt))


if __name__ == "__main__":
    main()
