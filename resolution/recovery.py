"""Frozen, local two-pass selection over one complete decision pool."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, Self

from pydantic import Field, model_validator

from contracts.pipeline import AcceptedPrediction, CompareResult, FrozenModel, Index
from hashing import content_sha256
from resolution.selection import (
    PostprocessConfig,
    PostprocessResult,
    SuppressionDecision,
    apply_policy,
    containment_nms,
)

CN90 = PostprocessConfig(policy_id="CN90", confidence_tau=0.90, min_score_gap=0.0)
OPERATOR_RULE_VERSION = "standalone-operator-cues-v1"
OPERATOR_CUES = ("不要", "排除", "不含", "不是", "除了", "或者", "以及", "或", "和", "且", "非")
# Word boundaries deliberately do not match cue characters inside Chinese names.
_OPERATOR = re.compile(r"(?<!\w)(?:" + "|".join(OPERATOR_CUES) + r")(?!\w)")


class RecoveryConfig(FrozenModel):
    policy_id: Literal["CN90_GAP80_V1"] = "CN90_GAP80_V1"
    primary_threshold: Literal[0.90] = 0.90
    recovery_threshold: Literal[0.80] = 0.80
    min_uncovered_run_chars: Literal[2] = 2
    max_selection_passes: Literal[2] = 2
    min_score_gap: Literal[0.0] = 0.0
    operator_rule_version: Literal["standalone-operator-cues-v1"] = OPERATOR_RULE_VERSION
    gap_rule_version: Literal["codepoint-complement-nonseparator-v1"] = (
        "codepoint-complement-nonseparator-v1"
    )

    @property
    def config_hash(self) -> str:
        return content_sha256(self.model_dump(mode="json", exclude={"config_hash"}))


class Interval(FrozenModel):
    start: Index
    end: Index

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start >= self.end:
            raise ValueError("interval must be nonempty")
        return self


class ProtectedInterval(Interval):
    mention: str
    rule_id: Literal["standalone-operator-cues-v1"] = OPERATOR_RULE_VERSION
    basis: Literal["non-word-or-query-boundaries"] = "non-word-or-query-boundaries"


class CandidateDecision(FrozenModel):
    candidate_id: str
    reason: Literal[
        "PRIMARY_RETAINED",
        "PROVIDER_REJECTED",
        "OVERLAP_WITH_PRIMARY",
        "PROTECTED_INTERVAL",
        "NO_ELIGIBLE_GAP",
        "BELOW_RECOVERY_THRESHOLD",
        "SECOND_PASS_NMS_SUPPRESSED",
        "RECOVERED_GAP_LOW_THRESHOLD",
    ]


class AdditionalInferenceUsage(FrozenModel):
    additional_decision_requests: Literal[0] = 0
    additional_decision_questions: Literal[0] = 0
    additional_decision_input_tokens: Literal[0] = 0
    additional_decision_output_tokens: Literal[0] = 0
    additional_candidate_inferences: Literal[0] = 0


def overlaps(a, b) -> bool:
    return a.start < b.end and b.start < a.end


def complement(length: int, occupied) -> tuple[Interval, ...]:
    """Return maximal uncovered intervals, merging any occupied overlaps."""
    cursor = 0
    gaps = []
    for item in sorted(occupied, key=lambda item: (item.start, item.end)):
        if cursor < item.start:
            gaps.append(Interval(start=cursor, end=item.start))
        cursor = max(cursor, item.end)
    if cursor < length:
        gaps.append(Interval(start=cursor, end=length))
    return tuple(gaps)


def protected_intervals(query: str) -> tuple[ProtectedInterval, ...]:
    """Lexical safeguard only; this is not operator/logic inference."""
    return tuple(
        ProtectedInterval(start=m.start(), end=m.end(), mention=m.group())
        for m in _OPERATOR.finditer(query)
    )


def eligible_gaps(query: str, occupied, config: RecoveryConfig) -> tuple[Interval, ...]:
    return tuple(
        gap
        for gap in complement(len(query), occupied)
        if gap.end - gap.start >= config.min_uncovered_run_chars
        and any(
            not c.isspace() and not unicodedata.category(c).startswith(("P", "S"))
            for c in query[gap.start : gap.end]
        )
    )


def _selection(result: CompareResult, baseline: PostprocessResult, config: RecoveryConfig):
    query = result.snapshot.raw_text
    primary = baseline.post_predictions
    protected = protected_intervals(query)
    gaps = eligible_gaps(query, (*primary, *protected), config)
    primary_ids = {p.candidate_id for p in primary}
    accepted = {p.candidate_id: p for p in result.accepted_predictions}
    reasons = {}
    eligible = []
    for decision in result.decisions:
        cid = decision.candidate_id
        candidate = accepted.get(cid)
        if candidate is None:
            reason = "PROVIDER_REJECTED"
        elif cid in primary_ids:
            reason = "PRIMARY_RETAINED"
        elif any(overlaps(candidate, p) for p in primary):
            reason = "OVERLAP_WITH_PRIMARY"
        elif any(overlaps(candidate, p) for p in protected):
            reason = "PROTECTED_INTERVAL"
        elif not any(g.start <= candidate.start < candidate.end <= g.end for g in gaps):
            reason = "NO_ELIGIBLE_GAP"
        elif candidate.confidence < config.recovery_threshold:
            reason = "BELOW_RECOVERY_THRESHOLD"
        else:
            eligible.append(candidate)
            reason = "RECOVERED_GAP_LOW_THRESHOLD"
        reasons[cid] = reason
    recovered, suppressions = containment_nms(tuple(eligible), config.min_score_gap)
    for decision in suppressions:
        reasons[decision.loser_id] = "SECOND_PASS_NMS_SUPPRESSED"
    final = tuple(sorted((*primary, *recovered), key=lambda p: (p.start, p.end, p.candidate_id)))
    return {
        "protected_intervals": protected,
        "gaps_before": complement(len(query), primary),
        "eligible_gaps": gaps,
        "gaps_after": complement(len(query), final),
        "eligible_predictions": tuple(eligible),
        "recovered_predictions": recovered,
        "final_predictions": final,
        "candidate_decisions": tuple(
            CandidateDecision(candidate_id=cid, reason=reason) for cid, reason in reasons.items()
        ),
        "second_pass_suppression_decisions": suppressions,
    }


class RecoveryResult(FrozenModel):
    schema_version: Literal["entity-gap-recovery-v1"] = "entity-gap-recovery-v1"
    config: RecoveryConfig
    source: CompareResult
    baseline: PostprocessResult
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_intervals: tuple[ProtectedInterval, ...]
    gaps_before: tuple[Interval, ...]
    eligible_gaps: tuple[Interval, ...]
    gaps_after: tuple[Interval, ...]
    eligible_predictions: tuple[AcceptedPrediction, ...]
    recovered_predictions: tuple[AcceptedPrediction, ...]
    final_predictions: tuple[AcceptedPrediction, ...]
    candidate_decisions: tuple[CandidateDecision, ...]
    second_pass_suppression_decisions: tuple[SuppressionDecision, ...]
    additional_inference_usage: AdditionalInferenceUsage = AdditionalInferenceUsage()

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        # Recompute from validated inputs, including hashes and complete decision coverage.
        if self.source_snapshot_hash != self.source.snapshot.snapshot_hash:
            raise ValueError("source snapshot identity mismatch")
        if self.classification_result_hash != content_sha256(self.source.model_dump(mode="json")):
            raise ValueError("classification identity mismatch")
        expected_baseline = apply_policy(self.source, CN90)
        if self.baseline != expected_baseline:
            raise ValueError("baseline must exactly reproduce CN90")
        if self.baseline_result_hash != content_sha256(self.baseline.model_dump(mode="json")):
            raise ValueError("baseline identity mismatch")
        for key, expected in _selection(self.source, self.baseline, self.config).items():
            if getattr(self, key) != expected:
                raise ValueError(f"recovery selection or trace mismatch: {key}")
        return self


DEFAULT_CONFIG = RecoveryConfig()


def recover_gaps(
    result: CompareResult,
    config: RecoveryConfig = DEFAULT_CONFIG,
    *,
    baseline: PostprocessResult | None = None,
) -> RecoveryResult:
    """Reuse accepted scores and CN90; never accepts gold, a client, or partial responses."""
    # Revalidation rejects model_construct/model_copy bypasses at the selection boundary.
    result = CompareResult.model_validate_json(result.model_dump_json())
    baseline = apply_policy(result, CN90) if baseline is None else baseline
    return RecoveryResult(
        config=config,
        source=result,
        baseline=baseline,
        source_snapshot_hash=result.snapshot.snapshot_hash,
        classification_result_hash=content_sha256(result.model_dump(mode="json")),
        baseline_result_hash=content_sha256(baseline.model_dump(mode="json")),
        **_selection(result, baseline, config),
    )


POLICY_ID = "CONFIDENCE_90_GAP_80"
