"""Offline, saved-artifact evidence review and honest metrics (T-02/T-03).

``review-evidence`` reads an existing pipeline output directory (never a job
upload), reconstructs the additive structure evidence when a saved artifact
predates it, validates a versioned reference annotation input against the
artifacts, and writes a self-contained local review bundle. No network,
Document Intelligence call, evaluation package or model download is involved.

Metric integrity rules enforced here:

* predictions are never used as ground truth; scoring only consumes the
  explicitly declared reference coverage,
* a missing/invalid reference yields ``not_scored``/``not_applicable`` with
  null metrics and a reason, never a vacuous perfect score,
* partial reference coverage cannot establish a global precision,
* document fingerprints, duplicate IDs, unknown IDs and nonsensical coverage
  are rejected before scoring,
* extraction, structure and comparison results stay in separate layers.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from document_compare.phase1.structure import reconstruction as structure
from document_compare.phase1.evaluation.evidence_review_html import render_html
from document_compare.phase1.stages.s05_anchors import OUTPUT_SCHEMA_VERSION, STEP5_VERSION
from document_compare.phase1.contracts.validation import require

REVIEW_BUNDLE_VERSION = "1.0.0"
REFERENCE_VERSION = "1.1.0"

STRUCTURED = "old_document_structured.json"
NORMALIZED = "old_document_normalized.json"
COMPARISON = "comparison_result.json"

SIDE_FILES = {
    "old": {
        "structured": "old_document_structured.json",
        "normalized": "old_document_normalized.json",
        "raw": "old_di_raw.json",
        "validation": "old_extraction_validation.json",
    },
    "new": {
        "structured": "new_document_structured.json",
        "normalized": "new_document_normalized.json",
        "raw": "new_di_raw.json",
        "validation": "new_extraction_validation.json",
    },
}

OPTIONAL_FILES = (
    "comparison_result.json",
    "table_comparison_result.json",
    "review_routing.json",
    "phase2_change_index.json",
)

REFERENCE_TEMPLATE = {
    "reference_version": REFERENCE_VERSION,
    "kind": "UNVERIFIED_TEMPLATE",
    "verified": False,
    "synthetic": False,
    "notes": (
        "UNVERIFIED template. It is never used automatically for scoring and "
        "is rejected by --reference. Fill document fingerprints from "
        "provenance.json and declare only independently verified coverage "
        "before passing a real reference. Allowed kinds: synthetic_authored "
        "(synthetic=true, notes required) or source_verified "
        "(verified=true, synthetic=false, notes required)."
    ),
    "documents": {
        "old": {"document_id": "<old document id>", "artifact_sha256": "<sha256 of the identity artifact>"},
        "new": {"document_id": "<new document id>", "artifact_sha256": "<sha256 of the identity artifact>"},
    },
    "coverage": {
        "old": {
            "toc_links": {"covered_entry_ids": [], "expected_body_element_ids": {}},
            "membership": {"covered_element_ids": [], "expected_section_ids": {}},
        },
        "new": {
            "toc_links": {"covered_entry_ids": [], "expected_body_element_ids": {}},
            "membership": {"covered_element_ids": [], "expected_section_ids": {}},
        },
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def _document_of(model: dict[str, Any]) -> dict[str, Any]:
    document = model.get("document")
    if not isinstance(document, dict):
        raise ValueError("artifact model is missing document")
    return document


def _validate_loaded_model(document: dict[str, Any], model: dict[str, Any], label: str) -> None:
    """Reject structurally invalid saved artifacts before building dictionaries."""
    document_id = document.get("document_id")
    require(
        isinstance(document_id, str) and document_id,
        f"{label}: saved artifact is missing a document_id",
    )
    schema_version = model.get("schema_version")
    require(
        isinstance(schema_version, str) and schema_version,
        f"{label}: saved artifact is missing a schema_version",
    )
    elements = document.get("elements")
    require(isinstance(elements, list) and elements, f"{label}: saved artifact has no elements")
    element_ids = [str(element.get("element_id")) for element in elements]
    require(all(element_ids), f"{label}: saved artifact element is missing an element_id")
    require(
        len(element_ids) == len(set(element_ids)),
        f"{label}: saved artifact has duplicate element_id",
    )
    cell_ids: list[str] = []
    for element in elements:
        if element.get("element_type") == "table":
            for cell in (element.get("table") or {}).get("cells") or []:
                cell_ids.append(str(cell.get("cell_id")))
    require(all(cell_ids), f"{label}: saved artifact table cell is missing a cell_id")
    require(
        len(cell_ids) == len(set(cell_ids)),
        f"{label}: saved artifact has duplicate table cell_id",
    )


def load_artifacts(artifacts: Path) -> dict[str, Any]:
    """Load saved artifacts and reconstruct structure evidence when absent.

    A saved ``structure_evidence`` layer is validated as-is: an invalid version
    or broken join fails explicitly rather than being silently rebuilt. When
    both normalized and structured artifacts exist their document identities
    must agree, so incompatible artifacts are never mixed.
    """
    require(artifacts.is_dir(), f"artifacts directory does not exist: {artifacts}")

    provenance: dict[str, Any] = {
        "artifacts_dir": str(artifacts),
        "artifact_sha256": {},
        "missing": [],
    }
    for name in OPTIONAL_FILES:
        path = artifacts / name
        if path.is_file():
            provenance["artifact_sha256"][name] = sha256_file(path)

    sides: dict[str, Any] = {}
    for label, files in SIDE_FILES.items():
        structured_path = artifacts / files["structured"]
        normalized_path = artifacts / files["normalized"]
        structured_model = _load_json(structured_path) if structured_path.is_file() else None
        normalized_model = _load_json(normalized_path) if normalized_path.is_file() else None
        if structured_model is None and normalized_model is None:
            raise ValueError(
                f"missing saved layer for {label}: neither {files['structured']} "
                f"nor {files['normalized']} exists in {artifacts}"
            )
        if structured_model is not None and normalized_model is not None:
            structured_id = _document_of(structured_model).get("document_id")
            normalized_id = _document_of(normalized_model).get("document_id")
            require(
                structured_id == normalized_id,
                f"incompatible artifacts for {label}: structured document_id "
                f"{structured_id!r} != normalized document_id {normalized_id!r}",
            )
        if structured_model is not None:
            identity_path, model = structured_path, structured_model
        else:
            identity_path, model = normalized_path, normalized_model
        document = _document_of(model)
        _validate_loaded_model(document, model, label)
        for name in (files["structured"], files["normalized"], files["raw"], files["validation"]):
            path = artifacts / name
            if path.is_file():
                provenance["artifact_sha256"][name] = sha256_file(path)
            else:
                provenance["missing"].append(name)
        existing = document.get("structure_evidence")
        if existing is not None:
            require(
                isinstance(existing, dict),
                f"invalid saved structure_evidence for {label}: not a mapping",
            )
            require(
                existing.get("version") == structure.STRUCTURE_EVIDENCE_VERSION,
                f"unsupported saved structure evidence version for {label}: "
                f"{existing.get('version')!r}",
            )
            # Fail explicitly on broken joins/IDs; never silently rebuild.
            structure.validate_structure_evidence(document, existing)
            evidence = existing
            evidence_source = "saved"
        else:
            evidence = structure.build_structure_evidence(document)
            evidence_source = "reconstructed_from_saved_artifacts"
        sides[label] = {
            "identity_artifact": identity_path.name,
            "identity_artifact_sha256": sha256_file(identity_path),
            "document_id": document.get("document_id"),
            "schema_version": model.get("schema_version"),
            "document": document,
            "structure_evidence": evidence,
            "structure_evidence_source": evidence_source,
        }

    comparison_summary = None
    comparison_path = artifacts / COMPARISON
    if comparison_path.is_file():
        canonical = _load_json(comparison_path)
        statistics = canonical.get("statistics") or {}
        comparison_summary = {
            "available": True,
            "changes": statistics.get("changes"),
            "table_changes": statistics.get("table_changes"),
            "review_queue": len(canonical.get("review_queue") or []),
            "change_type_counts": statistics.get("change_type_counts"),
        }
    else:
        provenance["missing"].append(COMPARISON)
        comparison_summary = {"available": False}

    return {
        "provenance": provenance,
        "sides": sides,
        "comparison": comparison_summary,
    }


# ---------------------------------------------------------------------------
# Reference validation
# ---------------------------------------------------------------------------


def validate_reference(reference: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    require(isinstance(reference, dict), "reference must be a JSON object")
    require(
        reference.get("reference_version") == REFERENCE_VERSION,
        f"reference_version must be {REFERENCE_VERSION!r}",
    )
    kind = reference.get("kind")
    require(
        kind in {"synthetic_authored", "source_verified"},
        f"unknown or disallowed reference kind {kind!r}",
    )
    verified = reference.get("verified")
    synthetic = reference.get("synthetic")
    require(isinstance(verified, bool), "reference.verified must be a boolean")
    require(isinstance(synthetic, bool), "reference.synthetic must be a boolean")
    notes = reference.get("notes")
    require(
        isinstance(notes, str) and notes.strip(),
        "reference requires provenance notes",
    )
    if kind == "synthetic_authored":
        # The approved synthetic path is explicitly allowed unverified; it must
        # still declare itself synthetic and non-real.
        require(synthetic is True, "synthetic_authored reference must set synthetic=true")
    else:
        # A real, source-verified reference must be verified and non-synthetic.
        require(verified is True, "source_verified reference must set verified=true")
        require(synthetic is False, "source_verified reference must set synthetic=false")

    declared_documents = reference.get("documents")
    require(isinstance(declared_documents, dict), "reference.documents must be a mapping")
    for label in ("old", "new"):
        declared = declared_documents.get(label)
        require(isinstance(declared, dict), f"reference.documents.{label} must be a mapping")
        side = bundle["sides"][label]
        require(
            declared.get("document_id") == side["document_id"],
            f"reference document_id mismatch for {label}: "
            f"{declared.get('document_id')!r} != {side['document_id']!r}",
        )
        require(
            declared.get("artifact_sha256") == side["identity_artifact_sha256"],
            f"reference artifact fingerprint mismatch for {label} "
            f"({side['identity_artifact']})",
        )

    coverage = reference.get("coverage")
    require(isinstance(coverage, dict), "reference.coverage must be a mapping")

    validated: dict[str, Any] = {}
    for label in ("old", "new"):
        side = bundle["sides"][label]
        evidence = side["structure_evidence"]
        document = side["document"]
        entries_by_id = {entry["entry_id"]: entry for entry in evidence["toc"]["entries"]}
        element_ids = {str(element.get("element_id")) for element in document["elements"]}
        section_ids = {section["section_id"] for section in evidence["sections"]}
        membership_by_element = evidence["membership"]["by_element"]

        side_coverage = coverage.get(label)
        require(
            isinstance(side_coverage, dict),
            f"reference.coverage.{label} must be a mapping",
        )
        toc_links = side_coverage.get("toc_links")
        membership = side_coverage.get("membership")
        require(isinstance(toc_links, dict), f"reference.coverage.{label}.toc_links must be a mapping")
        require(isinstance(membership, dict), f"reference.coverage.{label}.membership must be a mapping")

        covered_entry_ids = list(toc_links.get("covered_entry_ids") or [])
        require(
            len(covered_entry_ids) == len(set(covered_entry_ids)),
            f"reference covered_entry_ids contains duplicates for {label}",
        )
        expected_links = toc_links.get("expected_body_element_ids")
        require(isinstance(expected_links, dict), "expected_body_element_ids must be a mapping")
        for entry_id in covered_entry_ids:
            require(entry_id in entries_by_id, f"reference covers unknown TOC entry {entry_id!r} for {label}")
            require(
                entry_id in expected_links,
                f"reference is missing an expected link for covered entry {entry_id!r} for {label}",
            )
        for entry_id, body_id in expected_links.items():
            require(entry_id in covered_entry_ids, f"expected link for uncovered entry {entry_id!r} for {label}")
            require(
                body_id is None or body_id in element_ids,
                f"reference expects unknown body element {body_id!r} for {label}",
            )

        covered_element_ids = list(membership.get("covered_element_ids") or [])
        require(
            len(covered_element_ids) == len(set(covered_element_ids)),
            f"reference covered_element_ids contains duplicates for {label}",
        )
        expected_sections = membership.get("expected_section_ids")
        require(isinstance(expected_sections, dict), "expected_section_ids must be a mapping")
        for element_id in covered_element_ids:
            require(
                element_id in membership_by_element,
                f"reference covers unknown element {element_id!r} for {label}",
            )
            require(
                element_id in expected_sections,
                f"reference is missing an expected section for covered element {element_id!r} for {label}",
            )
        for element_id, section_id in expected_sections.items():
            require(
                element_id in covered_element_ids,
                f"expected section for uncovered element {element_id!r} for {label}",
            )
            require(
                section_id is None or section_id in section_ids,
                f"reference expects unknown section {section_id!r} for {label}",
            )

        validated[label] = {
            "covered_entry_ids": covered_entry_ids,
            "expected_body_element_ids": expected_links,
            "covered_element_ids": covered_element_ids,
            "expected_section_ids": expected_sections,
        }

    return {
        "reference_version": reference.get("reference_version"),
        "kind": kind,
        "verified": verified,
        "synthetic": synthetic,
        "notes": notes,
        "coverage": validated,
    }


# ---------------------------------------------------------------------------
# Structure scoring
# ---------------------------------------------------------------------------


def _metric(tp: int, fp: int, fn: int) -> dict[str, Any]:
    truth_count = tp + fn
    prediction_count = tp + fp
    precision = tp / prediction_count if prediction_count else None
    recall = tp / truth_count if truth_count else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    if truth_count == 0:
        status = "false_positive" if prediction_count > 0 else "not_applicable"
    else:
        status = "scored"
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "support": truth_count,
        "prediction_count": prediction_count,
        "precision": None if precision is None else round(precision, 3),
        "recall": None if recall is None else round(recall, 3),
        "f1": None if f1 is None else round(f1, 3),
        "status": status,
    }


def _pair_metric(pairs: list[tuple[str, Any, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Score (id, predicted, expected) pairs with strict pairing.

    A non-null prediction that disagrees with a non-null expectation counts as
    BOTH a false positive (the prediction is wrong) and a false negative (the
    expected item was not matched). A missing prediction against a non-null
    expectation is a false negative; a prediction against a null expectation is
    a false positive.
    """
    tp = fp = fn = 0
    failures: list[dict[str, Any]] = []
    for key, predicted, expected in pairs:
        if expected is not None and predicted == expected:
            tp += 1
            continue
        if expected is None and predicted is None:
            continue
        if predicted is not None:
            fp += 1
        if expected is not None:
            fn += 1
        failures.append({"id": key, "expected": expected, "predicted": predicted})
    return _metric(tp, fp, fn), failures


