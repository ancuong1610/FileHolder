"""Offline checks for grouped saved-result search, details, and viewer projection."""
import tempfile
import unittest
from pathlib import Path

from document_compare.phase2.reader import result_reader as reader
from document_compare.phase2.viewer.locations import resolve_locations
from document_compare.phase2.viewer.outline import build_outline
from tests.phase2.test_viewer_locations import _element, _page, _rect, _write_docs

JOB = "0123456789ab"
BLOCK_ID = "BLOCK-000001"


def _canonical(count=4):
    changes = []
    for index in range(1, count + 1):
        page = 1 if index == 1 else 2
        changes.append({
            "change_id": f"CHANGE-{index:06d}",
            "change_type": "DELETED",
            "old": {"element_id": f"E-{index}", "text": f"Aufbewahrung: Meldung {index} entfällt.",
                    "primary_page": page},
            "block_id": BLOCK_ID,
            "impact": {"level": "content", "priority": "normal", "kinds": ["block_member"]},
            "resolution": {"status": "review", "needs_review": True},
        })
    block = {
        "block_id": BLOCK_ID, "change_type": "DELETED", "side": "old",
        "anchor": "Geltungsbereich", "element_count": count, "pages": [1, 2],
        "contains_heading": True,
        "impact": {"level": "content", "priority": "normal", "kinds": ["section_deleted"]},
        "change_ids": [c["change_id"] for c in changes],
    }
    return {"changes": changes, "change_blocks": [block]}


class BlockReaderTests(unittest.TestCase):
    def test_default_search_collapses_members_and_searches_their_text(self):
        canonical = _canonical()
        hit, = reader.search(JOB, canonical, query="Meldung 4", priority="normal",
                             impact="content", status="review")["returned"]
        self.assertEqual((hit["kind"], hit["record_id"], hit["block_id"]), ("block", BLOCK_ID, BLOCK_ID))
        self.assertEqual(hit["pages"], [1, 2])
        self.assertEqual(hit["anchor"], "Geltungsbereich")
        self.assertEqual(hit["change_ids"], canonical["change_blocks"][0]["change_ids"])
        self.assertEqual(reader.search(JOB, canonical, kind="change")["total_matches"], 1)
        self.assertEqual(reader.search(JOB, canonical, priority="high")["total_matches"], 0)
        self.assertEqual(reader.search(JOB, canonical, impact="editorial")["total_matches"], 0)

    def test_expansion_and_priority_follow_the_cursor(self):
        canonical = _canonical()
        first = reader.search(JOB, canonical, kind="change", priority="normal", expand_blocks=True, limit=2)
        self.assertEqual(first["total_matches"], 4)
        second = reader.search(JOB, canonical, cursor=first["next_cursor"])
        self.assertEqual([row["record_id"] for row in second["returned"]], ["CHANGE-000003", "CHANGE-000004"])
        self.assertTrue(second["expand_blocks"])
        self.assertEqual(second["priority"], "normal")
        with self.assertRaises(reader.ReaderError) as error:
            reader.search(JOB, canonical, cursor=first["next_cursor"], priority="high")
        self.assertEqual(error.exception.code, "stale_cursor")

    def test_details_bounds_and_pages_through_block_members(self):
        canonical = _canonical(reader.MAX_METADATA_ITEMS + 1)
        first = reader.details(JOB, canonical, kind="block", record_id=BLOCK_ID)["block"]
        self.assertTrue(first["change_ids_truncated"])
        self.assertEqual(len(first["change_ids"]), reader.MAX_METADATA_ITEMS)
        self.assertEqual(len(first["members"]), reader.MAX_CONTEXT_SOURCES)
        second = reader.details(
            JOB, canonical, kind="block", record_id=BLOCK_ID,
            context_source_offset=first["next_source_offset"], text_limit=12,
        )["block"]
        self.assertEqual(second["members"][0]["change_id"], "CHANGE-000004")
        self.assertEqual(second["members"][0]["text"], "Aufbewahrung")
        self.assertEqual(second["members"][0]["text_next_offset"], 12)

    def test_legacy_result_still_returns_individual_changes(self):
        legacy = _canonical()
        legacy.pop("change_blocks")
        for change in legacy["changes"]:
            change.pop("block_id")
        result = reader.search(JOB, legacy)
        self.assertEqual(result["total_matches"], 4)
        self.assertEqual({row["kind"] for row in result["returned"]}, {"change"})
        self.assertEqual(result["returned"], reader.search(JOB, legacy, expand_blocks=True)["returned"])

    def test_block_and_group_review_locations_respect_member_ids(self):
        canonical = _canonical()
        elements = [
            _element(c["old"]["element_id"], [_rect(c["old"]["primary_page"], 1, 2*i, 3, 2*i+1)],
                     c["old"]["primary_page"])
            for i, c in enumerate(canonical["changes"], 1)
        ]
        canonical["review_queue"] = [{
            "review_id": "REVIEW-000001", "source_kind": "change_block", "block_id": BLOCK_ID,
            "change_ids": canonical["change_blocks"][0]["change_ids"][:2],
        }]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _write_docs(out, {"pages": [_page(1), _page(2)], "elements": elements}, None)
            block = resolve_locations(out, canonical, "block", BLOCK_ID)
            self.assertEqual([p["page_number"] for p in block["sides"]["old"]["regions"]], [1, 2, 2, 2])
            self.assertEqual(block["sides"]["new"]["counterpart"], "none")
            review = resolve_locations(out, canonical, "review", "REVIEW-000001")
            self.assertEqual(len(review["sides"]["old"]["regions"]), 2)
            grouped = build_outline(out, canonical)
            self.assertEqual(grouped["total"], 1)
            self.assertEqual(grouped["unplaced"][0]["record_id"], BLOCK_ID)
            self.assertEqual(grouped["unplaced"][0]["review_ids"], ["REVIEW-000001"])
            self.assertEqual(build_outline(out, canonical, expand_blocks=True)["total"], 4)
        review = reader.details(JOB, canonical, kind="review", record_id="REVIEW-000001")["review"]
        self.assertEqual(review["linked_refs"][0]["record_id"], BLOCK_ID)
        self.assertEqual(len(review["change_ids"]), 2)


