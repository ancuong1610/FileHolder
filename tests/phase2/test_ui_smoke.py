"""AppTest smoke tests for the Streamlit reviewer (stub client, no network)."""
from __future__ import annotations

import unittest

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover - streamlit is an optional extra
    raise unittest.SkipTest("streamlit is not installed")

from document_compare.phase2.ui import review, texts as ui_texts
from tests.phase2.ui_stub import StubClient, default_outline, item


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


class SectionOrderingTests(unittest.TestCase):
    def test_natural_prefix_order(self):
        cases = [
            ["2 Scope", "10 Rules"],
            ["A Scope", "B Rules", "C Appendix"],
            ["AT 1 Scope", "AT 4.3 Rules", "BT 1 Appendix"],
            ["BT 1.2.3 Scope", "BT 1.2.10 Rules"],
            ["A.2 Scope", "A.10 Rules"],
            ["I Scope", "II Rules", "IV Appendix"],
            ["II.2 Scope", "II.10 Rules", "IV.1 Appendix"],
        ]
        for expected in cases:
            with self.subTest(expected=expected):
                sections = [{"title": title} for title in reversed(expected)]
                ordered = review.order_sections(sections, "Section number")
                self.assertEqual([node["title"] for node in ordered], expected)
                self.assertFalse(review.numbers_ascending(sections))
                self.assertTrue(review.numbers_ascending(ordered))

    def test_toc_precedes_title_keys_and_unlabelled_order_is_stable(self):
        sections = [{"title": "Zebra notes"}, {"title": "2 Scope"},
                    {"title": "10 Rules", "toc_rank": 1}, {"title": "Appendix 1"},
                    {"title": "Introduction", "toc_rank": 0}, {"title": "Closing notes"}]
        ranked_first = {"sections": [sections[4], sections[1]]}
        self.assertIs(review.with_title_root(ranked_first), ranked_first)
        self.assertEqual(
            [n["title"] for n in review.order_sections(sections, "Section number")],
            ["Introduction", "10 Rules", "2 Scope", "Zebra notes", "Appendix 1", "Closing notes"],
        )
        self.assertEqual(
            [n["title"] for n in review.order_sections(sections, "Document order")],
            [n["title"] for n in sections],
        )


class ShellTests(unittest.TestCase):
    def test_app_loads_without_job(self):
        at = make_app(StubClient()).run()
        self.assertFalse(at.exception)
        self.assertEqual([t.label for t in at.sidebar.tabs], ["Vergleichen", "Auftrag öffnen", "Vergleichsassistent"])
        self.assertIn("Beim Vergleich werden beide PDF-Dateien an den konfigurierten Extraktionsdienst gesendet.",
                      texts(at.sidebar.caption) + texts(at.sidebar.markdown))
        self.assertEqual(len(at.sidebar.file_uploader), 2)
        self.assertIn("flex:1 1 0", "".join(str(m.value) for m in at.sidebar.markdown))  # tabs share the width
        self.assertEqual(at.main.title[0].value, "Dokumentenvergleich")

    def test_upload_requires_both_files(self):
        stub = StubClient()
        at = make_app(stub).run()
        at.sidebar.button(key="compare").click()
        at.run()
        self.assertFalse(at.exception)
        self.assertIn("beide", texts(at.sidebar.warning).lower())
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
        self.assertNotIn("outline", [c[0] for c in stub.calls])

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
        stub.raise_on["status"] = stub.make_error(None, "api_unreachable", "Die lokale API ist nicht erreichbar. at http://127.0.0.1:8321.")
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertIn("Die lokale API ist nicht erreichbar.", texts(at.main.error))
        self.assertEqual(at.session_state["job_id"], "job1")

    def test_outline_failure_is_a_visible_error(self):
        stub = StubClient()
        stub.raise_on["outline"] = stub.make_error(409, "invalid_artifact", "comparison result is not readable")
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertIn("Das gespeicherte Ergebnis oder eine zugehörige Datei ist nicht lesbar.", texts(at.main.error))

    def test_invalid_job_id_shows_visible_error(self):
        stub = StubClient()
        stub.raise_on["status"] = stub.make_error(
            None, "invalid_arguments", "Job ID must be 12 lowercase hex characters.")
        at = open_job(make_app(stub), "x/../../jobs")
        self.assertFalse(at.exception)
        self.assertIn("Die Auftrags-ID muss aus 12 Hexadezimalzeichen bestehen", texts(at.main.error))

    def test_select_job_clears_chat_state(self):
        at = open_job(make_app(StubClient()))
        at.session_state["chat_error"] = {"code": "chat_failed", "message": "old failure"}
        at.session_state["chat_history"] = [{"role": "user", "content": "old question"}]
        at.run()
        self.assertIn("Der Assistent konnte keine Antwort erstellen.", texts(at.sidebar.error))
        at.sidebar.text_input(key="open_job_id").set_value("job2")
        at.sidebar.button(key="open_job").click()
        at.run()
        self.assertNotIn("chat_error", at.session_state)
        self.assertEqual(at.session_state["chat_history"], [])
        self.assertNotIn("Der Assistent konnte keine Antwort erstellen.", texts(at.sidebar.error))


def pick(at: AppTest, record_id: str, kind: str = "change") -> AppTest:
    key = f"pick::{kind}::{record_id}"
    if not any(b.key == key for b in at.main.button):  # its section is collapsed: open the sections first
        for tog in [k for k in toggle_keys(at) if k != "tog::S3"]:
            if at.main.button(key=tog).label.startswith("\u203a"):
                at.main.button(key=tog).click()
                at.run()
    at.main.button(key=key).click()
    return at.run()


def page_calls(stub: StubClient) -> list[tuple]:
    return [c for c in stub.calls if c[0] == "page_png"]


def side(counterpart="present", location="polygon", page_count=3, primary_page=1):
    return {"counterpart": counterpart, "location": location, "page_count": page_count,
            "primary_page": primary_page, "regions": []}


def tree_keys(at: AppTest) -> list[str]:
    return [b.key for b in at.main.button if (b.key or "").startswith("pick::")]


def toggle_keys(at: AppTest) -> list[str]:
    return [b.key for b in at.main.button if (b.key or "").startswith("tog::")]


def toggle(at: AppTest, section_id: str) -> AppTest:
    at.main.button(key=f"tog::{section_id}").click()
    return at.run()


def flat_outline(titles, children_of_first=None):
    """Flat outline: one section per title, each with one ADDED change CHANGE-<n> (n = position + 1)."""
    from tests.phase2.ui_stub import item

    sections = []
    for n, title in enumerate(titles, start=1):
        change = item("change", f"CHANGE-{n:06d}", "ADDED", f"change in {title}", [title])
        sections.append({"section_id": f"S{n}", "title": title, "side": "new", "level": None, "change_count": 1,
                         "children": [], "items": [change]})
    if children_of_first:
        sections[0]["children"] = [sections.pop(1)]
    return {"documents": {"old": {"page_count": 3}, "new": {"page_count": 4}},
            "sections": sections, "unplaced": [], "review": [], "total": len(titles)}


def structure_text(at: AppTest) -> str:
    return texts(at.main.markdown) + texts(at.main.caption)


class OutlineHelperTests(unittest.TestCase):
    def test_target_page_rules(self):
        self.assertEqual(review.target_page(side(primary_page=2), side(primary_page=7)), 2)
        # no counterpart: other side's page, clamped to this side's page count
        self.assertEqual(review.target_page(side("none", "unavailable", 3, None), side(primary_page=7)), 3)
        self.assertEqual(review.target_page(side("none", "unavailable", 5, None), side(primary_page=2)), 2)
        # no page count and no own page: nothing to show
        self.assertIsNone(review.target_page(side("none", "unavailable", None, None), side(primary_page=2)))
        self.assertIsNone(review.target_page(side("none", "unavailable", 3, None), side("none", "unavailable", 3, None)))

    def test_filter_recomputes_counts_and_never_lists_review(self):
        outline = default_outline()
        added = review.filter_outline(outline, "all", "ADDED")
        self.assertEqual([n["change_count"] for n in added["sections"]], [1, 0])
        self.assertNotIn("review", added)
        self.assertEqual([i["record_id"] for i in review.flatten(added)], ["CHANGE-000002"])
        tables = review.filter_outline(outline, "table", "all")
        self.assertEqual(review.flatten(tables), [])
        everything = review.filter_outline(outline)
        self.assertEqual([i["record_id"] for i in review.flatten(everything)], ["CHANGE-000001", "CHANGE-000002"])

    def test_search_matches_label_or_section_titles(self):
        outline = default_outline()
        by_label = review.filter_outline(outline, query="  SUPPORTED ")
        self.assertEqual([i["record_id"] for i in review.flatten(by_label)], ["CHANGE-000001"])
        self.assertEqual([n["title"] for n in by_label["sections"]], ["Section A"])  # C dropped, B dropped
        self.assertEqual(by_label["sections"][0]["children"], [])
        by_title = review.filter_outline(outline, query="section b")
        self.assertEqual([i["record_id"] for i in review.flatten(by_title)], ["CHANGE-000002"])
        by_parent = review.filter_outline(outline, query="section a")  # parent title keeps everything below
        self.assertEqual([i["record_id"] for i in review.flatten(by_parent)], ["CHANGE-000001", "CHANGE-000002"])
        titled_empty = review.filter_outline(outline, query="section c")  # matching section stays, count 0
        self.assertEqual([(n["title"], n["change_count"]) for n in titled_empty["sections"]], [("Section C", 0)])
        combined = review.filter_outline(outline, ctype="ADDED", query="section a")
        self.assertEqual([i["record_id"] for i in review.flatten(combined)], ["CHANGE-000002"])
        self.assertEqual(review.flatten(review.filter_outline(outline, query="zzz")), [])

    def test_png_helpers(self):
        data = review._placeholder_png(30, 20)
        self.assertEqual(review._png_size(data), (30, 20))
        self.assertEqual(review._png_size(b"nope"), (612, 792))


