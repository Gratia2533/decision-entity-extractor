"""Safe completion telemetry with no query surfaces or provider bodies."""

import logging

from contracts.models import ResolverMetadata

logger = logging.getLogger("entity_resolution")


def log_resolution_metrics(*, metadata: ResolverMetadata, entity_count: int, candidate_count: int):
    logger.info(
        "entity_resolution_completed",
        extra={
            "metrics": {
                "policy_id": metadata.policy_id,
                "timings_ms": metadata.timings_ms,
                "entity_count": entity_count,
                "candidate_count": candidate_count,
                "source_statuses": {s.source_id: s.status for s in metadata.sources},
            }
        },
    )
