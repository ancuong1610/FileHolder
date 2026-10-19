"""Saved Phase-1 reader and phase selection: offline, no provider calls."""
import asyncio
import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from document_compare.phase2.workflow import local_reviewer as workflow
from document_compare.phase2.reader.result_reader import ReaderError


def canonical():
    return {
        "comparison_stage": "phase_1_final_comparison",
        "changes": [{
            "change_id": "CHANGE-000001", "change_type": "MODIFIED",
            "old": {"text": "Limit 10", "element_id": "O1"},
            "new": {"text": "Limit 20", "element_id": "N1"},
            "resolution": {"status": "accepted", "needs_review": False},
        }],
        "table_changes": [], "review_queue": [], "statistics": {},
    }


class SavedWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="saved workspace ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "output").mkdir()
        self.write("comparison_result.json", canonical())

    def write(self, name, value):
        (self.root / "output" / name).write_text(json.dumps(value), encoding="utf-8")

    def load(self):
        from document_compare.phase2.workflow.saved_workspace import SavedWorkspaceGateway
        return SavedWorkspaceGateway(self.root)

    def test_real_reader_search_and_details_are_read_only_and_path_free(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        gateway = self.load()
        summary = gateway.summarize(gateway.job_id)
        self.assertTrue(summary["complete"])
        self.assertIn("old_document_context_missing", summary["limitations"])
        result = gateway.search(gateway.job_id, query="20", limit=1)
        self.assertEqual(result["total_matches"], 1)
        detail = gateway.details(gateway.job_id, "change", "CHANGE-000001")
        self.assertIn("Limit 20", json.dumps(detail))
        self.assertNotIn(str(self.root), json.dumps([summary, result, detail]))
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(gateway.job_id, self.load().job_id)

    def test_rejects_other_job_in_all_reader_methods(self):
        gateway = self.load()
        for call in (lambda: gateway.summarize("000000000000"),
                     lambda: gateway.search("000000000000"),
                     lambda: gateway.details("000000000000", "change", "CHANGE-000001")):
            with self.assertRaises(ReaderError):
                call()

    def test_missing_and_malformed_canonical_fail_actionably(self):
        path = self.root / "output" / "comparison_result.json"
        for content in (None, "{", "[]", json.dumps({}),
                        json.dumps({**canonical(), "changes": {}})):
            with self.subTest(content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "comparison_result.json"):
                    self.load()

    def test_duplicate_record_ids_fail_instead_of_empty_success(self):
        data = canonical()
        data["changes"].append(copy.deepcopy(data["changes"][0]))
        self.write("comparison_result.json", data)
        with self.assertRaises(ValueError):
            self.load()

    def test_explicit_failed_or_unfinished_job_is_rejected(self):
        for state in ("failed", "running", "queued", None):
            (self.root / "job_status.json").write_text(json.dumps({"state": state}), encoding="utf-8")
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "job_status.json"):
                self.load()

    def test_done_job_preserves_its_identity(self):
        (self.root / "job_status.json").write_text(json.dumps({
            "state": "done", "job_id": "a1b2c3d4e5f6"}), encoding="utf-8")
        self.assertEqual(self.load().job_id, "a1b2c3d4e5f6")

    def test_malformed_optional_context_is_not_silently_ignored(self):
        self.write("old_document_structured.json", {"elements": "not a list"})
        with self.assertRaisesRegex(ValueError, "old_document_structured.json"):
            self.load()

    def test_stage5_document_wrapper_loads_real_context(self):
        self.write("old_document_structured.json", {"document": {"elements": [
            {"element_id": "O1", "text": "Limit 10", "reading_order": 0},
        ]}})
        self.write("new_document_structured.json", {"document": {"elements": [
            {"element_id": "N1", "text": "Limit 20", "reading_order": 0},
        ]}})
        gateway = self.load()
        summary = gateway.summarize(gateway.job_id)
        self.assertNotIn("old_document_context_missing", summary["limitations"])
        self.assertNotIn("new_document_context_missing", summary["limitations"])
        detail = gateway.details(gateway.job_id, "change", "CHANGE-000001")
        self.assertIn("Limit 20", json.dumps(detail))

    def test_artifact_symlink_cannot_escape_selected_workspace(self):
        with TemporaryDirectory() as other:
            target = Path(other) / "private.json"
            target.write_text(json.dumps(canonical()), encoding="utf-8")
            path = self.root / "output" / "comparison_result.json"
            path.unlink()
            path.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "workspace"):
                self.load()

    def test_check_only_needs_no_credentials_network_or_source_writes(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(workflow, "_start_api") as start, \
             patch.object(workflow, "TiaTextClient") as tia, \
             patch.object(workflow, "_require_credentials") as creds, \
             redirect_stdout(io.StringIO()) as output:
            asyncio.run(workflow.run_selected_phase("phase2", self.root, check_only=True))
        self.assertIn("reader_check", output.getvalue())
        self.assertNotIn("Limit 20", output.getvalue())
        start.assert_not_called()
        tia.assert_not_called()
        creds.assert_not_called()

    def test_saved_chat_uses_selected_identity_without_extraction_credentials(self):
        agent = MagicMock()
        agent.ask.return_value = "bounded answer"
        with patch.dict(os.environ, {"TIA_API_KEY": "test", "LLM_MODEL": "test"}, clear=True), \
             patch.object(workflow, "TiaTextClient"), \
             patch.object(workflow, "TiaComparisonAgent", return_value=agent), \
             patch("builtins.input", side_effect=["what changed?", "quit"]), \
             redirect_stdout(io.StringIO()):
            asyncio.run(workflow.run_selected_phase("phase2", self.root))
        self.assertEqual(agent.ask.call_args.args, ("what changed?", self.load().job_id))


class PhaseSelectionTest(unittest.TestCase):
    def test_phase1_runs_engine_without_api_or_chat(self):
        with TemporaryDirectory() as tmp, \
             patch("document_compare.phase1.pipeline.cli.run_stages") as run, \
             patch.object(workflow, "TiaTextClient") as tia, \
             patch.object(workflow, "_start_api") as start:
            asyncio.run(workflow.run_selected_phase("phase1", Path(tmp)))
        self.assertEqual(run.call_args.args[0], [f"{i:02d}" for i in range(1, 16)])
        tia.assert_not_called()
        start.assert_not_called()

    def test_both_runs_engine_before_saved_reader(self):
        order = []
        with TemporaryDirectory() as tmp, \
             patch("document_compare.phase1.pipeline.cli.run_stages", side_effect=lambda *a: order.append("engine")), \
             patch.object(workflow, "chat_saved_workspace", new=AsyncMock(side_effect=lambda *a: order.append("reader"))):
            asyncio.run(workflow.run_selected_phase("both", Path(tmp)))
        self.assertEqual(order, ["engine", "reader"])

    def test_workspace_flag_wins_over_environment(self):
        with patch.dict(os.environ, {"CRR_PROJECT_ROOT": "workspace/env"}):
            self.assertEqual(workflow.resolve_workspace(Path("workspace/explicit")), Path("workspace/explicit").resolve())
            self.assertEqual(workflow.resolve_workspace(None), Path("workspace/env").resolve())

    def test_invalid_check_only_combination_rejected_before_running(self):
        with self.assertRaises(ValueError):
            asyncio.run(workflow.run_selected_phase("phase1", Path("unused"), check_only=True))

    def test_phase1_does_not_overwrite_completed_workspace(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "output"
            out.mkdir()
            (out / "comparison_result.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "workspace"):
                asyncio.run(workflow.run_selected_phase("phase1", Path(tmp)))
