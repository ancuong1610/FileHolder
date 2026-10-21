"""Synthetic PDF outline prepass tests (approved outline-first slice).

These tests use locally generated PDFs only. They pin the source-bound contract:
native bookmarks are collected before DI, stored with the PDF SHA-256 and page
count, and consumed by Stage 5 only when the extraction identity still matches.
No real/private PDF or provider call is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from document_compare.phase1.extraction import pdf_outline
from document_compare.phase1.stages import s01_extract, s03_internal_schema, s05_anchors
from document_compare.phase1.structure import reconstruction
from tests.support import structure_fixtures


def _make_pdf(path: Path, *, pages: int = 3, toc=None, labels=None) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page()
        page.insert_text((72, 72), f"Synthetic page {number}")
    if labels:
        document.set_page_labels(labels)
    if toc:
        document.set_toc(toc)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_for(path: Path, *, side: str = "old") -> dict:
    return pdf_outline.build_outline_evidence(path, side=side)


def _document(elements, *, pages: int = 3, sha: str | None = "a" * 64,
              page_count: int | None = None, document_id: str = "DOC-OLD") -> dict:
    document = structure_fixtures.document(document_id, elements, pages)
    if sha is not None:
        document["source"] = {
            "provider": "azure_document_intelligence",
            "extraction_source": {
                "format": "pdf",
                "sha256": sha,
                "page_count": page_count if page_count is not None else pages,
                "basename": "source.pdf",
            },
        }
    return document


def _structured_model(document: dict) -> dict:
    return {
        "schema_version": s05_anchors.INPUT_SCHEMA_VERSION,
        "processing": {
            "internal_schema": {"version": "3.0.0"},
            "normalization": {"version": "1.0.0"},
        },
        "document": document,
    }


class AcquisitionTest(unittest.TestCase):
    def test_complete_outline_is_versioned_and_source_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(
                Path(directory) / "complete.pdf",
                toc=[[1, "Introduction", 2], [2, "Data", 3]],
            )
            evidence = _evidence_for(path)
            output = Path(directory) / "output"
            sidecar = pdf_outline.save_outline_sidecar(evidence, output)
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            expected_sha = _sha(path)

        self.assertEqual(saved["schema_version"], pdf_outline.OUTLINE_SIDECAR_VERSION)
        self.assertEqual(saved["side"], "old")
        self.assertEqual(saved["status"], "available")
        self.assertEqual(saved["source"]["sha256"], expected_sha)
        self.assertEqual(saved["source"]["page_count"], 3)
        self.assertEqual([entry["title"] for entry in saved["entries"]],
                         ["Introduction", "Data"])
        self.assertEqual([entry["level"] for entry in saved["entries"]], [1, 2])
        self.assertEqual([entry["physical_page"] for entry in saved["entries"]], [2, 3])
        # Bookmark acquisition stores no invented source element ids.
        self.assertFalse(any("element_id" in entry for entry in saved["entries"]))

    def test_absent_outline_is_a_normal_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(Path(directory) / "absent.pdf")
            evidence = _evidence_for(path)
        self.assertEqual(evidence["status"], "absent")
        self.assertEqual(evidence["entries"], [])

    def test_page_labels_are_preserved_but_physical_page_stays_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(
                Path(directory) / "labels.pdf",
                toc=[[1, "Introduction", 2]],
                labels=[{"startpage": 0, "prefix": "Cover-", "style": "D",
                         "firstpagenum": 1}],
            )
            evidence = _evidence_for(path)
        entry = evidence["entries"][0]
        self.assertEqual(entry["physical_page"], 2)
        self.assertEqual(entry["page_label"], "Cover-2")
        self.assertNotEqual(entry["page_label"], str(entry["physical_page"]))
        self.assertNotIn("printed_page", entry)

    def test_partial_invalid_and_external_destinations_are_diagnostics(self):
        raw = [
            (1, "Introduction", 2, {"kind": 1, "page": 1}),
            (1, "External", -1, {"kind": 0, "page": -1}),
            (2, "Beyond", 99, {"kind": 1, "page": 98}),
        ]
        evidence = pdf_outline.validate_outline_entries(
            raw,
            side="old",
            source={"sha256": "a" * 64, "page_count": 3, "basename": "x.pdf"},
            page_labels={1: "1", 2: "2", 3: "3"},
        )
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual([entry["title"] for entry in evidence["entries"]], ["Introduction"])
        reasons = {item["reason"] for item in evidence["diagnostics"]}
        self.assertIn("external_destination", reasons)
        self.assertIn("destination_out_of_bounds", reasons)

    def test_all_invalid_destinations_make_status_invalid(self):
        evidence = pdf_outline.validate_outline_entries(
            [(1, "External", -1, {"kind": 0, "page": -1})],
            side="new",
            source={"sha256": "b" * 64, "page_count": 2, "basename": "y.pdf"},
            page_labels={1: "1", 2: "2"},
        )
        self.assertEqual(evidence["status"], "invalid")
        self.assertEqual(evidence["entries"], [])

    def test_nonlocal_positive_page_is_never_reinterpreted_as_a_heading(self):
        evidence = pdf_outline.validate_outline_entries(
            [(1, "Fake local", 2, {"kind": 0, "page": 1})],
            side="old",
            source={"sha256": "a" * 64, "page_count": 3, "basename": "x.pdf"},
            page_labels={1: "1", 2: "2", 3: "3"},
        )
        self.assertEqual(evidence["status"], "invalid")
        self.assertEqual(evidence["entries"], [])
        self.assertEqual(evidence["diagnostics"][0]["reason"],
                         "nonlocal_destination")

    def test_inconsistent_page_and_nonfinite_coordinates_are_rejected(self):
        evidence = pdf_outline.validate_outline_entries(
            [
                (1, "Wrong page", 3, {"kind": 1, "page": 1}),
                (1, "NaN point", 2,
                 {"kind": 1, "page": 1, "to": [float("nan"), 10.0]}),
                (1, "Local heading", 2,
                 {"kind": 1, "page": 1, "to": [72.0, 36.0]}),
            ],
            side="old",
            source={"sha256": "a" * 64, "page_count": 3, "basename": "x.pdf"},
            page_labels={1: "1", 2: "2", 3: "3"},
        )
        reasons = {item["reason"] for item in evidence["diagnostics"]}
        self.assertIn("destination_page_mismatch", reasons)
        self.assertIn("invalid_destination_coordinates", reasons)
        self.assertEqual([entry["title"] for entry in evidence["entries"]],
                         ["Local heading"])
        self.assertEqual(evidence["status"], "partial")

    def test_read_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.object(pdf_outline.fitz, "open", side_effect=RuntimeError("boom")):
                evidence = pdf_outline.collect_outline(
                    Path(directory) / "missing.pdf", side="old", output_dir=output
                )
            sidecar = output / "old_pdf_outline.json"
            saved = json.loads(sidecar.read_text(encoding="utf-8"))

        self.assertEqual(evidence["status"], "read_failure")
        self.assertEqual(saved["status"], "read_failure")
        self.assertTrue(saved["diagnostics"])
        self.assertEqual(saved["entries"], [])


class IdentityTest(unittest.TestCase):
    def test_stale_hash_or_page_count_is_rejected_at_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(Path(directory) / "identity.pdf",
                             toc=[[1, "Introduction", 2]])
            evidence = _evidence_for(path)
            output = Path(directory) / "output"
            pdf_outline.save_outline_sidecar(evidence, output)
            sidecar = output / "old_pdf_outline.json"

            stale_hash = _document([], sha="b" * 64)
            resolved = pdf_outline.resolve_outline_evidence(stale_hash, sidecar)
            self.assertEqual(resolved["status"], "stale")
            self.assertEqual(resolved["entries"], [])

            stale_count = _document([], sha=evidence["source"]["sha256"], page_count=9)
            resolved = pdf_outline.resolve_outline_evidence(stale_count, sidecar)
            self.assertEqual(resolved["status"], "stale")
            self.assertEqual(resolved["entries"], [])

    def test_missing_extraction_identity_never_trusts_a_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(Path(directory) / "legacy.pdf",
                             toc=[[1, "Introduction", 2]])
            output = Path(directory) / "output"
            pdf_outline.save_outline_sidecar(_evidence_for(path), output)
            sidecar = output / "old_pdf_outline.json"
            resolved = pdf_outline.resolve_outline_evidence(_document([], sha=None), sidecar)

        self.assertEqual(resolved["status"], "unavailable")
        self.assertEqual(resolved["entries"], [])
        self.assertIn("extraction_identity", resolved["reason"])

    def test_missing_or_malformed_sidecar_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "old_pdf_outline.json"
            missing = pdf_outline.resolve_outline_evidence(
                _document([]), sidecar
            )
            sidecar.write_text("{not json", encoding="utf-8")
            malformed = pdf_outline.resolve_outline_evidence(
                _document([]), sidecar
            )
        self.assertEqual(missing["status"], "unavailable")
        self.assertEqual(malformed["status"], "unavailable")
        self.assertEqual(malformed["entries"], [])

    def test_partial_roundtrip_preserves_source_order_and_entry_ids(self):
        raw = [
            (1, "External", -1, {"kind": 0, "page": -1}),
            (1, "Valid second", 2, {"kind": 1, "page": 1}),
            (1, "Valid third", 3, {"kind": 1, "page": 2}),
        ]
        source = {"sha256": "a" * 64, "page_count": 3, "basename": "x.pdf"}
        evidence = pdf_outline.validate_outline_entries(
            raw, side="old", source=source,
            page_labels={1: "1", 2: "2", 3: "3"},
        )
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual(
            [entry["entry_id"] for entry in evidence["entries"]],
            ["OLD-OUTLINE-0002", "OLD-OUTLINE-0003"],
        )
        self.assertEqual(
            [entry["source_order"] for entry in evidence["entries"]], [1, 2]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            sidecar = pdf_outline.save_outline_sidecar(evidence, output)
            resolved = pdf_outline.resolve_outline_evidence(
                _document([], sha="a" * 64, page_count=3), sidecar
            )
        self.assertEqual(resolved["status"], "partial")
        self.assertEqual(
            [entry["entry_id"] for entry in resolved["entries"]],
            ["OLD-OUTLINE-0002", "OLD-OUTLINE-0003"],
        )
        self.assertEqual(
            [entry["source_order"] for entry in resolved["entries"]], [1, 2]
        )
        self.assertEqual(
            [entry["title"] for entry in resolved["entries"]],
            ["Valid second", "Valid third"],
        )

    def test_saved_sidecar_roundtrip_is_consumable_only_with_matching_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(Path(directory) / "roundtrip.pdf",
                             toc=[[1, "Introduction", 2]])
            output = Path(directory) / "output"
            evidence = pdf_outline.collect_outline(
                path, side="old", output_dir=output,
                expected_sha256=_sha(path), expected_page_count=3,
            )
            sidecar = output / "old_pdf_outline.json"
            document = _document(
                [structure_fixtures.paragraph("P-00001", "Introduction",
                                              order=0, page=2, role="sectionHeading")],
                sha=evidence["source"]["sha256"], page_count=3,
            )
            resolved = pdf_outline.resolve_outline_evidence(document, sidecar)
        self.assertEqual(resolved["status"], "available")
        self.assertEqual([entry["title"] for entry in resolved["entries"]],
                         ["Introduction"])
        structure = reconstruction.build_structure_evidence(
            document, pdf_outline=resolved
        )
        self.assertEqual(structure["outline"]["matched_count"], 1)

    def test_outline_hash_mismatch_is_recorded_and_never_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _make_pdf(Path(directory) / "mutated.pdf",
                             toc=[[1, "Introduction", 2]])
            evidence = pdf_outline.collect_outline(
                path, side="old", output_dir=Path(directory) / "output",
                expected_sha256="0" * 64, expected_page_count=3,
            )
        self.assertTrue(evidence.get("identity_mismatch"))
        self.assertEqual(evidence["status"], "read_failure")
        self.assertEqual(evidence["entries"], [])

    def test_stage3_carries_the_extraction_identity(self):
        raw = {
            "string_index_type": "utf16CodeUnit",
            "content": "",
            "pages": [{"pageNumber": 1, "width": 1, "height": 1,
                       "unit": "inch", "angle": 0}],
            "paragraphs": [],
            "tables": [],
            "source_binding": {
                "format": "pdf",
                "sha256": "c" * 64,
                "page_count": 1,
                "basename": "source.pdf",
            },
        }
        model = s03_internal_schema.build_internal_document(
            raw, None, document_id="DOC-OLD", prefix="OLD"
        )
        source = model["document"]["source"]
        self.assertEqual(source["extraction_source"]["sha256"], "c" * 64)
        self.assertEqual(source["extraction_source"]["page_count"], 1)
        # Legacy/DI-only raw input has no invented identity.
        raw.pop("source_binding")
        legacy = s03_internal_schema.build_internal_document(
            raw, None, document_id="DOC-OLD", prefix="OLD"
        )
        self.assertNotIn("extraction_source", legacy["document"]["source"])


class StageOnePrepassTest(unittest.TestCase):
    def test_outline_is_collected_before_reusing_cached_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir(parents=True)
            pdf_path = _make_pdf(root / "source.pdf", pages=2,
                                 toc=[[1, "Introduction", 2]])
            sha = _sha(pdf_path)
            chunk_dir = root / "cache" / "di_chunks" / sha
            chunk_dir.mkdir(parents=True)
            chunk = {
                "string_index_type": "utf16CodeUnit",
                "content": "Synthetic page 1Synthetic page 2",
                "pages": [
                    {"pageNumber": 1, "width": 612, "height": 792,
                     "unit": "inch", "angle": 0},
                    {"pageNumber": 2, "width": 612, "height": 792,
                     "unit": "inch", "angle": 0},
                ],
                "paragraphs": [],
                "tables": [],
                "styles": [],
            }
            (chunk_dir / "chunk_001_pages_0001_0002.json").write_text(
                json.dumps(chunk), encoding="utf-8"
            )
            doc = {"name": "old", "label": "old",
                   "final_output": out / "old_di_raw.json"}

            with patch.object(s01_extract, "PROJECT_ROOT", root), \
                    patch.object(s01_extract, "configured_pdf_path",
                                 return_value=pdf_path), \
                    patch.object(s01_extract, "extract_pdf_range_with_retry") as extract:
                merged = s01_extract.extract_merge_or_load_document_from_pdf(doc)

            extract.assert_not_called()
            sidecar = out / "old_pdf_outline.json"
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "available")
            self.assertEqual(saved["entries"][0]["title"], "Introduction")
            self.assertEqual(merged["source_binding"]["sha256"], sha)

    def test_debug_page_subset_keeps_full_source_outline_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir(parents=True)
            pdf_path = _make_pdf(root / "source.pdf", pages=3,
                                 toc=[[1, "Introduction", 2]])
            sha = _sha(pdf_path)
            chunk_dir = root / "cache" / "di_chunks" / sha
            chunk_dir.mkdir(parents=True)
            chunk = {
                "string_index_type": "utf16CodeUnit",
                "content": "Synthetic page 1Synthetic page 2",
                "pages": [
                    {"pageNumber": 1, "width": 612, "height": 792,
                     "unit": "inch", "angle": 0},
                    {"pageNumber": 2, "width": 612, "height": 792,
                     "unit": "inch", "angle": 0},
                ],
                "paragraphs": [],
                "tables": [],
                "styles": [],
            }
            (chunk_dir / "chunk_001_pages_0001_0002.json").write_text(
                json.dumps(chunk), encoding="utf-8"
            )
            doc = {"name": "old", "label": "old",
                   "final_output": out / "old_di_raw.json"}

            with patch.object(s01_extract, "PROJECT_ROOT", root), \
                    patch.object(s01_extract, "configured_pdf_path",
                                 return_value=pdf_path), \
                    patch.object(s01_extract, "debug_page_range", return_value=2), \
                    patch.object(s01_extract, "extract_pdf_range_with_retry") as extract:
                merged = s01_extract.extract_merge_or_load_document_from_pdf(doc)

            extract.assert_not_called()
            saved = json.loads(
                (out / "old_pdf_outline.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["status"], "available")
            self.assertEqual(saved["source"]["page_count"], 3)
            self.assertEqual(merged["source_binding"]["page_count"], 3)
            self.assertEqual(len(merged["pages"]), 2)

    def test_source_identity_mismatch_fails_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir(parents=True)
            pdf_path = _make_pdf(root / "source.pdf", pages=1)
            doc = {"name": "old", "label": "old",
                   "final_output": out / "old_di_raw.json"}
            with patch.object(s01_extract, "PROJECT_ROOT", root), \
                    patch.object(s01_extract, "configured_pdf_path",
                                 return_value=pdf_path), \
                    patch.object(s01_extract, "collect_outline",
                                 return_value={"identity_mismatch": True}), \
                    patch.object(s01_extract, "extract_pdf_range_with_retry") as extract:
                with self.assertRaises(RuntimeError) as caught:
                    s01_extract.extract_merge_or_load_document_from_pdf(doc)
            extract.assert_not_called()
            self.assertIn("outline", str(caught.exception))
            self.assertIn("changed", str(caught.exception))

    def test_outline_read_failure_does_not_abort_valid_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir(parents=True)
            pdf_path = _make_pdf(root / "source.pdf", pages=1)
            doc = {"name": "old", "label": "old",
                   "final_output": out / "old_di_raw.json"}

            def fake_extract(pdf_path, *, pages, max_attempts=3,
                             initial_delay_seconds=2.0):
                return {
                    "pages": [{"pageNumber": 1}],
                    "content": "a",
                    "string_index_type": "textElements",
                }

            with patch.object(s01_extract, "PROJECT_ROOT", root), \
                    patch.object(s01_extract, "configured_pdf_path",
                                 return_value=pdf_path), \
                    patch.object(s01_extract, "pdf_page_count", return_value=1), \
                    patch.object(pdf_outline.fitz, "open",
                                 side_effect=RuntimeError("outline read failure")), \
                    patch.object(s01_extract, "extract_pdf_range_with_retry",
                                 side_effect=fake_extract) as extract:
                merged = s01_extract.extract_merge_or_load_document_from_pdf(doc)

            extract.assert_called_once()
            self.assertEqual(merged["source_binding"]["sha256"], _sha(pdf_path))
            saved = json.loads(
                (out / "old_pdf_outline.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["status"], "read_failure")
            self.assertEqual(saved["entries"], [])


class StageFiveOutlineTest(unittest.TestCase):
    def _outline_evidence(self, entries):
        return {
            "schema_version": pdf_outline.OUTLINE_SIDECAR_VERSION,
            "side": "old",
            "status": "available",
            "source": {"sha256": "a" * 64, "page_count": 3, "basename": "source.pdf"},
            "entries": entries,
            "diagnostics": [],
            "limitations": [],
        }

    def _document(self):
        elements = [
            structure_fixtures.paragraph("P-00001", "Preface.", order=0),
            structure_fixtures.paragraph("P-00002", "Introduction", order=1,
                                         page=2, role="sectionHeading"),
            structure_fixtures.paragraph("P-00003", "Intro body.", order=2, page=2),
            structure_fixtures.paragraph("P-00004", "Methods", order=3,
                                         page=3, role="sectionHeading"),
            structure_fixtures.paragraph("P-00005", "Methods body.", order=4, page=3),
        ]
        return _document(elements)

    def test_outline_entries_link_and_support_provisional_context(self):
        document = self._document()
        evidence = self._outline_evidence([
            {"entry_id": "OUTLINE-0001", "source_order": 0, "level": 1,
             "title": "Introduction", "physical_page": 2, "page_label": "2",
             "validated": True},
            {"entry_id": "OUTLINE-0002", "source_order": 1, "level": 1,
             "title": "Methods", "physical_page": 3, "page_label": "3",
             "validated": True},
            {"entry_id": "OUTLINE-0003", "source_order": 2, "level": 2,
             "title": "Unmatched appendix", "physical_page": 3, "page_label": "3",
             "validated": True},
        ])
        structured = s05_anchors.reconstruct_provisional_context(
            _structured_model(document), pdf_outline=evidence
        )
        result = structured["document"]
        outline = result["structure_evidence"]["outline"]
        self.assertEqual(outline["status"], "available")
        self.assertEqual(outline["entry_count"], 3)
        self.assertEqual(outline["matched_count"], 2)
        self.assertEqual(
            [entry["body_element_id"] for entry in outline["entries"]],
            ["P-00002", "P-00004", None],
        )
        # Supported outline links are preferred structural evidence.
        sections = {section["heading_element_id"]: section
                    for section in result["structure_evidence"]["sections"]}
        self.assertEqual(sections["P-00002"]["basis"], "pdf_outline_link")
        self.assertEqual(sections["P-00004"]["basis"], "pdf_outline_link")
        # Unsupported entries stay visible but never become an arbitrary heading.
        unmatched = outline["entries"][2]
        self.assertEqual(unmatched["status"], "unmatched")
        self.assertIsNone(unmatched["body_element_id"])
        # Supported headings become provisional anchors/context support.
        anchors = {anchor["element_id"]: anchor
                   for anchor in result["provisional_anchors"]}
        self.assertEqual(anchors["P-00002"]["anchor_type"], "pdf_outline_heading")
        self.assertEqual(anchors["P-00004"]["anchor_type"], "pdf_outline_heading")

    def test_outline_never_promotes_derived_furniture(self):
        elements = [
            structure_fixtures.paragraph("H1", "Running title", order=0, page=1,
                                         role="pageHeader", element_type="header"),
            structure_fixtures.paragraph("H2", "Running title", order=1, page=2,
                                         role="pageHeader", element_type="header"),
            structure_fixtures.paragraph("P-00002", "Introduction", order=2,
                                         page=3, role="sectionHeading"),
        ]
        document = _document(elements)
        evidence = self._outline_evidence([
            {"entry_id": "OUTLINE-0001", "source_order": 0, "level": 1,
             "title": "Running title", "physical_page": 1, "page_label": "1",
             "validated": True},
        ])
        structured = s05_anchors.reconstruct_provisional_context(
            _structured_model(document), pdf_outline=evidence
        )
        result = structured["document"]
        self.assertEqual(result["structure_evidence"]["outline"]["matched_count"], 0)
        self.assertNotIn("H1", {section["heading_element_id"]
                                for section in result["structure_evidence"]["sections"]})
        membership = result["structure_evidence"]["membership"]["by_element"]
        self.assertEqual(membership["H1"]["category"], "layout")

    def test_no_outline_preserves_existing_fallback(self):
        document = self._document()
        baseline = reconstruction.build_structure_evidence(document)
        with_outline_block = reconstruction.build_structure_evidence(
            document, pdf_outline=None
        )
        self.assertEqual(baseline, with_outline_block)
        self.assertEqual(baseline["toc"]["entry_count"], 0)
        self.assertNotIn("outline", baseline)

    def test_stale_or_absent_outline_has_no_outline_block(self):
        document = self._document()
        for status in ("unavailable", "stale", "absent", "invalid", "read_failure"):
            with self.subTest(status=status):
                evidence = {
                    "schema_version": pdf_outline.OUTLINE_SIDECAR_VERSION,
                    "side": "old",
                    "status": status,
                    "source": None,
                    "entries": [],
                    "diagnostics": [],
                    "limitations": [],
                }
                built = reconstruction.build_structure_evidence(
                    document, pdf_outline=evidence
                )
                self.assertNotIn("outline", built)

    def test_legacy_structure_evidence_version_remains_readable(self):
        document = self._document()
        evidence = reconstruction.build_structure_evidence(document)
        legacy = json.loads(json.dumps(evidence))
        legacy["version"] = "1.1.2"
        legacy.pop("outline", None)
        for section in legacy["sections"]:
            section.pop("outline_entry_id", None)
        # A valid legacy snapshot must not be rejected merely because the
        # current producer version moved on.
        reconstruction.validate_structure_evidence(document, legacy)


if __name__ == "__main__":
    unittest.main()
