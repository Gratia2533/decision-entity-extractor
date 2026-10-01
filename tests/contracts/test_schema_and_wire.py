from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from adapters.typesafe.wire import (
    build_requests,
    classify_responses,
    prompt_for,
)
from contracts.errors import PipelineError, PipelineErrorCode
from contracts.models import EntityLabel, EntitySchema
from tests.factories import candidate, generic_schema, snapshot


def test_schema_accepts_arbitrary_unique_labels() -> None:
    schema = EntitySchema(labels=(EntityLabel(name="DEVICE", description="A physical device"),))
    assert schema.choices == ("DEVICE", "NOT_ENTITY_OR_MIXED")
    assert len(schema.schema_id) == 64


def test_schema_rejects_duplicate_and_rejection_labels() -> None:
    with pytest.raises(ValidationError):
        EntitySchema(
            labels=(
                EntityLabel(name="PERSON", description="one"),
                EntityLabel(name="PERSON", description="two"),
            )
        )
    with pytest.raises(ValidationError):
        EntitySchema(
            labels=(EntityLabel(name="REJECT", description="invalid"),),
            rejection_label="REJECT",
        )


def test_prompt_and_request_are_generated_from_schema() -> None:
    schema = generic_schema()
    source = snapshot(schema=schema)
    prompt = prompt_for(schema)
    request = build_requests(source)[0]
    question = request["questions"]["s000"]

    assert tuple(prompt["criteria"]) == schema.choices
    assert prompt["criteria"]["PLACE"] == "A named geographic place"
    assert question["criteria"] == prompt["criteria"]
    assert question["instructions"]["candidate"] == {
        "text": "Alice",
        "left_context": "",
        "right_context": " visited Taipei",
    }
    serialized = json.dumps(request).casefold()
    assert not any(term in serialized for term in ("salary", "employer", "recruit"))


def _wire_response(schema: EntitySchema, selected: str) -> str:
    probabilities = {choice: 0.05 for choice in schema.choices}
    probabilities[selected] = 0.9
    return json.dumps(
        {
            "model": "jev-1.13.0",
            "answers": {
                "s000": {
                    "type": "choice",
                    "choice": selected,
                    "confidence": 0.9,
                    "probabilities": probabilities,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
    )


def test_response_acceptance_and_rejection_use_configured_schema() -> None:
    schema = generic_schema()
    source = snapshot(schema=schema)
    accepted = classify_responses(source, (_wire_response(schema, "PERSON"),))
    rejected = classify_responses(source, (_wire_response(schema, schema.rejection_label),))

    assert accepted.accepted_predictions[0].label == "PERSON"
    assert rejected.accepted_predictions == ()
    assert rejected.decisions[0].accepted is False


def test_response_rejects_unconfigured_probability_keys() -> None:
    schema = generic_schema()
    source = snapshot(schema=schema)
    payload = json.loads(_wire_response(schema, "PERSON"))
    payload["answers"]["s000"]["probabilities"]["UNKNOWN"] = 0.01

    with pytest.raises(PipelineError) as raised:
        classify_responses(source, (json.dumps(payload),))
    assert raised.value.code is PipelineErrorCode.DECISION_RESPONSE_INVALID


def test_case_and_unicode_labels_and_custom_rejection() -> None:
    schema = EntitySchema(
        labels=(
            EntityLabel(name="device-kind", description="A physical device"),
            EntityLabel(name="地點", description="A named place"),
        ),
        rejection_label="reject",
    )
    source = snapshot(schema=schema)
    accepted = classify_responses(source, (_wire_response(schema, "device-kind"),))
    assert accepted.accepted_predictions[0].label == "device-kind"
    assert (
        classify_responses(source, (_wire_response(schema, "reject"),)).decisions[0].accepted
        is False
    )


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_label_and_description_reject_blank_values(value) -> None:
    with pytest.raises(ValidationError):
        EntityLabel(name=value, description="A place")
    with pytest.raises(ValidationError):
        EntityLabel(name="PLACE", description=value)


def test_response_order_and_raw_probability_mass_do_not_change_decisions() -> None:
    source = snapshot()
    payload = json.loads(_wire_response(source.entity_schema, "PERSON"))
    payload["answers"]["s000"]["probabilities"] = {
        source.entity_schema.rejection_label: 0.001,
        "PLACE": 0.001,
        "PERSON": 0.91,
    }
    chosen = classify_responses(source, (json.dumps(payload),))
    assert chosen.accepted_predictions[0].confidence == 0.91
    assert tuple(chosen.decisions[0].probabilities) == source.entity_schema.choices


def test_equal_probabilities_use_schema_order_instead_of_provider_choice() -> None:
    source = snapshot()
    payload = json.loads(_wire_response(source.entity_schema, "PERSON"))
    payload["answers"]["s000"].update(
        choice="PLACE", probabilities={name: 0.5 for name in source.entity_schema.choices}
    )
    chosen = classify_responses(source, (json.dumps(payload),))
    assert chosen.decisions[0].selected_label == "PERSON"
    assert chosen.decisions[0].tie_break_applied


@pytest.mark.parametrize(
    "fault",
    [
        "ids",
        "model",
        "choice",
        "not_max",
        "nan",
        "negative",
        "extra_field",
        "missing_key",
        "null_usage",
        "wrong_type",
    ],
)
def test_invalid_response_fails_closed(fault) -> None:
    source = snapshot()
    payload = json.loads(_wire_response(source.entity_schema, "PERSON"))
    answer = payload["answers"]["s000"]
    if fault == "ids":
        payload["answers"]["unknown"] = payload["answers"].pop("s000")
    elif fault == "model":
        payload["model"] = "other-model"
    elif fault == "choice":
        answer["choice"] = "unconfigured"
    elif fault == "not_max":
        answer["choice"] = "PLACE"
    elif fault == "nan":
        answer["probabilities"]["PERSON"] = float("nan")
    elif fault == "negative":
        answer["probabilities"]["PERSON"] = -0.1
    elif fault == "extra_field":
        answer["raw"] = "unsafe"
    elif fault == "missing_key":
        del answer["probabilities"]["PLACE"]
    elif fault == "null_usage":
        payload["usage"] = None
    elif fault == "wrong_type":
        answer["type"] = "score"
    with pytest.raises(PipelineError, match="invalid data"):
        classify_responses(source, (json.dumps(payload),))


def test_duplicate_json_keys_and_partial_batches_fail_closed() -> None:
    source = snapshot()
    with pytest.raises(PipelineError):
        classify_responses(source, ('{"model":"jev-1.13.0","model":"jev-1.13.0"}',))
    with pytest.raises(PipelineError):
        classify_responses(source, ())


def test_request_chunking_and_empty_candidate_short_circuit() -> None:
    text = "a" * 17
    source = snapshot(
        text, candidates=tuple(candidate(f"s{i:03d}", text, i, i + 1) for i in range(17))
    )
    requests = build_requests(source)
    assert [len(request["questions"]) for request in requests] == [16, 1]
    empty = snapshot(candidates=())
    assert build_requests(empty) == ()
    assert classify_responses(empty, ()).accepted_predictions == ()


def test_request_budget_checks_utf8_text_and_configured_descriptions() -> None:
    oversized = snapshot("字" * 10001, candidates=(candidate("s000", "字" * 10001, 0, 1),))
    with pytest.raises(PipelineError) as error:
        build_requests(oversized)
    assert error.value.code is PipelineErrorCode.QUERY_TOO_LONG
    schema = EntitySchema(labels=(EntityLabel(name="thing", description="x" * 30001),))
    with pytest.raises(PipelineError):
        build_requests(snapshot(schema=schema))
