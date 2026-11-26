"""Synthetic whole-page coverage through the Stage 14 entry point."""
import json
import tempfile
import unittest
from pathlib import Path

from document_compare.phase1.matching.impact import IMPACT_VERSION
from tests.phase1.test_automatic_reports import _empty_tables
from tests.phase1.test_change_blocks import _body, _candidate, _compare


def _on_page(element, page):
    return dict(element, primary_page=page)


def _candidates(side, page, orders):
    prefix = "D" if side == "old" else "A"
    candidates = [_candidate(prefix, side, order) for order in orders]
    for candidate in candidates:
        candidate[side]["primary_page"] = page
    return candidates


def _write_structure(folder, side, elements, membership=None):
    path = folder / f"{side}_document_structured.json"
    path.write_text(json.dumps({"document": {
        "elements": elements,
        "structure_evidence": {"membership": {"by_element": membership or {}}},
    }}), encoding="utf-8")


class PageChangeTests(unittest.TestCase):
    def test_deleted_page_keeps_all_four_change_ids_and_the_review_queue(self):
        deleted = _candidates("old", 34, range(1, 5))
        result = _compare(deleted)
        page, = result["page_changes"]
        block, = result["change_blocks"]
        self.assertEqual(page, {
            "page_change_id": "PAGE-000001",
            "side": "old",
            "change_type": "DELETED",
            "pages": [34],
            "change_ids": [f"CHANGE-{i:06d}" for i in range(1, 5)],
            "table_change_ids": [],
            "block_ids": [block["block_id"]],
            "element_count": 4,
            "anchor": None,
            "other_side_anchor_page": None,
            "other_side_anchor_element_id": None,
            "impact": {
                "version": IMPACT_VERSION, "level": "content", "priority": "normal",
                "kinds": ["page_deleted"], "signals": [], "basis": "deterministic_rule",
                "language_profile": "de-en",
            },
        })
        self.assertEqual(result["statistics"]["page_changes"], 1)
        self.assertIsNone(block["other_side_anchor"])
        self.assertTrue(all(c["other_side_anchor"] is None for c in result["changes"]))
        self.assertEqual([item["change_id"] for item in result["changes"]], page["change_ids"])
        self.assertEqual({item["page_change_id"] for item in result["changes"]}, {page["page_change_id"]})
        self.assertEqual(block["page_change_id"], page["page_change_id"])
        self.assertEqual(block["full_pages"], [34])
        review, = result["review_queue"]
        self.assertEqual(review["source_kind"], "change_block")
        self.assertEqual(review["change_ids"], page["change_ids"])
        self.assertNotIn("page_change_id", review)

    def test_one_surviving_matched_paragraph_disqualifies_the_page(self):
        deleted = _candidates("old", 34, range(1, 5))
        survivor = {"old": _on_page(_body("old", 5), 34), "new": _body("new", 5)}
        result = _compare(deleted, unchanged=[survivor])
        self.assertEqual(result["page_changes"], [])
        self.assertEqual(result["statistics"]["page_changes"], 0)
        self.assertEqual(result["change_blocks"][0]["full_pages"], [])
        self.assertNotIn("page_change_id", result["change_blocks"][0])

    def test_layout_and_furniture_are_skipped_but_toc_elements_count(self):
        deleted = _candidates("old", 34, range(2, 6))
        body = [candidate["old"] for candidate in deleted]
        furniture = [
            _on_page(_body("old", 1, "header"), 34),
            _on_page(_body("old", 6, "footer"), 34),
            _on_page(_body("old", 7, "heading"), 34),
            _on_page(_body("old", 8), 34),
            _on_page(_body("old", 9, "header"), 35),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write_structure(folder, "old", body + furniture, {
                body[0]["element_id"]: {"category": "toc"},
                furniture[2]["element_id"]: {"category": "layout"},
                furniture[3]["element_id"]: {"category": "layout"},
            })
            result = _compare(deleted, structure_dir=folder)
            page, = result["page_changes"]
            self.assertEqual(page["pages"], [34])
            self.assertEqual(page["element_count"], 4)
            toc_match = {"old": body[0], "new": _body("new", 2)}
            result = _compare(deleted[1:], unchanged=[toc_match], structure_dir=folder)
            self.assertEqual(result["page_changes"], [])

    def test_consecutive_pages_form_one_record_and_a_gap_starts_another(self):
        deleted = (_candidates("old", 34, range(1, 3)) + _candidates("old", 35, range(3, 5))
                   + _candidates("old", 37, range(5, 7)))
        result = _compare(deleted)
        first, second = result["page_changes"]
        self.assertEqual((first["pages"], second["pages"]), ([34, 35], [37]))
        self.assertEqual((first["element_count"], second["element_count"]), (4, 2))
        block, = result["change_blocks"]
        self.assertEqual(block["full_pages"], [34, 35, 37])
        self.assertNotIn("page_change_id", block)

    def test_added_new_page_uses_the_nearest_preceding_match_and_heading(self):
        heading = {
            "new": dict(_on_page(_body("new", 1, "heading"), 32), text="Geltungsbereich"),
            "old": _on_page(_body("old", 1, "heading"), 19),
        }
        modified = {
            "candidate_id": "MODIFIED-1", "old": _on_page(_body("old", 2), 22),
            "new": _on_page(_body("new", 2), 33),
        }
        later_match = {"new": _on_page(_body("new", 6), 35), "old": _on_page(_body("old", 6), 23)}
        added = _candidates("new", 34, range(3, 6))
        result = _compare(added=added, unchanged=[heading, later_match], modified=[modified])
        page, = result["page_changes"]
        self.assertEqual((page["side"], page["change_type"], page["pages"]), ("new", "ADDED", [34]))
        self.assertEqual(page["anchor"], "Geltungsbereich")
        self.assertEqual(page["other_side_anchor_page"], 22)
        self.assertEqual(page["other_side_anchor_element_id"], "old-2")
        anchor = {"element_id": "old-2", "page": 22, "position": "after"}
        for change in result["changes"]:
            if change["change_type"] == "ADDED":
                self.assertEqual(change["other_side_anchor"], anchor)
            else:
                self.assertNotIn("other_side_anchor", change)
        self.assertEqual(result["change_blocks"][0]["other_side_anchor"], anchor)
        self.assertEqual(page["impact"]["kinds"], ["page_added"])

    def test_block_partially_inside_group_has_full_pages_without_group_id(self):
        deleted = _candidates("old", 33, [2]) + _candidates("old", 34, [3, 4])
        survivor = {"old": _on_page(_body("old", 1), 33), "new": _body("new", 1)}
        result = _compare(deleted, unchanged=[survivor])
        page, = result["page_changes"]
        block, = result["change_blocks"]
        self.assertEqual(page["pages"], [34])
        self.assertEqual(page["block_ids"], [block["block_id"]])
        self.assertEqual(block["full_pages"], [34])
        self.assertNotIn("page_change_id", block)
        self.assertNotIn("page_change_id", result["changes"][0])

    def test_fully_deleted_table_fragments_count_once_each_and_share_one_table_id(self):
        deleted = _candidates("old", 34, [1, 2])
        fragments = [_on_page(_body("old", 3, "table"), 34), _on_page(_body("old", 4, "table"), 35)]
        tables = _empty_tables()
        tables["deleted_table_candidates"] = [{
            "logical_table_id": "OLD-LT0001", "source_table_ids": [e["element_id"] for e in fragments],
            "source_pages": [34, 35], "first_reading_order": 3,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write_structure(folder, "old", [c["old"] for c in deleted] + fragments)
            result = _compare(deleted, tables=tables, structure_dir=folder)
        page, = result["page_changes"]
        table, = result["table_changes"]
        self.assertEqual(page["pages"], [34, 35])
        self.assertEqual(page["element_count"], 4)
        self.assertEqual(page["table_change_ids"], [table["table_change_id"]])
        self.assertEqual(table["page_change_id"], page["page_change_id"])

    def test_partially_changed_table_blocks_the_page(self):
        deleted = _candidates("old", 34, range(1, 5))
        table = _on_page(_body("old", 5, "table"), 34)
        tables = _empty_tables()
        tables["table_matches"] = [{
            "table_match_id": "TABLE-MATCH-0001", "classification": "MODIFIED",
            "old": {"logical_table_id": "OLD-LT0001", "source_table_ids": [table["element_id"]],
                    "source_pages": [34]},
            "new": {"logical_table_id": "NEW-LT0001", "source_table_ids": ["new-1"], "source_pages": [33]},
            "cell_changes": [{"classification": "DELETED", "old": {"text": "Betrag"}, "new": None}],
        }]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write_structure(folder, "old", [c["old"] for c in deleted] + [table])
            result = _compare(deleted, tables=tables, structure_dir=folder)
        self.assertEqual(result["table_changes"][0]["change_type"], "TABLE_MODIFIED")
        self.assertNotIn("other_side_anchor", result["table_changes"][0])
        self.assertEqual(result["page_changes"], [])


class PageAnchorTests(unittest.TestCase):
    def test_anchor_uses_exact_matches_and_skips_moved_pairs(self):
        from document_compare.phase1.stages.s14_canonical_result import _build_page_changes

        def el(side, order, page):
            return {"element_id": f"{side}-{order}", "reading_order": order, "primary_page": page,
                    "element_type": "paragraph", "text": f"t{order}"}

        old = [el("old", 1, 10), el("old", 2, 11), el("old", 3, 12), el("old", 4, 12)]
        changes = [
            {"change_id": f"C{i}", "change_type": "DELETED", "old": old[i], "new": None,
             "impact": {"signals": [], "kinds": []}}
            for i in (2, 3)
        ]
        exact = [{"old": old[0], "new": el("new", 1, 5)}]
        moved_pair = {"old": old[1], "new": el("new", 9, 40)}
        sources = {"old": (old, set(), {}, True), "new": ([], set(), {}, True)}
        moves = [{"old_element_ids": ["old-2"], "new_element_ids": ["new-9"]}]
        page, = _build_page_changes(
            changes, [], [], {"modified": [moved_pair]}, {}, sources, moves, exact,
        )
        self.assertEqual(page["pages"], [12])
        self.assertEqual(page["other_side_anchor_page"], 5)
        self.assertEqual(page["other_side_anchor_element_id"], "new-1")
        anchor = {"element_id": "new-1", "page": 5, "position": "after"}
        self.assertEqual([item["other_side_anchor"] for item in changes], [anchor, anchor])

    def test_block_uses_first_member_in_reading_order_and_document_start(self):
        deleted = _candidates("old", 1, (3, 1, 2))
        added = _candidates("new", 1, (4,))
        result = _compare(deleted, added=added)
        anchor = {"element_id": None, "page": 1, "position": "document_start"}
        self.assertTrue(all(item["other_side_anchor"] == anchor for item in result["changes"]))
        block, = result["change_blocks"]
        first = next(change for change in result["changes"] if change["change_id"] == block["change_ids"][0])
        self.assertEqual(first["old"]["element_id"], "old-1")
        self.assertEqual(block["other_side_anchor"], first["other_side_anchor"])
        self.assertTrue(all(page["other_side_anchor_page"] is None for page in result["page_changes"]))

    def test_layout_pair_does_not_become_an_anchor(self):
        deleted = _candidates("old", 34, [3])
        survivor = {"old": _on_page(_body("old", 1), 32), "new": _on_page(_body("new", 1), 7)}
        furniture = {"old": _on_page(_body("old", 2), 33), "new": _on_page(_body("new", 2), 8)}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write_structure(folder, "old", [survivor["old"], furniture["old"], deleted[0]["old"]], {
                "old-2": {"category": "layout"},
            })
            result = _compare(deleted, modified=[
                dict(survivor, candidate_id="MODIFIED-1"), dict(furniture, candidate_id="MODIFIED-2"),
            ], structure_dir=folder)
        change = next(item for item in result["changes"] if item["change_type"] == "DELETED")
        self.assertEqual(change["other_side_anchor"], {"element_id": "new-1", "page": 7, "position": "after"})

    def test_table_anchors_to_first_fragment_with_or_without_structured_document(self):
        for side, other, group, change_type in (
            ("old", "new", "deleted_table_candidates", "TABLE_DELETED"),
            ("new", "old", "added_table_candidates", "TABLE_ADDED"),
        ):
            preceding = {side: _on_page(_body(side, 1), 3), other: _on_page(_body(other, 1), 7)}
            between = {side: _on_page(_body(side, 3), 5), other: _on_page(_body(other, 3), 8)}
            fragments = [_on_page(_body(side, order, "table"), order + 2) for order in (2, 4)]
            tables = _empty_tables()
            tables[group] = [{
                "logical_table_id": f"{side}-LT1",
                "source_table_ids": [element["element_id"] for element in fragments],
                "source_pages": [4, 6], "first_reading_order": 2,
            }]
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                _write_structure(folder, side, [preceding[side], *fragments, between[side]])
                for structure in (None, folder):
                    with self.subTest(side=side, structured=structure is not None):
                        result = _compare(tables=tables, structure_dir=structure, modified=[
                            dict(preceding, candidate_id="MODIFIED-1"),
                            dict(between, candidate_id="MODIFIED-3"),
                        ])
                        table, = result["table_changes"]
                        self.assertEqual(table["change_type"], change_type)
                        self.assertEqual(table["other_side_anchor"], {
                            "element_id": f"{other}-1", "page": 7, "position": "after",
                        })

    def test_anchor_skips_toc_to_body_pairs_and_out_of_order_pairs(self):
        from document_compare.phase1.stages.s14_canonical_result import _build_page_changes

        def el(side, order, page):
            return {"element_id": f"{side}-{order}", "reading_order": order, "primary_page": page,
                    "element_type": "paragraph", "text": f"t{order}"}

        old = {o: el("old", o, 10 + o) for o in range(1, 8)}
        new = {o: el("new", o, o) for o in (1, 2, 3, 4, 50, 60)}
        exact = [
            {"old": old[1], "new": new[1]},
            {"old": old[2], "new": new[2]},
            {"old": old[3], "new": new[50]},  # repeated sentence matched far ahead
            {"old": old[4], "new": new[3]},
            {"old": old[5], "new": new[4]},
            {"old": old[6], "new": new[60]},  # TOC line matched to a body element
        ]
        deleted = {"change_id": "C1", "change_type": "DELETED", "old": old[7], "new": None,
                   "impact": {"signals": [], "kinds": []}}
        early = {"change_id": "C2", "change_type": "DELETED", "old": el("old", 0, 10), "new": None,
                 "impact": {"signals": [], "kinds": []}}
        sources = {"old": ([early["old"], *old.values()], set(), {}, True), "new": (list(new.values()), set(), {}, True)}
        _build_page_changes([early, deleted], [], [], {}, {}, sources, [], exact,
                            {"old": {"old-6"}, "new": set()})
        self.assertEqual(deleted["other_side_anchor"], {"element_id": "new-4", "page": 4, "position": "after"})
        self.assertEqual(early["other_side_anchor"], {"element_id": None, "page": 1, "position": "document_start"})
