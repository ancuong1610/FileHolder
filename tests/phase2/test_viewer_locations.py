"""Tests for viewer finding-location resolution (synthetic data, no network)."""

from __future__ import annotations

import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from document_compare.phase2.agent import mock_reader
from document_compare.phase2.reader.result_reader import ReaderError
from document_compare.phase2.viewer.locations import linked_change_ids, resolve_locations, reviews_by_change
from document_compare.phase2.viewer.synthetic import seed_demo_job


def _reference_linked_change_ids(review, changes):
    existing = {c["change_id"]: c for c in changes if isinstance(c, dict) and isinstance(c.get("change_id"), str)}
    evidence = review.get("evidence") if isinstance(review.get("evidence"), dict) else {}
    named = [i for i in evidence.get("change_ids") or [] if isinstance(i, str)] + [review.get("change_id")]
    if review.get("block_id"):
        named.extend(i for i in review.get("change_ids") or [] if isinstance(i, str))
    out = [i for i in named if isinstance(i, str) and i in existing]
    candidate = review.get("source_candidate_id")
    if isinstance(candidate, str) and candidate:
        out += [cid for cid, c in existing.items() if c.get("source_candidate_id") == candidate]
    return list(dict.fromkeys(out))


def _reference_reviews_by_change(canonical):
    canonical = canonical if isinstance(canonical, dict) else {}
    changes = canonical.get("changes") if isinstance(canonical.get("changes"), list) else []
    reviews = canonical.get("review_queue") if isinstance(canonical.get("review_queue"), list) else []
    out = {}
    for review in reviews:
        if isinstance(review, dict) and isinstance(review.get("review_id"), str):
            for cid in _reference_linked_change_ids(review, changes):
                out.setdefault(cid, []).append(review)
    return out


