from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, unquote

import httpx
from flask.sessions import SecureCookieSessionInterface
from starlette.responses import PlainTextResponse, Response
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket

from helpers import login
from plugins._office.helpers import wopi_store


UPSTREAM_HTTP = "http://127.0.0.1:9980"
UPSTREAM_WS = "ws://127.0.0.1:9980"
HTTP_PROXY_ATTEMPTS = 4
HTTP_PROXY_RETRY_DELAYS = (0.2, 0.5, 1.0)
TRANSIENT_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)


class OfficeProxy:
    def __init__(self, flask_app=None) -> None:
        self.flask_app = flask_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await self.websocket(scope, receive, send)
            return
        if scope["type"] == "http":
            await self.http(scope, receive, send)
            return
        await PlainTextResponse("Unsupported scope", status_code=500)(scope, receive, send)

    def upstream_path(self, scope: Scope) -> str:
        raw_path = scope.get("raw_path")
        if raw_path:
            path = raw_path.decode("latin-1")
        else:
            path = scope.get("path", "")
        if not path.startswith("/office"):
            path = "/office" + (path if path.startswith("/") else "/" + path)
        query = scope.get("query_string", b"").decode("latin-1")
        return path + (f"?{query}" if query else "")

    async def http(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.is_authorized(scope):
            await PlainTextResponse("Authentication required", status_code=401)(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            more = bool(message.get("more_body"))

        method = scope.get("method", "GET")
        headers = self.forward_headers(scope)
        url = UPSTREAM_HTTP + self.upstream_path(scope)
        try:
            upstream, attempts = await self.request_upstream_http(method, url, body, headers)
            disable_cache = self.should_disable_cache(scope, upstream.status_code)
            omitted_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            if disable_cache:
                omitted_headers.update({"cache-control", "pragma", "expires"})
            response_headers = {
                key: value
                for key, value in upstream.headers.items()
                if key.lower() not in omitted_headers
            }
            response_headers["X-A0-Office-Proxy-Attempts"] = str(attempts)
            if disable_cache:
                response_headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
                response_headers["Pragma"] = "no-cache"
                response_headers["Expires"] = "0"
            await Response(upstream.content, status_code=upstream.status_code, headers=response_headers)(scope, receive, send)
        except Exception as exc:
            await PlainTextResponse(
                f"Collabora is unavailable after {HTTP_PROXY_ATTEMPTS} attempts: {exc}",
                status_code=503,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )(scope, receive, send)

    async def request_upstream_http(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[httpx.Response, int]:
        for attempt in range(1, HTTP_PROXY_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=36000.0), follow_redirects=False) as client:
                    response = await client.request(method, url, content=body, headers=headers)
                return response, attempt
            except TRANSIENT_HTTP_ERRORS:
                if attempt >= HTTP_PROXY_ATTEMPTS:
                    raise
                delay_index = min(attempt - 1, len(HTTP_PROXY_RETRY_DELAYS) - 1)
                await asyncio.sleep(HTTP_PROXY_RETRY_DELAYS[delay_index])
        raise RuntimeError("Collabora proxy retry loop exited unexpectedly")

    def should_disable_cache(self, scope: Scope, status_code: int) -> bool:
        path = self.upstream_path(scope).split("?", 1)[0]
        return status_code >= 500 or path.endswith("/cool.html")

    async def websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        import websockets

        websocket = WebSocket(scope, receive=receive, send=send)
        if not self.is_authorized(scope):
            await websocket.close(code=1008)
            return

        await websocket.accept()
        url = self.upstream_websocket_url(scope)
        headers = self.websocket_headers(scope)
        origin = self.header_value(scope, b"origin", "")
        try:
            async with websockets.connect(
                url,
                host="127.0.0.1",
                port=9980,
                origin=origin or None,
                additional_headers=headers,
                open_timeout=10,
                ping_interval=None,
            ) as upstream:
                async def browser_to_upstream():
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                        elif "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])

                async def upstream_to_browser():
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)

                await asyncio.gather(browser_to_upstream(), upstream_to_browser())
        except Exception:
            await websocket.close(code=1011)

    def forward_headers(self, scope: Scope) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key_b, value_b in scope.get("headers", []):
            key = key_b.decode("latin-1")
            value = value_b.decode("latin-1")
            if key.lower() in {"host", "content-length", "connection"}:
                continue
            headers[key] = value
        host = dict(scope.get("headers", [])).get(b"host", b"localhost:32080").decode("latin-1")
        headers["Host"] = host
        headers["X-Forwarded-Proto"] = scope.get("scheme", "http")
        return headers

    def upstream_websocket_url(self, scope: Scope) -> str:
        host = self.header_value(scope, b"host", "localhost:32080")
        return f"ws://{host}{self.upstream_path(scope)}"

    def websocket_headers(self, scope: Scope) -> list[tuple[str, str]]:
        hop_by_hop = {
            b"host",
            b"connection",
            b"upgrade",
            b"origin",
            b"sec-websocket-key",
            b"sec-websocket-version",
            b"sec-websocket-extensions",
        }
        headers = [
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in scope.get("headers", [])
            if key.lower() not in hop_by_hop
        ]
        return headers

    def header_value(self, scope: Scope, name: bytes, default: str = "") -> str:
        value = dict(scope.get("headers", [])).get(name, default.encode("latin-1"))
        return value.decode("latin-1") if isinstance(value, bytes) else str(value)

    def is_authorized(self, scope: Scope) -> bool:
        if self._has_valid_wopi_token(scope):
            return True
        credentials_hash = login.get_credentials_hash()
        if not credentials_hash:
            return True
        if not self.flask_app:
            return False
        serializer = SecureCookieSessionInterface().get_signing_serializer(self.flask_app)
        if not serializer:
            return False
        cookie_header = dict(scope.get("headers", [])).get(b"cookie", b"").decode("latin-1")
        if not cookie_header:
            return False
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        session_cookie = cookies.get(self.flask_app.config.get("SESSION_COOKIE_NAME", "session"))
        if not session_cookie:
            return False
        try:
            session_data = serializer.loads(session_cookie.value)
        except Exception:
            return False
        return session_data.get("authentication") == credentials_hash

    def _has_valid_wopi_token(self, scope: Scope) -> bool:
        if not self._is_collabora_editor_channel(scope):
            return False
        path = self.upstream_path(scope)
        decoded = unquote(path)
        marker = "/wopi/files/"
        marker_index = decoded.find(marker)
        if marker_index == -1:
            return False
        file_part = decoded[marker_index + len(marker):]
        file_id, separator, query_text = file_part.partition("?")
        if not separator or not file_id:
            return False
        file_id = file_id.strip("/")
        if "/" in file_id:
            file_id = file_id.split("/", 1)[0]
        token = (parse_qs(query_text, keep_blank_values=True).get("access_token") or [""])[0]
        if not token:
            return False
        try:
            wopi_store.validate_token(token, file_id, require_write=False)
        except Exception:
            return False
        return True

    def _is_collabora_editor_channel(self, scope: Scope) -> bool:
        path = scope.get("path", "")
        raw_path = scope.get("raw_path")
        if raw_path:
            path = raw_path.decode("latin-1", errors="ignore")
        return path.startswith("/office/cool/")
