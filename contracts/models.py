from __future__ import annotations

import json
import math
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from version import PACKAGE_VERSION

LabelName = Annotated[str, Field(min_length=1, pattern=r"^\S(?:[^\r\n]*\S)?$")]


class EntityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: LabelName
    description: str = Field(min_length=1, pattern=r"\S")


class EntitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    labels: tuple[EntityLabel, ...] = Field(min_length=1)
    rejection_label: LabelName = "NOT_ENTITY_OR_MIXED"

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        names = [label.name for label in self.labels]
        if len(names) != len(set(names)):
            raise ValueError("entity label names must be unique")
        if self.rejection_label in names:
            raise ValueError("rejection label must not be an entity label")
        return self

    @property
    def label_names(self) -> tuple[str, ...]:
        return tuple(label.name for label in self.labels)

    @property
    def choices(self) -> tuple[str, ...]:
        return (*self.label_names, self.rejection_label)

    @property
    def schema_id(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return sha256(payload).hexdigest()


class EvidenceSource(StrEnum):
    CANDIDATE_MODEL = "CANDIDATE_MODEL"
    DECISION_PROVIDER = "DECISION_PROVIDER"


class ResolutionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICTED = "CONFLICTED"


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: EvidenceSource
    source_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class ConflictHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: LabelName
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["LABEL_DISAGREEMENT"] = "LABEL_DISAGREEMENT"
    hypotheses: tuple[ConflictHypothesis, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_hypotheses(self) -> Self:
        labels = [item.label for item in self.hypotheses]
        if len(labels) != len(set(labels)):
            raise ValueError("conflict hypotheses must use distinct labels")
        return self


class FinalEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    mention: str = Field(min_length=1)
    label: LabelName
    normalized: str = Field(min_length=1)
    span: Span
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    sources: tuple[SourceEvidence, ...] = Field(min_length=1)
    resolution_status: ResolutionStatus = ResolutionStatus.EXTRACTED
    conflict: Conflict | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if (self.resolution_status is ResolutionStatus.CONFLICTED) != (self.conflict is not None):
            raise ValueError("conflicted status and conflict metadata must agree")
        return self


class RuntimeSourceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    status: Literal["SUCCESS", "EMPTY"]
    model_id: str | None = None
    revision: str | None = None
    threshold: float | None = Field(default=None, ge=0, le=1)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_verification: Literal["FULL_SHA256", "TRUSTED_SOURCE_NOT_HASHED"] | None = None
    tokenizer_model_id: str | None = None
    tokenizer_revision: str | None = None


class ResolverMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolver_version: str = PACKAGE_VERSION
    policy_id: str
    schema_id: str
    decoder_version: str | None = None
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sources: tuple[RuntimeSourceIdentity, ...] = ()
    timings_ms: dict[str, float] = Field(default_factory=dict)

    @field_validator("timings_ms")
    @classmethod
    def validate_timings(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) or item < 0 for item in value.values()):
            raise ValueError("timings must be finite and nonnegative")
        return value


class EntityResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["entity-resolution-v1"] = "entity-resolution-v1"
    text: str
    entities: tuple[FinalEntity, ...]
    warnings: tuple[str, ...] = ()
    metadata: ResolverMetadata

    @model_validator(mode="after")
    def validate_entities(self) -> Self:
        ids = [entity.id for entity in self.entities]
        keys = [(entity.span.start, entity.span.end, entity.label) for entity in self.entities]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise ValueError("entity IDs and span/label keys must be unique")
        if keys != sorted(keys):
            raise ValueError("entities must follow span and label order")
        for entity in self.entities:
            if entity.span.end > len(self.text) or (
                entity.mention != self.text[entity.span.start : entity.span.end]
            ):
                raise ValueError("entity mention must match its span in the input text")
            if entity.conflict and entity.label not in {
                item.label for item in entity.conflict.hypotheses
            }:
                raise ValueError("entity label must belong to its conflict hypotheses")
        return self


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def text_has_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace characters")
        return value