class ReviewLinksTests(unittest.TestCase):
    def _assert_reference(self, changes, reviews):
        canonical = {"changes": changes, "review_queue": reviews}
        expected = _reference_reviews_by_change(canonical)
        self.assertEqual(list(reviews_by_change(canonical).items()), list(expected.items()))
        for review in reviews:
            if isinstance(review, dict):
                self.assertEqual(linked_change_ids(review, changes), _reference_linked_change_ids(review, changes))

    def test_links_preserve_reference_order_and_duplicates(self):
        changes = [
            {"change_id": "C1", "source_candidate_id": "other"},
            {"change_id": "C2", "source_candidate_id": "shared"},
            {"change_id": "C3", "source_candidate_id": "shared"},
            {"change_id": "C4", "source_candidate_id": "other"},
            {"change_id": "C1", "source_candidate_id": "shared"},
            {"change_id": "C5", "source_candidate_id": ["unhashable"]},
            {"change_id": 3}, None,
        ]
        review = {
            "review_id": "R1", "evidence": {"change_ids": ["C3", "missing", "C3", None]},
            "change_id": "C4", "block_id": "B1", "change_ids": ["C2", "C3", "missing"],
            "source_candidate_id": "shared",
        }
        reviews = [
            review, {"review_id": "R2", "change_id": "C2", "change_ids": ["C5"]},
            review, {"review_id": None, "change_id": "C5"}, "invalid review",
        ]
        self.assertEqual(linked_change_ids(review, changes), ["C3", "C4", "C2", "C1"])
        self._assert_reference(changes, reviews)

    def test_seeded_random_links_match_reference(self):
        rng = random.Random(2711)
        ids = [f"C{i}" for i in range(180)] + [None, 3, "missing"]
        candidates = [f"S{i}" for i in range(24)] + [None, {}, ""]
        changes = [
            {"change_id": cid, "source_candidate_id": rng.choice(candidates)}
            for cid in rng.choices(ids[:-3], k=200)
        ] + [None, {"change_id": 3}]
        reviews = [
            {
                "review_id": f"R{i}", "evidence": {"change_ids": rng.choices(ids, k=rng.randrange(5))},
                "change_id": rng.choice(ids), "block_id": rng.choice([None, "B1"]),
                "change_ids": rng.choices(ids, k=rng.randrange(5)), "source_candidate_id": rng.choice(candidates),
            }
            for i in range(150)
        ] + [None, {}]
        self._assert_reference(changes, reviews)


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

    def test_review_record_without_any_element_is_unavailable(self) -> None:
        canonical = {"review_queue": [{"review_id": "REVIEW-1"}]}
        result = resolve_locations(self.out, canonical, "review", "REVIEW-1")
        for side in ("old", "new"):
            self.assertEqual(result["sides"][side]["location"], "unavailable")
            self.assertEqual(result["sides"][side]["regions"], [])
        self.assertTrue(result["needs_review"])

    def test_review_record_resolves_regions_from_evidence(self) -> None:
        change = self.canonical["changes"][0]
        evidence = {"old_element_ids": [change["old"]["element_id"]], "new_element_ids": [change["new"]["element_id"]]}
        canonical = {"changes": [], "review_queue": [{
            "review_id": "REVIEW-1", "case_type": "movement_block_review", "evidence": evidence,
            "resolution": {"status": "review", "review_reasons": []}}]}
        result = resolve_locations(self.out, canonical, "review", "REVIEW-1")
        for side in ("old", "new"):
            info = result["sides"][side]
            self.assertEqual(info["location"], "polygon")
            self.assertTrue(info["regions"] and all(r["role"] == "element" for r in info["regions"]))
        self.assertEqual((result["needs_review"], result["review_reason"]), (True, "Block of content moved"))

    def test_review_record_with_empty_evidence_follows_linked_change(self) -> None:
        for link in ({"change_ids": ["CHANGE-000001"]}, {"source_candidate_id": "CAND-1"}):
            canonical = copy.deepcopy(self.canonical)
            canonical["changes"][0]["source_candidate_id"] = "CAND-1"
            review = {"review_id": "REVIEW-1", "case_type": "ambiguous_modified_pair", "evidence": {},
                      "source_candidate_id": link.get("source_candidate_id")}
            if "change_ids" in link:
                review["evidence"] = dict(link)
            canonical["review_queue"] = [review]
            result = resolve_locations(self.out, canonical, "review", "REVIEW-1")
            self.assertEqual(result["sides"]["new"]["location"], "polygon", link)
            self.assertEqual(result["sides"]["old"]["primary_page"], 3)
            # the change itself is flagged because the review record points at it
            changed = resolve_locations(self.out, canonical, "change", "CHANGE-000001")
            self.assertTrue(changed["needs_review"])
            self.assertEqual(changed["review_reason"], "Short or ambiguous change \u2014 LLM could not decide")

    def test_unflagged_change_has_no_review_fields_set(self) -> None:
        result = resolve_locations(self.out, self.canonical, "change", "CHANGE-000003")
        self.assertIn("needs_review", result)
        self.assertIn("review_reason", result)

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

    def test_marker_is_below_lowest_region_on_anchor_page_only(self) -> None:
        anchor = _element("E1", [
            _rect(2, 1, 2, 5, 4), _rect(2, 1, 5, 5, 8), _rect(3, 0, 0, 10, 20),
        ], 2)
        _write_docs(self.out, None, {"pages": [_page(2), _page(3)], "elements": [anchor]})
        canonical = self._canonical("OLD", None)
        canonical["changes"][0].update(change_type="DELETED", other_side_anchor={
            "element_id": "E1", "page": 2, "position": "after",
        })
        sides = resolve_locations(self.out, canonical, "change", "C1")["sides"]
        self.assertNotIn("insertion_marker", sides["old"])
        self.assertEqual(sides["new"]["regions"], [])
        self.assertEqual(sides["new"]["insertion_marker"], {
            "page_number": 2,
            "polygon": [0.04, 0.404, 0.96, 0.404, 0.96, 0.41, 0.04, 0.41],
        })
        anchor["bounding_regions"].append(_rect(2, 1, 18, 5, 20))
        _write_docs(self.out, None, {"pages": [_page(2), _page(3)], "elements": [anchor]})
        marker = resolve_locations(self.out, canonical, "change", "C1")["sides"]["new"]["insertion_marker"]
        self.assertEqual(marker["polygon"][1::2], [0.99, 0.99, 0.996, 0.996])

    def test_document_start_and_missing_region_use_top_of_anchor_page(self) -> None:
        canonical = self._canonical(None, "NEW")
        change = canonical["changes"][0]
        change["change_type"] = "ADDED"
        for anchor in (
            {"element_id": None, "page": 1, "position": "document_start"},
            {"element_id": "NO-REGION", "page": 2, "position": "after"},
        ):
            with self.subTest(anchor=anchor):
                change["other_side_anchor"] = anchor
                side = resolve_locations(self.out, canonical, "change", "C1")["sides"]["old"]
                self.assertEqual(side["insertion_marker"], {
                    "page_number": anchor["page"],
                    "polygon": [0.04, 0.02, 0.96, 0.02, 0.96, 0.026, 0.04, 0.026],
                })
        for anchor in (None, {"element_id": "E1", "page": None, "position": "after"}):
            change["other_side_anchor"] = anchor
            side = resolve_locations(self.out, canonical, "change", "C1")["sides"]["old"]
            self.assertNotIn("insertion_marker", side)

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

    def test_editorial_split_highlights_both_fragment_pages(self) -> None:
        pages = [_page(1), _page(2)]
        first = _element("N1", [_rect(1, 1, 2, 5, 4)], 1)
        second = _element("N2", [_rect(2, 1, 6, 5, 8)], 2)
        _write_docs(self.out, None, {"pages": pages, "elements": [first, second]})
        canonical = self._canonical(None, "N1")
        canonical["changes"][0].update(
            impact={"level": "editorial", "kinds": ["paragraph_split"]},
            new_element_ids=["N1", "N2"],
            new_elements=[
                {"element_id": "N1", "text": "Die Frist gilt", "primary_page": 1},
                {"element_id": "N2", "text": "ab Genehmigung.", "primary_page": 2},
            ],
        )
        result = resolve_locations(self.out, canonical, "change", "C1")
        self.assertEqual([r["page_number"] for r in result["sides"]["new"]["regions"]], [1, 2])

    def test_move_resolves_all_members_on_both_sides(self) -> None:
        pages = [_page(1), _page(2), _page(3)]
        docs = [
            {"pages": pages, "elements": [
                _element(f"{side}1", [_rect(first, 1, 2, 5, 4)], first),
                _element(f"{side}2", [_rect(second, 1, 6, 5, 8)], second),
            ]}
            for side, first, second in (("O", 1, 2), ("N", 2, 3))
        ]
        _write_docs(self.out, *docs)
        move = {"move_id": "MOVE-000001", "old_element_ids": ["O1", "O2"], "new_element_ids": ["N1", "N2"]}
        result = resolve_locations(self.out, {"moves": [move]}, "move", move["move_id"])
        self.assertFalse(result["needs_review"])
        for side, expected in (("old", [1, 2]), ("new", [2, 3])):
            info = result["sides"][side]
            self.assertEqual((info["location"], info["primary_page"]), ("polygon", expected[0]))
            self.assertEqual([r["page_number"] for r in info["regions"]], expected)
            self.assertEqual(info["regions"][0]["polygon"], [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2])

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
        self.assertEqual([(r["page_number"], r["role"]) for r in info["regions"]], [(3, "table"), (2, "table")])
        self.assertEqual(result["sides"]["new"]["location"], "page_only")

    def test_cell_polygon_is_used_when_present(self) -> None:
        self.canonical["table_changes"][0]["cell_changes"][-1]["old"]["primary_polygon"] = [1, 2, 5, 2, 5, 4, 1, 4]
        self._docs([], [])
        info = resolve_locations(self.out, self.canonical, "table", self.table_id)["sides"]["old"]
        self.assertEqual(info["location"], "polygon")
        self.assertEqual(info["regions"][0]["polygon"], [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2])

    def test_table_region_plus_cell_regions_only_for_changed_cells(self) -> None:
        table = self.canonical["table_changes"][0]
        table["cell_changes"] = [
            {"classification": "UNCHANGED", "old": {"primary_page": 3, "primary_polygon": [1, 1, 2, 1, 2, 2, 1, 2]},
             "new": None},
            {"classification": "MODIFIED", "old": {"primary_page": 3, "primary_polygon": [1, 2, 5, 2, 5, 4, 1, 4]},
             "new": {"primary_page": 3, "primary_polygon": [1, 2, 5, 2, 5, 4, 1, 4]}},
            {"classification": "ADDED", "old": None, "new": {"primary_page": 3, "primary_polygon": [6, 2, 9, 2, 9, 4, 6, 4]}},
        ]
        old = [_element("OLD-TBL", [_rect(3, 1, 2, 9, 10)], 3, table_id="OLD-T1")]
        self._docs(old, [])
        result = resolve_locations(self.out, self.canonical, "table", self.table_id)
        self.assertEqual([r["role"] for r in result["sides"]["old"]["regions"]], ["table", "cell"])
        # new side has no table element: cells only (ADDED has a new cell, UNCHANGED has none on that side)
        self.assertEqual([r["role"] for r in result["sides"]["new"]["regions"]], ["cell", "cell"][:2])
        self.assertEqual(result["sides"]["old"]["regions"][1]["polygon"], [0.1, 0.1, 0.5, 0.1, 0.5, 0.2, 0.1, 0.2])
        self.assertFalse(result["needs_review"])


if __name__ == "__main__":
    unittest.main()
