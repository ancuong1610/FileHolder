"""Run an independent OpenCode model audit over accepted Phase 1 changes."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from crr_compare.authorization import official_text_from_xhtml
from crr_compare.common import REPO_ROOT


ALLOWED = {"PASS", "FAIL", "UNCERTAIN"}


def build_packet(canonical: dict, official_sections: dict[str, str]) -> dict:
    changes = []
    articles = set()
    for change in canonical.get("changes") or []:
        if (change.get("resolution") or {}).get("status") != "accepted":
            continue
        authorization = change.get("authorization") or {}
        if authorization.get("status") != "authorized" or not authorization.get("article"):
            raise ValueError(f"Accepted change lacks CRR3 authorization: {change.get('change_id')}")
        article = str(authorization["article"])
        if article not in official_sections:
            raise ValueError(f"Official CRR3 section missing for Article {article}")
        articles.add(article)
        changes.append({
            "change_id": change["change_id"],
            "change_fingerprint": change.get("change_fingerprint"),
            "change_type": change.get("change_type"),
            "article": article,
            "old_text": (change.get("old") or {}).get("comparison_text"),
            "new_text": (change.get("new") or {}).get("comparison_text"),
            "source_locations": authorization.get("source_locations") or [],
        })
    if not changes:
        raise ValueError("Canonical result has no accepted changes to audit")
    return {
        "contract": "Each PASS requires the change's new text and operation to be supported by its same-article official section.",
        "changes": changes,
        "official_sections": {article: official_sections[article] for article in sorted(articles)},
    }


def parse_response(raw: str, expected_ids: set[str]) -> dict:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    decoder = json.JSONDecoder()
    value = None
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("decisions"), list):
            value = candidate
            break
    if value is None:
        raise ValueError("Model response contains no decisions JSON object")
    decisions = value["decisions"]
    ids = [item.get("change_id") for item in decisions if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise ValueError("Model response does not contain exactly one decision per accepted change")
    for item in decisions:
        if item.get("decision") not in ALLOWED or not str(item.get("reason") or "").strip():
            raise ValueError(f"Invalid model audit decision: {item!r}")
    return value


def run(root: Path, model: str) -> dict:
    output = root / "output"
    canonical_path = output / "comparison_result.json"
    xhtml_path = REPO_ROOT / "test_data" / "sources" / "regulation_2024_1623_de.xhtml"
    canonical_raw = canonical_path.read_bytes()
    canonical = json.loads(canonical_raw)
    packet = build_packet(canonical, official_text_from_xhtml(xhtml_path))
    packet_path = output / "release_audit_packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt = (
        "Audit every change in the attached packet independently. Use only the matching article in "
        "official_sections. Return strict JSON only: {\"decisions\":[{\"change_id\":\"...\","
        "\"decision\":\"PASS|FAIL|UNCERTAIN\",\"reason\":\"concise evidence\"}]}. "
        "PASS only when the same-article official text supports the candidate; otherwise FAIL or UNCERTAIN."
    )
    process = subprocess.run(
        ["opencode", "run", "--pure", "-m", model, prompt, "-f", str(packet_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if process.returncode != 0:
        raise RuntimeError(f"OpenCode audit failed: {process.stderr[-2000:]}")
    response = parse_response(process.stdout, {change["change_id"] for change in packet["changes"]})
    counts = Counter(item["decision"] for item in response["decisions"])
    result = {
        "schema_version": "1.0.0",
        "model": model,
        "audited_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "canonical_sha256": hashlib.sha256(canonical_raw).hexdigest(),
        "packet_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
        "statistics": {"changes": len(packet["changes"]), "decision_counts": dict(counts)},
        "passed": counts.get("PASS", 0) == len(packet["changes"]),
        "decisions": response["decisions"],
    }
    (output / "release_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "workspace")
    parser.add_argument("--model", default="opencode/hy3-free")
    args = parser.parse_args()
    result = run(args.root.resolve(), args.model)
    print(json.dumps(result["statistics"], indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