def score_structure_side(evidence: dict[str, Any], coverage: dict[str, Any] | None) -> dict[str, Any]:
    entries_by_id = {entry["entry_id"]: entry for entry in evidence["toc"]["entries"]}
    membership_by_element = evidence["membership"]["by_element"]

    if coverage is None:
        return {
            "status": "not_scored",
            "reason": "no_reference_annotations",
            "links": None,
            "membership": None,
            "failures": [],
        }

    link_pairs = [
        (entry_id, entries_by_id[entry_id]["body_element_id"], coverage["expected_body_element_ids"][entry_id])
        for entry_id in coverage["covered_entry_ids"]
    ]
    links, link_failures = _pair_metric(link_pairs)
    predicted_linked_ids = {entry["entry_id"] for entry in evidence["toc"]["entries"] if entry["body_element_id"]}
    coverage_complete = set(coverage["covered_entry_ids"]) >= predicted_linked_ids
    links["global_precision"] = links["precision"] if coverage_complete else None
    links["coverage_complete"] = coverage_complete

    membership_pairs = [
        (element_id, membership_by_element[element_id]["section_id"], coverage["expected_section_ids"][element_id])
        for element_id in coverage["covered_element_ids"]
    ]
    membership, membership_failures = _pair_metric(membership_pairs)
    membership["coverage_complete"] = set(coverage["covered_element_ids"]) >= {
        element_id for element_id, record in membership_by_element.items() if record["section_id"] is not None
    }

    blocks = (links, membership)
    if any(block["status"] == "false_positive" for block in blocks):
        status = "false_positive"
        reason = "false_predictions_without_positive_truth"
    elif any(block["status"] == "scored" for block in blocks):
        status = "scored"
        reason = None
    else:
        status = "not_applicable"
        reason = "declared_coverage_has_no_positive_expectations"
    return {
        "status": status,
        "reason": reason,
        "scoring_definition": (
            "TP: a declared covered link/membership whose prediction equals the declared "
            "expectation. A non-null prediction that disagrees with a non-null expectation "
            "counts as BOTH FP and FN. A missing prediction against a non-null expectation "
            "is FN; a prediction against a null expectation is FP. Precision/recall are "
            "computed only over declared coverage; global_precision is null unless every "
            "predicted link is covered. Empty positive truth with false predictions is "
            "status=false_positive, never not_applicable."
        ),
        "links": links,
        "membership": membership,
        "failures": link_failures + membership_failures,
    }


