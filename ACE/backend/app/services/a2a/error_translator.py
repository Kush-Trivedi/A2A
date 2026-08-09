import httpx
from a2a.client import A2AClientError, A2AClientTimeoutError

from ...utils.errors import AppError, ExternalServiceError


class A2AErrorTranslator:
    """Maps A2A client/protocol failures onto the ACE error hierarchy.

    Chat callers always see the uniform ACE error envelope; the original
    protocol failure is preserved as the cause for logging/audit.
    """

    def translate(self, exc: Exception, *, agent_key: str) -> AppError:
        if isinstance(exc, AppError):
            return exc
        if isinstance(exc, A2AClientTimeoutError):
            return ExternalServiceError(
                f"Agent '{agent_key}' timed out.",
                code="a2a_timeout",
                details={"agent_key": agent_key},
                cause=exc,
            )
        if isinstance(exc, A2AClientError):
            return ExternalServiceError(
                f"Agent '{agent_key}' returned a protocol error.",
                code="a2a_protocol_error",
                details={"agent_key": agent_key},
                cause=exc,
            )
        if isinstance(exc, httpx.TimeoutException):
            return ExternalServiceError(
                f"Agent '{agent_key}' did not respond in time.",
                code="a2a_timeout",
                details={"agent_key": agent_key},
                cause=exc,
            )
        if isinstance(exc, httpx.HTTPError):
            return ExternalServiceError(
                f"Agent '{agent_key}' is unreachable.",
                code="a2a_unreachable",
                details={"agent_key": agent_key},
                cause=exc,
            )
        return ExternalServiceError(
            f"Agent '{agent_key}' call failed.",
            code="a2a_call_failed",
            details={"agent_key": agent_key},
            cause=exc,
        )
