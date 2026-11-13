import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from document_compare.phase2.viewer import import_workspace as iw


def _workspace(root: Path, missing: str | None = None) -> Path:
    ws = root / "EBA_TEST"
    (ws / "output").mkdir(parents=True)
    for name in iw.REQUIRED:
        if name != missing:
            (ws / "output" / name).write_text("{}", encoding="utf-8")
    (ws / "output" / "extra.json").write_text("{}", encoding="utf-8")
    return ws


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 test")
    return path


class ImportWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.jobs = self.root / "jobs"

    def test_creates_done_job_with_copies_and_names(self):
        ws = _workspace(self.root)
        old, new = _pdf(self.root / "input" / "Old Rule.pdf"), _pdf(self.root / "input" / "New Rule.pdf")
        job_id = iw.import_workspace(ws, old, new, self.jobs)
        job = self.jobs / job_id
        self.assertRegex(job_id, r"^[0-9a-f]{12}$")
        self.assertTrue((job / "output" / "comparison_result.json").is_file())
        self.assertTrue((job / "output" / "extra.json").is_file())
        self.assertEqual((job / "input" / "document" / "old.pdf").read_bytes(), old.read_bytes())
        status = json.loads((job / "job_status.json").read_text("utf-8"))
        self.assertEqual((status["job_id"], status["state"], status["old_name"], status["new_name"]),
                         (job_id, "done", "Old Rule.pdf", "New Rule.pdf"))
        self.assertTrue((ws / "output" / "comparison_result.json").is_file())  # source untouched

    def test_missing_artifact_is_reported_and_nothing_created(self):
        ws = _workspace(self.root, missing="new_document_structured.json")
        old, new = _pdf(self.root / "a.pdf"), _pdf(self.root / "b.pdf")
        with self.assertRaisesRegex(iw.ImportError_, "new_document_structured.json"):
            iw.import_workspace(ws, old, new, self.jobs)
        self.assertFalse(self.jobs.exists() and any(self.jobs.iterdir()))

    def test_bad_pdf_paths(self):
        ws = _workspace(self.root)
        old = _pdf(self.root / "a.pdf")
        with self.assertRaisesRegex(iw.ImportError_, "new PDF"):
            iw.import_workspace(ws, old, self.root / "nope.pdf", self.jobs)
        with self.assertRaisesRegex(iw.ImportError_, "same file"):
            iw.import_workspace(ws, old, old, self.jobs)

    def test_no_output_folder(self):
        (self.root / "empty").mkdir()
        with self.assertRaisesRegex(iw.ImportError_, "No output folder"):
            iw.import_workspace(self.root / "empty", self.root / "a.pdf", self.root / "b.pdf", self.jobs)

    def test_cli_args_and_quoted_paths(self):
        ws = _workspace(self.root)
        old, new = _pdf(self.root / "o.pdf"), _pdf(self.root / "n.pdf")
        out = io.StringIO()
        with redirect_stdout(out):
            code = iw.main(["--workspace", f'"{ws}"', "--old", str(old), "--new", str(new),
                            "--jobs-root", str(self.jobs)])
        self.assertEqual(code, 0)
        job_id = out.getvalue().strip().splitlines()[-1]
        self.assertTrue((self.jobs / job_id / "job_status.json").is_file())

    def test_cli_prompts_and_uses_workspace_pdf_defaults(self):
        ws = _workspace(self.root)
        _pdf(ws / "input" / "document" / "old.pdf")
        _pdf(ws / "input" / "document" / "new.pdf")
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=[str(ws), "", ""]), redirect_stdout(out):
            code = iw.main(["--jobs-root", str(self.jobs)])
        self.assertEqual(code, 0)
        status = json.loads((self.jobs / out.getvalue().strip().splitlines()[-1] / "job_status.json").read_text("utf-8"))
        self.assertEqual(status["old_name"], "old.pdf")

    def test_cli_error_exit_code(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = iw.main(["--workspace", str(self.root / "missing"), "--old", "a.pdf", "--new", "b.pdf",
                            "--jobs-root", str(self.jobs)])
        self.assertEqual(code, 1)
        self.assertIn("Not imported", err.getvalue())


if __name__ == "__main__":
    unittest.main()
