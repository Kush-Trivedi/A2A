import hashlib
import hmac
import secrets

from ...security.settings import AuthSettings, get_auth_settings

_SESSION_ID_PREFIX = "bs_"
_SESSION_ID_ENTROPY_BYTES = 32


class SessionCrypto:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        self._secret = (settings or get_auth_settings()).session_secret.encode("utf-8")

    def new_session_id(self) -> str:
        return _SESSION_ID_PREFIX + secrets.token_urlsafe(_SESSION_ID_ENTROPY_BYTES)

    def new_csrf_token(self) -> str:
        return secrets.token_urlsafe(_SESSION_ID_ENTROPY_BYTES)

    def hash(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def fingerprint(self, ip: str, user_agent: str) -> tuple[str, str]:
        return self.hash(ip or "unknown"), self.hash(user_agent or "unknown")

    def verify(self, value: str, expected_hash: str) -> bool:
        return hmac.compare_digest(self.hash(value), expected_hash)

    def verify_hash(self, actual_hash: str, expected_hash: str) -> bool:
        return hmac.compare_digest(actual_hash, expected_hash)
