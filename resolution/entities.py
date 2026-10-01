"""Surface-preserving resolution with generic, exact-span label conflicts."""

from collections import defaultdict

from contracts.models import (
    Conflict,
    ConflictHypothesis,
    EvidenceSource,
    FinalEntity,
    ResolutionStatus,
    SourceEvidence,
    Span,
)
from contracts.pipeline import CompareSnapshot
from resolution.annotation import AnnotatedPrediction


def resolve_entities(
    snapshot: CompareSnapshot,
    annotations: tuple[AnnotatedPrediction, ...],
    *,
    provider_id: str,
) -> tuple[FinalEntity, ...]:
    """Keep selected spans and raw decision scores; never invent canonical identities."""
    candidates = {item.candidate_id: item for item in snapshot.candidates}
    groups = defaultdict(list)
    keys = []
    for item in annotations:
        prediction = item.prediction
        candidate = candidates.get(prediction.candidate_id)
        if (
            candidate is None
            or (prediction.start, prediction.end, prediction.mention)
            != (candidate.start, candidate.end, candidate.mention)
            or prediction.label not in snapshot.entity_schema.label_names
        ):
            raise ValueError("annotation differs from the configured candidate set")
        keys.append((prediction.start, prediction.end, prediction.label))
        groups[(prediction.start, prediction.end)].append(prediction)
    if len(keys) != len(set(keys)):
        raise ValueError("selected span/label keys must be unique")
    conflicts = {
        boundary: Conflict(
            hypotheses=tuple(
                ConflictHypothesis(label=item.label, confidence=item.confidence)
                for item in sorted(group, key=lambda item: item.label)
            )
        )
        for boundary, group in groups.items()
        if len({item.label for item in group}) > 1
    }
    entities = []
    for number, annotation in enumerate(
        sorted(
            annotations,
            key=lambda item: (item.prediction.start, item.prediction.end, item.prediction.label),
        ),
        start=1,
    ):
        item = annotation.prediction
        candidate = candidates[item.candidate_id]
        conflict = conflicts.get((item.start, item.end))
        entities.append(
            FinalEntity(
                id=f"e{number}",
                mention=item.mention,
                label=item.label,
                normalized=annotation.normalized,
                span=Span(start=item.start, end=item.end),
                confidence=item.confidence,
                sources=(
                    *(
                        SourceEvidence(
                            source=EvidenceSource.CANDIDATE_MODEL,
                            source_id=source.provider_id,
                            confidence=source.original_score,
                        )
                        for source in candidate.provenance
                    ),
                    SourceEvidence(
                        source=EvidenceSource.DECISION_PROVIDER,
                        source_id=provider_id,
                        confidence=item.confidence,
                    ),
                ),
                resolution_status=(
                    ResolutionStatus.CONFLICTED if conflict else ResolutionStatus.EXTRACTED
                ),
                conflict=conflict,
            )
        )
    return tuple(entities)
