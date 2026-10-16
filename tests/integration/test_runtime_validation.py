"""Runtime validation that must survive ``python -O`` (Task 3).

``assert`` statements are removed under ``-O``/``-OO``; the ``require`` helper
raises ``ValueError`` unconditionally so critical integrity checks stay active.
"""
import subprocess
import sys
import unittest
from pathlib import Path

from document_compare.phase1.contracts.validation import require

ROOT = Path(__file__).resolve().parents[2]


class RequireTest(unittest.TestCase):
    def test_require_returns_none_for_true_condition(self):
        self.assertIsNone(require(True, "should not fire"))

    def test_require_raises_value_error_with_message(self):
        with self.assertRaises(ValueError) as ctx:
            require(False, "page count invariant violated")
        self.assertEqual(str(ctx.exception), "page count invariant violated")

    def test_require_uses_truthiness(self):
        with self.assertRaises(ValueError):
            require([], "empty list is falsey")
        self.assertIsNone(require([0], "non-empty list is truthy"))


class RequireSurvivesOptimizationTest(unittest.TestCase):
    """A subprocess check of the selected validation in normal and -O modes."""

    SCRIPT = (
        "from document_compare.phase1.contracts.validation import require\n"
        "try:\n"
        "    require(False, 'optimized check')\n"
        "except ValueError as exc:\n"
        "    print('VALUEERROR:' + str(exc))\n"
        "else:\n"
        "    raise SystemExit('require did not raise')\n"
    )

    def _run(self, flags):
        return subprocess.run(
            [sys.executable, *flags, "-c", self.SCRIPT],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_normal_mode_raises(self):
        proc = self._run([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("VALUEERROR:optimized check", proc.stdout)

    def test_optimized_mode_still_raises(self):
        proc = self._run(["-O"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("VALUEERROR:optimized check", proc.stdout)


if __name__ == "__main__":
    unittest.main()
