"""Synthetic mutation generator for pipeline evaluation.

Takes a real Step-3 internal document model (old side) and produces a mutated
copy that serves as the NEW document together with a ground-truth log.
Mutation types mirror the change kinds Phase 1 must detect:
  modify / delete / insert / move_block / table_cell
"""
from __future__ import annotations

import copy
import random
import re
from typing import Any

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{5,}")


def _scramble_word(word: str) -> str:
    if len(word) < 4:
        return word + "x"
    i = len(word) // 2
    return word[:i] + word[i + 1] + word[i] + word[i + 2 :]


def _mutate_one_text(text: str, rng: random.Random) -> tuple[str, bool]:
    matches = list(WORD_RE.finditer(text))
    # Preserve the 15% lead-in guard for multi-word text, but allow mutation
    # when the text consists of exactly one eligible word (e.g. table cells).
    if len(matches) == 1:
        editable = matches
    else:
        editable = [m for m in matches if m.start() > len(text) * 0.15]
    if not editable:
        return text, False
    target = rng.choice(editable)
    word = target.group(0)
    replacement = _scramble_word(word)
    if replacement == word:
        return text, False
    return text[: target.start()] + replacement + text[target.end() :], True


def _eligible_paragraphs(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in elements
        if e.get("element_type") == "paragraph"
        and e.get("comparison_eligible")
        and len(str(e.get("text") or "")) >= 100
        and len(e.get("element_id") or "") > 0
        and "-INS" not in e["element_id"]
    ]


def generate_mutations(
    model: dict[str, Any],
    *,
    seed: int = 42,
    n_modify: int = 8,
    n_delete: int = 4,
    n_insert: int = 4,
    move_block_size: int = 4,
    n_wrap_noise: int = 6,
    mutate_table_cell: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = random.Random(seed)
    mutated = copy.deepcopy(model)
    document = mutated["document"]
    elements = document["elements"]
    truth: list[dict[str, Any]] = []

    paragraphs = _eligible_paragraphs(elements)
    rng.shuffle(paragraphs)

    def take(count: int) -> list[dict[str, Any]]:
        taken = []
        while paragraphs and len(taken) < count:
            taken.append(paragraphs.pop())
        return taken

    # 1) word-level modifications -> expect MODIFIED detection
    for element in take(n_modify):
        new_text, changed = _mutate_one_text(str(element.get("text") or ""), rng)
        if not changed:
            continue
        normalized, normalized_changed = _mutate_one_text(
            str(element.get("normalized_text") or ""), rng
        )
        element["text"] = new_text
        if normalized_changed:
            element["normalized_text"] = normalized
        truth.append({"type": "modify", "element_id": element["element_id"]})

    # 2) deletions -> expect DELETED detection
    for element in take(n_delete):
        elements.remove(element)
        truth.append({"type": "delete", "element_id": element["element_id"]})

    # 3) insertions -> expect ADDED detection
    for index_in_batch, clone_source in enumerate(take(n_insert)):
        clone = copy.deepcopy(clone_source)
        new_text, changed = _mutate_one_text(str(clone.get("text") or ""), rng)
        if not changed:
            continue
        clone["text"] = new_text
        clone["element_id"] = f"{clone['element_id']}-INS{index_in_batch + 1}"
        position = rng.randrange(len(elements))
        elements.insert(position, clone)
        truth.append({"type": "insert", "element_id": clone["element_id"]})

    # 4) relocate a block of consecutive headings -> must stay paired
    heading_indexes = [
        index
        for index, element in enumerate(elements)
        if element.get("element_type") in {"heading", "title"}
        and str(element.get("text") or "").strip()
    ]
    moved_ids: list[str] = []
    if len(heading_indexes) >= move_block_size:
        block_range = heading_indexes[:move_block_size]
        block = [elements[index] for index in block_range]
        moved_ids = [e["element_id"] for e in block]
        for element in block:
            elements.remove(element)
        insertion = min(
            max(0, block_range[0] + rng.choice([-1, 1]) * rng.randrange(150, 350)),
            len(elements),
        )
        elements[insertion:insertion] = block
        truth.append({"type": "move_block", "element_ids": moved_ids})

    # 5) single table-cell edit -> expect table MODIFIED detection
    if mutate_table_cell:
        for element in elements:
            if element.get("element_type") != "table":
                continue
            cells = ((element.get("table") or {}).get("cells")) or []
            candidates = [
                c
                for c in cells
                if isinstance(c.get("row_index"), int)
                and c["row_index"] >= 1
                and len(str(c.get("normalized_text") or c.get("text") or "")) >= 6
            ]
            if not candidates:
                continue
            for cell in candidates:
                changed_any = False
                for field in ("text", "normalized_text"):
                    value = str(cell.get(field) or "")
                    new_value, changed = _mutate_one_text(value, rng)
                    if changed:
                        cell[field] = new_value
                        changed_any = True
                if changed_any:
                    truth.append(
                        {
                            "type": "table_cell",
                            "table_element_id": element["element_id"],
                            "cell_id": cell.get("cell_id"),
                        }
                    )
                    break
            if any(entry.get("table_element_id") == element["element_id"] for entry in truth):
                break

    # 6) hyphenation wrap noise -> must produce ZERO canonical changes.
    # Splits a long word with a real end-of-line hyphen followed by a literal
    # newline, exactly like DI renders a line break. This is intentional
    # line-wrap noise: only a raw ``-\n`` is source-proven under the approved
    # s11 policy. A same-line ``- `` would be ambiguous and would correctly
    # route to review instead of being suppressed.
    for element in take(n_wrap_noise):
        text = str(element.get("text") or "")
        words = list(WORD_RE.finditer(text))
        long_words = [w for w in words if (w.end() - w.start()) >= 8]
        if not long_words:
            continue
        target = rng.choice(long_words)
        word = target.group(0)
        cut = len(word) // 2
        wrapped = f"{word[:cut]}-\n{word[cut:]}"
        element["text"] = text[: target.start()] + wrapped + text[target.end() :]
        normalized = str(element.get("normalized_text") or "")
        if normalized:
            n_words = list(WORD_RE.finditer(normalized))
            n_long = [w for w in n_words if w.group(0) == word]
            if n_long:
                n_target = n_long[0]
                element["normalized_text"] = (
                    normalized[: n_target.start()] + wrapped + normalized[n_target.end() :]
                )
        truth.append({"type": "noise_hyphenation", "element_id": element["element_id"]})

    # renumber reading order sequentially
    for reading_order, element in enumerate(elements):
        element["reading_order"] = reading_order

    return mutated, truth
