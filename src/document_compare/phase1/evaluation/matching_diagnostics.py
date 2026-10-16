"""Matching diagnostics for s07: retrieval vs ranking vs selection.

**Input → Work → Output → Review**

- Input: a synthetic labeled fixture (``tests/fixtures/regression/
  matching_pairs.json``) holding one s07 ``exact`` payload and hand-authored
  truth pairs, plus the real ``s07_modified_matching.py`` algorithm.
- Work: import the import-safe s07 module, run ``build_result`` with the opt-in
  diagnostic collector, compare the collected element-ID trace against the
  authored truth, and repeat serially in fresh subprocesses while sweeping one
  threshold at a time.
- Output: ``<root>/matching_diagnostics_report.json`` plus a plain-text
  summary of per-stage coverage and misses. Reports never overwrite an
  existing file.
- Review: any report should be re-read as a measurement on one tiny synthetic
  fixture, not as accuracy on arbitrary PDFs. No production default is
  changed; overrides are applied in memory for one call and restored in a
  ``finally`` block.

s07 keeps all file orchestration inside ``main()`` behind a
``__name__ == "__main__"`` guard, so this module can import it directly and
call :func:`~document_compare.phase1.stages.s07_modified_matching.build_result` without
seeding a temporary workspace or running the stage twice.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from document_compare.shared.paths import REPO_ROOT
S07_PATH = Path(__file__).resolve().parent.parent / "stages" / "s07_modified_matching.py"
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "regression" / "matching_pairs.json"
REPORT_NAME = "matching_diagnostics_report.json"

# Documented constants (see docs/phase1-matching.md). The historical
# calibration of these values is not recorded in this repository; they are the
# existing defaults and are deliberately not retuned here.
DOCUMENTED_CONSTANTS = {
    "MIN_REVIEW_SCORE": 0.72,
    "MIN_AUTOMATIC_SCORE": 0.86,
    "MIN_AUTOMATIC_MARGIN": 0.10,
    "SHORT_TEXT_REVIEW_CHARACTERS": 40,
    "MAX_CONTEXT_RETRIEVAL_CANDIDATES": 40,
    "MAX_TOKEN_FALLBACK_CANDIDATES": 30,
    "MAX_EXPENSIVE_CANDIDATES_PER_OLD": 12,
    "CHEAP_TOKEN_WEIGHT": 0.80,
    "CHEAP_LENGTH_WEIGHT": 0.20,
    "similarity_weights": {
        "char_ratio": 0.65,
        "token_f1": 0.25,
        "length_ratio": 0.10,
    },
    "historical_calibration": (
        "not recorded in this repository; these are the existing defaults and "
        "were not retuned in this packet"
    ),
}

_ALLOWED_OVERRIDES = ("MIN_REVIEW_SCORE", "MIN_AUTOMATIC_SCORE", "MIN_AUTOMATIC_MARGIN")


def load_fixture(path: Path) -> dict[str, Any]:
    """Read and minimally validate the synthetic labeled matching fixture."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    if not isinstance(value.get("exact"), dict):
        raise ValueError(f"{path} is missing an 'exact' s07 input payload.")
    if not isinstance(value.get("truth_pairs"), list):
        raise ValueError(f"{path} is missing the 'truth_pairs' list.")
    return value


_S07_MODULE: Any = None


