import json, unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fastapi.testclient import TestClient
from document_compare.phase2.api import service
from document_compare.phase2.api.summary import build_summary


@contextmanager
def _mocked_job_dir(files: dict[str, bytes]):
    """Yield a temp job dir with output files and patched service accessors."""
    with TemporaryDirectory() as d:
        out = Path(d) / "output"
        out.mkdir()
        for name, content in files.items():
            (out / name).write_bytes(content)
        status = {"state": "done", "created_at": "2026-09-11T00:00:00+00:00"}
        with patch.object(service, "_read_job", return_value=status), \
             patch.object(service, "_job_dir", return_value=Path(d)):
            yield d


def _status(state="done", **extra):
    return {"state": state, "created_at": "2026-09-11T00:00:00+00:00", **extra}

class SummaryTest(unittest.TestCase):
    def test_queued_blocks_progress(self):
        with TemporaryDirectory() as d:
            out = build_summary(Path(d), _status(state="queued"))
        self.assertEqual(out["progress"]["state"], "queued")
        self.assertEqual(out["progress"]["stages_completed"], [])
        self.assertEqual(out["progress"]["total_stages"], 15)
        self.assertIsNone(out["progress"]["started_at"])
        self.assertIsNone(out["progress"]["finished_at"])
        self.assertIsNone(out["progress"]["duration_seconds"])
        self.assertIsNone(out["extraction"]["verdict"])
    def test_running_has_started(self):
        with TemporaryDirectory() as d:
            out = build_summary(Path(d), _status(state="running", started_at="2026-09-11T00:01:00+00:00", stages_completed=["01", "02"]))
        self.assertEqual(out["progress"]["started_at"], "2026-09-11T00:01:00+00:00")
        self.assertEqual(out["progress"]["stages_completed"], ["01", "02"])
    def test_failed_surfaces_error_tail(self):
        with TemporaryDirectory() as d:
            out = build_summary(Path(d), _status(state="failed", error_tail="boom"))
        self.assertEqual(out["progress"]["failure"], "boom")
    def test_done_reports_verdict_and_counts(self):
        with TemporaryDirectory() as d:
            out_dir = Path(d) / "output"
            out_dir.mkdir()
            (out_dir / "old_extraction_validation.json").write_text(json.dumps({
                "requires_review": False, "document_flags": [],
                "summary": {"content_characters": 1200},
                "native_text_validation": {"pages": []}}))
            (out_dir / "new_extraction_validation.json").write_text(json.dumps({
                "requires_review": False, "document_flags": [],
                "summary": {"content_characters": 1300},
                "native_text_validation": {"pages": []}}))
            (out_dir / "comparison_candidates_with_movement.json").write_text(json.dumps({
                "statistics": {
                    "input_unchanged": 5, "input_modified": 1, "input_modified_review": 0,
                    "input_duplicate_exact_review": 0, "remaining_deleted_candidates": 0,
                    "remaining_added_candidates": 0, "relocated_unchanged": 0,
                    "resegmentation_review_pairs": 0}}))
            canonical = {
                "old_document": {"page_count": 2, "source_element_count": 10},
                "new_document": {"page_count": 3, "source_element_count": 11},
                "changes": [
                    {"change_id": "C1", "change_type": "MODIFIED", "resolution": {"status": "accepted", "needs_review": False}},
                    {"change_id": "C2", "change_type": "DELETED", "resolution": {"status": "review", "needs_review": True}},
                ],
                "table_changes": [{"table_change_id": "T1", "change_type": "MODIFIED"}],
                "review_queue": [{"change_id": "C2"}],
                "non_semantic_events": [{"event_id": "N1"}],
                "statistics": {
                    "resolution_status_counts": {"accepted": 1, "review": 1},
                    "review_queue": 1,
                    "non_semantic_events": 1,
                },
            }
            (out_dir / "comparison_result.json").write_text(json.dumps(canonical))
            (out_dir / "llm_review_decisions.json").write_text(json.dumps({"processing": {"mode": "mock", "provider": "openai"}}))
            out = build_summary(Path(d), _status(stages_completed=[f"{i:02d}" for i in range(1, 16)]))
        self.assertEqual(out["documents"]["old"]["pages"], 2)
        self.assertEqual(out["documents"]["old"]["elements"], 10)
        self.assertEqual(out["documents"]["old"]["content_characters"], 1200)
        self.assertEqual(out["documents"]["new"]["pages"], 3)
        self.assertEqual(out["documents"]["new"]["elements"], 11)
        self.assertEqual(out["documents"]["new"]["content_characters"], 1300)
        self.assertEqual(out["changes"]["modified"], 1)
        self.assertEqual(out["changes"]["deleted"], 1)
        self.assertEqual(out["changes"]["table"], 1)
        self.assertEqual(out["disposition"]["accepted"], 1)
        self.assertEqual(out["disposition"]["review"], 1)
        self.assertEqual(out["disposition"]["suppressed"], 1)
        self.assertEqual(out["matching"]["unchanged"], 5)
        self.assertEqual(out["matching"]["modified"], 1)
        self.assertEqual(out["matching"]["old_unmatched"], 0)
        self.assertEqual(out["tables"]["changes"], 1)
        self.assertEqual(out["tables"]["unresolved_reviews"], 0)
        self.assertEqual(out["extraction"]["verdict"], "PASS")
    def test_evaluator_mode_present(self):
        with TemporaryDirectory() as d:
            out_dir = Path(d) / "output"
            out_dir.mkdir()
            (out_dir / "llm_review_decisions.json").write_text(json.dumps({
                "processing": {"mode": "mock", "provider": "openai", "model": "gpt-x"}}))
            out = build_summary(Path(d), _status())
        self.assertEqual((out["evaluator"]["mode"], out["evaluator"]["provider"], out["evaluator"]["model"]),
                         ("mock", "openai", "gpt-x"))

