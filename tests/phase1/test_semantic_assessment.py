"""Gate A semantic assessment: source binding, witness and abstention tests.

These tests pin the boundary between source *correspondence* and material
*meaning* proof.  A witness is only authoritative when it is a deterministic
proof over the actual old/new source text; a model advisory, a boolean
attestation, a stale hash or an offset/context mismatch can never become
authority.

The deterministic witness is deliberately narrow: one polarity/modal atom or
one unambiguous numeric/threshold atom over an otherwise byte-identical clause.
Quoted text, conditions, exceptions, coordinated clauses, ambiguous number
formats and unit-only edits abstain.
"""
from __future__ import annotations

import copy
import unittest

from document_compare.phase1.contracts import semantic_assessment as sa
from document_compare.phase1.contracts.result_state import derive_result_state
from document_compare.phase1.matching import semantic_evidence as se


def _element(element_id: str, text: str, element_type: str = "paragraph") -> dict:
    return {
        "element_id": element_id,
        "element_type": element_type,
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "reading_order": 1,
        "primary_page": 1,
        "extraction_validation": {"requires_extraction_review": False},
    }


class LegacyCompatibilityTest(unittest.TestCase):
    def test_missing_assessment_reads_not_assessed_and_keeps_legacy_status(self):
        old = _element("OLD-1", "The operator must record the result.")
        new = _element("NEW-1", "The operator must record the result.")
        normalized = sa.normalize_assessment(None, old_element=old, new_element=new)
        self.assertEqual(normalized["correspondence"], "not_applicable")
        self.assertEqual(normalized["relation"], "undetermined")
        self.assertEqual(normalized["basis"], "not_assessed")
        # Legacy deterministic status is not rewritten by the optional field.
        self.assertEqual(derive_result_state("accepted", False), "supported")

    def test_new_assessment_carries_versions_and_no_invented_confidence(self):
        old = _element("OLD-1", "Operators SHALL NOT store tokens in logs.")
        new = _element("NEW-1", "Operators SHALL store tokens in logs.")
        assessment = se.assess_pair(old, new, correspondence="verified")
        self.assertEqual(assessment["schema_version"], sa.ASSESSMENT_SCHEMA_VERSION)
        self.assertEqual(assessment["policy_version"], sa.SEMANTIC_POLICY_VERSION)
        self.assertNotIn("confidence", assessment)
        self.assertNotIn("confidence", assessment["witness"])
        self.assertEqual(assessment["basis"], "deterministic_witness")


class SourceBindingValidationTest(unittest.TestCase):
    def setUp(self):
        self.old = _element("OLD-1", "Operators SHALL NOT store tokens in logs.")
        self.new = _element("NEW-1", "Operators SHALL store tokens in logs.")
        self.assessment = se.assess_pair(self.old, self.new, correspondence="verified")

    def _valid(self, assessment, old=None, new=None):
        return sa.validate_semantic_assessment(
            assessment,
            old_element=old if old is not None else self.old,
            new_element=new if new is not None else self.new,
        )

    def test_valid_assessment_passes_and_is_not_mutated(self):
        before = copy.deepcopy(self.assessment)
        result = self._valid(self.assessment)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(self.assessment, before)

    def test_source_hash_tamper_is_rejected(self):
        forged = copy.deepcopy(self.assessment)
        forged["old_text_sha256"] = "0" * 64
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertIn("old_text_sha256_mismatch", result["errors"])

    def test_side_id_tamper_is_rejected(self):
        forged = copy.deepcopy(self.assessment)
        forged["new_element_id"] = "SOME-OTHER-ELEMENT"
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertIn("new_element_id_mismatch", result["errors"])

    def test_offset_or_span_text_tamper_is_rejected(self):
        forged = copy.deepcopy(self.assessment)
        forged["witness"]["old_span"]["start"] += 1
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertIn("old_span_offset_or_text_mismatch", result["errors"])

    def test_incomplete_context_is_rejected(self):
        forged = copy.deepcopy(self.assessment)
        forged["witness"]["old_context"]["prefix"] = "Operators "
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertIn("old_context_prefix_mismatch", result["errors"])

    def test_boolean_witness_attestation_is_not_authority(self):
        forged = copy.deepcopy(self.assessment)
        forged["witness"] = {"attested": True}
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertFalse(sa.is_deterministic_material_proof(forged))

    def test_model_advisory_is_valid_metadata_but_never_authority(self):
        advisory = {
            "schema_version": sa.ASSESSMENT_SCHEMA_VERSION,
            "policy_version": sa.SEMANTIC_POLICY_VERSION,
            "correspondence": "verified",
            "relation": "material_change",
            "basis": "model_advisory",
            "old_element_id": "OLD-1",
            "new_element_id": "NEW-1",
            "old_text_sha256": sa.sha256_text(self.old["text"]),
            "new_text_sha256": sa.sha256_text(self.new["text"]),
            "witness": None,
            "guards": {},
            "limitations": ["model_advice_is_not_authority"],
            "model": {"identity": "local-shadow", "mode": "advisory"},
        }
        result = self._valid(advisory)
        self.assertTrue(result["valid"], result["errors"])
        self.assertFalse(sa.is_deterministic_material_proof(advisory))

    def test_unknown_policy_version_is_rejected(self):
        forged = copy.deepcopy(self.assessment)
        forged["policy_version"] = "future-policy"
        result = self._valid(forged)
        self.assertFalse(result["valid"])
        self.assertIn("unsupported_policy_version", result["errors"])

    def test_wrong_side_text_hash_is_rejected(self):
        result = self._valid(self.assessment, old=self.new, new=self.old)
        self.assertFalse(result["valid"])
        self.assertIn("old_text_sha256_mismatch", result["errors"])


