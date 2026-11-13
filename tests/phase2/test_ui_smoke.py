"""AppTest smoke tests for the Streamlit reviewer (stub client, no network)."""
from __future__ import annotations

import unittest

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover - streamlit is an optional extra
    raise unittest.SkipTest("streamlit is not installed")

from document_compare.phase2.ui import review
from tests.phase2.ui_stub import StubClient


def _script(stub):
    from document_compare.phase2.ui import app

    app.main(client=stub)


def make_app(stub: StubClient) -> AppTest:
    return AppTest.from_function(_script, args=(stub,), default_timeout=15)


def open_job(at: AppTest, job_id: str = "job1") -> AppTest:
    at.run()
    at.sidebar.text_input(key="open_job_id").set_value(job_id)
    at.sidebar.button(key="open_job").click()
    return at.run()


def texts(elements) -> str:
    return "\n".join(str(getattr(e, "value", "")) for e in elements)


class ShellTests(unittest.TestCase):
    def test_app_loads_without_job(self):
        at = make_app(StubClient()).run()
        self.assertFalse(at.exception)
        self.assertIn("Open an existing job", texts(at.sidebar.subheader) + texts(at.sidebar.markdown))
        self.assertIn("Comparing sends both PDFs to the configured extraction provider.",
                      texts(at.sidebar.caption) + texts(at.sidebar.markdown))
        self.assertEqual(len(at.sidebar.file_uploader), 2)

    def test_upload_requires_both_files(self):
        stub = StubClient()
        at = make_app(stub).run()
        at.sidebar.button(key="compare").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn("both", texts(at.sidebar.warning).lower())
        self.assertFalse([c for c in stub.calls if c[0] == "submit"])

    def test_upload_submits_both_pdfs_and_opens_job(self):
        stub = StubClient(status_value={"job_id": "job1", "state": "running", "stages_completed": ["01", "02", "03"]})
        at = make_app(stub).run()
        at.sidebar.file_uploader(key="old_pdf").upload("old.pdf", b"%PDF-old")
        at.sidebar.file_uploader(key="new_pdf").upload("new.pdf", b"%PDF-new")
        at.run()
        at.sidebar.button(key="compare").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn(("submit", "old.pdf", "new.pdf"), stub.calls)
        self.assertEqual(at.session_state["job_id"], "job1")
        self.assertIn("3/15", texts(at.main.markdown) + texts(at.main.caption))

    def test_running_job_shows_progress(self):
        stub = StubClient(status_value={"job_id": "job1", "state": "running", "stages_completed": ["01"] * 7})
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertIn("7/15", texts(at.main.markdown) + texts(at.main.caption))
        self.assertEqual(len(at.main.metric), 0)

    def test_failed_job_shows_error_and_keeps_job_id(self):
        stub = StubClient(status_value={"job_id": "job1", "state": "failed", "stages_completed": ["01"],
                                        "error_tail": "stage 02 exploded"})
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        shown = texts(at.main.error)
        self.assertIn("stage 02 exploded", shown)
        self.assertIn("job1", shown)
        self.assertEqual(at.session_state["job_id"], "job1")

    def test_api_down_shows_error(self):
        stub = StubClient()
        stub.raise_on["status"] = stub.make_error(None, "api_unreachable", "Could not reach the API at http://127.0.0.1:8321.")
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertIn("Could not reach the API", texts(at.main.error))
        self.assertEqual(at.session_state["job_id"], "job1")

    def test_open_existing_job_shows_summary_counts(self):
        at = open_job(make_app(StubClient()))
        self.assertFalse(at.exception)
        metrics = {m.label: m.value for m in at.main.metric}
        self.assertEqual(metrics.get("Changes"), "2")
        self.assertEqual(metrics.get("Uncertain"), "1")

    def test_invalid_job_id_shows_visible_error(self):
        stub = StubClient()
        stub.raise_on["status"] = stub.make_error(
            None, "invalid_arguments", "Job ID must be 12 lowercase hex characters.")
        at = open_job(make_app(stub), "x/../../jobs")
        self.assertFalse(at.exception)
        self.assertIn("Job ID must be 12 lowercase hex characters.", texts(at.main.error))

    def test_select_job_clears_chat_error(self):
        at = open_job(make_app(StubClient()))
        at.session_state["chat_error"] = {"code": "chat_failed", "message": "old failure"}
        at.run()
        self.assertIn("old failure", texts(at.sidebar.error))
        at.sidebar.text_input(key="open_job_id").set_value("job2")
        at.sidebar.button(key="open_job").click()
        at.run()
        self.assertNotIn("chat_error", at.session_state)
        self.assertNotIn("old failure", texts(at.sidebar.error))


