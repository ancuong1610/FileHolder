"""TDD coverage for the offline evidence review and metrics (T-02/T-03).

These tests pin metric integrity (no reference -> not_scored, strict TP/FP/FN
pairing, empty-truth false positives, partial-coverage global precision
undefined, fingerprint/ID/gating rejection), safe HTML escaping, fresh-output
refusal, saved-artifact validation, and that saved artifacts are never
rewritten. Reference annotations are authored by the tests, never derived from
the predictions they score.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crr_compare import structure
from crr_compare.evidence_review import (
    REFERENCE_VERSION,
    REVIEW_BUNDLE_VERSION,
    review_evidence_command,
    sha256_file,
)
from crr_compare.evidence_review_html import _table_grid
from crr_compare.stages.s05_anchors import STEP5_VERSION
from tests import structure_fixtures as fixtures

ROOT = Path(__file__).resolve().parents[1]


def _structured_model(document: dict) -> dict:
    return {
        "schema_version": "0.3.0",
        "processing": {
            "section_reconstruction": {
                "version": STEP5_VERSION,
                "strategy": "provisional_anchor_evidence",
            }
        },
        "document": document,
    }


class ReviewCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="evidence-review-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.artifacts = self.base / "artifacts"
        self.artifacts.mkdir()
        self.old_doc = fixtures.load("toc_text")
        self.new_doc = fixtures.load("toc_text")
        self._write_sides(self.old_doc, self.new_doc)
        self.old_evidence = structure.build_structure_evidence(self.old_doc)
        self.new_evidence = structure.build_structure_evidence(self.new_doc)
        self.old_sha = sha256_file(self.artifacts / "old_document_structured.json")
        self.new_sha = sha256_file(self.artifacts / "new_document_structured.json")
        canonical = {
            "statistics": {"changes": 3, "table_changes": 1, "change_type_counts": {"MODIFIED": 1}},
            "review_queue": [{"source_kind": "change"}],
        }
        (self.artifacts / "comparison_result.json").write_text(json.dumps(canonical), encoding="utf-8")

    def _write_sides(self, old_doc: dict, new_doc: dict) -> None:
        (self.artifacts / "old_document_structured.json").write_text(
            json.dumps(_structured_model(old_doc)), encoding="utf-8"
        )
        (self.artifacts / "new_document_structured.json").write_text(
            json.dumps(_structured_model(new_doc)), encoding="utf-8"
        )

    def _entry(self, evidence: dict, title: str) -> dict:
        for entry in evidence["toc"]["entries"]:
            if entry["title"] == title:
                return entry
        raise AssertionError(f"no entry {title!r}")

    def _reference(
        self,
        *,
        old_toc=None,
        old_membership=None,
        new_toc=None,
        new_membership=None,
        old_sha=None,
        new_sha=None,
        kind="synthetic_authored",
        verified=False,
        synthetic=True,
        notes="authored in tests",
    ) -> dict:
        def links(covered=None, expected=None):
            return {"covered_entry_ids": list(covered or []), "expected_body_element_ids": dict(expected or {})}

        def membership(covered=None, expected=None):
            return {"covered_element_ids": list(covered or []), "expected_section_ids": dict(expected or {})}

        return {
            "reference_version": REFERENCE_VERSION,
            "kind": kind,
            "verified": verified,
            "synthetic": synthetic,
            "notes": notes,
            "documents": {
                "old": {"document_id": self.old_doc["document_id"], "artifact_sha256": old_sha or self.old_sha},
                "new": {"document_id": self.new_doc["document_id"], "artifact_sha256": new_sha or self.new_sha},
            },
            "coverage": {
                "old": {"toc_links": old_toc or links(), "membership": old_membership or membership()},
                "new": {"toc_links": new_toc or links(), "membership": new_membership or membership()},
            },
        }

    def _write_reference(self, reference: dict, name: str = "reference.json") -> Path:
        path = self.base / name
        path.write_text(json.dumps(reference), encoding="utf-8")
        return path

    def _run(self, reference: dict | None = None, out_name: str = "out") -> Path:
        out = self.base / out_name
        ref_path = self._write_reference(reference) if reference is not None else None
        review_evidence_command(artifacts=self.artifacts, out=out, reference=ref_path)
        return out

    def _scoring(self, out: Path, side: str = "old") -> dict:
        metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        return metrics["layers"]["structure"][side]["scoring"]

    def _metrics(self, out: Path) -> dict:
        return json.loads((out / "metrics.json").read_text(encoding="utf-8"))


class NoReferenceTest(ReviewCase):
    def test_missing_reference_is_not_scored_with_null_metrics(self):
        out = self._run()
        metrics = self._metrics(out)
        self.assertFalse(metrics["reference"]["provided"])
        scoring = metrics["layers"]["structure"]["old"]["scoring"]
        self.assertEqual(scoring["status"], "not_scored")
        self.assertIsNone(scoring["links"])
        self.assertIsNone(scoring["membership"])
        self.assertIsNone(metrics["layers"]["extraction"]["text_accuracy"])
        self.assertIsNone(metrics["layers"]["comparison"]["semantic_accuracy"])
        self.assertTrue((out / "index.html").is_file())
        self.assertTrue((out / "provenance.json").is_file())
        self.assertTrue((out / "structure_evidence_old.json").is_file())
        self.assertTrue((out / "structure_evidence_new.json").is_file())

    def test_reference_template_is_written_unverified(self):
        out = self._run()
        template = json.loads((out / "reference_template.json").read_text(encoding="utf-8"))
        self.assertEqual(template["kind"], "UNVERIFIED_TEMPLATE")
        self.assertFalse(template["verified"])


class KnownScoringTest(ReviewCase):
    def test_correct_null_membership_is_not_a_failure(self):
        reference = self._reference(old_membership={
            "covered_element_ids": ["P-00000"],
            "expected_section_ids": {"P-00000": None},
        })
        out = self._run(reference)
        scoring = self._scoring(out)
        self.assertEqual(scoring["failures"], [])
        self.assertEqual(scoring["membership"]["fp"], 0)
        self.assertEqual(scoring["membership"]["fn"], 0)
        self.assertEqual(scoring["membership"]["status"], "not_applicable")

    def test_wrong_link_counts_as_both_fp_and_fn(self):
        document = fixtures.load("toc_text")
        document["elements"] = [e for e in document["elements"] if e["element_id"] != "P-00010"]
        self.old_doc = document
        self._write_sides(document, self.new_doc)
        self.old_sha = sha256_file(self.artifacts / "old_document_structured.json")
        evidence = structure.build_structure_evidence(document)
        introduction = self._entry(evidence, "Introduction")
        methods = self._entry(evidence, "Methods")
        data = self._entry(evidence, "Data")
        self.assertEqual(data["status"], "unmatched")
        old_toc = {
            "covered_entry_ids": [introduction["entry_id"], methods["entry_id"], data["entry_id"]],
            "expected_body_element_ids": {
                introduction["entry_id"]: introduction["body_element_id"],  # TP
                methods["entry_id"]: "P-00011",  # wrong -> FP + FN
                data["entry_id"]: "P-00007",  # missing -> FN
            },
        }
        out = self._run(self._reference(old_toc=old_toc))
        links = self._scoring(out)["links"]
        self.assertEqual((links["tp"], links["fp"], links["fn"]), (1, 1, 2))
        self.assertEqual(links["precision"], 0.5)
        self.assertEqual(links["recall"], 0.333)
        self.assertEqual(self._scoring(out)["status"], "scored")
        self.assertEqual(self._metrics(out)["reference"]["kind"], "synthetic_authored")
        self.assertTrue(self._metrics(out)["config"]["synthetic"])

    def test_partial_coverage_leaves_global_precision_undefined(self):
        introduction = self._entry(self.old_evidence, "Introduction")
        old_toc = {
            "covered_entry_ids": [introduction["entry_id"]],
            "expected_body_element_ids": {introduction["entry_id"]: introduction["body_element_id"]},
        }
        out = self._run(self._reference(old_toc=old_toc))
        links = self._scoring(out)["links"]
        self.assertEqual(links["precision"], 1.0)
        self.assertIsNone(links["global_precision"])
        self.assertFalse(links["coverage_complete"])

    def test_empty_declared_coverage_is_not_applicable_not_perfect(self):
        out = self._run(self._reference())
        scoring = self._scoring(out)
        self.assertEqual(scoring["status"], "not_applicable")
        self.assertIsNone(scoring["links"]["precision"])
        self.assertIsNone(scoring["links"]["recall"])

    def test_empty_truth_with_false_predictions_is_a_visible_failure(self):
        introduction = self._entry(self.old_evidence, "Introduction")
        old_toc = {
            "covered_entry_ids": [introduction["entry_id"]],
            "expected_body_element_ids": {introduction["entry_id"]: None},
        }
        out = self._run(self._reference(old_toc=old_toc))
        scoring = self._scoring(out)
        self.assertEqual(scoring["links"]["status"], "false_positive")
        self.assertEqual(scoring["links"]["fp"], 1)
        self.assertEqual(scoring["status"], "false_positive")
        failures = self._metrics(out)["failures"]
        self.assertTrue(any(item["side"] == "old" and item["block"] == "links" for item in failures), failures)

    def test_membership_pairing_matches_links(self):
        membership = self.old_evidence["membership"]["by_element"]
        intro_section = membership["P-00007"]["section_id"]
        methods_section = membership["P-00009"]["section_id"]
        new_membership = {
            "covered_element_ids": ["P-00007", "P-00009"],
            "expected_section_ids": {"P-00007": intro_section, "P-00009": intro_section},
        }
        out = self._run(self._reference(new_membership=new_membership))
        block = self._scoring(out, "new")["membership"]
        self.assertEqual((block["tp"], block["fp"], block["fn"]), (1, 1, 1))
        self.assertEqual(block["precision"], 0.5)
        self.assertNotEqual(intro_section, methods_section)


class ReferenceRejectionTest(ReviewCase):
    def test_fingerprint_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(old_sha="0" * 64))

    def test_unknown_covered_entry_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(old_toc={"covered_entry_ids": ["NOPE"], "expected_body_element_ids": {"NOPE": None}}))

    def test_duplicate_covered_entry_ids_are_rejected(self):
        introduction = self._entry(self.old_evidence, "Introduction")
        with self.assertRaises(ValueError):
            self._run(self._reference(old_toc={
                "covered_entry_ids": [introduction["entry_id"], introduction["entry_id"]],
                "expected_body_element_ids": {introduction["entry_id"]: None},
            }))

    def test_missing_expected_link_key_is_invalid(self):
        introduction = self._entry(self.old_evidence, "Introduction")
        with self.assertRaises(ValueError):
            self._run(self._reference(old_toc={
                "covered_entry_ids": [introduction["entry_id"]],
                "expected_body_element_ids": {},
            }))

    def test_missing_expected_section_key_is_invalid(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(new_membership={
                "covered_element_ids": ["P-00007"],
                "expected_section_ids": {},
            }))

    def test_unknown_expected_section_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(new_membership={
                "covered_element_ids": ["P-00007"],
                "expected_section_ids": {"P-00007": "NOT-A-SECTION"},
            }))


class ReferenceGatingTest(ReviewCase):
    def test_unverified_template_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(kind="UNVERIFIED_TEMPLATE", synthetic=False))

    def test_unverified_nonsynthetic_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(kind="source_verified", verified=False, synthetic=False))

    def test_source_verified_reference_must_be_non_synthetic(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(kind="source_verified", verified=True, synthetic=True))

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run(self._reference(kind="mystery"))

    def test_synthetic_authored_may_be_unverified(self):
        out = self._run(self._reference(kind="synthetic_authored", verified=False, synthetic=True))
        reference = self._metrics(out)["reference"]
        self.assertEqual(reference["kind"], "synthetic_authored")
        self.assertFalse(reference["verified"])
        self.assertTrue(reference["synthetic"])

    def test_source_verified_verified_non_synthetic_is_accepted(self):
        out = self._run(self._reference(kind="source_verified", verified=True, synthetic=False))
        reference = self._metrics(out)["reference"]
        self.assertEqual(reference["kind"], "source_verified")
        self.assertTrue(reference["verified"])
        self.assertFalse(reference["synthetic"])


class SavedArtifactValidationTest(ReviewCase):
    def _write_structured(self, label: str, model: dict) -> None:
        (self.artifacts / f"{label}_document_structured.json").write_text(
            json.dumps(model), encoding="utf-8"
        )

    def test_invalid_saved_structure_evidence_version_is_rejected_not_rebuilt(self):
        model = _structured_model(self.old_doc)
        model["document"]["structure_evidence"] = {"version": "9.9.9"}
        self._write_structured("old", model)
        with self.assertRaises(ValueError):
            self._run()

    def test_broken_saved_structure_evidence_joins_are_rejected_not_rebuilt(self):
        model = _structured_model(self.old_doc)
        evidence = structure.build_structure_evidence(self.old_doc)
        del evidence["membership"]["by_element"]["P-00001"]
        model["document"]["structure_evidence"] = evidence
        self._write_structured("old", model)
        with self.assertRaises(ValueError):
            self._run()

    def test_missing_document_id_is_rejected(self):
        model = _structured_model(self.old_doc)
        del model["document"]["document_id"]
        self._write_structured("old", model)
        with self.assertRaises(ValueError):
            self._run()

    def test_missing_schema_version_is_rejected(self):
        model = _structured_model(self.old_doc)
        del model["schema_version"]
        self._write_structured("old", model)
        with self.assertRaises(ValueError):
            self._run()

    def test_duplicate_element_id_is_rejected(self):
        document = fixtures.load("toc_text")
        document["elements"][1]["element_id"] = document["elements"][0]["element_id"]
        self._write_structured("old", _structured_model(document))
        with self.assertRaises(ValueError):
            self._run()

    def test_duplicate_table_cell_id_is_rejected(self):
        document = fixtures.load("toc_table")
        cells = document["elements"][1]["table"]["cells"]
        cells[1]["cell_id"] = cells[0]["cell_id"]
        self._write_structured("old", _structured_model(document))
        with self.assertRaises(ValueError):
            self._run()

    def test_normalized_and_structured_id_mismatch_is_rejected(self):
        normalized = _structured_model(self.old_doc)
        normalized["document"]["document_id"] = "SOMETHING-ELSE"
        (self.artifacts / "old_document_normalized.json").write_text(
            json.dumps(normalized), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            self._run()


class OutputSafetyTest(ReviewCase):
    def test_refuses_populated_output_directory(self):
        out = self.base / "populated"
        out.mkdir()
        (out / "existing.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            review_evidence_command(artifacts=self.artifacts, out=out)

    def test_refuses_output_inside_artifacts(self):
        with self.assertRaises(ValueError):
            review_evidence_command(artifacts=self.artifacts, out=self.artifacts / "review")

    def test_saved_artifacts_are_not_rewritten(self):
        before = {
            path.name: sha256_file(path)
            for path in sorted(self.artifacts.iterdir())
            if path.is_file()
        }
        self._run()
        after = {
            path.name: sha256_file(path)
            for path in sorted(self.artifacts.iterdir())
            if path.is_file()
        }
        self.assertEqual(before, after)


class HtmlSafetyTest(ReviewCase):
    def test_document_strings_are_escaped(self):
        document = fixtures.load("toc_text")
        document["elements"][0]["text"] = "<script>alert(1)</script>"
        document["elements"][0]["normalized_text"] = "<script>alert(1)</script>"
        self.old_doc = document
        self._write_sides(document, self.new_doc)
        self.old_sha = sha256_file(self.artifacts / "old_document_structured.json")
        out = self._run()
        rendered = (out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("http://", rendered.replace("http://www.w3.org", ""))
        self.assertNotIn("src=", rendered)


class CliTest(ReviewCase):
    def test_cli_review_evidence_writes_bundle(self):
        out = self.base / "cli-out"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "crr_compare",
                "review-evidence",
                "--artifacts",
                str(self.artifacts),
                "--out",
                str(out),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ROOT),
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.base),
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((out / "index.html").is_file())
        self.assertTrue((out / "metrics.json").is_file())


class BundleVersionTest(ReviewCase):
    def test_metrics_report_bundle_and_reference_versions(self):
        out = self._run()
        metrics = self._metrics(out)
        self.assertEqual(metrics["bundle_version"], REVIEW_BUNDLE_VERSION)
        self.assertEqual(metrics["structure_evidence_version"], structure.STRUCTURE_EVIDENCE_VERSION)


class MergedGridTest(unittest.TestCase):
    @staticmethod
    def _cell(cell_id, row, column, text, row_span=1, column_span=1):
        return {
            "cell_id": cell_id,
            "row_index": row,
            "column_index": column,
            "row_span": row_span,
            "column_span": column_span,
            "text": text,
            "normalized_text": text,
            "spans": [{"offset": 0, "length": 1}],
            "primary_page": 1,
            "primary_polygon": [0, 0, 1, 0, 1, 1, 0, 1],
        }

    @staticmethod
    def _table(rows, columns, cells):
        return {"table": {"row_count": rows, "column_count": columns, "cells": cells}}

    def test_colspan_merge_emits_no_extra_cells(self):
        table = self._table(2, 2, [
            self._cell("C1", 0, 0, "Header", column_span=2),
            self._cell("C2", 1, 0, "A"),
            self._cell("C3", 1, 1, "B"),
        ])
        rendered = _table_grid(table)
        self.assertEqual(rendered.count("<td"), 3)
        self.assertNotIn("·", rendered)

    def test_rowspan_merge_skips_covered_coordinates(self):
        table = self._table(3, 2, [
            self._cell("C1", 0, 0, "R", row_span=2),
            self._cell("C2", 0, 1, "H"),
            self._cell("C3", 1, 1, "X"),
            self._cell("C4", 2, 0, "A"),
            self._cell("C5", 2, 1, "B"),
        ])
        rendered = _table_grid(table)
        self.assertEqual(rendered.count("<td"), 5)
        self.assertNotIn("·", rendered)

    def test_placeholder_only_for_a_genuine_gap(self):
        table = self._table(1, 2, [self._cell("C1", 0, 0, "A")])
        rendered = _table_grid(table)
        self.assertEqual(rendered.count("<td"), 2)
        self.assertIn("·", rendered)


class ReviewCompletenessTest(ReviewCase):
    def test_score_coverage_fields_and_mismatches_are_visible(self):
        introduction = self._entry(self.old_evidence, "Introduction")
        old_toc = {
            "covered_entry_ids": [introduction["entry_id"]],
            "expected_body_element_ids": {introduction["entry_id"]: "P-00007"},
        }
        out = self._run(self._reference(old_toc=old_toc))
        rendered = (out / "index.html").read_text(encoding="utf-8")
        for token in ("support", "F1", "global precision", "coverage complete", ">links<", ">membership<"):
            self.assertIn(token, rendered, token)
        self.assertIn("Mismatches in declared coverage", rendered)
        self.assertIn("P-00007", rendered)
        self.assertIn("P-00006", rendered)

    def test_extraction_diagnostics_are_visible_but_not_accuracy(self):
        out = self._run()
        metrics = self._metrics(out)
        diagnostics = metrics["layers"]["extraction"]["inventory"]["old"]
        self.assertIn("original_role_counts", diagnostics)
        self.assertIn("extraction_warning_flags", diagnostics)
        self.assertIn("extraction_review_flags", diagnostics)
        self.assertIn("missing_span_elements", diagnostics)
        self.assertIn("missing_location_cells", diagnostics)
        self.assertFalse(metrics["layers"]["extraction"]["diagnostics_are_accuracy"])
        rendered = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("Extraction layer (diagnostics only)", rendered)
        self.assertIn("original_role counts", rendered)

    def test_raw_and_normalized_text_are_shown_separately(self):
        document = fixtures.load("toc_text")
        document["elements"][1]["text"] = "RAW VALUE"
        document["elements"][1]["normalized_text"] = "normalized value"
        self._write_sides(document, document)
        out = self._run()
        rendered = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("RAW VALUE", rendered)
        self.assertIn("normalized value", rendered)
        self.assertIn("raw:", rendered)
        self.assertIn("normalized:", rendered)

    def test_table_represented_paragraph_resolves_source_table(self):
        document = fixtures.load("toc_table")
        self._write_sides(document, document)
        out = self._run()
        rendered = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("table-represented paragraph", rendered)
        self.assertIn("T-00001", rendered)
        self.assertIn("raw:", rendered)


if __name__ == "__main__":
    unittest.main()
