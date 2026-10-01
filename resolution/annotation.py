"""Scenario-neutral annotation that preserves the selected entity set."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.pipeline import AcceptedPrediction


@dataclass(frozen=True, slots=True)
class AnnotatedPrediction:
    prediction: AcceptedPrediction
    normalized: str

    @property
    def confidence(self) -> float:
        return self.prediction.confidence


def annotate_predictions(
    text: str, predictions: tuple[AcceptedPrediction, ...]
) -> tuple[AnnotatedPrediction, ...]:
    keys = [(item.start, item.end, item.label) for item in predictions]
    if len(keys) != len(set(keys)):
        raise ValueError("selected entity spans and labels must be unique")
    if any(
        not 0 <= item.start < item.end <= len(text) or text[item.start : item.end] != item.mention
        for item in predictions
    ):
        raise ValueError("annotation input does not match the original text")
    return tuple(AnnotatedPrediction(item, item.mention) for item in predictions)
