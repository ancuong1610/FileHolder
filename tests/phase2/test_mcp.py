import asyncio, os, unittest
from pathlib import Path
from unittest.mock import patch
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

ROOT = Path(__file__).resolve().parents[2]
TOOLS = {"submit_comparison", "check_status", "retrieve_report", "retrieve_structured_changes", "retrieve_summary"}

class McpToolsTest(unittest.TestCase):
    def test_handler_registration(self):
        from document_compare.phase2.mcp import workflow_server as mcp_server
        for name in TOOLS:
            self.assertTrue(hasattr(mcp_server, name), f"missing {name}")
            self.assertTrue(callable(getattr(mcp_server, name)), f"{name} not callable")

    def test_mocked_retrieve_summary(self):
        from document_compare.phase2.mcp import workflow_server as mcp_server
        with patch.object(mcp_server, "_request") as mock_req:
            mock_req.return_value = {"mode": "mock"}
            result = mcp_server.retrieve_summary("a1b2c3d4e5f6")
            self.assertEqual(result, {"mode": "mock"})
            mock_req.assert_called_once_with("GET", "/jobs/a1b2c3d4e5f6/summary")

    def test_actual_stdio_discovery(self):
        async def run():
            env = {**os.environ, "FASTMCP_CHECK_FOR_UPDATES": "off",
                   "CRR_API_BASE": "http://127.0.0.1:8321",
                   "CRR_MCP_UPLOAD_ROOT": str(ROOT / "input"),
                   "CRR_PROJECT_ROOT": str(ROOT / "workspace"),
                   "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")]))}
            server_file = str(ROOT / "src" / "document_compare" / "phase2" / "mcp" / "workflow_server.py")
            transport = PythonStdioTransport(server_file, env=env, cwd=str(ROOT))
            async with Client(transport) as client:
                return {t.name for t in await client.list_tools()}
        self.assertEqual(asyncio.run(run()), TOOLS)

    def test_reader_impact_schema_and_forwarding(self):
        from document_compare.phase2.mcp import reader_server

        async def run():
            async with Client(reader_server.mcp) as client:
                tools = {tool.name: tool.inputSchema for tool in await client.list_tools()}
                self.assertEqual(set(tools), {"summarize", "search", "details"})
                schema = tools["search"]
                self.assertIn("impact", schema["properties"])
                self.assertNotIn("impact", schema.get("required", []))
                await client.call_tool("search", {"job_id": "a1b2c3d4e5f6", "kind": "move", "impact": "moved"})

        with patch.object(reader_server.httpx, "get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"returned": []}
            asyncio.run(run())
            self.assertEqual(get.call_args.args[0], reader_server.API_BASE + "/jobs/a1b2c3d4e5f6/read/search")
            self.assertEqual(get.call_args.kwargs["params"]["impact"], "moved")
            self.assertEqual(get.call_args.kwargs["params"]["kind"], "move")

if __name__ == "__main__":
    unittest.main()
