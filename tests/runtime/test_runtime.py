from __future__ import annotations

import asyncio
from threading import Event

import pytest

from bootstrap import build_service
from contracts.errors import PipelineError, PipelineErrorCode
from contracts.models import ResolveRequest
from contracts.pipeline import CompareResult
from model_specs import MODELS
from runtime.pipeline import PipelineService
from tests.factories import candidate, decision, generic_schema, result, snapshot


class FakeProposer:
    def __init__(self) -> None:
        self.schema = generic_schema()
        self.calls = 0
        self.closed = False

    def load(self):
        return {
            key: {
                "model_id": model.model_id,
                "revision": model.revision,
                "artifact_sha256": "a" * 64,
                "content_verification": "TRUSTED_SOURCE_NOT_HASHED",
            }
            for key, model in MODELS.items()
        }

    def warmup(self) -> None:
        pass

    def propose_snapshot(self, text: str):
        self.calls += 1
        return snapshot(
            text,
            schema=self.schema,
            candidates=(candidate("s000", text, 0, min(5, len(text))),),
        )

    def close(self) -> None:
        self.closed = True


class FakeProvider:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.closed = False
        self.delay = delay
        self.started = Event()

    async def initialize(self) -> None:
        pass

    async def warmup(self) -> None:
        pass

    async def decide(self, source) -> CompareResult:
        self.calls += 1
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        item = decision("s000", source.entity_schema, "PERSON", 0.95)
        return result(source, (item,))

    async def aclose(self) -> None:
        self.closed = True


def resolver(*, cache_enabled: bool = True, delay: float = 0.0):
    proposer = FakeProposer()
    provider = FakeProvider(delay=delay)
    service = build_service(PipelineService(proposer, provider, cache_enabled=cache_enabled))
    return service, proposer, provider


def test_cache_and_lifecycle() -> None:
    service, proposer, provider = resolver()
    try:
        first = service.resolve(ResolveRequest(text="Alice"))
        second = service.resolve(ResolveRequest(text="Alice"))
        assert first.entities == second.entities
        assert (proposer.calls, provider.calls) == (1, 1)
    finally:
        service.close()
    assert proposer.closed and provider.closed
    assert not service.ready
    with pytest.raises(PipelineError) as raised:
        service.resolve(ResolveRequest(text="Alice"))
    assert raised.value.code is PipelineErrorCode.SERVICE_NOT_READY


def test_single_flight_shares_concurrent_decision() -> None:
    service, _proposer, provider = resolver(delay=0.05)

    async def run() -> None:
        responses = await asyncio.gather(
            *(service.resolve_async(ResolveRequest(text="Alice")) for _ in range(8))
        )
        assert all(item.entities == responses[0].entities for item in responses)

    try:
        asyncio.run(run())
        assert provider.calls == 1
    finally:
        service.close()


def test_admission_limit_fails_closed(monkeypatch) -> None:
    from runtime import pipeline as service_module

    monkeypatch.setattr(service_module, "ACTIVE_REQUESTS", 1)
    monkeypatch.setattr(service_module, "WAITING_REQUESTS", 0)
    service, _proposer, provider = resolver(cache_enabled=False, delay=0.1)

    async def run() -> None:
        active = asyncio.create_task(service.resolve_async(ResolveRequest(text="Alice")))
        await asyncio.to_thread(provider.started.wait, 1.0)
        with pytest.raises(PipelineError) as raised:
            await service.resolve_async(ResolveRequest(text="Grace"))
        assert raised.value.code is PipelineErrorCode.SERVICE_BUSY
        await active

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_failure_is_not_cached_and_invalid_snapshot_is_rejected():
    class TransientProvider(FakeProvider):
        async def decide(self, source):
            if not self.calls:
                self.calls += 1
                raise PipelineError("DECISION_PROVIDER_UNAVAILABLE")
            return await super().decide(source)

    proposer = FakeProposer()
    provider = TransientProvider()
    service = PipelineService(proposer, provider, cache_enabled=True)
    try:
        with pytest.raises(PipelineError):
            service.resolve("Alice")
        assert service.resolve("Alice").annotations[0].confidence == 0.95
        assert service.resolve("Alice").annotations[0].confidence == 0.95
        assert provider.calls == 2
    finally:
        service.close()

    class InvalidProposer(FakeProposer):
        def propose_snapshot(self, text):
            valid = super().propose_snapshot(text)
            return valid.model_copy(
                update={
                    "candidates": (valid.candidates[0].model_copy(update={"mention": "wrong"}),)
                }
            )

    service = PipelineService(InvalidProposer(), FakeProvider(), cache_enabled=True)
    try:
        with pytest.raises(PipelineError) as error:
            service.resolve("Alice")
        assert error.value.code is PipelineErrorCode.CANDIDATE_PROPOSAL_FAILED
    finally:
        service.close()


