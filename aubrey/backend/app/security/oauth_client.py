import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode
import httpx
from ..utils.common.logger import Logger
from .settings import AuthSettings, get_auth_settings

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    id_token: str
    refresh_token: str
    expires_in: int
    token_type: str
    scope: str


@dataclass(frozen=True)
class EntraUserProfile:
    actor_id: str
    email: str
    first_name: str
    last_name: str
    display_name: str


@dataclass(frozen=True)
class AuthorizeRequest:
    url: str
    state: str
    code_verifier: str
    nonce: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    code_verifier = _b64url(secrets.token_bytes(32))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return code_verifier, code_challenge


class EntraOauthClient:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        self._settings = settings or get_auth_settings()
        if not self._settings.oauth_enabled:
            raise RuntimeError("Entra OAuth is not enabled in settings.")

    def build_authorize_request(self, *, return_to: str | None = None) -> AuthorizeRequest:
        code_verifier, code_challenge = generate_pkce()
        state = _b64url(secrets.token_bytes(24))
        nonce = _b64url(secrets.token_bytes(16))

        params = {
            "client_id": self._settings.client_id,
            "response_type": "code",
            "redirect_uri": self._settings.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self._settings.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "prompt": "select_account",
        }

        url = f"{self._settings.authorize_endpoint}?{urlencode(params)}"
        return AuthorizeRequest(
            url=url,
            state=state,
            code_verifier=code_verifier,
            nonce=nonce,
        )

    async def exchange_code(self, *, code: str, code_verifier: str) -> TokenResponse:
        return await self._token_request(
            {
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.redirect_uri,
                "code_verifier": code_verifier,
                "scope": " ".join(self._settings.scopes),
            }
        )

    async def refresh_token(self, *, refresh_token: str, scope: str | None = None) -> TokenResponse:
        scope_value = scope or " ".join(self._settings.scopes)
        return await self._token_request(
            {
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scope_value,
            }
        )

    async def fetch_user_profile(self, access_token: str) -> EntraUserProfile | None:
        if not access_token or not any(
            scope.casefold() == "user.read" for scope in self._settings.scopes
        ):
            return None

        url = f"{self._settings.graph_endpoint}/me"
        params = {
            "$select": "id,displayName,givenName,surname,mail,userPrincipalName"
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            payload = response.json()

        return EntraUserProfile(
            actor_id=str(payload.get("id") or ""),
            email=str(payload.get("mail") or payload.get("userPrincipalName") or ""),
            first_name=str(payload.get("givenName") or ""),
            last_name=str(payload.get("surname") or ""),
            display_name=str(payload.get("displayName") or ""),
        )

    def build_logout_url(self) -> str:
        params: dict[str, str] = {}
        if self._settings.post_logout_redirect_uri:
            params["post_logout_redirect_uri"] = self._settings.post_logout_redirect_uri
        base = self._settings.logout_endpoint
        return f"{base}?{urlencode(params)}" if params else base

    async def _token_request(self, data: dict[str, str]) -> TokenResponse:
        body = {key: value or "" for key, value in data.items()}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self._settings.token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code >= 400:
                logger.warning(
                    "Entra token endpoint error",
                    extra={
                        "status_code": response.status_code,
                        "body": response.text[:500],
                    },
                )
            response.raise_for_status()
            payload = response.json()
            return TokenResponse(
                access_token=payload.get("access_token", ""),
                id_token=payload.get("id_token", ""),
                refresh_token=payload.get("refresh_token", ""),
                expires_in=int(payload.get("expires_in", 0)),
                token_type=payload.get("token_type", ""),
                scope=payload.get("scope", ""),
            )
