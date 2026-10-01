"""TypeSafe transport adapter behind the generic decision-provider boundary."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version

from adapters.typesafe.wire import MODEL, build_requests, classify_responses
from contracts.errors import PipelineError
from contracts.pipeline import CompareResult, CompareSnapshot

_LOG = logging.getLogger(__name__)


class _SafeSdkLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Pinned SDK 0.7.0 emits wire logs on typesafe_sdk, not child loggers.
        return record.levelno >= logging.WARNING


class TypeSafeDecisionProvider:
    def __init__(self, *, transport_factory=None) -> None:
        try:
            if version("typesafe-sdk") != "0.7.0":
                raise PipelineError("DECISION_CONFIGURATION_ERROR")
        except PackageNotFoundError:
            raise PipelineError("DECISION_CONFIGURATION_ERROR") from None
        self._api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        if not self._api_key:
            raise PipelineError("DECISION_CONFIGURATION_ERROR")
        self._transport_factory = transport_factory
        self._closed = False
        self._client = None
        self._loop = None
        self._warmed = False
        sdk_logger = logging.getLogger("typesafe_sdk")
        sdk_logger.setLevel(logging.WARNING)
        if not any(isinstance(item, _SafeSdkLogs) for item in sdk_logger.filters):
            sdk_logger.addFilter(_SafeSdkLogs())

    async def initialize(self) -> None:
        """Create the owned connection pool without making any network request."""
        import httpx2
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

        if self._closed:
            raise PipelineError("SERVICE_NOT_READY")
        loop = asyncio.get_running_loop()
        if self._client is not None:
            if loop is not self._loop:
                raise PipelineError("SERVICE_NOT_READY")
            return
        http_client = None
        try:
            limits = httpx2.Limits(
                max_connections=8, max_keepalive_connections=8, keepalive_expiry=5.0
            )
            transport = (
                self._transport_factory()
                if self._transport_factory
                else httpx2.AsyncHTTPTransport(limits=limits, retries=1, http2=False)
            )
            http_client = httpx2.AsyncClient(
                transport=transport, limits=limits, timeout=httpx2.Timeout(10.0), http2=False
            )
            self._client = AsyncTypeSafeClient(
                api_key=self._api_key,
                model=MODEL,
                base_url="https://api.typesafe.ai",
                timeout=10.0,
                retry=RetryPolicy(max_retries=0),
                http_client=http_client,
            )
            self._loop = loop
        except Exception:
            if http_client is not None:
                try:
                    await http_client.aclose()
                except Exception:
                    self._log_error("DECISION_CONFIGURATION_ERROR", None)
            self._log_error("DECISION_CONFIGURATION_ERROR", None)
            raise PipelineError("DECISION_CONFIGURATION_ERROR") from None

    async def decide(self, snapshot: CompareSnapshot) -> CompareResult:
        from typesafe_sdk import (
            TypeSafeAPIError,
            TypeSafeAPIResponseValidationError,
            TypeSafeAPITimeoutError,
            TypeSafeError,
        )

        if self._closed:
            raise PipelineError("SERVICE_NOT_READY")
        logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
        requests = build_requests(snapshot)
        if not requests:
            return classify_responses(snapshot, ())
        await self.initialize()
        # Per-query chunk concurrency only; this is not cross-query admission control.
        semaphore = asyncio.Semaphore(2)

        async def send(request):
            async with semaphore:
                response = await self._client.system_one(**request, timeout=10.0)
                return response.raw_http_response.content.decode("utf-8")

        tasks = []
        try:
            async with asyncio.timeout(10.0):
                tasks = [asyncio.create_task(send(request)) for request in requests]
                responses = await asyncio.gather(*tasks)
            return classify_responses(snapshot, tuple(responses))
        except (TypeSafeAPITimeoutError, TimeoutError):
            raise PipelineError("DECISION_TIMEOUT") from None
        except TypeSafeAPIResponseValidationError as exc:
            self._log_error("DECISION_RESPONSE_INVALID", exc.request_id)
            raise PipelineError("DECISION_RESPONSE_INVALID") from None
        except TypeSafeAPIError as exc:
            code = {
                401: "DECISION_CONFIGURATION_ERROR",
                403: "DECISION_CONFIGURATION_ERROR",
                422: "DECISION_RESPONSE_INVALID",
            }.get(exc.status, "DECISION_PROVIDER_UNAVAILABLE")
            self._log_error(code, exc.request_id)
            raise PipelineError(code) from None
        except UnicodeDecodeError:
            raise PipelineError("DECISION_RESPONSE_INVALID") from None
        except (TypeSafeError, OSError):
            raise PipelineError("DECISION_PROVIDER_UNAVAILABLE") from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def warmup(self) -> None:
        """One bounded Models request on the same pool; real quota impact is unverified."""
        from typesafe_sdk import (
            RetryPolicy,
            TypeSafeAPIError,
            TypeSafeAPIResponseValidationError,
            TypeSafeAPITimeoutError,
            TypeSafeError,
        )

        await self.initialize()
        if self._warmed:
            return
        logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
        try:
            async with asyncio.timeout(10.0):
                await self._client.models.list(retry=RetryPolicy(max_retries=0), timeout=10.0)
        except (TypeSafeAPITimeoutError, TimeoutError):
            raise PipelineError("DECISION_TIMEOUT") from None
        except TypeSafeAPIResponseValidationError as exc:
            self._log_error("DECISION_RESPONSE_INVALID", exc.request_id)
            raise PipelineError("DECISION_RESPONSE_INVALID") from None
        except TypeSafeAPIError as exc:
            code = {
                401: "DECISION_CONFIGURATION_ERROR",
                403: "DECISION_CONFIGURATION_ERROR",
                422: "DECISION_RESPONSE_INVALID",
            }.get(exc.status, "DECISION_PROVIDER_UNAVAILABLE")
            self._log_error(code, exc.request_id)
            raise PipelineError(code) from None
        except (TypeSafeError, OSError):
            raise PipelineError("DECISION_PROVIDER_UNAVAILABLE") from None
        except PipelineError:
            raise
        except Exception:
            self._log_error("DECISION_PROVIDER_UNAVAILABLE", None)
            raise PipelineError("DECISION_PROVIDER_UNAVAILABLE") from None
        self._warmed = True

    @staticmethod
    def _log_error(code, request_id):
        safe_id = (
            request_id
            if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", request_id)
            else None
        )
        _LOG.warning("Decision provider failed: code=%s request_id=%s", code, safe_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                self._log_error("DECISION_PROVIDER_UNAVAILABLE", None)
                raise PipelineError("DECISION_PROVIDER_UNAVAILABLE") from None
            finally:
                self._client = None