def _load_s07_module() -> Any:
    """Import the import-safe s07 stage module once and cache it.

    s07 defines its matching functions at module level and keeps file
    orchestration inside ``main()`` under a ``__name__ == "__main__"`` guard,
    so importing it has no workspace, file, print, or environment side effects.
    """
    global _S07_MODULE
    if _S07_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "document_compare.phase1.stages._s07_modified_matching",
            S07_PATH,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load s07 stage module from {S07_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _S07_MODULE = module
    return _S07_MODULE


def run_traced_build(
    exact: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    *,
    collect: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the real ``build_result`` with an optional diagnostics collector.

    The side-effect-free s07 module is imported in-process, so pipeline files
    are never seeded and the stage is never executed twice. ``overrides`` patch
    the module globals for this one call only and are restored in a ``finally``
    block, so no default stays mutated.
    """
    overrides = dict(overrides or {})
    unknown = set(overrides) - set(_ALLOWED_OVERRIDES)
    if unknown:
        raise ValueError(f"Unsupported diagnostics override(s): {sorted(unknown)}")

    build_result = _load_s07_module().build_result
    module_globals = build_result.__globals__
    previous = {key: module_globals[key] for key in overrides}

    trace: list[dict[str, Any]] = []
    try:
        for key, value in overrides.items():
            module_globals[key] = value
        result = build_result(
            exact,
            diagnostics=(trace if collect else None),
        )
    finally:
        for key, value in previous.items():
            module_globals[key] = value
    return result, trace


def _stage_metric(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator <= 0:
        return {
            "numerator": 0,
            "denominator": 0,
            "recall": None,
            "status": "not_applicable",
        }
    return {
        "numerator": numerator,
        "denominator": denominator,
        "recall": round(numerator / denominator, 6),
        "status": "evaluated",
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def score_pair_trace(
    traces: list[dict[str, Any]],
    truth_pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score an s07 diagnostic trace against independently authored truth.

    Each truth pair is placed at the furthest stage it reached:

    - ``lost_retrieval`` — never retrieved as a candidate
    - ``lost_shortlist`` — retrieved, dropped by the expensive-work cap
    - ``lost_scoring`` — shortlisted, scored below the review floor
    - ``lost_selection`` — passed the floor, not chosen as the pairing
    - ``correct_pairing`` — selected as the pair

    A stage with no truth is ``not_applicable``; a missing truth pair is never
    counted as a success.
    """
    retrieved: set[tuple[Any, Any]] = set()
    shortlisted: set[tuple[Any, Any]] = set()
    scored: dict[tuple[Any, Any], dict[str, Any]] = {}
    selected: set[tuple[Any, Any]] = set()
    classified: dict[tuple[Any, Any], dict[str, Any]] = {}

    for record in traces:
        old_id = record.get("old_element_id")
        new_id = record.get("new_element_id")
        if old_id is None or new_id is None:
            continue
        pair = (old_id, new_id)
        stage = record.get("stage")
        if stage == "retrieved":
            retrieved.add(pair)
        elif stage == "shortlisted":
            shortlisted.add(pair)
        elif stage == "scored":
            scored[pair] = record
        elif stage == "selected":
            selected.add(pair)
        elif stage == "classified":
            classified[pair] = record

    total = len(truth_pairs)
    counts = {"retrieval": 0, "shortlist": 0, "scoring": 0, "selection": 0}
    outcome_counts: Counter[str] = Counter()
    truth_set: set[tuple[Any, Any]] = set()
    outcomes: list[dict[str, Any]] = []
    classification_numerator = 0
    classification_denominator = 0

    for spec in truth_pairs:
        pair = (spec.get("old_element_id"), spec.get("new_element_id"))
        truth_set.add(pair)

        reached_retrieval = pair in retrieved
        reached_shortlist = pair in shortlisted
        scored_record = scored.get(pair)
        reached_scoring = bool(scored_record and scored_record.get("passed_review_floor"))
        reached_selection = pair in selected

        if reached_selection:
            outcome = "correct_pairing"
        elif reached_scoring:
            outcome = "lost_selection"
        elif scored_record is not None or reached_shortlist:
            outcome = "lost_scoring"
        elif reached_retrieval:
            outcome = "lost_shortlist"
        else:
            outcome = "lost_retrieval"

        if reached_retrieval:
            counts["retrieval"] += 1
        if reached_shortlist:
            counts["shortlist"] += 1
        if reached_scoring:
            counts["scoring"] += 1
        if reached_selection:
            counts["selection"] += 1

        expected = spec.get("expected_disposition")
        classified_record = classified.get(pair)
        actual = None if classified_record is None else classified_record.get("disposition")
        if expected is not None:
            classification_denominator += 1
            if actual == expected:
                classification_numerator += 1

        outcomes.append(
            {
                "old_element_id": pair[0],
                "new_element_id": pair[1],
                "scenario": spec.get("scenario"),
                "outcome": outcome,
                "expected_disposition": expected,
                "actual_disposition": actual,
            }
        )
        outcome_counts[outcome] += 1

    selected_count = len(selected)
    matched_count = len(selected & truth_set)
    precision = matched_count / selected_count if selected_count else None
    recall = matched_count / total if total else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)

    outcome_order = (
        "correct_pairing",
        "lost_retrieval",
        "lost_shortlist",
        "lost_scoring",
        "lost_selection",
    )

    return {
        "truth_count": total,
        "selected_count": selected_count,
        "matched_count": matched_count,
        "stages": {
            "retrieval": _stage_metric(counts["retrieval"], total),
            "shortlist": _stage_metric(counts["shortlist"], total),
            "scoring": _stage_metric(counts["scoring"], total),
            "selection": _stage_metric(counts["selection"], total),
            "classification": _stage_metric(
                classification_numerator,
                classification_denominator,
            ),
        },
        "pair_precision_recall": {
            "selected_count": selected_count,
            "truth_count": total,
            "matched_count": matched_count,
            "precision": _rounded(precision),
            "recall": _rounded(recall),
            "f1": _rounded(f1),
            "status": "evaluated" if total else "not_applicable",
        },
        "outcome_counts": {key: outcome_counts.get(key, 0) for key in outcome_order},
        "pair_outcomes": outcomes,
    }


def default_experiment_matrix() -> list[dict[str, Any]]:
    """One baseline plus one-factor-at-a-time threshold sweeps."""
    matrix: list[dict[str, Any]] = [
        {"id": "baseline", "factor": "baseline", "value": None, "overrides": {}},
    ]
    for value in (0.70, 0.72, 0.74):
        matrix.append(
            {
                "id": f"review_{value:.2f}",
                "factor": "review",
                "value": value,
                "overrides": {"MIN_REVIEW_SCORE": value},
            }
        )
    for value in (0.84, 0.86, 0.88):
        matrix.append(
            {
                "id": f"automatic_{value:.2f}",
                "factor": "automatic",
                "value": value,
                "overrides": {"MIN_AUTOMATIC_SCORE": value},
            }
        )
    for value in (0.08, 0.10, 0.12):
        matrix.append(
            {
                "id": f"margin_{value:.2f}",
                "factor": "margin",
                "value": value,
                "overrides": {"MIN_AUTOMATIC_MARGIN": value},
            }
        )
    return matrix


def _experiment_metrics(fixture_path: Path, overrides: dict[str, Any]) -> dict[str, Any]:
    fixture = load_fixture(fixture_path)
    result, trace = run_traced_build(fixture["exact"], overrides=overrides, collect=True)
    scored = score_pair_trace(trace, fixture["truth_pairs"])
    statistics = result["statistics"]
    return {
        "overrides": overrides,
        "review_counts": {
            "accepted": statistics["accepted_modified_alignments"],
            "review": statistics["review_modified_alignments"],
            "duplicate_exact_review": statistics["duplicate_exact_review_pairs"],
        },
        "stages": scored["stages"],
        "pair_precision_recall": scored["pair_precision_recall"],
        "selected_count": scored["selected_count"],
        "matched_count": scored["matched_count"],
        "outcome_counts": scored["outcome_counts"],
        "pair_outcomes": scored["pair_outcomes"],
    }


def _worker_main(payload_path: Path) -> None:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    metrics = _experiment_metrics(
        Path(payload["fixture"]),
        dict(payload.get("overrides") or {}),
    )
    sys.stdout.write(json.dumps(metrics, ensure_ascii=False))


def _run_experiment_subprocess(fixture_path: Path, experiment: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="crr-diag-payload-") as directory:
        payload_path = Path(directory) / "payload.json"
        payload_path.write_text(
            json.dumps(
                {"fixture": str(fixture_path), "overrides": experiment["overrides"]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(REPO_ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "document_compare.phase1.evaluation.matching_diagnostics",
                "--worker",
                str(payload_path),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"[MOCK/SYNTHETIC] diagnostics worker failed for "
                f"{experiment['id']!r}:\n{completed.stderr}"
            )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise SystemExit(
                f"[MOCK/SYNTHETIC] diagnostics worker for {experiment['id']!r} "
                "produced no output."
            )
        return json.loads(lines[-1])


def _fingerprint(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_counts": metrics["review_counts"],
        "pair_precision_recall": metrics["pair_precision_recall"],
        "selected_count": metrics["selected_count"],
        "matched_count": metrics["matched_count"],
        "outcome_counts": metrics["outcome_counts"],
        "stage_numerators": {
            stage: block["numerator"] for stage, block in metrics["stages"].items()
        },
    }


def _summarize(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = next(
        (item for item in experiments if item["factor"] == "baseline"),
        experiments[0],
    )
    baseline_metrics = baseline["metrics"]
    baseline_fingerprint = _fingerprint(baseline_metrics)

    sensitivity = []
    for item in experiments:
        fingerprint = _fingerprint(item["metrics"])
        changed = [
            key
            for key in fingerprint
            if fingerprint[key] != baseline_fingerprint[key]
        ]
        sensitivity.append({"id": item["id"], "factor": item["factor"], "changed": changed})

    return {
        "baseline_id": baseline["id"],
        "stage_coverage": {
            stage: block for stage, block in baseline_metrics["stages"].items()
        },
        "miss_stages": {
            key: count
            for key, count in baseline_metrics["outcome_counts"].items()
            if key != "correct_pairing" and count
        },
        "review_counts": baseline_metrics["review_counts"],
        "pair_precision_recall": baseline_metrics["pair_precision_recall"],
        "sensitivity": sensitivity,
    }


def _build_report(
    fixture_path: Path,
    experiments: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "report_kind": "matching_diagnostics",
        "synthetic": True,
        "not_a_pdf_comparison": True,
        "generated_by": "python -m document_compare.phase1.evaluation.matching_diagnostics",
        "note": (
            "Retrieval/ranking/selection metrics on one tiny synthetic fixture. "
            "This is not an accuracy claim about arbitrary PDFs and no "
            "production default was changed."
        ),
        "fixture": str(fixture_path),
        "constants": DOCUMENTED_CONSTANTS,
        "experiments": [
            {**experiment, "metrics": result}
            for experiment, result in zip(experiments, results)
        ],
        "summary": _summarize(
            [
                {**experiment, "metrics": result}
                for experiment, result in zip(experiments, results)
            ]
        ),
    }


def _format_recall(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _print_summary(report: dict[str, Any], report_path: Path) -> None:
    summary = report["summary"]
    print("[MOCK/SYNTHETIC] Matching diagnostics summary (fixture:", report["fixture"], ")")
    print("  Stage coverage (baseline):")
    for stage, block in summary["stage_coverage"].items():
        print(
            f"    {stage:14s} {block['numerator']}/{block['denominator']} "
            f"recall={_format_recall(block['recall'])} status={block['status']}"
        )
    pair = summary["pair_precision_recall"]
    print(
        f"  Pair precision={_format_recall(pair['precision'])} "
        f"recall={_format_recall(pair['recall'])} f1={_format_recall(pair['f1'])}"
    )
    print("  Baseline review counts:", json.dumps(summary["review_counts"], sort_keys=True))
    print("  Miss stages:", json.dumps(summary["miss_stages"], sort_keys=True))
    print("  Threshold sensitivity (changed metric groups vs baseline):")
    for entry in summary["sensitivity"]:
        changed = ", ".join(entry["changed"]) if entry["changed"] else "no change"
        print(f"    {entry['id']:16s} ({entry['factor']}): {changed}")
    print("  Report:", report_path)


def run_harness(
    root: Path,
    fixture_path: Path = DEFAULT_FIXTURE,
    experiments: list[dict[str, Any]] | None = None,
) -> Path:
    """Run the serial isolated experiments and write the JSON report.

    Every experiment runs in a fresh subprocess with in-memory-only overrides.
    Production defaults are never written. An existing report is never
    overwritten.
    """
    root = Path(root).expanduser().resolve()
    report_path = root / REPORT_NAME
    if report_path.exists():
        raise SystemExit(
            f"[MOCK/SYNTHETIC] Refusing to overwrite existing diagnostics report: "
            f"{report_path}"
        )

    fixture_path = Path(fixture_path)
    experiments = (
        list(experiments) if experiments is not None else default_experiment_matrix()
    )

    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for experiment in experiments:
        results.append(_run_experiment_subprocess(fixture_path, experiment))

    report = _build_report(fixture_path, experiments, results)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _print_summary(report, report_path)
    return report_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m document_compare.phase1.evaluation.matching_diagnostics",
        description=__doc__,
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="New report root; the report is written under it and must not exist.",
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker is not None:
        _worker_main(args.worker)
        return
    if args.root is None:
        parser.error("--root is required")
    run_harness(args.root, args.fixture)


if __name__ == "__main__":
    main()
