import asyncio, os, subprocess, unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock, AsyncMock
from document_compare.phase2.workflow.local_reviewer import (
    list_input_pdfs, prompt_for_two_pdfs, validate_pdf_filename,
    _require_credentials, _api_base, submit_and_poll, format_summary, _start_api, _stop_process)

def _pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4\n%%EOF")
    return path

class CredentialMatrixTest(unittest.TestCase):
    def _env(self, **values):
        base = {"TIA_API_KEY": "x", "TIA_AZURE_ENDPOINT": "http://x"}
        base.update(values)
        return base
    def test_default_provider_bbk_uses_tia_key(self):
        with patch.dict(os.environ, self._env(), clear=True):
            _require_credentials()
    def test_bbk_does_not_require_hub_key_when_tia_present(self):
        with patch.dict(os.environ, self._env(LLM_PROVIDER="bbk_ai_hub", BBK_AI_HUB_API_KEY=""), clear=True):
            _require_credentials()
    def test_bbk_cannot_replace_tia_key(self):
        env = self._env(LLM_PROVIDER="bbk_ai_hub", BBK_AI_HUB_API_KEY="k")
        del env["TIA_API_KEY"]
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                _require_credentials()
    def test_openai_requires_key_and_model(self):
        with patch.dict(os.environ, self._env(LLM_PROVIDER="openai_compatible", OPENAI_API_KEY=""), clear=True):
            with self.assertRaises(SystemExit):
                _require_credentials()
    def test_openai_accepts_key_and_model(self):
        with patch.dict(os.environ, self._env(LLM_PROVIDER="openai_compatible", OPENAI_API_KEY="k", LLM_MODEL="m"), clear=True):
            _require_credentials()
    def test_unsupported_provider_raises(self):
        with patch.dict(os.environ, self._env(LLM_PROVIDER="unknown"), clear=True):
            with self.assertRaises(RuntimeError):
                _require_credentials()

class ApiBaseTest(unittest.TestCase):
    def test_default_loopback_and_port(self):
        with patch.dict(os.environ, {"CRR_API_BASE": "http://127.0.0.1:8321"}, clear=True):
            base, port = _api_base()
            self.assertEqual(base, "http://127.0.0.1:8321")
            self.assertEqual(port, 8321)
    def test_respects_configured_port(self):
        with patch.dict(os.environ, {"CRR_API_BASE": "http://localhost:9000"}, clear=True):
            base, port = _api_base()
            self.assertEqual(port, 9000)
            self.assertIn("localhost", base)
    def test_rejects_non_loopback(self):
        with patch.dict(os.environ, {"CRR_API_BASE": "http://192.168.1.1:8321"}, clear=True):
            with self.assertRaises(ValueError):
                _api_base()
    def test_rejects_https(self):
        with patch.dict(os.environ, {"CRR_API_BASE": "https://127.0.0.1:8321"}, clear=True):
            with self.assertRaises(ValueError):
                _api_base()
    def test_rejects_path_query_fragment(self):
        for bad in ("http://127.0.0.1:8321/path", "http://127.0.0.1:8321?query=1", "http://127.0.0.1:8321#frag"):
            with patch.dict(os.environ, {"CRR_API_BASE": bad}, clear=True):
                with self.assertRaises(ValueError, msg=bad):
                    _api_base()

