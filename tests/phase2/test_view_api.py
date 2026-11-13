"""API tests for the finding-location, page-image and chat routes (offline)."""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from document_compare.phase2.api import service
from document_compare.phase2.viewer.synthetic import seed_demo_job

PNG = b"\x89PNG\r\n\x1a\n"
REC = {"kind": "change", "record_id": "CHANGE-000001"}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.job_id = seed_demo_job(cls.root)
        cls._patch = patch.object(service, "JOBS_ROOT", cls.root)
        cls._patch.start()
        cls.client = TestClient(service.app)

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        cls._tmp.cleanup()

    def _set_state(self, state):
        path = self.root / self.job_id / "job_status.json"
        original = path.read_text(encoding="utf-8")
        import json
        data = json.loads(original)
        data["state"] = state
        path.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(path.write_text, original, encoding="utf-8")

    def _page(self, side="new", page=3, **params):
        return self.client.get(f"/jobs/{self.job_id}/view/pages/{side}/{page}.png", params=params)


class ViewApiTest(_Base):
    def test_locations_ok(self):
        r = self.client.get(f"/jobs/{self.job_id}/view/locations", params=REC)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["record_id"], "CHANGE-000001")
        self.assertEqual(set(body["sides"]), {"old", "new"})

    def test_outline_ok(self):
        r = self.client.get(f"/jobs/{self.job_id}/view/outline")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"documents", "sections", "unplaced", "review", "total"})
        self.assertEqual(set(body["documents"]), {"old", "new"})
        self.assertGreater(body["total"], 0)
        self.assertTrue(body["sections"])
        self.assertEqual(set(body["sections"][0]),
                         {"section_id", "title", "side", "level", "change_count", "children", "items"})

    def test_outline_unknown_job_404_and_running_409(self):
        self.assertEqual(self.client.get("/jobs/000000000000/view/outline").status_code, 404)
        self._set_state("running")
        r = self.client.get(f"/jobs/{self.job_id}/view/outline")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["error"], "not_ready")

    def test_seeded_job_status_carries_file_names(self):
        status = self.client.get(f"/jobs/{self.job_id}/status").json()
        self.assertEqual(status["old_name"], "internal_policy_report_old.pdf")
        self.assertEqual(status["new_name"], "internal_policy_report_new.pdf")

    def test_page_png_ok(self):
        r = self._page()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(r.content.startswith(PNG))

    def test_page_png_with_highlight_differs_from_plain(self):
        plain = self._page()
        marked = self._page(**REC)
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertTrue(marked.content.startswith(PNG))
        self.assertNotEqual(plain.content, marked.content)

        # A page without regions for this finding is rendered exactly like the plain page.
        located = self.client.get(f"/jobs/{self.job_id}/view/locations", params=REC).json()
        region = located["sides"]["new"]["regions"][0]
        self.assertEqual(region["page_number"], 3)
        self.assertEqual(self._page(page=1, **REC).content, self._page(page=1).content)

        # On the highlighted page only the region is tinted.
        poly = region["polygon"]
        plain_pix, marked_pix = pymupdf.Pixmap(plain.content), pymupdf.Pixmap(marked.content)
        self.assertEqual((plain_pix.width, plain_pix.height), (marked_pix.width, marked_pix.height))
        cx = int(sum(poly[0::2]) / 4 * marked_pix.width)
        cy = int(sum(poly[1::2]) / 4 * marked_pix.height)
        self.assertNotEqual(plain_pix.pixel(cx, cy), marked_pix.pixel(cx, cy))
        corner = (5, 5)
        self.assertEqual(plain_pix.pixel(*corner), marked_pix.pixel(*corner))

    def test_unreadable_source_pdf_is_422_invalid_artifact(self):
        job_id = seed_demo_job(self.root)
        (self.root / job_id / "input" / "document" / "old.pdf").write_bytes(b"this is not a pdf at all")
        r = self.client.get(f"/jobs/{job_id}/view/pages/old/1.png")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"], {
            "error": "invalid_artifact", "message": "The source PDF could not be read."})
        # The other side of the same job is still readable.
        self.assertEqual(self.client.get(f"/jobs/{job_id}/view/pages/new/1.png").status_code, 200)

    def test_unknown_job_404(self):
        r = self.client.get("/jobs/000000000000/view/locations", params=REC)
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/jobs/000000000000/view/pages/old/1.png")
        self.assertEqual(r.status_code, 404)

    def test_running_job_409(self):
        self._set_state("running")
        self.assertEqual(self.client.get(f"/jobs/{self.job_id}/view/locations", params=REC).status_code, 409)
        self.assertEqual(self._page().status_code, 409)

    def test_unknown_record_404(self):
        r = self.client.get(f"/jobs/{self.job_id}/view/locations",
                            params={"kind": "change", "record_id": "CHANGE-999999"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"], "not_found")

    def test_page_out_of_range_422(self):
        for page in (0, 99):
            r = self._page(page=page)
            self.assertEqual(r.status_code, 422, page)

    def test_bad_scale_422(self):
        for scale in ("0.1", "9", "nan", "abc"):
            self.assertEqual(self._page(scale=scale).status_code, 422, scale)

    def test_kind_without_record_id_422(self):
        self.assertEqual(self._page(kind="change").status_code, 422)
        self.assertEqual(self._page(record_id="CHANGE-000001").status_code, 422)

    def test_side_must_be_old_or_new_422(self):
        self.assertEqual(self._page(side="newer").status_code, 422)
        self.assertIn(self._page(side="..").status_code, (404, 422))

    def test_record_id_traversal_rejected(self):
        for rid in ("../../job_status", "..%2F..%2Fjob_status", "CHANGE-000001/../x"):
            r = self.client.get(f"/jobs/{self.job_id}/view/locations",
                                params={"kind": "change", "record_id": rid})
            self.assertIn(r.status_code, (404, 422), rid)
        r = self._page(kind="change", record_id="../../job_status")
        self.assertIn(r.status_code, (404, 422))

    def test_missing_source_pdf_404_code(self):
        pdf = self.root / self.job_id / "input" / "document" / "new.pdf"
        backup = pdf.with_suffix(".bak")
        shutil.move(pdf, backup)
        self.addCleanup(shutil.move, backup, pdf)
        r = self._page()
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"], "source_pdf_unavailable")

    def test_existing_routes_unchanged(self):
        before = {
            "/jobs", "/jobs/{job_id}", "/jobs/{job_id}/status", "/jobs/{job_id}/summary",
            "/jobs/{job_id}/report", "/jobs/{job_id}/artifacts", "/jobs/{job_id}/artifacts/{name}",
            "/jobs/{job_id}/changes", "/jobs/{job_id}/changes/{change_id}",
            "/jobs/{job_id}/review-queue", "/jobs/{job_id}/review-queue.csv",
            "/jobs/{job_id}/read/summary", "/jobs/{job_id}/read/search", "/jobs/{job_id}/read/details",
        }
        after = set(service.app.openapi()["paths"])
        self.assertTrue(before <= after, before - after)


class CreateJobNamesTest(unittest.TestCase):
    def test_display_name_strips_paths_and_control_characters(self):
        self.assertEqual(service._display_name("../x/Old\x07 \x1bpolicy\x7f.pdf"), "Old policy.pdf")
        self.assertEqual(service._display_name("a\\b\\c.pdf"), "c.pdf")
        self.assertEqual(len(service._display_name("n" * 300)), 120)

    def test_create_job_stores_cleaned_upload_names(self):
        with TemporaryDirectory() as tmp, patch.object(service, "JOBS_ROOT", Path(tmp)), \
                patch.object(service, "_run_pipeline"):
            client = TestClient(service.app)
            long_name = "n" * 200 + ".pdf"
            r = client.post("/jobs", files={
                "old_pdf": ("C:\\docs\\Old policy.pdf", b"%PDF-1.4 old", "application/pdf"),
                "new_pdf": (long_name, b"%PDF-1.4 new", "application/pdf"),
            }, data={"session_id": "sess_1"})
            self.assertIn(r.status_code, (200, 201, 202), r.text)
            body = r.json()
            self.assertEqual(body["old_name"], "Old policy.pdf")
            self.assertEqual(body["new_name"], long_name[:120])
            self.assertEqual(len(body["new_name"]), 120)
            stored = client.get(f"/jobs/{body['job_id']}/status").json()
            self.assertEqual((stored["old_name"], stored["new_name"]), (body["old_name"], body["new_name"]))


class ChatApiTest(_Base):
    def _chat(self, question="What changed?"):
        return self.client.post(f"/jobs/{self.job_id}/chat", json={"question": question})

    @staticmethod
    def _planner(strategy):
        from document_compare.phase2.agent.mock_reader import ScriptedPlanner
        return ScriptedPlanner(strategy)

    @staticmethod
    def _summarize_then_final(observations):
        if not observations:
            return {"action": "summarize", "arguments": {}}
        return {"action": "final", "answer": "Three changes were found."}

    def test_chat_not_configured_503(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TIA_API_KEY", "LLM_MODEL")}
        with patch.dict(os.environ, env, clear=True):
            r = self._chat()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"]["error"], "chat_not_configured")

    def test_chat_answers_with_scripted_planner(self):
        planner = self._planner(self._summarize_then_final)
        with patch.object(service, "_chat_client", return_value=planner):
            r = self._chat()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["job_id"], self.job_id)
        self.assertEqual(body["answer"], "Three changes were found.")
        self.assertIsInstance(body["limitations"], list)

    def test_chat_uses_only_reader_tools(self):
        from document_compare.phase2.workflow import saved_workspace
        calls = []
        real = saved_workspace.SavedWorkspaceGateway

        class Recording(real):
            def summarize(self, job_id):
                calls.append(("summarize", job_id)); return super().summarize(job_id)

            def search(self, job_id, **f):
                calls.append(("search", job_id)); return super().search(job_id, **f)

            def details(self, job_id, kind, record_id, **o):
                calls.append(("details", job_id)); return super().details(job_id, kind, record_id, **o)

        def strategy(obs):
            steps = [
                {"action": "summarize", "arguments": {}},
                {"action": "search", "arguments": {"limit": 2}},
                {"action": "details", "arguments": {"kind": "change", "record_id": "CHANGE-000001"}},
            ]
            if len(obs) < 3:
                return steps[len(obs)]
            return {"action": "final", "answer": "done"}

        with patch.object(service, "_chat_client", return_value=self._planner(strategy)), \
                patch.object(saved_workspace, "SavedWorkspaceGateway", Recording):
            r = self._chat()
        self.assertEqual(r.status_code, 200, r.text)
        names = {n for n, _ in calls}
        self.assertTrue(names)
        self.assertLessEqual(names, {"summarize", "search", "details"})
        self.assertTrue(all(j == self.job_id for _, j in calls))

    def test_chat_step_limit_502(self):
        planner = self._planner(lambda obs: {"action": "summarize", "arguments": {}})
        with patch.object(service, "_chat_client", return_value=planner):
            r = self._chat()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"]["error"], "chat_failed")
        self.assertNotIn("summarize", r.text)

    def test_chat_empty_or_long_question_422(self):
        planner = self._planner(self._summarize_then_final)
        with patch.object(service, "_chat_client", return_value=planner):
            for q in ("", "   \n", "x" * 2001):
                self.assertEqual(self._chat(q).status_code, 422, len(q))
            self.assertEqual(self.client.post(f"/jobs/{self.job_id}/chat", json={}).status_code, 422)
            self.assertEqual(self._chat("x" * 2000).status_code, 200)
        self.assertEqual(planner.decisions[-1]["action"], "final")

    def test_chat_running_job_409(self):
        self._set_state("running")
        planner = self._planner(self._summarize_then_final)
        with patch.object(service, "_chat_client", return_value=planner):
            r = self._chat()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(planner.decisions, [])

    def test_chat_does_not_import_sdk_when_unconfigured(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TIA_API_KEY", "LLM_MODEL")}
        sys.modules.pop("bbk_ai_hub_sdk", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(service._chat_client())
            self._chat()
        self.assertNotIn("bbk_ai_hub_sdk", sys.modules)

    def test_chat_client_built_only_when_both_env_set(self):
        with patch.dict(os.environ, {"TIA_API_KEY": "k", "LLM_MODEL": "m"}):
            client = service._chat_client()
        self.assertEqual(type(client).__name__, "TiaTextClient")
        self.assertNotIn("bbk_ai_hub_sdk", sys.modules)


if __name__ == "__main__":
    unittest.main()
