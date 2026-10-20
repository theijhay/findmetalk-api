"""Request id and one access-log line per HTTP request.

Pure ASGI rather than ``BaseHTTPMiddleware`` so it passes WebSocket traffic
(the presence gateway) through untouched and does not buffer responses.
"""

import re
import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.core.errors import unhandled_error_handler
from api.utils.logger import logger, request_id_var

REQUEST_ID_HEADER = "x-request-id"
# A caller-supplied id is kept only when it is short and plain, so it cannot
# smuggle content into the logs. Anything else is replaced.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = incoming if incoming and _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = list(message.get("headers", []))
                headers = [
                    (key, value) for key, value in headers if key.lower() != REQUEST_ID_HEADER.encode()
                ]
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            # Handle while the request context is active. Starlette's outer
            # server-error middleware runs after this context is reset and
            # re-raises errors to the server, which can log sensitive text.
            if response_started:
                raise
            response = await unhandled_error_handler(Request(scope), exc)
            await response(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    # The route template (`/circles/{circle_id}/invites`), not
                    # the concrete path: a path can carry an invite code or an
                    # id, and an unmatched path is whatever a scanner sent.
                    "route": _route_template(scope),
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)


def _header(scope: Scope, name: str) -> str | None:
    target = name.encode()
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            decoded: str = value.decode("latin-1")
            return decoded
    return None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    if route is None and "endpoint" in scope:
        # FastAPI routes record themselves in the scope; plain Starlette ones
        # (/docs, /openapi.json) record only their endpoint.
        app = scope.get("app")
        routes = getattr(app, "routes", [])
        route = next((r for r in routes if getattr(r, "endpoint", None) is scope["endpoint"]), None)
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "<unmatched>"
