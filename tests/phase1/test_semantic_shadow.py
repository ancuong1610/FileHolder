"""Gate B local NLI shadow evaluation: policy, bounds, local-only backend, CLI.

These tests use a dependency-free fake backend.  They prove *policy* behaviour
only: a fake score is never real inference evidence.  The fake backend speaks
the same small interface the real local Transformers backend exposes:

    backend.max_tokens
    backend.count_pair_tokens(first, second) -> int
    backend.predict(first, second) -> {label: finite softmax float}
    backend.metadata -> mapping with model_id/revision/labels/backend

The real backend is loaded lazily and only from an explicit local directory;
this module never downloads or imports torch/transformers until instantiation.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from document_compare.phase1.evaluation import semantic_shadow as ss
from document_compare.phase1.evaluation import semantic_shadow_cli as cli
from document_compare.phase1.evaluation import semantic_shadow_model as model


ENTAILMENT = {"contradiction": 0.01, "entailment": 0.98, "neutral": 0.01}
CONTRADICTION = {"contradiction": 0.97, "entailment": 0.02, "neutral": 0.01}
NEUTRAL = {"contradiction": 0.01, "entailment": 0.02, "neutral": 0.97}


class FakeBackend:
    """Dependency-free policy backend; never represents real NLI inference."""

    def __init__(self, responder=None, *, max_tokens=512, counts=None, metadata=None):
        self.max_tokens = max_tokens
        self._responder = responder
        self._counts = dict(counts or {})
        self._metadata = dict(
            metadata
            or {
                "model_id": "fake/test-nli",
                "revision": "fake-revision",
                "backend": "fake",
            }
        )
        self.calls = []
        self.count_calls = []

    @property
    def metadata(self):
        return dict(self._metadata, labels=list(ss.CANONICAL_LABELS))

    def count_pair_tokens(self, first, second):
        self.count_calls.append((first, second))
        if (first, second) in self._counts:
            return self._counts[(first, second)]
        if "OVERWINDOW" in first or "OVERWINDOW" in second:
            return 513
        return len(first.split()) + len(second.split()) + 4

    def predict(self, first, second):
        self.calls.append((first, second))
        if self._responder is not None:
            return self._responder(first, second)
        return dict(ENTAILMENT)


def pair(
    old_text,
    new_text,
    *,
    pair_id="P1",
    correspondence="verified",
    language="en",
    context_complete=True,
    old_id="O1",
    new_id="N1",
):
    return {
        "pair_id": pair_id,
        "old": {"element_id": old_id, "text": old_text},
        "new": {"element_id": new_id, "text": new_text},
        "correspondence": correspondence,
        "language": language,
        "context_complete": context_complete,
    }


def run_one(backend, **kwargs):
    document = {"pairs": [pair(**kwargs)]}
    return ss.run_shadow(document, backend, max_pairs=200)


class BidirectionalPolicyTest(unittest.TestCase):
    def test_bidirectional_entailment_suggests_equivalence_for_verified_source(self):
        backend = FakeBackend()
        result = run_one(
            backend,
            old_text="The operator shall retain records.",
            new_text="The operator must retain records.",
        )
        row = result["results"][0]
        self.assertEqual(row["status"], "assessed")
        self.assertEqual(row["advisory_relation"], "possible_equivalent")
        self.assertEqual(row["nli_pattern"], "bidirectional_entailment")
        self.assertTrue(row["pair_assertion"])
        self.assertFalse(row["authority"])
        self.assertEqual(row["basis"], "model_advisory")
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(
            [call[0] for call in backend.calls],
            ["The operator shall retain records.", "The operator must retain records."],
        )
        self.assertEqual(
            set(row["directional_scores"]["old_to_new"]["model_scores"]),
            set(ss.CANONICAL_LABELS),
        )
        self.assertNotIn("confidence", json.dumps(row).lower())
        self.assertNotIn("final_status", row)
        self.assertNotIn("accepted", row)

    def test_proposed_correspondence_is_not_a_pair_assertion(self):
        backend = FakeBackend()
        result = run_one(
            backend,
            old_text="The operator shall retain records.",
            new_text="The operator must retain records.",
            correspondence="proposed",
        )
        row = result["results"][0]
        self.assertEqual(row["advisory_relation"], "undetermined")
        self.assertEqual(row["nli_pattern"], "bidirectional_entailment")
        self.assertFalse(row["pair_assertion"])
        self.assertIn("correspondence_not_verified", row["limitations"])

    def test_contradiction_is_only_an_advisory_material_change(self):
        backend = FakeBackend(responder=lambda old, new: dict(CONTRADICTION))
        result = run_one(
            backend,
            old_text="The operator shall store tokens.",
            new_text="The operator shall never store tokens.",
        )
        row = result["results"][0]
        self.assertEqual(row["status"], "assessed")
        self.assertEqual(row["advisory_relation"], "possible_material_change")
        self.assertEqual(row["nli_pattern"], "contradiction")
        self.assertFalse(row["authority"])
        self.assertEqual(row["basis"], "model_advisory")
        self.assertNotIn("final_status", row)
        self.assertNotIn("suppress", row)

    def test_neutral_and_asymmetric_are_scope_uncertainty(self):
        for responder, pattern, limitation in (
            (lambda old, new: dict(NEUTRAL), "neutral", "scope_context_uncertainty"),
            (
                lambda old, new: dict(ENTAILMENT)
                if old.startswith("The operator shall")
                else dict(NEUTRAL),
                "asymmetric_entailment",
                "scope_expansion_or_contraction",
            ),
        ):
            with self.subTest(pattern=pattern):
                backend = FakeBackend(responder=responder)
                result = run_one(
                    backend,
                    old_text="The operator shall retain records.",
                    new_text="The operator may retain records.",
                )
                row = result["results"][0]
                self.assertEqual(row["advisory_relation"], "undetermined")
                self.assertEqual(row["nli_pattern"], pattern)
                self.assertIn(limitation, row["limitations"])


class ScoreContractTest(unittest.TestCase):
    def test_logits_out_of_range_and_non_normalized_scores_are_error_rows(self):
        invalid_outputs = (
            {"contradiction": -3, "entailment": 8, "neutral": 1},
            {"contradiction": 0.1, "entailment": 1.2, "neutral": -0.1},
            {"contradiction": 0.1, "entailment": 0.8, "neutral": 0.8},
            {"contradiction": False, "entailment": 0.99, "neutral": 0.01},
        )
        for scores in invalid_outputs:
            with self.subTest(scores=scores):
                backend = FakeBackend(responder=lambda old, new, s=scores: dict(s))
                result = run_one(
                    backend,
                    old_text="The operator shall retain records.",
                    new_text="The operator must retain records.",
                )
                row = result["results"][0]
                self.assertEqual(row["status"], "error")
                self.assertEqual(row["error_code"], "invalid_model_scores")
                self.assertIsNone(row["advisory_relation"])
                self.assertFalse(row["authority"])

    def test_valid_probability_scores_are_not_renormalized_by_the_runner(self):
        scores = {"contradiction": 0.01, "entailment": 0.98, "neutral": 0.01}
        backend = FakeBackend(responder=lambda old, new: dict(scores))
        result = run_one(
            backend,
            old_text="The operator shall retain records.",
            new_text="The operator must retain records.",
        )
        row = result["results"][0]
        self.assertEqual(row["status"], "assessed")
        self.assertEqual(
            row["directional_scores"]["old_to_new"]["model_scores"], scores
        )


class ProtectedFactTest(unittest.TestCase):
    def test_paraphrase_with_unchanged_protected_context_stays_equivalent(self):
        result = run_one(
            FakeBackend(),
            old_text="The operator shall retain records for 30 days.",
            new_text="The operator must retain records for 30 days.",
        )
        row = result["results"][0]
        self.assertEqual(row["advisory_relation"], "possible_equivalent")
        self.assertEqual(row["protected_signature_differences"], [])

    def test_signed_values_and_duplicate_negation_counts_block_equivalence(self):
        cases = (
            (
                "The limit is -21 units.",
                "The limit is 21 units.",
                "numbers",
            ),
            (
                "Operators shall not not store logs.",
                "Operators shall not store logs.",
                "negation",
            ),
        )
        for old_text, new_text, difference in cases:
            with self.subTest(difference=difference):
                result = run_one(
                    FakeBackend(), old_text=old_text, new_text=new_text
                )
                row = result["results"][0]
                self.assertEqual(row["advisory_relation"], "undetermined")
                self.assertIn(difference, row["protected_signature_differences"])
                self.assertNotEqual(row["advisory_relation"], "possible_equivalent")

    def test_swapped_protected_facts_including_subject_binding_block_equivalence(self):
        cases = (
            (
                "The minimum is 21 and maximum is 99.",
                "The minimum is 99 and maximum is 21.",
                "numbers",
            ),
            (
                "Alpha is 21 and Beta is 99.",
                "Beta is 21 and Alpha is 99.",
                "fact_bindings",
            ),
            (
                "The duration is 5 days and the notice period is 3 months.",
                "The duration is 5 months and the notice period is 3 days.",
                "units",
            ),
        )
        for old_text, new_text, difference in cases:
            with self.subTest(difference=difference):
                result = run_one(
                    FakeBackend(), old_text=old_text, new_text=new_text
                )
                row = result["results"][0]
                self.assertEqual(row["advisory_relation"], "undetermined")
                self.assertIn(difference, row["protected_signature_differences"])
                self.assertIn("protected_fact_disagreement", row["limitations"])
                self.assertTrue(
                    row["protected_signature"]["old"]["fact_bindings"]
                )


    def test_entailment_cannot_approve_changed_protected_facts(self):
        cases = (
            (
                "The device shall retain logs for 31 days.",
                "The device shall retain logs for 30 days.",
                "numbers",
            ),
            ("The limit is 5.", "The limit is 6.", "numbers"),
            (
                "The threshold is at least 5.",
                "The threshold is at most 5.",
                "operators",
            ),
            (
                "The operator shall not write logs.",
                "The operator shall write logs.",
                "negation",
            ),
            (
                "The retention period is 5 days.",
                "The retention period is 5 months.",
                "units",
            ),
            (
                "The operator may access records if approved.",
                "The operator may access records unless approved.",
                "conditional_exception",
            ),
            (
                "The operator shall store records.",
                "The operator may store records.",
                "modal_force",
            ),
            (
                "The operator shall store all records.",
                "The operator shall store some records.",
                "scope_qualifiers",
            ),
        )
        for old_text, new_text, difference in cases:
            with self.subTest(difference=difference):
                backend = FakeBackend()
                result = run_one(
                    backend, old_text=old_text, new_text=new_text
                )
                row = result["results"][0]
                self.assertEqual(row["advisory_relation"], "undetermined")
                self.assertTrue(row["protected_fact_disagreement"])
                self.assertIn(difference, row["protected_signature_differences"])
                self.assertIn("protected_fact_disagreement", row["limitations"])
                self.assertNotEqual(row["advisory_relation"], "possible_equivalent")

    def test_capitalized_month_dates_are_guarded_but_lowercase_may_is_a_modal(self):
        date_result = run_one(
            FakeBackend(),
            old_text="The report is due 1 January 2027.",
            new_text="The report is due 1 February 2027.",
        )
        date_row = date_result["results"][0]
        self.assertEqual(date_row["advisory_relation"], "undetermined")
        self.assertIn("dates", date_row["protected_signature_differences"])

        modal_result = run_one(
            FakeBackend(),
            old_text="The operator may retain records.",
            new_text="The operator can retain records.",
        )
        modal_row = modal_result["results"][0]
        self.assertNotIn("dates", modal_row["protected_signature_differences"])
        self.assertEqual(modal_row["advisory_relation"], "possible_equivalent")

    def test_quoted_or_incomplete_fragments_never_claim_verified_equivalence(self):
        for text in (
            "The rule says 'retain all records'.",
            "The operator shall",
            "The operator shall retain records,",
        ):
            with self.subTest(text=text):
                backend = FakeBackend()
                result = run_one(backend, old_text=text, new_text=text)
                row = result["results"][0]
                self.assertEqual(row["advisory_relation"], "undetermined")
                self.assertIn("fragment_context_not_verified", row["limitations"])


class BoundsAndAccountingTest(unittest.TestCase):
    def test_unsupported_incomplete_oversized_and_overwindow_skip_without_inference(self):
        huge_old = "x" * 3500
        huge_new = "y" * 3500
        backend = FakeBackend(
            counts={
                (
                    "The operator shall retain OVERWINDOW records.",
                    "The operator must retain OVERWINDOW records.",
                ): 513
            }
        )
        document = {
            "pairs": [
                pair(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                    pair_id="LANG",
                    language="fr",
                ),
                pair(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                    pair_id="CTX",
                    context_complete=False,
                ),
                pair(huge_old, huge_new, pair_id="HUGE"),
                pair(
                    "The operator shall retain OVERWINDOW records.",
                    "The operator must retain OVERWINDOW records.",
                    pair_id="TOKENS",
                ),
            ]
        }
        result = ss.run_shadow(document, backend, max_pairs=200)
        by_id = {row["pair_id"]: row for row in result["results"]}
        self.assertEqual(len(result["results"]), 4)
        self.assertEqual(by_id["LANG"]["skip_reason"], "unsupported_language")
        self.assertEqual(by_id["CTX"]["skip_reason"], "missing_context")
        self.assertEqual(by_id["HUGE"]["skip_reason"], "source_too_large")
        self.assertEqual(by_id["TOKENS"]["skip_reason"], "token_window_exceeded")
        self.assertEqual(by_id["TOKENS"]["directional_token_counts"]["old_to_new"], 513)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result["budget"]["directional_inferences"], 0)
        self.assertNotIn(huge_old, json.dumps(result, ensure_ascii=False))

    def test_hard_pair_budget_caps_actual_inference_and_keeps_exhausted_rows(self):
        backend = FakeBackend()
        document = {
            "pairs": [
                pair(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                    pair_id=f"P{i}",
                )
                for i in range(201)
            ]
        }
        result = ss.run_shadow(document, backend, max_pairs=999)
        self.assertEqual(len(result["results"]), 201)
        self.assertEqual(result["budget"]["max_pairs_requested"], 999)
        self.assertEqual(result["budget"]["max_pairs"], 200)
        self.assertEqual(result["budget"]["max_directional_inferences"], 400)
        self.assertEqual(result["budget"]["pairs_assessed"], 200)
        self.assertEqual(result["budget"]["directional_inferences"], 400)
        self.assertEqual(len(backend.calls), 400)
        self.assertTrue(result["budget"]["budget_exhausted"])
        self.assertEqual(result["results"][-1]["status"], "skipped")
        self.assertEqual(result["results"][-1]["skip_reason"], "budget_exhausted")

    def test_failures_and_invalid_scores_preserve_every_row_without_verdict(self):
        def responder(first, second):
            if "RAISE" in first:
                raise RuntimeError("private clause text must not leak")
            if "NAN" in first:
                return {"contradiction": math.nan, "entailment": 0.5, "neutral": 0.5}
            if "WRONG" in first:
                return {"positive": 0.9, "negative": 0.05, "neutral": 0.05}
            if "MISSING" in first:
                return {"contradiction": 0.1, "entailment": 0.9}
            return dict(ENTAILMENT)

        backend = FakeBackend(responder=responder)
        markers = ("GOOD", "RAISE", "NAN", "WRONG", "MISSING")
        document = {
            "pairs": [
                pair(
                    f"The operator shall retain {marker} records.",
                    f"The operator must retain {marker} records.",
                    pair_id=marker,
                )
                for marker in markers
            ]
        }
        result = ss.run_shadow(document, backend, max_pairs=200)
        by_id = {row["pair_id"]: row for row in result["results"]}
        self.assertEqual(len(result["results"]), len(markers))
        self.assertEqual(by_id["GOOD"]["status"], "assessed")
        for marker, code in (
            ("RAISE", "inference_error"),
            ("NAN", "invalid_model_scores"),
            ("WRONG", "invalid_model_scores"),
            ("MISSING", "invalid_model_scores"),
        ):
            with self.subTest(marker=marker):
                row = by_id[marker]
                self.assertEqual(row["status"], "error")
                self.assertEqual(row["error_code"], code)
                self.assertIsNone(row["advisory_relation"])
                self.assertFalse(row["authority"])
                self.assertEqual(row["basis"], "model_advisory")
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private clause text must not leak", dumped)
        self.assertNotIn("The operator shall retain RAISE records.", dumped)

    def test_source_hashes_ids_and_limits_are_computed_and_input_is_read_only(self):
        document = {
            "pairs": [
                pair(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                    pair_id="STABLE",
                )
            ]
        }
        # Input-supplied hashes must not be trusted.
        document["pairs"][0]["source_hash"] = "0" * 64
        document["pairs"][0]["old"]["sha256"] = "f" * 64
        snapshot = json.loads(json.dumps(document))
        backend = FakeBackend()
        first = ss.run_shadow(document, backend, max_pairs=200)
        second = ss.run_shadow(document, FakeBackend(), max_pairs=200)
        row = first["results"][0]
        self.assertEqual(document, snapshot)
        self.assertEqual(row["shadow_id"], second["results"][0]["shadow_id"])
        self.assertEqual(len(row["shadow_id"]), 64)
        self.assertEqual(
            row["old_text_sha256"],
            hashlib.sha256(b"The operator shall retain records.").hexdigest(),
        )
        self.assertNotEqual(row["source_envelope_sha256"], "0" * 64)
        self.assertEqual(first["limits"]["max_source_characters"], 6000)
        self.assertEqual(first["limits"]["max_model_tokens"], 512)
        self.assertEqual(first["score_semantics"], "model_softmax_scores_not_calibrated_confidence")
        self.assertFalse(first["authority"])

    def test_softmax_logits_are_stable_and_normalized(self):
        probabilities = ss.softmax_logits([1000.0, 1000.0, 1000.0])
        self.assertAlmostEqual(sum(probabilities), 1.0, places=12)
        self.assertTrue(all(math.isfinite(value) for value in probabilities))
        with self.assertRaises(ValueError):
            ss.softmax_logits([0.0, math.inf])


def _fake_modules(id2label=None, max_position_embeddings=514, model_max_length=4096):
    calls = {}

    class FakeTensor:
        def __init__(self, data):
            self.data = data

        def __getitem__(self, index):
            return FakeTensor(self.data[index])

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.data

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, name, **kwargs):
            calls["tokenizer"] = (name, kwargs)
            return cls()

        def __call__(self, first, second=None, **kwargs):
            tokens = ["[CLS]"] + first.split()
            if second is not None:
                tokens += ["[SEP]"] + second.split()
            tokens += ["[SEP]"]
            if kwargs.get("return_tensors") == "pt":
                return {"input_ids": FakeTensor([tokens])}
            return {"input_ids": tokens}

    class FakeModel:
        def __init__(self):
            self.config = types.SimpleNamespace(
                id2label=id2label
                or {"0": "contradiction", "1": "entailment", "2": "neutral"},
                max_position_embeddings=max_position_embeddings,
            )
            self.to_devices = []
            self.eval_called = False
            self.inputs = []

        def to(self, device):
            self.to_devices.append(device)
            return self

        def eval(self):
            self.eval_called = True
            return self

        def __call__(self, **inputs):
            self.inputs.append(inputs)
            return types.SimpleNamespace(logits=FakeTensor([[0.0, 5.0, 0.0]]))

    FakeTokenizer.model_max_length = model_max_length

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, name, **kwargs):
            return FakeTokenizer.from_pretrained(name, **kwargs)

    class AutoModel:
        @classmethod
        def from_pretrained(cls, name, **kwargs):
            calls["model"] = (name, kwargs)
            instance = FakeModel()
            calls["model_instance"] = instance
            return instance

    torch = types.ModuleType("torch")

    @contextlib.contextmanager
    def inference_mode():
        yield

    torch.inference_mode = inference_mode
    torch.Tensor = FakeTensor

    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = AutoTokenizer
    transformers.AutoModelForSequenceClassification = AutoModel
    return torch, transformers, calls


class LocalBackendTest(unittest.TestCase):
    def test_local_only_kwargs_labels_window_and_metadata(self):
        torch, transformers, calls = _fake_modules()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                sys.modules, {"torch": torch, "transformers": transformers}
            ):
                backend = model.LocalTransformerNLIBackend(tmp)
                scores = backend.predict(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                )
        tokenizer_name, tokenizer_kwargs = calls["tokenizer"]
        self.assertEqual(Path(tokenizer_name), Path(tmp).resolve())
        self.assertIs(tokenizer_kwargs["local_files_only"], True)
        self.assertIs(tokenizer_kwargs["trust_remote_code"], False)
        model_name, model_kwargs = calls["model"]
        self.assertEqual(Path(model_name), Path(tmp).resolve())
        self.assertIs(model_kwargs["local_files_only"], True)
        self.assertIs(model_kwargs["trust_remote_code"], False)
        self.assertIs(model_kwargs["use_safetensors"], True)
        self.assertEqual(backend.max_tokens, 512)
        self.assertEqual(backend.metadata["labels"], list(ss.CANONICAL_LABELS))
        self.assertEqual(backend.metadata["revision"], model.PINNED_REVISION)
        self.assertEqual(backend.metadata["model_id"], model.PINNED_MODEL_ID)
        self.assertIs(backend.metadata["local_files_only"], True)
        instance = calls["model_instance"]
        self.assertEqual(instance.to_devices, ["cpu"])
        self.assertTrue(instance.eval_called)
        self.assertEqual(set(scores), set(ss.CANONICAL_LABELS))
        self.assertAlmostEqual(sum(scores.values()), 1.0, places=6)
        self.assertEqual(max(scores, key=scores.get), "entailment")

    def test_local_backend_fails_closed_on_wrong_label_set(self):
        for id2label in (
            {"0": "contradiction", "1": "entailment"},
            {"0": "contradiction", "1": "entailment", "2": "positive"},
        ):
            with self.subTest(id2label=id2label):
                torch, transformers, _ = _fake_modules(id2label=id2label)
                with tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.dict(
                        sys.modules, {"torch": torch, "transformers": transformers}
                    ):
                        with self.assertRaises(ValueError):
                            model.LocalTransformerNLIBackend(tmp)

    def test_local_backend_streams_artifact_identity_without_network(self):
        torch, transformers, calls = _fake_modules()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.safetensors").write_bytes(b"fake weights")
            (root / "config.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(
                sys.modules, {"torch": torch, "transformers": transformers}
            ):
                backend = model.LocalTransformerNLIBackend(root)
                metadata = backend.metadata
        self.assertRegex(metadata["artifact_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(metadata["artifact_file_count"], 2)
        self.assertGreater(metadata["artifact_bytes"], 0)
        self.assertEqual(metadata["artifact_hash_status"], "streamed_sha256")
        self.assertEqual(
            metadata["revision_verification"],
            "pinned_declaration_not_file_hash",
        )
        self.assertEqual(metadata["model_id_source"], "pinned_declaration")
        self.assertIs(metadata["local_files_only"], True)

    def test_run_shadow_preserves_artifact_provenance_and_stable_id(self):
        metadata = {
            "model_id": "fake/test-nli",
            "revision": "fake-revision",
            "revision_verification": "pinned_declaration_not_file_hash",
            "model_id_source": "pinned_declaration",
            "artifact_sha256": "a" * 64,
            "artifact_file_count": 2,
            "artifact_bytes": 13,
            "artifact_hash_status": "streamed_sha256",
        }
        document = {
            "pairs": [
                pair(
                    "The operator shall retain records.",
                    "The operator must retain records.",
                    pair_id="PROV",
                )
            ]
        }
        first = ss.run_shadow(
            document,
            FakeBackend(metadata=metadata),
            max_pairs=200,
            model_metadata={"artifact_sha256": None},
        )
        second = ss.run_shadow(
            document,
            FakeBackend(metadata=dict(metadata, artifact_sha256="b" * 64)),
            max_pairs=200,
        )
        output_model = first["model"]
        self.assertEqual(output_model["artifact_sha256"], "a" * 64)
        self.assertEqual(
            output_model["revision_verification"],
            "pinned_declaration_not_file_hash",
        )
        self.assertEqual(output_model["model_id_source"], "pinned_declaration")
        self.assertEqual(output_model["artifact_file_count"], 2)
        self.assertNotEqual(
            first["results"][0]["shadow_id"], second["results"][0]["shadow_id"]
        )

    def test_model_and_cli_import_without_optional_ml_dependencies(self):
        importlib.reload(model)
        importlib.reload(cli)
        self.assertFalse(hasattr(model, "torch"))
        self.assertFalse(hasattr(model, "transformers"))
        self.assertFalse(hasattr(cli, "LocalTransformerNLIBackend"))


class CliTest(unittest.TestCase):
    def _write_input(self, directory):
        path = Path(directory) / "pairs.json"
        path.write_text(
            json.dumps(
                {
                    "pairs": [
                        pair(
                            "The operator shall retain records.",
                            "The operator must retain records.",
                        )
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_cli_refuses_existing_output_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = self._write_input(tmp)
            before = input_path.read_bytes()
            out = Path(tmp) / "existing-out"
            out.mkdir()
            marker = out / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            called = []

            def factory(_):
                called.append(True)
                raise AssertionError("backend must not load")

            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                code = cli.main(
                    [
                        "--pairs",
                        str(input_path),
                        "--out",
                        str(out),
                        "--model-dir",
                        tmp,
                    ],
                    backend_factory=factory,
                )
            self.assertNotEqual(code, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(input_path.read_bytes(), before)
            self.assertEqual(called, [])
            self.assertNotIn("The operator", stderr.getvalue())

    def test_cli_writes_a_fresh_bounded_output_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = self._write_input(tmp)
            before = input_path.read_bytes()
            out = Path(tmp) / "fresh-out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(
                    [
                        "--pairs",
                        str(input_path),
                        "--out",
                        str(out),
                        "--model-dir",
                        tmp,
                        "--max-pairs",
                        "999",
                    ],
                    backend_factory=lambda _: FakeBackend(),
                )
            self.assertEqual(code, 0)
            self.assertEqual(input_path.read_bytes(), before)
            self.assertTrue((out / "shadow_results.json").is_file())
            self.assertTrue((out / "shadow_summary.md").is_file())
            payload = json.loads((out / "shadow_results.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["budget"]["max_pairs"], 200)
            self.assertEqual(payload["budget"]["max_pairs_requested"], 999)
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(payload["input"]["sha256"], hashlib.sha256(before).hexdigest())
            summary = (out / "shadow_summary.md").read_text(encoding="utf-8")
            self.assertIn("advisory", summary.lower())
            self.assertNotIn("The operator", summary)

    def test_cli_global_input_errors_are_safe_and_do_not_echo_clauses(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "duplicate.json"
            secret = "SECRET CLAUSE MUST NOT APPEAR"
            input_path.write_text(
                json.dumps(
                    {
                        "pairs": [
                            pair(secret, secret, pair_id="DUP"),
                            pair(secret, secret, pair_id="DUP"),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            out = Path(tmp) / "out"
            called = []

            def factory(_):
                called.append(True)
                raise AssertionError("backend must not load on global input error")

            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                code = cli.main(
                    [
                        "--pairs",
                        str(input_path),
                        "--out",
                        str(out),
                        "--model-dir",
                        tmp,
                    ],
                    backend_factory=factory,
                )
            self.assertNotEqual(code, 0)
            self.assertEqual(called, [])
            self.assertFalse(out.exists())
            self.assertNotIn(secret, stderr.getvalue())
            self.assertIn("duplicate_pair_id", stderr.getvalue())

    def test_cli_permission_error_is_a_safe_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = self._write_input(tmp)
            before = input_path.read_bytes()
            out = Path(tmp) / "blocked-out"
            with mock.patch.object(
                Path, "mkdir", side_effect=PermissionError("/private/secret/path")
            ):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    code = cli.main(
                        [
                            "--pairs",
                            str(input_path),
                            "--out",
                            str(out),
                            "--model-dir",
                            tmp,
                        ],
                        backend_factory=lambda _: FakeBackend(),
                    )
            self.assertNotEqual(code, 0)
            self.assertEqual(input_path.read_bytes(), before)
            self.assertIn("output_error:unavailable", stderr.getvalue())
            self.assertNotIn("/private/secret/path", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_partial_write_cleanup_keeps_foreign_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = self._write_input(tmp)
            out = Path(tmp) / "partial-out"

            def failing_summary(path, payload):
                (path.parent / "foreign.txt").write_text("keep", encoding="utf-8")
                raise OSError("simulated write failure")

            with mock.patch.object(cli, "_write_summary", side_effect=failing_summary):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    code = cli.main(
                        [
                            "--pairs",
                            str(input_path),
                            "--out",
                            str(out),
                            "--model-dir",
                            tmp,
                        ],
                        backend_factory=lambda _: FakeBackend(),
                    )
            self.assertNotEqual(code, 0)
            self.assertIn("output_error:write_failed", stderr.getvalue())
            self.assertTrue((out / "foreign.txt").is_file())
            self.assertFalse((out / "shadow_results.json").exists())
            self.assertFalse((out / "shadow_summary.md").exists())


if __name__ == "__main__":
    unittest.main()
