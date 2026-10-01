from __future__ import annotations

from adapters.otter.contracts import QueryInference, SpanCandidate
from adapters.otter.proposer import SelectedCandidateProposer
from contracts.models import EntityLabel, EntitySchema


class Adapter:
    def __init__(self) -> None:
        self.loaded = False

    def load(self):
        self.loaded = True
        return {"identity": "test"}

    def predict(self, text: str) -> QueryInference:
        candidate = SpanCandidate.from_offsets(text, 0, len(text), 0.8)
        return QueryInference((candidate,), 1, 32, 1, ((0, len(text)),), False, "configured_labels")

    def close(self) -> None:
        self.loaded = False


def test_candidate_probes_come_from_configured_descriptions() -> None:
    schema = EntitySchema(labels=(EntityLabel(name="DEVICE", description="A physical device"),))
    captured = []
    adapters = {}

    def factory(key, probes):
        captured.append((key, probes))
        adapters[key] = Adapter()
        return adapters[key]

    proposer = SelectedCandidateProposer("unused", schema, adapter_factory=factory)
    try:
        proposer.load()
        source = proposer.propose_snapshot("router")
        assert source.entity_schema == schema
        assert captured == [
            ("CM", ("A physical device",)),
            ("BM", ("A physical device",)),
        ]
    finally:
        proposer.close()
