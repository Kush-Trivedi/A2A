from typing import Any

from fastapi import FastAPI, Request
from ..utils.common.logger import Logger
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from ..utils.errors import AppError, ErrorCategory, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = Logger(__name__).get_logger()


class ErrorResponseFactory:
    @staticmethod
    def _request_id(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @classmethod
    def _envelope(
        cls,
        *,
        message: str,
        code: str,
        category: str,
        details: dict[str, Any] | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "category": category}
        if details:
            error["details"] = details
        if request_id:
            error["request_id"] = request_id
        return {"success": False, "message": message, "data": {"error": error}}

    @classmethod
    def from_app_error(cls, request: Request, exc: AppError) -> JSONResponse:
        request_id = cls._request_id(request)
        log = logger.exception if exc.http_status >= 500 else logger.warning
        log(
            "app_error",
            extra={
                "request_id": request_id,
                "code": exc.code,
                "category": exc.category.value,
                "status_code": exc.http_status,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=cls._envelope(
                message=exc.client_message(),
                code=exc.code,
                category=exc.category.value,
                details=exc.details,
                request_id=request_id,
            ),
        )

    @classmethod
    def from_validation_error(
        cls, request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        err = ValidationError(details={"fields": exc.errors()})
        return JSONResponse(
            status_code=err.http_status,
            content=cls._envelope(
                message=err.client_message(),
                code=err.code,
                category=err.category.value,
                details=err.details,
                request_id=cls._request_id(request),
            ),
        )

    @classmethod
    def from_http_exception(
        cls, request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        message = str(exc.detail) if exc.detail else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=cls._envelope(
                message=message,
                code=f"http_{exc.status_code}",
                category=ErrorCategory.INTERNAL.value,
                details=None,
                request_id=cls._request_id(request),
            ),
            headers=getattr(exc, "headers", None),
        )

    @classmethod
    def from_unexpected(cls, request: Request, exc: Exception) -> JSONResponse:
        request_id = cls._request_id(request)
        logger.exception(
            "unhandled_exception",
            extra={"request_id": request_id, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content=cls._envelope(
                message="An unexpected error occurred.",
                code="internal_error",
                category=ErrorCategory.INTERNAL.value,
                details=None,
                request_id=request_id,
            ),
        )


def register_exception_handlers(app: FastAPI) -> None:
    factory = ErrorResponseFactory

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return factory.from_app_error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return factory.from_validation_error(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return factory.from_http_exception(request, exc)

    @app.exception_handler(Exception)
    async def _unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        return factory.from_unexpected(request, exc)