class ReviewTests(unittest.TestCase):
    def test_layout_header_chips_and_removed_elements(self):
        stub = StubClient()
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        main_text = texts(at.main.markdown) + texts(at.main.caption)
        self.assertIn("old\\_policy\\.pdf", main_text)
        self.assertIn("3 Seiten", main_text)
        self.assertIn("new\\_policy\\.pdf", main_text)
        self.assertIn("4 Seiten", main_text)
        self.assertIn("job1", [c.value for c in at.main.code])
        for chip in (":blue-badge[Änderungen 2]", ":gray-badge[Tabellen 0]", ":green-badge[Hinzugefügt 1]",
                     ":red-badge[Gelöscht 0]", ":orange-badge[Geändert 1]"):
            self.assertIn(chip, main_text)
        for gone in ("Limitations", "not legal or semantic ground truth", "Uncertain", "Supported"):
            self.assertNotIn(gone, main_text)
        self.assertEqual({s.label for s in at.main.selectbox}, {"Änderungstyp", "Kategorie", "Auswirkung"})
        self.assertEqual(at.main.selectbox(key="f_type").options,
                         ["Alle", "Hinzugefügt", "Gelöscht", "Geändert", "Prüfung"])
        self.assertEqual(at.main.selectbox(key="f_kind").options, ["Alle", "Änderung", "Tabelle", "Verschiebung"])
        self.assertEqual([t.label for t in at.main.text_input], ["Suche"])
        self.assertEqual(at.main.text_input[0].placeholder, "Änderungen, Abschnitte oder IDs suchen")
        self.assertEqual(len(at.main.metric), 0)
        self.assertEqual(len(at.main.text), 0)  # no old/new text dump
        self.assertIn("Änderungs-ID: CHANGE-000001", main_text)
        self.assertNotIn(":blue-badge[Inhalt]", main_text)  # legacy change has unknown impact

    def test_impact_badges_filter_counts_and_move_viewer(self):
        outline = default_outline()
        top = outline["sections"][0]
        top["items"][0]["impact"] = {
            "level": "content", "priority": "high", "signals": list(ui_texts.IMPACT_SIGNALS), "kinds": [],
        }
        top["children"][0]["items"][0]["impact"] = {
            "level": "editorial", "priority": "low", "signals": [], "kinds": ["punctuation"],
        }
        for index, move_kind in ((1, "paragraph_move"), (2, "local_reorder")):
            move = item("move", f"MOVE-{index:06d}", None, "Versetzter Absatz", ["Section A"])
            move.update(from_anchor="Früher", to_anchor="Jetzt",
                        impact={"level": "moved", "priority": "normal", "signals": [], "kinds": [move_kind]})
            top["items"].append(move)
        outline["unplaced"].append(
            item("change", "CHANGE-000003", "MODIFIED", "Die Frist beträgt 60 Tage.", [])
        )
        stub = StubClient(outline_value=outline)
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="f_impact").options, ["Alle", "Inhalt", "Redaktionell", "Verschoben"])
        self.assertFalse(at.checkbox(key="f_local_reorders").value)
        self.assertEqual(at.checkbox(key="f_local_reorders").label, "Lokale Umstellungen anzeigen")
        self.assertIn("pick::move::MOVE-000001", tree_keys(at))
        self.assertNotIn("pick::move::MOVE-000002", tree_keys(at))
        main = texts(at.main.markdown)
        for badge in ("Inhalt 1", "Redaktionell 1", "Verschoben 1", "Wichtig"):
            self.assertIn(badge + "]", main)
        captions = texts(at.main.caption)
        for signal in ui_texts.IMPACT_SIGNALS.values():
            self.assertIn(signal, captions)

        at.selectbox(key="f_impact").select("moved").run()
        self.assertFalse(at.exception)
        self.assertEqual(tree_keys(at), ["pick::move::MOVE-000001"])
        self.assertEqual(at.session_state["selected"]["record_id"], "MOVE-000001")
        self.assertIn("Versetzter Absatz", texts(at.main.caption))
        self.assertIn("Früher → Jetzt", texts(at.main.caption))
        self.assertEqual(page_calls(stub)[-2:], [
            ("page_png", "job1", "old", 1, "move", "MOVE-000001", False),
            ("page_png", "job1", "new", 2, "move", "MOVE-000001", False),
        ])
        self.assertIn("Inhalt 0]", texts(at.main.markdown))
        self.assertIn("Änderungen 0]", texts(at.main.markdown))

        at.checkbox(key="f_local_reorders").check().run()
        self.assertEqual(tree_keys(at), ["pick::move::MOVE-000001", "pick::move::MOVE-000002"])
        self.assertIn("Verschoben 2]", texts(at.main.markdown))
        at.selectbox(key="f_impact").select("editorial").run()
        self.assertIn("1 Befunde ohne Auswirkungsangabe sind ausgeblendet.", texts(at.main.caption))
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000002"])
        self.assertIn(":gray-badge[Redaktionell]", texts(at.main.markdown))

    def test_table_label_is_localized(self):
        outline = default_outline()
        outline["sections"][0]["items"].append(
            item("table", "TABLE-000001", "TABLE_MODIFIED", "Table · p.2 · 1 cell changed", ["Section A"])
        )
        at = open_job(make_app(StubClient(outline_value=outline)))
        button = at.main.button(key="pick::table::TABLE-000001")
        self.assertIn("Tabelle · S\\. 2 · 1 Zelle geändert", button.label)

    def test_tree_structure_and_default_selection(self):
        stub = StubClient()
        at = open_job(make_app(stub))
        self.assertEqual(at.session_state["selected"], {"kind": "change", "record_id": "CHANGE-000001"})
        self.assertEqual(len(at.main.expander), 0)  # a tree, not boxed expanders
        main_text = texts(at.main.markdown) + texts(at.main.caption)
        self.assertNotIn("Flagged for review", main_text)
        self.assertNotIn("Flagged for review", "".join(b.label for b in at.main.button))
        # Section A is on the selected path (open); B is collapsed; C has nothing below (no caret, disabled)
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2", "tog::S3"])
        labels = {b.key: b.label for b in at.main.button}
        self.assertTrue(labels["tog::S1"].startswith("\u2304\u2002Section A"))
        self.assertTrue(labels["tog::S2"].startswith("\u203a\u2002Section B"))
        self.assertNotIn("\u203a", labels["tog::S3"])
        self.assertNotIn("\u2304", labels["tog::S3"])
        self.assertTrue(at.main.button(key="tog::S3").disabled)
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001"])  # B's item hidden while collapsed
        self.assertTrue(labels["pick::change::CHANGE-000001"].startswith(":orange[\u25cf]"))
        self.assertIn("supported snippet", labels["pick::change::CHANGE-000001"])
        self.assertIn(":gray[S. 2]", texts(at.main.markdown))
        self.assertIn(":gray-badge[2]", texts(at.main.markdown))
        types = {b.key: b.proto.type for b in at.main.button if (b.key or "").startswith(("pick::", "tog::"))}
        self.assertEqual(types["pick::change::CHANGE-000001"], "primary")
        self.assertEqual(types["tog::S1"], "tertiary")
        self.assertIn("Section A", texts(at.main.markdown))
        self.assertIn("Rot = gelöscht", texts(at.main.caption))

    def test_tree_toggle_expands_and_collapses(self):
        at = open_job(make_app(StubClient()))
        at = toggle(at, "S2")
        self.assertFalse(at.exception)
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001", "pick::change::CHANGE-000002"])
        self.assertTrue(at.main.button(key="tog::S2").label.startswith("\u2304"))
        at = toggle(at, "S1")  # collapse the parent hides everything below
        self.assertEqual(tree_keys(at), [])
        self.assertTrue(at.main.button(key="tog::S1").label.startswith("\u203a"))
        at = toggle(at, "S1")
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001", "pick::change::CHANGE-000002"])
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")  # toggling keeps selection

    def test_selecting_via_next_reveals_its_section(self):
        at = open_job(make_app(StubClient()))
        self.assertNotIn("pick::change::CHANGE-000002", tree_keys(at))
        at.main.button(key="next_finding").click()
        at.run()
        self.assertIn("pick::change::CHANGE-000002", tree_keys(at))
        self.assertTrue(at.main.button(key="tog::S2").label.startswith("\u2304"))

    def test_search_filters_the_tree(self):
        at = open_job(make_app(StubClient()))
        at.main.text_input(key="f_search").set_value("section b")  # matches a section title
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000002")
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2"])
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000002"])
        at.main.text_input(key="f_search").set_value("SUPPORTED")  # label, case-insensitive
        at.run()
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001"])
        at.main.text_input(key="f_search").set_value("nothing matches this")
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(tree_keys(at), [])
        self.assertIn("Keine Befunde entsprechen diesen Filtern.", texts(at.main.info))
        self.assertIn("Keine passenden Abschnitte oder Änderungen gefunden.", texts(at.main.caption))
        self.assertEqual(len(at.main.get("image")), 0)
        at.main.text_input(key="f_search").set_value("")
        at.run()
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")  # back to the default first
        self.assertEqual(len(at.main.get("image")), 2)

    def test_added_item_shows_old_section_context_page_without_highlight(self):
        stub = StubClient(locations_value={
            "kind": "change", "record_id": "CHANGE-000002", "change_type": "ADDED",
            "sides": {"old": side("none", "unavailable", 3, None), "new": side(primary_page=2, page_count=4)}})
        at = pick(open_job(make_app(stub)), "CHANGE-000002")
        self.assertFalse(at.exception)
        calls = [c for c in page_calls(stub) if c[2] == "old"]
        self.assertEqual([c[3:] for c in calls], [(1, None, None, False)])  # outline's context page, no polygons
        new_calls = [c for c in page_calls(stub) if c[2] == "new"]
        self.assertEqual(new_calls[-1][3:], (2, "change", "CHANGE-000002", True))  # highlighted, dashed
        self.assertEqual(at.session_state["selected"], {"kind": "change", "record_id": "CHANGE-000002"})
        self.assertIn("Section A \u203a Section B", texts(at.main.markdown))
        self.assertIn(":green[Hinzugefügt]", texts(at.main.markdown))
        self.assertEqual(len(at.main.get("image")), 2)
        captions = texts(at.main.caption)
        self.assertIn("Alt \u00b7 Seite 1 \u00b7 Abschnittskontext", captions)
        self.assertIn("Neu \u00b7 Seite 2", captions)
        self.assertNotIn("Abschnittskontext\n", captions.split("Neu")[-1])
        self.assertNotIn("No counterpart", captions + texts(at.main.markdown))
        self.assertIn("Änderungs-ID: CHANGE-000002", captions)
        types = {b.key: b.proto.type for b in at.main.button if (b.key or "").startswith("pick::")}
        self.assertEqual(types["pick::change::CHANGE-000002"], "primary")

    def test_modified_item_requests_both_pages_with_highlight(self):
        stub = StubClient()
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        calls = page_calls(stub)
        self.assertEqual({c[2] for c in calls}, {"old", "new"})
        for call in calls:
            self.assertEqual(call[4:], ("change", "CHANGE-000001", False))
        self.assertEqual(len(at.main.get("image")), 2)
        self.assertNotIn("Abschnittskontext", texts(at.main.caption))

    def test_next_previous_walk_tree_order(self):
        at = open_job(make_app(StubClient()))
        self.assertTrue(at.main.button(key="prev_finding").disabled)
        order = ["CHANGE-000002"]  # review items are not part of the walk
        for expected in order:
            at.main.button(key="next_finding").click()
            at.run()
            self.assertEqual(at.session_state["selected"]["record_id"], expected)
        self.assertTrue(at.main.button(key="next_finding").disabled)
        at.main.button(key="prev_finding").click()
        at.run()
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")
        self.assertFalse(at.exception)

    def test_filters_apply_client_side_and_reselect_first(self):
        stub = StubClient()
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        outline_calls = len([c for c in stub.calls if c[0] == "outline"])
        at.main.selectbox(key="f_type").select("Added")
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000002"])
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000002")
        self.assertIn(":gray-badge[1]", texts(at.main.markdown))
        self.assertEqual(len([c for c in stub.calls if c[0] == "outline"]), outline_calls)  # cached, no refetch
        at.main.selectbox(key="f_kind").select("Table")
        at.run()
        self.assertEqual(tree_keys(at), [])
        self.assertNotIn("selected", at.session_state)
        self.assertIn("Keine Befunde entsprechen diesen Filtern.", texts(at.main.info))
        self.assertEqual(len(at.main.get("image")), 0)

    def test_deleted_item_shows_new_section_context_without_placeholder_text(self):
        stub = StubClient(locations_value={
            "kind": "change", "record_id": "CHANGE-000001", "change_type": "DELETED",
            "sides": {"old": side(primary_page=2), "new": side("none", "unavailable", 4, None)}})
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertFalse(at.exception)
        self.assertNotIn("No counterpart", texts(at.main.caption))
        self.assertEqual(len(at.main.get("image")), 2)
        self.assertIn("Neu \u00b7 Seite 2 \u00b7 Abschnittskontext", texts(at.main.caption))
        new_call = [c for c in page_calls(stub) if c[2] == "new"][-1]
        self.assertEqual(new_call[4:6], (None, None))

    def test_unrenderable_context_side_keeps_placeholder(self):
        stub = StubClient(png=None)
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertIn("Quell-PDF", texts(at.main.info))
        self.assertEqual(len(at.main.get("image")), 2)

    def test_source_unavailable_shows_notice(self):
        stub = StubClient(png=None)
        at = pick(open_job(make_app(stub)), "CHANGE-000001")
        self.assertFalse(at.exception)
        self.assertIn("Quell-PDF", texts(at.main.info))
        self.assertEqual(len(at.main.text), 0)

    def test_missing_names_fall_back(self):
        stub = StubClient(status_value={"job_id": "job1", "state": "done", "stages_completed": []})
        at = open_job(make_app(stub))
        self.assertIn("alt\\.pdf", texts(at.main.markdown))
        self.assertIn("neu\\.pdf", texts(at.main.markdown))

    def test_flat_outline_with_unnumbered_title_gets_a_title_root(self):
        outline = flat_outline(["Policy Title", "Section 4 Scope", "Section 5 Rules"])
        at = open_job(make_app(StubClient(outline_value=outline)))
        self.assertFalse(at.exception)
        # root S1 is open; the others are its children (one indent level deeper), collapsed
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2", "tog::S3"])
        self.assertTrue(at.main.button(key="tog::S1").label.startswith("\u2304"))
        self.assertTrue(at.main.button(key="tog::S2").label.startswith("\u203a"))
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001"])  # the root's own item stays its own
        self.assertIn(":gray-badge[3]", texts(at.main.markdown))  # root counts everything
        self.assertEqual(outline["sections"][0]["children"], [])  # the API payload is untouched
        self.assertEqual(len(outline["sections"]), 3)

    def test_real_hierarchy_gets_no_title_root(self):
        outline = flat_outline(["Policy Title", "Section 4 Scope", "Section 5 Rules"], children_of_first=True)
        at = open_job(make_app(StubClient(outline_value=outline)))
        self.assertFalse(at.exception)
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2", "tog::S3"])  # S3 stays a top-level sibling
        self.assertTrue(at.main.button(key="tog::S3").label.startswith("\u203a"))
        self.assertNotIn(":gray-badge[3]", texts(at.main.markdown))  # nothing was re-parented
        numbered = flat_outline(["Section 1 First", "Section 2 Second"])
        at = open_job(make_app(StubClient(outline_value=numbered)))
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2"])
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001"])
        self.assertFalse(any(b.key == "tog::S2" and b.label.startswith("\u2304") for b in at.main.button))  # no root

    def test_section_number_order_reorders_tree_and_walk(self):
        outline = flat_outline(["Section 12 Last", "Section 1 First", "Section 2 Second"])
        at = open_job(make_app(StubClient(outline_value=outline)))
        self.assertEqual([o for o in at.main.radio(key="tree_order").options], ["Dokumentreihenfolge", "Abschnittsnummer"])
        self.assertEqual(at.main.radio(key="tree_order").value, "Document order")
        self.assertEqual(toggle_keys(at), ["tog::S1", "tog::S2", "tog::S3"])
        at.main.radio(key="tree_order").set_value("Section number")
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(toggle_keys(at), ["tog::S2", "tog::S3", "tog::S1"])  # 1, 2, 12
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")  # selection is kept
        self.assertTrue(at.main.button(key="next_finding").disabled)  # Section 12 is now last
        for expected in ("CHANGE-000003", "CHANGE-000002"):  # Previous walks 12 -> 2 -> 1 in displayed order
            at.main.button(key="prev_finding").click()
            at.run()
            self.assertEqual(at.session_state["selected"]["record_id"], expected)
        self.assertTrue(at.main.button(key="prev_finding").disabled)

    def test_order_caption_only_when_document_order_is_not_ascending(self):
        out_of_order = flat_outline(["Section 12 Last", "Section 1 First"])
        at = open_job(make_app(StubClient(outline_value=out_of_order)))
        self.assertIn(ui_texts.ORDER_NOTE, structure_text(at))
        at.main.radio(key="tree_order").set_value("Section number")
        at.run()
        self.assertNotIn(ui_texts.ORDER_NOTE, structure_text(at))
        ascending = flat_outline(["Section 1 First", "Section 2 Second"])
        at = open_job(make_app(StubClient(outline_value=ascending)))
        self.assertNotIn(ui_texts.ORDER_NOTE, structure_text(at))
        titled = flat_outline(["Policy Title", "Section 4 A", "Section 12 B", "Section 1 C"])  # under the root
        at = open_job(make_app(StubClient(outline_value=titled)))
        self.assertIn(ui_texts.ORDER_NOTE, structure_text(at))

    def test_empty_outline(self):
        stub = StubClient(outline_value={"documents": {"old": {"page_count": None}, "new": {"page_count": None}},
                                         "sections": [], "unplaced": [], "review": [], "total": 0})
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        self.assertIn("Dieses Ergebnis enthält keine Befunde.", texts(at.main.info))