def pick(at: AppTest, record_id: str, kind: str = "change") -> AppTest:
    at.main.button(key=f"pick::{kind}::{record_id}").click()
    return at.run()


def page_calls(stub: StubClient) -> list[tuple]:
    return [c for c in stub.calls if c[0] == "page_png"]


def side(counterpart="present", location="polygon", page_count=3, primary_page=1):
    return {"counterpart": counterpart, "location": location, "page_count": page_count,
            "primary_page": primary_page, "regions": []}


class ReviewTests(unittest.TestCase):
    def test_target_page_rules(self):
        self.assertEqual(review.target_page(side(primary_page=2), side(primary_page=7)), 2)
        # no counterpart: other side's page, clamped to this side's page count
        self.assertEqual(review.target_page(side("none", "unavailable", 3, None), side(primary_page=7)), 3)
        self.assertEqual(review.target_page(side("none", "unavailable", 5, None), side(primary_page=2)), 2)
        # no page count and no own page: nothing to show
        self.assertIsNone(review.target_page(side("none", "unavailable", None, None), side(primary_page=2)))
        self.assertIsNone(review.target_page(side("none", "unavailable", 3, None), side("none", "unavailable", 3, None)))

    def test_findings_list_shows_uncertain_by_default(self):
        stub = StubClient()
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        shown = "\n".join(b.label for b in at.main.button)
        self.assertIn("supported snippet", shown)
        self.assertIn("uncertain snippet", shown)
        self.assertIn("Uncertain", shown)
        first_search = [c for c in stub.calls if c[0] == "search"][0]
        self.assertEqual(first_search[2].get("kind"), "all")
        self.assertIn(first_search[2].get("status"), (None, "all"))

    def test_select_finding_requests_both_pages_with_highlight(self):
        stub = StubClient()
        at = pick(open_job(make_app(stub)), "CHANGE-000002")
        self.assertFalse(at.exception)
        calls = page_calls(stub)
        self.assertEqual({c[2] for c in calls}, {"old", "new"})
        for call in calls:
            self.assertEqual(call[4:], ("change", "CHANGE-000002", True))
        self.assertEqual(at.session_state["selected"], {"kind": "change", "record_id": "CHANGE-000002"})
        shown = texts(at.main.markdown) + texts(at.main.caption)
        self.assertIn("Uncertain", shown)
        self.assertIn("low similarity", shown)
        self.assertEqual(len(at.main.get("image")), 2)

    def test_next_previous_move_selection(self):
        at = pick(open_job(make_app(StubClient())), "CHANGE-000001")
        at.main.button(key="next_finding").click()
        at.run()
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000002")
        at.main.button(key="prev_finding").click()
        at.run()
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")
        self.assertFalse(at.exception)

    def test_filter_change_clears_selection(self):
        class FilteringStub(StubClient):
            def search(self, job_id, **filters):
                out = super().search(job_id, **filters)
                if filters.get("status") == "accepted":
                    out["returned"] = [r for r in out["returned"] if r["status"] == "accepted"]
                return out

        stub = FilteringStub()
        at = pick(open_job(make_app(stub)), "CHANGE-000002")
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000002")
        at.main.selectbox(key="f_state").select("Accepted")
        at.run()
        self.assertFalse(at.exception)
        self.assertFalse(at.session_state["selected"] if "selected" in at.session_state else None)
        self.assertIsNone(at.session_state["old_page"])
        self.assertIsNone(at.session_state["new_page"])
        self.assertEqual(len(at.main.get("image")), 0)
        self.assertIn("Select a finding", texts(at.main.info))
        self.assertEqual([b.key for b in at.main.button if (b.key or "").startswith("pick::")],
                         ["pick::change::CHANGE-000001"])
        # Next/Previous now work on the filtered list.
        at.main.button(key="next_finding").click()
        at.run()
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")

    def test_only_one_row_reads_selected(self):
        at = pick(open_job(make_app(StubClient())), "CHANGE-000001")
        at = pick(at, "CHANGE-000002")
        self.assertFalse(at.exception)
        selected = [b for b in at.main.button if b.label.startswith("Selected:")]
        self.assertEqual([b.key for b in selected], ["pick::change::CHANGE-000002"])

    def test_load_more_uses_next_cursor(self):
        stub = StubClient(next_cursors=["cur1"])
        at = open_job(make_app(stub))
        at.main.button(key="load_more").click()
        at.run()
        self.assertFalse(at.exception)
        cursors = [c[2].get("cursor") for c in stub.calls if c[0] == "search"]
        self.assertEqual(cursors[-1], "cur1")
        self.assertIn("MORE-cur1", "\n".join(b.key or "" for b in at.main.button))
        self.assertEqual(len([b for b in at.main.button if b.key == "load_more"]), 0)

    def test_page_controls_are_bounded(self):
        stub = StubClient()
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertTrue(at.main.button(key="old_prev").disabled)  # page 1 of 3
        at.main.button(key="old_next").click()
        at.run()
        self.assertEqual(at.session_state["old_page"], 2)
        at.main.button(key="old_next").click()
        at.run()
        self.assertEqual(at.session_state["old_page"], 3)
        self.assertTrue(at.main.button(key="old_next").disabled)
        self.assertIn(3, [c[3] for c in page_calls(stub) if c[2] == "old"])

    def test_no_counterpart_note_shown(self):
        stub = StubClient(locations_value={
            "kind": "change", "record_id": "CHANGE-000001", "change_type": "DELETED",
            "sides": {"old": side(primary_page=2), "new": side("none", "unavailable", 4, None)}})
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertFalse(at.exception)
        self.assertIn("No counterpart on this side", texts(at.main.caption) + texts(at.main.markdown))
        new_calls = [c for c in page_calls(stub) if c[2] == "new"]
        self.assertEqual(new_calls[0][3], 2)

    def test_source_unavailable_falls_back_to_text(self):
        stub = StubClient(png=None)
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertFalse(at.exception)
        self.assertIn("source PDF", texts(at.main.info) + texts(at.main.warning))
        shown = texts(at.main.text)
        self.assertIn("old text here", shown)
        self.assertIn("new text here", shown)
        self.assertEqual(len(at.main.get("image")), 0)

    def test_review_record_shows_text_only(self):
        stub = StubClient(locations_value={
            "kind": "review", "record_id": "REVIEW-000001", "change_type": None,
            "sides": {"old": side("none", "unavailable", 3, None), "new": side("none", "unavailable", 4, None)}})
        stub.rows = [{"kind": "review", "record_id": "REVIEW-000001", "change_type": None, "status": "review",
                      "result_state": "uncertain", "needs_review": True, "confirmed": False, "pages": [],
                      "snippet": "review snippet", "snippet_source": "x"}]
        at = pick(open_job(make_app(stub)), "REVIEW-000001", kind="review")
        self.assertFalse(at.exception)
        self.assertEqual(page_calls(stub), [])
        self.assertIn("needs a human", texts(at.main.markdown) + texts(at.main.caption))


