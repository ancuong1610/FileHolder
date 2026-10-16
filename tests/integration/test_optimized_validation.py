"""Subprocess proof that stage validators remain active under ``python -O``."""
import copy
import os
import runpy
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)

# Every test here replays the private ``output_sample`` seeds, so the module is
# optional in a public checkout that cannot ship those fixtures.
_SAMPLE_FILES = (
    "old_di_raw.json",
    "old_extraction_validation.json",
    "exact_element_matches.json",
)
_MISSING_SAMPLES = [name for name in _SAMPLE_FILES if not (ROOT / "output_sample" / name).is_file()]


@unittest.skipUnless(
    not _MISSING_SAMPLES,
    "private output_sample fixtures are unavailable in this checkout: "
    + ", ".join(_MISSING_SAMPLES),
)
class OptimizedStageValidationTest(unittest.TestCase):
    def _run(self, stage: str, mutation: str, optimized: bool) -> subprocess.CompletedProcess[str]:
        script = f"""
import copy, os, runpy
from pathlib import Path
root = Path({str(ROOT)!r})
os.chdir(root)
namespace = runpy.run_path(str(root / 'src' / 'document_compare' / 'phase1' / 'stages' / {('s03_internal_schema.py' if stage == 's03' else 's07_modified_matching.py')!r}))
{mutation}
"""
        flags = ["-O"] if optimized else []
        return subprocess.run(
            [str(PYTHON), *flags, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
        )

    def test_s03_duplicate_schema_failure_survives_optimization(self):
        # s03 is import-safe: no pipeline state is exposed at import, so the
        # harness builds the internal document explicitly from the sample raw
        # plus its Step-2 validation report, then proves the validator still
        # rejects a duplicated element_id under both normal and -O modes.
        mutation = """
raw = namespace['load_json'](root / 'output_sample' / 'old_di_raw.json')
report = namespace['load_json'](root / 'output_sample' / 'old_extraction_validation.json')
model = namespace['build_internal_document'](raw, report, document_id='DOC-OLD', prefix='OLD')
bad = copy.deepcopy(model)
bad['document']['elements'][1]['element_id'] = bad['document']['elements'][0]['element_id']
try:
    namespace['validate_internal_document'](bad, raw, report)
except (AssertionError, ValueError):
    raise SystemExit(0)
raise SystemExit('s03 accepted duplicate element_id')
"""
        normal = self._run("s03", mutation, False)
        optimized = self._run("s03", mutation, True)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(optimized.returncode, 0, optimized.stderr)

    def test_s07_malformed_duplicate_failure_survives_optimization(self):
        # s07 is import-safe, so its namespace exposes build_result/validate_result
        # without running the stage. Rebuild a valid result from the seeded
        # Step-6 input, duplicate one match, and prove validation still rejects it.
        mutation = """
exact = namespace['load_json'](root / 'output_sample' / 'exact_element_matches.json')
result = namespace['build_result'](exact)
bad = copy.deepcopy(result)
bad['modified_matches'].append(copy.deepcopy(bad['modified_matches'][0]))
try:
    namespace['validate_result'](bad)
except (AssertionError, ValueError):
    raise SystemExit(0)
raise SystemExit('s07 accepted duplicate match')
"""
        normal = self._run("s07", mutation, False)
        optimized = self._run("s07", mutation, True)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(optimized.returncode, 0, optimized.stderr)


if __name__ == "__main__":
    unittest.main()