# ---------------------------------------------------------------------------
# Metrics assembly
# ---------------------------------------------------------------------------


def _extraction_diagnostics(document: dict[str, Any]) -> dict[str, Any]:
    """Available saved-evidence diagnostics. These are not accuracy scores."""
    elements = document["elements"]
    role_counts = Counter(element.get("original_role") or "unlabeled" for element in elements)
    warning_flags: Counter = Counter()
    review_flags: Counter = Counter()
    missing_span_elements = 0
    missing_location_elements = 0
    missing_span_cells = 0
    missing_location_cells = 0
    for element in elements:
        validation = element.get("extraction_validation") or {}
        for flag in validation.get("extraction_warning_flags") or []:
            warning_flags[flag] += 1
        for flag in validation.get("extraction_review_flags") or []:
            review_flags[flag] += 1
        if not element.get("spans"):
            missing_span_elements += 1
        if element.get("primary_page") is None or not element.get("primary_polygon"):
            missing_location_elements += 1
        for cell in (element.get("table") or {}).get("cells") or []:
            if not cell.get("spans"):
                missing_span_cells += 1
            if cell.get("primary_page") is None or not cell.get("primary_polygon"):
                missing_location_cells += 1
    return {
        "original_role_counts": dict(sorted(role_counts.items())),
        "extraction_warning_flags": dict(sorted(warning_flags.items())),
        "extraction_review_flags": dict(sorted(review_flags.items())),
        "missing_span_elements": missing_span_elements,
        "missing_location_elements": missing_location_elements,
        "missing_span_cells": missing_span_cells,
        "missing_location_cells": missing_location_cells,
    }