def test_cancelled_waiter_preserves_shared_work_and_shutdown_drains():
    entered, release = Event(), Event()

    class BlockedProvider(FakeProvider):
        async def decide(self, source):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            return await super().decide(source)

    proposer = FakeProposer()
    provider = BlockedProvider()
    service = PipelineService(proposer, provider, cache_enabled=True)

    async def run():
        first = asyncio.create_task(service.resolve_async("Alice"))
        assert await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(service.resolve_async("Alice"))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        closing = asyncio.create_task(service.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        release.set()
        await second
        await closing
        assert provider.calls == 1
        assert provider.closed and proposer.closed
        assert service._pending == 0
        assert not service._thread.is_alive()

    try:
        asyncio.run(run())
    finally:
        release.set()
        service.close()


def test_single_flight_results_do_not_share_mutable_metadata():
    service = PipelineService(FakeProposer(), FakeProvider(delay=0.02), cache_enabled=True)

    async def run():
        first, second = await asyncio.gather(
            service.resolve_async("Alice"), service.resolve_async("Alice")
        )
        first.selection.source.decisions[0].probabilities["PERSON"] = 0.1
        first.timings_ns["total"] = -1
        assert second.selection.source.decisions[0].probabilities["PERSON"] == 0.95
        assert second.timings_ns["total"] >= 0
        cached = await service.resolve_async("Alice")
        assert cached.selection.source.decisions[0].probabilities["PERSON"] == 0.95

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_full_admission_limit_and_parallel_requests():
    release, active = Event(), Event()

    class BlockedProvider(FakeProvider):
        running = peak = 0

        async def decide(self, source):
            self.running += 1
            self.peak = max(self.peak, self.running)
            if self.running == 8:
                active.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            self.running -= 1
            return await super().decide(source)

    provider = BlockedProvider()
    service = PipelineService(FakeProposer(), provider)
    try:
        futures = [service._submit("Alice") for _ in range(24)]
        assert active.wait(3)
        with pytest.raises(PipelineError) as error:
            service.resolve("Grace")
        assert error.value.code is PipelineErrorCode.SERVICE_BUSY
        assert provider.peak == 8
        release.set()
        for future in futures:
            value = future.result(timeout=5)
            assert value.timings_ns["total"] >= value.timings_ns["queue_wait"] >= 0
        assert service._pending == 0
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize("stage", ["initialize", "load", "warmup"])
def test_startup_failure_closes_owned_resources(stage):
    class StartupProvider(FakeProvider):
        async def initialize(self):
            if stage == "initialize":
                raise PipelineError("DECISION_CONFIGURATION_ERROR")

    class StartupProposer(FakeProposer):
        def load(self):
            if stage == "load":
                raise RuntimeError("private startup details")
            return super().load()

        def warmup(self):
            if stage == "warmup":
                raise RuntimeError("private warmup details")

    proposer, provider = StartupProposer(), StartupProvider()
    with pytest.raises(PipelineError) as error:
        PipelineService(proposer, provider)
    assert "private" not in str(error.value)
    assert proposer.closed and provider.closed
