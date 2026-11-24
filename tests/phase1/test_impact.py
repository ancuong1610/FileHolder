"""Offline tests for the German-first impact axis and its canonical wiring."""

import unittest

from document_compare.phase1.matching.impact import (
    IMPACT_VERSION,
    classify_impact,
    editorial_tokens,
    glyph_fold,
)
from document_compare.phase1.stages import s08_candidates, s11_routing, s14_canonical_result
from document_compare.phase1.stages.s10_tables import compare_cell


class ImpactClassifierTest(unittest.TestCase):
    def test_glyph_equivalence(self):
        for old, new, kind in (
            ("Daten\u200bschutz", "Datenschutz", "invisible_character"),
            ("ﬁnanziell", "finanziell", "ligature_or_compatibility_form"),
            ("Pru\u0308fung", "Prüfung", "unicode_composition"),
            ("10\xa0Tage", "10 Tage", "whitespace"),
            ("• Der Vorstand", "Der Vorstand", "bullet_glyph"),
            ("Auf\u00adsicht", "Aufsicht", "invisible_character"),
            ("„Bericht“ – alt", '"Bericht" - alt', "quote_or_dash_variant"),
            ("Bericht", "Bericht", "identical"),
        ):
            with self.subTest(old=old, new=new):
                result = classify_impact(old, new)
                self.assertEqual(glyph_fold(old), glyph_fold(new))
                self.assertEqual((result["level"], result["priority"]), ("non_content", "low"))
                self.assertIn(kind, result["kinds"])
                self.assertEqual(result["signals"], [])

    def test_empty_inputs_and_impact_shape(self):
        self.assertEqual(
            classify_impact(None, None),
            {
                "version": IMPACT_VERSION,
                "level": "non_content",
                "priority": "low",
                "kinds": ["identical"],
                "signals": [],
                "basis": "deterministic_rule",
                "language_profile": "de-en",
            },
        )
        self.assertEqual(classify_impact("", " \t")["level"], "non_content")
        self.assertEqual(classify_impact(" \t", "Text")["signals"], ["added_text"])
        self.assertEqual(classify_impact("Text", "")["signals"], ["deleted_text"])

    def test_editorial_equivalence(self):
        for old, new, kind in (
            ("Der Bericht ist fertig.", "Der Bericht ist fertig", "punctuation"),
            (
                "Die Bank, sofern verpflichtet, muss berichten.",
                "Die Bank sofern verpflichtet muss berichten.",
                "punctuation",
            ),
            ("Bericht", "bericht", "letter_case"),
            ("daß", "dass", "orthography"),
            ("vier Wochen", "4 Wochen", "number_format"),
            ("Mitarbeiter*innen", "MitarbeiterInnen", "gender_form"),
            ("Mitarbeiter/-innen", "Mitarbeiter:innen", "gender_form"),
            ("1.000 EUR", "1000 EUR", "number_format"),
            ("10 %", "10 Prozent", "number_format"),
            ("10 v.H.", "10 vH", "number_format"),
        ):
            with self.subTest(old=old, new=new):
                result = classify_impact(old, new)
                self.assertEqual(editorial_tokens(old), editorial_tokens(new))
                self.assertEqual((result["level"], result["priority"]), ("editorial", "low"))
                self.assertIn(kind, result["kinds"])
                self.assertEqual(result["signals"], [])

    def test_sharp_s_and_numeric_operators_keep_material_distinctions(self):
        for old, new in (("daß", "dass"), ("muß", "muss"), ("läßt", "lässt"),
                         ("Schlußfolgerung", "Schlussfolgerung")):
            with self.subTest(old=old):
                self.assertEqual(classify_impact(old, new)["level"], "editorial")
        self.assertEqual(classify_impact("in Maßen", "in Massen")["level"], "content")
        for value in ("1/2", "1:2", "1-2", "1–2", "1x2", "1×2", "1 x 2"):
            with self.subTest(value=value):
                impact = classify_impact(value + " mg", "1 2 mg")
                self.assertEqual(impact["level"], "content")
                self.assertIn("number", impact["signals"])

    def test_pure_bullets_fold_but_state_markers_and_signed_values_survive(self):
        for glyph in "•◦▪‣⁃·":
            self.assertEqual(glyph_fold(f"{glyph} Freigabe"), "Freigabe")
        for glyph in "□■☐☑☒✓✔✗✘●○▫":
            with self.subTest(glyph=glyph):
                self.assertEqual(glyph_fold(f"{glyph} Freigabe"), f"{glyph} Freigabe")
                self.assertIn("state_marker", classify_impact(f"{glyph} Freigabe", "Freigabe")["signals"])
        for text in ("- 5 %", "- %", "- €", "- EUR 5", "- USD 5", "- £5", "- US$5"):
            with self.subTest(text=text):
                self.assertEqual(glyph_fold(text), text)
                self.assertEqual(classify_impact(text, text[2:])["level"], "content")
        self.assertEqual(glyph_fold("- Freigabe"), "Freigabe")
        self.assertEqual(glyph_fold("– Freigabe"), "- Freigabe")

    def test_german_modal_inflections_signal_insertion_and_removal(self):
        for modal in ("müsste müssten musste mussten sollst solltest dürften durfte "
                      "durften konnte konnten könne müsse solle dürfe").split():
            with self.subTest(modal=modal):
                sentence = f"Die Bank {modal} unverändert berichten."
                for old, new, signal in (
                    ("Die Bank unverändert berichten.", sentence, "added_text"),
                    (sentence, "Die Bank unverändert berichten.", "deleted_text"),
                    (sentence, "Die Bank wird unverändert berichten.", "replaced_text"),
                ):
                    impact = classify_impact(old, new)
                    self.assertEqual((impact["level"], impact["priority"]), ("content", "high"))
                    self.assertLessEqual({"modal", signal}, set(impact["signals"]))

    def test_content_signals(self):
        for old, new, signals in (
            ("muss", "kann", {"modal", "replaced_text"}),
            ("soll", "muss", {"modal"}),
            ("Die Bank meldet.", "Die Bank meldet nicht.", {"negation", "added_text"}),
            ("keine", "eine", {"negation"}),
            ("30 Tage", "60 Tage", {"number"}),
            ("vier", "fünf", {"number"}),
            ("31.12.2025", "30.06.2026", {"date", "number"}),
            ("2025-12-31", "2026-06-30", {"date", "number"}),
            ("§ 5 Abs. 2", "§ 5 Abs. 3", {"reference", "number"}),
            ("Buchst. a", "Buchst. b", {"reference"}),
            ("Abs. 2 a", "Abs. 2 b", {"reference"}),
            ("Melden, sofern fällig.", "Melden.", {"condition", "deleted_text"}),
            ("mindestens", "höchstens", {"qualifier"}),
            ("1.000,50", "1.000,60", {"number"}),
            ("shall", "may", {"modal"}),
            (None, "Die Bank muss einen Bericht einreichen.", {"modal", "added_text"}),
        ):
            with self.subTest(old=old, new=new):
                result = classify_impact(old, new)
                self.assertEqual((result["level"], result["priority"]), ("content", "high"))
                self.assertLessEqual(signals, set(result["signals"]))
                self.assertEqual(result["signals"], sorted(set(result["signals"])))

    def test_normal_priority_content(self):
        for old, new, signal in (
            ("Vorstand", "Aufsichtsrat", "replaced_text"),
            ("Der Vorstand muss melden.", "Der Aufsichtsrat muss melden.", "replaced_text"),
            ("Der Bericht liegt vor.", None, "deleted_text"),
        ):
            with self.subTest(old=old, new=new):
                result = classify_impact(old, new)
                self.assertEqual((result["level"], result["priority"]), ("content", "normal"))
                self.assertEqual(result["signals"], [signal])

    def test_preserves_ambiguous_values_and_articles(self):
        self.assertEqual(
            editorial_tokens("1.5 1,000 1.000,50 1.5.26 2025-12-31"),
            ["1.5", "1,000", "#1000.5", "@date:1.5.26", "@date:2025-12-31"],
        )
        self.assertEqual(
            editorial_tokens("ein eine einen einem einer one eins § 5a -10"),
            ["ein", "eine", "einen", "einem", "einer", "one", "#1", "§", "5a", "#-10"],
        )
        self.assertEqual(classify_impact("1,000", "1000")["level"], "content")
        self.assertEqual(classify_impact("-10", "10")["signals"], ["number", "replaced_text"])
        self.assertEqual(
            classify_impact("Kapitel mit 5 Regeln", "Kapitel mit 6 Regeln")["signals"],
            ["number", "replaced_text"],
        )


