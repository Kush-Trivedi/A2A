from fastapi import Response
from ..settings import AuthSettings, get_auth_settings

class SessionCookieManager:
    def __init__(self, settings: AuthSettings | None = None) -> None:
        self._settings = settings or get_auth_settings()

    @property
    def cookie_name(self) -> str:
        return self._settings.session_cookie_name

    @property
    def csrf_cookie_name(self) -> str:
        return f"{self._settings.session_cookie_name}_csrf"

    def set(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            key=self._settings.session_cookie_name,
            value=session_id,
            httponly=True,
            secure=self._settings.cookie_secure,
            samesite=self._settings.cookie_samesite,
            max_age=self._settings.session_ttl_seconds,
            path="/",
        )

    def set_csrf(self, response: Response, csrf_token: str) -> None:
        response.set_cookie(
            key=self.csrf_cookie_name,
            value=csrf_token,
            httponly=False,
            secure=self._settings.cookie_secure,
            samesite=self._settings.cookie_samesite,
            max_age=self._settings.session_ttl_seconds,
            path="/",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie(
            key=self._settings.session_cookie_name,
            httponly=True,
            secure=self._settings.cookie_secure,
            samesite=self._settings.cookie_samesite,
            path="/",
        )

        response.delete_cookie(
            key=self.csrf_cookie_name,
            httponly=False,
            secure=self._settings.cookie_secure,
            samesite=self._settings.cookie_samesite,
            path="/",
        )
