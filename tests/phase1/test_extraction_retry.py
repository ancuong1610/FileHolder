"""Bounded Azure extraction retries and chunk-cache safety (Task 3).

All tests use mock.patch for the extraction call and sleep only. No network,
credentials, or real Azure calls are involved.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from azure.core.exceptions import HttpResponseError, ServiceRequestError

from document_compare.phase1.stages import s01_extract

RETRYABLE_STATUSES = (408, 429, 500, 502, 503, 504)
NONRETRYABLE_STATUSES = (400, 401, 403)


def _http_error(status_code: int) -> HttpResponseError:
    response = type(
        "_Response",
        (),
        {"status_code": status_code, "reason": f"HTTP {status_code}"},
    )()
    return HttpResponseError(message=f"HTTP {status_code}", response=response)


class ExtractPdfRangeWithRetryTest(unittest.TestCase):
    def test_successful_second_attempt_sleeps_two_seconds(self):
        calls = []

        def fake_extract(pdf_path, *, pages):
            calls.append(pages)
            if len(calls) == 1:
                raise _http_error(503)
            return {"pages": [{"pageNumber": 1}]}

        with mock.patch.object(
            s01_extract, "extract_pdf_range", side_effect=fake_extract
        ), mock.patch.object(s01_extract.time, "sleep") as sleep:
            result = s01_extract.extract_pdf_range_with_retry(
                Path("old.pdf"), pages="1-100"
            )

        self.assertEqual(result, {"pages": [{"pageNumber": 1}]})
        self.assertEqual(calls, ["1-100", "1-100"])
        sleep.assert_called_once_with(2.0)

    def test_each_retryable_status_is_retried(self):
        for status in RETRYABLE_STATUSES:
            with self.subTest(status=status):
                calls = []

                def fake_extract(pdf_path, *, pages):
                    calls.append(pages)
                    if len(calls) == 1:
                        raise _http_error(status)
                    return {"pages": []}

                with mock.patch.object(
                    s01_extract, "extract_pdf_range", side_effect=fake_extract
                ), mock.patch.object(s01_extract.time, "sleep"):
                    s01_extract.extract_pdf_range_with_retry(
                        Path("old.pdf"), pages="1-100"
                    )
                self.assertEqual(len(calls), 2)

    def test_service_request_error_is_retried(self):
        calls = []

        def fake_extract(pdf_path, *, pages):
            calls.append(pages)
            if len(calls) == 1:
                raise ServiceRequestError("connection reset")
            return {"pages": []}

        with mock.patch.object(
            s01_extract, "extract_pdf_range", side_effect=fake_extract
        ), mock.patch.object(s01_extract.time, "sleep") as sleep:
            s01_extract.extract_pdf_range_with_retry(Path("old.pdf"), pages="1-100")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(2.0)

    def test_exhausted_attempts_three_calls_sleep_two_then_four(self):
        with mock.patch.object(
            s01_extract, "extract_pdf_range", side_effect=_http_error(429)
        ) as extract, mock.patch.object(s01_extract.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as ctx:
                s01_extract.extract_pdf_range_with_retry(
                    Path("old.pdf"), pages="1-100", max_attempts=3
                )

        self.assertEqual(extract.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 4.0])
        message = str(ctx.exception)
        self.assertIn("old.pdf", message)
        self.assertIn("1-100", message)
        self.assertIsInstance(ctx.exception.__cause__, HttpResponseError)

    def test_nonretryable_status_fails_immediately_without_sleep(self):
        for status in NONRETRYABLE_STATUSES:
            with self.subTest(status=status):
                with mock.patch.object(
                    s01_extract, "extract_pdf_range", side_effect=_http_error(status)
                ) as extract, mock.patch.object(s01_extract.time, "sleep") as sleep:
                    with self.assertRaises(RuntimeError):
                        s01_extract.extract_pdf_range_with_retry(
                            Path("old.pdf"), pages="1-100"
                        )
                self.assertEqual(extract.call_count, 1)
                sleep.assert_not_called()

    def test_config_or_value_error_fails_immediately_without_sleep(self):
        for error in (RuntimeError("Missing TIA_API_KEY / TIA_AZURE_ENDPOINT"),
                      ValueError("unsupported result type"),
                      TypeError("bad argument")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    s01_extract, "extract_pdf_range", side_effect=error
                ) as extract, mock.patch.object(s01_extract.time, "sleep") as sleep:
                    with self.assertRaises(RuntimeError):
                        s01_extract.extract_pdf_range_with_retry(
                            Path("old.pdf"), pages="1-100"
                        )
                self.assertEqual(extract.call_count, 1)
                sleep.assert_not_called()

    def test_invalid_attempt_count_rejects_before_any_call(self):
        for bad in (0, -1, 1.5, "3", None):
            with self.subTest(max_attempts=bad):
                with mock.patch.object(
                    s01_extract, "extract_pdf_range"
                ) as extract, mock.patch.object(s01_extract.time, "sleep") as sleep:
                    with self.assertRaises(ValueError):
                        s01_extract.extract_pdf_range_with_retry(
                            Path("old.pdf"), pages="1-100", max_attempts=bad
                        )
                extract.assert_not_called()
                sleep.assert_not_called()

    def test_exception_context_never_contains_credential_value(self):
        secret = "super-secret-api-key"
        with mock.patch.object(
            s01_extract, "extract_pdf_range", side_effect=RuntimeError(secret)
        ), mock.patch.object(s01_extract.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                s01_extract.extract_pdf_range_with_retry(
                    Path("new.pdf"), pages="5-9"
                )
        message = str(ctx.exception)
        self.assertIn("new.pdf", message)
        self.assertIn("5-9", message)
        self.assertNotIn(secret, message)


class ChunkCachePartialFailureTest(unittest.TestCase):
    def test_failed_later_chunk_keeps_prior_and_writes_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "old.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            cache = root / "di_chunks"
            doc = {
                "name": "old_doc",
                "label": "old",
                "final_output": root / "old_di_raw.json",
            }
            calls = []

            def fake_extract(pdf_path, *, pages, max_attempts=3,
                             initial_delay_seconds=2.0):
                calls.append(pages)
                if pages == "2-2":
                    raise RuntimeError("transient page 2 failure")
                return {
                    "pages": [{"pageNumber": 1}],
                    "content": "a",
                    "string_index_type": "textElements",
                }

            with mock.patch.object(
                s01_extract, "configured_pdf_path", return_value=pdf
            ), mock.patch.object(
                s01_extract, "debug_page_range", return_value=None
            ), mock.patch.object(
                s01_extract, "pdf_page_count", return_value=2
            ), mock.patch.object(
                s01_extract, "pdf_sha256", return_value="sha-test"
            ), mock.patch.object(
                s01_extract, "collect_outline", return_value={
                    "status": "read_failure",
                    "entries": [],
                    "diagnostics": [{"reason": "outline_read_failure"}],
                },
            ), mock.patch.object(
                s01_extract, "chunk_dir", return_value=cache
            ), mock.patch.object(
                s01_extract, "CHUNK_SIZE", 1
            ), mock.patch.object(
                s01_extract, "extract_pdf_range_with_retry", side_effect=fake_extract
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    s01_extract.extract_merge_or_load_document_from_pdf(doc)

            self.assertEqual(calls, ["1-1", "2-2"])
            chunk1 = cache / "chunk_001_pages_0001_0001.json"
            chunk2 = cache / "chunk_002_pages_0002_0002.json"
            self.assertTrue(chunk1.exists(), "prior valid chunk must survive")
            self.assertFalse(chunk2.exists(), "failed chunk must not be written")
            self.assertIn("old.pdf", str(ctx.exception))
            self.assertIn("2-2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