class BlockIntegrationTests(unittest.TestCase):
    def test_http_mcp_and_agent_forward_expansion_and_priority(self):
        from unittest.mock import Mock, patch

        from fastapi.testclient import TestClient

        from document_compare.phase2.agent.gateway import StdioReaderGateway
        from document_compare.phase2.agent.tools import build_tools
        from document_compare.phase2.api import service
        from document_compare.phase2.mcp import reader_server

        client = TestClient(service.app)
        canonical = _canonical()
        path = f"/jobs/{JOB}/read"
        with (
            patch.object(service, "_read_status", return_value={"state": "done"}),
            patch.object(service, "_reader_canonical", return_value=canonical),
            patch.object(service, "_structured_elements", return_value=[]),
        ):
            def http(method, route, **params):
                response = client.request(method, route, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            grouped = http("GET", f"{path}/search", kind="block", priority="normal")
            self.assertEqual(grouped["returned"][0]["record_id"], BLOCK_ID)
            payload = http("GET", f"{path}/details", kind="block", record_id=BLOCK_ID)
            self.assertEqual(payload["block"]["next_source_offset"], reader.MAX_CONTEXT_SOURCES)
            with patch.object(reader_server, "_get", side_effect=lambda route, params: http(
                "GET", route, **{k: v for k, v in params.items() if v is not None},
            )):
                gateway = StdioReaderGateway(Mock(), Mock())
                with patch.object(gateway, "_call_sync", side_effect=lambda name, args: getattr(
                    reader_server, name,
                )(**args)):
                    search_tool = build_tools(gateway)["search"]
                    result = search_tool.invoke({"job_id": JOB, "expand_blocks": True, "priority": "normal"})
            self.assertEqual(result["total_matches"], 4)
            self.assertEqual({hit["kind"] for hit in result["returned"]}, {"change"})

    def test_streamlit_block_expander_keeps_outline_grouped(self):
        from tests.phase2.test_ui_smoke import make_app, open_job, structure_text
        from tests.phase2.ui_stub import StubClient

        class BlockClient(StubClient):
            def outline(self, job_id, *, expand_blocks=False):
                self.calls.append(("outline", job_id, expand_blocks))
                return build_outline(Path(folder), canonical, expand_blocks=expand_blocks)

            def details(self, job_id, kind, record_id, **options):
                return reader.details(JOB, canonical, kind=kind, record_id=record_id, **options)

        canonical = _canonical()
        with tempfile.TemporaryDirectory() as folder:
            client = BlockClient()
            app = open_job(make_app(client))
            self.assertFalse(app.exception)
            self.assertIn("Abschnitt gelöscht", structure_text(app))
            self.assertIn("Geltungsbereich", structure_text(app))
            self.assertEqual(app.main.expander[0].label, "Änderungen im Block")
            self.assertIn("Meldung 3", structure_text(app))
            self.assertEqual(app.session_state["selected"]["kind"], "block")
            self.assertTrue(any(call == ("outline", "job1", False) for call in client.calls))
            app.main.button(key=f"block_members::job1::{BLOCK_ID}::next").click().run()
            self.assertFalse(app.exception)
            self.assertIn("Meldung 4", structure_text(app))
            self.assertTrue(all(c[0] != "outline" or c[2] is False for c in client.calls))
