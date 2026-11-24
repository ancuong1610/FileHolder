"""Apply this migration package to a work repo without shared git history.

For every file in changed-files.txt:
  A (added)    -> copy files/<path> if missing; report if a different file exists.
  M (modified) -> work == base  : replace with files/<path>
                  work == new   : already applied, skip
                  otherwise     : 3-way merge (git merge-file) of work / base / new,
                                  keeping work-PC changes; conflicts get markers.
Line endings: files are compared ignoring CRLF/LF, and every written file keeps
the line ending style the work file already had (new files keep the package's).

Default is a dry run. Usage (from anywhere):
  python apply_migration.py --repo <work repo root>            # report only
  python apply_migration.py --repo <work repo root> --apply    # write changes
Standard library + the `git` executable only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
BASE_LABEL, NEW_LABEL = "bbc17b5", "19db943"


def lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def eol_of(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def with_eol(data: bytes, eol: bytes) -> bytes:
    data = lf(data)
    return data.replace(b"\n", eol) if eol == b"\r\n" else data


def merge(work: bytes, base: bytes, new: bytes) -> tuple[bytes, int]:
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name, data in (("work", work), ("base", base), ("new", new)):
            path = Path(folder) / name
            path.write_bytes(lf(data))
            paths.append(str(path))
        result = subprocess.run(
            ["git", "merge-file", "-L", "work-pc", "-L", BASE_LABEL, "-L", NEW_LABEL, *paths],
            capture_output=True,
        )
        if result.returncode < 0 or result.returncode > 127:
            sys.exit(f"git merge-file failed: {result.stderr.decode(errors='replace')}")
        return Path(paths[0]).read_bytes(), result.returncode  # returncode = conflict count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo / "src" / "document_compare").is_dir():
        sys.exit(f"{repo} is not the FileHolder repo root (no src/document_compare)")

    report: dict[str, list[str]] = {
        "replaced": [], "added": [], "already_applied": [], "merged_clean": [],
        "merged_with_conflicts": [], "added_but_exists_different": [],
    }
    for line in (PACKAGE / "changed-files.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        status, rel = line.split("\t", 1)
        target, new = repo / rel, (PACKAGE / "files" / rel).read_bytes()
        if status == "A":
            if not target.exists():
                report["added"].append(rel)
                out = new
            elif lf(target.read_bytes()) == lf(new):
                report["already_applied"].append(rel)
                continue
            else:
                report["added_but_exists_different"].append(rel)
                continue
        elif status == "M":
            if not target.exists():
                report["added_but_exists_different"].append(rel + "  (missing on work PC)")
                continue
            work, base = target.read_bytes(), (PACKAGE / "base" / rel).read_bytes()
            if lf(work) == lf(new):
                report["already_applied"].append(rel)
                continue
            if lf(work) == lf(base):
                report["replaced"].append(rel)
                out = with_eol(new, eol_of(work))
            else:
                merged, conflicts = merge(work, base, new)
                key = "merged_with_conflicts" if conflicts else "merged_clean"
                report[key].append(f"{rel}  ({conflicts} conflict(s))" if conflicts else rel)
                out = with_eol(merged, eol_of(work))
        else:
            sys.exit(f"unexpected status {status!r} for {rel}")
        if args.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(out)

    print("APPLIED" if args.apply else "DRY RUN (nothing written; add --apply)")
    for key, items in report.items():
        print(f"\n{key}: {len(items)}")
        for item in items:
            print(f"  {item}")
    if report["merged_with_conflicts"] or report["added_but_exists_different"]:
        print("\nResolve the listed files by hand (search for '<<<<<<< work-pc'), "
              "then run the tests. Reference versions: files/ (new) and base/ (bbc17b5).")


if __name__ == "__main__":
    main()
