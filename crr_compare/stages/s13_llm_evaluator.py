"""Stage 13 - optional bounded LLM evaluator (false / mock / real).

Modes via env CRR_RUN_LLM (exactly ``false|mock|real``; default ``mock``):
  false - every packet becomes UNCERTAIN / not_run (fully deterministic)
  mock  - deterministic rule-based evaluator; writes a request/response
          trace per packet to output/llm_review_responses.jsonl so the
          real API shape can be inspected without any network call
  real  - actual provider call (bbk_ai_hub or openai_compatible), using
          existing providers and credentials only at call time

The mock reads each packet, decides among the three generic outcomes
(REAL_CHANGE / ARTIFACT / UNCERTAIN), and emits the same strict-JSON
response contract the real API must produce; parsing/validation/fallback
flow through the identical code path. There is no authorization acceptance
gate and no CRR/legal-verification vocabulary in the evaluator.

Imports are side-effect free: no file reads, no trace deletion, and no
provider invocation outside ``main()``/``call_llm_real``.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from crr_compare.common import find_project_root, output_dir
from crr_compare.stages.s12_packets import MAX_TEXT_CHARS

STEP13_VERSION = "3.0.0-generic-evaluator"
OUTCOMES = ("REAL_CHANGE", "ARTIFACT", "UNCERTAIN")
ALLOWED_KEYS = {"outcome", "confidence", "reason", "evidence_refs"}


def resolve_mode() -> str:
    """Normalize CRR_RUN_LLM; exactly ``false|mock|real``, default ``mock``."""
    mode = os.getenv("CRR_RUN_LLM", "mock").strip().lower()
    if mode not in {"false", "mock", "real"}:
        raise ValueError(
            f"Invalid CRR_RUN_LLM={mode!r}; expected one of: false, mock, real."
        )
    return mode


# Module-level constants remain for backward-compatible direct calls.
MODE = resolve_mode()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "bbk_ai_hub")
LLM_MODEL = os.getenv("LLM_MODEL", "mock-evaluator-v1" if MODE == "mock" else "")

SYSTEM_MESSAGE = """You are a conservative evaluator inside a document-comparison pipeline.
You do NOT re-match documents and you do NOT invent missing source content.

Choose exactly one outcome:
REAL_CHANGE: evidence supports a real source/content change.
ARTIFACT: difference is only extraction/layout/segmentation noise.
UNCERTAIN: evidence is insufficient.

Return strict JSON:
{
  "outcome": "...",
  "confidence": 0.0,
  "reason": "...",
  "evidence_refs": ["old.element_id", "new.element_id"]
}

Be conservative. If unsure, return UNCERTAIN.
"""


def build_prompt(packet: dict[str, Any]) -> str:
    """Build the provider prompt from the packet's bounded text only.

    Missing, invalid, or oversized text is a hard failure so the evaluator
    fallback handles it conservatively; no unbounded packet dump is used.
    """
    text = packet.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("Packet is missing bounded text.")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Packet text is not valid JSON: {exc}") from exc
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Packet text exceeds MAX_TEXT_CHARS ({len(text)} > {MAX_TEXT_CHARS})."
        )
    return "Evaluate this bounded special case.\n\n" + text


def _parse_body(packet: dict[str, Any]) -> dict[str, Any]:
    """Parse the packet's ``text`` body; return {} on any failure."""
    text = packet.get("text") or ""
    try:
        body = json.loads(text)
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# --------------------------------------------------------------------------
# Mock evaluator (deterministic, offline)
# --------------------------------------------------------------------------

