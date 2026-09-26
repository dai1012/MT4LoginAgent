from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES = 128 * 1024
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RequestBodyLimitMiddleware:
    """Bound the actual ASGI body, including chunked requests without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") not in _MUTATING_METHODS:
            await self.app(scope, receive, send)
            return
        try:
            declared = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except (TypeError, ValueError):
            declared = 0
        if declared > self.max_bytes:
            await self._send_limit_response(scope, receive, send)
            return

        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    await self._send_limit_response(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    async def _send_limit_response(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body is too large"},
        )
        await response(scope, receive, send)
