import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from a2a.client import ClientCallContext, CredentialService

from ...security.settings import AuthSettings, get_auth_settings
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError

logger = Logger(__name__).get_logger()

_REFRESH_SKEW_SECONDS = 60
_AUDIENCE_STATE_KEY = "ace_auth_audience"


class ServiceTokenProvider(ABC):
    """Provides service-plane bearer tokens for outbound A2A calls."""

    @abstractmethod
    async def token_for(self, audience: str) -> str | None:
        """Return a bearer token for the given audience, or None if unavailable."""


@dataclass
class _CachedToken:
    token: str
    expires_at: float

    @property
    def is_fresh(self) -> bool:
        return time.time() < (self.expires_at - _REFRESH_SKEW_SECONDS)


class EntraServiceTokenProvider(ServiceTokenProvider):
    """Entra client-credentials tokens, cached per audience.

    Uses ACE's own app registration (microsoft.entra in yaml) as the calling
    service identity. The user's identity never rides in this token — it
    travels in the ContextEnvelope. Mirrors the AzurePostgresToken cache
    pattern.
    """

    def __init__(self, settings: AuthSettings | None = None) -> None:
        self._settings = settings or get_auth_settings()
        self._cache: dict[str, _CachedToken] = {}

    async def token_for(self, audience: str) -> str | None:
        normalized = (audience or "").strip()
        if not normalized:
            return None

        cached = self._cache.get(normalized)
        if cached is not None and cached.is_fresh:
            return cached.token

        token, expires_in = await self._acquire(normalized)
        self._cache[normalized] = _CachedToken(
            token=token, expires_at=time.time() + expires_in
        )
        return token

    async def _acquire(self, audience: str) -> tuple[str, float]:
        scope = audience if audience.endswith("/.default") else f"{audience}/.default"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret,
            "scope": scope,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self._settings.token_endpoint, data=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                "Service token acquisition failed.",
                code="service_token_failed",
                details={"audience": audience},
                cause=exc,
            ) from exc

        token = str(body.get("access_token") or "")
        if not token:
            raise ExternalServiceError(
                "Token endpoint returned no access_token.",
                code="service_token_failed",
                details={"audience": audience},
            )
        expires_in = float(body.get("expires_in") or 3600)
        logger.info(
            "Service token acquired", extra={"audience": audience, "expires_in": expires_in}
        )
        return token, expires_in


class AceCredentialService(CredentialService):
    """Feeds the SDK's AuthInterceptor from a ServiceTokenProvider.

    The target audience travels in the per-call ClientCallContext state; the
    interceptor only fires when the agent's card declares security schemes,
    so agents without auth configured are called anonymously — behavior is
    driven by the card, not by environment.
    """

    def __init__(self, token_provider: ServiceTokenProvider) -> None:
        self._tokens = token_provider

    @staticmethod
    def context_for(audience: str | None) -> ClientCallContext | None:
        if not audience:
            return None
        return ClientCallContext(state={_AUDIENCE_STATE_KEY: audience})

    async def get_credentials(
        self, security_scheme_name: str, context: ClientCallContext | None
    ) -> str | None:
        if context is None:
            return None
        audience = str(context.state.get(_AUDIENCE_STATE_KEY) or "")
        if not audience:
            return None
        return await self._tokens.token_for(audience)


_provider: EntraServiceTokenProvider | None = None


def get_service_token_provider() -> EntraServiceTokenProvider:
    global _provider
    if _provider is None:
        _provider = EntraServiceTokenProvider()
    return _provider
