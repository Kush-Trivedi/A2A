from .base import AppError, ErrorCategory

class BadRequestError(AppError):
    http_status = 400
    code = "bad_request"
    category = ErrorCategory.VALIDATION
    default_message = "The request was malformed."


class ValidationError(AppError):
    http_status = 422
    code = "validation_error"
    category = ErrorCategory.VALIDATION
    default_message = "The request payload failed validation."


class UnauthorizedError(AppError):
    http_status = 401
    code = "unauthorized"
    category = ErrorCategory.AUTH
    default_message = "Authentication is required."


class ForbiddenError(AppError):
    http_status = 403
    code = "forbidden"
    category = ErrorCategory.AUTHORIZATION
    default_message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    http_status = 404
    code = "not_found"
    category = ErrorCategory.NOT_FOUND
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    http_status = 409
    code = "conflict"
    category = ErrorCategory.CONFLICT
    default_message = "The request conflicts with the current state."


class TooManyRequestsError(AppError):
    http_status = 429
    code = "too_many_requests"
    category = ErrorCategory.RATE_LIMIT
    default_message = "Too many requests. Please retry later."


class ServiceUnavailableError(AppError):
    http_status = 503
    code = "service_unavailable"
    category = ErrorCategory.INTERNAL
    default_message = "The service is temporarily unavailable."
