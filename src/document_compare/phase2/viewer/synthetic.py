"""Synthetic source PDFs and a seeded, offline demo job for the viewer."""
from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from document_compare.phase1.pipeline.cli import run_demo

POINTS_PER_INCH = 72
STAGES = [f"{i:02d}" for i in range(1, 16)]


def _region_rect(polygon: list[float]) -> pymupdf.Rect | None:
    xs, ys = polygon[0::2], polygon[1::2]
    if not xs or not ys:
        return None
    return pymupdf.Rect(
        min(xs) * POINTS_PER_INCH, min(ys) * POINTS_PER_INCH,
        max(xs) * POINTS_PER_INCH, max(ys) * POINTS_PER_INCH,
    )


def _draw_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str) -> None:
    size = 10.0
    while size >= 3.0:
        if page.insert_textbox(rect, text, fontsize=size, fontname="helv") >= 0:
            return
        size -= 0.5
    # Too long for the box at the smallest size: keep the first line inside it.
    page.insert_text((rect.x0, rect.y0 + 3.0), text.split("\n")[0][:200], fontsize=3.0, fontname="helv")


def build_synthetic_pdf(document: dict, target: Path) -> None:
    """Write one PDF page per document page with element text in its first region."""
    pdf = pymupdf.open()
    try:
        index = {}
        for position, spec in enumerate(document["pages"]):
            pdf.new_page(
                width=float(spec["width"]) * POINTS_PER_INCH,
                height=float(spec["height"]) * POINTS_PER_INCH,
            )
            index[int(spec["page_number"])] = position
        for element in document.get("elements", []):
            regions = element.get("bounding_regions") or []
            text = element.get("text") or ""
            if not regions or not text.strip():
                continue
            position = index.get(int(regions[0]["page_number"]))
            rect = _region_rect(regions[0].get("polygon") or [])
            if position is None or rect is None:
                continue
            page = pdf[position]
            _draw_text(page, rect & page.rect, text)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(target))
    finally:
        pdf.close()


def seed_demo_job(jobs_root: Path, fixture: str = "internal_policy_report") -> str:
    """Run the offline demo into a new job directory and add source PDFs and status."""
    jobs_root = Path(jobs_root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    while True:
        job_id = secrets.token_hex(6)
        job_dir = jobs_root / job_id
        try:
            job_dir.mkdir()
            break
        except FileExistsError:
            continue
    run_demo(fixture, job_dir)
    document_dir = job_dir / "input" / "document"
    document_dir.mkdir(parents=True, exist_ok=True)
    for side in ("old", "new"):
        loaded = json.loads((job_dir / "output" / f"{side}_document.json").read_text("utf-8"))
        build_synthetic_pdf(loaded["document"], document_dir / f"{side}.pdf")
    status = {
        "job_id": job_id,
        "session_id": job_id,
        "state": "done",
        "stages_completed": list(STAGES),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "old_name": f"{fixture}_old.pdf",
        "new_name": f"{fixture}_new.pdf",
    }
    (job_dir / "job_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return job_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed an offline synthetic demo job.")
    parser.add_argument("--jobs-root", required=True, type=Path)
    parser.add_argument("--fixture", default="internal_policy_report")
    args = parser.parse_args(argv)
    print(seed_demo_job(args.jobs_root, args.fixture))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
