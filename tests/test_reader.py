"""Unit/contract tests for the reusable read projection (crr_compare.result_reader).

Truth is authored here before any prediction code runs.
"""
import json
import unittest

from crr_compare import result_reader as rr


def _resolution(status="accepted", needs_review=False, reasons=None):
    return {
        "route": "ACCEPT_DETERMINISTIC",
        "case_type": "modified",
        "method": "deterministic",
        "status": status,
        "needs_review": needs_review,
        "review_reasons": reasons or [],
    }


def _change(index, ctype, old_text, new_text, status="accepted", needs_review=False):
    return {
        "change_id": f"CHANGE-{index:06d}",
        "source_candidate_id": f"C{index}",
        "source_group": "modified",
        "change_type": ctype,
        "old": {
            "element_id": f"OLD-{index}",
            "element_type": "PARA",
            "text": old_text,
            "normalized_text": old_text.lower(),
            "comparison_text": old_text.lower(),
            "primary_page": 1,
            "reading_order": index,
            "provisional_anchor_context": [],
        },
        "new": {
            "element_id": f"NEW-{index}",
            "element_type": "PARA",
            "text": new_text,
            "normalized_text": new_text.lower(),
            "comparison_text": new_text.lower(),
            "primary_page": 2,
            "reading_order": index,
            "provisional_anchor_context": [],
        },
        "token_diff": {"source_token_diff": {"counts": {"replace": 1}}},
        "similarity": 0.5,
        "resolution": _resolution(status, needs_review),
        "provenance": {"step9_candidate_id": f"C{index}", "matching_not_recomputed": True},
    }


def _cell(cid, text, row, col):
    return {
        "cell_id": cid,
        "source_table_id": "OLD-T1",
        "source_row_index": row,
        "row_index": row,
        "column_index": col,
        "row_span": 1,
        "column_span": 1,
        "kind": "content",
        "text": text,
        "normalized_text": text.lower(),
        "primary_page": 3,
        "primary_polygon": None,
        "spans": [],
    }


def sample_canonical():
    changes = [
        _change(1, "MODIFIED", "Fee is 21 days", "Fee is 99 days"),
        _change(2, "DELETED", "Obsolete clause text", "", status="review", needs_review=True),
    ]
    changes[1]["resolution"]["review_reasons"] = ["semantic_signal"]
    table = {
        "table_change_id": "TABLE-CHANGE-00001",
        "source_table_match_id": "TABLE-MATCH-0001",
        "change_type": "TABLE_MODIFIED",
        "classification": "MODIFIED",
        "old": {
            "logical_table_id": "OLD-T1",
            "header_signature": ["Item", "Value"],
            "row_count": 2,
            "column_count": 2,
        },
        "new": {
            "logical_table_id": "NEW-T1",
            "header_signature": ["Item", "Value"],
            "row_count": 2,
            "column_count": 2,
        },
        "cell_changes": [
            {
                "change_id": "TABLE-0001-R00000-C000",
                "classification": "UNCHANGED",
                "old": _cell("OC1", "Alpha", 0, 0),
                "new": _cell("NC1", "Alpha", 0, 0),
                "old_row_index": 0,
                "new_row_index": 0,
                "column_index": 0,
                "row_match_type": "matched",
                "row_similarity": 1.0,
            },
            {
                "change_id": "TABLE-0001-R00001-C000",
                "classification": "MODIFIED",
                "old": _cell("OC2", "twenty one", 1, 0),
                "new": _cell("NC2", "latecellmarker ninety nine", 1, 0),
                "old_row_index": 1,
                "new_row_index": 1,
                "column_index": 0,
                "row_match_type": "matched",
                "row_similarity": 0.8,
            },
            {
                "change_id": "TABLE-0001-R00001-C001",
                "classification": "UNCHANGED",
                "old": _cell("OC3", "day", 1, 1),
                "new": _cell("NC3", "day", 1, 1),
                "old_row_index": 1,
                "new_row_index": 1,
                "column_index": 1,
                "row_match_type": "matched",
                "row_similarity": 0.8,
            },
        ],
        "resolution": _resolution("accepted", False),
        "review_reasons": [],
    }
    review = [
        {
            "review_id": "REVIEW-000001",
            "source_kind": "non_table_change",
            "change_id": "CHANGE-000002",
            "source_candidate_id": "C2",
            "case_type": "modified_review",
            "resolution": _resolution("review", True, ["semantic_signal"]),
        }
    ]
    return {
        "schema_version": "3.0.0",
        "comparison_stage": "phase_1_final_comparison",
        "old_document": {
            "document_id": "OLD-DOC",
            "page_count": 4,
            "source_element_count": 40,
            "provisional_anchor_count": 10,
        },
        "new_document": {
            "document_id": "NEW-DOC",
            "page_count": 5,
            "source_element_count": 41,
            "provisional_anchor_count": 11,
        },
        "processing": {"version": "test", "matching_recomputed": False, "llm_optional": True},
        "statistics": {
            "changes": 2,
            "table_changes": 1,
            "review_queue": 1,
            "non_semantic_events": 0,
            "change_type_counts": {"MODIFIED": 1, "DELETED": 1},
            "resolution_status_counts": {"accepted": 2, "review": 1},
        },
        "changes": changes,
        "table_changes": [table],
        "review_queue": review,
        "non_semantic_events": [],
        "unresolved_table_reviews": [],
    }