def build_metrics(bundle: dict[str, Any], reference: dict[str, Any] | None) -> dict[str, Any]:
    validated_reference = None
    errors: list[str] = []
    if reference is not None:
        validated_reference = validate_reference(reference, bundle)

    structure_layer: dict[str, Any] = {}
    for label in ("old", "new"):
        side = bundle["sides"][label]
        evidence = side["structure_evidence"]
        coverage = validated_reference["coverage"][label] if validated_reference else None
        scoring = score_structure_side(evidence, coverage)
        structure_layer[label] = {
            "document_id": side["document_id"],
            "structure_evidence_source": side["structure_evidence_source"],
            "structure_evidence_version": evidence["version"],
            "toc": {
                "detected": evidence["toc"]["detected"],
                "usable": evidence["toc"]["usable"],
                "source_kinds": evidence["toc"]["source_kinds"],
                "entry_count": evidence["toc"]["entry_count"],
                "linked_count": evidence["toc"]["linked_count"],
                "ambiguous_count": evidence["toc"]["ambiguous_count"],
                "unmatched_count": evidence["toc"]["unmatched_count"],
                "ambiguous_entry_ids": evidence["toc"]["ambiguous_entry_ids"],
                "unmatched_entry_ids": evidence["toc"]["unmatched_entry_ids"],
            },
            "sections": len(evidence["sections"]),
            "membership_counts": evidence["membership"]["counts"],
            "scoring": scoring,
        }

    comparison = bundle["comparison"]
    comparison_layer = {
        "available": bool(comparison.get("available")),
        "changes": comparison.get("changes"),
        "table_changes": comparison.get("table_changes"),
        "review_queue": comparison.get("review_queue"),
        "change_type_counts": comparison.get("change_type_counts"),
        "semantic_accuracy": None,
        "semantic_accuracy_status": "not_scored",
        "semantic_accuracy_reason": "no_labeled_old_new_pairs_available",
    }

    extraction_layer = {
        "inventory": {
            label: {
                "document_id": bundle["sides"][label]["document_id"],
                "elements": len(bundle["sides"][label]["document"]["elements"]),
                "pages": bundle["sides"][label]["document"].get("page_count"),
                **_extraction_diagnostics(bundle["sides"][label]["document"]),
            }
            for label in ("old", "new")
        },
        "text_accuracy": None,
        "missing_content": None,
        "location_accuracy": None,
        "diagnostics_are_accuracy": False,
        "status": "not_scored",
        "reason": "no_verified_source_annotations",
    }

    if validated_reference is not None:
        reference_block = {
            "provided": True,
            "reference_version": validated_reference["reference_version"],
            "kind": validated_reference["kind"],
            "verified": validated_reference["verified"],
            "synthetic": validated_reference["synthetic"],
            "notes": validated_reference["notes"],
            "status": "scored",
            "errors": [],
        }
    else:
        reference_block = {
            "provided": False,
            "status": "not_scored",
            "reason": "no_reference_annotations",
            "errors": [],
        }

    support = {
        label: {
            "elements": len(bundle["sides"][label]["document"]["elements"]),
            "toc_entries": bundle["sides"][label]["structure_evidence"]["toc"]["entry_count"],
            "sections": len(bundle["sides"][label]["structure_evidence"]["sections"]),
        }
        for label in ("old", "new")
    }

    skips = [
        {"layer": "extraction", "reason": "no_verified_source_annotations"},
        {"layer": "comparison", "reason": "no_labeled_old_new_pairs_available"},
    ]

    # Surface empty-positive-truth false predictions at the top level so a
    # malformed or trivial reference cannot hide them inside a side block.
    failures = []
    for label in ("old", "new"):
        scoring = structure_layer[label]["scoring"]
        for block_name in ("links", "membership"):
            block = scoring.get(block_name)
            if isinstance(block, dict) and block.get("status") == "false_positive":
                failures.append({
                    "side": label,
                    "block": block_name,
                    "status": "false_positive",
                    "false_positives": block.get("fp"),
                })

    return {
        "bundle_version": REVIEW_BUNDLE_VERSION,
        "structure_evidence_version": structure.STRUCTURE_EVIDENCE_VERSION,
        "step5_version": STEP5_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "reference": reference_block,
        "layers": {
            "extraction": extraction_layer,
            "structure": structure_layer,
            "comparison": comparison_layer,
        },
        "support": support,
        "errors": errors,
        "failures": failures,
        "skips": skips,
        "config": {
            "artifacts": bundle["provenance"]["artifacts_dir"],
            "reference_provided": reference is not None,
            "synthetic": bool(validated_reference and validated_reference["synthetic"]),
        },
        "limitations": [
            "evidence_visualization_not_source_page_truth",
            "no_source_pdfs_so_no_claimed_source_verification",
            "synthetic_references_are_not_real_pdf_accuracy_claims",
        ],
    }


