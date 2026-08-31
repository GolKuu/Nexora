"""Domain errors and their HTTP mapping."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class UpstreamError(AppError):
    """A data provider (KASE) failed or returned unusable data."""

    status_code = 502
    code = "upstream_error"


class StreamingUnsupportedError(AppError):
    """This deployment cannot hold a long-lived connection open.

    Distinct from UpstreamError on purpose: KASE has not failed and no data is
    missing. A serverless function simply cannot serve SSE, so the client is
    told to poll instead. Reporting that as 502 made a healthy deployment look
    like it had a broken data provider on every stock page view.
    """

    status_code = 501
    code = "streaming_unsupported"


class ForbiddenError(AppError):
    """The caller is not allowed to use this endpoint."""

    status_code = 403
    code = "forbidden"


class ConfigurationError(AppError):
    status_code = 500
    code = "configuration_error"


class MockDataForbiddenError(ConfigurationError):
    """Raised when mock data would be served in production."""

    code = "mock_data_forbidden"


class InsufficientDataError(AppError):
    """We refuse to invent numbers: the input data is missing."""

    status_code = 409
    code = "insufficient_data"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:  # pragma: no cover
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )
