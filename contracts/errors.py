from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PipelineErrorCode(StrEnum):
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    SERVICE_BUSY = "SERVICE_BUSY"
    QUERY_TOO_LONG = "QUERY_TOO_LONG"
    CANDIDATE_PROPOSAL_FAILED = "CANDIDATE_PROPOSAL_FAILED"
    DECISION_TIMEOUT = "DECISION_TIMEOUT"
    DECISION_PROVIDER_UNAVAILABLE = "DECISION_PROVIDER_UNAVAILABLE"
    DECISION_RESPONSE_INVALID = "DECISION_RESPONSE_INVALID"
    DECISION_CONFIGURATION_ERROR = "DECISION_CONFIGURATION_ERROR"


_ERRORS = {
    PipelineErrorCode.SERVICE_NOT_READY: (503, "Entity resolution service is not ready."),
    PipelineErrorCode.SERVICE_BUSY: (503, "Entity resolution service is busy."),
    PipelineErrorCode.QUERY_TOO_LONG: (422, "Input text exceeds the configured runtime limit."),
    PipelineErrorCode.CANDIDATE_PROPOSAL_FAILED: (502, "Candidate proposal failed."),
    PipelineErrorCode.DECISION_TIMEOUT: (504, "Decision provider timed out."),
    PipelineErrorCode.DECISION_PROVIDER_UNAVAILABLE: (502, "Decision provider is unavailable."),
    PipelineErrorCode.DECISION_RESPONSE_INVALID: (502, "Decision provider returned invalid data."),
    PipelineErrorCode.DECISION_CONFIGURATION_ERROR: (503, "Decision provider is not configured."),
}


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: PipelineErrorCode
    message: str


class PipelineError(RuntimeError):
    def __init__(self, code: PipelineErrorCode | str) -> None:
        self.code = PipelineErrorCode(code)
        self.http_status, self.safe_message = _ERRORS[self.code]
        super().__init__(self.safe_message)

    def response(self) -> ErrorResponse:
        return ErrorResponse(code=self.code, message=self.safe_message)