class ChatTests(unittest.TestCase):
    def ask(self, stub, question="what changed?"):
        at = open_job(make_app(stub))
        at.sidebar.chat_input[0].set_value(question).run()
        return at

    def test_chat_not_configured_notice(self):
        stub = StubClient()
        stub.raise_on["chat"] = stub.make_error(503, "chat_not_configured", "Chat is not configured on this server.")
        at = self.ask(stub)
        self.assertFalse(at.exception)
        self.assertIn("not configured", texts(at.sidebar.info) + texts(at.sidebar.warning))
        self.assertEqual(len(at.main.metric), 4)  # rest of the UI still works

    def test_chat_appends_question_and_answer(self):
        stub = StubClient()
        at = self.ask(stub)
        self.assertFalse(at.exception)
        history = at.session_state["chat_history"]
        self.assertEqual([(m["role"], m["content"]) for m in history],
                         [("user", "what changed?"), ("assistant", "stub answer")])
        self.assertIn(("chat", "job1", "what changed?"), stub.calls)
        self.assertIn("stub answer", texts(at.sidebar.text))
        self.assertIn("Questions send bounded result excerpts to the configured model.",
                      texts(at.sidebar.caption) + texts(at.sidebar.markdown))
        self.assertIn("not legal or semantic ground truth",
                      texts(at.sidebar.caption) + texts(at.sidebar.markdown))

    def test_chat_error_is_shown_not_raised(self):
        stub = StubClient()
        stub.raise_on["chat"] = stub.make_error(502, "chat_failed", "The assistant could not produce an answer.")
        at = self.ask(stub)
        self.assertFalse(at.exception)
        self.assertIn("could not produce an answer", texts(at.sidebar.error))


if __name__ == "__main__":
    unittest.main()