class ValidateTest(unittest.TestCase):
    def test_rejects_bad_names(self):
        with TemporaryDirectory() as d:
            root = Path(d); _pdf(root / "a.pdf")
            for bad in ("/etc/passwd", "../a.pdf", "a.txt", "sub/a.pdf", ""):
                with self.assertRaises(ValueError, msg=bad):
                    validate_pdf_filename(root, bad)
    def test_rejects_bad_signature_and_oversize(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "bad.pdf").write_bytes(b"not a pdf")
            with self.assertRaises(ValueError):
                validate_pdf_filename(root, "bad.pdf")
            (root / "big.pdf").write_bytes(b"%PDF-" + b"0" * 32)
            with self.assertRaises(ValueError):
                validate_pdf_filename(root, "big.pdf", max_bytes=8)
    def test_accepts_valid_pdf(self):
        with TemporaryDirectory() as d:
            root = Path(d); _pdf(root / "a.pdf")
            self.assertEqual(validate_pdf_filename(root, "a.pdf"), (root / "a.pdf").resolve())
    def test_prompt_reprompts_and_requires_distinct(self):
        with TemporaryDirectory() as d:
            root = Path(d); _pdf(root / "a.pdf"); _pdf(root / "b.pdf")
            answers = iter(["a.pdf", "a.pdf", "b.pdf"])
            old, new = prompt_for_two_pdfs(root, input_fn=lambda _="": next(answers))
            self.assertNotEqual(old, new)
    def test_list_input_pdfs_sorted_and_casefold(self):
        with TemporaryDirectory() as d:
            root = Path(d); _pdf(root / "b.pdf"); _pdf(root / "a.PDF")
            self.assertEqual([p.name for p in list_input_pdfs(root)], ["a.PDF", "b.pdf"])
    def test_symlink_escape_skipped_if_unsupported(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            target = root.parent / "outside.pdf"
            link = root / "link.pdf"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                raise self.skipTest(f"symlinks not supported: {exc}")
            _pdf(target)
            with self.assertRaises(ValueError):
                validate_pdf_filename(root, "link.pdf")

class SubmitAndPollTest(unittest.TestCase):
    def test_calls_exact_tools_and_returns_job_id(self):
        async def run():
            client = MagicMock()
            client.call_tool = AsyncMock(side_effect=[
                MagicMock(data={"job_id": "a1b2c3d4e5f6"}),
                MagicMock(data={"state": "done", "stages_completed": ["01"]}),
            ])
            job_id, status = await submit_and_poll(client, Path("/in/old.pdf"), Path("/in/new.pdf"), "s1")
            self.assertEqual(job_id, "a1b2c3d4e5f6")
            self.assertEqual(status["state"], "done")
            calls = client.call_tool.call_args_list
            self.assertEqual(calls[0].args[0], "submit_comparison")
            self.assertEqual(calls[0].args[1]["old_pdf_path"], "/in/old.pdf")
            self.assertEqual(calls[0].args[1]["new_pdf_path"], "/in/new.pdf")
            self.assertEqual(calls[0].args[1]["session_id"], "s1")
            self.assertEqual(calls[1].args[0], "check_status")
            self.assertEqual(calls[1].args[1]["job_id"], "a1b2c3d4e5f6")
        asyncio.run(run())
    def test_failed_raises_with_error_tail(self):
        async def run():
            client = MagicMock()
            client.call_tool = AsyncMock(side_effect=[
                MagicMock(data={"job_id": "a1b2c3d4e5f6"}),
                MagicMock(data={"state": "failed", "error_tail": "boom", "stages_completed": []}),
            ])
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await submit_and_poll(client, Path("/in/old.pdf"), Path("/in/new.pdf"), "s1")
        asyncio.run(run())

class StdioReaderGatewayTest(unittest.TestCase):
    def test_exact_mapping_and_job_id(self):
        async def run():
            from document_compare.phase2.agent.gateway import StdioReaderGateway
            loop = asyncio.get_running_loop()
            client = MagicMock()
            client.call_tool = AsyncMock(return_value=MagicMock(data={"state": "running"}))
            gateway = StdioReaderGateway(client, loop)
            status = await asyncio.to_thread(gateway.summarize, "a1b2c3d4e5f6")
            report = await asyncio.to_thread(gateway.search, "a1b2c3d4e5f6")
            structured = await asyncio.to_thread(gateway.details, "a1b2c3d4e5f6", "change", "CHANGE-000001")
            self.assertEqual(status, {"state": "running"})
            self.assertEqual(report, {"state": "running"})
            self.assertEqual(structured, {"state": "running"})
            calls = client.call_tool.call_args_list
            self.assertEqual(calls[0].args[0], "summarize")
            self.assertEqual(calls[0].args[1], {"job_id": "a1b2c3d4e5f6"})
            self.assertEqual(calls[1].args[0], "search")
            self.assertEqual(calls[2].args[0], "details")
        asyncio.run(run())

class ChatIntegrationTest(unittest.TestCase):
    def test_chat_asks_agent_with_question_and_job_id(self):
        async def run():
            from document_compare.phase2.workflow.local_reviewer import main
            with TemporaryDirectory() as d:
                root = Path(d)
                (root / "input").mkdir()
                _pdf(root / "input" / "old.pdf")
                _pdf(root / "input" / "new.pdf")
                with patch("document_compare.phase2.workflow.local_reviewer._require_credentials"), \
                     patch.dict(os.environ, {"TIA_API_KEY": "test-key"}), \
                     patch("document_compare.phase2.workflow.local_reviewer.PythonStdioTransport"), \
                     patch("document_compare.phase2.workflow.local_reviewer.prompt_for_two_pdfs") as mock_prompt, \
                     patch("document_compare.phase2.workflow.local_reviewer._start_api") as mock_start, \
                     patch("document_compare.phase2.workflow.local_reviewer._api_base", return_value=("http://127.0.0.1:8321", 8321)), \
                     patch("document_compare.phase2.workflow.local_reviewer.Client") as MockClient, \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaComparisonAgent") as MockAgent, \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaTextClient") as MockTia, \
                      patch("document_compare.phase2.workflow.local_reviewer.StdioReaderGateway") as MockGateway, \
                     patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
                     patch("builtins.input", side_effect=["what changed?", "quit"]):
                     mock_prompt.return_value = (root / "input" / "old.pdf", root / "input" / "new.pdf")
                     mock_proc = MagicMock()
                     mock_proc.poll.return_value = None
                     mock_proc.wait.return_value = 0
                     mock_start.return_value = mock_proc
                     mock_client = AsyncMock()

                     mock_client.call_tool = AsyncMock(return_value=MagicMock(data={
                         "job_id": "a1b2c3d4e5f6",
                         "state": "done",
                         "stages_completed": [f"{i:02d}" for i in range(1, 16)],
                     }))
                     MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                     MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
                     mock_agent = MagicMock()
                     mock_agent.ask = MagicMock(return_value="nothing important")
                     MockAgent.return_value = mock_agent
                     await main(root)
                     mock_agent.ask.assert_called_once_with("what changed?", "a1b2c3d4e5f6")
                     mock_proc.terminate.assert_called_once()
        asyncio.run(run())

