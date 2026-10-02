"""Merge Otter cross-encoder (>= 0.04) and bi-encoder (>= 0.05) candidate spans."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from adapters.otter.contracts import QueryInference, SpanCandidate
from adapters.otter.runtime import OtterFamilyAdapter
from contracts.errors import PipelineError
from contracts.models import EntitySchema
from contracts.pipeline import CompareCandidate, CompareConfig, CompareSnapshot, ModelProvenance
from model_specs import MODEL_THRESHOLDS, MODELS, PARALLEL_INTRAOP_THREADS


@dataclass(frozen=True, slots=True)
class SelectedCandidate:
    start: int
    end: int
    mention: str
    source_scores: Mapping[str, float]


def union_candidates(
    query: str,
    cm: tuple[SpanCandidate, ...],
    bm: tuple[SpanCandidate, ...],
) -> tuple[SelectedCandidate, ...]:
    """Union exact boundaries while keeping model scores separate."""

    by_boundary: dict[tuple[int, int], dict[str, float]] = {}
    for model_key, candidates in (("CM", cm), ("BM", bm)):
        for item in candidates:
            validated = SpanCandidate.from_offsets(
                query, item.start, item.end, item.score, mention=item.mention
            )
            if validated.score < MODEL_THRESHOLDS[model_key]:
                raise ValueError(f"{model_key} returned a candidate below its configured threshold")
            boundary = (validated.start, validated.end)
            scores = by_boundary.setdefault(boundary, {})
            scores[model_key] = max(scores.get(model_key, float("-inf")), validated.score)
    return tuple(
        SelectedCandidate(start, end, query[start:end], by_boundary[(start, end)])
        for start, end in sorted(by_boundary)
    )


class CandidateAdapter(Protocol):
    def load(self) -> dict[str, object]: ...
    def predict(self, query: str) -> QueryInference: ...
    def close(self) -> None: ...


class SelectedCandidateProposer:
    """Own the two pinned candidate adapters and preserve their exact boundary union."""

    def __init__(
        self,
        artifact_root: str | Path,
        schema: EntitySchema,
        adapter_factory: Callable[[str, tuple[str, ...]], CandidateAdapter] | None = None,
    ) -> None:
        self._schema = schema
        probes = tuple(label.description for label in schema.labels)
        factory = adapter_factory or (
            lambda key, configured_probes: OtterFamilyAdapter(key, artifact_root, configured_probes)
        )
        self._adapters = {key: factory(key, probes) for key in MODEL_THRESHOLDS}
        self._workers = {
            key: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"entity-{key}")
            for key in MODEL_THRESHOLDS
        }
        self._loaded = False
        self._closed = False
        self._within_query_parallel = False

    def load(self) -> dict[str, dict[str, object]]:
        if self._loaded:
            raise RuntimeError("candidate proposer is already loaded")
        try:
            evidence = {
                key: self._workers[key].submit(adapter.load).result()
                for key, adapter in self._adapters.items()
            }
            if all(isinstance(adapter, OtterFamilyAdapter) for adapter in self._adapters.values()):
                import torch

                self._within_query_parallel = torch.get_num_threads() in PARALLEL_INTRAOP_THREADS
        except Exception:
            self.close()
            raise
        self._loaded = True
        return evidence

    def propose(self, query: str) -> tuple[SelectedCandidate, ...]:
        if not self._loaded or self._closed:
            raise RuntimeError("candidate proposer is not loaded")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must contain non-whitespace text")
        results: dict[str, QueryInference] = {}
        for key in MODEL_THRESHOLDS:
            result, _ = self._workers[key].submit(self._predict, key, query).result()
            results[key] = result
        return union_candidates(query, results["CM"].candidates, results["BM"].candidates)

    def _predict(self, key: str, query: str):
        adapter = self._adapters[key]
        result = adapter.predict(query)
        if result.truncated:
            raise PipelineError("QUERY_TOO_LONG")
        if result.cache_mode != "configured_labels":
            raise ValueError("unexpected label mode")
        # Capture before the next queued call overwrites the adapter's timing dictionary.
        return result, dict(getattr(adapter, "last_timings_ns", {}))

    async def propose_snapshot_async(self, query: str) -> CompareSnapshot:
        if not self._loaded or self._closed:
            raise RuntimeError("candidate proposer is not loaded")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must contain non-whitespace text")
        results = {}
        if self._within_query_parallel:
            pending = [
                asyncio.wrap_future(self._workers[key].submit(self._predict, key, query))
                for key in MODEL_THRESHOLDS
            ]
            # Drain both model calls even if one fails, retaining worker ownership.
            completed = await asyncio.gather(*pending, return_exceptions=True)
            for key, item in zip(MODEL_THRESHOLDS, completed, strict=True):
                if isinstance(item, BaseException):
                    raise item
                results[key], _ = item
        else:
            for key in MODEL_THRESHOLDS:
                results[key], _ = await asyncio.wrap_future(
                    self._workers[key].submit(self._predict, key, query)
                )
        selected = union_candidates(query, results["CM"].candidates, results["BM"].candidates)
        return self._snapshot(query, selected, "q000")

    def warmup(self) -> None:
        """One bounded local forward per model; no classifier or user data."""
        if not self._loaded:
            raise RuntimeError("candidate proposer is not loaded")
        for key in MODEL_THRESHOLDS:
            self._workers[key].submit(self._predict, key, "Example entity").result()

    def propose_snapshot(self, query: str, case_id: str = "q000") -> CompareSnapshot:
        selected = self.propose(query)
        return self._snapshot(query, selected, case_id)

    def _snapshot(self, query: str, selected: tuple[SelectedCandidate, ...], case_id: str):
        if len(selected) > CompareConfig().max_candidates:
            raise PipelineError("QUERY_TOO_LONG")
        return CompareSnapshot(
            case_id=case_id,
            raw_text=query,
            entity_schema=self._schema,
            config=CompareConfig(),
            source_statuses=("CM:SUCCESS", "BM:SUCCESS"),
            candidates=tuple(
                CompareCandidate(
                    candidate_id=f"s{index:03d}",
                    start=c.start,
                    end=c.end,
                    mention=c.mention,
                    provenance=tuple(
                        ModelProvenance(
                            provider_id=key,
                            model_id=MODELS[key].model_id,
                            revision=MODELS[key].revision,
                            threshold=MODEL_THRESHOLDS[key],
                            original_score=c.source_scores[key],
                        )
                        for key in MODEL_THRESHOLDS
                        if key in c.source_scores
                    ),
                )
                for index, c in enumerate(selected)
            ),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            failure = None
            for key, adapter in self._adapters.items():
                try:
                    self._workers[key].submit(adapter.close).result()
                except Exception as error:
                    failure = error
            if failure is not None:
                raise PipelineError("CANDIDATE_PROPOSAL_FAILED") from None
        finally:
            for worker in self._workers.values():
                worker.shutdown(wait=True)
            self._loaded = False
