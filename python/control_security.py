"""Local control-plane boundary. Tokens never enter static assets or URLs sent to servers."""

import os
import asyncio
import re
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

BIND_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1024 * 1024
MAX_CONTROL_REQUESTS = 32
BODY_TIMEOUT = 5


class ControlSecurity:
    def __init__(self):
        token = os.environ.get("LEGILIMENS_CONTROL_TOKEN")
        if token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ValueError("LEGILIMENS_CONTROL_TOKEN must contain 32-128 URL-safe characters")
        self.token = token or secrets.token_urlsafe(32)
        self.origins = set()
        self.configure()

    def configure(self, api_port=4436, ws_port=4435, proxy_port=4433):
        self.api_port = api_port
        self.ws_port = ws_port
        self.proxy_port = proxy_port
        self.origins.clear()
        self.origins.update({
            f"http://{host}:{port}"
            for host in ("localhost", BIND_HOST)
            for port in (api_port, 5180)
        })

    def valid_token(self, candidate):
        return (isinstance(candidate, str) and candidate.isascii()
                and secrets.compare_digest(candidate, self.token))

    def valid_origin(self, origin):
        # Non-browser clients omit Origin but must still authenticate.
        return origin is None or origin in self.origins


security = ControlSecurity()


def valid_host(value):
    if not value or any(c in value for c in "/\\@?# \t\r\n"):
        return False
    try:
        parsed = urlsplit("//" + value)
        return parsed.hostname in (BIND_HOST, "localhost") and parsed.port is not None
    except ValueError:
        return False


class ControlBoundary:
    def __init__(self, app):
        self.app = app
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        error = None
        status = 403
        if len(headers.getlist("host")) != 1 or not valid_host(headers.get("host")):
            error = "Invalid control host"
        elif len(headers.getlist("origin")) > 1 or not security.valid_origin(headers.get("origin")):
            error = "Origin is not allowed"
        else:
            path = scope["path"]
            public_asset = (scope["method"] in ("GET", "HEAD")
                            and (path in ("/", "/index.html") or path.startswith("/assets/")))
            preflight = (scope["method"] == "OPTIONS" and "origin" in headers
                         and "access-control-request-method" in headers)
            if not public_asset and not preflight:
                values = headers.getlist("authorization")
                candidate = values[0][7:] if len(values) == 1 and values[0].startswith("Bearer ") else None
                if not security.valid_token(candidate):
                    status, error = 401, "A valid control token is required"
        if error:
            response_headers = {"Cache-Control": "no-store"}
            if headers.get("origin") in security.origins:
                response_headers.update({"Access-Control-Allow-Origin": headers["origin"], "Vary": "Origin"})
            response = JSONResponse({"detail": error}, status_code=status,
                                    headers=response_headers)
            return await response(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                ])
            await send(message)

        if self.inflight >= MAX_CONTROL_REQUESTS:
            return await JSONResponse({"detail": "Control request capacity exceeded"}, status_code=429)(scope, receive, secure_send)
        self.inflight += 1
        reading_body = True
        try:
            lengths = headers.getlist("content-length")
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, secure_send)
            if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > MAX_BODY_BYTES):
                return await JSONResponse({"detail": "Request body exceeds 1 MiB"}, status_code=413)(scope, receive, secure_send)
            body = bytearray()
            async with asyncio.timeout(BODY_TIMEOUT):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        return await JSONResponse({"detail": "Request body exceeds 1 MiB"}, status_code=413)(scope, receive, secure_send)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
            reading_body = False
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            await self.app(scope, bounded_receive, secure_send)
        except TimeoutError:
            if not reading_body:
                raise
            await JSONResponse({"detail": "Request body timed out"}, status_code=408)(scope, receive, secure_send)
        finally:
            self.inflight -= 1
