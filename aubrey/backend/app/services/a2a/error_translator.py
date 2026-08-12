import httpx
from a2a.client import A2AClientError, A2AClientTimeoutError

from ...utils.errors import AppError, ExternalServiceError


class A2AErrorTranslator:
    """Every protocol/transport failure becomes one typed aubrey error with
    a stable code the chat surface can render — never a raw traceback."""

    @staticmethod
    def _cause_chain(exc: BaseException) -> list[BaseException]:
        chain: list[BaseException] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            chain.append(current)
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return chain

    def translate(self, exc: Exception, *, agent_key: str) -> AppError:
        if isinstance(exc, AppError):
            return exc
        # SDK errors wrap the transport failure — classify by the whole chain.
        chain = self._cause_chain(exc)
        if any(
            isinstance(e, (A2AClientTimeoutError, httpx.TimeoutException)) for e in chain
        ):
            return ExternalServiceError(
                f"Agent '{agent_key}' timed out.",
                code="a2a_timeout",
                cause=exc,
            )
        if any(isinstance(e, (httpx.ConnectError, httpx.TransportError)) for e in chain):
            return ExternalServiceError(
                f"Agent '{agent_key}' is unreachable — check its card_url and that it is running.",
                code="a2a_unreachable",
                cause=exc,
            )
        if isinstance(exc, A2AClientError):
            return ExternalServiceError(
                f"Agent '{agent_key}' returned a protocol error.",
                code="a2a_protocol_error",
                cause=exc,
            )
        return ExternalServiceError(
            f"The call to agent '{agent_key}' failed.",
            code="a2a_call_failed",
            cause=exc,
        )
