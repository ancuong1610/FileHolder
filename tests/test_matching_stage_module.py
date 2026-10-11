"""Task 6 tests: s07 is import-safe and still runs as a CLI-equivalent script.

Two properties are pinned here:

1. Importing ``s07_modified_matching`` must have **no** side effects: it must
   not resolve or create a workspace root, read or write pipeline files, print,
   or mutate the environment.  Historically the module resolved
   ``find_project_root()`` and ran the whole stage at import time, so importing
   it against a fresh workspace created the directory and then crashed on the
   missing ``output/exact_element_matches.json``.
2. Running the module as ``__main__`` stays the documented Stage-07 entry
   point: it reads ``output/exact_element_matches.json``, writes a validated
   ``output/modified_element_matches.json``, and prints the CLI-equivalent
   report including elapsed seconds.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.synthetic_stage_support import seed_internal_policy_stages

ROOT = Path(__file__).resolve().parents[1]
S07 = ROOT / "crr_compare" / "stages" / "s07_modified_matching.py"
PYTHON = sys.executable

_IMPORT_PROBE = """
import importlib.util
import os
from pathlib import Path

s07 = Path({s07!r})
workspace = Path({workspace!r})
os.environ["CRR_PROJECT_ROOT"] = str(workspace)
before_env = dict(os.environ)

spec = importlib.util.spec_from_file_location("s07_import_probe", s07)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert os.environ == before_env, "import mutated the environment"
assert not workspace.exists(), "import created the workspace root"

result = module.build_result(
    {{"unmatched_old": [], "unmatched_new": [], "old_document": {{}}, "new_document": {{}}}}
)
assert result["schema_version"] == "0.3.0", "build_result unusable after import"
"""


def _env(root: Path | None = None) -> dict[str, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)}
    if root is not None:
        env["CRR_PROJECT_ROOT"] = str(root)
    return env


class ImportSafetyTest(unittest.TestCase):
    def test_import_has_no_workspace_side_effects(self):
        with tempfile.TemporaryDirectory(prefix="crr-s07-import-") as directory:
            workspace = Path(directory) / "does-not-exist"
            script = _IMPORT_PROBE.format(s07=str(S07), workspace=str(workspace))
            completed = subprocess.run(
                [PYTHON, "-c", script],
                cwd=ROOT,
                env=_env(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "", "import printed to stdout")
            self.assertFalse(workspace.exists(), "import created the workspace root")


class MainScriptTest(unittest.TestCase):
    def test_main_writes_validated_result_and_reports(self):
        with tempfile.TemporaryDirectory(prefix="crr-s07-main-") as directory:
            root = Path(directory)
            seed_internal_policy_stages(root, 7)
            output = root / "output"
            script = f"import runpy\nrunpy.run_path({str(S07)!r}, run_name='__main__')\n"
            completed = subprocess.run(
                [PYTHON, "-c", script],
                cwd=ROOT,
                env=_env(root),
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = output / "modified_element_matches.json"
            self.assertTrue(result_path.is_file())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "0.3.0")
            self.assertEqual(
                result["comparison_stage"],
                "modified_element_matching_with_provisional_context",
            )
            self.assertIn("elapsed_seconds", result["processing"])
            self.assertIn("Saved:", completed.stdout)
            self.assertIn("Elapsed:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
