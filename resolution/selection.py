"""Pure, deterministic confidence gate and same-label containment NMS."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal, Self

from pydantic import Field, model_validator

from contracts.pipeline import AcceptedPrediction, CompareResult, FrozenModel, Probability
from hashing import content_sha256

SETTINGS = (("CN90", 0.90, 0.0),)


class PostprocessConfig(FrozenModel):
    schema_version: Literal["entity-selection-config-v1"] = "entity-selection-config-v1"
    policy_id: str
    confidence_tau: Probability | None
    min_score_gap: Probability | None

    @model_validator(mode="after")
    def check_setting(self) -> Self:
        if (self.policy_id, self.confidence_tau, self.min_score_gap) not in SETTINGS:
            raise ValueError("unfrozen postprocessing setting")
        return self

    @property
    def config_hash(self) -> str:
        return content_sha256(self.model_dump(mode="json"))


class ConfidenceDecision(FrozenModel):
    candidate_id: str
    score: Probability
    tau: Probability
    reason: Literal["CONFIDENCE_BELOW_THRESHOLD"] = "CONFIDENCE_BELOW_THRESHOLD"


class SuppressionDecision(FrozenModel):
    winner_id: str
    loser_id: str
    winner_score: Probability
    loser_score: Probability
    score_gap: float = Field(ge=0, allow_inf_nan=False)
    direction: Literal["WINNER_CONTAINS_LOSER", "LOSER_CONTAINS_WINNER"]
    iou: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: Literal["SAME_TYPE_CONTAINMENT"] = "SAME_TYPE_CONTAINMENT"


class PostprocessResult(FrozenModel):
    schema_version: Literal["entity-selection-v1"] = "entity-selection-v1"
    case_id: str
    source_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: PostprocessConfig
    pre_predictions: tuple[AcceptedPrediction, ...]
    post_confidence_predictions: tuple[AcceptedPrediction, ...]
    post_predictions: tuple[AcceptedPrediction, ...]
    confidence_decisions: tuple[ConfidenceDecision, ...]
    suppression_decisions: tuple[SuppressionDecision, ...]

    @model_validator(mode="after")
    def check_conservation(self) -> Self:
        before = {p.candidate_id: p for p in self.pre_predictions}
        after_confidence = {p.candidate_id: p for p in self.post_confidence_predictions}
        after = {p.candidate_id: p for p in self.post_predictions}
        if (
            len(before) != len(self.pre_predictions)
            or len(after_confidence) != len(self.post_confidence_predictions)
            or len(after) != len(self.post_predictions)
        ):
            raise ValueError("duplicate prediction IDs")
        if not set(after) <= set(after_confidence) <= set(before):
            raise ValueError("postprocessing must only delete predictions")
        if any(before[key] != value for key, value in after_confidence.items()):
            raise ValueError("confidence gate mutated a prediction")
        if any(before[key] != value for key, value in after.items()):
            raise ValueError("NMS mutated a prediction")
        confidence_ids = {d.candidate_id for d in self.confidence_decisions}
        loser_ids = {d.loser_id for d in self.suppression_decisions}
        if confidence_ids != set(before) - set(after_confidence):
            raise ValueError("confidence trace does not cover exactly its removed predictions")
        expected_confidence_ids = (
            {key for key, item in before.items() if item.confidence < self.config.confidence_tau}
            if self.config.confidence_tau is not None
            else set()
        )
        if confidence_ids != expected_confidence_ids:
            raise ValueError("confidence trace must remove exactly scores below tau")
        if loser_ids != set(after_confidence) - set(after):
            raise ValueError("NMS trace does not cover exactly its removed predictions")
        if len(confidence_ids) != len(self.confidence_decisions):
            raise ValueError("confidence deletion repeated")
        if len(loser_ids) != len(self.suppression_decisions):
            raise ValueError("NMS suppression repeated")
        if any(d.winner_id not in after for d in self.suppression_decisions):
            raise ValueError("each loser must have a retained winner")
        if any(
            d.tau != self.config.confidence_tau or d.score != before[d.candidate_id].confidence
            for d in self.confidence_decisions
        ):
            raise ValueError("confidence trace differs from frozen scores or policy")
        for decision in self.suppression_decisions:
            winner, loser = before[decision.winner_id], before[decision.loser_id]
            if winner.label != loser.label or not _contains(winner, loser):
                raise ValueError("suppression must be same-type direct containment")
            if (
                decision.winner_score != winner.confidence
                or decision.loser_score != loser.confidence
                or decision.score_gap != winner.confidence - loser.confidence
                or decision.iou != _iou(winner, loser)
            ):
                raise ValueError("suppression trace differs from frozen spans/scores")
            expected_direction = (
                "WINNER_CONTAINS_LOSER"
                if winner.start <= loser.start and loser.end <= winner.end
                else "LOSER_CONTAINS_WINNER"
            )
            if decision.direction != expected_direction:
                raise ValueError("suppression direction differs from geometry")
            if self.config.min_score_gap is None or decision.score_gap < self.config.min_score_gap:
                raise ValueError("suppression does not satisfy frozen score margin")
        if len(before) != len(after) + len(confidence_ids) + len(loser_ids):
            raise ValueError("postprocessing count conservation failed")
        return self


def _score_key(item: AcceptedPrediction) -> tuple[float, int, int, str]:
    return (-item.confidence, item.start, item.end, item.candidate_id)


def _contains(a: AcceptedPrediction, b: AcceptedPrediction) -> bool:
    return (a.start <= b.start and b.end <= a.end) or (b.start <= a.start and a.end <= b.end)


def _iou(a: AcceptedPrediction, b: AcceptedPrediction) -> float:
    intersection = max(0, min(a.end, b.end) - max(a.start, b.start))
    union = (a.end - a.start) + (b.end - b.start) - intersection
    return intersection / union


def confidence_gate(
    predictions: tuple[AcceptedPrediction, ...],
    tau: float | None,
) -> tuple[tuple[AcceptedPrediction, ...], tuple[ConfidenceDecision, ...]]:
    if tau is None:
        return predictions, ()
    retained, removed = [], []
    for item in predictions:
        if item.confidence >= tau:
            retained.append(item)
        else:
            removed.append(
                ConfidenceDecision(
                    candidate_id=item.candidate_id,
                    score=item.confidence,
                    tau=tau,
                )
            )
    return tuple(retained), tuple(removed)


def containment_nms(
    predictions: tuple[AcceptedPrediction, ...],
    min_score_gap: float | None,
) -> tuple[tuple[AcceptedPrediction, ...], tuple[SuppressionDecision, ...]]:
    if min_score_gap is None:
        return predictions, ()
    groups: dict[str, list[AcceptedPrediction]] = defaultdict(list)
    for item in predictions:
        groups[item.label].append(item)
    kept_ids: set[str] = set()
    suppressed = []
    for entity_type in sorted(groups):
        group = groups[entity_type]
        keepers: list[AcceptedPrediction] = []
        for item in sorted(group, key=_score_key):
            winner = next(
                (
                    other
                    for other in keepers
                    if _contains(other, item)
                    and other.confidence - item.confidence >= min_score_gap
                ),
                None,
            )
            if winner is None:
                keepers.append(item)
                kept_ids.add(item.candidate_id)
                continue
            suppressed.append(
                SuppressionDecision(
                    winner_id=winner.candidate_id,
                    loser_id=item.candidate_id,
                    winner_score=winner.confidence,
                    loser_score=item.confidence,
                    score_gap=winner.confidence - item.confidence,
                    direction=(
                        "WINNER_CONTAINS_LOSER"
                        if winner.start <= item.start and item.end <= winner.end
                        else "LOSER_CONTAINS_WINNER"
                    ),
                    iou=_iou(winner, item),
                )
            )
    retained = tuple(
        sorted(
            (item for item in predictions if item.candidate_id in kept_ids),
            key=lambda item: (item.start, item.end, item.candidate_id),
        )
    )
    return retained, tuple(suppressed)


def apply_policy(result: CompareResult, config: PostprocessConfig) -> PostprocessResult:
    """No gold, model, API or source adapter enters this selection boundary."""
    after_confidence, decisions = confidence_gate(
        result.accepted_predictions, config.confidence_tau
    )
    final, suppressions = containment_nms(after_confidence, config.min_score_gap)
    return PostprocessResult(
        case_id=result.case_id,
        source_result_hash=content_sha256(result.model_dump(mode="json")),
        config=config,
        pre_predictions=result.accepted_predictions,
        post_confidence_predictions=after_confidence,
        post_predictions=final,
        confidence_decisions=decisions,
        suppression_decisions=suppressions,
    )
