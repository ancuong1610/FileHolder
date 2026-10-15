"""Chunked DI merge element-reference rebasing (defect DI-REF-01).

``merge_document_intelligence_chunks`` shifts page numbers and span offsets and
then extends the merged collections.  Document Intelligence returns each
chunk's structured references (the ``elements`` JSON-pointer lists inside
tables/cells, figures, sections, key-value pairs and documents) relative to
that chunk's own result, so chunk 2's ``/paragraphs/3`` means *its* paragraph 3,
not the merged paragraph 3 from chunk 1.  Before this fix the references were
copied verbatim, silently re-pointing later chunks at earlier chunks' elements.

These tests reproduce the defect with synthetic two-chunk inputs, prove the
references are rebased per target collection with JSON-pointer suffixes
preserved, prove raw ``content``/text strings that merely look like pointers are
never rewritten, and prove malformed/unsupported/missing targets fail loudly.
They also pin the cached-merged reuse decision: a cache with references but
missing/legacy rebasing metadata must be remerged locally from the saved chunks
(no provider request), while a reference-free or metadata-tagged cache may be
reused.  All inputs are deep-copied and the committed ``output_sample``
artifacts stay read-only.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from crr_compare.stages import s01_extract

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_OLD = ROOT / "output_sample" / "old_di_raw.json"
_MISSING_SAMPLES = ["old_di_raw.json"] if not SAMPLE_OLD.is_file() else []


def _page(number):
    return {"pageNumber": number, "spans": []}


def _paragraph(text, *, spans=None):
    return {"content": text, "spans": list(spans or [])}


def _cell(elements):
    return {"rowIndex": 0, "columnIndex": 0, "elements": list(elements)}


def _chunk(
    number,
    start,
    end,
    *,
    content,
    paragraphs,
    tables=None,
    figures=None,
    sections=None,
    styles=None,
    key_value_pairs=None,
    documents=None,
    string_index_type="textElements",
):
    result = {
        "string_index_type": string_index_type,
        "content": content,
        "pages": [_page(start + i) for i in range(end - start + 1)],
        "paragraphs": paragraphs,
    }
    for name, value in (
        ("tables", tables),
        ("figures", figures),
        ("sections", sections),
        ("styles", styles),
        ("key_value_pairs", key_value_pairs),
        ("documents", documents),
    ):
        if value is not None:
            result[name] = value
    return {
        "chunk_number": number,
        "start_page": start,
        "end_page": end,
        "page_range": f"{start}-{end}",
        "path": f"chunk_{number:03d}",
        "result": result,
    }


def _elements(document, path):
    node = document
    for part in path:
        node = node[part]
    return node["elements"]


class TwoChunkReferenceRebasingTest(unittest.TestCase):
    def _merged(self):
        chunk0 = _chunk(
            1,
            1,
            1,
            content="a0",
            paragraphs=[_paragraph("a0")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
            figures=[{"elements": ["/paragraphs/0"]}],
            sections=[
                {"elements": ["/paragraphs/0", "/tables/0", "/figures/0", "/sections/0"]}
            ],
        )
        chunk1 = _chunk(
            2,
            2,
            2,
            content="b0",
            paragraphs=[_paragraph("b0")],
            tables=[{"cells": [_cell(["/paragraphs/0", "/tables/0/cells/0"])]}],
            figures=[{"elements": ["/paragraphs/0"]}],
            sections=[
                {"elements": ["/paragraphs/0", "/tables/0", "/figures/0", "/sections/0"]}
            ],
        )
        return s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])

    def test_chunk_one_references_are_rebased_into_each_collection(self):
        merged = self._merged()
        # chunk 1 keeps its own (zero-offset) local references unchanged.
        self.assertEqual(_elements(merged, ["tables", 0, "cells", 0]), ["/paragraphs/0"])
        self.assertEqual(_elements(merged, ["figures", 0]), ["/paragraphs/0"])
        self.assertEqual(
            _elements(merged, ["sections", 0]),
            ["/paragraphs/0", "/tables/0", "/figures/0", "/sections/0"],
        )
        # chunk 2 local index 0 moves behind chunk 1 per target collection.
        self.assertEqual(
            _elements(merged, ["tables", 1, "cells", 0]),
            ["/paragraphs/1", "/tables/1/cells/0"],
        )
        self.assertEqual(_elements(merged, ["figures", 1]), ["/paragraphs/1"])
        self.assertEqual(
            _elements(merged, ["sections", 1]),
            ["/paragraphs/1", "/tables/1", "/figures/1", "/sections/1"],
        )

    def test_json_pointer_suffix_is_preserved(self):
        merged = self._merged()
        self.assertEqual(
            _elements(merged, ["tables", 1, "cells", 0]),
            ["/paragraphs/1", "/tables/1/cells/0"],
        )

    def test_merged_metadata_marks_reference_rebasing(self):
        metadata = self._merged()["chunked_extraction"]
        self.assertTrue(metadata["span_offsets_rebased"])
        self.assertTrue(metadata["reference_rebasing"])
        self.assertEqual(
            metadata["reference_rebasing_version"],
            s01_extract.REFERENCE_REBASING_VERSION,
        )

    def test_merged_references_resolve_globally(self):
        merged = self._merged()
        s01_extract.validate_merged_extraction(
            merged, expected_pages=2, document_name="synthetic"
        )

    def test_duplicate_references_are_rebased_without_deduplication(self):
        chunk0 = _chunk(
            1, 1, 1, content="a", paragraphs=[_paragraph("a")],
            sections=[{"elements": []}],
        )
        chunk1 = _chunk(
            2, 2, 2, content="b", paragraphs=[_paragraph("b")],
            sections=[{"elements": ["/paragraphs/0", "/paragraphs/0"]}],
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])
        self.assertEqual(
            _elements(merged, ["sections", 1]), ["/paragraphs/1", "/paragraphs/1"]
        )

    def test_input_chunks_are_not_mutated(self):
        chunk0 = _chunk(
            1, 1, 1, content="a0", paragraphs=[_paragraph("a0")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
            sections=[{"elements": ["/paragraphs/0", "/tables/0"]}],
        )
        chunk1 = _chunk(
            2, 2, 2, content="b0", paragraphs=[_paragraph("b0")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
            sections=[{"elements": ["/paragraphs/0", "/tables/0"]}],
        )
        chunks = [chunk0, chunk1]
        snapshot = deepcopy(chunks)
        s01_extract.merge_document_intelligence_chunks(chunks)
        self.assertEqual(chunks, snapshot)


class IndependentCumulativeLengthsTest(unittest.TestCase):
    def test_each_collection_uses_its_own_cumulative_length(self):
        chunk0 = _chunk(
            1, 1, 1,
            content="a", paragraphs=[_paragraph("a"), _paragraph("b")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
            sections=[{"elements": []}, {"elements": []}],
        )
        chunk1 = _chunk(
            2, 2, 2,
            content="c", paragraphs=[_paragraph("c")],
            tables=[
                {"cells": [_cell(["/paragraphs/0"])]},
                {"cells": [_cell(["/paragraphs/0", "/tables/0"])]},
            ],
            figures=[{"elements": ["/paragraphs/0"]}],
            sections=[{"elements": ["/paragraphs/0", "/tables/0", "/sections/0"]}],
        )
        chunk2 = _chunk(
            3, 3, 3,
            content="d", paragraphs=[_paragraph("d")],
            sections=[{"elements": ["/paragraphs/0", "/sections/0"]}],
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1, chunk2])

        # chunk1: 2 paragraphs and 1 table already merged before it.
        self.assertEqual(_elements(merged, ["tables", 1, "cells", 0]), ["/paragraphs/2"])
        self.assertEqual(
            _elements(merged, ["tables", 2, "cells", 0]), ["/paragraphs/2", "/tables/1"]
        )
        self.assertEqual(_elements(merged, ["figures", 0]), ["/paragraphs/2"])
        self.assertEqual(
            _elements(merged, ["sections", 2]),
            ["/paragraphs/2", "/tables/1", "/sections/2"],
        )
        # chunk2: 3 paragraphs and 3 sections already merged before it.
        self.assertEqual(
            _elements(merged, ["sections", 3]), ["/paragraphs/3", "/sections/3"]
        )
        s01_extract.validate_merged_extraction(
            merged, expected_pages=3, document_name="synthetic"
        )


class RawTextPointersAreNotRewrittenTest(unittest.TestCase):
    def test_content_and_text_strings_that_look_like_pointers_are_untouched(self):
        chunk0 = _chunk(
            1, 1, 1, content="x", paragraphs=[_paragraph("x")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
        )
        chunk1 = _chunk(
            2, 2, 2,
            content="/paragraphs/0",
            paragraphs=[_paragraph("/paragraphs/0")],
            # A cell whose *content* is a pointer-looking string and whose
            # structured elements are a real reference: only the elements list
            # may be rebased.
            tables=[
                {
                    "cells": [
                        {
                            "rowIndex": 0,
                            "columnIndex": 0,
                            "content": "/paragraphs/0",
                            "elements": ["/paragraphs/0"],
                        }
                    ]
                }
            ],
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])
        self.assertEqual(merged["content"].split("\n")[1], "/paragraphs/0")
        self.assertEqual(merged["paragraphs"][1]["content"], "/paragraphs/0")
        self.assertEqual(merged["tables"][1]["cells"][0]["content"], "/paragraphs/0")
        self.assertEqual(merged["tables"][1]["cells"][0]["elements"], ["/paragraphs/1"])


class SpanAndPageBehaviourRetainedTest(unittest.TestCase):
    def test_page_numbers_content_separator_and_spans_are_still_rebased(self):
        chunk0 = _chunk(
            1, 1, 1, content="ab",
            paragraphs=[_paragraph("ab", spans=[{"offset": 0, "length": 2}])],
        )
        # local page numbering restarts at 1 inside the second chunk.
        chunk1 = _chunk(
            2, 2, 2, content="cd",
            paragraphs=[_paragraph("cd", spans=[{"offset": 0, "length": 2}])],
        )
        chunk1["result"]["pages"] = [{"pageNumber": 1, "spans": []}]
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])
        self.assertEqual(merged["content"], "ab\ncd")
        self.assertEqual(
            [page["pageNumber"] for page in merged["pages"]], [1, 2]
        )
        self.assertEqual(merged["paragraphs"][1]["spans"][0]["offset"], 3)
        s01_extract.validate_merged_extraction(
            merged, expected_pages=2, document_name="synthetic"
        )

    def test_unicode_index_type_lengths_are_preserved(self):
        chunk0 = _chunk(
            1, 1, 1, content="ä",
            paragraphs=[_paragraph("ä", spans=[{"offset": 0, "length": 1}])],
            string_index_type="unicodeCodePoint",
        )
        chunk1 = _chunk(
            2, 2, 2, content="ö",
            paragraphs=[_paragraph("ö", spans=[{"offset": 0, "length": 1}])],
            string_index_type="unicodeCodePoint",
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])
        self.assertEqual(merged["chunked_extraction"]["string_index_type"], "unicodeCodePoint")
        self.assertEqual(merged["paragraphs"][1]["spans"][0]["offset"], 2)


class SingleChunkTest(unittest.TestCase):
    def test_single_chunk_references_are_byte_identical(self):
        chunk0 = _chunk(
            1, 1, 1, content="a", paragraphs=[_paragraph("a")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
            figures=[{"elements": ["/paragraphs/0"]}],
            sections=[{"elements": ["/paragraphs/0", "/tables/0", "/figures/0", "/sections/0"]}],
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0])
        self.assertEqual(_elements(merged, ["tables", 0, "cells", 0]), ["/paragraphs/0"])
        self.assertEqual(_elements(merged, ["figures", 0]), ["/paragraphs/0"])
        self.assertEqual(
            _elements(merged, ["sections", 0]),
            ["/paragraphs/0", "/tables/0", "/figures/0", "/sections/0"],
        )
        self.assertTrue(merged["chunked_extraction"]["reference_rebasing"])


class InvalidReferenceDiagnosticsTest(unittest.TestCase):
    def _merge_with_second_chunk_section_elements(self, elements):
        chunk0 = _chunk(1, 1, 1, content="a", paragraphs=[_paragraph("a")])
        chunk1 = _chunk(
            2, 2, 2, content="b", paragraphs=[_paragraph("b")],
            sections=[{"elements": elements}],
        )
        return s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])

    def test_missing_target_fails_with_path_and_pointer(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge_with_second_chunk_section_elements(["/paragraphs/5"])
        message = str(ctx.exception)
        self.assertIn("/paragraphs/5", message)
        self.assertIn("sections", message)  # JSON path of the offending list
        self.assertIn("paragraphs has 1", message)

    def test_invalid_index_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge_with_second_chunk_section_elements(["/tables/2"])
        self.assertIn("/tables/2", str(ctx.exception))

    def test_malformed_pointer_fails(self):
        for bad in ("paragraphs/0", "/paragraphs/", "/paragraphs/x", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError) as ctx:
                    self._merge_with_second_chunk_section_elements([bad])
                self.assertIn(repr(bad), str(ctx.exception))

    def test_unsupported_target_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge_with_second_chunk_section_elements(["/unknown/0"])
        message = str(ctx.exception)
        self.assertIn("/unknown/0", message)
        self.assertIn("unsupported", message.lower())

    def test_non_string_reference_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge_with_second_chunk_section_elements([0])
        self.assertIn("expected a JSON-pointer string", str(ctx.exception))


class SuffixResolutionTest(unittest.TestCase):
    """The *complete* JSON pointer must resolve, not just ``/collection/index``.

    A reference like ``/tables/0/cells/999`` or ``/tables/0/noSuchField`` has a
    valid base index but a suffix that does not exist.  The suffix must be
    resolved against the target item locally before rebasing and against the
    merged item afterwards, honouring RFC 6901 ``~0``/``~1`` escaping, without
    rewriting the suffix.
    """

    def _merge(self, *, chunk0_tables=None, chunk1_tables=None, chunk1_sections=None):
        chunk0 = _chunk(
            1, 1, 1, content="a", paragraphs=[_paragraph("a")],
            tables=chunk0_tables,
        )
        chunk1 = _chunk(
            2, 2, 2, content="b", paragraphs=[_paragraph("b")],
            tables=chunk1_tables, sections=chunk1_sections,
        )
        return s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])

    def test_invalid_suffix_property_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(chunk1_tables=[{"cells": [_cell(["/tables/0/noSuchField"])]}])
        message = str(ctx.exception)
        self.assertIn("/tables/0/noSuchField", message)
        self.assertIn("noSuchField", message)

    def test_invalid_suffix_list_index_out_of_range_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(chunk1_tables=[{"cells": [_cell(["/tables/0/cells/999"])]}])
        self.assertIn("999", str(ctx.exception))

    def test_invalid_suffix_non_index_token_on_list_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(chunk1_tables=[{"cells": [_cell(["/tables/0/cells/foo"])]}])
        self.assertIn("foo", str(ctx.exception))

    def test_invalid_pointer_escape_fails(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(
                chunk1_tables=[{"cells": [], "meta~2key": {}}],
                chunk1_sections=[{"elements": ["/tables/0/meta~2key"]}],
            )
        self.assertIn("~2key", str(ctx.exception))

    def test_nested_valid_suffix_is_rebased_and_resolves(self):
        merged = self._merge(
            chunk0_tables=[{"cells": [_cell([])]}],
            chunk1_tables=[{"cells": [_cell([])]}, {"cells": [_cell([])]}],
            chunk1_sections=[{"elements": ["/tables/1/cells/0"]}],
        )
        # base index rebased, suffix untouched.
        self.assertEqual(_elements(merged, ["sections", 0]), ["/tables/2/cells/0"])
        s01_extract.validate_merged_extraction(
            merged, expected_pages=2, document_name="synthetic"
        )

    def test_escaped_property_names_resolve_and_are_not_rewritten(self):
        merged = self._merge(
            chunk0_tables=[{"cells": [_cell([])]}],
            chunk1_tables=[{"cells": [_cell([])], "meta/key": {"deep~name": True}}],
            chunk1_sections=[{"elements": ["/tables/0/meta~1key/deep~0name"]}],
        )
        # base index rebased to 1; escaped suffix preserved byte-for-byte.
        self.assertEqual(
            _elements(merged, ["sections", 0]), ["/tables/1/meta~1key/deep~0name"]
        )
        s01_extract.validate_merged_extraction(
            merged, expected_pages=2, document_name="synthetic"
        )

    def test_merged_side_suffix_validation(self):
        merged = self._merge(chunk1_tables=[{"cells": [_cell([])]}])
        merged["sections"] = [{"elements": ["/tables/0/noSuchField"]}]
        with self.assertRaises(AssertionError) as ctx:
            s01_extract.validate_merged_extraction(
                merged, expected_pages=2, document_name="synthetic"
            )
        self.assertIn("noSuchField", str(ctx.exception))

    def test_wrong_type_elements_string_is_diagnosed(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(chunk1_sections=[{"elements": "not-a-list"}])
        message = str(ctx.exception)
        self.assertIn("elements", message)
        self.assertIn("list", message)

    def test_wrong_type_elements_dict_is_diagnosed(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._merge(chunk1_sections=[{"elements": {"0": "/paragraphs/0"}}])
        self.assertIn("elements", str(ctx.exception))

    def test_literal_content_strings_are_still_preserved(self):
        chunk0 = _chunk(1, 1, 1, content="/tables/0", paragraphs=[_paragraph("/tables/0")])
        chunk1 = _chunk(
            2, 2, 2, content="b", paragraphs=[_paragraph("b")],
            tables=[{"cells": [_cell(["/tables/0/cells/0"])]}],
        )
        merged = s01_extract.merge_document_intelligence_chunks([chunk0, chunk1])
        self.assertEqual(merged["paragraphs"][0]["content"], "/tables/0")
        self.assertEqual(_elements(merged, ["tables", 0, "cells", 0]), ["/tables/0/cells/0"])


class CachedMergeCompatibilityTest(unittest.TestCase):
    """The cached final merge must not be reused when its references are stale."""

    def _write_chunks(self, cache: Path):
        chunk0 = _chunk(
            1, 1, 1, content="a0", paragraphs=[_paragraph("a0")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
        )
        chunk1 = _chunk(
            2, 2, 2, content="b0", paragraphs=[_paragraph("b0")],
            tables=[{"cells": [_cell(["/paragraphs/0"])]}],
        )
        for number, chunk in ((1, chunk0), (2, chunk1)):
            start = chunk["start_page"]
            end = chunk["end_page"]
            path = cache / f"chunk_{number:03d}_pages_{start:04d}_{end:04d}.json"
            path.write_text(json.dumps(chunk["result"]), encoding="utf-8")

    def _doc(self, root: Path):
        return {
            "name": "old_doc",
            "label": "old",
            "final_output": root / "output" / "old_di_raw.json",
        }

    def _patch_extraction_never_runs(self):
        stack = mock.patch.object(
            s01_extract, "extract_pdf_range_with_retry",
            side_effect=AssertionError("extraction must not run"),
        )
        stack.start()
        self.addCleanup(stack.stop)
        stack2 = mock.patch.object(
            s01_extract, "ensure_client",
            side_effect=AssertionError("provider client must not be created"),
        )
        stack2.start()
        self.addCleanup(stack2.stop)

    def test_legacy_cached_final_is_remerged_from_saved_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache" / "di_chunks" / "old_doc"
            cache.mkdir(parents=True)
            self._write_chunks(cache)
            doc = self._doc(root)
            doc["final_output"].parent.mkdir(parents=True)
            # A stale cache: chunk-2 element references were copied verbatim and
            # it carries no reference-rebasing metadata.
            doc["final_output"].write_text(
                json.dumps(
                    {
                        "string_index_type": "textElements",
                        "content": "a0\nb0",
                        "pages": [{"pageNumber": 1, "spans": []}, {"pageNumber": 2, "spans": []}],
                        "paragraphs": [_paragraph("a0"), _paragraph("b0")],
                        "tables": [
                            {"cells": [_cell(["/paragraphs/0"])]},
                            {"cells": [_cell(["/paragraphs/0"])]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self._patch_extraction_never_runs()
            with mock.patch.object(s01_extract, "chunk_dir", return_value=cache), \
                    mock.patch.object(s01_extract, "FORCE_MERGE", False):
                merged = s01_extract.extract_merge_or_load_document(doc)

            self.assertEqual(_elements(merged, ["tables", 1, "cells", 0]), ["/paragraphs/1"])
            self.assertTrue(merged["chunked_extraction"]["reference_rebasing"])
            on_disk = json.loads(doc["final_output"].read_text(encoding="utf-8"))
            self.assertEqual(_elements(on_disk, ["tables", 1, "cells", 0]), ["/paragraphs/1"])

    def test_compatible_cached_final_is_reused_without_remerging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache" / "di_chunks" / "old_doc"
            cache.mkdir(parents=True)
            self._write_chunks(cache)
            doc = self._doc(root)
            doc["final_output"].parent.mkdir(parents=True)
            compatible = {
                "string_index_type": "textElements",
                "content": "a0\nb0",
                "pages": [{"pageNumber": 1, "spans": []}, {"pageNumber": 2, "spans": []}],
                "paragraphs": [_paragraph("a0"), _paragraph("b0")],
                "tables": [
                    {"cells": [_cell(["/paragraphs/0"])]},
                    {"cells": [_cell(["/paragraphs/1"])]},
                ],
                "chunked_extraction": {
                    "enabled": True,
                    "chunk_count": 2,
                    "span_offsets_rebased": True,
                    "reference_rebasing": True,
                    "reference_rebasing_version": s01_extract.REFERENCE_REBASING_VERSION,
                },
            }
            doc["final_output"].write_text(json.dumps(compatible), encoding="utf-8")
            self._patch_extraction_never_runs()
            with mock.patch.object(s01_extract, "chunk_dir", return_value=cache), \
                    mock.patch.object(s01_extract, "FORCE_MERGE", False), \
                    mock.patch.object(
                        s01_extract, "merge_document_intelligence_chunks",
                        side_effect=AssertionError("merge must not run for a compatible cache"),
                    ):
                merged = s01_extract.extract_merge_or_load_document(doc)
            self.assertEqual(merged["content"], "a0\nb0")

    def test_reference_free_cached_final_stays_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache" / "di_chunks" / "old_doc"
            cache.mkdir(parents=True)
            self._write_chunks(cache)
            doc = self._doc(root)
            doc["final_output"].parent.mkdir(parents=True)
            legacy = {
                "string_index_type": "textElements",
                "content": "a0\nb0",
                "pages": [{"pageNumber": 1, "spans": []}, {"pageNumber": 2, "spans": []}],
                "paragraphs": [_paragraph("a0"), _paragraph("b0")],
                "tables": [{"cells": [_cell([])]}, {"cells": [_cell([])]}],
            }
            doc["final_output"].write_text(json.dumps(legacy), encoding="utf-8")
            self._patch_extraction_never_runs()
            with mock.patch.object(s01_extract, "chunk_dir", return_value=cache), \
                    mock.patch.object(s01_extract, "FORCE_MERGE", False), \
                    mock.patch.object(
                        s01_extract, "merge_document_intelligence_chunks",
                        side_effect=AssertionError("reference-free cache must be reused"),
                    ):
                merged = s01_extract.extract_merge_or_load_document(doc)
            self.assertEqual(merged["content"], "a0\nb0")

    def test_reuse_blocker_distinguishes_metadata_from_invalid_pointers(self):
        # Legacy metadata blocks reuse...
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "di.json"
            path.write_text(
                json.dumps(
                    {
                        "string_index_type": "textElements",
                        "paragraphs": [_paragraph("a")],
                        "sections": [{"elements": ["/paragraphs/0"]}],
                    }
                ),
                encoding="utf-8",
            )
            stale = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNotNone(s01_extract.cached_merge_reuse_blocker(stale))
        # ... but an out-of-range pointer is an invalid artifact, not a legacy cache.
        invalid = deepcopy(stale)
        invalid["sections"][0]["elements"] = ["/paragraphs/9"]
        with self.assertRaises(AssertionError) as ctx:
            s01_extract.validate_merged_extraction(
                invalid, expected_pages=0, document_name="invalid"
            )
        self.assertIn("/paragraphs/9", str(ctx.exception))

    @unittest.skipUnless(
        not _MISSING_SAMPLES,
        "private output_sample fixtures are unavailable in this checkout: "
        + ", ".join(_MISSING_SAMPLES),
    )
    def test_committed_sample_cache_is_not_reusable_and_not_modified(self):
        before = SAMPLE_OLD.read_bytes()
        merged = json.loads(before)
        self.assertIsNotNone(s01_extract.cached_merge_reuse_blocker(merged))
        self.assertEqual(SAMPLE_OLD.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
