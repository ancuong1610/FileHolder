"""Synthetic regression tests for the comparison agent's graph and event projection."""
import unittest
from dataclasses import asdict

from document_compare.phase2.agent.controller import TiaComparisonAgent
from document_compare.phase2.agent.help_de import HELP_DE
from document_compare.phase2.agent.observations import planner_observation
from document_compare.phase2.reader.result_reader import ReaderError
from tests.phase2.test_agent_reader import CapturePlanner, JOB, _RecordingGateway


RECORD = {"kind": "change", "record_id": "CHANGE-000001"}


class SearchGateway(_RecordingGateway):
    def __init__(self, hits=(), error=None):
        super().__init__()
        self.hits = hits
        self.error = error

    def search(self, job_id, **filters):
        super().search(job_id, **filters)
        if self.error is not None:
            raise self.error
        returned = [RECORD] if filters["query"] in self.hits else []
        return {"total_matches": len(returned), "returned": returned}


def _search_planner(query, **filters):
    def strategy(observations):
        if not observations:
            return {"action": "search", "arguments": {"query": query, **filters}}
        return {"action": "final", "answer": "Frist in CHANGE-000001."}
    return CapturePlanner(strategy)


class AgentGraphTest(unittest.TestCase):
    def test_stream_yields_only_projected_step_fields_before_each_tool_and_final_last(self):
        query = "Q" * 150
        decisions = [
            {"action": "summarize", "arguments": {"query": "internal", "job_id": "other"}},
            {"action": "search", "arguments": {"query": query, "status": "accepted"}},
            {"action": "details", "arguments": {**RECORD, "context_window": 2}},
            {"action": "final", "answer": "CHANGE-000001 und CHANGE-999999."},
        ]
        gateway = SearchGateway()
        agent = TiaComparisonAgent(CapturePlanner(lambda obs: decisions[len(obs)]), gateway, max_steps=4)
        stream = agent.stream("Was steht dort?", JOB)
        expected_steps = [
            {"type": "step", "step": 1, "action": "summarize", "query": None, "record_id": None},
            {"type": "step", "step": 2, "action": "search", "query": query[:120], "record_id": None},
            {"type": "step", "step": 3, "action": "details", "query": None, "record_id": RECORD["record_id"]},
        ]
        for index, expected in enumerate(expected_steps):
            self.assertEqual(next(stream), expected)
            self.assertEqual(len(gateway.calls), index)
        self.assertEqual(gateway.calls[0], ("summarize", {"job_id": JOB}))
        final, = stream
        self.assertEqual([action for action, _ in gateway.calls], ["summarize", "search", "details"])
        self.assertEqual(final, {
            "type": "final", "answer": decisions[-1]["answer"], "cited": [RECORD],
            "unverified_ids": ["CHANGE-999999"], "partial": True, "suggestions": [],
        })
        self.assertEqual(final, {"type": "final", **asdict(agent.ask("Was steht dort?", JOB))})

    def test_stream_step_limit_does_not_execute_or_emit_unallowed_action(self):
        gateway = SearchGateway()
        planner = CapturePlanner(lambda obs: {"action": "summarize"})
        stream = TiaComparisonAgent(planner, gateway, max_steps=2).stream("Was ist anders?", JOB)
        self.assertEqual(next(stream)["type"], "step")
        with self.assertRaises(RuntimeError) as ctx:
            next(stream)
        self.assertEqual(str(ctx.exception), "Agent reached its step limit without producing a final answer.")
        self.assertEqual(len(gateway.calls), 1)
        self.assertIn("FINAL_STEP: true", planner.prompts[-1][0])

    def test_invalid_planner_responses_keep_existing_errors(self):
        class RawPlanner:
            def __init__(self, raw):
                self.raw = raw

            def invoke(self, prompt, system_message):
                return self.raw

        cases = [
            ("not JSON", "TIA planner returned non-JSON output: 'not JSON'"),
            ("{}", "TIA planner response must contain a string action. Raw response: '{}'"),
            ('{"action":"final","answer":" "}', "TIA final action has no answer."),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError) as ctx:
                next(TiaComparisonAgent(RawPlanner(raw), SearchGateway()).stream("Was ist anders?", JOB))
            self.assertEqual(str(ctx.exception), expected)

    def test_fallback_stops_at_first_hit_and_cites_within_same_planner_step(self):
        query = "Fristen geändert"
        gateway = SearchGateway(hits={"Frist"})
        planner = _search_planner(query, kind="change", status="accepted", job_id="other")
        stream = TiaComparisonAgent(planner, gateway, max_steps=2).stream("Welche Frist?", JOB)
        self.assertEqual(next(stream)["query"], query)
        self.assertEqual(next(stream), {"type": "fallback", "query": query, "variant": "Frist"})
        self.assertEqual(len(planner.prompts), 1)
        final, = stream
        self.assertEqual(final["cited"], [RECORD])
        self.assertTrue(final["partial"])
        self.assertEqual(len(planner.prompts), 2)
        self.assertIn("FINAL_STEP: true", planner.prompts[-1][0])
        self.assertEqual([arguments["query"] for _, arguments in gateway.calls], [query, "Fristen", "Frist"])
        filters = {k: v for k, v in gateway.calls[0][1].items() if k != "query"}
        self.assertEqual(filters["job_id"], JOB)
        for name, arguments in gateway.calls:
            self.assertEqual(name, "search")
            self.assertEqual({k: v for k, v in arguments.items() if k != "query"}, filters)
        original, fallback = planner.seen_observations
        self.assertEqual(original["result"], {"total_matches": 0, "returned": []})
        self.assertEqual(fallback, {
            "action": "search_fallback",
            "arguments": {"job_id": JOB, "kind": "change", "status": "accepted", "query": "Frist"},
            "original_query": query, "variants_tried": ["Fristen", "Frist"],
            "result": {"total_matches": 1, "returned": [RECORD]},
        })

    def test_fallback_runs_at_most_three_alternatives_and_keeps_last_result(self):
        class NoHits(SearchGateway):
            def search(self, job_id, **filters):
                return {**super().search(job_id, **filters), "query": filters["query"]}

        gateway = NoHits()
        query = "Fristen Meldungen NEU"
        planner = _search_planner(query)
        events = list(TiaComparisonAgent(planner, gateway, max_steps=2).stream("Was wurde geändert?", JOB))
        self.assertEqual([event["type"] for event in events], ["step", "final"])
        self.assertEqual([args["query"] for _, args in gateway.calls], [
            query, "Fristen Meldungen", "Frist Meld", "Meld",
        ])
        self.assertEqual(len(planner.seen_observations), 2)
        fallback = planner.seen_observations[-1]
        self.assertEqual(fallback["variants_tried"], ["Fristen Meldungen", "Frist Meld", "Meld"])
        self.assertEqual(fallback["arguments"]["query"], "Meld")
        self.assertEqual(fallback["result"], {"total_matches": 0, "returned": [], "query": "Meld"})
        self.assertEqual(events[-1]["unverified_ids"], [RECORD["record_id"]])

    def test_ineligible_searches_do_not_retry(self):
        query = "Fristen geändert"
        cases = [
            (query, {"cursor": "page-token"}, SearchGateway()),
            ("", {}, SearchGateway()),
            ("  ", {}, SearchGateway()),
            (query, {}, SearchGateway(hits={query})),
            (query, {}, SearchGateway(error=ReaderError("not_ready", "synthetic job is running"))),
            (query, {}, SearchGateway(error=RuntimeError("synthetic tool failure"))),
            ("geändert NEU", {}, SearchGateway()),
        ]
        for question, filters, gateway in cases:
            with self.subTest(query=question, filters=filters, error=gateway.error):
                planner = _search_planner(question, **filters)
                events = list(TiaComparisonAgent(planner, gateway, max_steps=2).stream("Welche Frist?", JOB))
                self.assertEqual(len(gateway.calls), 1)
                self.assertEqual(len(planner.seen_observations), 1)
                self.assertEqual([event["type"] for event in events], ["step", "final"])

    def test_selected_details_are_available_on_first_plan_and_stream_as_step_zero(self):
        gateway = SearchGateway()
        planner = CapturePlanner(lambda obs: {"action": "final", "answer": "**CHANGE-000001**"})
        agent = TiaComparisonAgent(planner, gateway, max_steps=2)
        events = list(agent.stream("Erklär diese Änderung.", JOB, ui_context={"selected_record_id": RECORD["record_id"]}))
        self.assertEqual([(action, args["kind"], args["record_id"]) for action, args in gateway.calls],
                         [("details", "change", RECORD["record_id"])])
        self.assertEqual(len(planner.prompts), 1)
        self.assertIn("FINAL_STEP: false", planner.prompts[0][0])
        self.assertEqual(planner.seen_observations[0]["result"], RECORD)
        self.assertEqual(events[0], {"type": "step", "step": 0, "action": "details",
                                     "query": None, "record_id": RECORD["record_id"]})
        self.assertEqual(events[1]["cited"], [RECORD])
        self.assertFalse(events[1]["partial"])

    def test_prepare_skips_help_no_selection_other_id_and_unrelated_topic(self):
        selected = {"selected_record_id": RECORD["record_id"], "selected_kind": "change"}
        for question, context in (
            ("Was bedeutet das?", selected), ("Wie funktioniert das?", selected),
            ("What does this mean?", selected), ("Was heißt diese gestrichelte Linie?", selected),
            ("Erklär diese Änderung.", None), ("Erklär diese Änderung CHANGE-000002.", selected),
            ("Welche Fristen wurden geändert?", selected),
        ):
            with self.subTest(question=question):
                gateway = SearchGateway()
                planner = CapturePlanner(lambda obs: {"action": "final", "answer": "Antwort."})
                TiaComparisonAgent(planner, gateway).ask(question, JOB, ui_context=context)
                self.assertEqual(gateway.calls, [])
                self.assertEqual(planner.seen_observations, [])

    def test_answer_check_retries_once_without_a_step_event(self):
        for wrong, problem in (
            ("**CHANGE-000099**", "Cites unverified record IDs."),
            ("Die Frist wurde geändert.", "Cite the observed finding IDs for comparison claims."),
            (' {"record_id":"CHANGE-000001"}', "Answer contains a raw JSON or tool dump."),
        ):
            with self.subTest(wrong=wrong):
                def strategy(observations):
                    if not observations:
                        return {"action": "details", "arguments": RECORD}
                    answer = "**CHANGE-000001**" if observations[-1]["action"] == "answer_check" else wrong
                    return {"action": "final", "answer": answer}

                planner = CapturePlanner(strategy)
                events = list(TiaComparisonAgent(planner, SearchGateway()).stream("Welche Frist?", JOB))
                self.assertEqual(len(planner.prompts), 3)
                self.assertEqual(planner.seen_observations[-1], {"action": "answer_check", "problems": [problem]})
                self.assertEqual([event["type"] for event in events], ["step", "final"])
                self.assertEqual(events[-1]["cited"], [RECORD])

    def test_answer_check_does_not_repeat_or_exceed_planner_budget(self):
        for max_steps, expected_calls in ((1, 1), (5, 2)):
            with self.subTest(max_steps=max_steps):
                planner = CapturePlanner(lambda obs: {"action": "final", "answer": "**CHANGE-999999**"})
                result = TiaComparisonAgent(planner, SearchGateway(), max_steps=max_steps).ask("Welche Frist?", JOB)
                self.assertEqual(len(planner.prompts), expected_calls)
                self.assertEqual(len(planner.seen_observations), expected_calls - 1)
                self.assertEqual(result.unverified_ids, ["CHANGE-999999"])

    def test_suggestions_are_plain_and_bounded(self):
        suggestions = [None, 32, "<b>text</b>", "&lt;a&gt;", "[Website](https://example.org)",
                       "example.org/path", "   ", "  Welche\nFrist?  ", "W" * 121, "Offen?", "Vierte?"]
        planner = CapturePlanner(lambda obs: {"action": "final", "answer": "Antwort.", "suggestions": suggestions})
        result = TiaComparisonAgent(planner, SearchGateway()).ask("Welche Frist?", JOB)
        self.assertEqual(result.suggestions, ["Welche Frist?", "W" * 120, "Offen?"])

    def test_playbook_retains_numbered_rules_and_help(self):
        planner = CapturePlanner(lambda obs: {"action": "final", "answer": "Antwort."})
        TiaComparisonAgent(planner, SearchGateway()).ask("Wie funktioniert die Gliederung?", JOB)
        system = planner.prompts[0][1]
        self.assertIn("ANSWER PLAYBOOK", system)
        self.assertIn(HELP_DE, system)
        for number in range(1, 16):
            self.assertIn(f"\n{number}. ", system)
        for phrase in ("S. x (Alt)", "S. y (Neu)", "Seite N vollständig gelöscht (Alt)",
                       "gestrichelte Linie: hier entfernt", "gestrichelte Linie: hier eingefügt",
                       "Abschnitten des neuen Dokuments", "Statuszeilen"):
            self.assertIn(phrase, HELP_DE)


