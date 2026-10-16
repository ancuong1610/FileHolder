"""Agent + reader-MCP + HTTP reader integration tests.

Truth is authored independently here and in ``document_compare.phase2.agent.mock_reader`` (a fixed
dictionary written before any query runs); expected values are never derived
from a reader response. These tests assert literals against raw reader
responses from the real transport, not against mock behaviour.
"""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

from document_compare.phase2.agent import mock_reader as mr

ROOT = Path(__file__).resolve().parents[2]
JOB = "a1b2c3d4e5f6"


def _child_env(api_base: str) -> dict:
    return {
        **os.environ,
        "FASTMCP_CHECK_FOR_UPDATES": "off",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_LOG_LEVEL": "WARNING",
        "CRR_API_BASE": api_base,
        "CRR_MCP_UPLOAD_ROOT": str(ROOT / "input"),
        "CRR_PROJECT_ROOT": str(ROOT / "workspace"),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")])),
    }


class _RecordingGateway:
    """Minimal ReaderGateway that records forwarded arguments."""

    def __init__(self):
        self.calls = []

    def summarize(self, job_id):
        self.calls.append(("summarize", {"job_id": job_id}))
        return {"job_id": job_id, "state": "done"}

    def search(self, job_id, **filters):
        self.calls.append(("search", {"job_id": job_id, **filters}))
        return {"total_matches": 0}

    def details(self, job_id, kind, record_id, **options):
        self.calls.append(("details", {"job_id": job_id, "kind": kind, "record_id": record_id, **options}))
        return {"kind": kind, "record_id": record_id}