def _body_similarity(packet: dict[str, Any]) -> float | None:
    body = _parse_body(packet)
    value = body.get("similarity")
    if isinstance(value, (int, float)):
        return float(value)
    evidence = body.get("similarity_evidence") or body.get("matching_evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    value = evidence.get("combined_similarity") or evidence.get("score")
    return float(value) if isinstance(value, (int, float)) else None


def mock_decide(packet: dict[str, Any]) -> tuple[str, float, str]:
    """Deterministic rule-based three-way verdict for one packet."""
    case_type = str(packet.get("case_type") or "")
    similarity = _body_similarity(packet)

    if case_type in {"possible_extraction_word_split", "hyphenation_wrap_artifact",
                     "tokenization_neutral_difference", "table_extraction_artifact",
                     "list_label_only_difference", "single_glyph_difference"}:
        return "ARTIFACT", 0.9, "Token content identical; difference is extraction/layout artifact only."
    if case_type in {"possible_marker_or_version_marker_difference", "structural_heading_boundary",
                     "structural_boundary", "resegmentation_suspect"}:
        return "ARTIFACT", 0.85, "Difference is marker/segmentation/structural, not substantive text."
    if case_type == "ambiguous_modified_pair":
        if (similarity or 0.0) >= 0.9:
            return "REAL_CHANGE", 0.8, f"High similarity pair ({similarity:.3f}) with a real token difference."
        return "UNCERTAIN", 0.3, "Ambiguous modified pair below high-confidence similarity."
    if case_type == "modified_with_review_evidence":
        if (similarity or 0.0) >= 0.86:
            return "REAL_CHANGE", 0.8, f"Substantive modification (similarity {similarity:.3f})."
        return "UNCERTAIN", 0.3, "Review evidence without strong similarity."
    if case_type == "table_modified_with_review_evidence":
        if (similarity or 0.0) >= 0.8:
            return "REAL_CHANGE", 0.8, f"Table content changed (combined similarity {similarity:.3f})."
        return "UNCERTAIN", 0.3, "Table change near similarity threshold."
    if case_type in {"deleted_table_candidate", "added_table_candidate"}:
        # "No counterpart found" is not semantic proof: extraction may have
        # represented the table differently. Requires human/real-LLM review.
        return "UNCERTAIN", 0.3, "One-sided table candidate: acceptance requires verified cell evidence."
    if case_type.endswith("_candidate_with_review_evidence"):
        return "UNCERTAIN", 0.3, "One-sided candidate carries review evidence; insufficient for auto-decision."
    return "UNCERTAIN", 0.3, "No rule matched; conservative default."


def _simulated_latency_ms(packet: dict[str, Any]) -> int:
    digest = hashlib.sha256(str(packet.get("packet_id") or "").encode("utf-8")).hexdigest()
    return 40 + (int(digest[:8], 16) % 120)


def call_llm_mock(packet: dict[str, Any], trace_path: Path | None = None, *, model: str | None = None) -> str:
    prompt = build_prompt(packet)
    outcome, confidence, reason = mock_decide(packet)
    body = _parse_body(packet)
    old_el = body.get("old") or {}
    new_el = body.get("new") or {}
    if isinstance(old_el, dict):
        refs = [ref for ref in (old_el.get("element_id"), new_el.get("element_id")) if ref]
    else:
        refs = []
    raw = json.dumps(
        {
            "outcome": outcome,
            "confidence": confidence,
            "reason": reason,
            "evidence_refs": refs,
        },
        ensure_ascii=False,
    )
    if trace_path is not None:
        model = model or LLM_MODEL
        trace = {
            "packet_id": packet.get("packet_id"),
            "route_id": packet.get("route_id"),
            "case_type": packet.get("case_type"),
            "request": {
                "model": model,
                "system_chars": len(SYSTEM_MESSAGE),
                "prompt_chars": len(prompt),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            },
            "response": {
                "content": json.loads(raw),
                "simulated_latency_ms": _simulated_latency_ms(packet),
            },
            "evaluator": "mock",
        }
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return raw


# --------------------------------------------------------------------------
# Real providers (unchanged behaviour; called only in real mode)
# --------------------------------------------------------------------------

def response_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "response", "output_text", "answer"):
            if isinstance(value.get(key), str):
                return value[key]
    return str(value)


def call_llm_real(packet: dict[str, Any], *, provider: str | None = None, model: str | None = None) -> str:
    provider = provider or LLM_PROVIDER
    model = model or LLM_MODEL
    prompt = build_prompt(packet)
    if provider == "bbk_ai_hub":
        import bbk_ai_hub_sdk

        api_key = os.getenv("TIA_API_KEY") or os.getenv("BBK_AI_HUB_API_KEY")
        if not api_key:
            raise RuntimeError("Missing TIA_API_KEY / BBK_AI_HUB_API_KEY.")
        return response_text(bbk_ai_hub_sdk.invoke_text_api(api_key, prompt, SYSTEM_MESSAGE))
    if provider == "openai_compatible":
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        return response.choices[0].message.content or ""
    raise ValueError(f"Unsupported LLM_PROVIDER={provider!r}")


def call_llm(packet: dict[str, Any], trace_path: Path | None = None, *, mode: str | None = None, provider: str | None = None, model: str | None = None) -> str:
    mode = mode or MODE
    if mode == "mock":
        return call_llm_mock(packet, trace_path, model=model)
    return call_llm_real(packet, provider=provider, model=model)


# --------------------------------------------------------------------------
# Strict JSON contract (identical for mock and real)
# --------------------------------------------------------------------------

def parse_decision(raw: str) -> dict[str, Any]:
    """Parse the entire string as one strict JSON object.

    No regex extraction, prose, or fences: ``json.loads`` must succeed on
    the whole string. The object may contain only ``outcome``,
    ``confidence``, ``reason``, ``evidence_refs``; missing optional fields
    default to 0.0 / "" / [].
    """
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"Decision must be a JSON object, got {type(obj).__name__}.")
    extra = set(obj) - ALLOWED_KEYS
    if extra:
        raise ValueError(f"Unexpected decision keys: {sorted(extra)}.")
    outcome = obj.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(
            f"Invalid outcome {outcome!r}; expected one of {', '.join(OUTCOMES)}."
        )
    confidence = obj.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number.")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence outside [0,1].")
    reason = obj.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string.")
    refs = obj.get("evidence_refs", [])
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise ValueError("evidence_refs must be a list of strings.")
    return {
        "outcome": outcome,
        "confidence": confidence,
        "reason": reason,
        "evidence_refs": list(refs),
    }


