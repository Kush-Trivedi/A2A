from dataclasses import dataclass
from functools import lru_cache
from ..config.application_context import get_application_context

_DEFAULT_AUTHORITY = "https://login.microsoftonline.com"


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AuthSettings:
    tenant: str
    client_id: str
    client_secret: str
    authority: str
    redirect_uri: str
    post_login_redirect_uri: str
    post_logout_redirect_uri: str
    scopes: tuple[str, ...]
    api_audience: str
    graph_endpoint: str
    oauth_enabled: bool
    jwt_auth_enabled: bool
    rbac_enabled: bool
    session_cookie_name: str
    session_ttl_seconds: int
    session_secret: str
    cookie_secure: bool
    cookie_samesite: str
    allowed_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    cors_allow_credentials: bool
    max_request_bytes: int
    gzip_min_bytes: int

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant}/oauth2/v2.0/token"

    @property
    def logout_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant}/oauth2/v2.0/logout"

    @property
    def issuer(self) -> str:
        return f"{self.authority}/{self.tenant}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"{self.authority}/{self.tenant}/discovery/v2.0/keys"


def _split_scopes(raw: object) -> tuple[str, ...]:
    if not raw:
        return ("openid", "profile", "email", "offline_access", "User.Read")
    if isinstance(raw, (list, tuple)):
        items = [str(s).strip() for s in raw]
    else:
        items = [s.strip() for s in str(raw).replace(" ", ",").split(",")]
    return tuple(s for s in items if s)


def _split_csv(raw: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    if isinstance(raw, (list, tuple)):
        items = [str(s).strip() for s in raw]
    else:
        items = [s.strip() for s in str(raw).split(",")]
    values = tuple(s for s in items if s)
    return values or default


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    ac = get_application_context()
    entra = ac.microsoft["entra"]
    security = ac.security
    session = security.get("session", {}) or {}
    cors = security.get("cors", {}) or {}
    limits = security.get("limits", {}) or {}
    is_local = ac.environment == "local"

    authority = str(security.get("entra_authority") or _DEFAULT_AUTHORITY).rstrip("/")
    session_secret = str(session.get("secret") or "") or str(entra.get("client_secret", ""))
    session_ttl = session.get("ttl_seconds") or entra.get("refresh_token_ttl_seconds", 3600)

    default_origins = (
        (
            "http://localhost:5173",
            "https://localhost:5173",
            "http://localhost:3000",
        )
        if is_local
        else ()
    )
    default_hosts = (
        ("localhost", "127.0.0.1", "0.0.0.0")
        if is_local
        else ("*",)
    )

    return AuthSettings(
        tenant=str(entra["tenant_id"]),
        client_id=str(entra["client_id"]),
        client_secret=str(entra.get("client_secret", "")),
        authority=authority,
        redirect_uri=str(entra["redirect_uri"]),
        post_login_redirect_uri=str(entra.get("post_login_redirect_uri", "/")),
        post_logout_redirect_uri=str(entra.get("post_logout_redirect_uri", "/")),
        scopes=_split_scopes(entra.get("scopes")),
        api_audience=str(entra.get("api_audience", "") or ""),
        graph_endpoint=str(
            security.get("graph_endpoint") or "https://graph.microsoft.com/v1.0"
        ).rstrip("/"),
        oauth_enabled=_as_bool(entra.get("oauth_enabled"), default=True),
        jwt_auth_enabled=_as_bool(entra.get("jwt_auth_enabled"), default=True),
        rbac_enabled=_as_bool(ac.authorization.get("rbac_enabled"), default=True),
        session_cookie_name=str(session.get("cookie_name") or "ace_session"),
        session_ttl_seconds=int(session_ttl),
        session_secret=session_secret,
        cookie_secure=_as_bool(session.get("cookie_secure"), default=not is_local),
        cookie_samesite=str(session.get("cookie_samesite") or "lax"),
        allowed_origins=_split_csv(cors.get("allowed_origins"), default_origins),
        allowed_hosts=_split_csv(security.get("trusted_hosts"), default_hosts),
        cors_allow_credentials=_as_bool(cors.get("allow_credentials"), default=True),
        max_request_bytes=int(limits.get("max_request_bytes") or 10 * 1024 * 1024),
        gzip_min_bytes=int(limits.get("gzip_min_bytes") or 1000),
    )
