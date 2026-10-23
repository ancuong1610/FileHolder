"""Independent pre-release source-proof and retrieval-budget counterexamples."""
import copy
import unittest
from unittest.mock import patch

from document_compare.phase1.contracts import semantic_assessment as sa
from document_compare.phase1.matching import residual_proposals as rp
from document_compare.phase1.matching import semantic_evidence as se
from document_compare.phase2.reader import result_reader as reader


def element(identity, text):
    return {"element_id": identity, "text": text, "element_type": "paragraph",
            "primary_page": 1,
            "extraction_validation": {"requires_extraction_review": False}}


class SourceSafetyTest(unittest.TestCase):
    def test_equivalent_mandatory_modals_do_not_prove_materiality(self):
        self.assertIsNone(se.prove_material_delta(
            "The device must retain logs.", "The device shall retain logs."))

    def test_incomplete_or_multiple_clauses_abstain(self):
        for old, new in (
            ("The gateway SHALL NOT", "The gateway SHALL"),
            ("The policy is a draft. Operators SHALL NOT write logs.",
             "The policy is a draft. Operators SHALL write logs."),
        ):
            with self.subTest(old=old):
                self.assertIsNone(se.prove_material_delta(old, new))

    def test_partial_word_not_is_not_a_negation(self):
        self.assertIsNone(se.prove_material_delta(
            "The label is notable.", "The label is able."))

    def test_nonfinite_margin_cannot_verify_correspondence(self):
        old = element("O", "Operators SHALL NOT write logs.")
        new = element("N", "Operators SHALL write logs.")
        for margin in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(margin=margin):
                self.assertFalse(se.independent_correspondence_verified({
                    "old": old, "new": new, "similarity_evidence": {
                        "reciprocal_best": True, "minimum_best_margin": margin}}))

    def test_validator_rejects_a_sliced_locale_numeric_witness(self):
        old = element("O", "The rate is 1,5 percent.")
        new = element("N", "The rate is 2,5 percent.")
        start = old["text"].index("1")
        witness = {
            "kind": "quantity_value",
            "old_span": {"start": start, "end": start + 1, "text": "1"},
            "new_span": {"start": start, "end": start + 1, "text": "2"},
            "old_context": {"prefix": old["text"][:start], "suffix": old["text"][start+1:]},
            "new_context": {"prefix": new["text"][:start], "suffix": new["text"][start+1:]},
            "old_value": "1", "new_value": "2",
            "guards": {"quoted": False, "conditional_or_exception": False,
                       "coordinated_clause": False, "single_changed_atom": True,
                       "complete_context": True},
        }
        claimed = sa.build_assessment(correspondence="verified", relation="material_change",
            basis="deterministic_witness", old_element=old, new_element=new, witness=witness)
        self.assertFalse(sa.validate_semantic_assessment(
            claimed, old_element=old, new_element=new)["valid"])


class ReaderBasisTest(unittest.TestCase):
    def test_one_sided_proposal_preserves_absent_source_side(self):
        old = element("O", "The device must retain logs for 31 days.")
        claimed = se.reassess_residual(old, side="old", correspondence="proposed")
        canonical = {"comparison_stage": "phase_1_final_comparison", "changes": [{
            "change_id": "C1", "change_type": "DELETED", "old": old, "new": None,
            "resolution": {"status": "review", "needs_review": True},
            "semantic_assessment": claimed}], "table_changes": [], "review_queue": [],
            "non_semantic_events": []}
        out = reader.details("a1b2c3d4e5f6", canonical, kind="change", record_id="C1")
        self.assertEqual(out["change"]["semantic_assessment"]["correspondence"], "proposed")
        self.assertIsNone(out["change"]["semantic_assessment"]["new_text_sha256"])

    def test_summary_and_details_use_the_same_source_validated_basis(self):
        old = element("O", "The device must retain logs for 31 days.")
        new = element("N", "The device must retain logs for 62 days.")
        canonical = {"comparison_stage": "phase_1_final_comparison", "changes": [{
            "change_id": "C1", "change_type": "MODIFIED", "old": old, "new": new,
            "resolution": {"status": "accepted", "needs_review": False},
            "semantic_assessment": se.assess_pair(old, new, correspondence="verified")}],
            "table_changes": [], "review_queue": [], "non_semantic_events": []}
        before = copy.deepcopy(canonical)
        summary = reader.summarize("a1b2c3d4e5f6", "done", canonical)
        detail = reader.details("a1b2c3d4e5f6", canonical, kind="change", record_id="C1")
        self.assertEqual(detail["change"]["semantic_assessment"]["basis"], "deterministic_witness")
        self.assertEqual(summary["counts"]["semantic_basis_counts"], {"deterministic_witness": 1})
        self.assertEqual(canonical, before)


class ProposalWorkBudgetTest(unittest.TestCase):
    def candidates(self, side, count):
        return [{"candidate_id": f"{side}-{i:03}", side: {
            **element(f"{side}-E{i}", f"Common operating control clause number {i}."),
            "reading_order": i,
            "provisional_anchor_context": {"recent_anchor_keys": ["shared"]}}}
                for i in range(count)]

    def test_requested_override_cannot_exceed_hard_budget(self):
        result = rp.build_residual_proposals(
            self.candidates("old", 40), self.candidates("new", 40), max_pair_checks=10000)
        self.assertLessEqual(result["coverage"]["pair_checks"], 1000)

    def test_actual_score_work_is_accounted_for(self):
        with patch.object(rp, "_score", wraps=rp._score) as score:
            result = rp.build_residual_proposals(
                self.candidates("old", 40), self.candidates("new", 40))
            self.assertLessEqual(score.call_count, 1000)
            self.assertGreaterEqual(result["coverage"]["pair_checks"], score.call_count)

    def test_unindexed_compatible_residuals_remain_unsearched(self):
        old = [{"candidate_id": "D", "old": element("O", "Unique amber fragment.")}]
        new = [{"candidate_id": "A1", "new": element("N1", "Unique amber passage.")},
               {"candidate_id": "A2", "new": element("N2", "Different cobalt directive.")}]
        result = rp.build_residual_proposals(old, new)
        coverage = result["candidate_coverage"]["D"]
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["unsearched"], 1)


if __name__ == "__main__":
    unittest.main()
