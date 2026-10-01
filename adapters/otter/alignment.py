"""Provable query-local offset mapping for Otter cross encoders."""

from __future__ import annotations

from collections.abc import Sequence

from adapters.otter.contracts import CandidateValidationError

CONTAINED_OFFSETS = "contained_offsets"
BOUNDARY_TOKEN_PARITY = "boundary_token_parity"


def _offsets_at(
    offsets: Sequence[Sequence[int]], indices: Sequence[int], *, label: str
) -> list[tuple[int, int]]:
    if not indices or list(indices) != sorted(set(indices)):
        raise CandidateValidationError(f"{label} token indices must be non-empty and ordered")
    result = []
    for index in indices:
        if not 0 <= index < len(offsets) or len(offsets[index]) != 2:
            raise CandidateValidationError(f"{label} token offset is missing or malformed")
        start, end = offsets[index]
        if type(start) is not int or type(end) is not int:
            raise CandidateValidationError(f"{label} token offsets must be integers")
        result.append((start, end))
    if any(
        current[0] < previous[0] or current[1] < previous[1]
        for previous, current in zip(result, result[1:], strict=False)
    ):
        raise CandidateValidationError(f"{label} token offsets are not monotonic")
    return result


def cross_query_offset_mode(
    combined_offsets: Sequence[Sequence[int]],
    combined_text_indices: Sequence[int],
    *,
    prefix: str,
    query_length: int,
) -> str:
    """Classify only the two offset layouts with a lossless coordinate proof."""

    if query_length <= 0:
        raise CandidateValidationError("cross query must be non-empty")
    prefix_length = len(prefix)
    query_end = prefix_length + query_length
    offsets = _offsets_at(combined_offsets, combined_text_indices, label="combined")
    contained = [prefix_length <= start < end <= query_end for start, end in offsets]
    if all(contained):
        return CONTAINED_OFFSETS

    crossing = [
        index for index, (start, end) in enumerate(offsets) if 0 <= start < prefix_length < end
    ]
    if crossing != [0] or not all(contained[1:]):
        raise CandidateValidationError("combined query offsets have an unsupported boundary layout")
    crossing_start, crossing_end = offsets[0]
    prefix_fragment = prefix[crossing_start:prefix_length]
    if crossing_end > query_end or not prefix_fragment or not prefix_fragment.isspace():
        raise CandidateValidationError("cross-boundary token consumes non-whitespace prefix text")
    return BOUNDARY_TOKEN_PARITY


def align_cross_query_offsets(
    combined_token_ids: Sequence[int],
    combined_offsets: Sequence[Sequence[int]],
    combined_text_indices: Sequence[int],
    *,
    prefix: str,
    query_length: int,
    query_token_ids: Sequence[int] | None = None,
    query_offsets: Sequence[Sequence[int]] | None = None,
    query_text_indices: Sequence[int] | None = None,
) -> tuple[tuple[tuple[int, int], ...], bool]:
    """Map model tokens without clipping, searching, or dropping model tokens."""

    mode = cross_query_offset_mode(
        combined_offsets, combined_text_indices, prefix=prefix, query_length=query_length
    )
    prefix_length = len(prefix)
    aligned = [tuple(int(value) for value in offset) for offset in combined_offsets]
    raw_offsets = _offsets_at(combined_offsets, combined_text_indices, label="combined")
    if mode == CONTAINED_OFFSETS:
        for combined_index, (start, end) in zip(combined_text_indices, raw_offsets, strict=True):
            aligned[combined_index] = (start - prefix_length, end - prefix_length)
        return tuple(aligned), raw_offsets[-1][1] < prefix_length + query_length

    if query_token_ids is None or query_offsets is None or query_text_indices is None:
        raise CandidateValidationError("cross-boundary token requires query-only parity evidence")
    if any(not 0 <= index < len(combined_token_ids) for index in combined_text_indices):
        raise CandidateValidationError("combined token ID is missing")
    if any(not 0 <= index < len(query_token_ids) for index in query_text_indices):
        raise CandidateValidationError("query-only token ID is missing")
    query_local_offsets = _offsets_at(query_offsets, query_text_indices, label="query-only")
    combined_text_ids = [combined_token_ids[index] for index in combined_text_indices]
    query_text_ids = [query_token_ids[index] for index in query_text_indices]
    if combined_text_ids != query_text_ids:
        raise CandidateValidationError(
            "combined-prompt and query-only token IDs cannot be mapped exactly"
        )
    if not all(0 <= start < end <= query_length for start, end in query_local_offsets):
        raise CandidateValidationError("query-only tokenizer offsets are outside the raw query")
    for combined_index, offset in zip(combined_text_indices, query_local_offsets, strict=True):
        aligned[combined_index] = offset
    return tuple(aligned), query_local_offsets[-1][1] < query_length


__all__ = [
    "BOUNDARY_TOKEN_PARITY",
    "CONTAINED_OFFSETS",
    "align_cross_query_offsets",
    "cross_query_offset_mode",
]
