from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from contracts.errors import ErrorResponse, PipelineError, PipelineErrorCode
from contracts.models import EntityResolutionResult, ResolveRequest
from runtime.resolver import EntityResolver
from version import PACKAGE_VERSION


class ValidationErrorResponse(BaseModel):
    detail: list[dict[str, object]]


_ERROR_RESPONSES = {
    status: {"model": ErrorResponse}
    for status in sorted(
        {
            PipelineError(code).http_status
            for code in PipelineErrorCode
            if PipelineError(code).http_status != 422
        }
    )
}
_ERROR_RESPONSES[422] = {"model": ErrorResponse | ValidationErrorResponse}


def create_app(resolver: EntityResolver) -> FastAPI:
    application = FastAPI(title="Entity Resolution", version=PACKAGE_VERSION)

    @application.exception_handler(PipelineError)
    async def pipeline_error_handler(_request: Request, error: PipelineError):
        return JSONResponse(
            status_code=error.http_status,
            content=error.response().model_dump(mode="json"),
        )

    @application.get("/health/ready")
    async def readiness() -> dict[str, bool]:
        if not resolver.ready:
            raise PipelineError("SERVICE_NOT_READY")
        return {"ready": True}

    @application.post("/resolve", response_model=EntityResolutionResult, responses=_ERROR_RESPONSES)
    async def resolve(request: ResolveRequest) -> EntityResolutionResult:
        return await resolver.resolve_async(request)

    return application
