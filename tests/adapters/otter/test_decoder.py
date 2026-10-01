import math

import pytest

from adapters.otter.alignment import align_cross_query_offsets
from adapters.otter.contracts import CandidateValidationError
from adapters.otter.runtime import (
    SpanSpace,
    build_span_space,
    decode_raw_candidates,
)


def test_mask_first_threshold_inclusive_decode_keeps_overlapping_spans():
    space = build_span_space(
        [None, 0, 0, None],
        [(0, 0), (0, 1), (1, 2), (0, 0)],
        max_span_tokens=2,
        text_start_index=0,
        char_shift=0,
        text_length=2,
    )
    logits = [1000.0 if not valid else 0.0 for valid in space.valid]
    candidates, boundaries = decode_raw_candidates(
        "AB",
        [(0, 0), (0, 1), (1, 2), (0, 0)],
        space,
        [logits, logits],
        probes=("A person", "A place"),
        threshold=0.5,
    )
    assert boundaries == ((0, 1), (0, 2), (1, 2))
    assert {(item.start, item.end) for item in candidates} == set(boundaries)
    assert all(item.score == 0.5 for item in candidates)
    assert all(item.probes == ("A person", "A place") for item in candidates)


@pytest.mark.parametrize("logit,expected", [(-1000.0, 0.0), (1000.0, 1.0)])
def test_decode_handles_extreme_finite_logits(logit, expected):
    space = SpanSpace(((0, 0),), (1,), (True,), 0, 0)
    candidates, _ = decode_raw_candidates(
        "A", [(0, 1)], space, [[logit]], probes=("entity",), threshold=0.0
    )
    assert candidates[0].score == expected


@pytest.mark.parametrize("logit", [math.nan, math.inf, -math.inf])
def test_decode_rejects_nonfinite_active_logits(logit):
    space = SpanSpace(((0, 0),), (1,), (True,), 0, 0)
    with pytest.raises(CandidateValidationError):
        decode_raw_candidates("A", [(0, 1)], space, [[logit]], probes=("entity",), threshold=0.5)


def test_cross_encoder_offsets_translate_and_detect_truncation():
    aligned, truncated = align_cross_query_offsets(
        [0, 1], [(0, 3), (3, 8)], [1], prefix="p: ", query_length=5
    )
    assert aligned[1] == (0, 5) and not truncated
    _, truncated = align_cross_query_offsets(
        [0, 1], [(0, 3), (3, 7)], [1], prefix="p: ", query_length=5
    )
    assert truncated


def test_cross_boundary_offsets_require_exact_token_parity():
    args = dict(
        prefix="p: ",
        query_length=5,
        query_token_ids=[7],
        query_offsets=[(0, 5)],
        query_text_indices=[0],
    )
    aligned, truncated = align_cross_query_offsets([0, 7], [(0, 2), (2, 8)], [1], **args)
    assert aligned[1] == (0, 5) and not truncated
    with pytest.raises(CandidateValidationError):
        align_cross_query_offsets([0, 8], [(0, 2), (2, 8)], [1], **args)
    with pytest.raises(CandidateValidationError):
        align_cross_query_offsets([0, 7], [(0, 1), (1, 8)], [1], **args)
