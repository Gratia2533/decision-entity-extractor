"""Pinned Otter forward adapter with unsuppressed, mask-first span decoding."""

from __future__ import annotations

import gc
import math
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from adapters.otter.alignment import (
    BOUNDARY_TOKEN_PARITY,
    align_cross_query_offsets,
    cross_query_offset_mode,
)
from adapters.otter.contracts import (
    CandidateValidationError,
    QueryInference,
    SpanCandidate,
    deduplicate_candidates,
)
from model_specs import (
    MODEL_THRESHOLDS,
    MODELS,
    RUNTIME_VERSIONS,
)


@dataclass(frozen=True, slots=True)
class SpanSpace:
    mapping: tuple[tuple[int, int], ...]
    lengths: tuple[int, ...]
    valid: tuple[bool, ...]
    token_offset: int
    char_shift: int


def build_span_space(
    sequence_ids: Sequence[int | None],
    offsets: Sequence[Sequence[int]],
    *,
    max_span_tokens: int,
    text_start_index: int,
    char_shift: int,
    text_length: int,
) -> SpanSpace:
    """Enumerate the native bounded span space; invalid positions remain masked."""

    token_count = len(sequence_ids)
    if len(offsets) != token_count:
        raise CandidateValidationError("tokenizer offset/token cardinality mismatch")
    for index in range(text_start_index, token_count):
        if sequence_ids[index] is None:
            continue
        raw_start = int(offsets[index][0]) - char_shift
        raw_end = int(offsets[index][1]) - char_shift
        if not 0 <= raw_start < raw_end <= text_length:
            raise CandidateValidationError(
                f"tokenizer offset [{raw_start}, {raw_end}) is outside text length {text_length}"
            )
    relative_count = token_count - text_start_index
    mapping = tuple(
        (start, start + width)
        for start in range(relative_count)
        for width in range(max_span_tokens)
        if start + width < relative_count
    )
    valid = []
    for start, end in mapping:
        absolute_start = start + text_start_index
        absolute_end = end + text_start_index
        valid.append(
            sequence_ids[absolute_start] is not None
            and sequence_ids[absolute_end] is not None
            and int(offsets[absolute_start][1]) > char_shift
            and int(offsets[absolute_end][1]) > char_shift
        )
    lengths = tuple(end - start + 1 for start, end in mapping)
    return SpanSpace(mapping, lengths, tuple(valid), text_start_index, char_shift)


def _surface_boundary(
    query: str,
    offsets: Sequence[Sequence[int]],
    start_token: int,
    end_token: int,
    char_shift: int,
) -> tuple[int, int] | None:
    char_start = int(offsets[start_token][0]) - char_shift
    raw_end = int(offsets[end_token][1]) - char_shift
    if not 0 <= char_start < raw_end <= len(query):
        raise CandidateValidationError(
            f"tokenizer span [{char_start}, {raw_end}) is outside query length {len(query)}"
        )
    raw = query[char_start:raw_end]
    stripped = raw.lstrip()
    char_start += len(raw) - len(stripped)
    surface = stripped.rstrip()
    if not surface:
        return None
    return char_start, char_start + len(surface)


def decode_raw_candidates(
    query: str,
    offsets: Sequence[Sequence[int]],
    span_space: SpanSpace,
    span_logits: Sequence[Sequence[float]],
    *,
    probes: Sequence[str],
    threshold: float,
) -> tuple[tuple[SpanCandidate, ...], tuple[tuple[int, int], ...]]:
    """Apply native mask before >= threshold and retain every overlapping span."""

    if len(span_logits) != len(probes):
        raise ValueError("span logits/probe cardinality mismatch")
    candidates: list[SpanCandidate] = []
    representable: set[tuple[int, int]] = set()
    for span_index, (start, end) in enumerate(span_space.mapping):
        if not span_space.valid[span_index]:
            continue
        boundary = _surface_boundary(
            query,
            offsets,
            start + span_space.token_offset,
            end + span_space.token_offset,
            span_space.char_shift,
        )
        if boundary is None:
            continue
        representable.add(boundary)
        for probe_index, probe in enumerate(probes):
            if len(span_logits[probe_index]) != len(span_space.mapping):
                raise ValueError("span logits/native span cardinality mismatch")
            logit = float(span_logits[probe_index][span_index])
            if not math.isfinite(logit):
                raise CandidateValidationError("span logits must be finite")
            probability = (
                1.0 / (1.0 + math.exp(-logit))
                if logit >= 0
                else math.exp(logit) / (1.0 + math.exp(logit))
            )
            if probability >= threshold:
                start_char, end_char = boundary
                candidates.append(
                    SpanCandidate.from_offsets(
                        query,
                        start_char,
                        end_char,
                        probability,
                        probes=(probe,),
                    )
                )
    return deduplicate_candidates(candidates), tuple(sorted(representable))


