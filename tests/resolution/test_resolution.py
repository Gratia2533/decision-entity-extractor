from dataclasses import replace

import pytest
from pydantic import ValidationError

from contracts.models import (
    EntityResolutionResult,
    ResolutionStatus,
    ResolverMetadata,
)
from contracts.pipeline import AcceptedPrediction
from resolution.annotation import annotate_predictions
from resolution.entities import resolve_entities
from tests.factories import snapshot


def prediction(label="PERSON", confidence=0.95):
    return AcceptedPrediction(
        candidate_id="s000", mention="Alice", start=0, end=5, label=label, confidence=confidence
    )


def test_surface_annotation_and_source_fusion_preserve_selection_and_raw_score():
    source = snapshot()
    selected = (prediction(),)
    annotations = annotate_predictions(source.raw_text, selected)
    entities = resolve_entities(source, annotations, provider_id="example")
    assert annotations[0].prediction == selected[0]
    assert entities[0].normalized == "Alice"
    assert entities[0].resolution_status is ResolutionStatus.EXTRACTED
    assert entities[0].confidence == 0.95
    assert [(item.source_id, item.confidence) for item in entities[0].sources] == [
        ("CM", 0.8),
        ("example", 0.95),
    ]


def test_exact_span_label_conflicts_preserve_hypotheses_and_scores():
    source = snapshot()
    annotations = annotate_predictions(source.raw_text, (prediction("PLACE", 0.92), prediction()))
    entities = resolve_entities(source, annotations, provider_id="example")
    assert [item.label for item in entities] == ["PERSON", "PLACE"]
    assert all(item.resolution_status is ResolutionStatus.CONFLICTED for item in entities)
    assert entities[0].conflict == entities[1].conflict
    assert [(item.label, item.confidence) for item in entities[0].conflict.hypotheses] == [
        ("PERSON", 0.95),
        ("PLACE", 0.92),
    ]
    assert [item.confidence for item in entities] == [0.95, 0.92]


def test_annotation_and_resolution_reject_mutated_or_duplicate_selection():
    with pytest.raises(ValueError):
        annotate_predictions("Alice", (prediction(), prediction()))
    invalid = prediction().model_copy(update={"end": 7})
    with pytest.raises(ValueError):
        annotate_predictions("Alice", (invalid,))
    annotations = annotate_predictions("Alice", (prediction(),))
    with pytest.raises(ValueError):
        resolve_entities(
            snapshot(),
            (replace(annotations[0], prediction=prediction("unknown")),),
            provider_id="example",
        )


def test_final_contract_validates_spans_order_and_deterministic_serialization():
    source = snapshot()
    entities = resolve_entities(
        source, annotate_predictions(source.raw_text, (prediction(),)), provider_id="example"
    )
    contract = EntityResolutionResult(
        text=source.raw_text,
        entities=entities,
        metadata=ResolverMetadata(policy_id="test", schema_id="a" * 64),
    )
    assert EntityResolutionResult.model_validate_json(contract.model_dump_json()) == contract
    rebuilt = EntityResolutionResult.model_validate(contract.model_dump(mode="json"))
    assert contract.model_dump_json() == rebuilt.model_dump_json()
    with pytest.raises(ValidationError):
        EntityResolutionResult(text="wrong", entities=entities, metadata=contract.metadata)
    with pytest.raises(ValidationError):
        EntityResolutionResult(
            text=source.raw_text, entities=entities * 2, metadata=contract.metadata
        )
