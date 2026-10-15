"""Import-safety proof for the early (s02–s09) pipeline stages.

Each stage is required to be import-safe: importing the module must not
resolve or create a workspace root, read or write pipeline files, print, or
mutate the environment. All file orchestration lives in ``main()`` behind the
``__name__ == "__main__"`` guard, which the CLI stage runner invokes through
``runpy.run_path(..., run_name="__main__")``.

The check runs each import in a fresh subprocess with an empty throwaway
project root: any side effect (created file, printed output, environment
mutation, or raised error at import) fails the test.

Stage-execution smoke tests additionally pin that s08/s09 still run as
CLI-equivalent ``__main__`` scripts against the committed ``output_sample``
Step-6/7 seeds, and that their pure module-level helpers (``build_*``,
``element_text``, ...) stay usable after import without any pipeline data.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
STAGES = [
    "s02_validate_extraction",
    "s03_internal_schema",
    "s04_normalize",
    "s05_anchors",
    "s06_exact_matching",
    "s07_modified_matching",
    "s08_candidates",
    "s09_movement",
]

S08 = ROOT / "crr_compare" / "stages" / "s08_candidates.py"
S09 = ROOT / "crr_compare" / "stages" / "s09_movement.py"
SAMPLE_EXACT = ROOT / "output_sample" / "exact_element_matches.json"
SAMPLE_MODIFIED = ROOT / "output_sample" / "modified_element_matches.json"
_SAMPLE_FILES = ("exact_element_matches.json", "modified_element_matches.json")
_MISSING_SAMPLES = [name for name in _SAMPLE_FILES if not (ROOT / "output_sample" / name).is_file()]


def _import_script(stage: str) -> str:
    return f"""
import importlib, json, os, sys, tempfile
from pathlib import Path
repo = Path({str(ROOT)!r})
work = Path(tempfile.mkdtemp(prefix="import-safe-"))
os.environ["CRR_PROJECT_ROOT"] = str(work)
env_before = dict(sorted(os.environ.items()))
module = importlib.import_module("crr_compare.stages.{stage}")
env_after = dict(sorted(os.environ.items()))
created = sorted(
    str(path.relative_to(work))
    for path in work.rglob("*")
    if "__pycache__" not in path.parts
)
assert env_before == env_after, "import mutated the environment"
assert created == [], f"import created files under the empty root: {{created!r}}"
print("IMPORT_OK", module.__name__)
"""


class StageImportSafetyTest(unittest.TestCase):
    def _run_import(self, stage: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PYTHON), "-c", _import_script(stage)],
            cwd=str(ROOT),
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "CRR_RUN_LLM": "mock",
                "CRR_DOMAIN_MODE": "generic",
                "PYTHONPATH": str(ROOT),
            },
            capture_output=True,
            text=True,
        )

    def test_all_early_stages_import_without_side_effects(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                completed = self._run_import(stage)
                self.assertEqual(
                    completed.returncode, 0, completed.stderr + completed.stdout
                )
                self.assertIn("IMPORT_OK", completed.stdout)
                self.assertEqual(completed.stderr, "")


def _smoke_env(root: str) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "CRR_RUN_LLM": "mock",
        "CRR_DOMAIN_MODE": "generic",
        "CRR_PROJECT_ROOT": root,
        "PYTHONPATH": str(ROOT),
    }


def _run_subprocess(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


class ModuleHelpersUsableTest(unittest.TestCase):
    """s08/s09 pure helpers must work after import with no pipeline data.

    Importing the module must not resolve a root or read files, and the
    module-level functions must stay callable with ordinary inputs.
    """

    def test_s08_build_and_s09_helpers_usable_after_import(self):
        with tempfile.TemporaryDirectory(prefix="crr-s89-helpers-") as directory:
            empty = Path(directory) / "empty-root"
            script = f"""
import importlib, os
from pathlib import Path
os.environ["CRR_PROJECT_ROOT"] = str(Path({str(empty)!r}))
s08 = importlib.import_module("crr_compare.stages.s08_candidates")
s09 = importlib.import_module("crr_compare.stages.s09_movement")
probe = s08.build_comparison_candidates(
    {{"matches": [], "unmatched_old": [], "unmatched_new": [], "statistics": {{}}}},
    {{"modified_matches": [], "review_pairs": [],
      "duplicate_exact_review_pairs": [], "unmatched_old": [], "unmatched_new": []}},
)
assert probe["comparison_stage"] == "comparison_candidate_assembly_and_multi_representation_token_diff"
assert probe["statistics"]["total_candidate_records"] == 0
assert s08.element_ids([], "old") == []
assert s09.element_text({{"text": "  a b  "}}) == "a b"
assert s09.token_count("a b c d") == 4
assert not Path({str(empty)!r}).exists(), "import resolved or created the workspace root"
assert callable(s09.validate_input)
print("HELPERS_OK")
"""
            completed = _run_subprocess(script, _smoke_env(str(empty)))
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("HELPERS_OK", completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(empty.exists(), "import created the workspace root")


@unittest.skipUnless(
    not _MISSING_SAMPLES,
    "private output_sample fixtures are unavailable in this checkout: "
    + ", ".join(_MISSING_SAMPLES),
)
class StageExecutionSmokeTest(unittest.TestCase):
    """s08 then s09 must run as CLI-equivalent __main__ scripts.

    Seeds use the committed ``output_sample`` Step-6/7 results (read-only);
    every run happens in an isolated temporary root with the fixed offline
    environment, so no originals and no network are touched.
    """

    def test_s08_and_s09_run_end_to_end_on_sample_seeds(self):
        with tempfile.TemporaryDirectory(prefix="crr-s89-smoke-") as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            shutil.copyfile(SAMPLE_EXACT, output / "exact_element_matches.json")
            shutil.copyfile(SAMPLE_MODIFIED, output / "modified_element_matches.json")
            script = f"""
import json, os, runpy
from pathlib import Path
root = Path({str(root)!r})
os.environ["CRR_PROJECT_ROOT"] = str(root)
runpy.run_path({str(S08)!r}, run_name="__main__")
runpy.run_path({str(S09)!r}, run_name="__main__")
candidates = json.loads((root / "output" / "comparison_candidates.json").read_text(encoding="utf-8"))
movement = json.loads(
    (root / "output" / "comparison_candidates_with_movement.json").read_text(encoding="utf-8")
)
assert candidates["comparison_stage"] == "comparison_candidate_assembly_and_multi_representation_token_diff"
assert movement["comparison_stage"] == "movement_and_structural_reconciliation_evidence"
assert candidates["statistics"]["total_candidate_records"] >= 1
print("SMOKE_OK", candidates["statistics"]["total_candidate_records"],
      movement["statistics"]["movement_block_review_candidates"])
"""
            completed = _run_subprocess(script, _smoke_env(str(root)))
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("SMOKE_OK", completed.stdout)
            self.assertIn("Saved:", completed.stdout)
            self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
