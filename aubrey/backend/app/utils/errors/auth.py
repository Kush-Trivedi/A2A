from .base import ErrorCategory
from .common import ForbiddenError, UnauthorizedError


class InvalidTokenError(UnauthorizedError):
    code = "invalid_token"
    default_message = "The provided token is invalid."


class TokenExpiredError(UnauthorizedError):
    code = "token_expired"
    default_message = "The provided token has expired."


class SessionExpiredError(UnauthorizedError):
    code = "session_expired"
    default_message = "Your session has expired. Please sign in again."


class SessionNotFoundError(UnauthorizedError):
    code = "session_not_found"
    default_message = "No active session was found."


class OAuthStateError(UnauthorizedError):
    code = "oauth_state_invalid"
    default_message = "The sign-in request could not be verified. Please try again."


class CsrfValidationError(ForbiddenError):
    code = "csrf_invalid"
    category = ErrorCategory.AUTH
    default_message = "The CSRF token is missing or invalid."


class PermissionDeniedError(ForbiddenError):
    code = "permission_denied"
    default_message = "You are not authorized to access this resource."
