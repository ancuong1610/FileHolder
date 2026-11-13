"""Run the real Phase 1 comparison for PDFs configured in ``.env``.

This is a convenience wrapper around the supported ``python -m document_compare
run`` command. It does not replace the Phase 1 CLI and requires an editable
installation (``python -m pip install -e .``).

When Stage 01 is selected, set ``CRR_OLD_PDF`` and ``CRR_NEW_PDF`` in ``.env``.
Paths may be absolute or relative to the repository root. Later-stage reruns use
artifacts already saved in the selected workspace and do not require PDF paths.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parent
DEFAULT_WORKSPACE = REPOSITORY_ROOT / "workspace" / "my-first-test"
STAGES = tuple(f"{number:02d}" for number in range(1, 16))


def require_pdf_path(variable: str) -> Path:
    """Return an existing PDF path configured by ``variable``."""
    value = os.getenv(variable, "").strip()
    if not value:
        raise SystemExit(
            f"{variable} is not set. Add it to {REPOSITORY_ROOT / '.env'}, for example:\n"
            f"{variable}=input/your-document.pdf"
        )

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"{variable} does not point to an existing file: {path}")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=(
            "Pipeline workspace. Defaults to CRR_PROJECT_ROOT from .env, then "
            "workspace/my-first-test."
        ),
    )
    parser.add_argument(
        "--from-stage",
        choices=STAGES,
        default="01",
        help="First pipeline stage to run (default: 01).",
    )
    parser.add_argument(
        "--to-stage",
        choices=STAGES,
        default="15",
        help="Last pipeline stage to run (default: 15).",
    )
    return parser.parse_args()


def resolve_workspace(value: Path | None) -> Path:
    """Resolve an explicit workspace or the configured/default workspace."""
    candidate = value or Path(os.getenv("CRR_PROJECT_ROOT", DEFAULT_WORKSPACE))
    candidate = candidate.expanduser()
    if not candidate.is_absolute():
        candidate = REPOSITORY_ROOT / candidate
    return candidate.resolve()


def main() -> None:
    args = parse_args()
    if STAGES.index(args.from_stage) > STAGES.index(args.to_stage):
        raise SystemExit("--from-stage must not be after --to-stage.")

    # Repository configuration supplies credentials and default paths; command
    # line choices remain authoritative because dotenv does not override env vars.
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    workspace = resolve_workspace(args.workspace)

    env = dict(os.environ)
    env["CRR_PROJECT_ROOT"] = str(workspace)
    if args.from_stage == "01":
        env["CRR_OLD_PDF"] = str(require_pdf_path("CRR_OLD_PDF"))
        env["CRR_NEW_PDF"] = str(require_pdf_path("CRR_NEW_PDF"))

    command = [
        sys.executable,
        "-m",
        "document_compare",
        "run",
        "--root",
        str(workspace),
        "--from-stage",
        args.from_stage,
        "--to-stage",
        args.to_stage,
    ]
    completed = subprocess.run(command, cwd=REPOSITORY_ROOT, env=env)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
