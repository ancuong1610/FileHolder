# Phase 2 agent

The Q&A planner and its transports.

Read first: `controller.py` (planner graph), `observations.py` (planner evidence), `tools.py` (the three reader tools),
`gateway.py` (MCP stdio client), `prompts.py`; `mock_reader.py` is the offline
contract demo.

Input/output: plans only over `summarize`/`search`/`details` bound to one job;
`python -m document_compare.phase2.agent.mock_reader --out workspace/reader-mock`.

The graph can prefetch the selected finding before the first planner call. It
projects bounded reader results for the planner and tracks citation IDs from
the full reader response. A deterministic check can ask the planner once to
repair missing or unverified citations or a raw tool dump within the same
planner budget. A final answer may include up to three follow-up questions.

Dependencies: `phase2/mcp` reader client; TIA/BBK client for real chat.

Check: `python -m unittest tests.phase2.test_agent_graph tests.phase2.test_agent_reader`.