class OtterFamilyAdapter:
    """One exact-revision Otter checkpoint with startup-configured label inputs."""

    def __init__(
        self,
        model_key: str,
        artifact_root: str | Path,
        probes: tuple[str, ...],
    ) -> None:
        if model_key not in MODELS:
            raise ValueError(f"unknown model key: {model_key}")
        self.identity = MODELS[model_key]
        self.artifact_root = Path(artifact_root)
        if not probes or any(not item.strip() for item in probes):
            raise ValueError("candidate probes must be non-empty descriptions")
        self.probes = probes
        self.model: Any = None
        self.snapshot: Path | None = None
        self.last_timings_ns: dict[str, int] = {}
        self._prefix = self._prefix_ids = self._prefix_positions = self._type_inputs = None

    @property
    def model_key(self) -> str:
        return self.identity.key

    def load(self) -> dict[str, object]:
        actual_versions = {name: version(name) for name in RUNTIME_VERSIONS}
        if actual_versions != RUNTIME_VERSIONS:
            raise RuntimeError("Otter runtime versions differ from the frozen production extra")
        from transformers import AutoModel, AutoTokenizer

        from adapters.otter.artifact import inspect_trusted_artifact

        snapshot, evidence = inspect_trusted_artifact(self.artifact_root, self.model_key)
        model = AutoModel.from_pretrained(
            snapshot,
            trust_remote_code=True,
            local_files_only=True,
        ).to(device="cpu", dtype=self._torch().float32)
        model.eval()
        if self.identity.architecture == "cross_encoder":
            model._tokenizer = AutoTokenizer.from_pretrained(
                snapshot,
                trust_remote_code=True,
                local_files_only=True,
            )
        else:
            model._token_tokenizer = AutoTokenizer.from_pretrained(
                # Shipped TokenizersBackend metadata is incompatible with transformers 4.56.2.
                snapshot / "runtime_tokenizer",
                trust_remote_code=True,
                local_files_only=True,
            )
            model._type_tokenizer = AutoTokenizer.from_pretrained(
                snapshot / "type_tokenizer",
                trust_remote_code=True,
                local_files_only=True,
            )
        if getattr(model.config, "architecture", None) != self.identity.architecture:
            raise RuntimeError("loaded Otter architecture does not match the frozen identity")
        if int(model.config.max_span_length) != self.identity.max_span_tokens:
            raise RuntimeError("loaded Otter max_span_length does not match the protocol")
        if int(model.config.max_seq_length) != self.identity.max_sequence_tokens:
            raise RuntimeError("loaded Otter max_seq_length does not match the protocol")
        self.model, self.snapshot = model, snapshot
        if self.identity.architecture == "cross_encoder":
            self._prefix = model.build_prompt(self.probes)
            self._prefix_ids = tuple(
                model.tokenizer(self._prefix, add_special_tokens=True)["input_ids"]
            )
            label_id = model.tokenizer.convert_tokens_to_ids("[LABEL]")
            self._prefix_positions = tuple(
                i for i, token_id in enumerate(self._prefix_ids) if token_id == label_id
            )
            if len(self._prefix_positions) != len(self.probes):
                raise RuntimeError("label prefix markers do not match configured probes")
        else:
            self._type_inputs = model.encode_labels(self.probes)
        evidence.update(
            {
                "model_key": self.model_key,
                "runtime_versions": actual_versions,
                "model_id": self.identity.model_id,
                "revision": self.identity.revision,
                "license": self.identity.license,
                "actual_class": type(model).__name__,
                "architecture": model.config.architecture,
                "dtype": str(next(model.parameters()).dtype),
                "device": str(next(model.parameters()).device),
                "max_sequence_tokens": int(model.config.max_seq_length),
                "max_span_tokens": int(model.config.max_span_length),
                "decoder": "local-mask-first-ge-unsuppressed-v1",
            }
        )
        return evidence

    @staticmethod
    def _torch() -> Any:
        import torch

        return torch

    def _tokenize(self, query: str) -> tuple[Any, Any, SpanSpace, bool]:
        if self.identity.architecture == "cross_encoder":
            prefix = self._prefix
            text = prefix + query
            tokenizer = self.model.tokenizer
            prefix_length = len(prefix)
        else:
            text = query
            tokenizer = self.model.token_tokenizer
            prefix_length = 0
        encoding = tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=int(self.model.config.max_seq_length),
            return_offsets_mapping=True,
            return_tensors="pt",
        )
        offsets = encoding.pop("offset_mapping")
        sequence_ids = encoding.sequence_ids(0)
        if self.identity.architecture == "cross_encoder":
            text_start = next(
                index
                for index, item in enumerate(offsets[0])
                if sequence_ids[index] is not None and int(item[1]) > prefix_length
            )
            offsets, truncated = self._query_local_cross_offsets(
                query, prefix, encoding, offsets, sequence_ids, text_start
            )
        else:
            text_start = 0
            max_text_end = max(
                (
                    int(offsets[0][index][1])
                    for index, value in enumerate(sequence_ids)
                    if value is not None
                ),
                default=0,
            )
            truncated = max_text_end < len(query)
        space = build_span_space(
            sequence_ids,
            offsets[0],
            max_span_tokens=int(self.model.config.max_span_length),
            text_start_index=text_start,
            char_shift=0,
            text_length=len(query),
        )
        return encoding, offsets, space, truncated

    def _query_local_cross_offsets(
        self,
        query: str,
        prefix: str,
        encoding: Any,
        combined_offsets: Any,
        sequence_ids: Sequence[int | None],
        text_start: int,
    ) -> tuple[Any, bool]:
        """Map cross tokens by strict translation or exact boundary-token parity."""

        combined_indices = [
            index
            for index in range(text_start, len(sequence_ids))
            if sequence_ids[index] is not None
        ]
        mode = cross_query_offset_mode(
            combined_offsets[0].tolist(),
            combined_indices,
            prefix=prefix,
            query_length=len(query),
        )
        query_encoding = query_offsets = query_indices = None
        if mode == BOUNDARY_TOKEN_PARITY:
            query_encoding = self.model.tokenizer(
                [query],
                padding=True,
                truncation=False,
                return_offsets_mapping=True,
                return_tensors="pt",
            )
            query_offsets = query_encoding.pop("offset_mapping")
            query_sequence_ids = query_encoding.sequence_ids(0)
            query_indices = [
                index for index, value in enumerate(query_sequence_ids) if value is not None
            ]
        aligned, truncated = align_cross_query_offsets(
            [int(value) for value in encoding["input_ids"][0]],
            combined_offsets[0].tolist(),
            combined_indices,
            prefix=prefix,
            query_length=len(query),
            query_token_ids=(
                [int(value) for value in query_encoding["input_ids"][0]]
                if query_encoding is not None
                else None
            ),
            query_offsets=query_offsets[0].tolist() if query_offsets is not None else None,
            query_text_indices=query_indices,
        )
        return self._torch().tensor([aligned]), truncated

    def _model_labels(self, space: SpanSpace) -> dict[str, Any]:
        torch = self._torch()
        labels = {
            "span_subword_indices": torch.tensor([space.mapping], device="cpu"),
            "span_lengths": torch.tensor([space.lengths], device="cpu"),
            "valid_span_mask": torch.tensor(
                [[[value for value in space.valid] for _ in self.probes]], device="cpu"
            ),
        }
        if self.identity.architecture == "cross_encoder":
            positions = list(self._prefix_positions)
            if len(positions) != len(self.probes):
                raise RuntimeError("label prefix markers do not match configured probes")
            labels.update(
                {
                    "text_start_index": space.token_offset,
                    "label_token_subword_positions": positions,
                }
            )
        return labels

    def _forward(self, encoding: Any, labels: dict[str, Any]) -> Any:
        token_inputs = {key: value.to("cpu") for key, value in encoding.items()}
        if self.identity.architecture == "cross_encoder":
            return self.model(token_encoder_inputs=token_inputs, labels=labels).span_logits
        started = perf_counter_ns()
        type_inputs = self._type_inputs
        self.last_timings_ns["label"] += perf_counter_ns() - started
        return self.model(
            token_encoder_inputs=token_inputs,
            type_encoder_inputs=type_inputs,
            labels=labels,
        ).span_logits

    def predict(self, query: str) -> QueryInference:
        if self.model is None:
            raise RuntimeError("adapter is not loaded")
        self.last_timings_ns = {}
        started = perf_counter_ns()
        encoding, offsets, space, truncated = self._tokenize(query)
        self.last_timings_ns["tokenize"] = perf_counter_ns() - started
        started = perf_counter_ns()
        labels = self._model_labels(space)
        self.last_timings_ns["label"] = perf_counter_ns() - started
        torch = self._torch()
        initial_label_ns = self.last_timings_ns["label"]
        started = perf_counter_ns()
        with torch.inference_mode():
            logits = self._forward(encoding, labels)
        self.last_timings_ns["forward"] = (
            perf_counter_ns() - started - self.last_timings_ns["label"] + initial_label_ns
        )
        started = perf_counter_ns()
        candidates, representable = decode_raw_candidates(
            query,
            offsets[0].tolist(),
            space,
            logits[0].detach().float().cpu().tolist(),
            probes=self.probes,
            threshold=MODEL_THRESHOLDS[self.model_key],
        )
        self.last_timings_ns["decode"] = perf_counter_ns() - started
        return QueryInference(
            candidates=candidates,
            encoded_tokens=int(encoding["input_ids"].shape[1]),
            max_sequence_tokens=int(self.model.config.max_seq_length),
            native_span_count=sum(space.valid),
            representable_boundaries=representable,
            truncated=truncated,
            cache_mode="configured_labels",
        )

    def close(self) -> None:
        self.model = None
        self._prefix = self._prefix_ids = self._prefix_positions = self._type_inputs = None
        gc.collect()


__all__ = [
    "OtterFamilyAdapter",
    "SpanSpace",
    "align_cross_query_offsets",
    "build_span_space",
    "decode_raw_candidates",
]
