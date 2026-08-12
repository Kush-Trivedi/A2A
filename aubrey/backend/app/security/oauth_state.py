from dataclasses import dataclass
from functools import lru_cache
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from .settings import AuthSettings, get_auth_settings

_STATE_SALT = "ace.oauth.state.v1"
_STATE_MAX_AGE_SECONDS = 600


@dataclass(frozen=True, slots=True)
class OAuthState:
    state: str
    code_verifier: str
    nonce: str
    return_to: str


class OAuthStateManager:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        secret = (settings or get_auth_settings()).session_secret
        self._serializer = URLSafeTimedSerializer(secret, salt=_STATE_SALT)

    @property
    def cookie_name(self) -> str:
        return "__ace_oauth_state"

    @property
    def max_age_seconds(self) -> int:
        return _STATE_MAX_AGE_SECONDS

    def pack(self, state: OAuthState) -> str:
        return self._serializer.dumps(
            {
                "s": state.state,
                "v": state.code_verifier,
                "n": state.nonce,
                "r": state.return_to,
            }
        )

    def unpack(self, token: str) -> OAuthState | None:
        try:
            payload = self._serializer.loads(token, max_age=_STATE_MAX_AGE_SECONDS)
        except (BadSignature, SignatureExpired):
            return None
        return OAuthState(
            state=payload.get("s", ""),
            code_verifier=payload.get("v", ""),
            nonce=payload.get("n", ""),
            return_to=payload.get("r", ""),
        )


@lru_cache(maxsize=1)
def get_oauth_state_manager() -> OAuthStateManager:
    return OAuthStateManager()
