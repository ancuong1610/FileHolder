"""Read-only structural validation for the repository coding-agent harness.

The validator checks declared active entrypoints and local Markdown paths, the
five shared project skills, and the new governance decision records. It rejects
symlinked declared paths/components and link destinations into ignored roots
before content reads or directory enumeration. It does not scan ignored
input/workspace trees, fetch URLs, execute documented commands, or prove that a
client loaded a skill.

Usage:
    python tools/harness/validate.py [--root PATH]

Exit codes: 0 no findings, 1 findings, 2 invalid/unavailable root.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

SKILL_IDS = (
    "fileholder-investigate",
    "fileholder-change",
    "fileholder-evaluate",
    "fileholder-read-results",
    "fileholder-handover",
)

REQUIRED_FILES = (
    "AGENTS.md",
    "README.md",
    ".github/copilot-instructions.md",
    ".agents/README.md",
    ".agents/skills/README.md",
    *(f".agents/skills/{skill}/SKILL.md" for skill in SKILL_IDS),
    "docs/README.md",
    "docs/architecture.md",
    "docs/review-guide.md",
    "docs/KnowlesgeTranser Phase 1.md",
    "docs/operations/README.md",
    "docs/operations/phase-testing.md",
    "docs/operations/agent-harness-quickstart.md",
    "docs/contracts/README.md",
    "docs/governance/README.md",
    "docs/governance/authority.md",
    "docs/governance/workflows.md",
    "docs/governance/definition-of-done.md",
    "docs/governance/decisions/README.md",
    "docs/governance/decisions/0001-adopt-selective-agent-harness.md",
    "tools/README.md",
    "tools/harness/README.md",
    "tools/harness/validate.py",
)

ROUTE_SOURCES = (
    "AGENTS.md",
    ".agents/README.md",
    ".agents/skills/README.md",
)

REQUIRED_ROUTES = (
    "docs/KnowlesgeTranser Phase 1.md",
    "docs/architecture.md",
    "docs/operations/phase-testing.md",
    "docs/review-guide.md",
    "docs/contracts/README.md",
)

OPTIONAL_ACTIVE_DOCUMENTS = (
    "docs/superpowers/README.md",
    "tests/integration/README.md",
)

SKILL_NAME_PATTERN = re.compile(r"^fileholder-[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_NAME_LENGTH = 64
ALLOWED_DECISION_STATUSES = {"accepted", "proposed", "superseded", "rejected"}
ESCAPES_ROOT = "<outside-repository>"
SYMLINK_TARGET = "<symlink-target>"
IGNORED_TARGET = "<ignored-root>"
IGNORED_FIRST_COMPONENTS = {
    ".env",
    ".git",
    ".venv",
    "cache",
    "input",
    "models",
    "model-cache",
    "model_cache",
    "runtime",
    "temp",
    "venv",
    "workspace",
}
LINK_PATTERN = re.compile(
    r"(?<!!)\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)"
)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")
SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _safe_declared_path(
    root: Path, relative: str, findings: list[str], label: str
) -> Path | None:
    """Return a declared path only when no component below root is a symlink.

    Root itself may be an explicitly selected/normalized directory. Every
    component of the declared relative path is checked with lstat only, so a
    symlink into an ignored workspace/input tree is rejected before any stat or
    content read reaches its target.
    """
    current = root
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            findings.append(f"{label}-escapes-root: {relative}")
            return None
        current = current / part
        try:
            if current.is_symlink():
                findings.append(f"{label}-symlink: {relative}")
                return None
        except OSError:
            findings.append(f"{label}-unavailable: {relative}")
            return None
    return current


def _safe_file(
    root: Path, relative: str, findings: list[str], label: str
) -> Path | None:
    path = _safe_declared_path(root, relative, findings, label)
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        findings.append(f"{label}-unavailable: {relative}")
        return None
    return path


def _safe_directory(
    root: Path, relative: str, findings: list[str], label: str
) -> Path | None:
    path = _safe_declared_path(root, relative, findings, label)
    if path is None:
        return None
    try:
        if not path.is_dir():
            return None
    except OSError:
        findings.append(f"{label}-unavailable: {relative}")
        return None
    return path


def _read_active_text(
    root: Path, relative: str, findings: list[str]
) -> str | None:
    path = _safe_file(root, relative, findings, "active-path")
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        findings.append(f"active-path-unreadable: {relative}")
        return None


def _split_fences(text: str):
    """Yield (line_number, line) only outside fenced code blocks."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        yield number, line


def _raw_links(text: str):
    for _number, line in _split_fences(text):
        line = INLINE_CODE_PATTERN.sub("", line)
        for match in LINK_PATTERN.finditer(line):
            destination = match.group(1)
            if destination.startswith("<") and destination.endswith(">"):
                destination = destination[1:-1]
            yield destination


