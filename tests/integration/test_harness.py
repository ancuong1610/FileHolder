"""Structural checks for the repository-local coding-agent harness.

These tests pin the team-facing harness files and the read-only validator
contract. They exercise temporary fixtures for broken links, skill/decision
metadata and scope boundaries, then check the real repository's preserved
Knowledge Transfer and shared-skill layout.

Passing structural checks does not prove that Copilot or GitLab Duo loaded a
skill, that an agent followed instructions, or that comparison accuracy changed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "tools" / "harness" / "validate.py"
KNOWLEDGE_TRANSFER = ROOT / "docs" / "KnowlesgeTranser Phase 1.md"
KNOWLEDGE_TRANSFER_SHA256 = (
    "801a034d61de3be2c07814ce3a348b847b6ce2027125f93ba944b2e97d675e21"
)
SKILL_IDS = (
    "fileholder-investigate",
    "fileholder-change",
    "fileholder-evaluate",
    "fileholder-read-results",
    "fileholder-handover",
)


def _load_validator():
    if not VALIDATOR.is_file():
        raise AssertionError(f"missing validator: {VALIDATOR.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location(
        "harness_validator_under_test", VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise AssertionError("validator module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_harness(root: Path) -> None:
    routes = (
        "[README](README.md) [docs](docs/README.md) "
        "[Knowledge Transfer](docs/KnowlesgeTranser%20Phase%201.md) "
        "[architecture](docs/architecture.md) "
        "[phase testing](docs/operations/phase-testing.md) "
        "[review guide](docs/review-guide.md) "
        "[contracts](docs/contracts/README.md) "
        "[governance](docs/governance/README.md) "
        "[skills](.agents/skills/README.md)"
    )
    _write(root, "AGENTS.md", f"# Agent\n\n{routes}\n")
    _write(root, "README.md", "[docs](docs/README.md)\n")
    _write(
        root,
        ".github/copilot-instructions.md",
        "[AGENTS](../AGENTS.md) [docs](../docs/README.md)\n",
    )
    _write(
        root,
        ".agents/README.md",
        "[skills](skills/README.md) [governance](../docs/governance/README.md)\n",
    )
    index = "\n".join(
        f"- [{skill}]({skill}/SKILL.md)" for skill in SKILL_IDS
    )
    _write(root, ".agents/skills/README.md", f"# Skills\n\n{index}\n")
    for skill in SKILL_IDS:
        _write(
            root,
            f".agents/skills/{skill}/SKILL.md",
            "---\n"
            f"name: {skill}\n"
            "description: Use for a bounded, read-only fixture procedure.\n"
            "---\n\n"
            "Read [review guide](../../../docs/review-guide.md).\n",
        )
    _write(root, "docs/README.md", "[architecture](architecture.md)\n")
    _write(root, "docs/architecture.md", "# Architecture\n")
    _write(root, "docs/review-guide.md", "# Review guide\n")
    _write(root, "docs/KnowlesgeTranser Phase 1.md", "# Knowledge Transfer\n")
    _write(
        root,
        "docs/operations/README.md",
        "[testing](phase-testing.md) [quickstart](agent-harness-quickstart.md)\n",
    )
    _write(root, "docs/operations/phase-testing.md", "# Phase testing\n")
    _write(root, "docs/operations/agent-harness-quickstart.md", "# Quickstart\n")
    _write(root, "docs/contracts/README.md", "# Contracts\n")
    _write(
        root,
        "docs/governance/README.md",
        "[authority](authority.md) [workflows](workflows.md) "
        "[done](definition-of-done.md) [decisions](decisions/README.md)\n",
    )
    _write(root, "docs/governance/authority.md", "# Authority\n")
    _write(root, "docs/governance/workflows.md", "# Workflows\n")
    _write(root, "docs/governance/definition-of-done.md", "# Done\n")
    _write(
        root,
        "docs/governance/decisions/README.md",
        "[0001](0001-adopt-selective-agent-harness.md)\n",
    )
    _write(
        root,
        "docs/governance/decisions/0001-adopt-selective-agent-harness.md",
        "# Decision 0001\n\n"
        "- ID: 0001\n"
        "- Status: accepted\n"
        "- Approval: owner approved this fixture choice.\n",
    )
    _write(root, "tools/README.md", "[harness](harness/README.md)\n")
    _write(root, "tools/harness/README.md", "# Harness validator\n")
    _write(root, "tools/harness/validate.py", "# fixture validator placeholder\n")


def _under(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


class HarnessFixtureTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        _minimal_harness(self.root)
        self.validator = _load_validator()

    def tearDown(self):
        self._temp.cleanup()

    def findings(self):
        return self.validator.validate_repository(self.root)

    def test_valid_minimal_fixture_has_no_findings(self):
        self.assertEqual(self.findings(), [])

    def test_missing_required_entrypoint_is_a_finding(self):
        (self.root / "AGENTS.md").unlink()
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("missing-required-path: AGENTS.md") for item in findings),
            findings,
        )

    def test_broken_local_markdown_link_is_a_finding(self):
        _write(self.root, "README.md", "[broken](docs/missing.md)\n")
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("missing-link: README.md -> docs/missing.md") for item in findings),
            findings,
        )

    def test_encoded_space_destination_is_decoded(self):
        # The route uses %20 and resolves; a missing encoded destination fails.
        self.assertEqual(self.findings(), [])
        _write(self.root, "README.md", "[missing](docs/KnowlesgeTranser%20Phase%202.md)\n")
        findings = self.findings()
        self.assertTrue(
            any("KnowlesgeTranser%20Phase%202.md" in item for item in findings),
            findings,
        )

    def test_fenced_code_examples_and_external_urls_are_not_links(self):
        _write(
            self.root,
            "README.md",
            "[docs](docs/README.md)\n\n"
            "```markdown\n[not a link](docs/missing.md)\n```\n"
            "Inline `[not a link](docs/inline-missing.md)` stays code.\n"
            "[external](https://example.invalid/not-fetched)\n",
        )
        self.assertEqual(self.findings(), [])

    def test_invalid_skill_name_is_a_finding(self):
        _write(
            self.root,
            ".agents/skills/fileholder-INVALID/SKILL.md",
            "---\n"
            "name: fileholder-INVALID\n"
            "description: Use for an invalid-name fixture.\n"
            "---\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("skill-invalid-name:") for item in findings),
            findings,
        )

    def test_unterminated_quoted_skill_name_is_not_valid(self):
        _write(
            self.root,
            ".agents/skills/fileholder-change/SKILL.md",
            "---\n"
            'name: "fileholder-change\n'
            "description: Use for an unterminated-name fixture.\n"
            "---\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("skill-malformed-name:") for item in findings),
            findings,
        )

    def test_unterminated_quoted_skill_description_is_not_valid(self):
        _write(
            self.root,
            ".agents/skills/fileholder-change/SKILL.md",
            "---\n"
            "name: fileholder-change\n"
            'description: "Use for an unterminated-description fixture.\n'
            "---\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("skill-malformed-description:") for item in findings),
            findings,
        )

    def test_external_skill_symlink_is_not_read(self):
        skill_path = self.root / ".agents/skills/fileholder-change/SKILL.md"
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "SKILL.md"
            external.write_text(
                "---\nname: fileholder-change\ndescription: external\n---\n",
                encoding="utf-8",
            )
            skill_path.unlink()
            try:
                skill_path.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            read_paths: list[Path] = []
            original_read_text = Path.read_text

            def recording_read_text(path: Path, *args, **kwargs):
                read_paths.append(path.resolve())
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", recording_read_text):
                findings = self.findings()
        self.assertTrue(
            any(
                item.startswith("skill-entrypoint-symlink:")
                or item.startswith("required-path-escapes-root:")
                for item in findings
            ),
            findings,
        )
        self.assertNotIn(external.resolve(), read_paths)

    def test_self_referential_skill_symlink_fails_closed_without_traceback(self):
        skill_path = self.root / ".agents/skills/fileholder-change/SKILL.md"
        skill_path.unlink()
        try:
            skill_path.symlink_to(skill_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        findings = self.findings()
        self.assertTrue(
            any(
                item.startswith("skill-entrypoint-symlink:")
                or item.startswith("missing-required-path:")
                for item in findings
            ),
            findings,
        )
        result = self._run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("skill-entrypoint-symlink:", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_required_agents_symlink_to_workspace_is_not_read(self):
        target = self.root / "workspace" / "synthetic-only" / "agent-instructions.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            (self.root / "AGENTS.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        agents = self.root / "AGENTS.md"
        agents.unlink()
        try:
            agents.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        read_paths: list[Path] = []
        original_read_text = Path.read_text

        def recording_read_text(path: Path, *args, **kwargs):
            read_paths.append(path.resolve())
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", recording_read_text):
            findings = self.findings()
        self.assertTrue(findings)
        self.assertFalse(any(_under(path, target.resolve()) for path in read_paths))

    def test_governance_ancestor_symlink_to_workspace_is_not_read_or_enumerated(self):
        source = self.root / "docs" / "governance"
        target = self.root / "workspace" / "synthetic-governance"
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        shutil.rmtree(source)
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        read_paths: list[Path] = []
        glob_paths: list[Path] = []
        original_read_text = Path.read_text
        original_glob = Path.glob

        def recording_read_text(path: Path, *args, **kwargs):
            read_paths.append(path.resolve())
            return original_read_text(path, *args, **kwargs)

        def recording_glob(path: Path, *args, **kwargs):
            glob_paths.append(path.resolve())
            return original_glob(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", recording_read_text), mock.patch.object(
            Path, "glob", recording_glob
        ):
            findings = self.findings()
        resolved_target = target.resolve()
        self.assertTrue(findings)
        self.assertFalse(any(_under(path, resolved_target) for path in read_paths))
        self.assertFalse(any(_under(path, resolved_target) for path in glob_paths))

    def test_agents_ancestor_symlink_to_workspace_is_not_read_or_enumerated(self):
        source = self.root / ".agents"
        target = self.root / "workspace" / "synthetic-agents"
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        shutil.rmtree(source)
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        read_paths: list[Path] = []
        iterdir_paths: list[Path] = []
        original_read_text = Path.read_text
        original_iterdir = Path.iterdir

        def recording_read_text(path: Path, *args, **kwargs):
            read_paths.append(path.resolve())
            return original_read_text(path, *args, **kwargs)

        def recording_iterdir(path: Path):
            iterdir_paths.append(path.resolve())
            return original_iterdir(path)

        with mock.patch.object(Path, "read_text", recording_read_text), mock.patch.object(
            Path, "iterdir", recording_iterdir
        ):
            findings = self.findings()
        resolved_target = target.resolve()
        self.assertTrue(findings)
        self.assertFalse(any(_under(path, resolved_target) for path in read_paths))
        self.assertFalse(any(_under(path, resolved_target) for path in iterdir_paths))

    def test_link_destination_into_workspace_is_rejected_without_read(self):
        private = self.root / "workspace" / "synthetic-private" / "secret.md"
        private.parent.mkdir(parents=True)
        private.write_text("# synthetic only\n", encoding="utf-8")
        _write(self.root, "README.md", "[private](workspace/synthetic-private/secret.md)\n")
        read_paths: list[Path] = []
        original_read_text = Path.read_text

        def recording_read_text(path: Path, *args, **kwargs):
            read_paths.append(path.resolve())
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", recording_read_text):
            findings = self.findings()
        self.assertTrue(
            any(item.startswith("link-target-ignored-root:") for item in findings),
            findings,
        )
        self.assertFalse(
            any(_under(path, private.parent.resolve()) for path in read_paths)
        )

    def test_duplicate_skill_name_is_a_finding(self):
        _write(
            self.root,
            ".agents/skills/fileholder-extra/SKILL.md",
            "---\n"
            "name: fileholder-investigate\n"
            "description: Use for a duplicate-name fixture.\n"
            "---\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("skill-duplicate-name: fileholder-investigate") for item in findings),
            findings,
        )

    def test_malformed_skill_metadata_is_a_finding(self):
        _write(
            self.root,
            ".agents/skills/fileholder-change/SKILL.md",
            "---\nname: fileholder-change\n---\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(
                item.startswith("skill-missing-description: .agents/skills/fileholder-change")
                for item in findings
            ),
            findings,
        )

    def test_invalid_decision_status_is_a_finding(self):
        _write(
            self.root,
            "docs/governance/decisions/0001-adopt-selective-agent-harness.md",
            "# Decision 0001\n\n- ID: 0001\n- Status: agreed\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("decision-invalid-status:") for item in findings),
            findings,
        )

    def test_accepted_decision_requires_recorded_approval(self):
        _write(
            self.root,
            "docs/governance/decisions/0001-adopt-selective-agent-harness.md",
            "# Decision 0001\n\n- ID: 0001\n- Status: accepted\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("decision-accepted-without-approval:") for item in findings),
            findings,
        )

    def test_missing_required_route_is_a_finding(self):
        _write(self.root, "AGENTS.md", "# Agent\n\n[README](README.md)\n")
        _write(
            self.root,
            ".agents/README.md",
            "[skills](skills/README.md) [governance](../docs/governance/README.md)\n",
        )
        findings = self.findings()
        self.assertTrue(
            any(item.startswith("missing-route: docs/architecture.md") for item in findings),
            findings,
        )

    def test_ignored_private_roots_and_external_symlinks_are_not_scanned(self):
        _write(self.root, "workspace/broken.md", "[x](missing.md)\n")
        _write(self.root, "input/broken.md", "[x](missing.md)\n")
        _write(self.root, ".env", "[x](missing.md)\n")
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "outside.md"
            external.write_text("[x](missing.md)\n", encoding="utf-8")
            try:
                (self.root / "outside-link.md").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
        self.assertEqual(self.findings(), [])

    def test_historical_documents_are_not_treated_as_active_links(self):
        _write(self.root, "docs/superpowers/historical.md", "[old](missing.md)\n")
        self.assertEqual(self.findings(), [])

    def test_findings_are_stable_and_repository_relative(self):
        _write(self.root, "README.md", "[broken](docs/missing.md)\n")
        first = self.findings()
        second = self.findings()
        self.assertEqual(first, second)
        self.assertTrue(all(not Path(item.split(": ", 1)[-1]).is_absolute() for item in first))

    def test_cli_exit_codes_and_no_traceback_for_findings(self):
        success = self._run_cli(self.root)
        self.assertEqual(success.returncode, 0, success.stderr)
        _write(self.root, "README.md", "[broken](docs/missing.md)\n")
        findings = self._run_cli(self.root)
        self.assertEqual(findings.returncode, 1)
        self.assertIn("missing-link: README.md", findings.stdout)
        self.assertNotIn("Traceback", findings.stderr)
        bad_root = self.root / "does-not-exist"
        invalid = self._run_cli(bad_root)
        self.assertEqual(invalid.returncode, 2)
        self.assertNotIn("Traceback", invalid.stderr)

    def _run_cli(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )


class RealRepositoryHarnessTest(unittest.TestCase):
    def test_knowledge_transfer_bytes_are_preserved(self):
        digest = hashlib.sha256(KNOWLEDGE_TRANSFER.read_bytes()).hexdigest()
        self.assertEqual(digest, KNOWLEDGE_TRANSFER_SHA256)

    def test_five_shared_skills_have_matching_ids(self):
        for skill in SKILL_IDS:
            path = ROOT / ".agents" / "skills" / skill / "SKILL.md"
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            self.assertIn(f"name: {skill}", text)
            self.assertIn("description:", text)
            self.assertNotIn("\t", text)

    def test_skill_index_links_each_skill(self):
        index = (ROOT / ".agents" / "skills" / "README.md").read_text(
            encoding="utf-8"
        )
        for skill in SKILL_IDS:
            self.assertIn(f"({skill}/SKILL.md)", index)

    def test_copilot_bootstrap_links_shared_sources(self):
        text = (ROOT / ".github" / "copilot-instructions.md").read_text(
            encoding="utf-8"
        )
        for target in (
            "../AGENTS.md",
            "../docs/README.md",
            "../.agents/skills/README.md",
        ):
            self.assertIn(target, text)

    def test_no_duplicate_client_skill_copies(self):
        self.assertFalse((ROOT / ".github" / "skills").exists())
        self.assertFalse((ROOT / ".gitlab" / "duo" / "chat-rules.md").exists())

    def test_real_repository_passes_structural_validation(self):
        validator = _load_validator()
        self.assertEqual(validator.validate_repository(ROOT), [])


if __name__ == "__main__":
    unittest.main()
