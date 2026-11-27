"""Exercise an existing comparison job through the loopback chat API."""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from document_compare.phase2.ui.api_client import ApiClient, ApiError, DEFAULT_API_BASE, validate_base


def _section_items(sections: list[dict]) -> Iterator[dict]:
    for section in sections:
        yield from section.get("items") or []
        yield from _section_items(section.get("children") or [])


def _default_questions(outline: dict) -> list[tuple[str, dict[str, str], str]]:
    items = [*_section_items(outline.get("sections") or []), *(outline.get("unplaced") or [])]
    first = next((item for item in items if item.get("record_id")), {})
    important = next((item for item in items if item.get("record_id")
                      and (item.get("impact") or {}).get("level") == "content"
                      and (item.get("impact") or {}).get("priority") == "high"), {})
    context = {"selected_record_id": important["record_id"]} if important else {}
    if important.get("kind"):
        context["selected_kind"] = important["kind"]
    return [
        ("Was bedeutet Redaktionell?", {}, ""),
        ("Was heißt die gestrichelte Linie?", {}, ""),
        ("Was sind die wichtigsten Änderungen?", {}, ""),
        ("Welche Fristen wurden geändert?", {}, ""),
        ("Was hat sich bei der Auslagerung geändert?", {}, ""),
        ("Und welche davon sind noch offen?", {}, ""),
        ("Erklär diese Änderung.", context, "" if context else "No important finding in the outline."),
        ("Which changes still need review?", {}, ""),
        ("Gibt es Änderungen zu Kryptowährungen?", {}, ""),
        (f"Was ist {first['record_id']}?", {}, "") if first else
        ("Was ist <ID>?", {}, "No finding ID in the outline."),
    ]


def _remember(history: list[dict[str, str]], question: str, answer: str) -> None:
    history.extend(({"role": "user", "content": question}, {"role": "assistant", "content": answer[:4000]}))
    del history[:-6]
    while sum(len(turn["content"]) for turn in history) > 12_000:
        del history[:2]


def _report_entry(
    number: int, question: str, seconds: float, steps: list[dict], final: dict, issue: str,
) -> str:
    lines = [f"## {number}. {question}", "", f"- Latency: {seconds:.3f} s", "- Step events:"]
    lines.extend(f"  - `{json.dumps(event, ensure_ascii=False)}`" for event in steps)
    if not steps:
        lines.append("  - None")
    for field, label in (("cited", "Cited"), ("unverified_ids", "Unverified"),
                         ("limitations", "Limitations"), ("suggestions", "Suggestions")):
        lines.append(f"- {label}: {json.dumps(final.get(field, []), ensure_ascii=False)}")
    if issue:
        lines.append(f"- Status: {issue}")
    lines.extend(("", "### Answer", "", final.get("answer") or "—", ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="Completed job ID")
    parser.add_argument("--api", default=DEFAULT_API_BASE, help="Loopback HTTP API base")
    parser.add_argument("--questions", type=Path, help="UTF-8 file with one question per nonblank line")
    parser.add_argument("--out", type=Path, default=Path("workspace/agent_eval"), help="Markdown report directory")
    args = parser.parse_args(argv)
    try:
        base = validate_base(args.api)
    except ValueError:
        parser.error("--api must be a loopback HTTP base without a path, query or fragment")
    client = ApiClient(base)
    if args.questions:
        try:
            content = args.questions.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            parser.error("--questions must be a readable UTF-8 file")
        questions = [(line, {}, "") for line in map(str.strip, content.splitlines()) if line]
        if not questions:
            parser.error("--questions must contain at least one question")
    else:
        try:
            questions = _default_questions(client.outline(args.job))
        except ApiError as exc:
            parser.error(f"Could not load the job outline ({exc.code})")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    report = [f"# Chat evaluation\n\nJob `{args.job}` · {timestamp}\n"]
    history: list[dict[str, str]] = []
    summary = ["| # | Seconds | Steps | Cited | Unverified | Partial |", "|---:|---:|---:|---:|---:|:---|"]
    failed = False
    for number, (question, context, skip) in enumerate(questions, start=1):
        steps: list[dict] = []
        final: dict = {}
        seconds = 0.0
        issue = f"Skipped. {skip}" if skip else ""
        if not skip:
            started = time.perf_counter()
            try:
                for event in client.chat_stream(args.job, question, history=history, context=context):
                    if event["type"] == "final":
                        final = event
                    elif event["type"] in {"step", "fallback"}:
                        steps.append(event)
                if not final:
                    issue = "Failed (missing final event)."
            except ApiError as exc:
                issue = f"Failed ({exc.code})."
            seconds = time.perf_counter() - started
            failed |= bool(issue)
            if final.get("answer"):
                _remember(history, question, final["answer"])
        report.append(_report_entry(number, question, seconds, steps, final, issue))
        step_count = sum(event["type"] == "step" for event in steps)
        partial = "yes" if "partial_answer" in final.get("limitations", []) else "no"
        summary.append(f"| {number} | {seconds:.3f} | {step_count} | {len(final.get('cited', []))} | "
                       f"{len(final.get('unverified_ids', []))} | {partial} |")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / f"{timestamp}.md").open("x", encoding="utf-8") as output:
        output.write("\n".join(report))
    print("\n".join(summary))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
