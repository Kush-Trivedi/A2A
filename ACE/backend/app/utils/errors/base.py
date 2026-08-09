from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    VALIDATION = "validation"
    AUTH = "auth"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    DATABASE = "database"
    INTEGRATION = "integration"
    AZURE = "azure"
    SHAREPOINT = "sharepoint"
    LLM = "llm"
    INTERNAL = "internal"


class AppError(Exception):
    http_status: int = 500
    code: str = "internal_error"
    category: ErrorCategory = ErrorCategory.INTERNAL
    default_message: str = "An unexpected error occurred."
    expose_message: bool = True

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}
        if cause is not None:
            self.__cause__ = cause
        super().__init__(self.message)

    def client_message(self) -> str:
        return self.message if self.expose_message else self.default_message

    def to_error_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "category": self.category.value,
        }
        if self.details:
            body["details"] = self.details
        return body
