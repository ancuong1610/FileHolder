import importlib
import json
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path

from document_compare.phase1.evaluation.evaluate import (
    _load_canonical,
    _mock_env,
    _prepare_case_root,
    _run_stages,
    _score_mutation_case,
    run_identity,
    run_mutation,
)
from document_compare.phase1.evaluation.mutations import _mutate_one_text, generate_mutations

ROOT = Path(__file__).resolve().parents[2]


class BootstrapTest(unittest.TestCase):
    def test_python_version(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 10))

    def test_packages_import(self):
        for name in ("document_compare.phase1", "document_compare.phase2"):
            importlib.import_module(name)

    def test_required_tree(self):
        for rel in ("requirements.txt", ".env.example", "input/.gitkeep",
                    "tests/fixtures/general/crr3/old_di_raw.json",
                    "tests/fixtures/general/crr3/new_di_raw.json"):
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_fixture_present(self):
        self.assertTrue([p for p in (ROOT / "tests" / "fixtures" / "general").rglob("*")])

    def test_clean_tree(self):
        banned_dirs = {"private", "runtime", "cache"}
        ignored_roots = {".venv", "venv", "workspace", "__pycache__"}
        found = {
            p.name for p in ROOT.rglob("*")
            if p.is_dir()
            and not any(part in ignored_roots for part in p.relative_to(ROOT).parts)
        }
        self.assertFalse(banned_dirs & found, banned_dirs & found)

    def test_no_bbk_in_public_requirements(self):
        req = (ROOT / "requirements.txt").read_text()
        self.assertNotIn("bbk_ai_hub_sdk", req)


class BenchmarkTest(unittest.TestCase):
    def test_identity_zero_changes(self):
        result = run_identity(ROOT / "workspace")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["element_changes"], 0)
        self.assertEqual(result["table_changes"], 0)

    def test_mutation_summary_passes(self):
        result = run_mutation(ROOT / "workspace", seed=42)
        self.assertTrue(result["passed"], result)
        self.assertIn("summary", result)
        for category in ("modify", "delete", "insert", "move_block", "table_cell", "noise_hyphenation"):
            self.assertIn(category, result["summary"], category)

    def test_cli_writes_report_list_and_exits_zero(self):
        proc = subprocess.run([sys.executable, "-m", "document_compare", "eval", "--root", "workspace"],
            cwd=ROOT, capture_output=True, text=True, env={**os.environ, "CRR_RUN_LLM": "mock", "CRR_DOMAIN_MODE": "generic"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads((ROOT / "workspace" / "eval" / "eval_report.json").read_text())
        self.assertIsInstance(report, list)
        self.assertTrue(any(entry.get("case") == "identity" for entry in report))
        self.assertTrue(any(entry.get("case") == "mutation" for entry in report))


class MutationUnitTest(unittest.TestCase):
    def test_single_word_text_is_mutated(self):
        """Regression: a single eligible word must be mutable despite the 15% lead-in guard."""
        new_text, changed = _mutate_one_text("Standard", random.Random(42))
        self.assertTrue(changed)
        self.assertNotEqual(new_text, "Standard")


class MutationNoisePipelineTest(unittest.TestCase):
    """The generated wrap noise must be a *real* line wrap and stay suppressed.

    The fixture carries only a handful of eligible paragraphs, so the default
    seed injects no wrap noise.  This test forces one injection on the real
    fixture, proves the injected text is a literal ``-\\n`` line wrap, and runs
    the actual s04-s15 pipeline to prove it does not leak into canonical changes.
    """

    def test_forced_wrap_noise_is_injected_and_does_not_leak(self):
        case_root = ROOT / "workspace" / "eval" / "noise_probe"
        with _mock_env():
            _prepare_case_root(case_root, both_from_old=True)
            _run_stages("02", "03", case_root)
            model = json.loads(
                (case_root / "output" / "old_document.json").read_text(encoding="utf-8")
            )
            mutated, truth = generate_mutations(
                model,
                seed=42,
                n_modify=0,
                n_delete=0,
                n_insert=0,
                n_wrap_noise=1,
            )
            noise_truth = [entry for entry in truth if entry["type"] == "noise_hyphenation"]
            self.assertGreaterEqual(len(noise_truth), 1, truth)
            noise_ids = {entry["element_id"] for entry in noise_truth}
            injected_texts = [
                str(element.get("text") or "")
                for element in mutated["document"]["elements"]
                if element.get("element_id") in noise_ids
            ]
            self.assertTrue(
                any("-\n" in text for text in injected_texts),
                injected_texts,
            )

            (case_root / "output" / "new_document.json").write_text(
                json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _run_stages("04", "15", case_root)
            canonical = _load_canonical(case_root)
            score = _score_mutation_case(canonical, truth)

        noise = score["summary"]["noise_hyphenation"]
        self.assertGreater(noise["truth_count"], 0, score)
        self.assertEqual(noise["leaked_into_changes"], 0, score)


if __name__ == "__main__":
    unittest.main()
