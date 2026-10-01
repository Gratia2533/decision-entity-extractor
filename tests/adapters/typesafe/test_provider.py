"""Exercise the pinned SDK and real adapter over an in-memory HTTP transport."""

import asyncio
import json

import pytest

from adapters.typesafe.provider import TypeSafeDecisionProvider
from contracts.errors import PipelineError, PipelineErrorCode
from tests.factories import snapshot

httpx2 = pytest.importorskip("httpx2")
sdk = pytest.importorskip("typesafe_sdk")


def response_for(request):
    payload = json.loads(request.content)
    answers = {}
    for name, question in payload["questions"].items():
        # SDK Choice also validates that criteria is a mapping on the actual request.
        sdk.Choice.model_validate(question)
        choices = tuple(question["criteria"])
        answers[name] = {
            "type": "choice",
            "choice": choices[0],
            "confidence": 0.95,
            "probabilities": {label: 0.95 if i == 0 else 0.025 for i, label in enumerate(choices)},
        }
    return {
        "model": payload["model"],
        "answers": answers,
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


def test_sdk_transport_sends_generated_criteria_and_retains_metadata(monkeypatch, caplog):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-credential")
    calls = []

    def transport(request):
        calls.append(request.url.path)
        return httpx2.Response(200, json=response_for(request))

    provider = TypeSafeDecisionProvider(transport_factory=lambda: httpx2.MockTransport(transport))

    async def run():
        try:
            await provider.initialize()
            assert calls == []
            empty = await provider.decide(snapshot(candidates=()))
            assert empty.decisions == () and calls == []
            answer = await provider.decide(snapshot())
            assert answer.provider_id == "typesafe"
            assert answer.decision_model == "jev-1.13.0"
            assert answer.accepted_predictions[0].confidence == 0.95
            assert len(answer.prompt_hash) == 64
        finally:
            await provider.aclose()
            await provider.aclose()

    asyncio.run(run())
    assert calls == ["/v1/systemone"]
    assert "test-credential" not in caplog.text
    assert "Alice" not in caplog.text


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "DECISION_CONFIGURATION_ERROR"),
        (403, "DECISION_CONFIGURATION_ERROR"),
        (422, "DECISION_RESPONSE_INVALID"),
        (429, "DECISION_PROVIDER_UNAVAILABLE"),
        (503, "DECISION_PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_http_errors_are_safe_and_not_retried(monkeypatch, status, code):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-credential")
    calls = []

    def transport(request):
        calls.append(request)
        return httpx2.Response(status, json={"message": "unsafe body"})

    provider = TypeSafeDecisionProvider(transport_factory=lambda: httpx2.MockTransport(transport))

    async def run():
        try:
            with pytest.raises(PipelineError) as error:
                await provider.decide(snapshot())
            assert error.value.code is PipelineErrorCode(code)
            assert "unsafe" not in str(error.value)
        finally:
            await provider.aclose()

    asyncio.run(run())
    assert len(calls) == 1


def test_provider_timeout_and_missing_configuration(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(PipelineError) as error:
        TypeSafeDecisionProvider()
    assert error.value.code is PipelineErrorCode.DECISION_CONFIGURATION_ERROR
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-credential")

    def transport(request):
        raise httpx2.ReadTimeout("unsafe timeout", request=request)

    provider = TypeSafeDecisionProvider(transport_factory=lambda: httpx2.MockTransport(transport))

    async def run():
        try:
            with pytest.raises(PipelineError) as error:
                await provider.decide(snapshot())
            assert error.value.code is PipelineErrorCode.DECISION_TIMEOUT
        finally:
            await provider.aclose()

    asyncio.run(run())
