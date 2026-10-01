from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.pipeline import AcceptedPrediction
from resolution.recovery import recover_gaps
from resolution.selection import confidence_gate, containment_nms
from tests.factories import candidate, decision, generic_schema, result, snapshot


def prediction(
    candidate_id: str,
    start: int,
    end: int,
    label: str,
    confidence: float,
) -> AcceptedPrediction:
    text = "Alice visited Taipei"
    return AcceptedPrediction(
        candidate_id=candidate_id,
        mention=text[start:end],
        start=start,
        end=end,
        label=label,
        confidence=confidence,
    )


def test_snapshot_enforces_span_integrity_and_candidate_order() -> None:
    text = "Alice visited Taipei"
    with pytest.raises(ValidationError):
        snapshot(
            text,
            candidates=(
                candidate("s000", text, 14, 20),
                candidate("s001", text, 0, 5),
            ),
        )
    invalid = candidate("s000", text, 0, 5).model_copy(update={"mention": "wrong"})
    with pytest.raises(ValidationError):
        snapshot(text, candidates=(invalid,))


def test_confidence_gate_and_same_label_containment_are_generic() -> None:
    outer = prediction("s000", 0, 5, "PERSON", 0.95)
    inner = prediction("s001", 1, 4, "PERSON", 0.90)
    other_label = prediction("s002", 1, 4, "PLACE", 0.92)
    low = prediction("s003", 14, 20, "PLACE", 0.79)

    retained, confidence_decisions = confidence_gate((outer, inner, other_label, low), 0.8)
    final, suppressions = containment_nms(retained, 0.0)

    assert {item.candidate_id for item in final} == {"s000", "s002"}
    assert confidence_decisions[0].candidate_id == "s003"
    assert suppressions[0].winner_id == "s000"
    assert suppressions[0].loser_id == "s001"


def test_gap_recovery_preserves_current_threshold_policy() -> None:
    text = "Alice visited Taipei"
    schema = generic_schema()
    source = snapshot(
        text,
        schema=schema,
        candidates=(
            candidate("s000", text, 0, 5),
            candidate("s001", text, 14, 20),
        ),
    )
    decisions = (
        decision("s000", schema, "PERSON", 0.95),
        decision("s001", schema, "PLACE", 0.85),
    )

    recovered = recover_gaps(result(source, decisions))

    assert [item.candidate_id for item in recovered.final_predictions] == ["s000", "s001"]
    assert recovered.candidate_decisions[1].reason == "RECOVERED_GAP_LOW_THRESHOLD"
