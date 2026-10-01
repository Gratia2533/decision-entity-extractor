from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts.errors import PipelineError, PipelineErrorCode
from contracts.models import (
    EntityResolutionResult,
    ResolverMetadata,
)
from transport.api import create_app


class FakeResolver:
    ready = True

    async def resolve_async(self, request):
        return EntityResolutionResult(
            text=request.text,
            entities=(),
            warnings=("NO_ENTITY_EVIDENCE",),
            metadata=ResolverMetadata(policy_id="TEST", schema_id="a" * 64),
        )


def test_api_resolves_generic_text_and_exposes_readiness() -> None:
    client = TestClient(create_app(FakeResolver()))
    assert client.get("/health/ready").json() == {"ready": True}
    response = client.post("/resolve", json={"text": "Book a table in Taipei"})
    assert response.status_code == 200
    assert response.json()["text"] == "Book a table in Taipei"


def test_api_rejects_blank_text_and_unknown_fields() -> None:
    client = TestClient(create_app(FakeResolver()))
    assert client.post("/resolve", json={"text": "   "}).status_code == 422
    assert client.post("/resolve", json={"text": "Alice", "debug": True}).status_code == 422


@pytest.mark.parametrize("code", list(PipelineErrorCode))
def test_api_maps_every_pipeline_error_without_private_details(code):
    class FailingResolver(FakeResolver):
        async def resolve_async(self, request):
            raise PipelineError(code) from RuntimeError("unsafe vendor body")

    with TestClient(create_app(FailingResolver())) as client:
        response = client.post("/resolve", json={"text": "Alice"})
    error = PipelineError(code)
    assert response.status_code == error.http_status
    assert response.json() == error.response().model_dump(mode="json")
    assert "unsafe" not in response.text


def test_readiness_and_openapi_error_contracts():
    resolver = FakeResolver()
    resolver.ready = False
    with TestClient(create_app(resolver)) as client:
        assert client.get("/health/ready").status_code == 503
        schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/resolve"]["post"]["responses"]
    assert {"422", "502", "503", "504"} <= responses.keys()
    assert "anyOf" in responses["422"]["content"]["application/json"]["schema"]
