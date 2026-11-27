"""Exercise the eval CLI through ApiClient with a synthetic in-process transport."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from document_compare.phase2.ui.api_client import ApiClient
from tools import agent_eval


class AgentEvalTest(unittest.TestCase):
    def test_default_questions_write_full_report_but_only_counts_to_stdout(self):
        job = "0123456789ab"
        requests = []
        citation = {"kind": "change", "record_id": "CHANGE-000002"}

        def handle(request):
            if request.method == "GET":
                self.assertEqual(request.url.path, f"/jobs/{job}/view/outline")
                return httpx.Response(200, json={"sections": [{
                    "items": [{"kind": "change", "record_id": "CHANGE-000001"}],
                    "children": [{"items": [{**citation, "impact": {"level": "content", "priority": "high"}}]}],
                }]})
            self.assertEqual(request.url.path, f"/jobs/{job}/chat/stream")
            requests.append(json.loads(request.content))
            events = [
                {"type": "step", "step": 1, "action": "search", "query": "Frist", "record_id": None},
                {"type": "fallback", "query": "Fristen", "variant": "Frist"},
                {"type": "final", "answer": "Synthetic answer text.", "cited": [citation],
                 "unverified_ids": ["CHANGE-999999"], "limitations": ["partial_answer"],
                 "suggestions": ["Welche Frist?"]},
            ]
            return httpx.Response(200, text="\n".join(json.dumps(event) for event in events))

        with TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch.object(agent_eval, "ApiClient", side_effect=lambda base: ApiClient(base, httpx.MockTransport(handle))), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(agent_eval.main(["--job", job, "--out", directory]), 0)
            report, = Path(directory).glob("*.md")
            self.assertTrue(report.stem.endswith("Z"))
            text = report.read_text(encoding="utf-8")
        self.assertEqual(len(requests), 10)
        self.assertEqual(requests[5]["question"], "Und welche davon sind noch offen?")
        self.assertEqual(requests[5]["history"][-2]["content"], "Was hat sich bei der Auslagerung geändert?")
        self.assertEqual(len(requests[5]["history"]), 6)
        self.assertEqual(requests[6]["context"], {"selected_record_id": citation["record_id"], "selected_kind": "change"})
        self.assertEqual(requests[-1]["question"], "Was ist CHANGE-000001?")
        for expected in ("Latency:", "Step events:", '"type": "fallback"', "Synthetic answer text.",
                         "CHANGE-000002", "CHANGE-999999", "partial_answer", "Welche Frist?"):
            self.assertIn(expected, text)
        self.assertEqual(len(stdout.getvalue().splitlines()), 12)
        self.assertRegex(stdout.getvalue(), r"\| 1 \| \d+\.\d{3} \| 1 \| 1 \| 1 \| yes \|")
        self.assertNotIn("Synthetic answer text.", stdout.getvalue())
        self.assertNotIn("Welche Frist?", stdout.getvalue())

    def test_rejects_remote_base_before_creating_client(self):
        with patch.object(agent_eval, "ApiClient") as client, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                agent_eval.main(["--job", "0123456789ab", "--api", "http://example.org"])
        self.assertEqual(error.exception.code, 2)
        client.assert_not_called()
