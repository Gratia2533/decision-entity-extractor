"""Strict input-text span contracts for candidate inference."""

from __future__ import annotations

import math
from dataclasses import dataclass


class CandidateValidationError(ValueError):
    """Raised when a proposed span cannot be grounded in the raw query."""


@dataclass(frozen=True, slots=True)
class SpanCandidate:
    start: int
    end: int
    mention: str
    score: float
    # Entity label descriptions supplied as type inputs to the candidate model.
    # Records which descriptions produced this span, including after deduplication.
    probes: tuple[str, ...] = ()

    @classmethod
    def from_offsets(
        cls,
        query: str,
        start: object,
        end: object,
        score: object,
        *,
        mention: object | None = None,
        probes: tuple[str, ...] = (),
    ) -> SpanCandidate:
        if isinstance(start, bool) or not isinstance(start, int):
            raise CandidateValidationError("candidate start must be an integer")
        if isinstance(end, bool) or not isinstance(end, int):
            raise CandidateValidationError("candidate end must be an integer")
        if not 0 <= start < end <= len(query):
            raise CandidateValidationError(
                f"candidate [{start}, {end}) is outside query length {len(query)}"
            )
        try:
            normalized_score = float(score)
        except (TypeError, ValueError) as exc:
            raise CandidateValidationError("candidate score must be numeric") from exc
        if (
            isinstance(score, bool)
            or not math.isfinite(normalized_score)
            or not 0 <= normalized_score <= 1
        ):
            raise CandidateValidationError("candidate score must be a finite probability")
        surface = query[start:end]
        if mention is not None and mention != surface:
            raise CandidateValidationError(
                f"candidate surface mismatch at [{start}, {end}): {mention!r} != {surface!r}"
            )
        if any(not isinstance(probe, str) or not probe for probe in probes):
            raise CandidateValidationError("candidate probes must be non-empty strings")
        return cls(start, end, surface, normalized_score, tuple(sorted(set(probes))))


@dataclass(frozen=True, slots=True)
class QueryInference:
    candidates: tuple[SpanCandidate, ...]
    encoded_tokens: int
    max_sequence_tokens: int
    native_span_count: int
    representable_boundaries: tuple[tuple[int, int], ...]
    truncated: bool
    cache_mode: str


def deduplicate_candidates(candidates: list[SpanCandidate]) -> tuple[SpanCandidate, ...]:
    """Keep max score and complete probe provenance for each exact boundary."""

    grouped: dict[tuple[int, int], list[SpanCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault((candidate.start, candidate.end), []).append(candidate)
    result = []
    for values in grouped.values():
        best = max(values, key=lambda item: item.score)
        probes = tuple(sorted({probe for item in values for probe in item.probes}))
        result.append(SpanCandidate(best.start, best.end, best.mention, best.score, probes))
    return tuple(sorted(result, key=lambda item: (-item.score, item.start, item.end)))


__all__ = [
    "CandidateValidationError",
    "QueryInference",
    "SpanCandidate",
    "deduplicate_candidates",
]
