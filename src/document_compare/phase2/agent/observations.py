"""Project reader observations for the planner without changing citation evidence."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


_READER_METADATA = {"snapshot", "limits", "canonical_reference", "ground_truth_notice", "provenance"}
_ASSESSMENT_METADATA = {"old_text_sha256", "new_text_sha256", "guards", "schema_version", "policy_version"}
_TOKEN_LISTS = {"old_tokens", "new_tokens"}
_CONTEXT_PREFIXES = ("old_context_", "new_context_")
_EDIT_LIMIT = 15
_NUMBER = re.compile(
    r"(?<!\w)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|"
    r"[+−-]?\d+(?:[.,'’]\d+|[ \u00a0\u202f]\d{3}(?!\d))*(?:[^\S\r\n]*%)?)(?!\w)"
)


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _slim_result(value: Any, parent: str = "") -> Any:
    if isinstance(value, list):
        return [_slim_result(item, parent) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, field in value.items():
        if key in _READER_METADATA or (parent == "semantic_assessment" and key in _ASSESSMENT_METADATA):
            continue
        if key in _TOKEN_LISTS or key.startswith(_CONTEXT_PREFIXES):
            if field == "<clipped>" or (isinstance(field, list) and "<clipped>" in field):
                continue
        result[key] = _slim_result(field, key)
    return result


def _changed_numbers(values: list[str], other: list[str]) -> list[str]:
    shared = Counter(other)
    changed = []
    for value in values:
        if shared[value]:
            shared[value] -= 1
        else:
            changed.append(value)
    return changed


def _change_brief(change: dict) -> dict:
    sides = {side: _object(change.get(side)) for side in ("old", "new")}
    numbers = {
        side: _NUMBER.findall(text) if isinstance(text := record.get("text"), str) else []
        for side, record in sides.items()
    }
    token_diff = change.get("token_diff")
    comparison_diff = _object(token_diff).get("comparison_token_diff")
    diff = _object(comparison_diff)
    changes = diff.get("changes")
    operations = changes if isinstance(changes, list) else []
    edits = [
        {"op": op.get("operation"), "old": op.get("old_text"), "new": op.get("new_text")}
        for op in operations[:_EDIT_LIMIT] if isinstance(op, dict)
    ]
    count = diff.get("change_operation_count")
    impact = _object(change.get("impact"))
    resolution = _object(change.get("resolution"))
    return {
        "type": change.get("change_type"),
        "pages": {side: record.get("primary_page") for side, record in sides.items()},
        "impact": {"level": impact.get("level"), "priority": impact.get("priority"),
                   "signals": impact.get("signals") or []},
        "needs_review": (
            change.get("needs_review") is True or resolution.get("needs_review") is True
            or change.get("result_state") == "uncertain"
            or "review" in (change.get("status"), resolution.get("status"))
        ),
        "edits": edits,
        "numbers": {"old": _changed_numbers(numbers["old"], numbers["new"]),
                    "new": _changed_numbers(numbers["new"], numbers["old"])},
        "edits_truncated": ("<clipped>" in (token_diff, comparison_diff, changes) or len(operations) > len(edits)
                            or (type(count) is int and count > len(edits))),
    }


def _block_member_briefs(block: dict) -> dict:
    briefs = {}
    side = block.get("side")
    for member in block.get("members") or []:
        if not isinstance(member, dict) or not member.get("change_id"):
            continue
        change = {"change_type": block.get("change_type"), **member}
        if isinstance(member.get("text"), str) and side in ("old", "new"):
            change.setdefault(side, member)
        if not any(isinstance(_object(change.get(key)).get("text"), str) for key in ("old", "new")):
            continue
        briefs[member["change_id"]] = _change_brief(change)
        if len(briefs) == 5:
            break
    return briefs


def planner_observation(observation: dict) -> dict:
    """Keep briefs beside the reader result and leave the original observation intact."""
    if "result" not in observation:
        return observation.copy()
    raw = observation["result"]
    projected = {**observation, "result": _slim_result(raw)}
    if observation["action"] == "details" and isinstance(raw, dict):
        if isinstance(raw.get("change"), dict):
            projected["change_brief"] = _change_brief(raw["change"])
        if isinstance(raw.get("block"), dict):
            briefs = _block_member_briefs(raw["block"])
            if briefs:
                projected["member_change_briefs"] = briefs
    return projected