class AgentToolsContractTest(unittest.TestCase):
    def test_planner_receives_current_tool_argument_schemas(self):
        from document_compare.phase2.agent.controller import TiaComparisonAgent

        class CapturePlanner:
            def invoke(self, prompt, system):
                self.prompt = prompt
                return json.dumps({"action": "final", "answer": "schema capture"})

        planner = CapturePlanner()
        agent = TiaComparisonAgent(planner, _RecordingGateway())
        agent._plan({"question": "Inspect the result", "job_id": JOB, "observations": []})
        self.assertIn("TOOL_SCHEMAS_JSON:\n", planner.prompt)
        schema_text = planner.prompt.split("TOOL_SCHEMAS_JSON:\n", 1)[1]
        schemas, _ = json.JSONDecoder().raw_decode(schema_text)
        self.assertEqual(set(schemas), {"summarize", "search", "details"})
        properties = schemas["details"]["properties"]
        for argument in ("cell_offset", "cell_limit", "text_offset", "text_limit", "context_source_offset"):
            self.assertIn(argument, properties)
        self.assertNotIn("submit_comparison", schemas)


    def test_existing_nonempty_output_is_rejected_without_deleting_files(self):
        with TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            out.mkdir()
            sentinel = out / "keep.txt"
            sentinel.write_text("preserve user data")
            with self.assertRaises(SystemExit):
                mr._prepare_out_dir(out)
            self.assertEqual(sentinel.read_text(), "preserve user data")

    def test_output_symlink_is_rejected_without_touching_target(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir()
            out = Path(directory) / "out"
            out.symlink_to(target, target_is_directory=True)
            with self.assertRaises(SystemExit):
                mr._prepare_out_dir(out)
            self.assertTrue(out.is_symlink())

    def test_exactly_three_read_tools(self):
        from document_compare.phase2.agent.tools import build_tools

        tools = build_tools(_RecordingGateway(), result_limit=7)
        self.assertEqual(set(tools), {"summarize", "search", "details"})

    def test_search_binds_result_limit_not_planner_argument(self):
        from document_compare.phase2.agent.tools import build_tools

        gateway = _RecordingGateway()
        tools = build_tools(gateway, result_limit=7)
        tools["search"].invoke({"job_id": JOB, "query": "fee"})
        name, arguments = gateway.calls[-1]
        self.assertEqual(name, "search")
        self.assertEqual(arguments["job_id"], JOB)
        self.assertEqual(arguments["query"], "fee")
        self.assertEqual(arguments["limit"], 7)

    def test_details_forwards_cell_window_for_table_pagination(self):
        from document_compare.phase2.agent.tools import build_tools

        gateway = _RecordingGateway()
        tools = build_tools(gateway)
        tools["details"].invoke(
            {
                "job_id": JOB,
                "kind": "table",
                "record_id": "TABLE-CHANGE-00001",
                "cell_offset": 2,
                "cell_limit": 3,
            }
        )
        name, arguments = gateway.calls[-1]
        self.assertEqual(name, "details")
        self.assertEqual(arguments["cell_offset"], 2)
        self.assertEqual(arguments["cell_limit"], 3)

    def test_details_defaults_cell_window_when_omitted(self):
        from document_compare.phase2.agent.tools import build_tools

        gateway = _RecordingGateway()
        tools = build_tools(gateway)
        tools["details"].invoke(
            {"job_id": JOB, "kind": "change", "record_id": "CHANGE-000001"}
        )
        _, arguments = gateway.calls[-1]
        self.assertEqual(arguments["cell_offset"], 0)
        self.assertEqual(arguments["cell_limit"], 20)


class ReaderGatewayTransportTest(unittest.TestCase):
    def test_stdio_reader_gateway_exact_names_and_arguments(self):
        async def run():
            from unittest.mock import AsyncMock, MagicMock

            from document_compare.phase2.agent.gateway import StdioReaderGateway

            loop = asyncio.get_running_loop()
            client = MagicMock()
            client.call_tool = AsyncMock(return_value=MagicMock(data={"ok": True}))
            gateway = StdioReaderGateway(client, loop)
            await asyncio.to_thread(gateway.summarize, JOB)
            await asyncio.to_thread(gateway.search, JOB, query="q", limit=3)
            await asyncio.to_thread(
                gateway.details, JOB, "table", "T1", cell_offset=2, cell_limit=2
            )
            calls = client.call_tool.call_args_list
            self.assertEqual([c.args[0] for c in calls], ["summarize", "search", "details"])
            self.assertEqual(calls[0].args[1], {"job_id": JOB})
            self.assertEqual(calls[1].args[1]["limit"], 3)
            self.assertEqual(calls[2].args[1]["cell_offset"], 2)
            self.assertEqual(calls[2].args[1]["cell_limit"], 2)

        asyncio.run(run())

    def test_await_future_timeout_cancels(self):
        from document_compare.phase2.agent.gateway import _await_future

        class _Future:
            def __init__(self):
                self.cancelled = False

            def result(self, timeout):
                raise TimeoutError("bounded")

            def cancel(self):
                self.cancelled = True

        future = _Future()
        with self.assertRaises(TimeoutError):
            _await_future(future, 0.01)
        self.assertTrue(future.cancelled)


class ReaderDiscoveryTest(unittest.TestCase):
    def test_real_stdio_discovery_exact_three(self):
        async def run():
            transport = PythonStdioTransport(
                str(ROOT / "src" / "document_compare" / "phase2" / "mcp" / "reader_server.py"),
                env=_child_env("http://127.0.0.1:9"),
                cwd=str(ROOT),
                python_cmd=sys.executable,
            )
            async with Client(transport) as client:
                return sorted(tool.name for tool in await client.list_tools())

        self.assertEqual(asyncio.run(run()), ["details", "search", "summarize"])


class ReaderMockEndToEndTest(unittest.TestCase):
    """Runs the full agent -> tools -> reader MCP -> HTTP -> service path once."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.report = mr.run_demo(Path(cls._tmp.name) / "out")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _scenario(self, name):
        return next(s for s in self.report["scenarios"] if s["name"] == name)

    def test_discovery_and_all_scenarios_pass(self):
        self.assertTrue(self.report["tool_discovery"]["pass"])
        self.assertEqual(self.report["tool_discovery"]["observed"], ["details", "search", "summarize"])
        self.assertEqual(self.report["summary"]["failed"], [])
        self.assertTrue(self.report["summary"]["ok"])
        self.assertEqual(self.report["summary"]["passed"], 12)

    def test_overview_counts_from_live_reader(self):
        trace = self._scenario("overview")["trace"]
        summary = next(c["result"] for c in trace if c["name"] == "summarize")
        self.assertEqual(summary["state"], "done")
        self.assertEqual(summary["counts"]["changes"], 7)
        self.assertEqual(summary["counts"]["table_changes"], 1)
        self.assertEqual(summary["counts"]["review_open"], 1)

    def test_numeric_change_and_surrounding_context(self):
        trace = self._scenario("search_details_numeric_context")["trace"]
        details = next(c["result"] for c in trace if c["name"] == "details")
        self.assertEqual(details["record_id"], "CHANGE-000001")
        self.assertIn("21", details["change"]["old"]["text"])
        self.assertIn("99", details["change"]["new"]["text"])
        self.assertTrue(details["context"]["new"]["available"])

    def test_review_item_is_not_confirmed(self):
        trace = self._scenario("review_distinction")["trace"]
        review = next(c["result"] for c in trace if c["name"] == "search")
        record = review["returned"][0]
        self.assertEqual(record["record_id"], "REVIEW-000001")
        self.assertTrue(record["needs_review"])
        self.assertFalse(record["confirmed"])

    def test_multipage_collects_all_payment_changes(self):
        trace = self._scenario("multipage_complete")["trace"]
        searches = [c for c in trace if c["name"] == "search"]
        self.assertEqual(len(searches), 3)
        ids = [r["record_id"] for c in searches for r in c["result"]["returned"]]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)

    def test_table_cell_pagination_reaches_last_quantity_cell(self):
        trace = self._scenario("table_cell_pagination")["trace"]
        details = [c["result"] for c in trace if c["name"] == "details"]
        self.assertEqual(len(details), 3)
        cells = [cell for d in details for cell in d["table"]["cell_changes"]]
        self.assertEqual(len(cells), 6)
        self.assertEqual(details[-1]["table"]["cell_total"], 6)
        self.assertIn("21", cells[-1]["old"]["text"])
        self.assertIn("99", cells[-1]["new"]["text"])

    def test_long_paragraph_tail_via_text_offset(self):
        trace = self._scenario("long_text_tail")["trace"]
        details = [c["result"] for c in trace if c["name"] == "details"]
        self.assertEqual(details[0]["change"]["new"]["text_length"], len(mr.LONG_PARA_TEXT))
        self.assertEqual(details[0]["change"]["new"]["text_next_offset"], 4000)
        self.assertIn(mr.LONG_PARA_TAIL, details[1]["change"]["new"]["text"])

    def test_unfinished_job_summary_safe_search_not_ready(self):
        trace = self._scenario("unfinished_job")["trace"]
        summary = next(c["result"] for c in trace if c["name"] == "summarize")
        search = next(c["result"] for c in trace if c["name"] == "search")
        self.assertEqual(summary["state"], "running")
        self.assertFalse(summary["complete"])
        self.assertNotIn("counts", summary)
        self.assertEqual(search["error"], "not_ready")

    def test_attempted_job_switch_never_leaks_other_job(self):
        scenario = self._scenario("attempted_job_switch")
        self.assertTrue(scenario["observed"]["planner_requested_other"])
        for call in scenario["trace"]:
            self.assertEqual(call["arguments"]["job_id"], mr.JOB_MAIN)
        blob = json.dumps(scenario["trace"], ensure_ascii=False)
        self.assertNotIn(mr.OTHER_MARKER, blob)
        self.assertIn(mr.MAIN_MARKER, blob)

    def test_forbidden_action_makes_no_mcp_write_call(self):
        scenario = self._scenario("forbidden_action")
        self.assertEqual(scenario["trace"], [])
        names = {c["name"] for c in scenario["trace"]}
        self.assertTrue(names.issubset(set(mr.READ_TOOLS)))

    def test_step_budget_is_bounded(self):
        scenario = self._scenario("step_budget_bounded")
        self.assertIn("RuntimeError", scenario["error"])
        self.assertEqual(len([c for c in scenario["trace"]]), 3)

    def test_owned_service_port_released_after_run(self):
        port = self.report["service"]["port"]
        with self.assertRaises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/openapi.json", timeout=2.0)


class FixtureTruthTest(unittest.TestCase):
    """The authored fixture truth is fixed and independent of any response."""

    def test_expected_truth_literals(self):
        self.assertEqual(mr.EXPECTED["overview"]["changes"], 7)
        self.assertEqual(mr.EXPECTED["multipage_complete"]["total_matches"], 6)
        self.assertEqual(mr.EXPECTED["table_cell_pagination"]["cell_total"], 6)
        self.assertEqual(mr.EXPECTED["long_text_tail"]["text_length"], len(mr.LONG_PARA_TEXT))
        self.assertGreater(mr.EXPECTED["long_text_tail"]["text_length"], 4000)

    def test_fixture_canonical_shape(self):
        canonical = mr.canonical_main()
        self.assertEqual(canonical["comparison_stage"], "phase_1_final_comparison")
        for key in ("changes", "table_changes", "review_queue", "statistics"):
            self.assertIn(key, canonical)
        self.assertEqual(len(canonical["changes"]), 7)
        self.assertEqual(len(canonical["table_changes"][0]["cell_changes"]), 6)


if __name__ == "__main__":
    unittest.main()