def sample_elements():
    return [
        {
            "element_id": f"NEW-{i}",
            "element_type": "PARA",
            "text": f"context line {i}",
            "reading_order": i,
            "primary_page": 2,
        }
        for i in range(1, 6)
    ]


JOB = "a1b2c3d4e5f6"
JOB2 = "f6e5d4c3b2a1"


class SummarizeTest(unittest.TestCase):
    def test_done_counts(self):
        out = rr.summarize(JOB, "done", sample_canonical())
        self.assertEqual(out["state"], "done")
        self.assertTrue(out["complete"])
        self.assertEqual(out["counts"]["changes"], 2)
        self.assertEqual(out["counts"]["table_changes"], 1)
        self.assertEqual(out["counts"]["review_open"], 1)
        self.assertEqual(out["counts"]["change_type_counts"]["MODIFIED"], 1)
        self.assertTrue(out["snapshot"])

    def test_done_identity(self):
        out = rr.summarize(JOB, "done", sample_canonical())
        self.assertEqual(out["documents"]["old"]["page_count"], 4)
        self.assertEqual(out["documents"]["new"]["page_count"], 5)

    def test_running_is_safe_without_canonical(self):
        out = rr.summarize(JOB, "running", None)
        self.assertFalse(out["complete"])
        self.assertEqual(out["state"], "running")
        self.assertNotIn("counts", out)
        self.assertNotIn("snapshot", out)

    def test_failed_is_safe(self):
        out = rr.summarize(JOB, "failed", None)
        self.assertFalse(out["complete"])
        self.assertEqual(out["state"], "failed")
        self.assertNotIn("counts", out)

    def test_bad_job_id_rejected(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.summarize("../etc", "done", sample_canonical())
        self.assertEqual(ctx.exception.code, "invalid_arguments")


class SearchTest(unittest.TestCase):
    def test_default_scope_includes_review(self):
        out = rr.search(JOB, sample_canonical())
        self.assertEqual(out["total_matches"], 4)  # 2 changes + 1 table + 1 review
        kinds = {item["kind"] for item in out["returned"]}
        self.assertEqual(kinds, {"change", "table", "review"})

    def test_empty_result_is_success(self):
        out = rr.search(JOB, sample_canonical(), query="zzz-none")
        self.assertEqual(out["total_matches"], 0)
        self.assertEqual(out["returned"], [])
        self.assertFalse(out["truncated"])
        self.assertIsNone(out["next_cursor"])
        self.assertTrue(out["empty"])

    def test_pagination_cursor_advances(self):
        first = rr.search(JOB, sample_canonical(), limit=1)
        self.assertEqual(len(first["returned"]), 1)
        self.assertTrue(first["truncated"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(first["offset"], 0)
        second = rr.search(JOB, sample_canonical(), cursor=first["next_cursor"])
        self.assertEqual(second["offset"], 1)
        self.assertEqual(second["total_matches"], 4)
        self.assertNotEqual(first["returned"][0]["record_id"], second["returned"][0]["record_id"])

    def test_cursor_stale_on_query_change(self):
        first = rr.search(JOB, sample_canonical(), query="day", limit=1)
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), query="item", cursor=first["next_cursor"])
        self.assertEqual(ctx.exception.code, "stale_cursor")

    def test_cursor_stale_on_other_job(self):
        first = rr.search(JOB, sample_canonical(), limit=1)
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB2, sample_canonical(), cursor=first["next_cursor"])
        self.assertEqual(ctx.exception.code, "stale_cursor")

    def test_cursor_invalid_decode(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), cursor="not-a-real-cursor")
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_table_late_cell_snippet(self):
        out = rr.search(JOB, sample_canonical(), query="latecellmarker")
        self.assertEqual(out["total_matches"], 1)
        record = out["returned"][0]
        self.assertEqual(record["kind"], "table")
        self.assertIn("latecellmarker", record["snippet"].lower())

    def test_rejects_oversized_limit(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), limit=51)
        self.assertEqual(ctx.exception.code, "invalid_arguments")
        with self.assertRaises(rr.ReaderError):
            rr.search(JOB, sample_canonical(), limit=0)

    def test_rejects_bad_kind(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), kind="secrets")
        self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_rejects_bad_job_id(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search("nope", sample_canonical())
        self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_rejects_long_query(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), query="x" * 500)
        self.assertEqual(ctx.exception.code, "invalid_arguments")