def _candidate(candidate_id, old, new, *, source=None):
    old_raw, new_raw = source if source is not None else (old, new)
    return {
        "candidate_id": candidate_id,
        "old": {
            "element_id": f"OLD-{candidate_id}",
            "text": old_raw,
            "normalized_text": old,
            "extraction_validation": {"requires_extraction_review": False},
        },
        "new": {
            "element_id": f"NEW-{candidate_id}",
            "text": new_raw,
            "normalized_text": new,
            "extraction_validation": {"requires_extraction_review": False},
        },
        "token_diff": {
            "source_token_diff": s08_candidates.build_token_diff(old_raw, new_raw),
            "normalized_token_diff": s08_candidates.build_token_diff(old, new),
            "comparison_token_diff": s08_candidates.build_token_diff(old, new),
        },
    }


def _canonical(*candidates, tables=None):
    movement = {
        "comparison_stage": s14_canonical_result.EXPECTED_STEP9_STAGE,
        "modified": list(candidates),
    }
    tables = {"comparison_stage": s14_canonical_result.EXPECTED_STEP10_STAGE, **(tables or {})}
    routing = {
        "comparison_stage": s14_canonical_result.EXPECTED_STEP11_STAGE,
        **s11_routing.build_generic_routing(movement, tables),
    }
    return s14_canonical_result.build_comparison_result(movement, tables, routing)


