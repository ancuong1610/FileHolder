"""Lazy, local-only Transformers NLI backend for Gate B shadow evaluation.

Optional third-party ML imports live inside ``LocalTransformerNLIBackend.__init__``
so importing this module never imports ``torch``/``transformers`` and never
touches the network.  The backend accepts only an explicit local model
directory; a remote model ID string is rejected by the directory check.

Selected candidate (lead-run experiment, not a production dependency):
``cross-encoder/nli-deberta-v3-small`` at revision
``fa2804872c3b4bd748f38c0185cc85775361e735`` (Apache-2.0, English SNLI/MNLI).

Safety properties:

* ``from_pretrained(..., local_files_only=True, trust_remote_code=False,
  use_safetensors=True)`` for the model and ``local_files_only=True,
  trust_remote_code=False`` for the tokenizer;
* config ``id2label`` is read, not assumed; it must contain exactly the three
  NLI labels or initialization fails closed;
* the model is moved to CPU and ``eval()`` before inference;
* ``count_pair_tokens`` and ``predict`` tokenize with ``truncation=False`` and
  include special tokens, so an over-window input is rejected, never sliced;
* the local model directory is fingerprinted once by streaming file bytes into
  ``artifact_sha256``; the pinned model/revision remain declared expectations,
  not a claim that an arbitrary local directory is the pinned snapshot;
* ``predict`` accepts only the two source strings (no extra model keys) and
  returns finite softmax probabilities keyed by canonical labels.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from document_compare.phase1.evaluation.semantic_shadow import (
    CANONICAL_LABELS,
    MAX_MODEL_TOKENS,
    softmax_logits,
)

PINNED_MODEL_ID = "cross-encoder/nli-deberta-v3-small"
PINNED_REVISION = "fa2804872c3b4bd748f38c0185cc85775361e735"

_ARTIFACT_CHUNK_BYTES = 1024 * 1024


def _artifact_identity(path: Path) -> tuple[str | None, int, int, str]:
    """Stream a deterministic SHA-256 over the local model directory files.

    The digest includes each relative path and file bytes.  It fingerprints the
    actual local artifact so output/stable IDs do not imply that any local
    directory is the pinned remote revision.  Hashing is local-only and happens
    once at backend initialization.
    """
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    try:
        files = sorted(
            (candidate for candidate in path.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(path).as_posix(),
        )
        for candidate in files:
            relative = candidate.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with candidate.open("rb") as handle:
                while True:
                    chunk = handle.read(_ARTIFACT_CHUNK_BYTES)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    digest.update(chunk)
            digest.update(b"\0")
            file_count += 1
    except OSError:
        return None, file_count, total_bytes, "unavailable"
    if file_count == 0:
        return None, 0, 0, "empty_directory"
    return digest.hexdigest(), file_count, total_bytes, "streamed_sha256"


def _canonical_index_to_label(config: Any) -> dict[int, str]:
    raw = getattr(config, "id2label", None)
    if not isinstance(raw, dict):
        raise ValueError("model_labels_unavailable")
    mapped: dict[int, str] = {}
    for key, value in raw.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            raise ValueError("model_labels_malformed") from None
        label = str(value).strip().casefold()
        if index in mapped:
            raise ValueError("model_labels_malformed")
        mapped[index] = label
    if len(mapped) != 3 or set(mapped) != {0, 1, 2}:
        raise ValueError("model_labels_must_be_exactly_three_indices")
    if set(mapped.values()) != set(CANONICAL_LABELS):
        raise ValueError("model_labels_must_be_contradiction_entailment_neutral")
    return mapped


def _window_size(tokenizer: Any, config: Any) -> int:
    candidates: list[int] = []
    tokenizer_limit = getattr(tokenizer, "model_max_length", None)
    config_limit = getattr(config, "max_position_embeddings", None)
    for limit in (tokenizer_limit, config_limit):
        if (
            isinstance(limit, int)
            and not isinstance(limit, bool)
            and 0 < limit < 10**9
        ):
            candidates.append(limit)
    if not candidates:
        raise ValueError("model_token_window_unavailable")
    window = min(*candidates, MAX_MODEL_TOKENS)
    if window <= 0:
        raise ValueError("model_token_window_invalid")
    return window


class LocalTransformerNLIBackend:
    """Local-only, CPU, safetensors NLI backend with lazy optional imports."""

    def __init__(self, model_dir: str | Path, *, revision: str = PINNED_REVISION):
        path = Path(model_dir).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("model_dir_must_be_a_local_directory")
        self._path = path
        self._revision = revision
        (
            self._artifact_sha256,
            self._artifact_file_count,
            self._artifact_bytes,
            self._artifact_hash_status,
        ) = _artifact_identity(path)

        # Deliberately lazy: no optional ML import at module import time.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(path),
            local_files_only=True,
            trust_remote_code=False,
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(path),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        )
        self._index_to_label = _canonical_index_to_label(self._model.config)
        self.max_tokens = _window_size(self._tokenizer, self._model.config)

        # CPU inference with dropout disabled by eval mode.
        self._model.to("cpu")
        self._model.eval()

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": PINNED_MODEL_ID,
            "model_id_source": "pinned_declaration",
            "revision": self._revision,
            "revision_verification": "pinned_declaration_not_file_hash",
            "artifact_sha256": self._artifact_sha256,
            "artifact_file_count": self._artifact_file_count,
            "artifact_bytes": self._artifact_bytes,
            "artifact_hash_status": self._artifact_hash_status,
            "labels": list(CANONICAL_LABELS),
            "max_tokens": self.max_tokens,
            "score_format": "softmax_probability",
            "backend": "transformers_local_safetensors",
            "local_files_only": True,
        }

    def count_pair_tokens(self, first: str, second: str) -> int:
        encoded = self._tokenizer(
            first,
            second,
            add_special_tokens=True,
            truncation=False,
            return_tensors=None,
        )
        input_ids = encoded["input_ids"]
        if input_ids and isinstance(input_ids[0], (list, tuple)):
            input_ids = input_ids[0]
        return len(input_ids)

    def predict(self, first: str, second: str) -> dict[str, float]:
        """Return canonical-label softmax probabilities, never logits."""
        if self.count_pair_tokens(first, second) > self.max_tokens:
            raise ValueError("input_exceeds_model_token_window")
        encoded = self._tokenizer(
            first,
            second,
            add_special_tokens=True,
            truncation=False,
            padding=False,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            outputs = self._model(**encoded)
        logits = outputs.logits[0].detach().cpu().tolist()
        if not isinstance(logits, list) or len(logits) != 3:
            raise ValueError("model_output_must_have_three_logits")
        probabilities = softmax_logits(logits)
        scores = {
            self._index_to_label[index]: float(probabilities[index])
            for index in range(3)
        }
        if set(scores) != set(CANONICAL_LABELS):
            raise ValueError("model_output_labels_unexpected")
        if any(not math.isfinite(value) for value in scores.values()):
            raise ValueError("model_output_not_finite")
        return scores


__all__ = ["LocalTransformerNLIBackend", "PINNED_MODEL_ID", "PINNED_REVISION"]