def _is_non_semantic_evidence(evidence: Any) -> bool:
    """Explicit non-semantic/layout-only evidence for STRUCTURAL_ONLY migration."""
    if not isinstance(evidence, dict):
        return False
    if evidence.get("semantic") is False:
        return True
    for key in ("non_semantic", "layout_only", "structural_only", "extraction_artifact"):
        if evidence.get(key) is True:
            return True
    kind = evidence.get("kind") or evidence.get("type")
    if isinstance(kind, str) and kind.casefold() in {
        "structural", "layout", "non_semantic", "extraction_artifact",
    }:
        return True
    return False


def normalize_legacy_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Migrate a legacy s13 decision dict to the generic three-outcome contract.

    KEEP_CHANGE -> REAL_CHANGE; SUPPRESS_ARTIFACT -> ARTIFACT;
    UNCERTAIN -> UNCERTAIN. STRUCTURAL_ONLY -> ARTIFACT with reason
    ``structural_only`` only when the decision carries explicit
    non-semantic/layout-only evidence; otherwise UNCERTAIN. The old
    ``decision`` field is never preserved.
    """
    if not isinstance(decision, dict):
        raise ValueError("Legacy decision must be a dict.")
    value = decision.get("decision")
    if value == "KEEP_CHANGE":
        outcome = "REAL_CHANGE"
    elif value == "SUPPRESS_ARTIFACT":
        outcome = "ARTIFACT"
    elif value == "UNCERTAIN":
        outcome = "UNCERTAIN"
    elif value == "STRUCTURAL_ONLY":
        if _is_non_semantic_evidence(decision.get("evidence")):
            outcome = "ARTIFACT"
        else:
            outcome = "UNCERTAIN"
    else:
        raise ValueError(f"Unknown legacy decision {value!r}.")
    result = {"outcome": outcome}
    confidence = decision.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        result["confidence"] = float(confidence)
    reason = decision.get("reason")
    if isinstance(reason, str):
        result["reason"] = reason
    refs = decision.get("evidence_refs")
    if isinstance(refs, list) and all(isinstance(r, str) for r in refs):
        result["evidence_refs"] = list(refs)
    if value == "STRUCTURAL_ONLY" and outcome == "ARTIFACT":
        result["reason"] = "structural_only"
    return result


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main() -> None:
    # Resolve runtime mode and provider/model locally so env changes after
    # import are honored; module constants remain only for compatibility.
    mode = resolve_mode()
    provider = os.getenv("LLM_PROVIDER", "bbk_ai_hub")
    model = os.getenv("LLM_MODEL", "mock-evaluator-v1" if mode == "mock" else "")

    root = find_project_root()
    out = output_dir(root)
    packets_path = out / "llm_review_packets.json"
    decisions_path = out / "llm_review_decisions.json"
    trace_path = out / "llm_review_responses.jsonl"

    packets_doc = json.loads(packets_path.read_text(encoding="utf-8"))
    if packets_doc.get("comparison_stage") != "special_case_packet_generation":
        raise SystemExit("Stage 13 requires Step 12 packets.")
    packets = packets_doc.get("packets") or []
    print(f"Packets: {len(packets)} | CRR_RUN_LLM = {mode}")

    if trace_path.exists():
        trace_path.unlink()

    decisions = []
    for packet in packets:
        base = {
            "packet_id": packet["packet_id"],
            "route_id": packet["route_id"],
            "candidate_id": packet.get("candidate_id"),
            "input_hash": packet["input_hash"],
            "case_type": packet.get("case_type"),
        }

        if mode == "false":
            decisions.append({
                **base,
                "outcome": "UNCERTAIN",
                "confidence": 0.0,
                "reason": "LLM evaluation disabled; review status preserved.",
                "evidence_refs": [],
                "evaluator": "not_run",
            })
            continue

        try:
            raw = call_llm(packet, trace_path, mode=mode, provider=provider, model=model)
            parsed = parse_decision(raw)
            item = {**base, **parsed, "evaluator": model if mode == "mock" else provider}
        except Exception as exc:  # fallback keeps the pipeline conservative
            item = {
                **base,
                "outcome": "UNCERTAIN",
                "confidence": 0.0,
                "reason": f"Evaluator failure: {type(exc).__name__}: {exc}",
                "evidence_refs": [],
                "evaluator": "fallback",
            }

        decisions.append(item)

    result = {
        "schema_version": "1.1.0",
        "comparison_stage": "optional_llm_special_case_evaluation",
        "processing": {
            "version": STEP13_VERSION,
            "mode": mode,
            "provider": (model if mode == "mock" else provider) if mode != "false" else None,
            "trace_file": trace_path.name if mode == "mock" else None,
        },
        "statistics": {
            "packets": len(packets),
            "decision_counts": dict(Counter(x["outcome"] for x in decisions)),
        },
        "decisions": decisions,
    }

    decisions_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    if mode == "mock":
        print(f"Request/response trace: {trace_path}")
    print("Saved:", decisions_path)


if __name__ == "__main__":
    main()
