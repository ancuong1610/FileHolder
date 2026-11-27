"""Offline tests for the Streamlit app's HTTP client (httpx.MockTransport)."""
from __future__ import annotations

import json
import unittest

import httpx

from document_compare.phase2.ui.api_client import ApiClient, ApiError


def _client(handler) -> ApiClient:
    return ApiClient("http://127.0.0.1:8321", transport=httpx.MockTransport(handler))


class ApiClientTests(unittest.TestCase):
    def test_rejects_non_loopback_base(self):
        for bad in ("http://example.com:8321", "https://127.0.0.1:8321", "http://127.0.0.1:8321/x"):
            with self.assertRaises(ValueError):
                ApiClient(bad)
        ApiClient("http://localhost:9000")

    def test_submit_posts_two_pdfs_and_session_id(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"], seen["path"] = request.method, request.url.path
            seen["body"] = request.content
            return httpx.Response(202, json={"job_id": "abc", "state": "queued", "stages_completed": []})

        out = _client(handler).submit("o.pdf", b"%PDF-old", "n.pdf", b"%PDF-new", "sess_1")
        self.assertEqual(out["job_id"], "abc")
        self.assertEqual((seen["method"], seen["path"]), ("POST", "/jobs"))
        body = seen["body"]
        for token in (b'name="old_pdf"', b'filename="o.pdf"', b'name="new_pdf"', b'filename="n.pdf"',
                      b'name="session_id"', b"sess_1", b"%PDF-old", b"%PDF-new"):
            self.assertIn(token, body)

    def test_page_png_none_when_source_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("kind"):
                return httpx.Response(404, json={"detail": {"error": "source_pdf_unavailable", "message": "gone"}})
            return httpx.Response(200, content=b"\x89PNG-bytes")

        client = _client(handler)
        self.assertIsNone(client.page_png("0123456789ab", "old", 1, kind="change", record_id="CHANGE-000001"))
        self.assertEqual(client.page_png("0123456789ab", "old", 1), b"\x89PNG-bytes")

    def test_page_png_sends_highlight_params(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"], seen["params"] = request.url.path, dict(request.url.params)
            return httpx.Response(200, content=b"x")

        _client(handler).page_png("0123456789ab", "new", 3, kind="table", record_id="T1", uncertain=True)
        self.assertEqual(seen["path"], "/jobs/0123456789ab/view/pages/new/3.png")
        self.assertEqual(seen["params"], {"kind": "table", "record_id": "T1", "uncertain": "true"})

    def test_error_maps_to_api_error(self):
        cases = [
            (httpx.Response(404, json={"detail": {"error": "not_found", "message": "unknown job"}}),
             (404, "not_found", "unknown job")),
            (httpx.Response(422, json={"detail": [{"loc": ["query", "kind"], "msg": "bad kind"}]}),
             (422, "invalid_arguments", "bad kind")),
            (httpx.Response(400, json={"detail": "Uploads must be PDF files"}),
             (400, "error", "Uploads must be PDF files")),
            (httpx.Response(503, json={"detail": {"error": "chat_not_configured", "message": "nope"}}),
             (503, "chat_not_configured", "nope")),
            (httpx.Response(500, text="boom"), (500, "error", "HTTP 500")),
        ]
        for response, expected in cases:
            client = _client(lambda request, r=response: r)
            with self.assertRaises(ApiError) as ctx:
                client.status("0123456789ab")
            self.assertEqual((ctx.exception.status, ctx.exception.code, ctx.exception.message), expected)

    def test_transport_failure_is_api_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with self.assertRaises(ApiError) as ctx:
            _client(handler).summary("0123456789ab")
        self.assertIsNone(ctx.exception.status)
        self.assertEqual(ctx.exception.code, "api_unreachable")

    def test_read_helpers_use_expected_routes_and_drop_empty_filters(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.path, dict(request.url.params)))
            return httpx.Response(200, json={"ok": True})

        c = _client(handler)
        c.summary("0123456789ab")
        c.search("0123456789ab", query="", kind="all", status=None, change_type="ADDED", cursor=None, limit=10)
        c.details("0123456789ab", "change", "CHANGE-000001")
        c.locations("0123456789ab", "change", "CHANGE-000001")
        c.chat("0123456789ab", "why?")
        self.assertEqual(c.outline("0123456789ab"), {"ok": True})
        self.assertEqual(seen[5], ("/jobs/0123456789ab/view/outline", {}))
        self.assertEqual(seen[0], ("/jobs/0123456789ab/read/summary", {}))
        self.assertEqual(seen[1], ("/jobs/0123456789ab/read/search", {"kind": "all", "change_type": "ADDED", "limit": "10"}))
        self.assertEqual(seen[2], ("/jobs/0123456789ab/read/details", {"kind": "change", "record_id": "CHANGE-000001"}))
        self.assertEqual(seen[3][0], "/jobs/0123456789ab/view/locations")
        self.assertEqual(seen[4][0], "/jobs/0123456789ab/chat")

    def test_timeout_is_distinct_from_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        with self.assertRaises(ApiError) as ctx:
            _client(handler).chat("0123456789ab", "why?")
        self.assertIsNone(ctx.exception.status)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(ctx.exception.message, "The API did not answer in time; it may still be working.")

    def test_chat_uses_longer_timeout_than_other_calls(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.extensions["timeout"]["read"]
            return httpx.Response(200, json={"ok": True})

        c = _client(handler)
        c.summary("0123456789ab")
        c.chat("0123456789ab", "why?")
        self.assertEqual(seen["/jobs/0123456789ab/read/summary"], 60.0)
        self.assertEqual(seen["/jobs/0123456789ab/chat"], 300.0)

    def test_chat_sends_optional_history_and_context_only_when_given(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"answer": "ok"})

        client = _client(handler)
        history = [{"role": "user", "content": "What changed?"}]
        context = {"selected_kind": "change", "selected_record_id": "CHANGE-000001"}
        client.chat("0123456789ab", "why?")
        client.chat("0123456789ab", "why?", history=history, context=context)
        self.assertEqual(bodies, [
            {"question": "why?"},
            {"question": "why?", "history": history, "context": context},
        ])

    def test_chat_stream_decodes_lines_and_uses_chat_body_and_timeout(self):
        history = [{"role": "user", "content": "Frage"}]
        context = {"preset": "important"}

        def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/jobs/0123456789ab/chat/stream")
            self.assertEqual(request.extensions["timeout"]["read"], 300.0)
            self.assertEqual(json.loads(request.content), {
                "question": "Was ist neu?", "history": history, "context": context,
            })
            return httpx.Response(200, text='{"type":"step","action":"summarize"}\n\n'
                                            '{"type":"final","answer":"Antwort"}\n')

        events = list(_client(handler).chat_stream("0123456789ab", "Was ist neu?", history=history, context=context))
        self.assertEqual(events, [
            {"type": "step", "action": "summarize"}, {"type": "final", "answer": "Antwort"},
        ])

    def test_chat_stream_maps_http_and_in_band_errors(self):
        cases = [
            (httpx.Response(503, json={"detail": {"error": "chat_not_configured", "message": "not configured"}}),
             (503, "chat_not_configured", "not configured")),
            (httpx.Response(422, json={"detail": [{"msg": "invalid question"}]}),
             (422, "invalid_arguments", "invalid question")),
            (httpx.Response(200, text='{"type":"error","code":"chat_failed"}\n'), (None, "chat_failed", "")),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ApiError) as ctx:
                list(_client(lambda request: response).chat_stream("0123456789ab", "Frage"))
            self.assertEqual((ctx.exception.status, ctx.exception.code, ctx.exception.message), expected)

    def test_invalid_job_id_never_reaches_transport(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={})

        c = _client(handler)
        for bad in ("x/../../jobs", "ABCDEF123456", "abc", "0123456789abc", "", "0123456789a\n"):
            for call in (c.status, c.summary, c.search):
                with self.assertRaises(ApiError) as ctx:
                    call(bad)
                self.assertEqual((ctx.exception.status, ctx.exception.code, ctx.exception.message),
                                 (None, "invalid_arguments", "Job ID must be 12 lowercase hex characters."))
            for call in (lambda: c.details(bad, "change", "C1"), lambda: c.locations(bad, "change", "C1"), lambda: c.outline(bad),
                         lambda: c.page_png(bad, "old", 1), lambda: c.chat(bad, "q")):
                with self.assertRaises(ApiError):
                    call()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