class ApiProcessTest(unittest.TestCase):
    def test_cleanup_terminates_and_kills_on_timeout(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 10), 0]
        with patch("subprocess.Popen", return_value=proc), \
             patch("httpx.get", side_effect=RuntimeError("no server")):
            with self.assertRaises(RuntimeError):
                _start_api({}, Path("/tmp"), "http://127.0.0.1:8321", 8321)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

class FormatSummaryTest(unittest.TestCase):
    def test_format_summary_lines(self):
        summary = {
            "progress": {"state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)], "duration_seconds": 12.345},
            "documents": {"old": {"pages": 2, "elements": 10}, "new": {"pages": 3, "elements": 11}},
            "extraction": {
                "verdict": "PASS",
                "old": {"status": "PASS", "warning_count": 0, "issue_count": 0},
                "new": {"status": "PASS", "warning_count": 1, "issue_count": 0},
            },
            "changes": {"modified": 1, "added": 0, "deleted": 1, "table": 0},
            "disposition": {"accepted": 1, "review": 1, "suppressed": 0},
            "matching": {"unchanged": 5, "modified": 1, "old_unmatched": 0, "new_unmatched": 0},
            "tables": {"changes": 1, "unresolved_reviews": 0},
            "evaluator": {"provider": "openai", "mode": "mock"},
        }
        text = format_summary(summary)
        self.assertIn("Current Document Health", text)
        self.assertIn("progress: 15/15 stages", text)
        self.assertIn("extraction: verdict=PASS", text)
        self.assertIn("changes: modified=1, added=0, deleted=1, table=0", text)
        self.assertIn("disposition: accepted=1, review=1, suppressed=0", text)
        self.assertIn("matching: unchanged=5", text)
        self.assertIn("tables: changes=1, unresolved=0", text)
        self.assertIn("evaluator: provider=openai, mode=mock", text)

class WorkspacePreservationTest(unittest.TestCase):
    def test_main_does_not_delete_workspace(self):
        async def run():
            from document_compare.phase2.workflow import local_reviewer
            with TemporaryDirectory() as d:
                root = Path(d)
                (root / "input").mkdir()
                _pdf(root / "input" / "old.pdf")
                _pdf(root / "input" / "new.pdf")
                (root / "workspace").mkdir()
                with patch.object(local_reviewer, "REPO_ROOT", root), \
                     patch("document_compare.phase2.workflow.local_reviewer._require_credentials"), \
                     patch.dict(os.environ, {"TIA_API_KEY": "test-key"}), \
                     patch("document_compare.phase2.workflow.local_reviewer.PythonStdioTransport"), \
                     patch("document_compare.phase2.workflow.local_reviewer.prompt_for_two_pdfs") as mock_prompt, \
                     patch("document_compare.phase2.workflow.local_reviewer._start_api") as mock_start, \
                     patch("document_compare.phase2.workflow.local_reviewer.Client") as MockClient, \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaComparisonAgent"), \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaTextClient"), \
                      patch("document_compare.phase2.workflow.local_reviewer.StdioReaderGateway"), \
                     patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
                     patch("builtins.input", side_effect=["quit"]), \
                     patch("shutil.rmtree") as mock_rmtree:
                     mock_prompt.return_value = (root / "input" / "old.pdf", root / "input" / "new.pdf")
                     mock_proc = MagicMock()
                     mock_proc.poll.return_value = None
                     mock_proc.wait.return_value = 0
                     mock_start.return_value = mock_proc
                     mock_client = AsyncMock()
                     mock_client.call_tool = AsyncMock(return_value=MagicMock(data={
                         "job_id": "a1b2c3d4e5f6", "state": "done",
                         "stages_completed": [f"{i:02d}" for i in range(1, 16)]}))
                     MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                     MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
                     await local_reviewer.main(root)
                     for call in mock_rmtree.call_args_list:
                         self.assertNotIn("workspace", str(call.args[0]))
        asyncio.run(run())

