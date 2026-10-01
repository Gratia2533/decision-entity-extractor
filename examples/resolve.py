"""Run from the checkout with python -m examples.resolve."""

import argparse
from pathlib import Path

from adapters.otter.proposer import SelectedCandidateProposer
from adapters.typesafe.provider import TypeSafeDecisionProvider
from bootstrap import build_service
from contracts.models import ResolveRequest
from runtime.config import load_schema
from runtime.pipeline import PipelineService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=Path(".local-artifacts"))
    parser.add_argument("--schema", type=Path, default=Path("config/schema.example.json"))
    parser.add_argument("--text", default="Alice visited Taipei.")
    args = parser.parse_args()
    schema = load_schema(args.schema)
    request = ResolveRequest(text=args.text)
    proposer = SelectedCandidateProposer(args.artifact_root, schema)
    provider = TypeSafeDecisionProvider()
    service = build_service(PipelineService(proposer, provider, cache_enabled=True))
    try:
        print(service.resolve(request).model_dump_json(indent=2))
    finally:
        service.close()


if __name__ == "__main__":
    main()
