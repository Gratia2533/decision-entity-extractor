"""Validated candidate and decision boundaries for the production pipeline."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.models import EntitySchema, LabelName
from hashing import content_sha256
from model_specs import MODEL_THRESHOLDS, MODELS

Index = Annotated[int, Field(strict=True, ge=0)]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


REQUEST_BUILDER_VERSION = "decision-v1"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CompareConfig(FrozenModel):
    """Limits for classifying the combined Otter candidate pool."""

    schema_version: Literal["entity-decision-config-v1"] = "entity-decision-config-v1"
    policy_id: Literal["otter-union-decision-v1"] = "otter-union-decision-v1"
    max_candidates: Literal[64] = 64
    questions_per_request: Literal[16] = 16
    request_byte_budget: Literal[60_000] = 60_000
    state_question_byte_budget: Literal[30_000] = 30_000
    automatic_retries: Literal[0] = 0
    prediction_cache: Literal[False] = False


class ModelProvenance(FrozenModel):
    # Stable model keys; full checkpoint identities are defined in model_specs.MODELS.
    # CM identifies the Otter cross-encoder; BM identifies the Otter bi-encoder.
    provider_id: Literal["CM", "BM"]
    model_id: str
    revision: str
    threshold: Probability
    original_score: Probability


class CompareCandidate(FrozenModel):
    """One source-text span proposed by either or both candidate models."""

    candidate_id: str = Field(pattern=r"^s[0-9]{3,}$")
    start: Index
    end: Index
    mention: str = Field(min_length=1)
    provenance: tuple[ModelProvenance, ...] = Field(min_length=1)


class CompareSnapshot(FrozenModel):
    """Validated input text, schema, and candidate pool sent for decision scoring."""

    schema_version: Literal["entity-candidate-snapshot-v1"] = "entity-candidate-snapshot-v1"
    case_id: str = Field(pattern=r"^q[0-9]{3}$")
    raw_text: str = Field(min_length=1)
    entity_schema: EntitySchema
    config: CompareConfig
    candidates: tuple[CompareCandidate, ...]
    source_statuses: tuple[Literal["CM:SUCCESS", "BM:SUCCESS"], ...]

    @model_validator(mode="after")
    def check_snapshot(self) -> Self:
        if self.source_statuses != ("CM:SUCCESS", "BM:SUCCESS"):
            raise ValueError("CM and BM must both succeed in canonical order")
        boundaries = [(item.start, item.end) for item in self.candidates]
        if boundaries != sorted(set(boundaries)) or len(boundaries) > self.config.max_candidates:
            raise ValueError("candidate boundaries must be sorted, unique and within capacity")
        for number, item in enumerate(self.candidates):
            if item.candidate_id != f"s{number:03d}":
                raise ValueError("candidate IDs must follow boundary order")
            if not 0 <= item.start < item.end <= len(self.raw_text):
                raise ValueError("candidate offset outside text")
            if self.raw_text[item.start : item.end] != item.mention:
                raise ValueError("candidate mention differs from original text")
            sources = tuple(source.provider_id for source in item.provenance)
            if sources != tuple(key for key in MODEL_THRESHOLDS if key in sources):
                raise ValueError("candidate provenance must be unique and ordered CM then BM")
            for source in item.provenance:
                pinned = MODELS[source.provider_id]
                if (source.model_id, source.revision, source.threshold) != (
                    pinned.model_id,
                    pinned.revision,
                    MODEL_THRESHOLDS[source.provider_id],
                ) or source.original_score < source.threshold:
                    raise ValueError("candidate provenance differs from pinned runtime")
        return self

    @property
    def snapshot_hash(self) -> str:
        return content_sha256(self.model_dump(mode="json"))


class SpanDecision(FrozenModel):
    candidate_id: str
    selected_label: str
    accepted: bool
    probabilities: dict[str, Probability]
    selected_probability: Probability
    provider_choice: str
    tie_break_applied: bool


class AcceptedPrediction(FrozenModel):
    candidate_id: str
    mention: str
    start: Index
    end: Index
    label: LabelName
    confidence: Probability

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.start >= self.end or len(self.mention) != self.end - self.start:
            raise ValueError("prediction must have a nonempty span matching its mention length")
        return self


def accepted_predictions(
    snapshot: CompareSnapshot,
    decisions: tuple[SpanDecision, ...],
) -> tuple[AcceptedPrediction, ...]:
    candidates = {candidate.candidate_id: candidate for candidate in snapshot.candidates}
    return tuple(
        AcceptedPrediction(
            candidate_id=candidate.candidate_id,
            mention=candidate.mention,
            start=candidate.start,
            end=candidate.end,
            label=decision.selected_label,
            confidence=decision.selected_probability,
        )
        for decision in decisions
        if decision.accepted
        for candidate in (candidates[decision.candidate_id],)
    )


class CompareResult(FrozenModel):
    """Complete provider decisions and accepted predictions for one candidate snapshot."""

    schema_version: Literal["entity-decision-result-v1"] = "entity-decision-result-v1"
    case_id: str
    source_response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_id: str = Field(min_length=1)
    decision_model: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: CompareSnapshot
    decisions: tuple[SpanDecision, ...]
    accepted_predictions: tuple[AcceptedPrediction, ...]

    @model_validator(mode="after")
    def check_projection(self) -> Self:
        if self.case_id != self.snapshot.case_id:
            raise ValueError("case ID mismatch")
        if tuple(item.candidate_id for item in self.decisions) != tuple(
            item.candidate_id for item in self.snapshot.candidates
        ):
            raise ValueError("one decision per candidate required in canonical order")
        choices = self.snapshot.entity_schema.choices
        for decision in self.decisions:
            if tuple(decision.probabilities) != choices:
                raise ValueError("decision probabilities must follow configured choice order")
            maximum = max(decision.probabilities.values())
            winners = [name for name, score in decision.probabilities.items() if score == maximum]
            selected = winners[0]
            if (
                decision.selected_label != selected
                or decision.provider_choice not in winners
                or decision.selected_probability != maximum
                or decision.tie_break_applied != (len(winners) > 1)
                or decision.accepted != (selected != self.snapshot.entity_schema.rejection_label)
            ):
                raise ValueError("decision diagnostics do not match configured argmax")
        if self.accepted_predictions != accepted_predictions(self.snapshot, self.decisions):
            raise ValueError("accepted predictions must equal decision projection")
        return self
