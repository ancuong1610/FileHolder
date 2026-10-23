"""Command-line entry point for the Gate B local semantic shadow experiment.

Usage::

    python -m document_compare.phase1.evaluation.semantic_shadow_cli \
        --pairs path/to/pairs.json \
        --out path/to/new-output-directory \
        --model-dir /explicit/local/model/snapshot \
        [--max-pairs 200]

The command never starts a service, never contacts a remote model and never
writes into the input path.  ``--out`` must be a fresh directory; an existing
path is refused before the model loads.  The output directory receives
``shadow_results.json`` and a concise ``shadow_summary.md``.  Every input row is
represented, including skipped, oversized, over-window, failed and
budget-exhausted rows.

The optional ML backend is imported only inside the default factory.  Tests and
the lead can pass ``backend_factory=...`` to supply the documented local/fake
backend without any optional dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from document_compare.phase1.evaluation import semantic_shadow as shadow

BackendFactory = Callable[[Path], Any]


def _default_backend_factory(model_dir: Path) -> Any:
    # Lazy optional import; module import alone stays dependency-free.
    from document_compare.phase1.evaluation.semantic_shadow_model import (
        LocalTransformerNLIBackend,
    )

    return LocalTransformerNLIBackend(model_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded, local-only semantic shadow experiment (advisory)."
    )
    parser.add_argument("--pairs", required=True, type=Path, help="input pairs JSON")
    parser.add_argument(
        "--out", required=True, type=Path, help="fresh output directory (must not exist)"
    )
    parser.add_argument(
        "--model-dir",
        required=True,
        type=Path,
        help="explicit local Transformers model directory",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=shadow.MAX_PAIRS,
        help=f"pair budget (hard-capped at {shadow.MAX_PAIRS}, cannot be raised)",
    )
    return parser


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    budget = payload["budget"]
    model = payload["model"]
    status_counts = Counter(row["status"] for row in payload["results"])
    reason_counts = Counter(
        row["skip_reason"]
        for row in payload["results"]
        if row.get("skip_reason")
    )
    error_counts = Counter(
        row["error_code"] for row in payload["results"] if row.get("error_code")
    )
    lines = [
        "# Semantic shadow run (advisory only)",
        "",
        "This is an optional Gate B research experiment. Model output has no",
        "canonical authority, does not change Stage 13/14 dispositions, and is not",
        "calibrated semantic confidence or legal ground truth.",
        "",
        f"- mode: `{payload['mode']}`",
        f"- authority: `{str(payload['authority']).lower()}`",
        f"- model: `{model.get('model_id')}`",
        f"- model identity source: `{model.get('model_id_source')}`",
        f"- revision: `{model.get('revision')}`",
        f"- revision verification: `{model.get('revision_verification')}`",
        f"- artifact sha256: `{model.get('artifact_sha256')}`",
        f"- artifact files/bytes: {model.get('artifact_file_count')} / {model.get('artifact_bytes')}",
        f"- labels: `{', '.join(model.get('labels') or [])}`",
        f"- model token limit: {model.get('max_tokens')}",
        f"- score semantics: `{payload['score_semantics']}`",
        "",
        "## Budget and coverage",
        "",
        f"- input pairs: {budget['input_pairs']}",
        f"- pair budget: {budget['max_pairs']} (requested {budget['max_pairs_requested']})",
        f"- directional inference limit: {budget['max_directional_inferences']}",
        f"- pairs attempted: {budget['pairs_attempted']}",
        f"- pairs assessed: {budget['pairs_assessed']}",
        f"- pairs skipped: {budget['pairs_skipped']}",
        f"- pairs errored: {budget['pairs_errored']}",
        f"- directional inferences: {budget['directional_inferences']}",
        f"- budget exhausted: `{str(budget['budget_exhausted']).lower()}`",
        "",
        "## Row status",
        "",
    ]
    for status in sorted(status_counts):
        lines.append(f"- {status}: {status_counts[status]}")
    if reason_counts:
        lines.extend(["", "## Skip reasons", ""])
        for reason in sorted(reason_counts):
            lines.append(f"- {reason}: {reason_counts[reason]}")
    if error_counts:
        lines.extend(["", "## Error codes", ""])
        for code in sorted(error_counts):
            lines.append(f"- {code}: {error_counts[code]}")
    lines.extend(
        [
            "",
            "Directional softmax scores are model outputs, not accuracy percentages.",
            "Protected source guards and explicit abstention remain authoritative for safety.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _safe_stderr(code: str) -> None:
    print(code, file=sys.stderr)


def main(argv: list[str] | None = None, *, backend_factory: BackendFactory | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_pairs <= 0:
        _safe_stderr("input_error:invalid_max_pairs")
        return 2

    out = args.out
    if out.exists():
        _safe_stderr("output_error:refusing_existing_output_path")
        return 4

    try:
        raw_bytes = args.pairs.read_bytes()
    except OSError:
        _safe_stderr("input_error:unavailable")
        return 2
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _safe_stderr("input_error:invalid_json")
        return 2

    try:
        shadow.validate_pairs_document(document)
    except shadow.ShadowInputError as exc:
        _safe_stderr(f"input_error:{exc.code}")
        return 2

    model_dir = args.model_dir
    if not model_dir.is_dir():
        _safe_stderr("model_error:model_dir_unavailable")
        return 3

    factory = backend_factory or _default_backend_factory
    try:
        backend = factory(model_dir)
    except Exception as exc:  # no raw model path, document text or stack trace
        _safe_stderr(f"model_error:{type(exc).__name__}")
        return 3

    try:
        payload = shadow.run_shadow(document, backend, max_pairs=args.max_pairs)
    except shadow.ShadowInputError as exc:
        _safe_stderr(f"input_error:{exc.code}")
        return 2
    except Exception as exc:
        _safe_stderr(f"run_error:{type(exc).__name__}")
        return 1

    payload["input"] = {
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "pair_count": len(document.get("pairs", [])),
    }

    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _safe_stderr("output_error:refusing_existing_output_path")
        return 4
    except OSError:
        _safe_stderr("output_error:unavailable")
        return 1

    try:
        (out / "shadow_results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_summary(out / "shadow_summary.md", payload)
    except OSError:
        # Remove only this invocation's own filenames.  The freshly claimed
        # directory may already contain files from another process; never
        # rmtree it or delete foreign entries.
        for name in ("shadow_results.json", "shadow_summary.md"):
            try:
                (out / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        _safe_stderr("output_error:write_failed")
        return 1

    print(
        f"wrote {out / 'shadow_results.json'} "
        f"({payload['budget']['pairs_assessed']} assessed, "
        f"{payload['budget']['pairs_skipped']} skipped, "
        f"{payload['budget']['pairs_errored']} errored)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
