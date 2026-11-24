"""Offline coverage for paragraph boundaries and relative order changes."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from time import perf_counter

from document_compare.phase1.matching.moves import detect_moves
from document_compare.phase1.stages.s09_movement import (
    OUTPUT_STAGE,
    detect_paragraph_resegmentation,
)
from document_compare.phase1.stages.s11_routing import build_generic_routing
from document_compare.phase1.stages.s14_canonical_result import build_comparison_result


def _element(side, index, text, *, element_type="paragraph", page=1):
    return {
        "element_id": f"{side}-{index}",
        "element_type": element_type,
        "reading_order": index,
        "primary_page": page,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "extraction_validation": {
            "requires_extraction_review": False,
            "extraction_warning_flags": [],
            "extraction_review_flags": [],
        },
    }


def _pair(old, new, text=None, *, element_type="paragraph"):
    text = text or f"Paragraph {old}"
    return {
        "candidate_id": f"P-{old}",
        "old": _element("OLD", old, text, element_type=element_type),
        "new": _element("NEW", new, text, element_type=element_type),
    }


def _canonical(movement):
    movement = {"comparison_stage": OUTPUT_STAGE, **movement}
    tables = {"comparison_stage": "logical_table_comparison_with_provisional_context"}
    routing = build_generic_routing(movement, tables)
    routing["comparison_stage"] = "deterministic_review_routing"
    return build_comparison_result(movement, tables, routing), routing


class MoveDetectionTest(unittest.TestCase):
    def test_single_paragraph_swap_within_same_section_is_low_priority_local_reorder(self):
        # Heading at 0 on both sides; paragraphs 1 and 2 swap under it.
        pairs = [
            _pair(0, 0, "Abschnitt 1", element_type="heading"),
            _pair(1, 2, "Erster Absatz"), _pair(2, 1, "Zweiter Absatz"), _pair(3, 3, "Dritter Absatz"),
        ]
        anchors = {"old": {0: "Abschnitt 1"}, "new": {0: "Abschnitt 1"}}
        moves = self._moves(pairs, anchors=anchors)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["impact"]["kinds"], ["local_reorder"])
        self.assertEqual(moves[0]["impact"]["priority"], "low")

    def test_single_paragraph_moved_to_other_section_is_paragraph_move(self):
        pairs = [
            _pair(0, 0, "Abschnitt 1", element_type="heading"),
            _pair(1, 1, "A"), _pair(2, 5, "Wandert"), _pair(3, 2, "B"), _pair(4, 3, "C"),
            _pair(5, 4, "Abschnitt 2", element_type="heading"),
        ]
        anchors = {"old": {0: "Abschnitt 1", 5: "Abschnitt 2"},
                   "new": {0: "Abschnitt 1", 4: "Abschnitt 2"}}
        moves = self._moves(pairs, anchors=anchors)
        self.assertEqual([m["impact"]["kinds"] for m in moves], [["paragraph_move"]])
        self.assertEqual(moves[0]["impact"]["priority"], "normal")

    def test_body_pair_crossing_matched_headings_is_moved_even_in_lis(self):
        pairs = [
            _pair(1, 1, "Ausnahmen", element_type="heading"),
            _pair(2, 3, "Genehmigungen", element_type="heading"),
            _pair(3, 2, "Regeltext"),
        ]
        anchors = {"old": {1: "Ausnahmen", 2: "Genehmigungen"},
                   "new": {1: "Ausnahmen", 3: "Genehmigungen"}}
        moves = self._moves(pairs, anchors=anchors)
        rule, = [move for move in moves if move["old_element_ids"] == ["OLD-3"]]
        self.assertEqual(rule["new_element_ids"], ["NEW-2"])
        self.assertEqual((rule["from_anchor"], rule["to_anchor"]), ("Genehmigungen", "Ausnahmen"))
        self.assertEqual(rule["impact"]["kinds"], ["paragraph_move"])

    def test_paragraphs_under_renamed_heading_are_not_reported_as_moved(self):
        # "Zweigstellen" (OLD 2) is renamed in NEW (unmatched heading at NEW 2):
        # its body paragraph stays in place, so it must not become a move.
        pairs = [
            _pair(1, 1, "Allgemeines", element_type="heading"),
            _pair(3, 3, "Regeltext unter umbenanntem Abschnitt"),
            _pair(5, 5, "Risiken", element_type="heading"),
            _pair(6, 6, "Text zu Risiken"),
        ]
        anchors = {"old": {1: "Allgemeines", 2: "Zweigstellen", 5: "Risiken"},
                   "new": {1: "Allgemeines", 2: "Drittstaatenzweigstellen", 5: "Risiken"}}
        self.assertEqual(self._moves(pairs, anchors=anchors), [])

    def _moves(self, pairs, *, anchors=None, layout_ids=None, modified_ids=()):
        return detect_moves(
            pairs,
            modified_ids=set(modified_ids),
            layout_ids=layout_ids or {"old": set(), "new": set()},
            anchors=anchors or {"old": {}, "new": {}},
        )

    def test_single_paragraph_move(self):
        pairs = [
            _pair(0, 0, "Zugriff", element_type="heading"),
            _pair(1, 4), _pair(2, 1), _pair(3, 2),
            _pair(4, 3, "Löschung", element_type="heading"),
            _pair(5, 5),
        ]
        move, = self._moves(pairs)
        self.assertEqual(move["old_element_ids"], ["OLD-1"])
        self.assertEqual(move["new_element_ids"], ["NEW-4"])
        self.assertEqual(move["from_anchor"], "Zugriff")
        self.assertEqual(move["to_anchor"], "Löschung")
        self.assertEqual(move["impact"]["kinds"], ["paragraph_move"])
        self.assertEqual(move["impact"]["priority"], "normal")

    def test_three_sections_move_as_one_block(self):
        order = [0, *range(9, 15), *range(1, 9)]
        pairs = [
            _pair(old, new, element_type="heading" if old in {1, 3, 5, 7} else "paragraph")
            for old, new in enumerate(order)
        ]
        block, = self._moves(pairs)
        self.assertEqual(block["old_element_ids"], [f"OLD-{i}" for i in range(1, 7)])
        self.assertEqual(block["new_element_ids"], [f"NEW-{i}" for i in range(9, 15)])
        self.assertEqual(block["element_count"], 6)
        self.assertEqual(block["impact"]["kinds"], ["section_move"])
        self.assertEqual(block["impact"]["priority"], "high")

    def test_blank_order_and_increasing_order_do_not_report_moves(self):
        self.assertEqual(self._moves([]), [])
        pairs = [_pair(1, 5), _pair(2, 9), _pair(3, 0)]
        pairs[-1]["new"]["reading_order"] = None
        self.assertEqual(self._moves(pairs), [])

    def test_two_crossing_moves_have_deterministic_ids(self):
        pairs = [_pair(old, new) for old, new in enumerate([0, 9, *range(2, 9), 1, 10])]
        moves = self._moves(pairs)
        self.assertEqual(moves, self._moves(list(reversed(pairs))))
        self.assertEqual(
            [(m["move_id"], m["old_element_ids"], m["new_element_ids"]) for m in moves],
            [("MOVE-000001", ["OLD-1"], ["NEW-9"]), ("MOVE-000002", ["OLD-9"], ["NEW-1"])],
        )

    def test_explicit_and_derived_furniture_excluded(self):
        pairs = [_pair(0, 3, element_type="footer"), _pair(1, 4, element_type="heading"),
                 _pair(2, 0), _pair(3, 1), _pair(4, 2)]
        self.assertEqual(self._moves(pairs, layout_ids={"old": set(), "new": {"NEW-4"}}), [])

    def test_modified_move_links_to_existing_change_and_uses_high_priority(self):
        pairs = [_pair(old, new) for old, new in enumerate([0, 3, 1, 2])]
        modified = pairs[1]
        modified["old"] = _element("OLD", 1, "Die Frist beträgt 10 Tage.")
        modified["new"] = _element("NEW", 3, "Die Frist beträgt 12 Tage.")
        moves = self._moves(pairs, modified_ids={"P-1"})
        movement = {"unchanged": [pairs[0], *pairs[2:]], "modified": [modified]}
        before, _ = _canonical(movement)
        after, _ = _canonical({**movement, "moves": moves})
        self.assertEqual(after["moves"][0]["impact"]["priority"], "high")
        self.assertTrue(after["moves"][0]["contains_modified"])
        self.assertEqual(after["review_queue"], before["review_queue"])
        self.assertEqual(
            after["changes"], [{**before["changes"][0], "movement": {"move_id": "MOVE-000001"}}],
        )


class ParagraphResegmentationTest(unittest.TestCase):
    def _detect(self, *candidates):
        return detect_paragraph_resegmentation(
            list(candidates), {"old": set(), "new": set()}
        )

    def test_cross_section_or_extraction_review_split_stays_visible(self):
        old = _element("OLD", 4, "Der Zugriff ist zulässig.")
        new = [_element("NEW", 4, "Der Zugriff"), _element("NEW", 5, "ist zulässig.")]
        candidates = [{"candidate_id": "d", "old": old}, *[
            {"candidate_id": f"a{i}", "new": part} for i, part in enumerate(new)
        ]]
        layout = {"old": set(), "new": set()}
        same = {"old": {3: "Ausnahmen"}, "new": {3: "• AUSNAHMEN"}}
        different = {"old": same["old"], "new": {3: "Voraussetzungen"}}
        group, = detect_paragraph_resegmentation(candidates, layout, same)
        self.assertEqual(detect_paragraph_resegmentation(candidates, layout, different), [])
        stale = {**group, "nearest_heading_keys": {"old": ["ausnahmen"], "new": ["voraussetzungen"] * 2}}
        result, _ = _canonical({"paragraph_resegmentation": [stale]})
        self.assertEqual(result["non_semantic_events"], [])
        self.assertTrue(result["changes"][0]["resolution"]["needs_review"])

        for metadata in ({"requires_extraction_review": True},
                         {"requires_extraction_review": False, "extraction_review_flags": ["probable_missing_di_text"]}):
            with self.subTest(metadata=metadata):
                old["extraction_validation"] = metadata
                self.assertEqual(detect_paragraph_resegmentation(candidates, layout, same), [])
                # Replaying a group with extraction review must stay visible at routing, too.
                result, _ = _canonical({"paragraph_resegmentation": [group]})
                self.assertEqual(result["non_semantic_events"], [])
                self.assertTrue(result["changes"][0]["resolution"]["needs_review"])
                self.assertEqual(len(result["review_queue"]), 1)

    def test_material_editorial_collisions_do_not_form_paragraph_groups(self):
        for old, parts in (("Es gilt in Maßen.", ("Es gilt", "in Massen.")),
                           ("Die Dosis beträgt 1/2 mg.", ("Die Dosis", "beträgt 1 2 mg."))):
            with self.subTest(old=old):
                groups = self._detect(
                    {"candidate_id": "d", "old": _element("OLD", 1, old)},
                    {"candidate_id": "a1", "new": _element("NEW", 1, parts[0])},
                    {"candidate_id": "a2", "new": _element("NEW", 2, parts[1])},
                )
                self.assertEqual(groups, [])

    def test_repeated_german_split_bucket_stays_bounded(self):
        candidates = [
            {"candidate_id": f"d{i}", "old": _element("OLD", i, "Freigabe erteilt Freigabe erteilt")}
            for i in range(2000)
        ] + [
            {"candidate_id": f"a{i}", "new": _element("NEW", i, "Freigabe erteilt")}
            for i in range(2000)
        ]
        started = perf_counter()
        self.assertEqual(self._detect(*candidates), [])
        self.assertLess(perf_counter() - started, 2)

    def test_split_into_two_paragraphs(self):
        group, = self._detect(
            {"candidate_id": "d", "old": _element("OLD", 4, "Der Zugriff ist zulässig.")},
            {"candidate_id": "a1", "new": _element("NEW", 4, "Der Zugriff")},
            {"candidate_id": "a2", "new": _element("NEW", 5, "ist zulässig.")},
        )
        self.assertEqual(group["kind"], "paragraph_split")
        self.assertEqual(group["impact_level"], "non_content")
        self.assertEqual(group["source_candidate_ids"], ["a1", "a2", "d"])

    def test_split_into_three_paragraphs_releases_a_paired_suffix(self):
        group, = self._detect(
            {"candidate_id": "a1", "new": _element("NEW", 4, "Der Zugriff")},
            {"candidate_id": "a2", "new": _element("NEW", 5, "ist nur")},
            {"candidate_id": "m",
             "old": _element("OLD", 4, "Der Zugriff ist nur mit Genehmigung zulässig."),
             "new": _element("NEW", 6, "mit Genehmigung zulässig.", page=2)},
        )
        self.assertEqual(group["kind"], "paragraph_split")
        self.assertEqual(group["impact_level"], "non_content")
        self.assertEqual([e["element_id"] for e in group["new_elements"]], ["NEW-4", "NEW-5", "NEW-6"])

    def test_merge_releases_modified_review_pair(self):
        group, = self._detect(
            {"candidate_id": "modified-review",
             "old": _element("OLD", 1, "Der Zugang ist zulässig."),
             "new": _element("NEW", 1, "Der Zugang ist zulässig. Zugriffe werden protokolliert.")},
            {"candidate_id": "deleted", "old": _element("OLD", 2, "Zugriffe werden protokolliert.")},
        )
        self.assertEqual(group["kind"], "paragraph_merge")
        self.assertEqual(group["impact_level"], "non_content")
        self.assertEqual(group["source_candidate_ids"], ["deleted", "modified-review"])
        self.assertEqual([e["element_id"] for e in group["old_elements"]], ["OLD-1", "OLD-2"])

    def test_editorial_concatenation_stays_visible_and_preserves_unrelated_ids(self):
        modified = {
            "candidate_id": "related",
            "old": _element("OLD", 1, "Der Zugriff ist zulässig."),
            "new": _element("NEW", 1, "Der Zugriff"),
        }
        added = {"candidate_id": "added-part", "new": _element("NEW", 2, "ist zulässig!")}
        unrelated = {
            "candidate_id": "unrelated",
            "old": _element("OLD", 4, "Die Regeln gelten bis 2026."),
            "new": _element("NEW", 4, "Die Regeln gelten bis 2027."),
        }
        groups = self._detect(modified, added, unrelated)
        before, prior_routes = _canonical({"modified_review": [modified, unrelated], "added": [added]})
        after, routes = _canonical({
            "modified_review": [unrelated],
            "paragraph_resegmentation": groups,
            "candidate_identity_history": {
                "modified_review": {"order": ["related", "unrelated"],
                                    "replaced": {"related": modified}},
                "added": {"order": ["added-part"], "replaced": {"added-part": added}},
            },
        })
        retained, editorial = after["changes"]
        self.assertEqual(retained["change_id"], before["changes"][1]["change_id"])
        self.assertEqual(after["review_queue"], [before["review_queue"][1]])
        self.assertEqual(routes["routes"][0]["route_id"], prior_routes["routes"][1]["route_id"])
        self.assertEqual(editorial["impact"]["level"], "editorial")
        self.assertEqual(editorial["new_element_ids"], ["NEW-1", "NEW-2"])
        self.assertEqual(after["non_semantic_events"], [])

    def test_content_change_or_nonconsecutive_parts_do_not_group(self):
        deleted = {"candidate_id": "d", "old": _element("OLD", 0, "Die Frist beträgt 10 Tage.")}
        prefix = {"candidate_id": "a1", "new": _element("NEW", 1, "Die Frist beträgt")}
        for order, page, suffix in ((2, 1, "12 Tage."), (3, 1, "10 Tage."), (2, 3, "10 Tage.")):
            with self.subTest(order=order, page=page, suffix=suffix):
                fragment = {"candidate_id": "a2", "new": _element("NEW", order, suffix, page=page)}
                self.assertEqual(self._detect(deleted, prefix, fragment), [])


SECTIONS = [
    ("Zweck", ["Diese Richtlinie regelt die Aufbewahrung von Unterlagen.",
               "Sie konkretisiert die gesetzlichen Vorgaben für das Institut."]),
    ("Geltungsbereich", ["Die Richtlinie gilt für alle Organisationseinheiten.",
                       "Sie gilt auch für ausgelagerte Prozesse."]),
    ("Begriffe", ["Unterlagen sind alle Daten, die im Geschäftsbetrieb entstehen.",
                 "Archivierung bezeichnet die revisionssichere Ablage."]),
    ("Fristen", ["Handelsbücher sind zehn Jahre aufzubewahren und danach zu löschen, sofern keine Sperre besteht.",
                "Geschäftsbriefe sind sechs Jahre aufzubewahren."]),
    ("Speicherung", ["Die Speicherung erfolgt im zentralen Archivsystem.",
                    "Wechseldatenträger sind zu verschlüsseln."]),
    ("Zugriff", ["Der Zugriff ist nur mit Genehmigung zulässig.",
                "Zugriffe werden protokolliert."]),
    ("Löschung", ["Die Löschung ist zu dokumentieren.",
                 "Löschprotokolle sind zwei Jahre aufzubewahren."]),
    ("Kontrolle", ["Die Interne Revision prüft die Einhaltung jährlich.",
                  "Feststellungen werden an den Vorstand berichtet."]),
    ("Schulung", ["Alle Beschäftigten werden jährlich geschult.",
                 "Die Teilnahme wird dokumentiert."]),
    ("Inkrafttreten", ["Diese Richtlinie tritt am 01.01.2026 in Kraft.",
                      "Sie ersetzt die Fassung vom 01.01.2024."]),
]


def _di(rows):
    paragraphs = []
    for index, (role, content) in enumerate(rows):
        page, row = divmod(index, 20)
        y = 0.5 + 0.45 * row
        paragraphs.append({
            "content": content,
            "role": role,
            "boundingRegions": [{
                "pageNumber": page + 1,
                "polygon": [0.8, y, 7.7, y, 7.7, y + 0.3, 0.8, y + 0.3],
            }],
        })
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "stringIndexType": "unicodeCodePoint",
        "content": "\n".join(text for _, text in rows),
        "pages": [
            {"pageNumber": page + 1, "angle": 0, "width": 8.5, "height": 11, "unit": "inch"}
            for page in range((len(rows) + 19) // 20)
        ],
        "paragraphs": paragraphs,
        "tables": [],
    }


def _section_rows(sections):
    rows = [("title", "Richtlinie Aufbewahrung (synthetisch)")]
    for number, (heading, paragraphs) in sections:
        rows.append(("sectionHeading", f"Abschnitt {number}: {heading}"))
        rows.extend((None, text) for text in paragraphs)
    return rows


class StructureChangesEndToEndTest(unittest.TestCase):
    def test_german_di_paragraph_and_section_changes(self):
        old = list(enumerate(SECTIONS, 1))
        new = copy.deepcopy(old)
        split = ["Handelsbücher sind zehn Jahre aufzubewahren",
                 "und danach zu löschen, sofern keine Sperre besteht."]
        new[3][1][1][0:1] = split
        new[5][1][1][:] = [" ".join(SECTIONS[5][1])]
        moved = new[6][1][1].pop()
        new[8][1][1].append(moved)
        new = new[3:] + new[:3]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            for side, sections in (("old", old), ("new", new)):
                (output / f"{side}_di_raw.json").write_text(
                    json.dumps(_di(_section_rows(sections)), ensure_ascii=False),
                    encoding="utf-8",
                )
            completed = subprocess.run(
                [sys.executable, "-m", "document_compare", "run", "--root", str(root),
                 "--from-stage", "02", "--to-stage", "15"],
                env={**os.environ, "CRR_RUN_LLM": "mock", "CRR_DOMAIN_MODE": "generic",
                     "CRR_PROJECT_ROOT": str(root)},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads((output / "comparison_result.json").read_text(encoding="utf-8"))

        self.assertEqual(result["changes"], [])
        self.assertEqual(result["review_queue"], [])
        events = {tuple(event["impact"]["kinds"]): event for event in result["non_semantic_events"]}
        for kind, old_texts, new_texts in (
            ("paragraph_split", [SECTIONS[3][1][0]], split),
            ("paragraph_merge", SECTIONS[5][1], [" ".join(SECTIONS[5][1])]),
        ):
            with self.subTest(kind=kind):
                event = events[(kind,)]
                self.assertEqual(event["impact"]["level"], "non_content")
                for side, expected in (("old", old_texts), ("new", new_texts)):
                    elements = event[f"{side}_elements"]
                    self.assertEqual([element["text"] for element in elements], expected)
                    self.assertEqual(event[f"{side}_element_ids"],
                                     [element["element_id"] for element in elements])

        self.assertEqual(result["statistics"]["moves"], 2)
        self.assertEqual(result["moves"], result["generic_projection"]["moves"])
        paragraph, = [m for m in result["moves"] if m["impact"]["kinds"] == ["paragraph_move"]]
        self.assertEqual([e["text"] for e in paragraph["old_elements"]], [moved])
        self.assertEqual(paragraph["from_anchor"], "Abschnitt 7: Löschung")
        self.assertEqual(paragraph["to_anchor"], "Abschnitt 9: Schulung")
        block, = [m for m in result["moves"] if m["impact"]["kinds"] == ["section_move"]]
        self.assertEqual(block["element_count"], 9)
        self.assertEqual(
            [e["text"] for e in block["old_elements"] if e["element_type"] == "heading"],
            ["Abschnitt 1: Zweck", "Abschnitt 2: Geltungsbereich", "Abschnitt 3: Begriffe"],
        )
        self.assertEqual(block["impact"]["priority"], "high")
