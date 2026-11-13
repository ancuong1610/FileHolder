import json
import re
import tempfile
import unittest
from pathlib import Path

import pymupdf

from document_compare.phase1.pipeline.cli import run_demo
from document_compare.phase2.viewer.synthetic import build_synthetic_pdf, seed_demo_job
from document_compare.phase2.workflow.saved_workspace import SavedWorkspaceGateway


class ViewerSyntheticTests(unittest.TestCase):
    def test_pdf_matches_document_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = run_demo("internal_policy_report", Path(tmp) / "demo")
            doc = json.loads((root / "output" / "old_document.json").read_text("utf-8"))["document"]
            target = Path(tmp) / "old.pdf"
            build_synthetic_pdf(doc, target)
            with pymupdf.open(target) as pdf:
                self.assertEqual(pdf.page_count, doc["page_count"])
                self.assertEqual((pdf[0].rect.width, pdf[0].rect.height), (612, 792))
                element = doc["elements"][0]
                region = element["bounding_regions"][0]
                xs, ys = region["polygon"][0::2], region["polygon"][1::2]
                clip = pymupdf.Rect(min(xs) * 72, min(ys) * 72, max(xs) * 72, max(ys) * 72)
                text = pdf[region["page_number"] - 1].get_text("text", clip=clip)
                self.assertIn(element["text"].split()[0], text)

    def test_seed_demo_job_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_id = seed_demo_job(Path(tmp))
            self.assertRegex(job_id, r"^[0-9a-f]{12}$")
            job_dir = Path(tmp) / job_id
            for name in ("old.pdf", "new.pdf"):
                self.assertTrue((job_dir / "input" / "document" / name).read_bytes().startswith(b"%PDF-"))
            status = json.loads((job_dir / "job_status.json").read_text("utf-8"))
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["stages_completed"], [f"{i:02d}" for i in range(1, 16)])
            self.assertEqual(SavedWorkspaceGateway(job_dir).check()["reader_check"], "passed")

    def test_seed_refuses_existing_job_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = seed_demo_job(Path(tmp))
            marker = Path(tmp) / first / "marker.txt"
            marker.write_text("keep", "utf-8")
            second = seed_demo_job(Path(tmp))
            self.assertNotEqual(first, second)
            self.assertEqual(marker.read_text("utf-8"), "keep")
            self.assertTrue(re.fullmatch(r"[0-9a-f]{12}", second))


if __name__ == "__main__":
    unittest.main()
