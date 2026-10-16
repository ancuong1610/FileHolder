# Phase 2 agent

The Q&A planner and its transports.

Read first: `controller.py` (planner loop), `tools.py` (the three reader tools),
`gateway.py` (MCP stdio client), `prompts.py`; `mock_reader.py` is the offline
contract demo.

Input/output: plans only over `summarize`/`search`/`details` bound to one job;
`python -m document_compare.phase2.agent.mock_reader --out workspace/reader-mock`.

Dependencies: `phase2/mcp` reader client; TIA/BBK client for real chat.

Check: `python -m unittest tests.phase2.test_agent_reader`.
