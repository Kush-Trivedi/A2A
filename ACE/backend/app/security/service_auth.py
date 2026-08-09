from fastapi import Request

from ..config.application_context import get_application_context
from ..utils.common.logger import Logger
from ..utils.errors import UnauthorizedError
from .jwt_validator import JWTValidator, get_jwt_validator

logger = Logger(__name__).get_logger()


class ServiceAuthGuard:
    """Guards service-plane capability endpoints.

    `security.capability_auth_enabled` (explicit yaml switch) turns bearer
    validation on; tokens are validated with the same Entra JWKS machinery as
    user JWTs. Off = local/dev topologies where agents and ACE share a trust
    boundary — an explicit configuration choice, never inferred.
    """

    def __init__(self, validator: JWTValidator | None = None) -> None:
        self._validator = validator or get_jwt_validator()

    @property
    def enabled(self) -> bool:
        return bool(
            get_application_context().security.get("capability_auth_enabled", False)
        )

    async def authenticate(self, request: Request) -> None:
        if not self.enabled:
            return
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise UnauthorizedError("Service bearer token required.")
        try:
            await self._validator.validate(token.strip())
        except Exception as exc:  # noqa: BLE001
            raise UnauthorizedError("Service token validation failed.") from exc


_guard: ServiceAuthGuard | None = None


def get_service_auth_guard() -> ServiceAuthGuard:
    global _guard
    if _guard is None:
        _guard = ServiceAuthGuard()
    return _guard
