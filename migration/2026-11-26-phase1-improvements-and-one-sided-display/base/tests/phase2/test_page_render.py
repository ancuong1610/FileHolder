from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf

from document_compare.phase2.viewer.page_render import STYLES, render_page, style_for
from document_compare.phase2.viewer.synthetic import build_synthetic_pdf

# Element box in inches on an 8.5 x 11 page: x 1..5, y 2..3.
BOX = (1.0, 2.0, 5.0, 3.0)
POLY = [
    BOX[0] / 8.5, BOX[1] / 11, BOX[2] / 8.5, BOX[1] / 11,
    BOX[2] / 8.5, BOX[3] / 11, BOX[0] / 8.5, BOX[3] / 11,
]
DOCUMENT = {
    "page_count": 1,
    "pages": [{"page_number": 1, "width": 8.5, "height": 11}],
    "elements": [{
        "text": "MMMMMMMMMMMMMMMM MMMMMMMMMMMMMMMM MMMMMMMMMMMMMMMM",
        "bounding_regions": [{
            "page_number": 1,
            "polygon": [BOX[0], BOX[1], BOX[2], BOX[1], BOX[2], BOX[3], BOX[0], BOX[3]],
        }],
    }],
}


def _png_pixmap(data: bytes) -> pymupdf.Pixmap:
    return pymupdf.Pixmap(data)


def _diff_box(a: pymupdf.Pixmap, b: pymupdf.Pixmap):
    """Bounding box (x0, y0, x1, y1) of pixels that differ, or None."""
    sa, sb, n = a.samples, b.samples, a.n
    xs, ys = [], []
    for y in range(a.height):
        row = y * a.width * n
        if sa[row:row + a.width * n] == sb[row:row + a.width * n]:
            continue
        for x in range(a.width):
            i = row + x * n
            if sa[i:i + n] != sb[i:i + n]:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _dark_box(pix: pymupdf.Pixmap):
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            if sum(pix.pixel(x, y)[:3]) < 200:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


class PageRenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.pdf = self.dir / "page.pdf"
        build_synthetic_pdf(DOCUMENT, self.pdf)

    def test_style_helpers(self):
        self.assertEqual(style_for("DELETED", "old"), "deleted")
        self.assertEqual(style_for("ADDED", "new"), "added")
        self.assertEqual(style_for("MODIFIED", "new"), "modified")
        self.assertEqual(style_for("MOVED", "old"), "modified")
        self.assertEqual(set(STYLES), {"deleted", "added", "modified", "review"})
        self.assertEqual(STYLES["review"], (124, 58, 237))

    def test_pixel_inside_polygon_is_tinted_and_outside_is_not(self):
        base = _png_pixmap(render_page(self.pdf, 1, []))
        hit = _png_pixmap(render_page(self.pdf, 1, [POLY], style="added"))
        s = 1.5 * 72
        # Inside, away from text and outline: bottom area of the box.
        inside = (int(3.0 * s), int(2.97 * s) - 1)
        outside = (int(7.0 * s), int(9.0 * s))
        self.assertEqual(base.pixel(*inside), (255, 255, 255))
        r, g, b = hit.pixel(*inside)[:3]
        self.assertNotEqual((r, g, b), (255, 255, 255))
        self.assertGreater(r, 150)  # light tint of green (22,163,74) at 0.25
        self.assertGreater(g, r)
        self.assertEqual(hit.pixel(*outside), base.pixel(*outside))

    def test_table_role_draws_outline_without_fill(self):
        s = 1.5 * 72
        base = _png_pixmap(render_page(self.pdf, 1, []))
        table = _png_pixmap(render_page(self.pdf, 1, [POLY], roles=["table"]))
        cell = _png_pixmap(render_page(self.pdf, 1, [POLY], roles=["cell"]))
        inside = (int(3.0 * s), int(2.97 * s) - 1)
        self.assertEqual(table.pixel(*inside), base.pixel(*inside))  # no tint inside a table frame
        self.assertNotEqual(cell.pixel(*inside), base.pixel(*inside))
        edge = (int(1.0 * s), int(2.5 * s))  # left edge of the box carries the outline
        self.assertNotEqual(table.pixel(*edge), base.pixel(*edge))
        self.assertEqual(_png_pixmap(render_page(self.pdf, 1, [POLY])).samples, cell.samples)  # default = element

    def test_review_style_is_purple_and_roles_are_validated(self):
        s = 1.5 * 72
        inside = (int(3.0 * s), int(2.97 * s) - 1)
        r, g, b = _png_pixmap(render_page(self.pdf, 1, [POLY], style="review")).pixel(*inside)[:3]
        self.assertGreater(b, g)  # tint of (124, 58, 237) at 0.25 on white
        self.assertGreater(r, g)
        with self.assertRaises(ValueError):
            render_page(self.pdf, 1, [POLY], roles=["bogus"])
        with self.assertRaises(ValueError):
            render_page(self.pdf, 1, [POLY], roles=[])

    def test_image_size_follows_scale(self):
        for scale in (0.5, 1.5, 3.0):
            pix = _png_pixmap(render_page(self.pdf, 1, [POLY], scale=scale))
            self.assertLessEqual(abs(pix.width - round(612 * scale)), 1)
            self.assertLessEqual(abs(pix.height - round(792 * scale)), 1)

    def test_rotated_page_highlight_lands_on_text(self):
        rotated = self.dir / "rot.pdf"
        doc = pymupdf.open(str(self.pdf))
        doc[0].set_rotation(90)
        doc.save(str(rotated))
        doc.close()
        base = _png_pixmap(render_page(rotated, 1, []))
        hit = _png_pixmap(render_page(rotated, 1, [POLY]))
        self.assertGreater(base.width, base.height)  # landscape after rotation
        text = _dark_box(base)
        changed = _diff_box(base, hit)
        self.assertIsNotNone(text)
        self.assertIsNotNone(changed)
        tol = 6
        self.assertLessEqual(changed[0], text[0] + tol)
        self.assertLessEqual(changed[1], text[1] + tol)
        self.assertGreaterEqual(changed[2], text[2] - tol)
        self.assertGreaterEqual(changed[3], text[3] - tol)
        # The tinted area is exactly the element box mapped through the rotation.
        doc = pymupdf.open(str(rotated))
        expected = (pymupdf.Rect(*[v * 72 for v in BOX]) * doc[0].rotation_matrix).normalize()
        doc.close()
        for got, want in zip(changed, (expected.x0, expected.y0, expected.x1, expected.y1)):
            self.assertLessEqual(abs(got - want * 1.5), 4)

    def test_cropped_page_highlight_lands_on_text(self):
        cropped = self.dir / "crop.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((200, 250), "MMMMMMMM", fontsize=20)
        page.set_cropbox(pymupdf.Rect(100, 100, 400, 500))  # visible 300 x 400 pt
        doc.save(str(cropped))
        doc.close()
        vis_w, vis_h = 300.0, 400.0
        # Text occupies about x 100..235, y 135..155 in visible coordinates.
        x0, y0, x1, y1 = 90.0, 130.0, 250.0, 160.0
        poly = [x0 / vis_w, y0 / vis_h, x1 / vis_w, y0 / vis_h,
                x1 / vis_w, y1 / vis_h, x0 / vis_w, y1 / vis_h]
        base = _png_pixmap(render_page(cropped, 1, []))
        hit = _png_pixmap(render_page(cropped, 1, [poly]))
        self.assertEqual((base.width, base.height), (450, 600))  # crop box only
        text = _dark_box(base)
        changed = _diff_box(base, hit)
        self.assertIsNotNone(text)
        self.assertIsNotNone(changed)
        tol = 6
        self.assertLessEqual(changed[0], text[0] + tol)
        self.assertLessEqual(changed[1], text[1] + tol)
        self.assertGreaterEqual(changed[2], text[2] - tol)
        self.assertGreaterEqual(changed[3], text[3] - tol)
        for got, want in zip(changed, (x0 * 1.5, y0 * 1.5, x1 * 1.5, y1 * 1.5)):
            self.assertLessEqual(abs(got - want), 4)

    def test_pixel_cap_rejects_huge_pages(self):
        huge = self.dir / "huge.pdf"
        doc = pymupdf.open()
        doc.new_page(width=14400, height=14400)
        doc.save(str(huge))
        doc.close()
        with self.assertRaises(ValueError):
            render_page(huge, 1, [], scale=3.0)

    def test_uncertain_outline_differs_from_solid(self):
        solid = render_page(self.pdf, 1, [POLY])
        dashed = render_page(self.pdf, 1, [POLY], uncertain=True)
        self.assertNotEqual(solid, dashed)
        a, b = _png_pixmap(solid), _png_pixmap(dashed)
        self.assertIsNotNone(_diff_box(a, b))

    def test_rejects_bad_page_scale_style_polygon(self):
        good = POLY
        for kwargs in (
            {"page_number": 0}, {"page_number": 2}, {"page_number": -1},
        ):
            args = {"page_number": 1, **kwargs}
            with self.assertRaises(ValueError):
                render_page(self.pdf, args["page_number"], [good])
        for scale in (0.4, 3.1, float("nan")):
            with self.assertRaises(ValueError):
                render_page(self.pdf, 1, [good], scale=scale)
        with self.assertRaises(ValueError):
            render_page(self.pdf, 1, [good], style="bogus")
        for bad in ([0.1] * 7, [0.1] * 9, [0.1] * 7 + [float("inf")], [0.1] * 7 + [float("nan")],
                    [0.1] * 7 + ["x"]):
            with self.assertRaises(ValueError):
                render_page(self.pdf, 1, [bad])

    def test_source_pdf_is_unchanged(self):
        before = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        render_page(self.pdf, 1, [POLY], style="deleted", uncertain=True)
        self.assertEqual(hashlib.sha256(self.pdf.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