class CanonicalImpactTest(unittest.TestCase):
    def test_routing_and_statistics(self):
        glyph = _candidate("glyph", "Daten\u200bschutz gilt.", "Datenschutz gilt.")
        protected = _candidate(
            "protected",
            "Daten\u200bschutz gilt für 30 Tage.",
            "Datenschutz gilt für 30 Tage.",
            source=("Daten\u200bschutz gilt für 30 Tage.", "Datenschutz gilt für 60 Tage."),
        )
        editorial = _candidate("editorial", "Der Bericht ist fertig.", "Der Bericht ist fertig")
        modal = _candidate("modal", "Die Bank muss melden.", "Die Bank kann melden.")
        self.assertEqual(
            s11_routing.route_non_table("modified", glyph),
            (
                "SPECIAL_CASE_RULE",
                "glyph_or_invisible_char_artifact",
                ["impact_non_content_glyph_fold_equal"],
            ),
        )
        self.assertTrue(s11_routing.representation_semantic_signal_tokens(protected))
        self.assertNotEqual(
            s11_routing.route_non_table("modified", protected)[1],
            "glyph_or_invisible_char_artifact",
        )

        canonical = _canonical(glyph, protected, editorial, modal)
        changes = {item["source_candidate_id"]: item for item in canonical["changes"]}
        self.assertEqual(set(changes), {"protected", "editorial", "modal"})
        self.assertEqual(changes["editorial"]["impact"]["level"], "editorial")
        self.assertEqual(changes["modal"]["impact"]["level"], "content")
        self.assertIn("modal", changes["modal"]["impact"]["signals"])
        event, = canonical["non_semantic_events"]
        self.assertEqual(event["source_candidate_id"], "glyph")
        self.assertEqual(event["resolution"]["status"], "suppressed_artifact")
        self.assertEqual(event["impact"]["level"], "non_content")
        self.assertIn("invisible_character", event["impact"]["kinds"])
        self.assertEqual(event["old"]["text"], glyph["old"]["text"])
        stats = canonical["statistics"]
        self.assertEqual(stats["impact_level_counts"], {"non_content": 1, "editorial": 1, "content": 1})
        self.assertEqual(stats["impact_priority_counts"], {"low": 2, "high": 1})
        self.assertEqual(stats["table_impact_level_counts"], {})

    def test_extraction_review_blocks_glyph_suppression(self):
        candidate = _candidate("glyph", "Daten\u200bschutz", "Datenschutz")
        candidate["old"]["extraction_validation"]["requires_extraction_review"] = True
        canonical = _canonical(candidate)
        self.assertEqual(canonical["non_semantic_events"], [])
        change, = canonical["changes"]
        self.assertTrue(change["resolution"]["needs_review"])
        self.assertEqual(change["impact"]["level"], "non_content")

    def test_raw_state_and_sign_changes_stay_visible(self):
        checkbox = _candidate("state", "□ Freigabe erteilt", "■ Freigabe erteilt")
        minus = _candidate("minus", "- 5 %", "5 %")
        raw_only = _candidate(
            "raw_sign", "• Abschlag beträgt 5 %", "Abschlag beträgt 5 %",
            source=("Abschlag beträgt - 5 %", "Abschlag beträgt 5 %"),
        )
        raw_only["comparison_token_equivalent"] = True
        result = _canonical(checkbox, minus, raw_only)
        changes = {item["source_candidate_id"]: item for item in result["changes"]}
        self.assertEqual(set(changes), {"state", "minus", "raw_sign"})
        self.assertIn("state_marker", changes["state"]["impact"]["signals"])
        self.assertTrue(changes["raw_sign"]["resolution"]["needs_review"])

        movement = {"comparison_stage": s14_canonical_result.EXPECTED_STEP9_STAGE, "modified": [checkbox]}
        tables = {"comparison_stage": s14_canonical_result.EXPECTED_STEP10_STAGE}
        routing = {
            **s11_routing.build_generic_routing(movement, tables),
            "comparison_stage": s14_canonical_result.EXPECTED_STEP11_STAGE,
        }
        routing["routes"][0].update(route="SPECIAL_CASE_RULE", case_type="glyph_or_invisible_char_artifact")
        stale = s14_canonical_result.build_comparison_result(movement, tables, routing)
        self.assertEqual(stale["non_semantic_events"], [])
        self.assertIn("raw_glyph_signature_changed", stale["changes"][0]["resolution"]["review_reasons"])

    def test_existing_wrap_disposition_sets_impact(self):
        wrap = _candidate("wrap", "gehandhabt", "ge- handhabt", source=("ge-\nhandhabt", "ge- handhabt"))
        canonical = _canonical(wrap)
        self.assertEqual(canonical["changes"], [])
        event, = canonical["non_semantic_events"]
        self.assertEqual(event["resolution"]["case_type"], "hyphenation_wrap_artifact")
        self.assertEqual(event["impact"]["level"], "non_content")
        self.assertEqual(event["impact"]["priority"], "low")
        self.assertIn("deterministic_artifact_rule", event["impact"]["kinds"])

    def test_table_span_and_membership_changes_have_content_impact(self):
        old = {"text": "Status", "column_span": 1}
        new = {"text": "Status", "column_span": 2}
        span = compare_cell(old, new)
        self.assertEqual(span["classification"], "STRUCTURE_CHANGED_REVIEW")
        for classification, cells, counts, priority in (
            ("STRUCTURE_CHANGED_REVIEW", [span], (2, 2), "normal"),
            ("MODIFIED", [compare_cell(None, {"text": "Status"})], (2, 2), "normal"),
            ("UNCHANGED", [compare_cell(old, old)], (2, 3), "normal"),
            ("MODIFIED", [span, compare_cell({"text": "30 Tage"}, {"text": "60 Tage"})], (2, 2), "high"),
        ):
            with self.subTest(classification=classification, priority=priority):
                table = {
                    "table_match_id": "status",
                    "classification": classification,
                    "old": {"column_count": counts[0]},
                    "new": {"column_count": counts[1]},
                    "needs_review": True,
                    "cell_changes": cells,
                }
                result = _canonical(tables={"table_matches": [table]})
                change, = result["table_changes"]
                impact = change["impact"]
                self.assertEqual((impact["level"], impact["priority"]), ("content", priority))
                self.assertIn("table_structure_changed", impact["kinds"])

    def test_table_impact_aggregates_cells_and_keeps_layout_review(self):
        def cell(old, new):
            return compare_cell({"text": old}, {"text": new})

        tables = {
            "table_matches": [
                {
                    "table_match_id": "changed",
                    "classification": "MODIFIED",
                    "cell_changes": [
                        cell("Vorstand", "Aufsichtsrat"),
                        cell("muss", "kann"),
                        cell("30 Tage", "60 Tage"),
                        cell("Bericht.", "Bericht"),
                        cell("unverändert", "unverändert"),
                    ],
                },
                {
                    "table_match_id": "layout",
                    "classification": "LAYOUT_EQUIVALENT",
                    "needs_review": True,
                },
                {"table_match_id": "unchanged", "classification": "UNCHANGED"},
            ],
            "added_table_candidates": [
                {
                    "logical_table_id": "added",
                    "cell_changes": [compare_cell(None, {"text": "Die Bank muss melden."})],
                },
            ],
            "deleted_table_candidates": [{"logical_table_id": "unavailable"}],
        }
        result = _canonical(tables=tables)
        impacts = {
            item["source_table_match_id"]: item["impact"] for item in result["table_changes"]
        }
        self.assertEqual(set(impacts), {"changed", "layout", "added", "unavailable"})
        self.assertEqual(impacts["changed"]["level"], "content")
        self.assertEqual(impacts["changed"]["priority"], "high")
        self.assertEqual(impacts["changed"]["signals"], ["modal", "number", "replaced_text"])
        self.assertEqual(impacts["changed"]["kinds"], ["punctuation"])
        self.assertEqual(impacts["layout"]["level"], "non_content")
        self.assertEqual(impacts["layout"]["kinds"], ["table_layout_equivalent"])
        self.assertEqual(impacts["added"]["signals"], ["added_text", "modal"])
        self.assertEqual(impacts["unavailable"]["level"], "content")
        self.assertEqual(impacts["unavailable"]["priority"], "normal")
        self.assertEqual(impacts["unavailable"]["kinds"], ["table_change_unclassified"])
        self.assertEqual(
            result["statistics"]["table_impact_level_counts"], {"content": 3, "non_content": 1}
        )


if __name__ == "__main__":
    unittest.main()