def _resolve_link(root: Path, source_relative: str, destination: str) -> str | None:
    """Resolve a Markdown destination without following or reading symlinks."""
    destination = destination.strip()
    if not destination or destination.startswith("#"):
        return None
    if SCHEME_PATTERN.match(destination):
        return None
    path_part = destination.split("#", 1)[0].split("?", 1)[0]
    if not path_part:
        return None
    path_part = unquote(path_part)
    if path_part.startswith("/"):
        current = root
        parts = path_part.lstrip("/").split("/")
    else:
        current = (root / source_relative).parent
        parts = path_part.split("/")
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if current == root:
                return ESCAPES_ROOT
            current = current.parent
            continue
        current = current / part
        try:
            relative = current.relative_to(root).as_posix()
        except ValueError:
            return ESCAPES_ROOT
        if relative.split("/", 1)[0] in IGNORED_FIRST_COMPONENTS:
            return IGNORED_TARGET
        try:
            if current.is_symlink():
                return SYMLINK_TARGET
        except OSError:
            return None
    try:
        return current.relative_to(root).as_posix()
    except ValueError:
        return ESCAPES_ROOT


def _links_in_document(
    root: Path, relative: str, findings: list[str]
) -> list[tuple[str, str]]:
    text = _read_active_text(root, relative, findings)
    if text is None:
        return []
    links = []
    for destination in _raw_links(text):
        resolved = _resolve_link(root, relative, destination)
        links.append((destination, resolved))
    return links


def _valid_skill_name(name: str) -> bool:
    return (
        len(name) <= MAX_SKILL_NAME_LENGTH
        and SKILL_NAME_PATTERN.fullmatch(name) is not None
    )


def _unquote_scalar(value: str) -> str | None:
    value = value.strip()
    if not value:
        return ""
    if value[0] in "\"'":
        if len(value) < 2 or value[-1] != value[0]:
            return None
        return value[1:-1]
    if value[-1] in "\"'":
        return None
    return value


def _frontmatter(text: str) -> dict[str, str | None] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str | None] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = _unquote_scalar(value)
    return None


