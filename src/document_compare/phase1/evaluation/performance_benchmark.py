"""Controlled synthetic performance benchmark over a real saved Step-3 model.

``benchmark-performance`` answers one owner question: *how well is the engine
actually doing, and where does it fail?*  It reads an existing pipeline output
directory (never a job upload, never a fresh Document Intelligence call, never
the original PDFs) and takes the saved Step-3 internal model
``old_document.json`` as the immutable OLD side.  For each category it copies
that internal model, injects a controlled, fully declared edit, writes the copy
as the NEW side, records the exact truth *before* any prediction exists, then
runs stages 04-15 with the offline mock evaluator and scores the canonical
result against the declared truth.

Metric integrity rules enforced here:

* truth is declared by the benchmark, never read back from predictions; the
  truth file is written before the pipeline runs,
* the OLD-vs-OLD identity baseline has complete change truth (nothing may
  change), so any identity change is a false alarm,
* a prediction is a true positive only when it matches the exact declared
  ``(kind, old_id, new_id)`` (or ``(classification, old_cell_id, new_cell_id)``)
  AND is accepted AND the canonical raw text payload agrees on every side the
  truth declares (the absent ``new`` side of a deletion and the absent ``old``
  side of an insertion have no expected text); a wrong partner, wrong type,
  wrong payload or unmatched prediction is a false positive, and the
  corresponding declared truth is a false negative,
* duplicate predictions are never collapsed: a second record for the same truth
  is a false positive,
* a detection that matched but is still routed to review is reported separately
  ("đúng nhưng cần review") and does not count as a true positive,
* a category with no injected truth is ``not_applicable`` (null metrics), never
  a vacuous perfect score,
* spans/bounding regions kept from the source model are origin references only:
  they do not evidence the modified text, and every case is explicitly labelled
  synthetic *after extraction*,
* move retention is not movement accuracy and is never scored as a detection.

The module is import-safe: importing it only defines helpers and never reads a
workspace, prints, or mutates the environment.  File orchestration lives in
:func:`benchmark_performance_command`.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import random
import re
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from document_compare.phase1.pipeline import cli
from document_compare.phase1.contracts.validation import require

BENCHMARK_VERSION = "1.1.0"
DEFAULT_SEED = 42
SMALL_SAMPLE_FLOOR = 10
MOVE_NOTE = "move retention is not movement accuracy and is not scored"

# Exact scope of what the accepted/asserted metric measures.  The scorer compares
# the canonical raw text payload on every side the truth declares, in addition to
# the (kind, id) pair and the accepted state.  It does not measure how faithfully
# the PDF rendering or the source PDF was reproduced, and a scoring change must
# never be turned into a before/after improvement claim.
EVALUATION_SCOPE = "canonical-pair-kind-raw-text"
EVALUATION_SCOPE_NOTE = (
    "Phạm vi chấm chính xác: cặp (kind/loại, old–new id) + trạng thái accepted + "
    "văn bản thô canonical trước/sau (canonical-pair-kind-raw-text). "
    "KHÔNG đo độ trung thực của bản dựng PDF hay PDF nguồn "
    "(PDF rendering / source PDF fidelity NOT measured). "
    "Thay đổi cách chấm không tạo tuyên bố cải thiện trước/sau."
)

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{5,}")
LONG_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{8,}")
NUMBER_RE = re.compile(r"\d+")

DEFAULT_COUNTS: dict[str, int] = {
    "word": 20,
    "numeric": 10,
    "delete": 10,
    "insert": 10,
    "noise": 10,
    "table_cell": 5,
}

ELEMENT_CATEGORIES = ("word", "numeric", "delete", "insert")
CATEGORY_ORDER = ("identity", "word", "numeric", "delete", "insert", "noise", "table_cell")
# Categories whose injected edits are real semantic changes (noise is a
# suppression condition and is reported separately, never folded into totals).
SEMANTIC_CATEGORIES = ("word", "numeric", "delete", "insert", "table_cell")

CATEGORY_KIND = {
    "word": "modify",
    "numeric": "modify",
    "delete": "delete",
    "insert": "insert",
    "noise": "noise",
    "table_cell": "table_cell",
}
KIND_TO_ENGINE = {"modify": "MODIFIED", "delete": "DELETED", "insert": "ADDED"}

CATEGORY_LABEL_VI = {
    "identity": "Đối chứng giống hệt (old vs old)",
    "word": "Sửa chữ (wording)",
    "numeric": "Sửa số (numeric)",
    "delete": "Xoá phần tử",
    "insert": "Thêm phần tử",
    "noise": "Nhiễu gạch nối xuống dòng",
    "table_cell": "Sửa ô bảng",
}

BEFORE_AFTER_NOTE = "chưa đo trước/sau — chưa kết luận cải thiện"


def _stage_range(start: int, end: int) -> list[str]:
    """Every stage number from ``start`` to ``end`` (inclusive)."""
    return [num for num, _ in cli.STAGES if start <= int(num) <= end]


BENCH_STAGES = _stage_range(4, 15)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def _mock_env():
    """Force the offline mock evaluator and generic domain for the wrapped block."""
    prev = {k: os.environ.get(k) for k in ("CRR_RUN_LLM", "CRR_DOMAIN_MODE")}
    os.environ["CRR_RUN_LLM"] = "mock"
    os.environ["CRR_DOMAIN_MODE"] = "generic"
    try:
        yield
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Base model loading
# ---------------------------------------------------------------------------


def _derive_old_model(artifacts: Path, staging_root: Path) -> Path:
    """Fallback: derive the Step-3 model from saved raw DI JSON (stages 02-03)."""
    out = staging_root / "output"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("old_di_raw.json", "new_di_raw.json"):
        shutil.copyfile(artifacts / name, out / name)
    with _mock_env():
        cli.run_stages(_stage_range(2, 3), staging_root)
    return out / "old_document.json"


def load_base_model(artifacts: Path, staging_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and validate the immutable OLD Step-3 model from saved artifacts."""
    artifacts = Path(artifacts).expanduser().resolve()
    require(artifacts.is_dir(), f"artifacts directory does not exist: {artifacts}")
    path = artifacts / "old_document.json"
    derived = False
    if not path.is_file():
        raw_old = artifacts / "old_di_raw.json"
        raw_new = artifacts / "new_di_raw.json"
        require(
            raw_old.is_file() and raw_new.is_file(),
            f"neither {path.name} nor both raw DI files exist in {artifacts}; "
            "the benchmark needs a saved Step-3 old_document.json",
        )
        path = _derive_old_model(artifacts, staging_root)
        derived = True

    model = _load_json(path)
    document = model.get("document")
    require(isinstance(document, dict), f"{path} is missing a document object")
    elements = document.get("elements")
    require(
        isinstance(elements, list) and elements,
        f"{path} document has no elements to benchmark",
    )
    ids = [element.get("element_id") for element in elements]
    require(
        all(isinstance(i, str) and i for i in ids),
        f"{path} has an element without a non-empty element_id",
    )
    require(len(set(ids)) == len(ids), f"{path} has duplicate element_id values")

    manifest = {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "document_id": document.get("document_id"),
        "element_count": len(elements),
        "derived_from_raw_di": derived,
    }
    return model, manifest


# ---------------------------------------------------------------------------
# Eligibility and mutation primitives
# ---------------------------------------------------------------------------


def _text(element: dict[str, Any]) -> str:
    return str(element.get("text") or "")


def _is_eligible_paragraph(element: dict[str, Any]) -> bool:
    return (
        element.get("comparison_eligible") is True
        and element.get("element_type") == "paragraph"
        and bool(element.get("element_id"))
    )


def _controlled_word(text: str, rng: random.Random) -> dict[str, Any] | None:
    """Swap two adjacent characters in a mid-word; same length, clearly changed."""
    matches = list(WORD_RE.finditer(text))
    editable = [m for m in matches if m.start() > len(text) * 0.15] or matches
    if not editable:
        return None
    target = rng.choice(editable)
    word = target.group(0)
    index = len(word) // 2
    replacement = word[:index] + word[index + 1] + word[index] + word[index + 2 :]
    if replacement == word:
        return None
    return {
        "new_text": text[: target.start()] + replacement + text[target.end() :],
        "before": word,
        "after": replacement,
    }


def _controlled_numeric(text: str, rng: random.Random) -> dict[str, Any] | None:
    """Change one digit inside a number run to a different digit."""
    matches = [m for m in NUMBER_RE.finditer(text) if m.start() > 0] or list(NUMBER_RE.finditer(text))
    if not matches:
        return None
    target = rng.choice(matches)
    digits = target.group(0)
    index = rng.randrange(len(digits))
    old_digit = digits[index]
    new_digit = str((int(old_digit) + 1) % 10)
    if new_digit == old_digit:
        new_digit = str((int(old_digit) + 2) % 10)
    replacement = digits[:index] + new_digit + digits[index + 1 :]
    if replacement == digits:
        return None
    return {
        "new_text": text[: target.start()] + replacement + text[target.end() :],
        "before": digits,
        "after": replacement,
    }


