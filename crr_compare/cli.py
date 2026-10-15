"""CLI runner for the CRR comparison pipeline."""
from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

STAGES = [
    ("01", "s01_extract.py"),
    ("02", "s02_validate_extraction.py"),
    ("03", "s03_internal_schema.py"),
    ("04", "s04_normalize.py"),
    ("05", "s05_anchors.py"),
    ("06", "s06_exact_matching.py"),
    ("07", "s07_modified_matching.py"),
    ("08", "s08_candidates.py"),
    ("09", "s09_movement.py"),
    ("10", "s10_tables.py"),
    ("11", "s11_routing.py"),
    ("12", "s12_packets.py"),
    ("13", "s13_llm_evaluator.py"),
    ("14", "s14_canonical_result.py"),
    ("15", "s15_serve.py"),
]


def stage_path(number: str) -> Path:
    for num, name in STAGES:
        if num == number:
            return REPO_ROOT / "crr_compare" / "stages" / name
    raise SystemExit(f"Unknown stage {number!r}")


def run_stages(numbers: list[str], root: Path) -> None:
    os.environ["CRR_PROJECT_ROOT"] = str(root)
    started = time.perf_counter()
    for number in numbers:
        path = stage_path(number)
        print(f"\n{'=' * 70}\nSTAGE {number}: {path.name}\n{'=' * 70}", flush=True)
        stage_start = time.perf_counter()
        try:
            runpy.run_path(str(path), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise SystemExit(f"Stage {number} failed with exit code {exc.code}") from exc
        print(f"--- stage {number} finished in {time.perf_counter() - stage_start:.1f}s")
    print(f"\nAll selected stages completed in {time.perf_counter() - started:.1f}s")


DEMO_FIXTURES = ("crr3", "internal_policy_report", "scai_api_guide")
DEMO_DEFAULT_FIXTURE = "internal_policy_report"
DEMO_STAGE_RANGE = ("02", "15")
DEMO_FIXTURE_FILES = ("old_di_raw.json", "new_di_raw.json")


def fixture_dir(fixture: str) -> Path:
    """Directory holding one bundled, already-extracted DI fixture pair."""
    return REPO_ROOT / "test_data" / "fixtures_general" / fixture


def _seed_demo_fixture(fixture: str, root: Path) -> None:
    """Copy the bundled already-extracted DI JSON into ``<root>/output/``.

    No PDF, DI, TIA, or LLM call is involved: the fixture pair is exactly the
    Stage 01 output the later stages consume.
    """
    source = fixture_dir(fixture)
    missing = [name for name in DEMO_FIXTURE_FILES if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            f"[MOCK/SYNTHETIC] Demo fixture {fixture!r} is missing "
            f"{', '.join(missing)} under {source}. "
            "Restore the bundled fixture files and retry."
        )
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    for name in DEMO_FIXTURE_FILES:
        shutil.copyfile(source / name, out / name)


def _ensure_demo_root_available(root: Path) -> None:
    """Refuse to run into a root whose output already holds data."""
    out = root / "output"
    if out.exists() and any(out.iterdir()):
        raise SystemExit(
            f"[MOCK/SYNTHETIC] Refusing to overwrite existing output in {out}. "
            "Choose a new --root directory."
        )


def _demo_child_env(root: Path) -> dict[str, str]:
    """Child-only environment: offline mock evaluator plus generic domain.

    Returns a fresh copy: the parent process environment is never mutated.
    """
    env = dict(os.environ)
    env["CRR_RUN_LLM"] = "mock"
    env["CRR_DOMAIN_MODE"] = "generic"
    env["CRR_PROJECT_ROOT"] = str(root)
    return env


def _demo_command(root: Path) -> list[str]:
    """Child CLI invocation: stages 02-15 only (never Stage 01 extraction)."""
    return [
        sys.executable,
        "-m",
        "crr_compare",
        "run",
        "--root",
        str(root),
        "--from-stage",
        DEMO_STAGE_RANGE[0],
        "--to-stage",
        DEMO_STAGE_RANGE[1],
    ]


def _write_demo_provenance(root: Path, fixture: str) -> Path:
    """Write ``output/mock_run.json``: synthetic provenance, not canonical data."""
    provenance = {
        "schema_version": "1.0.0",
        "run_kind": "mock_synthetic_demo",
        "synthetic": True,
        "not_a_pdf_comparison": True,
        "fixture": fixture,
        "stage_range": list(DEMO_STAGE_RANGE),
        "evaluator": "mock",
        "domain_mode": "generic",
        "uses_document_intelligence": False,
        "uses_uploaded_pdfs": False,
        "source_extraction": (
            f"test_data/fixtures_general/{fixture}/{{old,new}}_di_raw.json "
            "(bundled, already extracted)"
        ),
        "canonical_result": "output/comparison_result.json",
        "redline_pdf": "output/comparison_redline.pdf",
    }
    path = root / "output" / "mock_run.json"
    path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_demo(fixture: str, root: Path) -> Path:
    """Run the offline synthetic demo for one bundled fixture pair.

    Seeds the already-extracted DI JSON (no Stage 01), then runs Stages 02-15
    in a child process whose environment forces the mock evaluator and the
    generic domain. The parent environment is never mutated.
    """
    if fixture not in DEMO_FIXTURES:
        raise SystemExit(
            f"Unknown demo fixture {fixture!r}; choose one of {', '.join(DEMO_FIXTURES)}."
        )
    root = Path(root).expanduser().resolve()
    _ensure_demo_root_available(root)
    _seed_demo_fixture(fixture, root)

    env = _demo_child_env(root)
    command = _demo_command(root)

    print(f"[MOCK/SYNTHETIC] Offline demo: fixture={fixture} root={root}")
    print(f"[MOCK/SYNTHETIC] Stages {DEMO_STAGE_RANGE[0]}-{DEMO_STAGE_RANGE[1]}, mock evaluator, generic domain.")
    print(
        "[MOCK/SYNTHETIC] No DI/TIA/LLM calls and no uploaded PDFs; "
        "this is not a comparison of your own PDFs."
    )

    completed = subprocess.run(command, cwd=str(REPO_ROOT), env=env)
    if completed.returncode != 0:
        raise SystemExit(
            f"[MOCK/SYNTHETIC] Demo pipeline failed (exit {completed.returncode})."
        )

    provenance_path = _write_demo_provenance(root, fixture)
    report = root / "output" / "comparison_result.json"
    redline = root / "output" / "comparison_redline.pdf"
    if not report.is_file() or not redline.is_file():
        raise SystemExit(
            "[MOCK/SYNTHETIC] Demo finished but did not produce "
            f"{report.name} / {redline.name}."
        )
    print(f"[MOCK/SYNTHETIC] Artifacts: {report} | {redline} | {provenance_path}")
    return root


def parse_stage_range(args) -> list[str]:
    numbers = [num for num, _ in STAGES]
    start = args.from_stage or numbers[0]
    end = args.to_stage or numbers[-1]
    if start not in numbers or end not in numbers or numbers.index(start) > numbers.index(end):
        raise SystemExit(f"Invalid stage range {start}..{end}")
    return [num for num in numbers if numbers.index(start) <= numbers.index(num) <= numbers.index(end)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="crr_compare", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--root", type=Path, default=REPO_ROOT / "workspace",
                       help="Pipeline workspace root (default: ./workspace)")

    run_p = sub.add_parser("run", help="Run pipeline stages in order")
    common(run_p)
    run_p.add_argument("--from-stage", choices=[n for n, _ in STAGES])
    run_p.add_argument("--to-stage", choices=[n for n, _ in STAGES])

    eval_p = sub.add_parser("eval", help="Run identity + mutation evaluation suite")
    common(eval_p)
    eval_p.add_argument("--seed", type=int, default=42)
    eval_p.add_argument("--skip-identity", action="store_true")

    redline_p = sub.add_parser("redline", help="Regenerate the HTML redline only")
    common(redline_p)

    gt_p = sub.add_parser(
        "eval-gt",
        help="Score output against a BU ground-truth benchmark JSON",
    )
    common(gt_p)
    gt_p.add_argument("--gt", type=Path, default=REPO_ROOT / "test_data" / "GroundTruth.json")

    demo_p = sub.add_parser(
        "demo",
        help="Run the bundled-data offline synthetic demo (stages 02-15, mock)",
    )
    common(demo_p)
    demo_p.add_argument(
        "--fixture",
        choices=list(DEMO_FIXTURES),
        default=DEMO_DEFAULT_FIXTURE,
        help=f"Bundled already-extracted DI fixture pair (default: {DEMO_DEFAULT_FIXTURE})",
    )

    bench_p = sub.add_parser(
        "benchmark-performance",
        help="Run a controlled synthetic benchmark over a saved Step-3 model (offline)",
    )
    bench_p.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="Existing pipeline output directory holding old_document.json (read-only)",
    )
    bench_p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Fresh output directory for the benchmark bundle",
    )
    bench_p.add_argument("--seed", type=int, default=42)

    review_p = sub.add_parser(
        "review-evidence",
        help="Build an offline saved-artifact structure/evidence review bundle",
    )
    common(review_p)
    review_p.add_argument(
        "--artifacts",
        type=Path,
        required=True,
        help="Existing pipeline output directory (read-only saved artifacts)",
    )
    review_p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Fresh output directory for the review bundle",
    )
    review_p.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="Optional versioned reference annotation JSON",
    )

    args = parser.parse_args(argv)
    root: Path | None = (
        args.root.expanduser().resolve() if getattr(args, "root", None) is not None else None
    )

    if args.command == "run":
        run_stages(parse_stage_range(args), root)
    elif args.command == "redline":
        os.environ["CRR_PROJECT_ROOT"] = str(root)
        runpy.run_path(str(REPO_ROOT / "crr_compare" / "redline.py"), run_name="__main__")
    elif args.command == "eval":
        from crr_compare.evaluate import evaluate_command

        evaluate_command(root=root, seed=args.seed, skip_identity=args.skip_identity)
    elif args.command == "eval-gt":
        from crr_compare.gt_benchmark import run as run_gt

        run_gt(root, args.gt.expanduser().resolve())
    elif args.command == "demo":
        run_demo(args.fixture, root)
    elif args.command == "benchmark-performance":
        from crr_compare.performance_benchmark import benchmark_performance_command

        benchmark_performance_command(
            artifacts=args.artifacts,
            out=args.out,
            seed=args.seed,
        )
    elif args.command == "review-evidence":
        from crr_compare.evidence_review import review_evidence_command

        review_evidence_command(
            artifacts=args.artifacts,
            out=args.out,
            reference=args.reference,
        )


if __name__ == "__main__":
    main()
