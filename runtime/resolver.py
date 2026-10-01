"""Public entity-resolution facade."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.errors import PipelineError
from contracts.models import (
    EntityResolutionResult,
    ResolveRequest,
    ResolverMetadata,
    RuntimeSourceIdentity,
)
from model_specs import MODEL_THRESHOLDS
from resolution.entities import resolve_entities
from runtime.pipeline import PipelineResult, PipelineService
from runtime.telemetry import log_resolution_metrics


@dataclass(frozen=True)
class ResolutionExecution:
    result: EntityResolutionResult
    timings_ms: dict[str, float]
    candidate_count: int


class EntityResolver:
    def __init__(self, pipeline: PipelineService) -> None:
        self._pipeline = pipeline
        self._closed = False

    @property
    def ready(self) -> bool:
        return not self._closed and self._pipeline.ready

    def resolve(self, request: ResolveRequest) -> EntityResolutionResult:
        return self.resolve_with_trace(request).result

    async def resolve_async(self, request: ResolveRequest) -> EntityResolutionResult:
        return (await self.resolve_with_trace_async(request)).result

    def resolve_with_trace(self, request: ResolveRequest) -> ResolutionExecution:
        request = self._validate(request)
        return self._project(request, self._pipeline.resolve(request.text))

    async def resolve_with_trace_async(self, request: ResolveRequest) -> ResolutionExecution:
        request = self._validate(request)
        return self._project(request, await self._pipeline.resolve_async(request.text))

    def _validate(self, request) -> ResolveRequest:
        if not self.ready:
            raise PipelineError("SERVICE_NOT_READY")
        return ResolveRequest.model_validate(request)

    def _project(self, request: ResolveRequest, result: PipelineResult) -> ResolutionExecution:
        snapshot = result.selection.source.snapshot
        if snapshot.raw_text != request.text:
            raise PipelineError("DECISION_RESPONSE_INVALID")
        selected = result.selection.final_predictions
        if tuple(item.prediction for item in result.annotations) != selected:
            raise PipelineError("DECISION_RESPONSE_INVALID")
        try:
            entities = resolve_entities(
                snapshot, result.annotations, provider_id=result.provider_id
            )
        except ValueError:
            raise PipelineError("DECISION_RESPONSE_INVALID") from None
        identities = []
        for key in ("CM", "BM"):
            identity = self._pipeline.source_identity[key]
            tokenizer = identity.get("runtime_tokenizer", {})
            identities.append(
                RuntimeSourceIdentity(
                    source_id=key,
                    status="SUCCESS",
                    model_id=identity["model_id"],
                    revision=identity["revision"],
                    threshold=MODEL_THRESHOLDS[key],
                    artifact_sha256=identity["artifact_sha256"],
                    content_verification=identity.get("content_verification"),
                    tokenizer_model_id=tokenizer.get("model_id"),
                    tokenizer_revision=tokenizer.get("revision"),
                )
            )
        identities.append(
            RuntimeSourceIdentity(
                source_id=result.provider_id,
                status="SUCCESS" if result.selection.source.decisions else "EMPTY",
                model_id=result.decision_model,
            )
        )
        timings = {name: value / 1e6 for name, value in result.timings_ns.items()}
        metadata = ResolverMetadata(
            policy_id=result.policy_id,
            schema_id=snapshot.entity_schema.schema_id,
            decoder_version=result.decoder_version,
            prompt_hash=result.prompt_hash,
            sources=tuple(identities),
            timings_ms=timings,
        )
        contract = EntityResolutionResult(
            text=request.text,
            entities=entities,
            warnings=() if entities else ("NO_ENTITY_EVIDENCE",),
            metadata=metadata,
        )
        log_resolution_metrics(
            metadata=metadata,
            entity_count=len(entities),
            candidate_count=len(snapshot.candidates),
        )
        return ResolutionExecution(contract, timings, len(snapshot.candidates))

    def close(self) -> None:
        self._closed = True
        self._pipeline.close()

    async def aclose(self) -> None:
        self._closed = True
        await self._pipeline.aclose()
