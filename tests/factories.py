from __future__ import annotations

from contracts.models import EntityLabel, EntitySchema
from contracts.pipeline import (
    CompareCandidate,
    CompareConfig,
    CompareResult,
    CompareSnapshot,
    ModelProvenance,
    SpanDecision,
    accepted_predictions,
)
from model_specs import MODEL_THRESHOLDS, MODELS


def generic_schema() -> EntitySchema:
    return EntitySchema(
        labels=(
            EntityLabel(name="PERSON", description="A named person"),
            EntityLabel(name="PLACE", description="A named geographic place"),
        )
    )


def candidate(candidate_id: str, text: str, start: int, end: int) -> CompareCandidate:
    return CompareCandidate(
        candidate_id=candidate_id,
        start=start,
        end=end,
        mention=text[start:end],
        provenance=(
            ModelProvenance(
                provider_id="CM",
                model_id=MODELS["CM"].model_id,
                revision=MODELS["CM"].revision,
                threshold=MODEL_THRESHOLDS["CM"],
                original_score=0.8,
            ),
        ),
    )


def snapshot(
    text: str = "Alice visited Taipei",
    *,
    schema: EntitySchema | None = None,
    candidates: tuple[CompareCandidate, ...] | None = None,
) -> CompareSnapshot:
    return CompareSnapshot(
        case_id="q000",
        raw_text=text,
        entity_schema=schema or generic_schema(),
        config=CompareConfig(),
        candidates=candidates if candidates is not None else (candidate("s000", text, 0, 5),),
        source_statuses=("CM:SUCCESS", "BM:SUCCESS"),
    )


def decision(
    candidate_id: str,
    schema: EntitySchema,
    selected: str,
    confidence: float,
) -> SpanDecision:
    other_score = (1.0 - confidence) / (len(schema.choices) - 1)
    probabilities = {
        choice: confidence if choice == selected else other_score for choice in schema.choices
    }
    return SpanDecision(
        candidate_id=candidate_id,
        selected_label=selected,
        accepted=selected != schema.rejection_label,
        probabilities=probabilities,
        selected_probability=confidence,
        provider_choice=selected,
        tie_break_applied=False,
    )


def result(
    source: CompareSnapshot,
    decisions: tuple[SpanDecision, ...],
) -> CompareResult:
    return CompareResult(
        case_id=source.case_id,
        source_response_hash="b" * 64,
        provider_id="test",
        decision_model="test-model",
        prompt_hash="c" * 64,
        snapshot=source,
        decisions=decisions,
        accepted_predictions=accepted_predictions(source, decisions),
    )