class DetailsTest(unittest.TestCase):
    def test_change_values_and_context(self):
        out = rr.details(
            JOB,
            sample_canonical(),
            kind="change",
            record_id="CHANGE-000001",
            context_window=1,
            old_elements=sample_elements(),
            new_elements=sample_elements(),
        )
        self.assertEqual(out["kind"], "change")
        self.assertEqual(out["record_id"], "CHANGE-000001")
        self.assertEqual(out["change"]["old"]["text"], "Fee is 21 days")
        self.assertEqual(out["change"]["new"]["text"], "Fee is 99 days")
        self.assertTrue(out["context"]["new"]["available"])
        self.assertGreaterEqual(len(out["context"]["new"]["items"]), 1)

    def test_context_unavailable_warns(self):
        out = rr.details(
            JOB, sample_canonical(), kind="change", record_id="CHANGE-000001",
            old_elements=None, new_elements=None,
        )
        self.assertFalse(out["context"]["old"]["available"])
        self.assertIn("context_unavailable", out["warnings"])

    def test_unknown_record_not_found(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, sample_canonical(), kind="change", record_id="CHANGE-999999")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_bad_kind_invalid(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, sample_canonical(), kind="all", record_id="CHANGE-000001")
        self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_table_cell_pagination(self):
        first = rr.details(JOB, sample_canonical(), kind="table", record_id="TABLE-CHANGE-00001", cell_limit=1)
        self.assertEqual(len(first["table"]["cell_changes"]), 1)
        self.assertTrue(first["table"]["cell_changes_truncated"])
        self.assertEqual(first["table"]["next_offset"], 1)
        second = rr.details(
            JOB, sample_canonical(), kind="table", record_id="TABLE-CHANGE-00001",
            cell_limit=1, cell_offset=1,
        )
        self.assertEqual(second["table"]["cell_offset"], 1)
        self.assertEqual(len(second["table"]["cell_changes"]), 1)

    def test_context_window_bounded(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, sample_canonical(), kind="change", record_id="CHANGE-000001", context_window=99)
        self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_review_details(self):
        out = rr.details(JOB, sample_canonical(), kind="review", record_id="REVIEW-000001")
        self.assertEqual(out["review"]["resolution"]["status"], "review")
        self.assertIn("semantic_signal", out["review"]["resolution"]["review_reasons"])


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_stable_for_identical_content(self):
        self.assertEqual(rr.snapshot_fingerprint(sample_canonical()), rr.snapshot_fingerprint(sample_canonical()))

    def test_fingerprint_changes_when_text_changes_same_ids(self):
        base = rr.snapshot_fingerprint(sample_canonical())
        mutated = sample_canonical()
        mutated["changes"][0]["new"]["text"] = "Fee is 100 days"
        self.assertNotEqual(base, rr.snapshot_fingerprint(mutated))

    def test_fingerprint_changes_when_resolution_changes_same_ids(self):
        base = rr.snapshot_fingerprint(sample_canonical())
        mutated = sample_canonical()
        mutated["changes"][0]["resolution"]["status"] = "review"
        self.assertNotEqual(base, rr.snapshot_fingerprint(mutated))

    def test_fingerprint_changes_when_cell_changes_same_ids(self):
        base = rr.snapshot_fingerprint(sample_canonical())
        mutated = sample_canonical()
        mutated["table_changes"][0]["cell_changes"][1]["new"]["text"] = "changed cell"
        self.assertNotEqual(base, rr.snapshot_fingerprint(mutated))