class SummaryEndpointTest(unittest.TestCase):
    def test_summary_endpoint_returns_build_summary(self):
        status = {"state": "done", "created_at": "2026-09-11T00:00:00+00:00", "stages_completed": ["01"]}
        with TemporaryDirectory() as d:
            with patch.object(service, "_read_job", return_value=status), \
                patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get("/jobs/a1b2c3d4e5f6/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), build_summary(Path(d), status))

    def test_artifact_pdf_serves_with_media_type(self):
        with _mocked_job_dir({"comparison_redline.pdf": b"%PDF-1.4\n"}):
            response = TestClient(service.app).get("/jobs/a1b2c3d4e5f6/artifacts/comparison_redline.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/pdf"))
        self.assertIn("comparison_redline.pdf", service.ARTIFACT_NAMES)

    def test_artifact_manifest_json_serves_with_media_type(self):
        with _mocked_job_dir({"bu_comparison_review_manifest.json": b'{"schema_version":"1.0.0"}'}):
            response = TestClient(service.app).get("/jobs/a1b2c3d4e5f6/artifacts/bu_comparison_review_manifest.json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertIn("bu_comparison_review_manifest.json", service.ARTIFACT_NAMES)

    def test_artifact_inline_pdf_serves_with_media_type(self):
        with _mocked_job_dir({"comparison_inline.pdf": b"%PDF-1.4\n"}):
            response = TestClient(service.app).get("/jobs/a1b2c3d4e5f6/artifacts/comparison_inline.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/pdf"))
        self.assertIn("comparison_inline.pdf", service.ARTIFACT_NAMES)

    def test_artifact_inline_audit_json_serves_with_media_type(self):
        with _mocked_job_dir({"inline_pdf_audit.json": b'{"schema_version":"1.0.0"}'}):
            response = TestClient(service.app).get("/jobs/a1b2c3d4e5f6/artifacts/inline_pdf_audit.json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertIn("inline_pdf_audit.json", service.ARTIFACT_NAMES)

class ReadEndpointTest(unittest.TestCase):
    JOB = "a1b2c3d4e5f6"

    def _canonical_bytes(self, changes):
        return json.dumps(
            {
                "comparison_stage": "phase_1_final_comparison",
                "old_document": {"document_id": "OLD"},
                "new_document": {"document_id": "NEW"},
                "changes": changes,
                "table_changes": [],
                "review_queue": [],
                "statistics": {},
            }
        )

    def test_malformed_status_json_is_invalid_artifact(self):
        with TemporaryDirectory() as d:
            (Path(d) / "job_status.json").write_text("{not json")
            with patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(f"/jobs/{self.JOB}/read/summary")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "invalid_artifact")

    def test_non_object_status_is_invalid_artifact(self):
        with TemporaryDirectory() as d:
            (Path(d) / "job_status.json").write_text("[]")
            with patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(f"/jobs/{self.JOB}/read/summary")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "invalid_artifact")

    def test_mismatched_status_job_id_is_invalid_artifact(self):
        with TemporaryDirectory() as d:
            (Path(d) / "job_status.json").write_text(
                json.dumps({"state": "done", "job_id": "ffffffffffff"})
            )
            with patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(f"/jobs/{self.JOB}/read/summary")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "invalid_artifact")

    def test_read_details_text_offset_end_to_end(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            (out / "comparison_result.json").write_text(
                self._canonical_bytes(
                    [
                        {
                            "change_id": "CHANGE-000001",
                            "change_type": "MODIFIED",
                            "old": {"element_id": "O1", "text": "a"},
                            "new": {"element_id": "N1", "text": "B" * 20 + "TAIL"},
                        }
                    ]
                )
            )
            status = {"state": "done", "job_id": self.JOB}
            with patch.object(service, "_read_job", return_value=status), \
                patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(
                    f"/jobs/{self.JOB}/read/details",
                    params={
                        "kind": "change",
                        "record_id": "CHANGE-000001",
                        "text_offset": 20,
                        "text_limit": 10,
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["change"]["new"]["text"], "TAIL")

    def test_read_search_duplicate_ids_invalid_artifact(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            (out / "comparison_result.json").write_text(
                self._canonical_bytes([{"change_id": "C1"}, {"change_id": "C1"}])
            )
            status = {"state": "done", "job_id": self.JOB}
            with patch.object(service, "_read_job", return_value=status), \
                patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(f"/jobs/{self.JOB}/read/search")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"], "invalid_artifact")


    def test_read_details_response_too_large_maps_413(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            cells = [
                {
                    "change_id": f"R{index}",
                    "classification": "MODIFIED",
                    "old": {"cell_id": f"OC{index}", "source_table_id": "OLD-T1", "text": "A" * 4000},
                    "new": {"cell_id": f"NC{index}", "source_table_id": "NEW-T1", "text": "B" * 4000},
                    "old_row_index": index,
                    "new_row_index": index,
                    "column_index": 0,
                    "row_match_type": "matched",
                }
                for index in range(40)
            ]
            canonical = {
                "comparison_stage": "phase_1_final_comparison",
                "old_document": {"document_id": "OLD"},
                "new_document": {"document_id": "NEW"},
                "changes": [],
                "table_changes": [
                    {"table_change_id": "TABLE-CHANGE-00001", "cell_changes": cells}
                ],
                "review_queue": [],
                "statistics": {},
            }
            (out / "comparison_result.json").write_text(json.dumps(canonical))
            status = {"state": "done", "job_id": self.JOB}
            with patch.object(service, "_read_job", return_value=status), \
                patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(
                    f"/jobs/{self.JOB}/read/details",
                    params={
                        "kind": "table",
                        "record_id": "TABLE-CHANGE-00001",
                        "cell_limit": 100,
                        "text_limit": 4000,
                    },
                )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["error"], "response_too_large")


    def test_read_details_review_context_end_to_end(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "output"
            out.mkdir()
            canonical = {
                "comparison_stage": "phase_1_final_comparison",
                "old_document": {"document_id": "OLD"},
                "new_document": {"document_id": "NEW"},
                "changes": [],
                "table_changes": [],
                "review_queue": [
                    {
                        "review_id": "REVIEW-100001",
                        "evidence": {"old_element_ids": ["OLD-1"], "new_element_ids": ["NEW-1"]},
                        "resolution": {
                            "status": "review",
                            "needs_review": True,
                            "review_reasons": ["resegmentation_suspect"],
                        },
                    }
                ],
                "statistics": {},
            }
            (out / "comparison_result.json").write_text(json.dumps(canonical))
            (out / "old_document_structured.json").write_text(
                json.dumps(
                    {
                        "document": {
                            "document_id": "OLD",
                            "elements": [
                                {"element_id": "OLD-0", "element_type": "PARA", "reading_order": -1, "text": "old before"},
                                {"element_id": "OLD-1", "element_type": "PARA", "reading_order": 0, "text": "old body one"},
                            ],
                        }
                    }
                )
            )
            (out / "new_document_structured.json").write_text(
                json.dumps(
                    {
                        "document": {
                            "document_id": "NEW",
                            "elements": [
                                {"element_id": "NEW-1", "element_type": "PARA", "reading_order": 0, "text": "new body one"}
                            ],
                        }
                    }
                )
            )
            status = {"state": "done", "job_id": self.JOB}
            with patch.object(service, "_read_job", return_value=status), \
                patch.object(service, "_job_dir", return_value=Path(d)):
                response = TestClient(service.app).get(
                    f"/jobs/{self.JOB}/read/details",
                    params={"kind": "review", "record_id": "REVIEW-100001"},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["review"]["needs_review"])
        old_context = body["context"]["old"]
        self.assertEqual(old_context["sources_total"], 1)
        group = old_context["groups"][0]
        self.assertEqual(group["anchor_id"], "OLD-1")
        self.assertIn("old body one", [item["text"] for item in group["items"]])


if __name__ == "__main__":
    unittest.main()