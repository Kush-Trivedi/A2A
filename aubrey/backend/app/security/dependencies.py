from fastapi import Request, Response
from ..utils.common.logger import Logger
from ..utils.errors import CsrfValidationError, UnauthorizedError
from .cookies import SessionCookieManager
from .session import SessionContext, SessionStore, get_session_store

logger = Logger(__name__).get_logger()

_CSRF_HEADER = "X-CSRF-Token"

class AuthDependencies:
    def __init__(
        self,
        session_store: SessionStore | None = None,
        cookies: SessionCookieManager | None = None,
    ) -> None:
        self._store = session_store or get_session_store()
        self._cookies = cookies or SessionCookieManager()

    @staticmethod
    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _user_agent(request: Request) -> str:
        return request.headers.get("user-agent", "unknown")

    async def optional_context(
        self, request: Request, response: Response
    ) -> SessionContext | None:
        session_id = request.cookies.get(self._cookies.cookie_name)
        if not session_id:
            return None

        context = await self._store.get(
            session_id,
            ip=self._client_ip(request),
            user_agent=self._user_agent(request),
        )
        if context is None:
            self._cookies.clear(response)
            return None

        await self._store.touch(session_id)
        return context

    async def current_context(
        self, request: Request, response: Response
    ) -> SessionContext:
        context = await self.optional_context(request, response)
        if context is None:
            raise UnauthorizedError("Authentication required.")
        return context

    async def require_csrf(
        self, request: Request, response: Response
    ) -> SessionContext:
        context = await self.current_context(request, response)
        token = request.headers.get(_CSRF_HEADER, "")
        if not token:
            raise CsrfValidationError("Missing CSRF token.")
        if not await self._store.verify_csrf(context.session_id, token):
            raise CsrfValidationError("Invalid CSRF token.")
        return context
    
_auth_dependencies = AuthDependencies()

get_optional_context = _auth_dependencies.optional_context
get_current_context = _auth_dependencies.current_context
require_csrf = _auth_dependencies.require_csrf
