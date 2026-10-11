"""Parse official Publications Office metadata for CRR3 amendments."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path


CRR3_SOURCE = "http://publications.europa.eu/resource/oj/L_202401623"
CRR_TARGET = "http://publications.europa.eu/resource/celex/32013R0575"
RDF_RESOURCE = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource"
RDF_NODE_ID = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}nodeID"
RDF_ABOUT = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
OWL = "{http://www.w3.org/2002/07/owl#}"
ANNOTATION = "{http://publications.europa.eu/ontology/annotation#}"
LOCATION_PART = re.compile(r"\{(?P<code>[A-Z]+)\|[^}]+\}(?:\s*(?P<value>[^{}]+?))?(?=\s*\{|$)")
PART_NAMES = {
    "AR": "article",
    "PA": "paragraph",
    "PO": "point",
    "PTA": "point",
    "PTI": "subpoint",
    "ALN": "subparagraph",
    "FR": "first sentence",
    "TIT": "title",
    "TAB": "table",
    "TXT": "text",
    "PRT": "part",
    "TIS": "title subdivision",
    "SCT": "section",
    "CHA": "chapter",
    "SBS": "subsection",
    "COL": "column",
    "AN": "annex",
}
ROLE_NAMES = {"R": "replace", "J": "insert", "DEL": "delete"}
ARTICLE_RE = re.compile(r"(?:^|:)artikel(?:\s*:\s*|\s+)(\d+[a-z]*)\b", re.IGNORECASE)
ARTICLE_HEADING_RE = re.compile(r"\bartikel\s+(\d+[a-z]*)\b", re.IGNORECASE)
AMENDMENT_BLOCK_RE = re.compile(
    r"^\d+\.\s+(?:(?:in|folgender|folgende|folgendes)\s+)?artikel\s+(\d+[a-z]*)\b",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")


def _resources(node: ET.Element, tag: str) -> list[str | None]:
    return [child.get(RDF_RESOURCE) for child in node.findall(tag)]


def _texts(node: ET.Element, tag: str) -> list[str]:
    return [child.text.strip() for child in node.findall(tag) if child.text and child.text.strip()]


def parse_modified_location(raw: str) -> tuple[str | None, str]:
    parts = []
    article = None
    end = 0
    for match in LOCATION_PART.finditer(raw):
        if raw[end:match.start()].strip():
            raise ValueError(f"Unrecognized amendment location: {raw!r}")
        code = match.group("code")
        if code not in PART_NAMES:
            raise ValueError(f"Unknown amendment-location code {code!r}")
        value = (match.group("value") or "").strip()
        name = PART_NAMES[code]
        if code == "AR":
            article = value
        parts.append(f"{name} {value}".strip())
        end = match.end()
    if not parts or raw[end:].strip():
        raise ValueError(f"Unrecognized amendment location: {raw!r}")
    return article, "/".join(parts)


def parse_amendment_rdf(value: str | bytes) -> list[dict[str, str | None]]:
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed Publications Office RDF: {exc}") from exc

    records = []
    for node in root:
        sources = _resources(node, OWL + "annotatedSource")
        targets = _resources(node, OWL + "annotatedTarget")
        locations = _texts(node, ANNOTATION + "reference_to_modified_location")
        roles = _texts(node, ANNOTATION + "role2")
        dates = _texts(node, ANNOTATION + "start_of_validity")
        has_amendment_fields = bool(locations or roles or dates)
        if has_amendment_fields and (
            (CRR3_SOURCE in sources and not targets)
            or (CRR_TARGET in targets and not sources)
        ):
            raise ValueError("CRR3 amendment candidate is missing its source or target")
        if CRR3_SOURCE not in sources or CRR_TARGET not in targets:
            continue
        if sources != [CRR3_SOURCE] or targets != [CRR_TARGET]:
            raise ValueError("CRR3 amendment axiom has conflicting source or target values")
        if "http://www.w3.org/2002/07/owl#Axiom" not in _resources(
            node, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}type"
        ):
            raise ValueError("CRR3 amendment metadata is not an owl:Axiom")
        if len(locations) != 1 or len(roles) != 1 or len(dates) != 1:
            raise ValueError("CRR3 amendment axiom must have one location, role, and effective date")
        raw_location, raw_role, effective_date = locations[0], roles[0], dates[0]
        article, location = parse_modified_location(raw_location)
        role_match = re.fullmatch(r"\{(R|J|DEL)\|[^}]+\}", raw_role)
        if not role_match:
            raise ValueError(f"Unknown CRR3 amendment role: {raw_role!r}")
        role_code = role_match.group(1)
        try:
            dt.date.fromisoformat(effective_date)
        except ValueError as exc:
            raise ValueError(f"Invalid CRR3 effective date: {effective_date!r}") from exc
        source_node = node.get(RDF_NODE_ID) or node.get(RDF_ABOUT)
        if not source_node:
            raise ValueError("CRR3 amendment axiom has no stable RDF identifier")
        records.append({
            "article": article,
            "location": location,
            "raw_location": raw_location,
            "role": ROLE_NAMES.get(role_code, role_code.casefold()),
            "role_code": role_code,
            "raw_role": raw_role,
            "effective_date": effective_date,
            "source_node": source_node,
            "source": CRR3_SOURCE,
            "target": CRR_TARGET,
        })

    source_nodes = [record["source_node"] for record in records]
    if len(source_nodes) != len(set(source_nodes)):
        raise ValueError("Official source contains duplicate CRR3 amendment axiom identifiers")
    result = sorted(records, key=lambda record: json.dumps(record, ensure_ascii=False, sort_keys=True))
    if not result:
        raise ValueError("Official source contained no CRR3 amendments to Regulation 575/2013")
    return result


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_hash(source: Path, manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [item for item in manifest.get("sources", []) if item.get("file") == source.name]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(matches[0].get("sha256") or "")):
        raise ValueError(f"Manifest has no unique trusted hash for {source.name}")
    return matches[0]["sha256"]


def build_index(source: Path, manifest_path: Path | None = None) -> dict:
    raw = source.read_bytes()
    manifest_path = manifest_path or source.parent / "manifest.json"
    expected_hash = _expected_hash(source, manifest_path)
    if _sha256(raw) != expected_hash:
        raise ValueError(f"Official source hash does not match {manifest_path}")
    records = parse_amendment_rdf(raw)
    result = {
        "schema_version": "1.0.0",
        "source": {
            "celex": "32024R1623",
            "url": "http://publications.europa.eu/resource/celex/32024R1623",
            "sha256": _sha256(raw),
            "file": source.name,
        },
        "statistics": {"amendment_locations": len(records)},
        "records": records,
    }
    validate_index(result)
    return result


def validate_index(value: dict) -> None:
    if value.get("schema_version") != "1.0.0" or not isinstance(value.get("records"), list) or not value["records"]:
        raise ValueError("Invalid or empty CRR3 amendment index")
    source = value.get("source") or {}
    if source.get("celex") != "32024R1623" or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256") or "")):
        raise ValueError("Invalid CRR3 index provenance")
    if (value.get("statistics") or {}).get("amendment_locations") != len(value["records"]):
        raise ValueError("CRR3 index statistics do not match its records")
    required = {
        "article", "location", "raw_location", "role", "role_code", "raw_role",
        "effective_date", "source_node", "source", "target",
    }
    for record in value["records"]:
        if set(record) != required or record["source"] != CRR3_SOURCE or record["target"] != CRR_TARGET:
            raise ValueError("Invalid CRR3 amendment record shape or target")
        if not isinstance(record["source_node"], str) or not record["source_node"].strip():
            raise ValueError("Invalid CRR3 amendment source-node provenance")
        article, location = parse_modified_location(record["raw_location"])
        if article != record["article"] or location != record["location"]:
            raise ValueError("CRR3 amendment normalized location does not match raw evidence")
        if record["role_code"] not in ROLE_NAMES or record["role"] != ROLE_NAMES[record["role_code"]]:
            raise ValueError("Invalid CRR3 amendment role")
        if record["raw_role"] != record["raw_role"].strip() or not re.fullmatch(
            rf"\{{{record['role_code']}\|[^}}]+\}}", record["raw_role"]
        ):
            raise ValueError("Invalid raw CRR3 amendment role")
        try:
            dt.date.fromisoformat(record["effective_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid CRR3 amendment effective date") from exc
    if len({record["source_node"] for record in value["records"]}) != len(value["records"]):
        raise ValueError("Duplicate CRR3 amendment source nodes")


def load_index(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_index(value)
    source_path = path.parent / "sources" / value["source"]["file"]
    manifest_path = source_path.parent / "manifest.json"
    if not source_path.exists() or _sha256(source_path.read_bytes()) != value["source"]["sha256"]:
        raise ValueError(f"CRR3 index source is missing or has a mismatched hash: {source_path}")
    regenerated = build_index(source_path, manifest_path)
    if value != regenerated:
        raise ValueError("CRR3 amendment index does not exactly match its authenticated source")
    return value


def normalize_evidence_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\u00ad", "").replace("\u00a0", " ")
    value = value.translate(str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    }))
    return WHITESPACE.sub(" ", value).strip().casefold()


def official_text_from_xhtml(path: Path, manifest_path: Path | None = None) -> dict[str, str]:
    raw = path.read_bytes()
    manifest_path = manifest_path or path.parent / "manifest.json"
    if _sha256(raw) != _expected_hash(path, manifest_path):
        raise ValueError(f"Official XHTML hash does not match {manifest_path}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed official XHTML: {exc}") from exc
    sections: dict[str, list[str]] = {}

    def visit(node: ET.Element, inside_table: bool = False) -> None:
        is_table = node.tag.rsplit("}", 1)[-1] == "table"
        if is_table and not inside_table:
            text = normalize_evidence_text(" ".join(node.itertext()))
            match = AMENDMENT_BLOCK_RE.match(text)
            if match:
                sections.setdefault(match.group(1).casefold(), []).append(text)
            for descendant in node.iter():
                if descendant.tag.rsplit("}", 1)[-1] != "div":
                    continue
                articles = set()
                for heading in descendant.iter():
                    if "oj-ti-art" not in str(heading.get("class") or "").split():
                        continue
                    heading_match = ARTICLE_HEADING_RE.search(
                        normalize_evidence_text(" ".join(heading.itertext()))
                    )
                    if heading_match:
                        articles.add(heading_match.group(1).casefold())
                if len(articles) == 1:
                    sections.setdefault(articles.pop(), []).append(
                        normalize_evidence_text(" ".join(descendant.itertext()))
                    )
        for child in node:
            visit(child, inside_table or is_table)

    visit(root)
    result = {article: " ".join(parts) for article, parts in sections.items()}
    if not result:
        raise ValueError(f"Official XHTML contains no CRR amendment blocks: {path}")
    return result


def _article_of(element: dict | None) -> str | None:
    if not isinstance(element, dict):
        return None
    for key in ("comparison_text", "normalized_text", "text"):
        value = element.get(key)
        if isinstance(value, str):
            match = ARTICLE_RE.search(value.strip())
            if match and match.start() == 0:
                return match.group(1).casefold()
    keys = (element.get("provisional_anchor_context") or {}).get("recent_anchor_keys") or []
    for key in reversed(keys):
        match = ARTICLE_RE.search(str(key))
        if match:
            return match.group(1).casefold()
    return None


def authorize_candidate(
    group: str,
    candidate: dict,
    index: dict,
    official_text_by_article: dict[str, str],
) -> dict:
    element = candidate.get("new") or candidate.get("old")
    article = _article_of(element)
    records = [
        record for record in index.get("records", [])
        if str(record.get("article") or "").casefold() == article
    ]
    evidence = {
        "article": article,
        "source_locations": sorted({str(record.get("location")) for record in records}),
        "source_nodes": sorted({str(record.get("source_node")) for record in records if record.get("source_node")}),
    }
    if not article or not records:
        return {**evidence, "status": "unsupported", "reason": "article_not_amended_by_crr3"}
    if group == "deleted":
        return {**evidence, "status": "ambiguous", "reason": "deletion_requires_exact_target_mapping"}

    new = candidate.get("new") or {}
    text = normalize_evidence_text(str(
        new.get("comparison_text") or new.get("normalized_text") or new.get("text") or ""
    ))
    official = normalize_evidence_text(official_text_by_article.get(article, ""))
    if len(text) >= 40 and text in official:
        return {**evidence, "status": "authorized", "reason": "new_text_quoted_in_official_crr3"}

    direct_insert = any(
        record.get("role") == "insert" and record.get("location") == f"article {article}"
        for record in records
    )
    if group == "added" and direct_insert and ARTICLE_RE.fullmatch(text):
        return {**evidence, "status": "authorized", "reason": "new_article_explicitly_inserted_by_crr3"}
    return {**evidence, "status": "ambiguous", "reason": "article_amended_but_candidate_target_unproven"}


def gate_route(
    route: str,
    case_type: str,
    reasons: list[str],
    authorization: dict,
) -> tuple[str, str, list[str]]:
    if route not in {"ACCEPT_DETERMINISTIC", "SPECIAL_CASE_LLM_OPTIONAL"}:
        return route, case_type, reasons
    status = authorization.get("status")
    if status == "authorized":
        return route, case_type, reasons
    if status == "unsupported":
        return (
            "HUMAN_REVIEW",
            "unsupported_crr3_authorization",
            sorted(set([*reasons, "not_authorized_by_crr3"])),
        )
    return (
        "SPECIAL_CASE_LLM_OPTIONAL",
        case_type,
        sorted(set([*reasons, "crr3_authorization_requires_review"])),
    )


def can_accept_change(authorization: dict | None) -> bool:
    return bool(authorization and authorization.get("status") == "authorized")


def enforce_authorized_resolution(
    authorization: dict | None,
    status: str,
    needs_review: bool,
) -> tuple[str, bool]:
    if not can_accept_change(authorization):
        return "review", True
    return status, needs_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build_index(args.source), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