class ArtifactValidationTest(unittest.TestCase):
    def test_search_rejects_duplicate_ids(self):
        canonical = sample_canonical()
        canonical["changes"].append(dict(canonical["changes"][0]))
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical)
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_search_rejects_missing_id(self):
        canonical = sample_canonical()
        canonical["changes"].append({"change_type": "ADDED"})
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical)
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_search_rejects_nonstring_id(self):
        canonical = sample_canonical()
        canonical["table_changes"][0]["table_change_id"] = 123
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical)
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_search_rejects_nonlist_records(self):
        canonical = sample_canonical()
        canonical["changes"] = {"not": "a list"}
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical)
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_details_rejects_missing_nested_id(self):
        canonical = sample_canonical()
        canonical["review_queue"].append({"case_type": "x"})
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, canonical, kind="review", record_id="REVIEW-000001")
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_details_rejects_malformed_nested_row(self):
        canonical = sample_canonical()
        canonical["changes"][0]["old"] = ["not", "an", "object"]
        # malformed nested shapes must not raise an attribute error
        out = rr.details(JOB, canonical, kind="change", record_id="CHANGE-000001")
        self.assertEqual(out["change"]["old"]["text"], "")


class CursorValidationTest(unittest.TestCase):
    def _cursor(self, **overrides):
        canonical = sample_canonical()
        payload = {
            "v": 1,
            "job_id": JOB,
            "query": "",
            "kind": "all",
            "status": "all",
            "change_type": "",
            "fingerprint": rr.snapshot_fingerprint(canonical),
            "offset": 1,
        }
        payload.update(overrides)
        return rr._encode_cursor(payload), canonical

    def test_rejects_non_int_offset(self):
        cursor, canonical = self._cursor(offset="bad")
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical, cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_negative_offset(self):
        cursor, canonical = self._cursor(offset=-5)
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical, cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_oversized_cursor(self):
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), cursor="a" * 5000)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_wrong_version(self):
        cursor, canonical = self._cursor(v=2)
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical, cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_bad_kind_field(self):
        cursor, canonical = self._cursor(kind="secrets")
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical, cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_non_hex_fingerprint(self):
        cursor, canonical = self._cursor(fingerprint="not-a-fingerprint")
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, canonical, cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")

    def test_rejects_non_object_payload(self):
        cursor = rr._encode_cursor([1, 2, 3])
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.search(JOB, sample_canonical(), cursor=cursor)
        self.assertEqual(ctx.exception.code, "invalid_cursor")


