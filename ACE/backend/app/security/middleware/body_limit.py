from collections.abc import Awaitable, Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_RequestResponseCall = Callable[[Request], Awaitable[Response]]

class _BodyTooLarge(Exception):
    """Internal signal raised when a streamed body exceeds the configured cap."""
    

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": "Request body too large.",
                "data": {
                    "error": {
                        "code": "payload_too_large",
                        "category": "validation",
                    }
                },
            },
        )

    async def dispatch(
        self, request: Request, call_next: _RequestResponseCall
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_bytes:
                    return self._too_large()
            except ValueError:
                pass

        max_bytes = self._max_bytes
        received = 0
        original_receive = request._receive

        async def _guarded_receive():
            nonlocal received
            message = await original_receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _BodyTooLarge()
            return message

        request._receive = _guarded_receive
        try:
            return await call_next(request)
        except _BodyTooLarge:
            return self._too_large()