class ObservationTest(unittest.TestCase):
    def test_details_projection_preserves_evidence_and_full_citation_ids(self):
        op = {"operation": "replace", "old_text": "5 %", "new_text": "7,5 %",
              "old_tokens": ["<clipped>"], "new_tokens": "<clipped>",
              "old_context_before": ["<clipped>"], "new_context_after": ["<clipped>"]}
        impact = {"level": "content", "priority": "high", "signals": ["numeric"]}
        protected = {"text_next_offset": 100, "next_offset": 2, "next_cursor": "cursor", "truncated": True,
                     "result_state": "uncertain", "status": "review", "confirmed": False, "needs_review": True,
                     "warnings": ["context_unavailable"], "search_coverage": {"complete": False},
                     "error": "synthetic_error", "message": "Synthetic message."}
        assessment = {key: "hidden" for key in ("old_text_sha256", "new_text_sha256", "guards", "schema_version",
                                                "policy_version")}
        change = {"change_id": RECORD["record_id"], "change_type": "MODIFIED", "impact": impact,
                  "old": {"primary_page": 3, "pages": [3], "text": "1.000 EUR, 5 %, 01.10.2026, 2027."},
                  "new": {"primary_page": 4, "pages": [4], "text": "1.000 EUR, 7,5 %, 2026-10-02, 2027."},
                  "token_diff": {"comparison_token_diff": {"changes": [op] * 16}},
                  "semantic_assessment": {**assessment, "verdict": "uncertain"},
                  "provenance": {"record_id": "MOVE-000002"}, **protected}
        raw = {**RECORD, "change": change, "snapshot": "omitted", "limits": {}, "canonical_reference": {},
               "ground_truth_notice": "omitted"}

        class Gateway(SearchGateway):
            def details(self, *args, **kwargs):
                return raw

        def strategy(observations):
            if not observations:
                return {"action": "details", "arguments": RECORD}
            return {"action": "final", "answer": "**CHANGE-000001**, **MOVE-000002**"}

        planner = CapturePlanner(strategy)
        answer = TiaComparisonAgent(planner, Gateway()).ask("Welche Frist?", JOB)
        observation, = planner.seen_observations
        self.assertEqual(answer.cited, [RECORD, {"kind": "move", "record_id": "MOVE-000002"}])
        result = observation["result"]
        self.assertEqual(set(result), {*RECORD, "change"})
        slim = result["change"]
        self.assertEqual({k: slim[k] for k in protected}, protected)
        for key in ("old", "new", "impact", "change_id"):
            self.assertEqual(slim[key], change[key])
        self.assertEqual(slim["semantic_assessment"], {"verdict": "uncertain"})
        self.assertNotIn("provenance", slim)
        self.assertEqual(slim["token_diff"]["comparison_token_diff"]["changes"][0],
                         {"operation": "replace", "old_text": "5 %", "new_text": "7,5 %"})
        self.assertIn("provenance", raw["change"])
        self.assertEqual(observation["change_brief"], {
            "type": "MODIFIED", "pages": {"old": 3, "new": 4}, "impact": impact, "needs_review": True,
            "edits": [{"op": "replace", "old": "5 %", "new": "7,5 %"}] * 15, "edits_truncated": True,
            "numbers": {"old": ["5 %", "01.10.2026"], "new": ["7,5 %", "2026-10-02"]},
        })

    def test_block_briefs_cover_at_most_five_members_with_text(self):
        members = [{"change_id": "CHANGE-000001", "primary_page": 1}] + [
            {"change_id": f"CHANGE-{i:06d}", "primary_page": 2, "text": "10 %", "needs_review": True}
            for i in range(2, 8)
        ]
        block = {"side": "old", "change_type": "DELETED", "members": members, "next_source_offset": 7}
        projected = planner_observation({"action": "details", "result": {"block": block}})
        self.assertEqual(projected["result"]["block"], block)
        briefs = projected["member_change_briefs"]
        self.assertEqual(list(briefs), [member["change_id"] for member in members[1:6]])
        self.assertEqual(briefs["CHANGE-000002"], {
            "type": "DELETED", "pages": {"old": 2, "new": None},
            "impact": {"level": None, "priority": None, "signals": []}, "needs_review": True,
            "edits": [], "edits_truncated": False, "numbers": {"old": ["10 %"], "new": []},
        })