class TextWindowTest(unittest.TestCase):
    def test_change_text_offset_retrieves_tail(self):
        canonical = sample_canonical()
        text = "A" * 100 + "TAILMARKER"
        canonical["changes"][0]["new"]["text"] = text
        first = rr.details(JOB, canonical, kind="change", record_id="CHANGE-000001", text_limit=50)
        self.assertEqual(first["change"]["new"]["text_offset"], 0)
        self.assertEqual(first["change"]["new"]["text_length"], len(text))
        self.assertEqual(first["change"]["new"]["text_next_offset"], 50)
        self.assertTrue(first["change"]["new"]["text_truncated"])
        second = rr.details(
            JOB, canonical, kind="change", record_id="CHANGE-000001", text_offset=100, text_limit=50
        )
        self.assertEqual(second["change"]["new"]["text"], "TAILMARKER")
        self.assertIsNone(second["change"]["new"]["text_next_offset"])

    def test_text_offset_rejects_bool_and_out_of_range(self):
        for bad in (True, -1, 10_000_000):
            with self.assertRaises(rr.ReaderError) as ctx:
                rr.details(
                    JOB, sample_canonical(), kind="change", record_id="CHANGE-000001", text_offset=bad
                )
            self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_text_limit_bounded(self):
        for bad in (0, 4001):
            with self.assertRaises(rr.ReaderError) as ctx:
                rr.details(
                    JOB, sample_canonical(), kind="change", record_id="CHANGE-000001", text_limit=bad
                )
            self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_cell_text_offset_retrieves_tail(self):
        canonical = sample_canonical()
        canonical["table_changes"][0]["cell_changes"][1]["new"]["text"] = "B" * 60 + "CELLTAIL"
        first = rr.details(
            JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001",
            cell_offset=1, cell_limit=1, text_limit=20,
        )
        cell = first["table"]["cell_changes"][0]["new"]
        self.assertEqual(cell["text_next_offset"], 20)
        second = rr.details(
            JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001",
            cell_offset=1, cell_limit=1, text_offset=60, text_limit=20,
        )
        self.assertEqual(second["table"]["cell_changes"][0]["new"]["text"], "CELLTAIL")

    def test_context_text_bounded(self):
        elements = sample_elements()
        elements[0]["text"] = "C" * 40 + "CTXTAIL"
        out = rr.details(
            JOB, sample_canonical(), kind="change", record_id="CHANGE-000001",
            new_elements=elements, text_limit=10,
        )
        item = next(i for i in out["context"]["new"]["items"] if i["element_id"] == "NEW-1")
        self.assertEqual(item["text_length"], 47)
        self.assertEqual(item["text_next_offset"], 10)


class ContextSourceTest(unittest.TestCase):
    def test_prefers_element_id_over_reading_order(self):
        elements = [
            {"element_id": "ZZ", "reading_order": 42, "element_type": "PARA", "text": "wrong"},
            {"element_id": "NEW-1", "reading_order": 0, "element_type": "PARA", "text": "right"},
        ]
        out = rr.details(
            JOB, sample_canonical(), kind="change", record_id="CHANGE-000001", new_elements=elements
        )
        self.assertTrue(out["context"]["new"]["available"])
        ids = [item["element_id"] for item in out["context"]["new"]["items"]]
        self.assertIn("NEW-1", ids)

    def test_missing_element_id_reports_not_found(self):
        out = rr.details(
            JOB, sample_canonical(), kind="change", record_id="CHANGE-000001",
            old_elements=sample_elements(), new_elements=sample_elements(),
        )
        self.assertFalse(out["context"]["old"]["available"])
        self.assertEqual(out["context"]["old"]["reason"], "element_not_found")
        self.assertIn("old_context_element_not_found", out["warnings"])

    def test_duplicate_element_id_unavailable(self):
        elements = [
            {"element_id": "NEW-1", "reading_order": 0, "element_type": "PARA", "text": "a"},
            {"element_id": "NEW-1", "reading_order": 1, "element_type": "PARA", "text": "b"},
        ]
        out = rr.details(
            JOB, sample_canonical(), kind="change", record_id="CHANGE-000001", new_elements=elements
        )
        self.assertFalse(out["context"]["new"]["available"])
        self.assertEqual(out["context"]["new"]["reason"], "ambiguous_element_id")

    def test_ambiguous_reading_order_unavailable(self):
        canonical = sample_canonical()
        canonical["changes"][0]["new"] = {"element_id": None, "reading_order": 0, "text": "x"}
        elements = [
            {"element_id": "A", "reading_order": 0, "text": "a"},
            {"element_id": "B", "reading_order": 0, "text": "b"},
        ]
        out = rr.details(
            JOB, canonical, kind="change", record_id="CHANGE-000001", new_elements=elements
        )
        self.assertEqual(out["context"]["new"]["reason"], "ambiguous_reading_order")