def _wrap_noise(text: str, rng: random.Random) -> dict[str, Any] | None:
    """Split a long word with a real end-of-line ``-\\n`` wrap (source-proven noise)."""
    matches = [m for m in LONG_WORD_RE.finditer(text) if m.start() > 0]
    if not matches:
        return None
    target = rng.choice(matches)
    word = target.group(0)
    cut = len(word) // 2
    wrapped = f"{word[:cut]}-\n{word[cut:]}"
    return {
        "new_text": text[: target.start()] + wrapped + text[target.end() :],
        "before": word,
        "after": wrapped,
    }


def _select(rng: random.Random, pool: list[dict[str, Any]], count: int) -> tuple[list[dict[str, Any]], int]:
    """Deterministically pick up to ``count`` distinct candidates from a pool."""
    ordered = sorted(pool, key=lambda element: element["element_id"])
    rng.shuffle(ordered)
    return ordered[:count], len(ordered)


def _renumber(elements: list[dict[str, Any]]) -> None:
    for reading_order, element in enumerate(elements):
        element["reading_order"] = reading_order


def _case_seed(seed: int, category: str) -> int:
    digest = hashlib.sha256(f"{seed}:{category}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# ---------------------------------------------------------------------------
# Case builders (one category per case)
# ---------------------------------------------------------------------------


def _truth_entry(
    *,
    case_id: str,
    category: str,
    kind: str,
    old_id: str | None,
    new_id: str | None,
    before_text: str,
    after_text: str,
    table_element_id: str | None = None,
    old_cell_id: str | None = None,
    new_cell_id: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "kind": kind,
        "old_id": old_id,
        "new_id": new_id,
        "table_element_id": table_element_id,
        "old_cell_id": old_cell_id,
        "new_cell_id": new_cell_id,
        "before_text": before_text,
        "after_text": after_text,
        "synthetic": True,
        "benchmark_only_provenance": True,
        "spans_note": (
            "spans/bounding regions inherited from the source model are origin "
            "references only; they do not evidence the modified text"
        ),
    }


def build_case(
    base_model: dict[str, Any],
    category: str,
    *,
    seed: int,
    count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return ``(new_model, truth, support)`` for one category."""
    rng = random.Random(_case_seed(seed, category))
    new_model = copy.deepcopy(base_model)
    elements = new_model["document"]["elements"]
    by_id = {element["element_id"]: element for element in elements}
    truth: list[dict[str, Any]] = []
    support: dict[str, Any] = {
        "category": category,
        "requested": count,
        "eligible": 0,
        "attempted": 0,
        "actual": 0,
        "skipped": [],
    }

    def skip(element_id: str, reason: str) -> None:
        support["skipped"].append({"element_id": element_id, "reason": reason})

    if category in ("word", "numeric"):
        predicate = (
            (lambda element: WORD_RE.search(_text(element)) is not None)
            if category == "word"
            else (lambda element: NUMBER_RE.search(_text(element)) is not None)
        )
        pool = [
            element
            for element in elements
            if _is_eligible_paragraph(element) and len(_text(element)) >= 100 and predicate(element)
        ]
        chosen, support["eligible"] = _select(rng, pool, count)
        for index, element in enumerate(chosen):
            support["attempted"] += 1
            edit = (
                _controlled_word(_text(element), rng)
                if category == "word"
                else _controlled_numeric(_text(element), rng)
            )
            if edit is None or edit["new_text"] == _text(element):
                skip(element["element_id"], "no eligible controllable token")
                continue
            before = _text(element)
            element["text"] = edit["new_text"]
            truth.append(
                _truth_entry(
                    case_id=f"{category}-{index + 1:04d}",
                    category=category,
                    kind="modify",
                    old_id=element["element_id"],
                    new_id=element["element_id"],
                    before_text=before,
                    after_text=edit["new_text"],
                )
            )

    elif category == "delete":
        pool = [
            element
            for element in elements
            if _is_eligible_paragraph(element) and len(_text(element)) >= 40
        ]
        chosen, support["eligible"] = _select(rng, pool, count)
        for index, element in enumerate(chosen):
            support["attempted"] += 1
            before = _text(element)
            elements.remove(element)
            by_id.pop(element["element_id"], None)
            truth.append(
                _truth_entry(
                    case_id=f"delete-{index + 1:04d}",
                    category=category,
                    kind="delete",
                    old_id=element["element_id"],
                    new_id=None,
                    before_text=before,
                    after_text="",
                )
            )

    elif category == "insert":
        pool = [
            element
            for element in elements
            if _is_eligible_paragraph(element) and len(_text(element)) >= 100
        ]
        chosen, support["eligible"] = _select(rng, pool, count)
        for index, source in enumerate(chosen):
            support["attempted"] += 1
            clone = copy.deepcopy(source)
            edit = _controlled_word(_text(clone), rng)
            if edit is None or edit["new_text"] == _text(clone):
                skip(source["element_id"], "no eligible controllable token for clone")
                continue
            clone["text"] = edit["new_text"]
            new_id = f"BENCH-INS-{index + 1:04d}"
            while new_id in by_id:
                new_id = f"{new_id}X"
            clone["element_id"] = new_id
            position = min(len(elements), max(0, rng.randrange(len(elements) + 1)))
            elements.insert(position, clone)
            by_id[new_id] = clone
            truth.append(
                _truth_entry(
                    case_id=f"insert-{index + 1:04d}",
                    category=category,
                    kind="insert",
                    old_id=None,
                    new_id=new_id,
                    before_text="",
                    after_text=clone["text"],
                )
            )

    elif category == "noise":
        pool = [
            element
            for element in elements
            if _is_eligible_paragraph(element)
            and len(_text(element)) >= 100
            and LONG_WORD_RE.search(_text(element)) is not None
        ]
        chosen, support["eligible"] = _select(rng, pool, count)
        for index, element in enumerate(chosen):
            support["attempted"] += 1
            edit = _wrap_noise(_text(element), rng)
            if edit is None or edit["new_text"] == _text(element):
                skip(element["element_id"], "no long word to wrap")
                continue
            before = _text(element)
            element["text"] = edit["new_text"]
            truth.append(
                _truth_entry(
                    case_id=f"noise-{index + 1:04d}",
                    category=category,
                    kind="noise",
                    old_id=element["element_id"],
                    new_id=None,
                    before_text=before,
                    after_text=edit["new_text"],
                )
            )

    elif category == "table_cell":
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for element in elements:
            if element.get("element_type") != "table":
                continue
            for cell in (element.get("table") or {}).get("cells") or []:
                if (
                    isinstance(cell.get("row_index"), int)
                    and cell["row_index"] >= 1
                    and isinstance(cell.get("cell_id"), str)
                    and len(str(cell.get("text") or "")) >= 20
                    and WORD_RE.search(str(cell.get("text") or ""))
                ):
                    candidates.append((element, cell))
        candidates.sort(key=lambda pair: (pair[0]["element_id"], pair[1]["cell_id"]))
        rng.shuffle(candidates)
        support["eligible"] = len(candidates)
        for index, (element, cell) in enumerate(candidates[:count]):
            support["attempted"] += 1
            before = str(cell.get("text") or "")
            edit = _controlled_word(before, rng)
            if edit is None or edit["new_text"] == before:
                skip(cell["cell_id"], "no eligible controllable token in cell")
                continue
            cell["text"] = edit["new_text"]
            truth.append(
                _truth_entry(
                    case_id=f"table_cell-{index + 1:04d}",
                    category=category,
                    kind="table_cell",
                    old_id=None,
                    new_id=None,
                    before_text=before,
                    after_text=edit["new_text"],
                    table_element_id=element["element_id"],
                    old_cell_id=cell["cell_id"],
                    new_cell_id=cell["cell_id"],
                )
            )
    else:
        raise ValueError(f"unknown benchmark category {category!r}")

    support["actual"] = len(truth)
    _renumber(elements)
    return new_model, truth, support


# ---------------------------------------------------------------------------
# Truth validation
# ---------------------------------------------------------------------------


def _truth_tuple(entry: dict[str, Any]) -> tuple:
    if entry["kind"] == "table_cell":
        return ("table_cell", entry["old_cell_id"], entry["new_cell_id"])
    return (entry["kind"], entry["old_id"], entry["new_id"])


def _payload_matches(
    entry: dict[str, Any],
    prediction: dict[str, Any],
    *,
    cell: bool = False,
) -> bool:
    """Exact raw-text equality on every side the truth declares.

    ``entry`` is a declared truth; ``prediction`` carries the canonical raw text
    observed for its present sides (``old_text``/``new_text``).  A side the truth
    does not declare — the absent ``new`` side of a deletion, the absent ``old``
    side of an insertion — has no expected text, so the prediction's unused text
    on that side is ignored rather than treated as a mismatch.
    """
    if cell:
        old_present = entry.get("old_cell_id") is not None
        new_present = entry.get("new_cell_id") is not None
    else:
        old_present = entry.get("old_id") is not None
        new_present = entry.get("new_id") is not None
    if old_present and prediction.get("old_text") != entry.get("before_text"):
        return False
    if new_present and prediction.get("new_text") != entry.get("after_text"):
        return False
    return True


def _mismatch_summary(never: list[dict[str, Any]]) -> dict[str, Any]:
    """Split never-matched truths into wrong-pair, wrong-payload and absent.

    Kept separate so a right-pair/wrong-text detection is never reported as a
    wrong-pair miss (and vice versa); both are the accepted-stream FP+FN case.
    """
    wrong_pair = [item for item in never if item.get("mismatch") == "pair"]
    wrong_payload = [item for item in never if item.get("mismatch") == "payload"]
    return {
        "wrong_pair_mismatches": len(wrong_pair),
        "wrong_payload_mismatches": len(wrong_payload),
        "wrong_pair_examples": wrong_pair[:8],
        "wrong_payload_examples": wrong_payload[:8],
    }


def validate_truth(
    truth: list[dict[str, Any]],
    *,
    base_ids: set[str],
    new_ids: set[str],
    base_cell_ids: set[str],
) -> None:
    """Reject duplicate tuples, missing required ids and unknown input identities."""
    seen_cases: set[str] = set()
    seen_tuples: set[tuple] = set()
    for entry in truth:
        case_id = entry.get("case_id")
        require(isinstance(case_id, str) and case_id, "truth entry is missing case_id")
        require(case_id not in seen_cases, f"duplicate truth case_id: {case_id}")
        seen_cases.add(case_id)
        key = _truth_tuple(entry)
        require(key not in seen_tuples, f"duplicate truth tuple: {key}")
        seen_tuples.add(key)
        kind = entry["kind"]
        if kind in ("modify", "delete", "noise"):
            require(
                entry.get("old_id") in base_ids,
                f"{case_id}: old_id {entry.get('old_id')!r} is not in the base model",
            )
        if kind == "modify":
            require(
                entry.get("new_id") in new_ids,
                f"{case_id}: new_id {entry.get('new_id')!r} is not in the new model",
            )
        if kind == "insert":
            require(
                entry.get("new_id") in new_ids,
                f"{case_id}: new_id {entry.get('new_id')!r} is not in the new model",
            )
        if kind == "table_cell":
            require(
                entry.get("old_cell_id") in base_cell_ids,
                f"{case_id}: old_cell_id {entry.get('old_cell_id')!r} is not in the base model",
            )


# ---------------------------------------------------------------------------
# Strict scoring
# ---------------------------------------------------------------------------


def _element_predictions(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    predictions = []
    for change in canonical.get("changes") or []:
        resolution = change.get("resolution") or {}
        predictions.append(
            {
                "kind": change.get("change_type"),
                "old_id": (change.get("old") or {}).get("element_id"),
                "new_id": (change.get("new") or {}).get("element_id"),
                "accepted": resolution.get("status") == "accepted",
                "needs_review": bool(resolution.get("needs_review")),
                "change_id": change.get("change_id"),
                "old_text": (change.get("old") or {}).get("text"),
                "new_text": (change.get("new") or {}).get("text"),
            }
        )
    return predictions


def _table_predictions(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    """Cell-level predictions. Acceptance requires the ROOT record to be accepted.

    ``cell.needs_review`` alone is not enough: a cell with ``needs_review`` false
    inside a table record whose ``resolution.status`` is not ``accepted`` (or
    missing) is an unresolved suggestion, not an accepted detection.
    """
    predictions = []
    for record in canonical.get("table_changes") or []:
        root_resolution = record.get("resolution") or {}
        root_status = root_resolution.get("status")
        root_needs_review = bool(root_resolution.get("needs_review"))
        for cell in record.get("cell_changes") or []:
            classification = cell.get("classification")
            if classification == "UNCHANGED":
                continue
            cell_needs_review = bool(cell.get("needs_review"))
            accepted = root_status == "accepted" and not cell_needs_review
            predictions.append(
                {
                    "kind": classification,
                    "old_cell_id": (cell.get("old") or {}).get("cell_id"),
                    "new_cell_id": (cell.get("new") or {}).get("cell_id"),
                    "accepted": accepted,
                    "needs_review": cell_needs_review or root_status != "accepted",
                    "root_status": root_status,
                    "root_needs_review": root_needs_review,
                    "cell_needs_review": cell_needs_review,
                    "old_text": (cell.get("old") or {}).get("text"),
                    "new_text": (cell.get("new") or {}).get("text"),
                }
            )
    return predictions


def _metric_block(
    *,
    tp: int,
    fp: int,
    fn: int,
    truth_count: int,
    prediction_count: int,
    review_only_correct: int = 0,
    precision_measurable: bool = True,
) -> dict[str, Any]:
    if not precision_measurable:
        precision = None
    elif tp + fp:
        precision = tp / (tp + fp)
    else:
        precision = None
    recall = tp / truth_count if truth_count else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    if truth_count == 0:
        status = "false_positive" if fp else "not_applicable"
    else:
        status = "evaluated"
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "truth_count": truth_count,
        "prediction_count": prediction_count,
        "review_only_correct": review_only_correct,
        "precision": None if precision is None else round(precision, 3),
        "recall": None if recall is None else round(recall, 3),
        "f1": None if f1 is None else round(f1, 3),
        "status": status,
    }


def _apply_review_stream(
    block: dict[str, Any],
    *,
    tp: int,
    truth_count: int,
    surfaced_for_review: int,
    never_surfaced: int,
) -> dict[str, Any]:
    """Attach stream B without ever letting review inflate asserted recall.

    Enforces support conservation: ``tp + surfaced_for_review + never_surfaced``
    must equal ``truth_count``, and stream A's ``tp + fn`` must too.  The
    ``detection_including_review`` union counts distinct declared truths (each
    truth is matched at most once) and is explicitly labelled as not resolving
    anything.
    """
    require(
        tp + surfaced_for_review + never_surfaced == truth_count,
        "truth conservation violated: "
        f"{tp}+{surfaced_for_review}+{never_surfaced} != {truth_count}",
    )
    require(
        block["fn"] == truth_count - tp,
        f"asserted recall conservation violated: fn={block['fn']} truth={truth_count} tp={tp}",
    )
    block["surfaced_for_review"] = surfaced_for_review
    block["never_surfaced"] = never_surfaced
    block["detection_including_review"] = tp + surfaced_for_review
    block["detection_including_review_note"] = (
        "nhãn riêng: gộp (accepted + đúng-cần-review) theo từng truthID khác nhau; "
        "KHÔNG tự động giải quyết và KHÔNG tính vào recall asserted"
    )
    return block


def _accepted_review_counts(canonical: dict[str, Any]) -> dict[str, int]:
    """Accepted vs review counts, consistent with the table root-resolution rule."""
    accepted = review = 0
    for change in canonical.get("changes") or []:
        status = (change.get("resolution") or {}).get("status")
        if status == "accepted":
            accepted += 1
        else:
            review += 1
    for record in canonical.get("table_changes") or []:
        root_status = (record.get("resolution") or {}).get("status")
        for cell in record.get("cell_changes") or []:
            if cell.get("classification") == "UNCHANGED":
                continue
            if root_status == "accepted" and not cell.get("needs_review"):
                accepted += 1
            else:
                review += 1
    return {"accepted": accepted, "review": review}


def _score_element_case(
    canonical: dict[str, Any], truth: list[dict[str, Any]], category: str
) -> dict[str, Any]:
    expected_kind = CATEGORY_KIND[category]
    engine_kind = KIND_TO_ENGINE[expected_kind]
    predictions = _element_predictions(canonical)
    used: set[int] = set()
    tp = 0
    never: list[dict[str, Any]] = []
    review_matches: list[dict[str, Any]] = []
    touched_ids = set()
    for prediction in predictions:
        if prediction["old_id"]:
            touched_ids.add(prediction["old_id"])
        if prediction["new_id"]:
            touched_ids.add(prediction["new_id"])
    for entry in truth:
        target = (entry["old_id"], entry["new_id"])
        match = None
        pair_match: dict[str, Any] | None = None
        for index, prediction in enumerate(predictions):
            if index in used:
                continue
            if prediction["kind"] == engine_kind and (
                prediction["old_id"],
                prediction["new_id"],
            ) == target:
                if pair_match is None:
                    pair_match = prediction
                if _payload_matches(entry, prediction):
                    match = index
                    break
        if match is None:
            item = {
                "expected": {"kind": entry["kind"], "old_id": entry["old_id"], "new_id": entry["new_id"]},
                "expected_payload": {
                    "before_text": entry["before_text"],
                    "after_text": entry["after_text"],
                },
                "old_text": entry["before_text"],
                "new_text": entry["after_text"],
            }
            if pair_match is not None:
                # Same kind and ids, but the canonical raw text payload differs.
                item.update(
                    {
                        "mismatch": "payload",
                        "touched_wrong": True,
                        "observed": {
                            "kind": pair_match["kind"],
                            "old_id": pair_match["old_id"],
                            "new_id": pair_match["new_id"],
                            "accepted": pair_match["accepted"],
                        },
                        "observed_payload": {
                            "old_text": pair_match["old_text"],
                            "new_text": pair_match["new_text"],
                        },
                        "reason": "đúng cặp/loại nhưng sai văn bản thô (payload)",
                    }
                )
            elif (entry["old_id"] in touched_ids) or (
                entry["new_id"] is not None and entry["new_id"] in touched_ids
            ):
                item.update(
                    {
                        "mismatch": "pair",
                        "touched_wrong": True,
                        "reason": "có dự đoán chạm tới nhưng sai cặp/loại — chưa khớp chính xác",
                    }
                )
            else:
                item.update(
                    {
                        "mismatch": "absent",
                        "touched_wrong": False,
                        "reason": "không có dự đoán nào chạm tới",
                    }
                )
            never.append(item)
            continue
        used.add(match)
        if predictions[match]["accepted"]:
            tp += 1
        else:
            review_matches.append(
                {
                    "expected": {"kind": entry["kind"], "old_id": entry["old_id"], "new_id": entry["new_id"]},
                    "old_text": entry["before_text"],
                    "new_text": entry["after_text"],
                    "reason": "khớp chính xác nhưng đang ở trạng thái review (chưa accepted)",
                }
            )

    truth_old = {entry["old_id"] for entry in truth if entry["old_id"]}
    truth_new = {entry["new_id"] for entry in truth if entry["new_id"]}
    truth_by_pair = {(entry["old_id"], entry["new_id"]): entry for entry in truth}
    scoped_accepted_fp: list[dict[str, Any]] = []
    scoped_review_suggestions: list[dict[str, Any]] = []
    unmodified = 0
    for index, prediction in enumerate(predictions):
        if index in used:
            continue
        pair_entry = truth_by_pair.get((prediction["old_id"], prediction["new_id"]))
        if (
            pair_entry is not None
            and prediction["kind"] == engine_kind
            and not _payload_matches(pair_entry, prediction)
        ):
            reason = "đúng cặp/loại nhưng sai văn bản thô (payload)"
        elif prediction["old_id"] in truth_old or prediction["new_id"] in truth_new:
            reason = "sai cặp/thăm dò sai loại"
        else:
            reason = "phần tử không được sửa (báo nhầm)"
            unmodified += 1
        item = {
            "observed": {
                "kind": prediction["kind"],
                "old_id": prediction["old_id"],
                "new_id": prediction["new_id"],
                "accepted": prediction["accepted"],
            },
            "old_text": prediction["old_text"],
            "new_text": prediction["new_text"],
            "reason": reason,
        }
        if prediction["accepted"]:
            scoped_accepted_fp.append(item)
        else:
            scoped_review_suggestions.append(item)

    # Cross-stream: this is an element case, so any table change is unexpected.
    cross_accepted_fp: list[dict[str, Any]] = []
    cross_review_suggestions: list[dict[str, Any]] = []
    cross_predictions = _table_predictions(canonical)
    for prediction in cross_predictions:
        item = {
            "observed": {
                "kind": prediction["kind"],
                "old_cell_id": prediction["old_cell_id"],
                "new_cell_id": prediction["new_cell_id"],
                "accepted": prediction["accepted"],
            },
            "old_text": prediction["old_text"],
            "new_text": prediction["new_text"],
            "reason": "thay đổi bảng không được khai báo trong ca phần tử",
        }
        if prediction["accepted"]:
            cross_accepted_fp.append(item)
        else:
            cross_review_suggestions.append(item)

    never_touched = sum(1 for item in never if item["touched_wrong"])
    fp = len(scoped_accepted_fp) + len(cross_accepted_fp)
    block = _metric_block(
        tp=tp,
        fp=fp,
        fn=len(truth) - tp,
        truth_count=len(truth),
        prediction_count=len(predictions) + len(cross_predictions),
        review_only_correct=len(review_matches),
    )
    block.update(
        {
            "category": category,
            "scoped_false_positives": len(scoped_accepted_fp),
            "cross_stream_accepted_fp": len(cross_accepted_fp),
            "cross_stream_review_suggestions": len(cross_review_suggestions),
            "review_suggestions": len(scoped_review_suggestions) + len(cross_review_suggestions),
            "unexpected_cross_stream": len(cross_predictions),
            "false_positive_examples": (scoped_accepted_fp + cross_accepted_fp)[:8],
            "review_suggestion_examples": (scoped_review_suggestions + cross_review_suggestions)[:8],
            "miss_examples": never[:8],
            "review_examples": review_matches[:8],
            "unmodified_false_positives": unmodified,
            "never_surfaced_touched_wrong": never_touched,
            "never_surfaced_truly_absent": len(never) - never_touched,
            **_mismatch_summary(never),
            **_accepted_review_counts(canonical),
        }
    )
    _apply_review_stream(
        block,
        tp=tp,
        truth_count=len(truth),
        surfaced_for_review=len(review_matches),
        never_surfaced=len(never),
    )
    return block


def _score_table_case(canonical: dict[str, Any], truth: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = _table_predictions(canonical)
    used: set[int] = set()
    tp = 0
    never: list[dict[str, Any]] = []
    review_matches: list[dict[str, Any]] = []
    touched_cells = set()
    for prediction in predictions:
        if prediction["old_cell_id"]:
            touched_cells.add(prediction["old_cell_id"])
        if prediction["new_cell_id"]:
            touched_cells.add(prediction["new_cell_id"])
    for entry in truth:
        match = None
        pair_match: dict[str, Any] | None = None
        for index, prediction in enumerate(predictions):
            if index in used:
                continue
            if (
                prediction["kind"] == "MODIFIED"
                and prediction["old_cell_id"] == entry["old_cell_id"]
                and prediction["new_cell_id"] == entry["new_cell_id"]
            ):
                if pair_match is None:
                    pair_match = prediction
                if _payload_matches(entry, prediction, cell=True):
                    match = index
                    break
        if match is None:
            item = {
                "expected": {
                    "kind": "table_cell",
                    "table_element_id": entry["table_element_id"],
                    "old_cell_id": entry["old_cell_id"],
                    "new_cell_id": entry["new_cell_id"],
                },
                "expected_payload": {
                    "before_text": entry["before_text"],
                    "after_text": entry["after_text"],
                },
                "old_text": entry["before_text"],
                "new_text": entry["after_text"],
            }
            if pair_match is not None:
                item.update(
                    {
                        "mismatch": "payload",
                        "touched_wrong": True,
                        "observed": {
                            "kind": pair_match["kind"],
                            "old_cell_id": pair_match["old_cell_id"],
                            "new_cell_id": pair_match["new_cell_id"],
                            "accepted": pair_match["accepted"],
                        },
                        "observed_payload": {
                            "old_text": pair_match["old_text"],
                            "new_text": pair_match["new_text"],
                        },
                        "reason": "đúng cặp/loại ô nhưng sai văn bản thô (payload)",
                    }
                )
            elif (entry["old_cell_id"] in touched_cells) or (
                entry["new_cell_id"] in touched_cells
            ):
                item.update(
                    {
                        "mismatch": "pair",
                        "touched_wrong": True,
                        "reason": "có thay đổi ô chạm tới nhưng sai cặp/loại — chưa khớp chính xác",
                    }
                )
            else:
                item.update(
                    {
                        "mismatch": "absent",
                        "touched_wrong": False,
                        "reason": "không có thay đổi ô nào chạm tới",
                    }
                )
            never.append(item)
            continue
        used.add(match)
        if predictions[match]["accepted"]:
            tp += 1
        else:
            review_matches.append(
                {
                    "expected": {
                        "kind": "table_cell",
                        "table_element_id": entry["table_element_id"],
                        "old_cell_id": entry["old_cell_id"],
                        "new_cell_id": entry["new_cell_id"],
                    },
                    "old_text": entry["before_text"],
                    "new_text": entry["after_text"],
                    "reason": "khớp chính xác nhưng đang ở trạng thái review (chưa accepted)",
                }
            )

    truth_cells = {entry["old_cell_id"] for entry in truth}
    truth_by_pair = {(entry["old_cell_id"], entry["new_cell_id"]): entry for entry in truth}
    scoped_accepted_fp: list[dict[str, Any]] = []
    scoped_review_suggestions: list[dict[str, Any]] = []
    unexpected_unmodified = 0
    for index, prediction in enumerate(predictions):
        if index in used:
            continue
        pair_entry = truth_by_pair.get(
            (prediction["old_cell_id"], prediction["new_cell_id"])
        )
        if (
            pair_entry is not None
            and prediction["kind"] == "MODIFIED"
            and not _payload_matches(pair_entry, prediction, cell=True)
        ):
            reason = "đúng cặp/loại ô nhưng sai văn bản thô (payload)"
        elif prediction["old_cell_id"] in truth_cells or prediction["new_cell_id"] in truth_cells:
            reason = "sai loại/cặp ô"
        else:
            reason = "ô không được sửa (báo nhầm)"
            unexpected_unmodified += 1
        item = {
            "observed": {
                "kind": prediction["kind"],
                "old_cell_id": prediction["old_cell_id"],
                "new_cell_id": prediction["new_cell_id"],
                "accepted": prediction["accepted"],
            },
            "old_text": prediction["old_text"],
            "new_text": prediction["new_text"],
            "reason": reason,
        }
        if prediction["accepted"]:
            scoped_accepted_fp.append(item)
        else:
            scoped_review_suggestions.append(item)

    # Cross-stream: this is a table case, so any element change is unexpected.
    cross_accepted_fp: list[dict[str, Any]] = []
    cross_review_suggestions: list[dict[str, Any]] = []
    cross_predictions = _element_predictions(canonical)
    for prediction in cross_predictions:
        item = {
            "observed": {
                "kind": prediction["kind"],
                "old_id": prediction["old_id"],
                "new_id": prediction["new_id"],
                "accepted": prediction["accepted"],
            },
            "old_text": prediction["old_text"],
            "new_text": prediction["new_text"],
            "reason": "thay đổi phần tử không được khai báo trong ca bảng",
        }
        if prediction["accepted"]:
            cross_accepted_fp.append(item)
        else:
            cross_review_suggestions.append(item)

    never_touched = sum(1 for item in never if item["touched_wrong"])
    fp = len(scoped_accepted_fp) + len(cross_accepted_fp)
    block = _metric_block(
        tp=tp,
        fp=fp,
        fn=len(truth) - tp,
        truth_count=len(truth),
        prediction_count=len(predictions) + len(cross_predictions),
        review_only_correct=len(review_matches),
    )
    block.update(
        {
            "category": "table_cell",
            "scoped_false_positives": len(scoped_accepted_fp),
            "cross_stream_accepted_fp": len(cross_accepted_fp),
            "cross_stream_review_suggestions": len(cross_review_suggestions),
            "review_suggestions": len(scoped_review_suggestions) + len(cross_review_suggestions),
            "unexpected_cross_stream": len(cross_predictions),
            "false_positive_examples": (scoped_accepted_fp + cross_accepted_fp)[:8],
            "review_suggestion_examples": (scoped_review_suggestions + cross_review_suggestions)[:8],
            "miss_examples": never[:8],
            "review_examples": review_matches[:8],
            "unexpected_unmodified_cells": unexpected_unmodified,
            "never_surfaced_touched_wrong": never_touched,
            "never_surfaced_truly_absent": len(never) - never_touched,
            **_mismatch_summary(never),
            **_accepted_review_counts(canonical),
        }
    )
    _apply_review_stream(
        block,
        tp=tp,
        truth_count=len(truth),
        surfaced_for_review=len(review_matches),
        never_surfaced=len(never),
    )
    return block


def _score_noise_case(
    canonical: dict[str, Any], truth: list[dict[str, Any]], all_truth: list[dict[str, Any]]
) -> dict[str, Any]:
    """Noise is a suppression condition: every wrap must stay quiet."""
    predictions = _element_predictions(canonical)
    table_predictions = _table_predictions(canonical)
    noise_ids = {entry["old_id"] for entry in truth}
    touched_records = [
        prediction
        for prediction in predictions
        if prediction["old_id"] in noise_ids or prediction["new_id"] in noise_ids
    ]
    leaked_cases = {
        prediction["old_id"] if prediction["old_id"] in noise_ids else prediction["new_id"]
        for prediction in touched_records
    }
    truth_old = {entry["old_id"] for entry in all_truth if entry["old_id"]}
    truth_new = {entry["new_id"] for entry in all_truth if entry["new_id"]}
    unmodified_records = [
        prediction
        for prediction in predictions
        if prediction["old_id"] not in truth_old and prediction["new_id"] not in truth_new
    ]
    # Any unexpected element change or table change also fails the noise verdict,
    # not only changes that touch an injected noise element.
    unexpected_element_changes = len(unmodified_records)
    unexpected_table_changes = len(table_predictions)

    truth_count = len(truth)
    suppressed = truth_count - len(leaked_cases)
    fp = len(leaked_cases) + unexpected_element_changes + unexpected_table_changes
    block = _metric_block(
        tp=max(0, suppressed),
        fp=fp,
        fn=0,
        truth_count=truth_count,
        prediction_count=truth_count + len(predictions) + len(table_predictions),
        precision_measurable=False,
    )
    block.update(
        {
            "category": "noise",
            "changed_records_touching_noise": len(touched_records),
            "changed_records_touching_unmodified": len(unmodified_records),
            "unexpected_unmodified_element_changes": unexpected_element_changes,
            "unexpected_table_changes": unexpected_table_changes,
            "surfaced_for_review": 0,
            "never_surfaced": 0,
            "detection_including_review": block["tp"],
            "detection_including_review_note": (
                "nhãn riêng: nhiễu là điều kiện kìm hãm; không có dự đoán dương để gộp"
            ),
            "leaked_examples": [
                {
                    "observed": {
                        "kind": prediction["kind"],
                        "old_id": prediction["old_id"],
                        "new_id": prediction["new_id"],
                    },
                    "old_text": prediction["old_text"],
                    "new_text": prediction["new_text"],
                    "reason": "nhiễu gạch nối bị báo thành thay đổi",
                }
                for prediction in touched_records[:8]
            ],
            "unexpected_change_examples": [
                {
                    "observed": {
                        "kind": prediction["kind"],
                        "old_id": prediction["old_id"],
                        "new_id": prediction["new_id"],
                    },
                    "old_text": prediction["old_text"],
                    "new_text": prediction["new_text"],
                    "reason": "thay đổi phần tử không được khai báo trong ca nhiễu",
                }
                for prediction in unmodified_records[:8]
            ]
            + [
                {
                    "observed": {
                        "kind": prediction["kind"],
                        "old_cell_id": prediction["old_cell_id"],
                        "new_cell_id": prediction["new_cell_id"],
                    },
                    "old_text": prediction["old_text"],
                    "new_text": prediction["new_text"],
                    "reason": "thay đổi bảng không được khai báo trong ca nhiễu",
                }
                for prediction in table_predictions[:8]
            ],
            **_accepted_review_counts(canonical),
        }
    )
    return block


def score_case(
    canonical: dict[str, Any],
    truth: list[dict[str, Any]],
    *,
    category: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    if category in ELEMENT_CATEGORIES:
        block = _score_element_case(canonical, truth, category)
    elif category == "table_cell":
        block = _score_table_case(canonical, truth)
    elif category == "noise":
        block = _score_noise_case(canonical, truth, truth)
    else:
        raise ValueError(f"unknown benchmark category {category!r}")

    block["case"] = category
    block["elapsed_seconds"] = round(elapsed_seconds, 3)
    block["supported"] = block["truth_count"] > 0
    block["small_sample"] = 0 < block["truth_count"] < SMALL_SAMPLE_FLOOR
    if category == "noise":
        block["ok"] = block["fp"] == 0 and block["truth_count"] > 0
        block["review_pending"] = False
    else:
        block["ok"] = (
            block["fp"] == 0
            and block["fn"] == 0
            and block["review_only_correct"] == 0
            and block.get("review_suggestions", 0) == 0
            and block.get("unexpected_cross_stream", 0) == 0
            and block["truth_count"] > 0
        )
        block["review_pending"] = (
            block["fp"] == 0
            and block.get("never_surfaced", 0) == 0
            and block.get("surfaced_for_review", 0) > 0
            and block.get("review_suggestions", 0) == 0
            and block.get("unexpected_cross_stream", 0) == 0
        )
    return block


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------


def _run_case(
    *,
    category: str,
    base_model: dict[str, Any],
    out_dir: Path,
    seed: int,
    count: int,
) -> dict[str, Any]:
    case_root = out_dir / "cases" / category
    output_dir = case_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    new_model, truth, support = build_case(base_model, category, seed=seed, count=count)
    base_ids = {element["element_id"] for element in base_model["document"]["elements"]}
    new_ids = {element["element_id"] for element in new_model["document"]["elements"]}
    base_cell_ids = {
        cell["cell_id"]
        for element in base_model["document"]["elements"]
        for cell in ((element.get("table") or {}).get("cells") or [])
        if isinstance(cell.get("cell_id"), str)
    }
    validate_truth(truth, base_ids=base_ids, new_ids=new_ids, base_cell_ids=base_cell_ids)

    _atomic_write_json(output_dir / "old_document.json", base_model)
    _atomic_write_json(output_dir / "new_document.json", new_model)
    truth_payload = {
        "benchmark_version": BENCHMARK_VERSION,
        "category": category,
        "seed": seed,
        "synthetic": True,
        "benchmark_only_provenance": True,
        "label": (
            "synthetic AFTER EXTRACTION: the variation copies the saved Step-3 "
            "internal model; it is not a fresh DI result and not the original PDF"
        ),
        "move_note": MOVE_NOTE,
        "support": support,
        "truth": truth,
    }
    # Truth is recorded before any prediction exists.
    _atomic_write_json(case_root / "truth.json", truth_payload)

    started = time.perf_counter()
    with _mock_env():
        cli.run_stages(BENCH_STAGES, case_root)
    elapsed = time.perf_counter() - started

    canonical = _load_json(output_dir / "comparison_result.json")
    block = score_case(canonical, truth, category=category, elapsed_seconds=elapsed)
    block["support"] = support
    block["insufficient_eligible"] = block["truth_count"] < support["requested"]
    block["canonical_sha256"] = sha256_file(output_dir / "comparison_result.json")
    block["new_document_sha256"] = sha256_file(output_dir / "new_document.json")
    block["truth_sha256"] = sha256_file(case_root / "truth.json")
    _atomic_write_json(case_root / "score.json", block)
    return block


def _run_identity(base_model: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    case_root = out_dir / "cases" / "identity"
    output_dir = case_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_dir / "old_document.json", base_model)
    _atomic_write_json(output_dir / "new_document.json", base_model)

    started = time.perf_counter()
    with _mock_env():
        cli.run_stages(BENCH_STAGES, case_root)
    elapsed = time.perf_counter() - started

    canonical = _load_json(output_dir / "comparison_result.json")
    changes = canonical.get("changes") or []
    table_changes = canonical.get("table_changes") or []
    from collections import Counter

    block = {
        "case": "identity",
        "category": "identity",
        "elapsed_seconds": round(elapsed, 3),
        "element_changes": len(changes),
        "table_changes": len(table_changes),
        "false_alarms": len(changes) + len(table_changes),
        "ok": len(changes) == 0 and len(table_changes) == 0,
        "review_queue_composition": dict(
            Counter(item.get("source_kind") for item in canonical.get("review_queue") or [])
        ),
        "canonical_sha256": sha256_file(output_dir / "comparison_result.json"),
    }
    _atomic_write_json(case_root / "score.json", block)
    return block


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _conclusion(report: dict[str, Any]) -> dict[str, Any]:
    categories = report["categories"]
    supported = [name for name in CATEGORY_ORDER if name in categories and categories[name]["supported"]]
    unscored = [name for name in CATEGORY_ORDER if name in categories and not categories[name]["supported"]]
    review_pending = [
        name for name in supported if categories[name].get("review_pending")
    ]
    failing = [
        name for name in supported if not categories[name]["ok"] and name not in review_pending
    ]
    small = [name for name in supported if categories[name]["small_sample"]]
    identity_ok = report["identity"]["ok"]
    overall_ok = bool(
        identity_ok and not failing and not review_pending and not small and supported
    )
    return {
        "ok": overall_ok,
        "supported_categories": supported,
        "unscored_categories": unscored,
        "failing_categories": failing,
        "review_pending_categories": review_pending,
        "small_sample_categories": small,
        "identity_ok": identity_ok,
        "note": (
            "Tổng kết KHÔNG màu xanh khi có ca được hỗ trợ thất bại/chưa chấm, "
            "khi có ca chỉ được đưa sang review (chưa accepted), khi mẫu nhỏ, "
            "hoặc khi đối chứng giống hệt báo thay đổi."
        ),
    }


def build_report(
    *,
    manifest: dict[str, Any],
    seed: int,
    config_fingerprint: str,
    identity: dict[str, Any],
    categories: dict[str, Any],
    elapsed_seconds: float,
    artifacts: Path,
    python_hash_seed: str | None = None,
    interpreter: str | None = None,
    python_version: str | None = None,
) -> dict[str, Any]:
    report = {
        "benchmark_version": BENCHMARK_VERSION,
        "schema": "performance_benchmark_report",
        "synthetic": True,
        "benchmark_only_provenance": True,
        "evaluation_scope": EVALUATION_SCOPE,
        "evaluation_scope_note": EVALUATION_SCOPE_NOTE,
        "seed": seed,
        "config_fingerprint": config_fingerprint,
        "python_hash_seed": (
            os.environ.get("PYTHONHASHSEED", "random/unset")
            if python_hash_seed is None
            else python_hash_seed
        ),
        "interpreter": sys.executable if interpreter is None else interpreter,
        "python_version": sys.version if python_version is None else python_version,
        "source": manifest,
        "artifacts_dir": str(artifacts),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "move_note": MOVE_NOTE,
        "before_after": BEFORE_AFTER_NOTE,
        "identity": identity,
        "categories": categories,
    }
    report["conclusion"] = _conclusion(report)
    return report


def _percent(value: float | None) -> str:
    if value is None:
        return "không xác định"
    return f"{value * 100:.1f}%"


def _fmt(value: Any) -> str:
    if value is None:
        return "không xác định"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _bar(value: float | None, label: str) -> str:
    if value is None:
        return (
            f"<div class='bar-wrap'><div class='bar' style='width:0%'></div></div>"
            f"<span class='muted'>{html.escape(label)}: không xác định</span>"
        )
    percent = max(0.0, min(1.0, value)) * 100
    return (
        f"<div class='bar-wrap'><div class='bar' style='width:{percent:.1f}%'></div></div>"
        f"<span>{html.escape(label)}: {percent:.1f}%</span>"
    )


def _truncate(text: Any, limit: int = 220) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\n", "\\n")
    if len(value) > limit:
        return value[:limit] + "…"
    return value


def _examples_html(items: list[dict[str, Any]], kind: str) -> str:
    if not items:
        return "<p class='muted'>không có ví dụ</p>"
    rows = []
    for item in items:
        observed = item.get("observed")
        expected = item.get("expected")
        if expected is not None:
            left = f"mong đợi: {html.escape(json.dumps(expected, ensure_ascii=False))}"
        else:
            left = f"dự đoán: {html.escape(json.dumps(observed, ensure_ascii=False))}"
        rows.append(
            "<li>"
            f"<div>{left}</div>"
            f"<div class='mono'>cũ: {html.escape(_truncate(item.get('old_text')))}</div>"
            f"<div class='mono'>mới: {html.escape(_truncate(item.get('new_text')))}</div>"
            f"<div class='muted'>lý do: {html.escape(str(item.get('reason')))}</div>"
            "</li>"
        )
    return f"<ul class='examples {kind}'>" + "".join(rows) + "</ul>"


def render_html(report: dict[str, Any]) -> str:
    """Self-contained Vietnamese HTML report; no external JS or assets."""
    conclusion = report["conclusion"]
    status_class = "ok" if conclusion["ok"] else "warn"
    headline = (
        "Tổng kết: ĐẠT trên các ca được hỗ trợ"
        if conclusion["ok"]
        else "Tổng kết: CHƯA ĐẠT — đọc kỹ các ca bên dưới"
    )
    headline_summary = _headline_summary(report)
    headline_html = (
        "<div class='headline'>"
        + "".join(f"<p>{html.escape(line)}</p>" for line in _headline_lines(headline_summary))
        + "</div>"
    )

    rows = []
    for name in CATEGORY_ORDER:
        block = report["categories"].get(name)
        if block is None:
            continue
        if block.get("ok"):
            block_class = "ok"
        elif block.get("review_pending"):
            block_class = "review"
        else:
            block_class = "warn"
        rows.append(
            f"<tr class='{block_class}'>"
            f"<td>{html.escape(CATEGORY_LABEL_VI.get(name, name))}</td>"
            f"<td>{block['truth_count']}</td>"
            f"<td>{block['tp']}</td>"
            f"<td>{block['fn']}</td>"
            f"<td>{block['fp']}</td>"
            f"<td>{block['surfaced_for_review']}</td>"
            f"<td>{block['never_surfaced']}</td>"
            f"<td>{block['detection_including_review']} <span class='muted'>(riêng)</span></td>"
            f"<td>{html.escape(_fmt(block['precision']))}</td>"
            f"<td>{html.escape(_fmt(block['recall']))}</td>"
            f"<td>{html.escape(_fmt(block['f1']))}</td>"
            f"<td>{block['elapsed_seconds']:.3f}s</td>"
            f"<td>{html.escape(block['status'])}</td>"
            f"<td><a href='cases/{html.escape(name)}/score.json'>chi tiết</a></td>"
            "</tr>"
        )

    detail_sections = []
    for name in CATEGORY_ORDER:
        block = report["categories"].get(name)
        if block is None:
            continue
        fp_examples = block.get("false_positive_examples") or block.get("leaked_examples") or []
        miss_examples = block.get("miss_examples") or []
        review_examples = block.get("review_examples") or []
        bars = (
            _bar(block["recall"], "recall asserted (accepted)")
            + _bar(block["precision"], "precision (accepted)")
            + _bar(block["f1"], "F1")
        )
        detail_sections.append(
            "<section class='section'>"
            f"<h3>{html.escape(CATEGORY_LABEL_VI.get(name, name))}</h3>"
            f"<p><b>Luồng A — thay đổi đã khai báo (asserted):</b> "
            f"hỗ trợ <b>{block['truth_count']}</b> · "
            f"bắt đúng accepted <b>{block['tp']}</b> · "
            f"bỏ sót (fn = truth − tp) <b>{block['fn']}</b> · "
            f"báo nhầm <b>{block['fp']}</b></p>"
            f"<p><b>Luồng B — định tuyến review hữu ích:</b> "
            f"đúng nhưng cần review <b>{block['surfaced_for_review']}</b> · "
            f"không hề xuất hiện <b>{block['never_surfaced']}</b> · "
            f"phát hiện gồm review <b>{block['detection_including_review']}</b> "
            f"<span class='muted'>(riêng; không tự giải quyết, không tính vào recall)</span></p>"
            f"<p>precision={html.escape(_fmt(block['precision']))}, "
            f"recall={html.escape(_fmt(block['recall']))}, "
            f"f1={html.escape(_fmt(block['f1']))} "
            f"<span class='muted'>(null = không xác định, không phải 0 và không phải hoàn hảo)</span></p>"
            f"<div class='bars'>{bars}</div>"
            f"<p class='muted'>trước/sau: {html.escape(report['before_after'])}</p>"
            f"<details><summary>Ví dụ thất bại / báo nhầm</summary>"
            f"<h4>Báo nhầm (false positive)</h4>{_examples_html(fp_examples, 'fp')}"
            f"<h4>Bỏ sót thật (không hề xuất hiện)</h4>{_examples_html(miss_examples, 'fn')}"
            f"<h4>Đúng nhưng cần review (không tính TP)</h4>{_examples_html(review_examples, 'review')}"
            "</details>"
            f"<p class='muted mono'>truth: cases/{html.escape(name)}/truth.json · "
            f"kết quả: cases/{html.escape(name)}/output/comparison_result.json</p>"
            "</section>"
        )

    identity = report["identity"]
    identity_html = (
        "<section class='section'>"
        "<h3>Đối chứng giống hệt (old vs old)</h3>"
        f"<p>Thay đổi phần tử: <b>{identity['element_changes']}</b> · "
        f"Thay đổi bảng: <b>{identity['table_changes']}</b> · "
        f"Báo động giả: <b>{identity['false_alarms']}</b> · "
        f"Thời gian: {identity['elapsed_seconds']:.3f}s</p>"
        f"<p class='muted'>review_queue: {html.escape(json.dumps(identity['review_queue_composition'], ensure_ascii=False))}</p>"
        "</section>"
    )

    source = report["source"]
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engine đang làm tốt đến đâu?</title>
<style>
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 1.5rem; color: #1c1c1c; }}
h1, h2, h3, h4 {{ color: #10305a; }}
table {{ border-collapse: collapse; margin: 0.5rem 0 1rem; }}
th, td {{ border: 1px solid #c8ccd4; padding: 0.25rem 0.5rem; font-size: 0.85rem; vertical-align: top; }}
th {{ background: #eef2f8; text-align: left; }}
tr.ok td:first-child {{ border-left: 5px solid #1a6b2a; }}
tr.review td:first-child {{ border-left: 5px solid #b8860b; }}
tr.warn td:first-child {{ border-left: 5px solid #a11; }}
.section {{ border: 1px solid #d7dbe2; border-radius: 6px; padding: 0.75rem 1rem; margin: 1rem 0; }}
.banner {{ padding: 0.75rem 1rem; border-radius: 6px; margin: 1rem 0; font-weight: 600; }}
.banner.ok {{ background: #e8f5ea; border: 1px solid #1a6b2a; color: #14501f; }}
.banner.warn {{ background: #fdecec; border: 1px solid #a11; color: #7a1010; }}
.headline {{ background: #f2f6ff; border: 1px solid #2a6fb0; border-radius: 6px; padding: 0.75rem 1rem; margin: 1rem 0; }}
.headline p {{ margin: 0.25rem 0; }}
.muted {{ color: #5b6472; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }}
.bar-wrap {{ background: #eef2f8; border: 1px solid #c8ccd4; border-radius: 4px; height: 14px; width: 320px; margin: 0.2rem 0; }}
.bar {{ background: #2a6fb0; height: 12px; }}
.bars {{ margin: 0.5rem 0; }}
.examples {{ font-size: 0.85rem; }}
.examples li {{ margin: 0.3rem 0; }}
details {{ margin: 0.4rem 0; }}
summary {{ cursor: pointer; font-weight: 600; }}
</style>
</head>
<body>
<h1>Engine đang làm tốt đến đâu?</h1>
{headline_html}
<div class="banner {status_class}">{html.escape(headline)}</div>
<p><b>Kết luận đơn giản:</b> {html.escape(_plain_conclusion(report))}</p>
<p class="muted">Thử nghiệm tổng hợp có kiểm soát (synthetic AFTER EXTRACTION): biến thể sao chép mô hình Step-3 đã lưu,
chạy s04-s15 với mock. Không gọi DI, không dùng PDF gốc. Toàn bộ sự thật được khai báo trước khi chạy dự đoán.</p>
<p class="muted">Nguồn: {html.escape(str(source.get('path')))} (sha256 {html.escape(str(source.get('sha256'))[:16])}…),
seed={report['seed']}, config_fingerprint={html.escape(report['config_fingerprint'])}, thời gian tổng={report['elapsed_seconds']:.3f}s</p>
<p class="muted">python_hash_seed={html.escape(str(report['python_hash_seed']))} · interpreter={html.escape(str(report['interpreter']))} · python_version={html.escape(str(report['python_version']))}</p>
<p class="muted">before/after: {html.escape(report['before_after'])}</p>
<p class="muted">phạm vi chấm (evaluation_scope): {html.escape(str(report.get('evaluation_scope', EVALUATION_SCOPE)))} — {html.escape(str(report.get('evaluation_scope_note', EVALUATION_SCOPE_NOTE)))}</p>

<h2>Bảng tổng hợp theo loại thay đổi</h2>
<table>
<tr><th>Loại</th><th>Hỗ trợ</th><th>Bắt đúng<br>(accepted)</th><th>Bỏ sót<br>(fn)</th><th>Báo nhầm<br>(fp)</th>
<th>Đúng cần review</th><th>Không hề xuất hiện</th><th>Phát hiện gồm review<br>(riêng)</th><th>Precision</th><th>Recall<br>(asserted)</th><th>F1</th><th>Thời gian</th><th>Trạng thái</th><th>Chi tiết</th></tr>
{''.join(rows)}
</table>

{identity_html}

<h2>Chi tiết từng loại</h2>
{''.join(detail_sections)}

<section class="section">
<h3>Ghi chú phương pháp</h3>
<ul>
<li>TP chỉ tính khi khớp <b>chính xác</b> cặp (loại, old_id, new_id), được <b>accepted</b> <b>và</b> khớp <b>văn bản thô canonical</b> trước/sau; sai nội dung văn bản (payload) dù cùng cặp cũng là FP+FN. Dự đoán trùng lặp không được gộp.</li>
<li>Phía không tồn tại trong sự thật (new của xoá, old của thêm) không có văn bản mong đợi; văn bản dự đoán ở phía đó bị bỏ qua. Sai cặp/loại (<code>wrong_pair</code>) và sai nội dung cùng cặp (<code>wrong_payload</code>) được báo riêng.</li>
<li>Sai cặp/sai loại tính là FP, và sự thật tương ứng tính là FN. Phần tử không được sửa mà bị báo cũng là FP.</li>
<li>Dự đoán <b>accepted</b> ở luồng khác (thay đổi bảng trong ca phần tử, hoặc thay đổi phần tử trong ca bảng) được tính là <b>FP xuyên luồng</b>, không bị bỏ qua.</li>
<li>Phát hiện khớp nhưng còn ở trạng thái review được báo riêng, không tính TP. Gợi ý review không khớp cũng được báo riêng và <b>không được đánh đồng</b> với FP accepted.</li>
<li>Ô bảng chỉ được coi là accepted khi <b>cả</b> bản ghi gốc <code>resolution.status == "accepted"</code> <b>và</b> ô không <code>needs_review</code>.</li>
<li>Loại không có sự thật là <b>not_applicable</b> (null), không phải điểm hoàn hảo.</li>
<li>{html.escape(MOVE_NOTE)}.</li>
<li>Không tuyên bố xác suất đã hiệu chỉnh.</li>
</ul>
</section>
</body>
</html>
"""


def _headline_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Plain-language headline totals, derived from the actual category blocks.

    Totals cover only the semantic change categories (word, numeric, delete,
    insert, table_cell).  Noise is a suppression condition and is reported
    separately, never folded into the semantic counts.  Problems are surfaced,
    not masked: false positives, genuine misses and unscored categories all
    appear explicitly.
    """
    categories = report["categories"]
    present = [
        name
        for name in SEMANTIC_CATEGORIES
        if name in categories and categories[name].get("supported")
    ]
    truth = sum(categories[name]["truth_count"] for name in present)
    accepted = sum(categories[name]["tp"] for name in present)
    review = sum(categories[name]["surfaced_for_review"] for name in present)
    never = sum(categories[name]["never_surfaced"] for name in present)
    fp = sum(categories[name]["fp"] for name in present)
    surfaced = accepted + review

    def percent(value: int) -> float | None:
        return None if not truth else round(value / truth * 100, 1)

    noise = categories.get("noise")
    noise_total = noise["truth_count"] if noise else 0
    noise_suppressed = noise["tp"] if noise else 0

    problems: list[str] = []
    if fp:
        problems.append(f"có {fp} báo nhầm (dự đoán accepted sai)")
    if never:
        problems.append(f"có {never} bỏ sót thật (không hề xuất hiện)")
    unscored = [
        name
        for name in SEMANTIC_CATEGORIES
        if name in categories and not categories[name].get("supported")
    ]
    if unscored:
        problems.append("chưa chấm được: " + ", ".join(unscored))

    statements: list[str] = []
    if all(categories.get(name, {}).get("ok") for name in ("word", "numeric")):
        statements.append("Sửa chữ/số tốt trong mẫu")
    if any(categories.get(name, {}).get("review_pending") for name in ("delete", "insert")):
        statements.append("thêm/xóa còn cần review")
    if categories.get("table_cell", {}).get("small_sample"):
        statements.append("bảng mẫu nhỏ")
    return {
        "semantic_truth_count": truth,
        "semantic_accepted": accepted,
        "semantic_review": review,
        "semantic_surfaced": surfaced,
        "semantic_never_surfaced": never,
        "semantic_fp": fp,
        "accepted_percent": percent(accepted),
        "review_percent": percent(review),
        "surfaced_percent": percent(surfaced),
        "noise_total": noise_total,
        "noise_suppressed": noise_suppressed,
        "problems": problems,
        "statements": statements,
    }


def _headline_lines(headline: dict[str, Any]) -> list[str]:
    """Plain Vietnamese headline sentences (used by HTML and plaintext)."""
    truth = headline["semantic_truth_count"]
    accepted = headline["semantic_accepted"]
    review = headline["semantic_review"]
    surfaced = headline["semantic_surfaced"]
    lines = [
        (
            f"{accepted}/{truth} thay đổi đã khai báo được chấp nhận "
            f"({headline['accepted_percent']}%); "
            f"{review}/{truth} cần review ({headline['review_percent']}%); "
            f"tổng đúng-được-nêu {surfaced}/{truth} ({headline['surfaced_percent']}%)."
        ),
        (
            "Đây là thử nghiệm biến thể tổng hợp có kiểm soát, KHÔNG phải độ chính "
            "xác trên tài liệu thật và KHÔNG kết luận cải thiện."
        ),
    ]
    if headline["statements"]:
        lines.append("; ".join(headline["statements"]) + ".")
    lines.append(
        f"Nhiễu xuống dòng: {headline['noise_suppressed']}/{headline['noise_total']} "
        "xuống dòng được bỏ qua đúng."
    )
    if headline["problems"]:
        lines.append("Cần lưu ý: " + "; ".join(headline["problems"]) + ".")
    return lines


def _plain_conclusion(report: dict[str, Any]) -> str:
    conclusion = report["conclusion"]
    if conclusion["ok"]:
        return "Trên các loại được hỗ trợ, engine bắt đúng mọi thay đổi đã khai báo và không báo nhầm."
    parts = []
    if not conclusion["identity_ok"]:
        parts.append("đối chứng giống hệt lại báo thay đổi")
    if conclusion["failing_categories"]:
        parts.append("còn thất bại thật ở: " + ", ".join(conclusion["failing_categories"]))
    if conclusion["review_pending_categories"]:
        parts.append(
            "chỉ được đưa sang review (không phải lỗi sản phẩm nhưng chưa accepted) ở: "
            + ", ".join(conclusion["review_pending_categories"])
        )
    if conclusion["small_sample_categories"]:
        parts.append("mẫu còn nhỏ ở: " + ", ".join(conclusion["small_sample_categories"]))
    if not conclusion["supported_categories"]:
        parts.append("không có loại nào đủ dữ liệu để chấm")
    return "Chưa thể kết luận tốt: " + "; ".join(parts) + "."


def render_text_summary(report: dict[str, Any]) -> str:
    """Plain Vietnamese per-case outcome summary (no markup)."""
    identity = report["identity"]
    lines = [
        "Engine đang làm tốt đến đâu? — tóm tắt dạng văn bản",
        f"seed={report['seed']} config_fingerprint={report['config_fingerprint']} "
        f"thời gian tổng={report['elapsed_seconds']:.3f}s",
        f"python_hash_seed={report['python_hash_seed']} interpreter={report['interpreter']} "
        f"python_version={report['python_version']}",
        f"nguồn={report['source'].get('path')} sha256={report['source'].get('sha256')}",
        f"phạm vi chấm (evaluation_scope)={report.get('evaluation_scope', EVALUATION_SCOPE)}",
        (
            "đối chứng giống hệt (old vs old): "
            f"thay đổi phần tử={identity['element_changes']}, "
            f"thay đổi bảng={identity['table_changes']} -> "
            f"{'ĐẠT' if identity['ok'] else 'KHÔNG ĐẠT'}"
        ),
    ]
    lines.append("— Kết luận nhanh —")
    lines.extend(_headline_lines(_headline_summary(report)))
    for name in CATEGORY_ORDER:
        block = report["categories"].get(name)
        if block is None:
            continue
        if block.get("ok"):
            verdict = "ĐẠT"
        elif block.get("review_pending"):
            verdict = "REVIEW (chưa accepted)"
        else:
            verdict = "CHƯA ĐẠT"
        lines.append(
            f"- {CATEGORY_LABEL_VI.get(name, name)}: hỗ trợ={block['truth_count']} | "
            f"LUỒNG A: bắt đúng accepted={block['tp']}, bỏ sót(fn)={block['fn']}, "
            f"báo nhầm(fp)={block['fp']} | "
            f"LUỒNG B: đúng-cần-review={block['surfaced_for_review']}, "
            f"không hề xuất hiện={block['never_surfaced']}, "
            f"phát hiện-gồm-review={block['detection_including_review']} (riêng, không tự giải quyết) | "
            f"precision={_fmt(block['precision'])}, recall={_fmt(block['recall'])}, "
            f"f1={_fmt(block['f1'])} | {block['elapsed_seconds']:.3f}s | {verdict}"
        )
    lines.append("kết luận: " + _plain_conclusion(report))
    lines.append("trước/sau: " + report["before_after"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def benchmark_performance_command(
    *,
    artifacts: Path,
    out: Path,
    seed: int = DEFAULT_SEED,
    counts: dict[str, int] | None = None,
) -> Path:
    """Run the controlled benchmark and write a fresh, self-contained bundle."""
    artifacts = Path(artifacts).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    require(artifacts.is_dir(), f"artifacts directory does not exist: {artifacts}")
    require(
        out != artifacts and artifacts not in out.parents,
        "output directory must not be inside the input artifact tree",
    )
    if out.exists():
        require(
            out.is_dir() and not any(out.iterdir()),
            f"refusing to write into populated output directory: {out}",
        )
    out.mkdir(parents=True, exist_ok=True)

    resolved_counts = dict(DEFAULT_COUNTS)
    if counts:
        resolved_counts.update(counts)

    started = time.perf_counter()
    base_model, manifest = load_base_model(artifacts, out / "_derived" / "staging")
    base_ids = sorted(element["element_id"] for element in base_model["document"]["elements"])
    # A deterministic content/config fingerprint. This is NOT a PYTHONHASHSEED:
    # it pins the input and selection config, not the interpreter's hash seed.
    config_fingerprint = hashlib.sha256(
        f"{seed}:{manifest['sha256']}:{','.join(base_ids)}".encode("utf-8")
    ).hexdigest()[:16]

    identity = _run_identity(base_model, out)
    categories: dict[str, Any] = {}
    for category in ("word", "numeric", "delete", "insert", "noise", "table_cell"):
        categories[category] = _run_case(
            category=category,
            base_model=base_model,
            out_dir=out,
            seed=seed,
            count=resolved_counts.get(category, DEFAULT_COUNTS[category]),
        )

    elapsed = time.perf_counter() - started
    report = build_report(
        manifest=manifest,
        seed=seed,
        config_fingerprint=config_fingerprint,
        identity=identity,
        categories=categories,
        elapsed_seconds=elapsed,
        artifacts=artifacts,
        python_hash_seed=os.environ.get("PYTHONHASHSEED", "random/unset"),
        interpreter=sys.executable,
        python_version=sys.version,
    )
    report["counts"] = resolved_counts
    report["config"] = {
        "evaluator": "mock",
        "domain_mode": "generic",
        "stage_range": ["04", "15"],
        "synthetic_after_extraction": True,
        "reproduce_hint": (
            "Để cố định môi trường, chạy lại với PYTHONHASHSEED=0 đặt từ bên ngoài "
            "tiến trình: PYTHONHASHSEED=0 python -m document_compare.phase1.evaluation.performance_benchmark "
            "--artifacts <dir> --out <fresh-dir> --seed 42"
        ),
    }
    _atomic_write_json(out / "benchmark_report.json", report)
    _atomic_write_text(out / "index.html", render_html(report))
    _atomic_write_text(out / "summary_vi.txt", render_text_summary(report))
    return out