class RedlineUrlTest(unittest.TestCase):
    def test_redline_url_exact(self):
        from document_compare.phase2.workflow import local_reviewer
        captured: list[str] = []
        async def run():
            with TemporaryDirectory() as d:
                root = Path(d)
                (root / "input").mkdir()
                _pdf(root / "input" / "old.pdf")
                _pdf(root / "input" / "new.pdf")
                with patch.object(local_reviewer, "REPO_ROOT", root), \
                     patch("document_compare.phase2.workflow.local_reviewer._require_credentials"), \
                     patch.dict(os.environ, {"TIA_API_KEY": "test-key"}), \
                     patch("document_compare.phase2.workflow.local_reviewer.PythonStdioTransport"), \
                     patch("document_compare.phase2.workflow.local_reviewer.prompt_for_two_pdfs") as mock_prompt, \
                     patch("document_compare.phase2.workflow.local_reviewer._start_api") as mock_start, \
                     patch("document_compare.phase2.workflow.local_reviewer._api_base", return_value=("http://127.0.0.1:8321", 8321)), \
                     patch("document_compare.phase2.workflow.local_reviewer.Client") as MockClient, \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaComparisonAgent"), \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaTextClient"), \
                      patch("document_compare.phase2.workflow.local_reviewer.StdioReaderGateway"), \
                     patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
                     patch("builtins.input", side_effect=["quit"]), \
                     patch("builtins.print", side_effect=lambda s: captured.append(str(s))):
                     mock_prompt.return_value = (root / "input" / "old.pdf", root / "input" / "new.pdf")
                     mock_proc = MagicMock()
                     mock_proc.poll.return_value = None
                     mock_proc.wait.return_value = 0
                     mock_start.return_value = mock_proc
                     mock_client = AsyncMock()
                     mock_client.call_tool = AsyncMock(return_value=MagicMock(data={
                         "job_id": "a1b2c3d4e5f6", "state": "done",
                         "stages_completed": [f"{i:02d}" for i in range(1, 16)]}))
                     MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                     MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
                     await local_reviewer.main(root)
        asyncio.run(run())
        self.assertTrue(any("http://127.0.0.1:8321/jobs/a1b2c3d4e5f6/artifacts/comparison_redline.pdf" in line for line in captured))