class SummaryRecordCountsTest(unittest.TestCase):
    def test_counts_ignore_stale_statistics(self):
        canonical = sample_canonical()
        canonical["statistics"] = {
            "change_type_counts": {"MODIFIED": 99},
            "resolution_status_counts": {"accepted": 99},
            "review_queue": 99,
        }
        out = rr.summarize(JOB, "done", canonical)
        self.assertEqual(out["counts"]["changes"], 2)
        self.assertEqual(out["counts"]["review_open"], 1)
        self.assertEqual(out["counts"]["change_type_counts"]["MODIFIED"], 1)
        self.assertEqual(out["counts"]["resolution_status_counts"]["accepted"], 2)
        self.assertEqual(out["counts"]["resolution_status_counts"]["review"], 1)

    def test_document_identity_shape(self):
        out = rr.summarize(JOB, "done", sample_canonical())
        self.assertTrue(out["documents"]["old"]["available"])
        self.assertEqual(out["documents"]["old"]["provisional_anchor_count"], 10)

    def test_rejects_duplicate_ids_safely(self):
        canonical = sample_canonical()
        canonical["changes"].append(dict(canonical["changes"][0]))
        out = rr.summarize(JOB, "done", canonical)
        self.assertFalse(out["complete"])
        self.assertEqual(out["status"], "invalid_artifact")

    def test_rejects_missing_ids_safely(self):
        canonical = sample_canonical()
        canonical["review_queue"].append({"case_type": "x"})
        out = rr.summarize(JOB, "done", canonical)
        self.assertEqual(out["status"], "invalid_artifact")


class SearchStatusSemanticsTest(unittest.TestCase):
    def test_accepted_filter_excludes_needs_review(self):
        canonical = sample_canonical()
        canonical["changes"][0]["resolution"]["needs_review"] = True
        out = rr.search(JOB, canonical, status="accepted")
        ids = {record["record_id"] for record in out["returned"]}
        self.assertNotIn("CHANGE-000001", ids)
        self.assertIn("TABLE-CHANGE-00001", ids)

    def test_review_kind_always_needs_review(self):
        canonical = sample_canonical()
        canonical["review_queue"][0]["resolution"] = {
            "status": "accepted", "needs_review": False, "review_reasons": []
        }
        out = rr.search(JOB, canonical, kind="review")
        record = out["returned"][0]
        self.assertTrue(record["needs_review"])
        self.assertFalse(record["confirmed"])