def review_outline() -> dict:
    """Default outline, plus: CHANGE-000001 flagged, and a standalone review item in Section A."""
    outline = default_outline()
    top = outline["sections"][0]
    flagged = top["items"][0]
    flagged.update(needs_review=True, review_reason="Possible match on the other side not confirmed",
                   review_ids=["REVIEW-000001"])
    plain = [i for n in (top, top["children"][0]) for i in n["items"] if i is not flagged]
    for i in plain:
        i.update(needs_review=False, review_reason=None, review_ids=[])
    standalone = item("review", "REVIEW-000002", "REVIEW", "Moved block text", ["Section A"], 1, 3)
    standalone.update(needs_review=True, review_reason="Block of content moved", review_ids=["REVIEW-000002"])
    top["items"].append(standalone)
    top["change_count"] += 1
    return outline


class NeedsReviewTests(unittest.TestCase):
    def test_chip_pill_and_header_reason(self):
        stub = StubClient(outline_value=review_outline())
        at = open_job(make_app(stub))
        self.assertFalse(at.exception)
        main_text = texts(at.main.markdown) + texts(at.main.caption)
        self.assertIn(":violet-badge[Prüfung erforderlich 2]", main_text)
        self.assertIn("violett gestrichelt = Prüfung erforderlich", main_text)
        labels = {b.key: b.label for b in at.main.button}
        self.assertIn(":violet[Prüfung]", labels["pick::change::CHANGE-000001"])
        self.assertTrue(labels["pick::change::CHANGE-000001"].startswith(":orange["))  # type dot stays first
        self.assertTrue(labels["pick::review::REVIEW-000002"].startswith(":violet[\u25cf] :violet[Prüfung]"))
        self.assertIn("Prüfung erforderlich \u2014 Mögliche Entsprechung auf der anderen Seite unbestätigt", main_text)

    def test_llm_opinion_panel_only_on_items_the_llm_saw(self):
        outline = review_outline()
        flagged = next(i for i in review.flatten(outline) if i["record_id"] == "CHANGE-000001")
        flagged["llm_opinion"] = {"outcome": "UNCERTAIN", "reason": "Ambiguous pair.",
                                  "evaluator": "mock-evaluator-v1", "evidence_refs": ["OLD-P1", "NEW-P1"]}
        at = open_job(make_app(StubClient(outline_value=outline)))
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["selected"]["record_id"], "CHANGE-000001")
        shown = texts(at.main.markdown) + texts(at.main.caption)
        self.assertIn("Einschätzung des Sprachmodells · beratend", shown)
        self.assertIn(":violet-badge[Unsicher] Ambiguous pair\\.", shown)
        self.assertIn("mock-evaluator-v1 · Berücksichtigte Belege: OLD-P1, NEW-P1", shown.replace("\\", ""))
        self.assertNotIn("0.3", shown)
        at.session_state["selected"] = {"kind": "review", "record_id": "REVIEW-000002"}
        at.run()
        self.assertNotIn("Einschätzung des Sprachmodells · beratend", texts(at.main.markdown) + texts(at.main.caption))

    def test_needs_review_only_filter_and_review_type(self):
        stub = StubClient(outline_value=review_outline())
        at = open_job(make_app(stub))
        self.assertEqual(at.main.checkbox(key="f_needs_review").label, "Nur mit Prüfbedarf")
        self.assertFalse(at.main.checkbox(key="f_needs_review").value)
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001", "pick::review::REVIEW-000002"])
        at.main.checkbox(key="f_needs_review").check()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(tree_keys(at), ["pick::change::CHANGE-000001", "pick::review::REVIEW-000002"])
        self.assertNotIn("pick::change::CHANGE-000002", [b.key for b in at.main.button])
        at.main.selectbox(key="f_type").select("Review")  # combines: only standalone review items
        at.run()
        self.assertEqual(tree_keys(at), ["pick::review::REVIEW-000002"])
        self.assertEqual(at.session_state["selected"], {"kind": "review", "record_id": "REVIEW-000002"})
        self.assertIn("Prüfung erforderlich \u2014 Ein Inhaltsblock wurde verschoben", texts(at.main.markdown))
        self.assertEqual(len(at.main.get("image")), 2)
        calls = [c for c in stub.calls if c[0] == "page_png" and c[4] == "review"]
        self.assertTrue(calls and all(c[6] for c in calls))  # review highlights are drawn dashed

    def test_only_flagged_filter_hides_unflagged_items(self):
        outline = review_outline()
        flagged = review.filter_outline(outline, flagged_only=True)
        self.assertEqual([i["record_id"] for i in review.flatten(flagged)], ["CHANGE-000001", "REVIEW-000002"])
        self.assertEqual(review.chip_counts(outline)["Needs review"], 2)
        self.assertEqual(review.chip_counts(outline)["Changes"], 2)  # review items are not changes


