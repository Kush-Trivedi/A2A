import jwt
from jwt import PyJWK
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from ..utils.common.logger import Logger
from .jwks_cache import JWKSCache
from .settings import AuthSettings, get_auth_settings

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class ValidatedIdentity:
    tenant_id: str
    actor_id: str
    email: str
    first_name: str
    last_name: str
    display_name: str
    app_roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    raw_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def has_group_overage(self) -> bool:
        names = self.raw_claims.get("_claim_names") or {}
        return "groups" in names


class JWTValidator:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        self._settings = settings or get_auth_settings()
        self._jwks = JWKSCache(self._settings.jwks_uri)

    async def validate(self, token: str) -> ValidatedIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            logger.warning(
                "Entra id_token header could not be parsed",
                extra={"error_code": "jwt_header_invalid"},
            )
            raise

        kid = header.get("kid")
        if not kid:
            raise jwt.InvalidTokenError("id_token header missing 'kid'.")

        jwk_data = await self._jwks.get_jwk(kid)
        signing_key = PyJWK.from_dict(jwk_data).key

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._settings.client_id,
                issuer=self._settings.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.InvalidTokenError:
            logger.warning(
                "Entra id_token validation failed",
                extra={"error_code": "jwt_invalid"},
            )
            raise

        return self._to_identity(claims)

    def _to_identity(self, claims: dict[str, Any]) -> ValidatedIdentity:
        tenant_id = str(claims.get("tid", ""))
        actor_id = str(claims.get("oid") or claims.get("sub", ""))
        email = str(
            claims.get("email")
            or claims.get("preferred_username")
            or claims.get("upn")
            or ""
        )

        given = str(claims.get("given_name", "") or "")
        family = str(claims.get("family_name", "") or "")
        display = str(claims.get("name", "") or (f"{given} {family}".strip()))

        app_roles = tuple(str(r) for r in (claims.get("roles") or ()))
        groups = tuple(str(g) for g in (claims.get("groups") or ()))

        return ValidatedIdentity(
            tenant_id=tenant_id,
            actor_id=actor_id,
            email=email,
            first_name=given,
            last_name=family,
            display_name=display,
            app_roles=app_roles,
            groups=groups,
            raw_claims=claims,
        )


@lru_cache(maxsize=1)
def get_jwt_validator() -> JWTValidator:
    return JWTValidator()