# ---------------------------------------------------------------------------
# Bundle writer
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def review_evidence_command(
    artifacts: Path,
    out: Path,
    reference: Path | None = None,
) -> Path:
    artifacts = Path(artifacts).expanduser().resolve()
    out = Path(out).expanduser().resolve()

    require(artifacts.is_dir(), f"artifacts directory does not exist: {artifacts}")
    require(
        out != artifacts and artifacts not in out.parents,
        "output directory must not be inside the input artifact tree",
    )
    if out.exists():
        require(
            out.is_dir() and not any(out.iterdir()),
            f"refusing to write into populated output directory: {out}",
        )

    bundle = load_artifacts(artifacts)

    reference_model = None
    if reference is not None:
        reference_path = Path(reference).expanduser().resolve()
        require(reference_path.is_file(), f"reference file does not exist: {reference_path}")
        reference_model = _load_json(reference_path)

    metrics = build_metrics(bundle, reference_model)

    out.mkdir(parents=True, exist_ok=True)

    context = {
        "metrics": metrics,
        "provenance": bundle["provenance"],
        "sides": {
            label: {
                "document": bundle["sides"][label]["document"],
                "structure_evidence": bundle["sides"][label]["structure_evidence"],
                "document_id": bundle["sides"][label]["document_id"],
                "structure_evidence_source": bundle["sides"][label]["structure_evidence_source"],
            }
            for label in ("old", "new")
        },
    }

    _atomic_write_text(out / "index.html", render_html(context))
    _atomic_write_json(out / "metrics.json", metrics)
    for label in ("old", "new"):
        _atomic_write_json(out / f"structure_evidence_{label}.json", bundle["sides"][label]["structure_evidence"])
    _atomic_write_json(out / "provenance.json", bundle["provenance"])
    _atomic_write_json(out / "reference_template.json", REFERENCE_TEMPLATE)

    return out