class ChatTests(unittest.TestCase):
    def ask(self, stub, question="what changed?"):
        at = open_job(make_app(stub))
        at.sidebar.chat_input[0].set_value(question).run()
        return at

    def test_chat_not_configured_shows_one_caption(self):
        stub = StubClient()
        stub.raise_on["chat"] = stub.make_error(503, "chat_not_configured", "Chat is not configured on this server.")
        at = self.ask(stub)
        self.assertFalse(at.exception)
        self.assertIn("Der Assistent ist auf diesem Rechner nicht konfiguriert.", texts(at.sidebar.caption))
        self.assertEqual(len(at.sidebar.warning) + len(at.sidebar.info) + len(at.sidebar.error), 0)
        self.assertTrue(tree_keys(at))  # rest of the UI still works

    def test_chat_appends_question_and_answer(self):
        stub = StubClient()
        at = self.ask(stub)
        self.assertFalse(at.exception)
        history = at.session_state["chat_history"]
        self.assertEqual([(m["role"], m["content"]) for m in history],
                         [("user", "what changed?"), ("assistant", "stub answer")])
        self.assertIn(("chat", "job1", "what changed?"), stub.calls)
        self.assertIn("stub answer", texts(at.sidebar.text))
        shown = texts(at.sidebar.caption) + texts(at.sidebar.markdown)
        self.assertNotIn("not legal or semantic ground truth", shown)

    def test_chat_error_is_shown_not_raised(self):
        stub = StubClient()
        stub.raise_on["chat"] = stub.make_error(502, "chat_failed", "The assistant could not produce an answer.")
        at = self.ask(stub)
        self.assertFalse(at.exception)
        self.assertIn("konnte keine Antwort erstellen.", texts(at.sidebar.error))


if __name__ == "__main__":
    unittest.main()
