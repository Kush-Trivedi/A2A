import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWK
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_JWKS_CACHE_TTL_SECONDS = 43_200
_PUBLIC_PATH_PREFIXES = ("/.well-known/",)


@dataclass(frozen=True)
class AgentAuthSettings:
    """Team-owned auth configuration from agent.yaml `auth:`.

    `enabled` is an explicit yaml switch — never inferred from placeholder
    values. `issuer`/`jwks_url` overrides exist for sovereign clouds, B2C
    custom domains, and test harnesses; when empty they derive from the
    Entra tenant.
    """

    enabled: bool
    tenant_id: str
    audience: str
    authority: str = "https://login.microsoftonline.com"
    issuer_override: str = ""
    jwks_url_override: str = ""

    @property
    def issuer(self) -> str:
        if self.issuer_override:
            return self.issuer_override
        return f"{self.authority}/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        if self.jwks_url_override:
            return self.jwks_url_override
        return f"{self.authority}/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def openid_configuration_url(self) -> str:
        return f"{self.authority}/{self.tenant_id}/v2.0/.well-known/openid-configuration"


class JwksCache:
    """Fetches and caches the tenant's signing keys (kid -> JWK)."""

    def __init__(self, jwks_url: str) -> None:
        self._jwks_url = jwks_url
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0

    async def get_jwk(self, kid: str) -> dict[str, Any]:
        if self._is_stale() or kid not in self._keys:
            await self._refresh()
        if kid not in self._keys:
            raise jwt.InvalidTokenError(f"Signing key '{kid}' not found in JWKS.")
        return self._keys[kid]

    def _is_stale(self) -> bool:
        return time.monotonic() - self._fetched_at > _JWKS_CACHE_TTL_SECONDS

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self._jwks_url)
            response.raise_for_status()
            data = response.json()
        self._keys = {
            entry["kid"]: entry
            for entry in data.get("keys", [])
            if entry.get("use") in {"sig", None}
        }
        self._fetched_at = time.monotonic()


class EntraTokenValidator:
    """Validates inbound service-plane bearer tokens (issuer + audience)."""

    def __init__(
        self, settings: AgentAuthSettings, jwks_cache: JwksCache | None = None
    ) -> None:
        self._settings = settings
        self._jwks = jwks_cache or JwksCache(settings.jwks_url)

    async def validate(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise jwt.InvalidTokenError("Token header missing 'kid'.")

        jwk_data = await self._jwks.get_jwk(kid)
        signing_key = PyJWK.from_dict(jwk_data).key

        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self._settings.audience,
            issuer=self._settings.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )


class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated A2A requests when auth is enabled.

    The agent card (/.well-known/*) stays public — discovery is
    unauthenticated by design; everything else requires a valid bearer token.
    """

    def __init__(self, app, validator: EntraTokenValidator) -> None:
        super().__init__(app)
        self._validator = validator

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith(_PUBLIC_PATH_PREFIXES):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return self._unauthorized("Missing bearer token.")

        try:
            claims = await self._validator.validate(token.strip())
        except jwt.InvalidTokenError as exc:
            return self._unauthorized(f"Invalid token: {exc}")
        except Exception:  # noqa: BLE001 — never leak internals
            return self._unauthorized("Token validation failed.")

        request.state.service_claims = claims
        return await call_next(request)

    @staticmethod
    def _unauthorized(detail: str) -> JSONResponse:
        return JSONResponse(
            {"error": "unauthorized", "detail": detail},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
