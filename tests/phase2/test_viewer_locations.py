"""Tests for viewer finding-location resolution (synthetic data, no network)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from document_compare.phase2.agent import mock_reader
from document_compare.phase2.reader.result_reader import ReaderError
from document_compare.phase2.viewer.locations import resolve_locations
from document_compare.phase2.viewer.synthetic import seed_demo_job


def _page(number: int, width: float = 10.0, height: float = 20.0) -> dict:
    return {"page_number": number, "width": width, "height": height, "unit": "inch"}


def _element(element_id: str, regions: list, page: int | None, table_id: str | None = None) -> dict:
    return {
        "element_id": element_id,
        "bounding_regions": regions,
        "primary_page": page,
        "primary_polygon": regions[0]["polygon"] if regions else None,
        "represented_by_table_id": table_id,
    }


def _rect(page: int, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"page_number": page, "polygon": [x0, y0, x1, y0, x1, y1, x0, y1]}


def _write_docs(out: Path, old: dict | None, new: dict | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for side, doc in (("old", old), ("new", new)):
        if doc is not None:
            (out / f"{side}_document.json").write_text(json.dumps({"document": doc}), encoding="utf-8")


class SeededLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        root = Path(cls._tmp.name)
        job_id = seed_demo_job(root)
        cls.out = root / job_id / "output"
        cls.canonical = json.loads((cls.out / "comparison_result.json").read_text(encoding="utf-8"))

    def test_modified_has_polygons_on_both_sides(self) -> None:
        result = resolve_locations(self.out, self.canonical, "change", "CHANGE-000001")
        self.assertEqual(result["kind"], "change")
        self.assertEqual(result["record_id"], "CHANGE-000001")
        self.assertEqual(result["change_type"], "MODIFIED")
        for side in ("old", "new"):
            info = result["sides"][side]
            self.assertEqual(info["counterpart"], "present")
            self.assertEqual(info["location"], "polygon")
            self.assertEqual(info["primary_page"], 3)
            self.assertEqual(info["page_count"], 3)
            self.assertTrue(info["regions"])
            for region in info["regions"]:
                self.assertEqual(len(region["polygon"]), 8)
                self.assertTrue(all(0.0 <= v <= 1.0 for v in region["polygon"]))

    def test_added_has_no_old_counterpart(self) -> None:
        result = resolve_locations(self.out, self.canonical, "change", "CHANGE-000003")
        self.assertEqual(result["sides"]["old"]["counterpart"], "none")
        self.assertEqual(result["sides"]["old"]["regions"], [])
        self.assertEqual(result["sides"]["new"]["location"], "polygon")

    def test_deleted_has_no_new_counterpart(self) -> None:
        result = resolve_locations(self.out, self.canonical, "change", "CHANGE-000002")
        self.assertEqual(result["sides"]["new"]["counterpart"], "none")
        self.assertEqual(result["sides"]["new"]["regions"], [])
        self.assertEqual(result["sides"]["old"]["counterpart"], "present")

    def test_empty_dict_and_null_element_id_count_as_none(self) -> None:
        for missing in ({}, {"element_id": None, "primary_page": None}):
            canonical = copy.deepcopy(self.canonical)
            canonical["changes"][2]["old"] = missing
            result = resolve_locations(self.out, canonical, "change", "CHANGE-000003")
            self.assertEqual(result["sides"]["old"]["counterpart"], "none")

    def test_review_record_is_unavailable(self) -> None:
        canonical = {"review_queue": [{"review_id": "REVIEW-1"}]}
        result = resolve_locations(self.out, canonical, "review", "REVIEW-1")
        for side in ("old", "new"):
            self.assertEqual(result["sides"][side]["location"], "unavailable")
            self.assertEqual(result["sides"][side]["regions"], [])

    def test_unknown_record_is_not_found(self) -> None:
        with self.assertRaises(ReaderError) as ctx:
            resolve_locations(self.out, self.canonical, "change", "CHANGE-999999")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_unknown_kind_is_invalid(self) -> None:
        with self.assertRaises(ReaderError) as ctx:
            resolve_locations(self.out, self.canonical, "bogus", "CHANGE-000001")
        self.assertEqual(ctx.exception.code, "invalid_arguments")

    def test_missing_document_json_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = resolve_locations(out, self.canonical, "change", "CHANGE-000001")
            for side in ("old", "new"):
                self.assertEqual(result["sides"][side]["location"], "unavailable")
                self.assertEqual(result["sides"][side]["regions"], [])
                self.assertEqual(result["sides"][side]["counterpart"], "present")


class HandBuiltLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    @staticmethod
    def _canonical(old_id: str | None, new_id: str | None) -> dict:
        def side(eid: str | None, page: int) -> dict | None:
            return None if eid is None else {"element_id": eid, "primary_page": page}

        return {
            "changes": [
                {"change_id": "C1", "change_type": "MODIFIED", "old": side(old_id, 2), "new": side(new_id, 2)}
            ]
        }

    def test_multi_region_element_returns_every_region(self) -> None:
        pages = [_page(1), _page(2), _page(3)]
        regions = [_rect(2, 1, 2, 5, 4), _rect(3, 0, 0, 10, 20)]
        doc = {"page_count": 3, "pages": pages, "elements": [_element("E1", regions, 2)]}
        _write_docs(self.out, doc, doc)
        result = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")
        info = result["sides"]["old"]
        self.assertEqual([r["page_number"] for r in info["regions"]], [2, 3])
        self.assertEqual(info["regions"][0]["polygon"], [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2])
        self.assertEqual(info["regions"][1]["polygon"], [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0])

    def test_values_are_clamped_to_unit_range(self) -> None:
        doc = {"page_count": 1, "pages": [_page(1)], "elements": [_element("E1", [_rect(1, -1, -1, 50, 50)], 1)]}
        _write_docs(self.out, doc, doc)
        info = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")["sides"]["old"]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in info["regions"][0]["polygon"]))

    def test_missing_element_is_unavailable_not_guessed(self) -> None:
        doc = {"page_count": 3, "pages": [_page(2)], "elements": [_element("OTHER", [_rect(2, 1, 1, 2, 2)], 2)]}
        _write_docs(self.out, doc, doc)
        info = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")["sides"]["old"]
        self.assertEqual(info["location"], "unavailable")
        self.assertEqual(info["regions"], [])
        self.assertEqual(info["counterpart"], "present")

    def test_zero_page_size_is_unavailable(self) -> None:
        doc = {
            "page_count": 1,
            "pages": [_page(1, width=0, height=20)],
            "elements": [_element("E1", [_rect(1, 1, 1, 2, 2)], 1)],
        }
        _write_docs(self.out, doc, doc)
        info = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")["sides"]["old"]
        self.assertEqual(info["location"], "unavailable")
        self.assertEqual(info["regions"], [])

    def test_region_on_page_missing_from_pdf_is_dropped(self) -> None:
        doc = {"page_count": 1, "pages": [_page(1)], "elements": [_element("E1", [_rect(7, 1, 1, 2, 2)], 7)]}
        _write_docs(self.out, doc, doc)
        info = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")["sides"]["old"]
        self.assertEqual(info["regions"], [])
        self.assertEqual(info["location"], "unavailable")

    def test_element_without_polygon_is_page_only(self) -> None:
        doc = {"page_count": 3, "pages": [_page(1), _page(2)], "elements": [_element("E1", [], 2)]}
        _write_docs(self.out, doc, doc)
        info = resolve_locations(self.out, self._canonical("E1", "E1"), "change", "C1")["sides"]["old"]
        self.assertEqual(info["location"], "page_only")
        self.assertEqual(info["primary_page"], 2)
        self.assertEqual(info["regions"], [])

    def test_path_escape_is_not_followed(self) -> None:
        outer = Path(self._tmp.name)
        out = outer / "job" / "output"
        out.mkdir(parents=True)
        real = outer / "elsewhere.json"
        real.write_text(json.dumps({"document": {"pages": [_page(1)], "elements": []}}), encoding="utf-8")
        (out / "old_document.json").symlink_to(real)
        result = resolve_locations(out, self._canonical("E1", None), "change", "C1")
        self.assertEqual(result["sides"]["old"]["location"], "unavailable")
        self.assertIsNone(result["sides"]["old"]["page_count"])


class TableLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.canonical = mock_reader.canonical_main()
        self.table_id = self.canonical["table_changes"][0]["table_change_id"]

    def _docs(self, old_elements: list, new_elements: list) -> None:
        pages = [_page(1), _page(2), _page(3)]
        _write_docs(
            self.out,
            {"page_count": 3, "pages": pages, "elements": old_elements},
            {"page_count": 3, "pages": pages, "elements": new_elements},
        )

    def test_table_cell_without_polygon_is_page_only(self) -> None:
        self._docs([], [])
        result = resolve_locations(self.out, self.canonical, "table", self.table_id)
        self.assertEqual(result["kind"], "table")
        for side in ("old", "new"):
            info = result["sides"][side]
            self.assertEqual(info["counterpart"], "present")
            self.assertEqual(info["location"], "page_only")
            self.assertEqual(info["primary_page"], 3)
            self.assertEqual(info["regions"], [])

    def test_table_cell_falls_back_to_table_element(self) -> None:
        old = [
            _element("OLD-TBL", [_rect(3, 1, 2, 9, 10), _rect(2, 0, 0, 1, 1)], 3, table_id="OLD-T1"),
            _element("OLD-X", [_rect(3, 5, 5, 6, 6)], 3, table_id="OTHER"),
        ]
        self._docs(old, [])
        result = resolve_locations(self.out, self.canonical, "table", self.table_id)
        info = result["sides"]["old"]
        self.assertEqual(info["location"], "polygon")
        self.assertEqual([r["page_number"] for r in info["regions"]], [3])
        self.assertEqual(result["sides"]["new"]["location"], "page_only")

    def test_cell_polygon_is_used_when_present(self) -> None:
        self.canonical["table_changes"][0]["cell_changes"][-1]["old"]["primary_polygon"] = [1, 2, 5, 2, 5, 4, 1, 4]
        self._docs([], [])
        info = resolve_locations(self.out, self.canonical, "table", self.table_id)["sides"]["old"]
        self.assertEqual(info["location"], "polygon")
        self.assertEqual(info["regions"][0]["polygon"], [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2])


if __name__ == "__main__":
    unittest.main()
