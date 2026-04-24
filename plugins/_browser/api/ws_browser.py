from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from agent import AgentContext
from helpers.ws import WsHandler
from helpers.ws_manager import WsResult
from plugins._browser.helpers.runtime import get_runtime


class WsBrowser(WsHandler):
    _streams: ClassVar[dict[tuple[str, str], asyncio.Task[None]]] = {}

    async def on_disconnect(self, sid: str) -> None:
        for key in [key for key in self._streams if key[0] == sid]:
            task = self._streams.pop(key)
            task.cancel()

    async def process(
        self,
        event: str,
        data: dict[str, Any],
        sid: str,
    ) -> dict[str, Any] | WsResult | None:
        if not event.startswith("browser_"):
            return None

        if event == "browser_viewer_subscribe":
            return await self._subscribe(data, sid)
        if event == "browser_viewer_unsubscribe":
            return self._unsubscribe(data, sid)
        if event == "browser_viewer_command":
            return await self._command(data, sid)
        if event == "browser_viewer_input":
            return await self._input(data, sid)

        return WsResult.error(
            code="UNKNOWN_BROWSER_EVENT",
            message=f"Unknown browser event: {event}",
            correlation_id=data.get("correlationId"),
        )

    async def _subscribe(self, data: dict[str, Any], sid: str) -> dict[str, Any] | WsResult:
        context_id = self._context_id(data)
        if not context_id:
            return self._error("MISSING_CONTEXT", "context_id is required", data)
        if not AgentContext.get(context_id):
            return self._error("CONTEXT_NOT_FOUND", f"Context '{context_id}' was not found", data)

        runtime = await get_runtime(context_id)
        listing = await runtime.call("list")
        browsers = listing.get("browsers") or []
        if not browsers:
            opened = await runtime.call("open", "about:blank")
            listing = await runtime.call("list")
            browsers = listing.get("browsers") or []
            if opened.get("id"):
                listing["last_interacted_browser_id"] = opened.get("id")
        active_id = data.get("browser_id") or listing.get("last_interacted_browser_id")
        if not active_id and browsers:
            active_id = browsers[0].get("id")

        stream_key = (sid, context_id)
        existing = self._streams.pop(stream_key, None)
        if existing:
            existing.cancel()
        self._streams[stream_key] = asyncio.create_task(
            self._stream_frames(sid, context_id, active_id)
        )

        return {
            "context_id": context_id,
            "active_browser_id": active_id,
            "browsers": browsers,
        }

    def _unsubscribe(self, data: dict[str, Any], sid: str) -> dict[str, Any] | WsResult:
        context_id = self._context_id(data)
        if not context_id:
            return self._error("MISSING_CONTEXT", "context_id is required", data)
        task = self._streams.pop((sid, context_id), None)
        if task:
            task.cancel()
        return {"context_id": context_id, "unsubscribed": True}

    async def _command(self, data: dict[str, Any], sid: str) -> dict[str, Any] | WsResult:
        context_id = self._context_id(data)
        if not context_id:
            return self._error("MISSING_CONTEXT", "context_id is required", data)
        runtime = await get_runtime(context_id)
        command = str(data.get("command") or "").strip().lower().replace("-", "_")
        browser_id = data.get("browser_id")

        try:
            if command == "open":
                result = await runtime.call("open", data.get("url") or "about:blank")
            elif command == "navigate":
                result = await runtime.call("navigate", browser_id, data.get("url") or "")
            elif command == "back":
                result = await runtime.call("back", browser_id)
            elif command == "forward":
                result = await runtime.call("forward", browser_id)
            elif command == "reload":
                result = await runtime.call("reload", browser_id)
            elif command == "close":
                result = await runtime.call("close_browser", browser_id)
            elif command == "list":
                result = await runtime.call("list")
            else:
                return self._error("UNKNOWN_COMMAND", f"Unknown browser command: {command}", data)
        except Exception as exc:
            return self._error("COMMAND_FAILED", str(exc), data)

        listing = await runtime.call("list")
        last_interacted_browser_id = listing.get("last_interacted_browser_id")
        await self.emit_to(
            sid,
            "browser_viewer_state",
            {
                "context_id": context_id,
                "result": result,
                "browsers": listing.get("browsers") or [],
                "last_interacted_browser_id": last_interacted_browser_id,
            },
            correlation_id=data.get("correlationId"),
        )
        return {
            "result": result,
            "browsers": listing.get("browsers") or [],
            "last_interacted_browser_id": last_interacted_browser_id,
        }

    async def _input(self, data: dict[str, Any], sid: str) -> dict[str, Any] | WsResult:
        context_id = self._context_id(data)
        if not context_id:
            return self._error("MISSING_CONTEXT", "context_id is required", data)
        runtime = await get_runtime(context_id, create=False)
        if not runtime:
            return self._error("NO_BROWSER_RUNTIME", "No browser runtime exists for this context", data)

        input_type = str(data.get("input_type") or "").strip().lower()
        browser_id = data.get("browser_id")
        try:
            if input_type == "mouse":
                result = await runtime.call(
                    "mouse",
                    browser_id,
                    data.get("event_type") or "click",
                    float(data.get("x") or 0),
                    float(data.get("y") or 0),
                    data.get("button") or "left",
                )
            elif input_type == "keyboard":
                result = await runtime.call(
                    "keyboard",
                    browser_id,
                    key=str(data.get("key") or ""),
                    text=str(data.get("text") or ""),
                )
            elif input_type == "viewport":
                result = await runtime.call(
                    "set_viewport",
                    browser_id,
                    int(data.get("width") or 0),
                    int(data.get("height") or 0),
                )
            elif input_type == "wheel":
                result = await runtime.call(
                    "wheel",
                    browser_id,
                    float(data.get("x") or 0),
                    float(data.get("y") or 0),
                    float(data.get("delta_x") or 0),
                    float(data.get("delta_y") or 0),
                )
            else:
                return self._error("UNKNOWN_INPUT", f"Unknown browser input: {input_type}", data)
        except Exception as exc:
            return self._error("INPUT_FAILED", str(exc), data)

        return {"state": result}

    async def _stream_frames(
        self,
        sid: str,
        context_id: str,
        browser_id: int | str | None,
    ) -> None:
        while True:
            try:
                runtime = await get_runtime(context_id, create=False)
                if runtime:
                    listing = await runtime.call("list")
                    browsers = listing.get("browsers") or []
                    browser_ids = {str(browser.get("id")) for browser in browsers}
                    requested_id = str(browser_id or "") if browser_id else ""
                    active_id = (
                        browser_id
                        if requested_id and requested_id in browser_ids
                        else listing.get("last_interacted_browser_id")
                    )
                    if active_id and str(active_id) not in browser_ids:
                        active_id = None
                    if not active_id and browsers:
                        active_id = browsers[0].get("id")
                    if active_id:
                        frame = await runtime.call("screenshot", active_id)
                        frame["context_id"] = context_id
                        frame["browsers"] = browsers
                        await self.emit_to(sid, "browser_viewer_frame", frame)
                    else:
                        await self.emit_to(
                            sid,
                            "browser_viewer_frame",
                            {
                                "context_id": context_id,
                                "browser_id": None,
                                "browsers": browsers,
                                "image": "",
                                "mime": "",
                                "state": None,
                            },
                        )
                await asyncio.sleep(0.75)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.5)

    @staticmethod
    def _context_id(data: dict[str, Any]) -> str:
        return str(data.get("context_id") or data.get("context") or "").strip()

    @staticmethod
    def _error(code: str, message: str, data: dict[str, Any]) -> WsResult:
        return WsResult.error(
            code=code,
            message=message,
            correlation_id=data.get("correlationId"),
        )
