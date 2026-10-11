"""Offline setup and synthetic-demo contract tests (Task 2).

These tests never touch the network or credentials. They prove:

- public requirements contain no internal SDK and no internal index URL;
- ``.env.example`` carries only variable names/placeholders;
- ``.gitignore`` protects secrets/runtime while preserving placeholders;
- the ``demo`` command is synthetic/mock-only: stages 02-15, never stage 01,
  never a real provider even when the parent shell says ``CRR_RUN_LLM=real``;
- the parent environment is never mutated;
- a non-empty output root is refused, paths with spaces work, and a missing
  fixture fails clearly;
- ``output/mock_run.json`` provenance, the ``[MOCK/SYNTHETIC]`` label, and the
  canonical comparison-result schema are as specified.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crr_compare import cli

ROOT = Path(__file__).resolve().parents[1]

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

_REQUIRED_ENV_KEYS = {
    "TIA_API_KEY",
    "TIA_AZURE_ENDPOINT",
    "BBK_AI_HUB_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "CRR_RUN_LLM",
    "CRR_API_BASE",
    "CRR_MCP_UPLOAD_ROOT",
    "CRR_PROJECT_ROOT",
    "FASTMCP_CHECK_FOR_UPDATES",
}

_BENIGN_ENV_VALUES = {
    "",
    "mock",
    "off",
    "bbk_ai_hub",
    "openai_compatible",
    "true",
    "false",
    "http://127.0.0.1:8321",
    "./input",
    "./workspace",
}

_CANONICAL_TOP_LEVEL = {
    "schema_version",
    "comparison_stage",
    "old_document",
    "new_document",
    "processing",
    "statistics",
    "changes",
    "table_changes",
    "review_queue",
    "non_semantic_events",
    "unresolved_table_reviews",
    "domain_profile",
    "domain_verification_review",
    "outcome_migration",
    "generic_projection",
}


def _parse_env_example(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        pairs[key.strip()] = value.strip()
    return pairs


class PublicSetupTest(unittest.TestCase):
    def test_requirements_exclude_internal_sdk(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("bbk_ai_hub_sdk", text)

    def test_requirements_have_no_internal_index_url(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for token in ("artifactory", "bundesbank", "index-url", "index_url", "trusted-host"):
            self.assertNotIn(token, text, token)

    def test_env_example_only_names_and_placeholders(self):
        path = ROOT / ".env.example"
        self.assertTrue(path.is_file(), ".env.example is missing")
        pairs = _parse_env_example(path)
        self.assertEqual(set(pairs), _REQUIRED_ENV_KEYS)
        for key in pairs:
            self.assertRegex(key, _ENV_KEY_RE)
        text = path.read_text(encoding="utf-8")
        for token in ("artifactory", "bundesbank", "index-url", "index_url", "trusted-host"):
            self.assertNotIn(token, text, token)
        for key, value in pairs.items():
            if not value or "<" in value:
                continue  # explicit placeholder
            self.assertIn(value, _BENIGN_ENV_VALUES, f"{key} carries a non-placeholder value")

    def test_gitignore_protects_secrets_runtime_and_placeholders(self):
        path = ROOT / ".gitignore"
        self.assertTrue(path.is_file(), ".gitignore is missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn(".env", text)
        self.assertIn("workspace/", text)
        self.assertIn(".pdf", text)
        self.assertIn("!.env.example", text)
        self.assertIn("!input/.gitkeep", text)


class DemoUnitTest(unittest.TestCase):
    def test_child_env_forces_mock_and_generic_without_mutating_parent(self):
        root = Path("/approved/demo root")
        with patch.dict(
            os.environ,
            {"CRR_RUN_LLM": "real", "CRR_DOMAIN_MODE": "crr3"},
            clear=False,
        ):
            before = dict(os.environ)
            env = cli._demo_child_env(root)
            self.assertEqual(env["CRR_RUN_LLM"], "mock")
            self.assertEqual(env["CRR_DOMAIN_MODE"], "generic")
            self.assertEqual(env["CRR_PROJECT_ROOT"], str(root))
            self.assertIsNot(env, os.environ)
            self.assertEqual(dict(os.environ), before)

    def test_demo_command_runs_only_stages_02_to_15(self):
        command = cli._demo_command(Path("/approved/demo root"))
        self.assertIn("--from-stage", command)
        self.assertIn("--to-stage", command)
        self.assertEqual(command[command.index("--from-stage") + 1], "02")
        self.assertEqual(command[command.index("--to-stage") + 1], "15")
        self.assertNotIn("01", command)

    def test_missing_fixture_files_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo root"
            with patch.object(cli, "fixture_dir", return_value=Path(directory) / "absent"):
                with self.assertRaises(SystemExit) as caught:
                    cli._seed_demo_fixture("crr3", root)
            message = str(caught.exception)
            self.assertIn("crr3", message)
            self.assertIn("missing", message.lower())

    def test_refuses_non_empty_output_root_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir()
            existing = out / "comparison_result.json"
            existing.write_text('{"untouched": true}')
            with self.assertRaises(SystemExit) as caught:
                cli.run_demo("internal_policy_report", root)
            self.assertIn("refus", str(caught.exception).lower())
            self.assertEqual(existing.read_text(), '{"untouched": true}')

    def test_invalid_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                cli.run_demo("not_a_fixture", Path(directory) / "root")


class DemoEndToEndTest(unittest.TestCase):
    def _canonical(self, root: Path) -> dict:
        return json.loads((root / "output" / "comparison_result.json").read_text(encoding="utf-8"))

    def test_default_demo_is_synthetic_mock_and_schema_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo root"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                cli.main(["demo", "--root", str(root)])

            out = root / "output"
            provenance = json.loads((out / "mock_run.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["fixture"], "internal_policy_report")
            self.assertTrue(provenance["synthetic"])
            self.assertEqual(provenance["evaluator"], "mock")
            self.assertEqual(provenance["domain_mode"], "generic")
            self.assertFalse(provenance["uses_document_intelligence"])
            self.assertFalse(provenance["uses_uploaded_pdfs"])
            self.assertIn("[MOCK/SYNTHETIC]", buffer.getvalue())
            self.assertIn("comparison_redline.pdf", buffer.getvalue())

            canonical = self._canonical(root)
            self.assertTrue(_CANONICAL_TOP_LEVEL <= set(canonical))
            self.assertEqual(canonical["schema_version"], "3.0.0")
            self.assertEqual(canonical["comparison_stage"], "phase_1_final_comparison")
            self.assertNotIn("mock_run", canonical)

            decisions = json.loads((out / "llm_review_decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(decisions["processing"]["mode"], "mock")

            self.assertTrue((out / "comparison_redline.pdf").is_file())
            self.assertGreater((out / "comparison_redline.pdf").stat().st_size, 0)
            # Stage 01 was never run: no chunk cache and no dropped input PDFs.
            self.assertFalse((root / "cache").exists())
            self.assertFalse((root / "input").exists())

    def test_scai_demo_paths_with_spaces_and_mock_evaluator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo root with spaces"
            with patch.dict(os.environ, {"CRR_RUN_LLM": "real"}, clear=False):
                cli.run_demo("scai_api_guide", root)

            decisions = json.loads(
                (root / "output" / "llm_review_decisions.json").read_text(encoding="utf-8")
            )
            self.assertEqual(decisions["processing"]["mode"], "mock")
            self.assertGreaterEqual(decisions["statistics"]["packets"], 1)
            self.assertNotIn("fallback", {
                decision.get("evaluator") for decision in decisions["decisions"]
            })

            canonical = self._canonical(root)
            self.assertEqual(canonical["statistics"]["changes"], 5)
            self.assertEqual(canonical["statistics"]["table_changes"], 1)
            self.assertEqual(canonical["statistics"]["review_queue"], 1)


if __name__ == "__main__":
    unittest.main()
