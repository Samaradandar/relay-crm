# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request body size limit (BUG-P1-22). Files go straight to storage by signed URL, so API bodies
are small JSON documents; anything larger is refused with 413 in the standard error envelope."""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 2 * 1024 * 1024
# The local-development file store (no S3/MinIO) receives the file itself, up to the file limit.
LOCAL_FILES_PREFIX = "/files/local/"
MAX_FILE_BYTES = 25 * 1024 * 1024 + 64 * 1024


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = MAX_FILE_BYTES if scope["path"].startswith(LOCAL_FILES_PREFIX) else self.max_bytes
        for name, value in scope["headers"]:
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await self._refuse(send)
                return
        seen = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        body = json.dumps(
            {
                "code": "payload_too_large",
                "message": f"The request is larger than {self.max_bytes // (1024 * 1024)} MB",
                "details": {},
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
