"""Stage-10 (s10_tables) module-safety and behavior tests.

Three properties are pinned here, following the s07/s08/s09 module-safety
pattern:

1. Importing ``s10_tables`` must have **no** side effects: it must not
   resolve or create a workspace root, read or write pipeline files, print,
   or mutate the environment.  Historically the module resolved
   ``find_project_root()`` (which *creates* the root directory), printed,
   and then crashed on the missing ``output/old_document_structured.json``
   at import time.
2. The pure table helpers must stay callable after import with no pipeline
   data, and the conservative normalization that routes table cells to
   review must keep its meaning: layout hyphenation is preserved, selection
   states are distinct, and numeric skeletons unify only under the
   aggressive artifact path.
3. Running the module as ``__main__`` stays the documented Stage-10 entry
   point: it reads the three Step-3/9 seeds, writes a validated
   ``output/table_comparison_result.json``, and prints the CLI-equivalent
   report.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.synthetic_stage_support import seed_internal_policy_stages

ROOT = Path(__file__).resolve().parents[1]
S10 = ROOT / "crr_compare" / "stages" / "s10_tables.py"
PYTHON = Path(__import__("sys").executable)


def _env(root: Path | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "CRR_RUN_LLM": "mock",
        "CRR_DOMAIN_MODE": "generic",
        "PYTHONPATH": str(ROOT),
    }
    if root is not None:
        env["CRR_PROJECT_ROOT"] = str(root)
    return env


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


class ImportSafetyTest(unittest.TestCase):
    """Importing s10_tables must not touch the workspace or print."""

    def test_import_has_no_workspace_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-import-") as directory:
            workspace = Path(directory) / "empty-root"
            script = f"""
import importlib, os
from pathlib import Path
workspace = Path({str(workspace)!r})
os.environ["CRR_PROJECT_ROOT"] = str(workspace)
env_before = dict(sorted(os.environ.items()))
module = importlib.import_module("crr_compare.stages.s10_tables")
env_after = dict(sorted(os.environ.items()))
created = sorted(
    str(path.relative_to(workspace))
    for path in workspace.rglob("*")
    if "__pycache__" not in path.parts
)
assert env_before == env_after, "import mutated the environment"
assert created == [], f"import created files under the empty root: {{created!r}}"
print("IMPORT_OK", module.STEP10_VERSION)
"""
            completed = _run(script, _env())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("IMPORT_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(
                workspace.exists(), "import resolved or created the workspace root"
            )


class ModuleHelpersUsableTest(unittest.TestCase):
    """Pure table helpers must work after import with no pipeline data.

    The normalization meanings that route uncertain cells to review are
    pinned exactly: layout hyphenation is preserved (not collapsed), the
    two selection states stay distinct, and the aggressive artifact
    skeleton unifies only transparently.
    """

    def test_helpers_usable_and_normalization_meanings_preserved(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-helpers-") as directory:
            empty = Path(directory) / "empty-root"
            script = f"""
import importlib, os
from pathlib import Path
os.environ["CRR_PROJECT_ROOT"] = str(Path({str(empty)!r}))
s10 = importlib.import_module("crr_compare.stages.s10_tables")

# Conservative layout normalization preserves hyphenation.
assert s10.layout_text("Waren- und") != s10.layout_text("Warenund")
assert s10.layout_text("Beta-Faktor") != s10.layout_text("BetaFaktor")
assert s10.layout_text("10 - 20") == "10-20", "digit range spacing normalized"
# Selection states are distinct.
assert s10.selection_states(":selected:") != s10.selection_states(":unselected:")
# Aggressive artifact skeleton unifies true OCR/line-wrap artifacts only.
assert s10.alphanumeric_skeleton("Beta- Faktor") == s10.alphanumeric_skeleton("BetaFaktor")
assert s10.artifact_tolerant_text("Be- ta-Fak- tor") == s10.artifact_tolerant_text("BetaFaktor")

# Pure helpers usable with ordinary inputs.
assert s10.source_text("  a  b ") == "a b"
assert s10.header_similarity(("A", "B"), ("A", "B")) == 1.0
assert s10.compact_cell(None) is None
assert s10.compare_cell(None, {{"text": "x"}})["classification"] == "ADDED"
assert s10.cell_structure({{"row_span": 2, "column_span": 3, "kind": "data"}}) == (2, 3, "data")
assert s10.compact_token_diff("a b", "a c")["change_operation_count"] == 1
assert s10.compare_cell(
    {{"text": "same"}}, {{"text": "same"}}
)["classification"] == "UNCHANGED"
assert s10.residual_context_key({{"nearest_provisional_context_key": "K1"}}) == "K1"
assert s10.residual_context_key({{}}) is None

# Core table assembly entry points usable on empty inputs.
old_tables, audit = s10.make_logical_tables("old", [], {{"": ""}})
assert old_tables == [] and audit == []
matches, unmatched_old, unmatched_new, match_audit = s10.match_logical_tables([], [])
assert matches == [] and unmatched_old == [] and unmatched_new == []
assert match_audit["exact_matches"] == 0 and match_audit["fuzzy_matches"] == 0

assert not Path({str(empty)!r}).exists(), "import resolved or created the workspace root"
print("HELPERS_OK")
"""
            completed = _run(script, _env())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("HELPERS_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(empty.exists(), "import created the workspace root")


class MainScriptTest(unittest.TestCase):
    """s10_tables must run as a CLI-equivalent __main__ script.

    Seeds are read-only copies of the committed ``output_sample`` Step-3/9
    results; every run happens in an isolated temporary root with the fixed
    offline environment, so no originals and no network are touched.
    """

    def test_main_writes_table_comparison_result_and_reports(self):
        with tempfile.TemporaryDirectory(prefix="crr-s10-main-") as directory:
            root = Path(directory)
            seed_internal_policy_stages(root, 9, fixture="crr3")
            output = root / "output"
            script = (
                f"import runpy\n"
                f"runpy.run_path({str(S10)!r}, run_name='__main__')\n"
            )
            completed = _run(script, _env(root))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = output / "table_comparison_result.json"
            self.assertTrue(result_path.is_file())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "0.4.0")
            self.assertEqual(
                result["comparison_stage"],
                "logical_table_comparison_with_provisional_context",
            )
            self.assertGreaterEqual(result["statistics"]["matched_logical_tables"], 1)
            self.assertIn("Saved:", completed.stdout)
            self.assertIn("=== ADDED TABLE CANDIDATES ===", completed.stdout)


if __name__ == "__main__":
    unittest.main()