class DeterministicWitnessTest(unittest.TestCase):
    def test_explicit_polarity_negation_is_material(self):
        witness = se.prove_material_delta(
            "Operators SHALL NOT store tokens in logs.",
            "Operators SHALL store tokens in logs.",
        )
        self.assertIsNotNone(witness)
        self.assertEqual(witness["kind"], "polarity_negation")
        self.assertEqual(
            witness["old_span"]["text"].strip().casefold(),
            "not",
        )

    def test_modal_obligation_edit_is_material(self):
        witness = se.prove_material_delta(
            "The board shall amend this policy following an incident review.",
            "The board may amend this policy following an incident review.",
        )
        self.assertIsNotNone(witness)
        self.assertEqual(witness["kind"], "modal_obligation")

    def test_equivalent_mandatory_modals_are_not_material(self):
        for old, new in (
            (
                "The device must retain logs.",
                "The device shall retain logs.",
            ),
            (
                "The processor shall erase keys.",
                "The processor must erase keys.",
            ),
        ):
            with self.subTest(old=old):
                self.assertIsNone(se.prove_material_delta(old, new))
        permissive = se.prove_material_delta(
            "The device must retain logs.",
            "The device may retain logs.",
        )
        self.assertIsNotNone(permissive)
        self.assertEqual(permissive["kind"], "modal_obligation")

    def test_unambiguous_quantity_edit_is_material(self):
        witness = se.prove_material_delta(
            "Backups are retained for 30 days.",
            "Backups are retained for 60 days.",
        )
        self.assertIsNotNone(witness)
        self.assertEqual(witness["kind"], "quantity_value")
        self.assertEqual((witness["old_value"], witness["new_value"]), ("30", "60"))

    def test_unambiguous_date_component_edit_is_material(self):
        witness = se.prove_material_delta(
            "This policy takes effect on 15 March 2026.",
            "This policy takes effect on 15 March 2027.",
        )
        self.assertIsNotNone(witness)
        self.assertEqual(witness["kind"], "quantity_value")

    def test_threshold_operator_edit_is_material(self):
        witness = se.prove_material_delta(
            "The reserve must be at least 5 units.",
            "The reserve must be at most 5 units.",
        )
        self.assertIsNotNone(witness)
        self.assertEqual(witness["kind"], "threshold_operator")

    def test_equal_equivalent_quantities_are_not_material(self):
        for old, new in (
            ("The reserve is 14 units.", "The reserve is 014 units."),
            ("The reserve is 14.0 units.", "The reserve is 14.00 units."),
        ):
            with self.subTest(old=old, new=new):
                self.assertIsNone(se.prove_material_delta(old, new))

    def test_equivalent_threshold_phrasings_are_not_material(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The reserve must be at least 5 units.",
                "The reserve must be no less than 5 units.",
            )
        )

    def test_ambiguous_locale_is_not_material(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The reserve is 1,5 units.",
                "The reserve is 1.5 units.",
            )
        )

    def test_comma_decimal_and_thousands_tokens_are_not_sliced(self):
        for old, new in (
            (
                "The rate is 1,5 percent.",
                "The rate is 2,5 percent.",
            ),
            (
                "The allowance is 1,000 units.",
                "The allowance is 2,000 units.",
            ),
        ):
            with self.subTest(old=old):
                self.assertIsNone(se.prove_material_delta(old, new))

    def test_partial_word_inside_a_longer_word_is_not_negation(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The label is notable.",
                "The label is able.",
            )
        )

    def test_incomplete_or_dangling_clause_is_not_complete_context(self):
        for old, new in (
            ("The gateway SHALL NOT", "The gateway SHALL"),
            ("The gateway SHALL NOT.", "The gateway SHALL."),
        ):
            with self.subTest(old=old):
                self.assertIsNone(se.prove_material_delta(old, new))

    def test_multiple_sentences_are_not_a_single_clause_proof(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The policy is a draft. Operators SHALL NOT write logs.",
                "The policy is a draft. Operators SHALL write logs.",
            )
        )

    def test_unit_only_edit_abstains(self):
        self.assertIsNone(
            se.prove_material_delta(
                "Backups are retained for 14 days.",
                "Backups are retained for 14 weeks.",
            )
        )

    def test_quoted_negation_abstains(self):
        self.assertIsNone(
            se.prove_material_delta(
                'The notice must state "SHALL NOT store".',
                'The notice must state "SHALL store".',
            )
        )

    def test_exception_and_unless_abstain(self):
        for old, new in (
            (
                "Operators SHALL NOT store tokens except in the audit log.",
                "Operators SHALL store tokens except in the audit log.",
            ),
            (
                "Operators SHALL NOT store tokens unless encrypted.",
                "Operators SHALL store tokens unless encrypted.",
            ),
        ):
            with self.subTest(old=old):
                self.assertIsNone(se.prove_material_delta(old, new))

    def test_coordinated_clause_abstains(self):
        self.assertIsNone(
            se.prove_material_delta(
                "Operators SHALL NOT store or forward tokens.",
                "Operators SHALL store or forward tokens.",
            )
        )

    def test_multiple_changed_atoms_abstain(self):
        self.assertIsNone(
            se.prove_material_delta(
                "Operators SHALL NOT store 30 tokens.",
                "Operators SHALL store 60 tokens.",
            )
        )

    def test_wording_paraphrase_is_not_synonym_memorised(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The operator must record the result.",
                "The operator is required to document the result.",
            )
        )

    def test_unrelated_text_is_not_a_witness(self):
        self.assertIsNone(
            se.prove_material_delta(
                "The archive begins after 14 days.",
                "Archiving starts following fourteen days.",
            )
        )


