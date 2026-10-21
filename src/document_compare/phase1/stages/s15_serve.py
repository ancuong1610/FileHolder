"""Stage 15 - Phase-2 serving index + manifest (LLM-ready).

Replaces notebook 15. Consumes the canonical comparison_result.json and the
Step-5 structured documents and writes:
- output/phase2_change_index.json  (rich units for downstream LLM/chat use)
- output/bu_comparison_review_manifest.json

The human-facing inline redline is produced separately by phase1/reporting/html.py.

Imports are side-effect free: no file reads and no pipeline execution outside
``main()``/``build_phase2_index()``.
"""
import json
from collections import Counter

from document_compare.shared.paths import find_project_root, output_dir
from document_compare.phase1.reporting.inline_pdf import build_audit as build_inline_audit
from document_compare.phase1.reporting.inline_pdf import render_pdf as render_inline_pdf
from document_compare.phase1.reporting.html import anchor_label_map, render as render_redline
from document_compare.phase1.reporting.redline_pdf import render_pdf


def build_bucket_index(model: dict) -> dict[str, str]:
    """element_id -> current structural bucket label (reading order walk).

    Bucket identity comes from each element's Step-5
    ``provisional_anchor_context``: the nearest provisional anchor's text is
    the bucket label. No legal/language prefix lists or regexes.
    """
    labels = anchor_label_map(model)
    buckets: dict[str, str] = {}
    current = "Front matter"
    elements = sorted(
        (model.get("document") or {}).get("elements") or [],
        key=lambda e: e.get("reading_order") if isinstance(e.get("reading_order"), int) else 10**12,
    )
    for element in elements:
        context = element.get("provisional_anchor_context") or {}
        nearest_id = context.get("nearest_anchor_id")
        if nearest_id and str(nearest_id) in labels:
            current = labels[str(nearest_id)]
        buckets[element.get("element_id")] = current
    return buckets


def bucket_for(change: dict, old_buckets: dict, new_buckets: dict) -> str:
    """Bucket label for a change/table record via its element ids.

    Element changes carry ``element_id``; table records carry
    ``source_table_ids`` (the physical table element ids). Domain
    verification never influences the bucket.
    """
    old = change.get("old") or {}
    new = change.get("new") or {}
    candidates = [old.get("element_id"), new.get("element_id")]
    candidates += (old.get("source_table_ids") or [])
    candidates += (new.get("source_table_ids") or [])
    for elem_id in candidates:
        if not elem_id:
            continue
        bucket = old_buckets.get(elem_id) or new_buckets.get(elem_id)
        if bucket:
            return bucket
    return "Unassigned"


def compact_diff_ops(token_diff: dict | None, field: str = "comparison_token_diff") -> list[dict]:
    ops = ((token_diff or {}).get(field) or {}).get("changes") or []
    return [
        {
            "operation": op.get("operation"),
            "old": " ".join(op.get("old_tokens") or []),
            "new": " ".join(op.get("new_tokens") or []),
        }
        for op in ops
    ]


def build_phase2_index(comparison: dict, old_doc: dict, new_doc: dict) -> dict:
    """Pure Phase-2 index over the canonical result; no filesystem access."""
    old_buckets = build_bucket_index(old_doc)
    new_buckets = build_bucket_index(new_doc)

    units = []

    # Semantic corpus for downstream analysis: ACCEPTED changes only. Review
    # items are uncertain, not meaningful; artifacts never reach canonical
    # `changes` (suppressed upstream in Step 14).
    for change in comparison.get("changes") or []:
        resolution = change.get("resolution") or {}
        if resolution.get("status") != "accepted":
            continue
        old = change.get("old") or {}
        new = change.get("new") or {}
        unit = {
            "unit_id": change["change_id"],
            "kind": "element",
            "bucket": bucket_for(change, old_buckets, new_buckets),
            "change_type": change.get("change_type"),
            "meaningful_status": resolution.get("status"),
            "similarity": change.get("similarity"),
            "old": {
                "element_id": old.get("element_id"),
                "page": old.get("primary_page"),
                "text": old.get("text"),
            },
            "new": {
                "element_id": new.get("element_id"),
                "page": new.get("primary_page"),
                "text": new.get("text"),
            },
            "token_operations": compact_diff_ops(change.get("token_diff")),
        }
        units.append(unit)

    for record in comparison.get("table_changes") or []:
        resolution = record.get("resolution") or {}
        if resolution.get("status") != "accepted":
            continue
        old_t = record.get("old") or {}
        new_t = record.get("new") or {}
        changed_cells = [
            {
                "cell_ref": f"r{c.get('old_row_index')}/n{c.get('new_row_index')}c{c.get('column_index')}",
                "classification": c.get("classification"),
                "old_text": (c.get("old") or {}).get("text"),
                "new_text": (c.get("new") or {}).get("text"),
            }
            for c in (record.get("cell_changes") or [])
            if (c.get("classification") or "UNCHANGED") != "UNCHANGED"
        ]
        unit = {
            "unit_id": record["table_change_id"],
            "kind": "table",
            "bucket": bucket_for(record, old_buckets, new_buckets),
            "change_type": record.get("change_type"),
            "classification": record.get("classification"),
            "meaningful_status": resolution.get("status"),
            "old": {
                "table_ids": old_t.get("source_table_ids") or [],
                "pages": old_t.get("source_pages") or [],
                "rows": old_t.get("row_count"),
                "columns": old_t.get("column_count"),
            },
            "new": {
                "table_ids": new_t.get("source_table_ids") or [],
                "pages": new_t.get("source_pages") or [],
                "rows": new_t.get("row_count"),
                "columns": new_t.get("column_count"),
            },
            "changed_cells": changed_cells[:200],
        }
        units.append(unit)

    return {
        "schema_version": "1.2.0",
        "serving_stage": "phase2_change_index",
        "source_comparison_schema": comparison.get("schema_version"),
        "usage": {
            "purpose": (
                "Grounding corpus for Phase-2 LLM chat over document changes. "
                "Contains ONLY accepted semantic changes; uncertain items live in "
                "the review queue (review_queue_export.csv), proven artifacts are "
                "suppressed upstream."
            ),
            "meaningful_status": {
                "accepted": "deterministically accepted substantive change",
            },
        },
        "statistics": {
            "units": len(units),
            "by_change_type": dict(Counter(u["change_type"] for u in units)),
            "buckets": len({u["bucket"] for u in units}),
            "suppressed_non_semantic_events": len(comparison.get("non_semantic_events") or []),
            "open_review_cases": len(comparison.get("review_queue") or []),
            "review_queue_by_case_type": dict(
                Counter(item.get("case_type") for item in comparison.get("review_queue") or [])
            ),
        },
        "change_units": units,
    }