class SessionIdTest(unittest.TestCase):
    def test_main_generates_distinct_nonlocal_session_id(self):
        async def run():
            from document_compare.phase2.workflow import local_reviewer
            captured: list[str] = []
            async def fake_submit_and_poll(client, old_pdf_path, new_pdf_path, session_id, *, poll_seconds=3.0):
                captured.append(session_id)
                return "a1b2c3d4e5f6", {"state": "done"}
            summary = {
                "progress": {"state": "done", "stages_completed": [f"{i:02d}" for i in range(1, 16)], "duration_seconds": 0.0},
                "documents": {"old": {"pages": 1, "elements": 1}, "new": {"pages": 1, "elements": 1}},
                "extraction": {"verdict": "PASS", "old": {"status": "PASS", "warning_count": 0, "issue_count": 0}, "new": {"status": "PASS", "warning_count": 0, "issue_count": 0}},
                "changes": {"modified": 0, "added": 0, "deleted": 0, "table": 0},
                "disposition": {"accepted": 0, "review": 0, "suppressed": 0},
                "matching": {"unchanged": 0, "modified": 0, "old_unmatched": 0, "new_unmatched": 0},
                "tables": {"changes": 0, "unresolved_reviews": 0},
                "evaluator": {"provider": "openai", "mode": "mock"},
            }
            with TemporaryDirectory() as d:
                root = Path(d)
                (root / "input").mkdir()
                _pdf(root / "input" / "old.pdf")
                _pdf(root / "input" / "new.pdf")
                with patch("document_compare.phase2.workflow.local_reviewer._require_credentials"), \
                     patch.dict(os.environ, {"TIA_API_KEY": "test-key"}), \
                     patch("document_compare.phase2.workflow.local_reviewer.PythonStdioTransport"), \
                     patch("document_compare.phase2.workflow.local_reviewer.prompt_for_two_pdfs") as mock_prompt, \
                     patch("document_compare.phase2.workflow.local_reviewer._start_api") as mock_start, \
                     patch("document_compare.phase2.workflow.local_reviewer.Client") as MockClient, \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaComparisonAgent"), \
                     patch("document_compare.phase2.workflow.local_reviewer.TiaTextClient"), \
                      patch("document_compare.phase2.workflow.local_reviewer.StdioReaderGateway"), \
                     patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
                     patch("builtins.input", side_effect=["quit", "quit"]), \
                     patch("document_compare.phase2.workflow.local_reviewer.submit_and_poll", side_effect=fake_submit_and_poll):
                    mock_prompt.return_value = (root / "input" / "old.pdf", root / "input" / "new.pdf")
                    mock_proc = MagicMock()
                    mock_proc.poll.return_value = None
                    mock_proc.wait.return_value = 0
                    mock_start.return_value = mock_proc
                    mock_client = AsyncMock()
                    mock_client.call_tool = AsyncMock(return_value=MagicMock(data=summary))
                    MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
                    await local_reviewer.main(root)
                    await local_reviewer.main(root)
            self.assertEqual(len(captured), 2)
            for sid in captured:
                self.assertNotEqual(sid, "local")
                self.assertRegex(sid, r"^[A-Za-z0-9_-]{1,128}$")
            self.assertNotEqual(captured[0], captured[1])
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
