"""Render one PDF page to PNG with outlined, tinted regions."""
from __future__ import annotations

import math
from pathlib import Path

import pymupdf

STYLES = {"deleted": (220, 38, 38), "added": (22, 163, 74), "modified": (217, 119, 6)}
FILL_OPACITY = 0.25
MIN_SCALE, MAX_SCALE = 0.5, 3.0
MAX_PIXELS = 40_000_000


def style_for(change_type: str, side: str) -> str:
    if change_type == "DELETED":
        return "deleted"
    if change_type == "ADDED":
        return "added"
    return "modified"


def _validate_polygon(polygon) -> list[float]:
    try:
        values = list(polygon)
    except TypeError as exc:
        raise ValueError("polygon must be a list of 8 numbers") from exc
    if len(values) != 8:
        raise ValueError("polygon must have exactly 8 numbers")
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("polygon values must be finite numbers")
        out.append(float(value))
    return out


def render_page(
    pdf_path: Path,
    page_number: int,
    polygons: list[list[float]],
    *,
    style: str = "modified",
    uncertain: bool = False,
    scale: float = 1.5,
) -> bytes:
    """Return PNG bytes of one page with normalized polygons highlighted.

    Polygons are normalized to the unrotated page. The source file is only
    read; drawing happens on the in-memory document, which is never saved.
    """
    if style not in STYLES:
        raise ValueError(f"unknown style: {style!r}")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) \
            or not MIN_SCALE <= scale <= MAX_SCALE:
        raise ValueError("scale must be between 0.5 and 3.0")
    checked = [_validate_polygon(p) for p in polygons]
    color = tuple(c / 255 for c in STYLES[style])
    document = pymupdf.open(str(pdf_path))
    try:
        if isinstance(page_number, bool) or not isinstance(page_number, int) \
                or not 1 <= page_number <= document.page_count:
            raise ValueError("page_number outside the document")
        page = document[page_number - 1]
        # Shape drawing takes unrotated coordinates relative to the crop box, and only
        # the crop box is rendered, so normalized polygons scale by the crop box size.
        width, height = page.cropbox.width, page.cropbox.height
        if width * scale * height * scale > MAX_PIXELS:
            raise ValueError("rendered page would exceed the pixel limit")
        for polygon in checked:
            points = [
                pymupdf.Point(polygon[i] * width, polygon[i + 1] * height)
                for i in range(0, 8, 2)
            ]
            shape = page.new_shape()
            shape.draw_polyline(points + [points[0]])
            shape.finish(
                color=color, fill=color, fill_opacity=FILL_OPACITY, stroke_opacity=1.0,
                width=1.5, dashes="[4 3] 0" if uncertain else None, closePath=True,
            )
            shape.commit()
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    finally:
        document.close()
