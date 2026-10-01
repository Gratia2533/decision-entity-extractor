"""Production composition root."""

from pathlib import Path

from adapters.otter.proposer import SelectedCandidateProposer
from adapters.typesafe.provider import TypeSafeDecisionProvider
from contracts.models import EntitySchema
from runtime.pipeline import PipelineService
from runtime.resolver import EntityResolver


def build_service(pipeline: PipelineService) -> EntityResolver:
    return EntityResolver(pipeline)


def build_production_service(
    *, artifact_root_path: str | Path, schema: EntitySchema, cache_enabled: bool = True
) -> EntityResolver:
    proposer = SelectedCandidateProposer(artifact_root_path, schema)
    provider = TypeSafeDecisionProvider()
    return build_service(PipelineService(proposer, provider, cache_enabled=cache_enabled))