def _paired_element(element_id: str, text: str) -> dict:
    return {
        "element_id": element_id,
        "element_type": "paragraph",
        "text": text,
        "normalized_text": text,
        "comparison_text": text,
        "reading_order": 1,
        "primary_page": 1,
        "extraction_validation": {"requires_extraction_review": False},
    }


def _short_pair(
    *,
    old_text: str = "Staff MUST NOT retain access keys after review.",
    new_text: str = "Staff MUST retain access keys after review.",
    reasons: list[str] | None = None,
    margin: float = 0.93,
    assessment: dict | None = None,
) -> dict:
    old = _paired_element("OLD-1", old_text)
    new = _paired_element("NEW-1", new_text)
    if assessment is None:
        assessment = se.assess_pair(old, new, correspondence="verified")
    candidate = {
        "candidate_id": "MODIFIED-REVIEW-000001",
        "candidate_type": "MODIFIED_REVIEW",
        "old": old,
        "new": new,
        "similarity": 0.93,
        "similarity_evidence": {
            "score": 0.93,
            "reciprocal_best": True,
            "minimum_best_margin": margin,
            "shared_provisional_anchor_keys": ["heading:key custody"],
        },
        "needs_review": True,
        "review_reasons": (
            ["short_text_similarity_pair"] if reasons is None else reasons
        ),
        "token_diff": None,
    }
    if assessment is not None:
        candidate["semantic_assessment"] = assessment
    return candidate


def _movement_for(candidate: dict, group: str = "modified_review") -> dict:
    movement = {
        "comparison_stage": "movement_and_structural_reconciliation_evidence",
        "modified": [],
        "modified_review": [],
        "duplicate_exact_review": [],
        "relocated": [],
        "deleted": [],
        "added": [],
        "movement_review": [],
        "structural_split_merge_review": [],
        "structural_boundary_review": [],
        "resegmentation_review": [],
        "residual_proposals": [],
        "residual_proposal_groups": [],
    }
    movement[group] = [candidate]
    return movement


def _empty_tables() -> dict:
    return {
        "comparison_stage": "logical_table_comparison_with_provisional_context",
        "table_matches": [],
        "deleted_table_candidates": [],
        "added_table_candidates": [],
        "unresolved_table_groups": [],
    }


def _routing(movement: dict) -> dict:
    from document_compare.phase1.stages import s11_routing

    generic = s11_routing.build_generic_routing(movement, _empty_tables())
    return {
        "comparison_stage": "deterministic_review_routing",
        "schema_version": "1.0.0",
        "routes": generic["routes"],
        "non_table_routes": generic["non_table_routes"],
        "table_routes": generic["table_routes"],
        "generic_review_list": generic["generic_review_list"],
        "statistics": {},
    }