def main() -> None:
    root = find_project_root()
    out = output_dir(root)
    final_path = out / "comparison_result.json"
    old_path = out / "old_document_structured.json"
    new_path = out / "new_document_structured.json"
    index_path = out / "phase2_change_index.json"
    manifest_path = out / "bu_comparison_review_manifest.json"

    comparison = json.loads(final_path.read_text(encoding="utf-8"))
    old_doc = json.loads(old_path.read_text(encoding="utf-8"))
    new_doc = json.loads(new_path.read_text(encoding="utf-8"))

    if comparison.get("comparison_stage") != "phase_1_final_comparison":
        raise SystemExit("Stage 15 requires canonical Step 14 comparison_result.json.")

    phase2 = build_phase2_index(comparison, old_doc, new_doc)

    index_path.write_text(json.dumps(phase2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {index_path}")
    print(json.dumps(phase2["statistics"], ensure_ascii=False, indent=2))

    pdf_path = render_pdf(
        comparison,
        out / "comparison_redline.pdf",
        old_document=old_doc,
        new_document=new_doc,
    )
    print(f"Saved: {pdf_path}")

    # Additive inline one-flow PDF (report-only) plus its omission/join audit.
    # This never changes the redline path above or the canonical comparison.
    inline_pdf_path = render_inline_pdf(comparison, out / "comparison_inline.pdf")
    print(f"Saved: {inline_pdf_path}")
    inline_audit_path = out / "inline_pdf_audit.json"
    inline_audit_path.write_text(
        json.dumps(build_inline_audit(comparison), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved: {inline_audit_path}")

    manifest = {
        "schema_version": "1.0.0",
        "source_comparison": final_path.name,
        "phase2_index": index_path.name,
        "redline_html": "bu_comparison_redline.html",
        "redline_pdf": "comparison_redline.pdf",
        "inline_pdf": "comparison_inline.pdf",
        "inline_pdf_audit": "inline_pdf_audit.json",
        "units": len(phase2["change_units"]),
        "changes": len(comparison.get("changes") or []),
        "table_changes": len(comparison.get("table_changes") or []),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {manifest_path}")

    # BU triage export: the full review queue as CSV (opens directly in Excel).
    import csv

    csv_path = out / "review_queue_export.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([
            "review_id", "source_kind", "case_type", "route", "status",
            "needs_review", "candidate_id", "similarity", "reasons",
            "old_element_ids", "new_element_ids",
        ])
        for item in comparison.get("review_queue") or []:
            resolution = item.get("resolution") or {}
            evidence = item.get("evidence") or {}
            writer.writerow([
                item.get("review_id"),
                item.get("source_kind"),
                item.get("case_type"),
                resolution.get("route"),
                resolution.get("status"),
                "yes" if resolution.get("needs_review") else "no",
                item.get("source_candidate_id"),
                evidence.get("similarity"),
                "; ".join(
                    (item.get("resolution") or {}).get("review_reasons")
                    or evidence.get("review_reasons")
                    or []
                ),
                "; ".join(evidence.get("old_element_ids") or []),
                "; ".join(evidence.get("new_element_ids") or []),
            ])
    print(f"Saved: {csv_path}")

    render_redline(root)
    print("Stage 15 complete.")


if __name__ == "__main__":
    main()