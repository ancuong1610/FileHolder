# Harness validator

`validate.py` performs deterministic, read-only structural checks for the shared
repository coding-agent harness. It is optional development tooling, not a
runtime dependency or an agent-effectiveness test.

```bash
python tools/harness/validate.py
python tools/harness/validate.py --root .
```

Exit codes: `0` no findings, `1` findings, `2` invalid/unavailable root. Findings
are sorted repository-relative diagnostics such as `missing-link:
README.md -> docs/missing.md`; no traceback is expected for an ordinary invalid
fixture.

## Checks

- Required root, client bootstrap, documentation, governance, skill and tool
  entrypoints exist as regular files.
- Declared active files and directory listings fail closed on symlinked files or
  symlinked ancestor components, even when the link target is inside the selected
  root. Links into `workspace/`, `input/`, `.git`, `.env`, virtual environments or
  model/cache roots are rejected before content reads or directory enumeration.
  The selected root itself may be normalized (for example macOS `/var` to
  `/private/var`); only declared paths below it are constrained.
- Active Markdown links resolve as local files/directories, including encoded
  spaces. External URLs are ignored, fenced code examples are not treated as
  links, and destinations escaping the selected root are rejected.
- The five `fileholder-` skills have a `SKILL.md`, matching directory/name,
  description, unique ID and an index entry. No per-client skill copies are
  allowed.
- Decision files have an ID matching the filename, a valid status, an index entry
  and a recorded approval before `accepted`.
- Task routes link Knowledge Transfer, architecture, phase testing, review guide
  and current contracts.

## Scope and limits

The validator reads only declared active documents and immediate skill/decision
directories. It never scans `workspace/`, `input/`, `.env`, `.git`, virtual
environments, model caches or the external reference checkout. It does not follow
paths outside the selected root, fetch URLs, execute commands or inspect prose
semantics. Historical documents are not rewritten or treated as active routes.

Run the fixture tests with:

```bash
python -m unittest tests.integration.test_harness
```

Passing means the declared files and links are structurally consistent, not that
Copilot/GitLab Duo loaded a skill or that comparison accuracy improved.