class ShortPairRouteRegressionTest(unittest.TestCase):
    """POS-018-style short polarity pair with fresh phrases, not fixed IDs."""

    def test_verified_short_pair_is_accepted_end_to_end(self):
        from document_compare.phase1.stages import s14_canonical_result

        movement = _movement_for(_short_pair())
        routing = _routing(movement)
        route = routing["routes"][0]
        self.assertEqual(route["route"], "ACCEPT_DETERMINISTIC")
        self.assertEqual(route["case_type"], "semantic_material_change")

        result = s14_canonical_result.build_comparison_result(
            movement, _empty_tables(), routing
        )
        self.assertEqual(len(result["changes"]), 1)
        change = result["changes"][0]
        self.assertEqual(change["resolution"]["status"], "accepted")
        self.assertFalse(change["resolution"]["needs_review"])
        self.assertEqual(change["semantic_assessment"]["basis"], "deterministic_witness")
        self.assertEqual(change["semantic_assessment"]["relation"], "material_change")

    def test_legacy_short_pair_without_assessment_still_requires_review(self):
        from document_compare.phase1.stages import s14_canonical_result

        candidate = _short_pair()
        candidate.pop("semantic_assessment", None)
        movement = _movement_for(candidate)
        routing = _routing(movement)
        self.assertEqual(routing["routes"][0]["route"], "SPECIAL_CASE_LLM_OPTIONAL")
        result = s14_canonical_result.build_comparison_result(
            movement, _empty_tables(), routing
        )
        self.assertEqual(result["changes"][0]["resolution"]["status"], "review")

    def test_forged_assessment_cannot_be_accepted_even_by_route(self):
        from document_compare.phase1.stages import s14_canonical_result

        forged = se.assess_pair(
            _paired_element("OLD-1", "Staff MUST NOT retain access keys after review."),
            _paired_element("NEW-1", "Staff MUST retain access keys after review."),
            correspondence="verified",
        )
        forged["old_text_sha256"] = "0" * 64
        movement = _movement_for(_short_pair(assessment=forged))
        routing = _routing(movement)
        # Simulate a stale/forged route that still claims semantic acceptance.
        routing["routes"][0]["route"] = "ACCEPT_DETERMINISTIC"
        routing["routes"][0]["case_type"] = "semantic_material_change"
        result = s14_canonical_result.build_comparison_result(
            movement, _empty_tables(), routing
        )
        change = result["changes"][0]
        self.assertEqual(change["resolution"]["status"], "review")
        self.assertTrue(change["resolution"]["needs_review"])
        self.assertIn(
            "semantic_witness_not_independently_valid",
            change["resolution"]["review_reasons"],
        )
        self.assertEqual(
            change["semantic_assessment"]["basis"], "not_assessed"
        )

    def test_model_advisory_cannot_be_accepted(self):
        from document_compare.phase1.stages import s14_canonical_result

        advisory = {
            "schema_version": sa.ASSESSMENT_SCHEMA_VERSION,
            "policy_version": sa.SEMANTIC_POLICY_VERSION,
            "correspondence": "verified",
            "relation": "material_change",
            "basis": "model_advisory",
            "old_element_id": "OLD-1",
            "new_element_id": "NEW-1",
            "old_text_sha256": sa.sha256_text(
                "Staff MUST NOT retain access keys after review."
            ),
            "new_text_sha256": sa.sha256_text(
                "Staff MUST retain access keys after review."
            ),
            "witness": None,
            "guards": {},
            "limitations": [],
            "model": {"identity": "shadow", "mode": "advisory"},
        }
        movement = _movement_for(_short_pair(assessment=advisory))
        routing = _routing(movement)
        routing["routes"][0]["route"] = "ACCEPT_DETERMINISTIC"
        routing["routes"][0]["case_type"] = "semantic_material_change"
        result = s14_canonical_result.build_comparison_result(
            movement, _empty_tables(), routing
        )
        self.assertEqual(result["changes"][0]["resolution"]["status"], "review")

    def test_any_other_review_reason_blocks_semantic_acceptance(self):
        candidate = _short_pair(
            reasons=[
                "short_text_similarity_pair",
                "no_shared_provisional_anchor_evidence",
            ]
        )
        movement = _movement_for(candidate)
        routing = _routing(movement)
        self.assertEqual(routing["routes"][0]["route"], "SPECIAL_CASE_LLM_OPTIONAL")

    def test_weak_margin_blocks_semantic_acceptance(self):
        candidate = _short_pair(margin=0.01)
        movement = _movement_for(candidate)
        routing = _routing(movement)
        self.assertEqual(routing["routes"][0]["route"], "SPECIAL_CASE_LLM_OPTIONAL")


if __name__ == "__main__":
    unittest.main()