class BoundedProjectionTest(unittest.TestCase):
    def test_large_evidence_bounded(self):
        canonical = sample_canonical()
        canonical["review_queue"][0]["evidence"] = {"items": list(range(100000))}
        out = rr.details(JOB, canonical, kind="review", record_id="REVIEW-000001")
        evidence = out["review"]["evidence"]
        self.assertLessEqual(len(evidence["items"]), rr.MAX_METADATA_ITEMS)
        self.assertIn("evidence", out["review"]["clipped"])

    def test_large_token_diff_bounded(self):
        canonical = sample_canonical()
        canonical["changes"][0]["token_diff"] = {"source_token_diff": {"ops": list(range(100000))}}
        out = rr.details(JOB, canonical, kind="change", record_id="CHANGE-000001")
        ops = out["change"]["token_diff"]["source_token_diff"]["ops"]
        self.assertLessEqual(len(ops), rr.MAX_METADATA_ITEMS)
        self.assertIn("token_diff", out["change"]["clipped"])

    def test_table_header_signature_bounded(self):
        canonical = sample_canonical()
        canonical["table_changes"][0]["old"]["header_signature"] = [f"H{i}" for i in range(1000)]
        out = rr.details(JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001")
        self.assertLessEqual(len(out["table"]["old"]["header_signature"]), rr.MAX_TABLE_LIST)

    def test_resolution_reasons_bounded(self):
        canonical = sample_canonical()
        canonical["changes"][1]["resolution"]["review_reasons"] = [f"r{i}" for i in range(1000)]
        out = rr.details(JOB, canonical, kind="change", record_id="CHANGE-000002")
        self.assertLessEqual(len(out["change"]["resolution"]["review_reasons"]), rr.MAX_METADATA_ITEMS)

    def test_cell_polygon_and_span_metadata_preserved(self):
        canonical = sample_canonical()
        cell = canonical["table_changes"][0]["cell_changes"][1]["new"]
        cell["primary_polygon"] = [[0.0, 1.0], [2.0, 3.0]]
        cell["spans"] = [{"row_span": 1, "column_span": 1}]
        out = rr.details(
            JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001", cell_limit=10
        )
        target = next(
            change for change in out["table"]["cell_changes"]
            if change["new"] and change["new"]["cell_id"] == "NC2"
        )
        self.assertEqual(target["new"]["primary_polygon"], [[0.0, 1.0], [2.0, 3.0]])
        self.assertEqual(target["new"]["spans"], [{"row_span": 1, "column_span": 1}])
        self.assertEqual(target["new"]["row_span"], 1)

    def test_details_carries_canonical_reference(self):
        out = rr.details(JOB, sample_canonical(), kind="change", record_id="CHANGE-000001")
        ref = out["canonical_reference"]
        self.assertEqual(ref["artifact"], "comparison_result.json")
        self.assertEqual(ref["job_id"], JOB)
        self.assertEqual(ref["record_id"], "CHANGE-000001")


class ReviewContextTest(unittest.TestCase):
    def _canonical(self):
        canonical = sample_canonical()
        canonical["review_queue"] = [
            {
                "review_id": "REVIEW-100001",
                "source_kind": "resegmentation_pair",
                "case_type": "resegmentation_suspect",
                "evidence": {"old_element_ids": ["OLD-1", "OLD-2"], "new_element_ids": ["NEW-1"]},
                "resolution": _resolution("review", True, ["resegmentation_suspect"]),
            }
        ]
        return canonical

    def _elements(self):
        old = [
            {"element_id": f"OLD-{i}", "element_type": "PARA", "reading_order": i,
             "primary_page": 1, "text": f"old body {i}"}
            for i in range(1, 5)
        ]
        new = [
            {"element_id": f"NEW-{i}", "element_type": "PARA", "reading_order": i,
             "primary_page": 2, "text": f"new body {i}"}
            for i in range(1, 5)
        ]
        return old, new

    def test_review_context_groups_by_anchor(self):
        old_elements, new_elements = self._elements()
        out = rr.details(
            JOB, self._canonical(), kind="review", record_id="REVIEW-100001",
            old_elements=old_elements, new_elements=new_elements,
        )
        self.assertTrue(out["review"]["needs_review"])
        self.assertFalse(out["review"]["confirmed"])
        old_context = out["context"]["old"]
        self.assertTrue(old_context["available"])
        self.assertEqual(old_context["sources_total"], 2)
        self.assertEqual(old_context["source_offset"], 0)
        self.assertFalse(old_context["sources_truncated"])
        self.assertEqual([g["anchor_id"] for g in old_context["groups"]], ["OLD-1", "OLD-2"])
        first = old_context["groups"][0]
        self.assertTrue(first["available"])
        self.assertIn("old body 1", [item["text"] for item in first["items"]])
        self.assertEqual(
            [g["anchor_id"] for g in out["context"]["new"]["groups"]], ["NEW-1"]
        )

    def test_review_context_pages_multiple_anchors(self):
        old_elements, new_elements = self._elements()
        canonical = self._canonical()
        canonical["review_queue"][0]["evidence"]["old_element_ids"] = [f"OLD-{i}" for i in range(1, 5)]
        first = rr.details(
            JOB, canonical, kind="review", record_id="REVIEW-100001",
            old_elements=old_elements, new_elements=new_elements,
        )
        old_context = first["context"]["old"]
        self.assertEqual(old_context["sources_total"], 4)
        self.assertEqual(len(old_context["groups"]), rr.MAX_CONTEXT_SOURCES)
        self.assertTrue(old_context["sources_truncated"])
        self.assertEqual(old_context["next_source_offset"], rr.MAX_CONTEXT_SOURCES)
        second = rr.details(
            JOB, canonical, kind="review", record_id="REVIEW-100001",
            old_elements=old_elements, new_elements=new_elements,
            context_source_offset=old_context["next_source_offset"],
        )
        second_old = second["context"]["old"]
        self.assertEqual(second_old["source_offset"], rr.MAX_CONTEXT_SOURCES)
        self.assertEqual(len(second_old["groups"]), 1)
        self.assertFalse(second_old["sources_truncated"])

    def test_review_linked_refs_and_change_anchors(self):
        canonical = self._canonical()
        canonical["review_queue"][0].pop("evidence")
        canonical["review_queue"][0]["change_id"] = "CHANGE-000001"
        old_elements, new_elements = self._elements()
        out = rr.details(
            JOB, canonical, kind="review", record_id="REVIEW-100001",
            old_elements=old_elements, new_elements=new_elements,
        )
        ref = out["review"]["linked_refs"][0]
        self.assertEqual((ref["kind"], ref["record_id"], ref["found"]), ("change", "CHANGE-000001", True))
        self.assertTrue(ref["needs_review"])
        self.assertFalse(ref["confirmed"])
        self.assertEqual(out["review"]["resolution"]["status"], "review")
        self.assertEqual([g["anchor_id"] for g in out["context"]["old"]["groups"]], ["OLD-1"])
        self.assertEqual([g["anchor_id"] for g in out["context"]["new"]["groups"]], ["NEW-1"])

    def test_review_unknown_link_invalid_artifact(self):
        canonical = self._canonical()
        canonical["review_queue"][0]["change_id"] = "CHANGE-999999"
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, canonical, kind="review", record_id="REVIEW-100001")
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_review_no_sources_context_unavailable(self):
        canonical = sample_canonical()
        canonical["review_queue"] = [
            {
                "review_id": "REVIEW-100001",
                "case_type": "resegmentation_suspect",
                "resolution": _resolution("review", True, []),
            }
        ]
        out = rr.details(JOB, canonical, kind="review", record_id="REVIEW-100001")
        self.assertFalse(out["context"]["old"]["available"])
        self.assertEqual(out["context"]["old"]["reason"], "context_unavailable")
        self.assertIn("context_unavailable", out["warnings"])

    def test_review_bad_source_list_invalid_artifact(self):
        canonical = self._canonical()
        canonical["review_queue"][0]["evidence"]["old_element_ids"] = "OLD-1"
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, canonical, kind="review", record_id="REVIEW-100001")
        self.assertEqual(ctx.exception.code, "invalid_artifact")