def _field(text: str, name: str) -> str | None:
    pattern = re.compile(
        r"^\s*[-*]?\s*" + re.escape(name) + r"\s*:\s*(.+?)\s*$", re.IGNORECASE
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip("\"'")
    return None


def _check_required_paths(root: Path, findings: list[str]) -> None:
    for relative in REQUIRED_FILES:
        path = _safe_declared_path(root, relative, findings, "required-path")
        if path is None:
            continue
        try:
            if not path.is_file():
                findings.append(f"missing-required-path: {relative}")
        except OSError:
            findings.append(f"missing-required-path: {relative}")


def _check_routes(root: Path, findings: list[str]) -> None:
    resolved: set[str] = set()
    for source in ROUTE_SOURCES:
        for _destination, target in _links_in_document(root, source, findings):
            if target and target not in {ESCAPES_ROOT, SYMLINK_TARGET, IGNORED_TARGET}:
                resolved.add(target)
    for required in REQUIRED_ROUTES:
        if required not in resolved:
            findings.append(f"missing-route: {required}")


def _check_markdown_links(root: Path, findings: list[str]) -> None:
    documents = set(REQUIRED_FILES) | set(OPTIONAL_ACTIVE_DOCUMENTS)
    for relative in sorted(documents):
        if not relative.endswith(".md"):
            continue
        for destination, target in _links_in_document(root, relative, findings):
            if target == ESCAPES_ROOT:
                findings.append(f"link-escapes-root: {relative} -> {destination}")
            elif target == SYMLINK_TARGET:
                findings.append(f"link-symlink-target: {relative} -> {destination}")
            elif target == IGNORED_TARGET:
                findings.append(f"link-target-ignored-root: {relative} -> {destination}")
            elif target is not None:
                path = _safe_declared_path(root, target, findings, "link-target")
                if path is None:
                    continue
                try:
                    if not path.exists():
                        findings.append(f"missing-link: {relative} -> {destination}")
                except OSError:
                    findings.append(f"missing-link: {relative} -> {destination}")


def _check_skills(root: Path, findings: list[str]) -> None:
    skills_root = _safe_directory(root, ".agents/skills", findings, "skills-directory")
    if skills_root is None:
        return
    names: dict[str, str] = {}
    indexed: set[str] = set()
    for _destination, target in _links_in_document(
        root, ".agents/skills/README.md", findings
    ):
        if target:
            indexed.add(target)
    for entry in sorted(skills_root.iterdir()):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        relative = entry.relative_to(root).as_posix()
        skill_relative = f"{relative}/SKILL.md"
        skill_path = _safe_declared_path(
            root, skill_relative, findings, "skill-entrypoint"
        )
        if skill_path is None:
            continue
        try:
            if not skill_path.is_file():
                findings.append(f"skill-missing-entrypoint: {relative}")
                continue
        except OSError:
            findings.append(f"skill-missing-entrypoint: {relative}")
            continue
        if not _valid_skill_name(entry.name):
            findings.append(f"skill-invalid-directory: {relative}")
        readme = entry / "README.md"
        try:
            if readme.is_symlink() or readme.is_file():
                findings.append(f"skill-redundant-readme: {relative}")
        except OSError:
            findings.append(f"skill-redundant-readme: {relative}")
        try:
            field_data = _frontmatter(skill_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            findings.append(f"skill-entrypoint-unreadable: {skill_relative}")
            continue
        if field_data is None:
            findings.append(f"skill-frontmatter-missing: {relative}")
            continue
        name = ""
        if "name" not in field_data:
            findings.append(f"skill-missing-name: {relative}")
        elif field_data["name"] is None:
            findings.append(f"skill-malformed-name: {relative}")
        else:
            name = (field_data["name"] or "").strip()
            if not name:
                findings.append(f"skill-missing-name: {relative}")
            else:
                if name != entry.name:
                    findings.append(f"skill-name-mismatch: {relative} (name={name})")
                if not _valid_skill_name(name):
                    findings.append(f"skill-invalid-name: {relative} (name={name})")
        description = ""
        if "description" not in field_data:
            findings.append(f"skill-missing-description: {relative}")
        elif field_data["description"] is None:
            findings.append(f"skill-malformed-description: {relative}")
        else:
            description = (field_data["description"] or "").strip()
            if not description:
                findings.append(f"skill-missing-description: {relative}")
            elif len(description) > 500:
                findings.append(f"skill-description-too-long: {relative}")
        if name:
            previous = names.get(name)
            if previous is not None:
                findings.append(f"skill-duplicate-name: {name}")
            else:
                names[name] = relative
        if f"{relative}/SKILL.md" not in indexed:
            findings.append(f"skill-not-indexed: {relative}")
    for skill in SKILL_IDS:
        relative = f".agents/skills/{skill}/SKILL.md"
        path = _safe_declared_path(root, relative, findings, "skill-entrypoint")
        if path is None:
            continue
        try:
            if not path.is_file():
                findings.append(f"skill-missing: {skill}")
        except OSError:
            findings.append(f"skill-missing: {skill}")
    client_skills = root / ".github" / "skills"
    duo_chat_rules = root / ".gitlab" / "duo" / "chat-rules.md"
    try:
        if client_skills.is_symlink() or client_skills.exists():
            findings.append("duplicate-client-skills: .github/skills")
    except OSError:
        findings.append("duplicate-client-skills: .github/skills")
    try:
        if duo_chat_rules.is_symlink() or duo_chat_rules.exists():
            findings.append("duplicate-duo-chat-rules: .gitlab/duo/chat-rules.md")
    except OSError:
        findings.append("duplicate-duo-chat-rules: .gitlab/duo/chat-rules.md")


def _check_decisions(root: Path, findings: list[str]) -> None:
    decisions_root = _safe_directory(
        root, "docs/governance/decisions", findings, "decisions-directory"
    )
    if decisions_root is None:
        return
    index_relative = "docs/governance/decisions/README.md"
    indexed = {
        target
        for _destination, target in _links_in_document(
            root, index_relative, findings
        )
        if target
    }
    for path in sorted(decisions_root.glob("*.md")):
        if path.name == "README.md":
            continue
        relative = path.relative_to(root).as_posix()
        decision_path = _safe_declared_path(
            root, relative, findings, "decision"
        )
        if decision_path is None:
            continue
        if not decision_path.is_file():
            continue
        try:
            text = decision_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"decision-unreadable: {relative}")
            continue
        decision_id = _field(text, "ID")
        status = _field(text, "Status")
        approval = _field(text, "Approval")
        if not decision_id:
            findings.append(f"decision-missing-id: {relative}")
        elif decision_id != path.name.split("-", 1)[0]:
            findings.append(f"decision-id-mismatch: {relative}")
        if not status:
            findings.append(f"decision-missing-status: {relative}")
        elif status.lower() not in ALLOWED_DECISION_STATUSES:
            findings.append(f"decision-invalid-status: {relative} (status={status})")
        elif status.lower() == "accepted" and not approval:
            findings.append(f"decision-accepted-without-approval: {relative}")
        if relative not in indexed:
            findings.append(f"decision-not-indexed: {relative}")
    governance_readme = "docs/governance/README.md"
    governance_links = {
        target
        for _destination, target in _links_in_document(
            root, governance_readme, findings
        )
        if target
    }
    if index_relative not in governance_links:
        findings.append(f"missing-decision-index-link: {governance_readme}")


def validate_repository(root: Path) -> list[str]:
    """Return sorted structural findings for an existing repository root."""
    root = Path(root)
    try:
        if not root.is_dir():
            return ["invalid-root"]
        root = root.resolve()
    except (OSError, RuntimeError):
        return ["invalid-root"]
    if not root.is_dir():
        return ["invalid-root"]
    findings: list[str] = []
    _check_required_paths(root, findings)
    _check_routes(root, findings)
    _check_markdown_links(root, findings)
    _check_skills(root, findings)
    _check_decisions(root, findings)
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the repository coding-agent harness structure."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: the repository containing this script)",
    )
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else Path(__file__).resolve().parents[2]
    if not root.is_dir():
        print(f"invalid root: {root}", file=sys.stderr)
        return 2
    try:
        findings = validate_repository(root)
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        print(f"validation-error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding)
        return 1
    print("Harness structure valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
