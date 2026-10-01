"""Concurrent production pipeline with bounded admission, cache, and lifecycle."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import Condition, Thread, current_thread
from time import perf_counter_ns

from contracts.errors import PipelineError
from contracts.interfaces import CandidateProposer, DecisionProvider
from contracts.pipeline import REQUEST_BUILDER_VERSION, CompareResult, CompareSnapshot
from hashing import content_sha256
from model_specs import DECODER_VERSION, MODEL_THRESHOLDS
from resolution.annotation import AnnotatedPrediction, annotate_predictions
from resolution.recovery import POLICY_ID, RecoveryResult, recover_gaps
from runtime.cache import SuccessCache
from runtime.config import ACTIVE_REQUESTS, WAITING_REQUESTS


@dataclass(frozen=True)
class PipelineResult:
    selection: RecoveryResult
    annotations: tuple[AnnotatedPrediction, ...]
    timings_ns: dict[str, int]
    policy_id: str = POLICY_ID
    decision_model: str = ""
    provider_id: str = ""
    prompt_hash: str = ""
    decoder_version: str = DECODER_VERSION


class PipelineService:
    """Own startup, readiness, in-flight work, and shutdown on one event loop."""

    def __init__(
        self,
        proposer: CandidateProposer,
        decision_provider: DecisionProvider,
        *,
        cache_enabled: bool = False,
    ) -> None:
        self._proposer = proposer
        self._decision_provider = decision_provider
        self._cache_enabled = cache_enabled
        self._candidates: SuccessCache[CompareSnapshot] = SuccessCache()
        self._decisions: SuccessCache[CompareResult] = SuccessCache()
        self._flights: dict[str, asyncio.Task[PipelineResult]] = {}
        self._admission = asyncio.Semaphore(ACTIVE_REQUESTS)
        self._condition = Condition()
        self._pending = 0
        self._closing = self._closed = False
        self._state = "STARTING"
        self._loop = asyncio.new_event_loop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="entity-candidates")
        self._thread = Thread(target=self._run_loop, name="entity-pipeline", daemon=True)
        self._thread.start()
        try:
            self.source_identity = asyncio.run_coroutine_threadsafe(
                self._initialize(), self._loop
            ).result()
        except Exception as error:
            self._state = "FAILED"
            try:
                self.close()
            except PipelineError:
                pass
            if isinstance(error, PipelineError):
                raise error from None
            raise PipelineError("CANDIDATE_PROPOSAL_FAILED") from None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _initialize(self) -> dict:
        await self._decision_provider.initialize()
        identity = await self._loop.run_in_executor(self._executor, self._proposer.load)
        await self._loop.run_in_executor(self._executor, self._proposer.warmup)
        await self._decision_provider.warmup()
        self._state = "READY"
        return identity

    @property
    def ready(self) -> bool:
        return self._state == "READY" and not self._closing and not self._closed

    def _submit(self, text: str):
        with self._condition:
            if not self.ready:
                raise PipelineError("SERVICE_NOT_READY")
            if self._pending >= ACTIVE_REQUESTS + WAITING_REQUESTS:
                raise PipelineError("SERVICE_BUSY")
            self._pending += 1
            future = asyncio.run_coroutine_threadsafe(
                self._admitted(text, perf_counter_ns()), self._loop
            )
        future.add_done_callback(self._finished)
        return future

    def _finished(self, _future) -> None:
        with self._condition:
            self._pending -= 1
            self._condition.notify_all()

    def resolve(self, text: str) -> PipelineResult:
        if current_thread() is self._thread:
            raise RuntimeError("synchronous resolution cannot run inside the service event loop")
        return self._submit(text).result()

    async def resolve_async(self, text: str) -> PipelineResult:
        return await asyncio.shield(asyncio.wrap_future(self._submit(text)))

    async def _admitted(self, text: str, submitted: int) -> PipelineResult:
        async with self._admission:
            result = await self._execute(text)
            result.timings_ns["queue_wait"] = perf_counter_ns() - submitted
            result.timings_ns["total"] = perf_counter_ns() - submitted
            return result

    def _candidate_key(self, text: str) -> str:
        return content_sha256(
            {
                "text": text,
                "sources": self.source_identity,
                "decoder": DECODER_VERSION,
                "thresholds": dict(MODEL_THRESHOLDS),
            }
        )

    async def _decide(self, snapshot: CompareSnapshot) -> CompareResult:
        try:
            result = await self._decision_provider.decide(snapshot)
            result = CompareResult.model_validate_json(result.model_dump_json())
            if result.snapshot != snapshot:
                raise ValueError("decision snapshot differs from proposed text")
            return result
        except PipelineError:
            raise
        except Exception:
            raise PipelineError("DECISION_RESPONSE_INVALID") from None

    @staticmethod
    def _select(result: CompareResult):
        try:
            selection = recover_gaps(result)
            annotations = annotate_predictions(
                result.snapshot.raw_text, selection.final_predictions
            )
            return selection, annotations
        except PipelineError:
            raise
        except Exception:
            raise PipelineError("CANDIDATE_PROPOSAL_FAILED") from None

    async def _resolution(self, text: str) -> PipelineResult:
        if not self._cache_enabled:
            return self._result(*(await self._compute(text)))
        candidate_key = self._candidate_key(text)
        snapshot = self._candidates.get(candidate_key)
        if snapshot is None:
            snapshot, candidate_ns = await self._propose(text)
            self._candidates.put(candidate_key, snapshot)
        else:
            candidate_ns = 0
        key = content_sha256(
            {
                "candidate": snapshot.snapshot_hash,
                "schema": snapshot.entity_schema.model_dump(mode="json"),
                "request_builder": REQUEST_BUILDER_VERSION,
            }
        )
        cached = self._decisions.get(key)
        if cached is not None:
            return self._result(cached, candidate_ns, 0)
        task = self._flights.get(key)
        if task is None:
            task = asyncio.create_task(self._publish(key, snapshot, candidate_ns))
            self._flights[key] = task
            task.add_done_callback(lambda done: self._forget_flight(key, done))
        shared = await asyncio.shield(task)
        return deepcopy(shared)

    def _forget_flight(self, key: str, task: asyncio.Task) -> None:
        if self._flights.get(key) is task:
            del self._flights[key]
        if not task.cancelled():
            task.exception()

    async def _publish(
        self, key: str, snapshot: CompareSnapshot, candidate_ns: int
    ) -> PipelineResult:
        started = perf_counter_ns()
        result = await self._decide(snapshot)
        decision_ns = perf_counter_ns() - started
        projected = self._result(result, candidate_ns, decision_ns)
        self._decisions.put(key, result)
        return projected

    async def _propose(self, text: str) -> tuple[CompareSnapshot, int]:
        started = perf_counter_ns()
        try:
            propose_async = getattr(self._proposer, "propose_snapshot_async", None)
            if propose_async is not None:
                snapshot = await propose_async(text)
            else:
                snapshot = await self._loop.run_in_executor(
                    self._executor, self._proposer.propose_snapshot, text
                )
            if snapshot.raw_text != text:
                raise ValueError("candidate snapshot text mismatch")
            snapshot = CompareSnapshot.model_validate_json(snapshot.model_dump_json())
            return snapshot, perf_counter_ns() - started
        except PipelineError:
            raise
        except Exception:
            raise PipelineError("CANDIDATE_PROPOSAL_FAILED") from None

    async def _compute(self, text: str) -> tuple[CompareResult, int, int]:
        snapshot, candidate_ns = await self._propose(text)
        started = perf_counter_ns()
        result = await self._decide(snapshot)
        return result, candidate_ns, perf_counter_ns() - started

    def _result(self, result: CompareResult, candidate_ns: int, decision_ns: int) -> PipelineResult:
        selecting = perf_counter_ns()
        selection, annotations = self._select(result)
        return PipelineResult(
            selection=selection,
            annotations=annotations,
            timings_ns={
                "candidates": candidate_ns,
                "decision": decision_ns,
                "selection_annotation": perf_counter_ns() - selecting,
                "total": 0,
            },
            decision_model=result.decision_model,
            provider_id=result.provider_id,
            prompt_hash=result.prompt_hash,
        )

    async def _execute(self, text: str) -> PipelineResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must contain non-whitespace characters")
        return await self._resolution(text)

    async def _shutdown(self) -> None:
        await asyncio.gather(*tuple(self._flights.values()), return_exceptions=True)
        self._candidates.clear()
        self._decisions.clear()
        try:
            await self._decision_provider.aclose()
        finally:
            await self._loop.run_in_executor(self._executor, self._proposer.close)

    def close(self) -> None:
        if current_thread() is self._thread:
            raise RuntimeError("synchronous close cannot run inside the service event loop")
        with self._condition:
            if self._closed:
                return
            if self._closing:
                self._condition.wait_for(lambda: self._closed)
                return
            self._closing = True
            self._state = "CLOSING"
            self._condition.wait_for(lambda: self._pending == 0)
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._executor.shutdown(wait=True)
            with self._condition:
                self._closed = True
                self._state = "CLOSED"
                self._condition.notify_all()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)
