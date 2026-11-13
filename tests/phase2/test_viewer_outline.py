"""Tests for the section outline (offline, synthetic data)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from document_compare.phase2.reader.result_reader import ReaderError
from document_compare.phase2.viewer.outline import build_outline
from document_compare.phase2.viewer.synthetic import seed_demo_job


def _section(sid, title, level=None, parent=None, known=False):
    return {"section_id": sid, "title": title, "level": level, "parent_section_id": parent, "parent_known": known}


def _heading_elements(sections, pages):
    """Heading elements for sections, page given per section id."""
    return [{"element_id": f"H-{s['section_id']}", "primary_page": pages[s["section_id"]], "reading_order": 0}
            for s in sections if s["section_id"] in pages]


def _structured(sections, membership, elements=None, page_count=2):
    sections = [dict(s, heading_element_id=f"H-{s['section_id']}") for s in sections]
    return {"document": {
        "page_count": page_count, "pages": [], "elements": elements or [],
        "structure_evidence": {
            "sections": sections,
            "membership": {"by_element": {e: {"category": "section", "section_id": s} for e, s in membership.items()}},
        }}}


def _change(cid, ctype, old=None, new=None):
    def side(eid, text):
        return None if eid is None else {"element_id": eid, "text": text, "primary_page": 1, "reading_order": 1}
    return {"change_id": cid, "change_type": ctype, "old": side(old, f"old {cid}"), "new": side(new, f"new {cid}")}


def _write(out: Path, side: str, data) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{side}_document_structured.json").write_text(json.dumps(data), encoding="utf-8")


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


class SeededJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        job = root / seed_demo_job(root)
        cls.out = job / "output"
        cls.canonical = json.loads((cls.out / "comparison_result.json").read_text("utf-8"))
        cls.outline = build_outline(cls.out, cls.canonical)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_change_and_table_appears_exactly_once(self):
        placed = [i["record_id"] for n in _walk(self.outline["sections"]) for i in n["items"]]
        placed += [i["record_id"] for i in self.outline["unplaced"]]
        expected = [c["change_id"] for c in self.canonical["changes"]] + \
                   [t["table_change_id"] for t in self.canonical["table_changes"]]
        self.assertTrue(expected)
        self.assertEqual(sorted(placed), sorted(expected))

    def test_review_records_are_listed(self):
        ids = [i["record_id"] for i in self.outline["review"]]
        self.assertEqual(ids, [r["review_id"] for r in self.canonical["review_queue"]])
        self.assertTrue(ids)
        self.assertTrue(all(i["kind"] == "review" and i["label"] for i in self.outline["review"]))
        self.assertEqual(self.outline["total"], len(self.canonical["changes"]) + len(self.canonical["table_changes"])
                         + len(ids))

    def test_documents_pages_and_item_shape(self):
        self.assertEqual(self.outline["documents"], {"old": {"page_count": 3}, "new": {"page_count": 3}})
        item = next(i for n in _walk(self.outline["sections"]) for i in n["items"])
        self.assertEqual(set(item), {"kind", "record_id", "change_type", "label", "page_old", "page_new",
                                    "context_old", "context_new", "section_path"})
        self.assertTrue(item["section_path"])
        self.assertEqual([n["side"] for n in self.outline["sections"]], ["new"] * len(self.outline["sections"]))

    def test_deleted_uses_old_side_and_added_new_side(self):
        by_id = {i["record_id"]: (n["title"], i) for n in _walk(self.outline["sections"]) for i in n["items"]}
        title, deleted = by_id["CHANGE-000002"]
        self.assertEqual(deleted["change_type"], "DELETED")
        self.assertTrue(deleted["context_new"] and not deleted["context_old"])
        self.assertIsNotNone(deleted["page_new"])  # section context page on the new side
        self.assertEqual(deleted["section_path"][-1], title)
        added_title, added = by_id["CHANGE-000003"]
        self.assertEqual(added["change_type"], "ADDED")
        self.assertTrue(added["context_old"] and not added["context_new"])
        self.assertIsNotNone(added["page_old"])
        old_doc = json.loads((self.out / "old_document_structured.json").read_text("utf-8"))["document"]
        heading = next(sec["heading_element_id"] for sec in old_doc["structure_evidence"]["sections"]
                       if sec["title"] == added_title)
        expected = next(e["primary_page"] for e in old_doc["elements"] if e["element_id"] == heading)
        self.assertEqual(added["page_old"], expected)

    def test_no_structured_files_everything_unplaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_outline(Path(tmp), self.canonical)
        self.assertEqual(result["sections"], [])
        self.assertEqual(len(result["unplaced"]), len(self.canonical["changes"]))
        self.assertEqual(len(result["review"]), len(self.canonical["review_queue"]))
        self.assertEqual(result["documents"], {"old": {"page_count": None}, "new": {"page_count": None}})


class HandMadeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def test_nesting_rolls_counts_up(self):
        _write(self.out, "new", _structured(
            [_section("N1", "Part 1", 1), _section("N2", "Part 1.1", 2, "N1", True),
             _section("N3", "Part 1.1.1", 3, "N2", True), _section("N4", "Part 2", 1)],
            {"E1": "N1", "E2": "N3", "E3": "N3", "E4": "N4"}))
        canonical = {"changes": [_change("C1", "ADDED", new="E1"), _change("C2", "ADDED", new="E2"),
                                 _change("C3", "MODIFIED", old="X", new="E3")]}
        result = build_outline(self.out, canonical)
        top, part2 = result["sections"]
        self.assertEqual((top["title"], top["change_count"], len(top["items"])), ("Part 1", 3, 1))
        mid = top["children"][0]
        self.assertEqual((mid["title"], mid["change_count"], mid["items"]), ("Part 1.1", 2, []))
        leaf = mid["children"][0]
        self.assertEqual((leaf["change_count"], [i["record_id"] for i in leaf["items"]]), (2, ["C2", "C3"]))
        self.assertEqual(leaf["items"][0]["section_path"], ["Part 1", "Part 1.1", "Part 1.1.1"])
        self.assertEqual(part2["change_count"], 0)
        self.assertEqual(result["total"], 3)

    def test_unknown_parent_stays_flat_and_cycle_is_harmless(self):
        _write(self.out, "new", _structured(
            [_section("A", "A", 1, "B", True), _section("B", "B", 1, "A", True), _section("C", "C", 2, "A", False)], {}))
        result = build_outline(self.out, {})
        titles = sorted(n["title"] for n in _walk(result["sections"]))
        self.assertEqual(titles, ["A", "B", "C"])

    def test_old_only_section_and_title_merge(self):
        _write(self.out, "new", _structured([_section("N1", "Scope")], {"NE": "N1"}))
        _write(self.out, "old", _structured(
            [_section("O1", "  SCOPE \n "), _section("O2", "Retired clause")], {"OE1": "O1", "OE2": "O2"}))
        canonical = {"changes": [_change("C1", "DELETED", old="OE2"), _change("C2", "DELETED", old="OE1"),
                                 _change("C3", "ADDED", new="NE")]}
        result = build_outline(self.out, canonical)
        scope, retired = result["sections"]
        self.assertEqual((scope["side"], scope["change_count"]), ("new", 2))  # merged by normalized title
        self.assertEqual({i["record_id"] for i in scope["items"]}, {"C2", "C3"})
        self.assertEqual((retired["side"], retired["title"], retired["change_count"]), ("old", "Retired clause", 1))
        self.assertEqual(retired["items"][0]["section_path"], ["Retired clause"])

    def test_one_side_missing_still_builds_from_other(self):
        _write(self.out, "old", _structured([_section("O1", "Only old")], {"OE": "O1"}))
        canonical = {"changes": [_change("C1", "MODIFIED", old="OE", new="NE")]}
        result = build_outline(self.out, canonical)
        self.assertEqual([(n["title"], n["side"], n["change_count"]) for n in result["sections"]],
                         [("Only old", "old", 1)])
        self.assertEqual(result["unplaced"], [])

    def test_unknown_element_and_preamble_are_unplaced(self):
        _write(self.out, "new", _structured([_section("N1", "S")], {"E1": "N1"}))
        canonical = {"changes": [_change("C1", "ADDED", new="NOPE"), _change("C2", "ADDED", new=None)],
                     "review_queue": [{"review_id": "R1", "case_type": "unresolved_residual_counterpart"}]}
        result = build_outline(self.out, canonical)
        self.assertEqual([i["record_id"] for i in result["unplaced"]], ["C1", "C2"])
        self.assertEqual(result["sections"][0]["change_count"], 0)
        self.assertEqual(result["review"][0]["label"], "unresolved residual counterpart")

    def test_table_is_placed_through_its_table_element(self):
        elements = [{"element_id": "NT", "represented_by_table_id": "NEW-T1", "reading_order": 3}]
        _write(self.out, "new", _structured([_section("N1", "Tables")], {"NT": "N1"}, elements))
        table = {"table_change_id": "T1", "change_type": "TABLE_MODIFIED",
                 "old": {"logical_table_id": "OLD-T1", "header_signature": ["Item", "Qty"]},
                 "new": {"logical_table_id": "NEW-T1", "header_signature": ["Item", "Qty"]},
                 "cell_changes": [{"classification": "MODIFIED", "old": {"text": "3", "primary_page": 2},
                                   "new": {"text": "5", "primary_page": 2}}]}
        result = build_outline(self.out, {"table_changes": [table]})
        item = result["sections"][0]["items"][0]
        self.assertEqual((item["kind"], item["record_id"], item["page_old"], item["page_new"]), ("table", "T1", 2, 2))
        self.assertEqual(item["label"], "Table: 5")
        self.assertEqual(item["section_path"], ["Tables"])

    def test_label_collapses_whitespace_and_truncates(self):
        change = _change("C1", "ADDED", new="E")
        change["new"]["text"] = "word  \n  " + "x" * 200
        result = build_outline(self.out, {"changes": [change]})
        label = result["unplaced"][0]["label"]
        self.assertEqual(len(label), 91)
        self.assertTrue(label.startswith("word xxx") and label.endswith("…"))
        change["new"]["text"] = "short   text"
        self.assertEqual(build_outline(self.out, {"changes": [change]})["unplaced"][0]["label"], "short text")

    def test_unparsable_structured_json_raises(self):
        self.out.mkdir(exist_ok=True)
        (self.out / "new_document_structured.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ReaderError) as ctx:
            build_outline(self.out, {})
        self.assertEqual(ctx.exception.code, "invalid_artifact")

    def test_items_keep_reading_order_within_a_section(self):
        _write(self.out, "new", _structured([_section("N1", "S")], {"E1": "N1", "E2": "N1"}))
        first, second = _change("C1", "ADDED", new="E1"), _change("C2", "ADDED", new="E2")
        first["new"]["reading_order"], second["new"]["reading_order"] = 9, 2
        result = build_outline(self.out, {"changes": [first, second]})
        self.assertEqual([i["record_id"] for i in result["sections"][0]["items"]], ["C2", "C1"])


class ContextPageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _item(self, canonical, cid):
        result = build_outline(self.out, canonical)
        everything = [i for n in _walk(result["sections"]) for i in n["items"]] + result["unplaced"]
        return next(i for i in everything if i["record_id"] == cid)

    def _docs(self, new_sections, old_sections, new_pages, old_pages, membership_new, membership_old,
              new_count=9, old_count=9):
        _write(self.out, "new", _structured(new_sections, membership_new, _heading_elements(
            [dict(s, section_id=s["section_id"]) for s in new_sections], new_pages), new_count))
        _write(self.out, "old", _structured(old_sections, membership_old, _heading_elements(
            old_sections, old_pages), old_count))

    def test_added_gets_old_section_heading_page_and_deleted_mirrors(self):
        self._docs([_section("N1", "Scope")], [_section("O1", "scope")], {"N1": 5}, {"O1": 2},
                   {"NE": "N1"}, {"OE": "O1"})
        canonical = {"changes": [_change("C1", "ADDED", new="NE"), _change("C2", "DELETED", old="OE")]}
        added = self._item(canonical, "C1")
        self.assertEqual((added["page_old"], added["context_old"], added["context_new"]), (2, True, False))
        self.assertEqual(added["page_new"], 1)
        deleted = self._item(canonical, "C2")
        self.assertEqual((deleted["page_new"], deleted["context_new"], deleted["context_old"]), (5, True, False))

    def test_context_page_is_closest_page_inside_the_section_span(self):
        old_sections = [_section("O1", "Scope")]
        old_elements = _heading_elements(old_sections, {"O1": 1}) + [
            {"element_id": "O-body-2", "primary_page": 2, "reading_order": 1},
            {"element_id": "O-body-3", "primary_page": 3, "reading_order": 2}]
        _write(self.out, "old", _structured(old_sections, {"H-O1": "O1", "O-body-2": "O1", "O-body-3": "O1"},
                                            old_elements, 9))
        new_sections = [_section("N1", "Scope")]
        _write(self.out, "new", _structured(new_sections, {"NE": "N1"}, _heading_elements(new_sections, {"N1": 1}), 9))
        change = _change("C1", "ADDED", new="NE")
        change["new"]["primary_page"] = 3
        self.assertEqual((self._item({"changes": [change]}, "C1")["page_old"]), 3)  # span 1-3, change on p.3
        change["new"]["primary_page"] = 7
        self.assertEqual((self._item({"changes": [change]}, "C1")["page_old"]), 3)  # clamped to span end

    def test_section_only_on_one_side_uses_nearest_previous_section(self):
        self._docs([_section("N1", "Intro"), _section("N2", "Fresh"), _section("N3", "Outro")],
                   [_section("O1", "Intro"), _section("O2", "Outro")],
                   {"N1": 1, "N2": 3, "N3": 5}, {"O1": 2, "O2": 7},
                   {"NE": "N2"}, {})
        canonical = {"changes": [_change("C1", "ADDED", new="NE")]}
        item = self._item(canonical, "C1")
        self.assertEqual((item["page_old"], item["context_old"]), (2, True))  # Intro precedes Fresh

    def test_no_previous_section_clamps_other_page(self):
        self._docs([_section("N1", "Fresh")], [_section("O1", "Other")], {"N1": 4}, {}, {"NE": "N1"}, {},
                   old_count=3)
        canonical = {"changes": [_change("C1", "ADDED", new="NE")]}
        canonical["changes"][0]["new"]["primary_page"] = 8
        item = self._item(canonical, "C1")
        self.assertEqual((item["page_old"], item["context_old"]), (3, True))  # 8 clamped to 3 pages

    def test_no_sections_clamps_and_unknown_count_stays_none(self):
        _write(self.out, "old", _structured([], {}, None, 4))
        canonical = {"changes": [_change("C1", "ADDED", new="NE")]}
        canonical["changes"][0]["new"]["primary_page"] = 9
        item = self._item(canonical, "C1")
        self.assertEqual((item["page_old"], item["context_old"]), (4, True))
        self.assertEqual((item["page_new"], item["context_new"]), (9, False))
        with tempfile.TemporaryDirectory() as empty:
            result = build_outline(Path(empty), canonical)
        self.assertEqual((result["unplaced"][0]["page_old"], result["unplaced"][0]["context_old"]), (None, False))


if __name__ == "__main__":
    unittest.main()
