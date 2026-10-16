"""Architecture and import-boundary guards for the phase-separated layout.

These tests pin the approved structural contract: the new namespaces exist,
Phase 1 owns exactly fifteen stages, dependency directions stay one-way
(Phase 1 never imports Phase 2; ``shared`` imports neither phase), Stage 14
does not pull in optional evaluation tooling, legacy namespaces are gone, and
every maintained folder carries a README.
"""
from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "document_compare"
PHASE1 = SRC / "phase1"
PHASE2 = SRC / "phase2"
SHARED = SRC / "shared"

STAGE_FILES = [
    "s01_extract.py",
    "s02_validate_extraction.py",
    "s03_internal_schema.py",
    "s04_normalize.py",
    "s05_anchors.py",
    "s06_exact_matching.py",
    "s07_modified_matching.py",
    "s08_candidates.py",
    "s09_movement.py",
    "s10_tables.py",
    "s11_routing.py",
    "s12_packets.py",
    "s13_llm_evaluator.py",
    "s14_canonical_result.py",
    "s15_serve.py",
]


def _imported_modules(path: Path) -> set[str]:
    """Every module name referenced by an import statement in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
                modules.update(
                    f"{node.module}.{alias.name}" for alias in node.names
                )
            elif node.level:
                # Relative imports are resolved by walking up from the file.
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                rel = base.relative_to(SRC).as_posix().replace("/", ".")
                prefix = f"document_compare.{rel}" if rel else "document_compare"
                if node.module:
                    modules.add(f"{prefix}.{node.module}")
                else:
                    modules.update(
                        f"{prefix}.{alias.name}" for alias in node.names
                    )
    return modules


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


class NamespaceTest(unittest.TestCase):
    def test_new_namespaces_import(self):
        for name in (
            "document_compare",
            "document_compare.phase1",
            "document_compare.phase1.pipeline.cli",
            "document_compare.phase1.contracts.validation",
            "document_compare.phase1.contracts.review_identity",
            "document_compare.phase1.contracts.outcomes",
            "document_compare.phase1.reporting.html",
            "document_compare.phase1.evaluation.evaluate",
            "document_compare.phase2.api.service",
            "document_compare.phase2.api.summary",
            "document_compare.phase2.reader.result_reader",
            "document_compare.phase2.mcp.workflow_server",
            "document_compare.phase2.mcp.reader_server",
            "document_compare.phase2.workflow.local_reviewer",
            "document_compare.phase2.agent.controller",
            "document_compare.phase2.agent.tools",
            "document_compare.phase2.agent.gateway",
            "document_compare.shared.paths",
            "document_compare.shared.json_io",
            "document_compare.phase1.extraction.di_fields",
        ):
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_legacy_namespaces_are_gone(self):
        for name in ("crr_compare", "agent_poc"):
            with self.subTest(module=name):
                self.assertFalse((ROOT / name).exists(), name)
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(name)
        self.assertFalse((ROOT / "run_local.py").exists())


class StageLayoutTest(unittest.TestCase):
    def test_exactly_fifteen_stages_with_retained_names(self):
        stages = PHASE1 / "stages"
        found = sorted(p.name for p in stages.glob("s*.py"))
        self.assertEqual(found, STAGE_FILES)

    def test_stage_modules_import_from_phase1(self):
        importlib.import_module("document_compare.phase1.stages.s15_serve")

    def test_stage15_filename_unchanged_and_phase1_owned(self):
        self.assertTrue((PHASE1 / "stages" / "s15_serve.py").is_file())
        self.assertFalse((PHASE2 / "stages").exists())


class DependencyBoundaryTest(unittest.TestCase):
    def test_phase1_does_not_import_phase2(self):
        offenders = []
        for path in _python_files(PHASE1):
            for module in _imported_modules(path):
                if module == "document_compare.phase2" or module.startswith(
                    "document_compare.phase2."
                ):
                    offenders.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(offenders, [])

    def test_shared_imports_neither_phase(self):
        offenders = []
        for path in _python_files(SHARED):
            for module in _imported_modules(path):
                if module.startswith(("document_compare.phase1", "document_compare.phase2")):
                    offenders.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(offenders, [])

    def test_stage14_does_not_import_evaluation(self):
        stage14 = PHASE1 / "stages" / "s14_canonical_result.py"
        for module in _imported_modules(stage14):
            self.assertFalse(
                module.startswith("document_compare.phase1.evaluation"),
                f"Stage 14 imports evaluation tooling: {module}",
            )

    def test_outcome_migration_lives_in_phase1_contracts(self):
        outcomes = PHASE1 / "contracts" / "outcomes.py"
        self.assertTrue(outcomes.is_file())
        source = outcomes.read_text(encoding="utf-8")
        self.assertIn("def build_outcome_migration", source)


class ReadmeCoverageTest(unittest.TestCase):
    # Folders that are taxonomy containers only and intentionally have no README.
    EXEMPT = {
        ROOT / "input",
        ROOT / "workspace",
    }

    def _maintained_dirs(self):
        for path in SRC.rglob("*"):
            if path.is_dir() and "__pycache__" not in path.parts:
                yield path
        for path in (ROOT / "tests").rglob("*"):
            if path.is_dir() and "__pycache__" not in path.parts:
                yield path
        for path in (ROOT / "docs").rglob("*"):
            if path.is_dir() and "__pycache__" not in path.parts:
                yield path

    def test_every_maintained_folder_has_readme(self):
        missing = []
        for directory in self._maintained_dirs():
            if directory in self.EXEMPT:
                continue
            if not (directory / "README.md").is_file():
                missing.append(str(directory.relative_to(ROOT)))
        self.assertEqual(sorted(missing), [])


if __name__ == "__main__":
    unittest.main()