class SearchCoverageTest(unittest.TestCase):
    def test_review_source_text_not_indexed_warning(self):
        canonical = sample_canonical()
        canonical["review_queue"][0]["evidence"] = {
            "old_element_ids": ["OLD-1"], "new_element_ids": ["NEW-1"]
        }
        out = rr.search(JOB, canonical, kind="review")
        coverage = out["search_coverage"]
        self.assertFalse(coverage["review_source_text_indexed"])
        self.assertEqual(coverage["warning"], "review_source_text_not_indexed")
        self.assertIn("review_source_text_not_indexed", out["warnings"])

    def test_change_only_scope_has_no_review_coverage(self):
        out = rr.search(JOB, sample_canonical(), kind="change")
        self.assertIsNone(out["search_coverage"])


class ResponseBudgetTest(unittest.TestCase):
    def _oversized_table(self):
        canonical = sample_canonical()
        cells = []
        for index in range(40):
            cells.append(
                {
                    "change_id": f"TABLE-0001-R{index:05d}-C000",
                    "classification": "MODIFIED",
                    "old": _cell(f"OC{index}", "A" * 4000, index, 0),
                    "new": _cell(f"NC{index}", "B" * 4000, index, 0),
                    "old_row_index": index,
                    "new_row_index": index,
                    "column_index": 0,
                    "row_match_type": "matched",
                    "row_similarity": 1.0,
                }
            )
        canonical["table_changes"][0]["cell_changes"] = cells
        return canonical

    def test_oversized_table_payload_rejected(self):
        canonical = self._oversized_table()
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(
                JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001",
                cell_limit=rr.MAX_CELL_LIMIT, text_limit=rr.MAX_TEXT_LENGTH,
            )
        self.assertEqual(ctx.exception.code, "response_too_large")

    def test_reduced_window_returns_complete_page_and_input_unchanged(self):
        canonical = self._oversized_table()
        before = json.dumps(canonical, sort_keys=True)
        out = rr.details(
            JOB, canonical, kind="table", record_id="TABLE-CHANGE-00001",
            cell_limit=2, text_limit=100,
        )
        self.assertEqual(len(out["table"]["cell_changes"]), 2)
        self.assertTrue(out["table"]["cell_changes_truncated"])
        self.assertEqual(json.dumps(canonical, sort_keys=True), before)

    def test_oversized_metadata_rejected(self):
        canonical = sample_canonical()
        canonical["review_queue"][0]["evidence"] = {
            f"k{index}": ["x" * 512 for _ in range(25)] for index in range(25)
        }
        with self.assertRaises(rr.ReaderError) as ctx:
            rr.details(JOB, canonical, kind="review", record_id="REVIEW-000001")
        self.assertEqual(ctx.exception.code, "response_too_large")


if __name__ == "__main__":
    unittest.main()
