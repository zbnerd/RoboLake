"""Streaming ASGI request-body byte limit."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    """Internal signal translated by the API boundary to a stable 413."""


class BodyLimitMiddleware:
    """Count every HTTP request chunk without trusting Content-Length."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0
        buffered: list[Message] = []
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "REQUEST_BODY_TOO_LARGE",
                                "message": "Request body exceeds the manifest protocol limit.",
                                "next_action": None,
                            }
                        },
                    )
                    await response(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        iterator = iter(buffered)

        async def replay_receive() -> Message:
            return next(iterator, {"type": "http.disconnect"})

        await self.app(scope, replay_receive, send)
