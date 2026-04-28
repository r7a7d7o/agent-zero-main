from __future__ import annotations

import atexit
import asyncio
import base64
import contextlib
import os
import re
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from helpers import files
from helpers.defer import DeferredTask
from helpers.print_style import PrintStyle

from plugins._browser.helpers.config import (
    DEFAULT_HOMEPAGE_KEY,
    build_browser_launch_config,
    get_browser_config,
)
from plugins._browser.helpers.playwright import configure_playwright_env, ensure_playwright_binary


PLUGIN_DIR = Path(__file__).resolve().parents[1]
CONTENT_HELPER_PATH = PLUGIN_DIR / "assets" / "browser-page-content.js"
RUNTIME_DATA_KEY = "_browser_runtime"
DEFAULT_VIEWPORT = {"width": 1024, "height": 768}
CHROME_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")
SCREENCAST_MAX_WIDTH = 4096
SCREENCAST_MAX_HEIGHT = 4096
VIEWPORT_SIZE_TOLERANCE = 4
VIEWPORT_REMOUNT_PAUSE_SECONDS = 0.05

_SPECIAL_SCHEME_RE = re.compile(r"^(?:about|blob|data|file|mailto|tel):", re.I)
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z\d+\-.]*://", re.I)
_LOCAL_HOST_RE = re.compile(
    r"^(?:localhost|\[[0-9a-f:.]+\]|(?:\d{1,3}\.){3}\d{1,3})(?::\d+)?$",
    re.I,
)
_TYPED_HOST_RE = re.compile(
    r"^(?:localhost|\[[0-9a-f:.]+\]|(?:\d{1,3}\.){3}\d{1,3}|"
    r"(?:[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?\.)+[a-z\d-]{2,63})(?::\d+)?$",
    re.I,
)
_SAFE_CONTEXT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _nudged_viewport(viewport: dict[str, int]) -> dict[str, int]:
    width = int(viewport["width"])
    height = int(viewport["height"])
    if width < 4096:
        return {"width": width + 1, "height": height}
    if width > 320:
        return {"width": width - 1, "height": height}
    if height < 4096:
        return {"width": width, "height": height + 1}
    return {"width": width, "height": height - 1}


def normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Browser navigation requires a non-empty URL.")

    def with_trailing_path(url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme in {"http", "https"} and not parts.path:
            return urlunsplit((parts.scheme, parts.netloc, "/", parts.query, parts.fragment))
        return urlunsplit(parts)

    try:
        host = re.split(r"[/?#]", raw, 1)[0] or ""
        if (
            not _URL_SCHEME_RE.match(raw)
            and not _SPECIAL_SCHEME_RE.match(raw)
            and not raw.startswith(("/", "?", "#", "."))
            and not re.search(r"\s", raw)
            and _TYPED_HOST_RE.match(host)
        ):
            protocol = "http://" if _LOCAL_HOST_RE.match(host) else "https://"
            return with_trailing_path(protocol + raw)

        parts = urlsplit(raw)
        if parts.scheme:
            return with_trailing_path(raw)
    except Exception:
        pass

    return with_trailing_path("https://" + raw)


def _safe_context_id(context_id: str) -> str:
    return _SAFE_CONTEXT_RE.sub("_", str(context_id or "default")).strip("._") or "default"


@dataclass
class BrowserPage:
    id: int
    page: Any


class _BrowserScreencast:
    def __init__(
        self,
        *,
        stream_id: str,
        browser_id: int,
        session: Any,
        mime: str,
    ):
        self.id = stream_id
        self.browser_id = browser_id
        self.session = session
        self.mime = mime
        self.queue = asyncio.Queue(maxsize=1)
        self.stopped = False
        self._ack_tasks: set[asyncio.Task] = set()
        self._expected_width = 0
        self._expected_height = 0

    async def start(
        self,
        *,
        quality: int,
        every_nth_frame: int,
        viewport: dict[str, int],
    ) -> None:
        self.session.on("Page.screencastFrame", self._on_frame)
        width = max(320, min(4096, int(viewport.get("width") or DEFAULT_VIEWPORT["width"])))
        height = max(200, min(4096, int(viewport.get("height") or DEFAULT_VIEWPORT["height"])))
        self._expected_width = width
        self._expected_height = height
        with contextlib.suppress(Exception):
            await self.session.send("Page.enable")
        await self._apply_cdp_viewport_with_remount({"width": width, "height": height})
        await self.session.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": max(20, min(95, int(quality))),
                "maxWidth": SCREENCAST_MAX_WIDTH,
                "maxHeight": SCREENCAST_MAX_HEIGHT,
                "everyNthFrame": max(1, int(every_nth_frame)),
            },
        )

    async def _apply_cdp_viewport_with_remount(self, viewport: dict[str, int]) -> None:
        await self._apply_cdp_viewport(viewport)
        await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)
        await self._apply_cdp_viewport(_nudged_viewport(viewport))
        await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)
        await self._apply_cdp_viewport(viewport)
        await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)

    async def _apply_cdp_viewport(self, viewport: dict[str, int]) -> None:
        width = max(320, min(4096, int(viewport.get("width") or DEFAULT_VIEWPORT["width"])))
        height = max(200, min(4096, int(viewport.get("height") or DEFAULT_VIEWPORT["height"])))
        await self.session.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
                "dontSetVisibleSize": True,
            },
        )
        with contextlib.suppress(Exception):
            await self.session.send(
                "Emulation.setVisibleSize",
                {
                    "width": width,
                    "height": height,
                },
            )

    async def next_frame(self, timeout: float = 1.0) -> dict[str, Any]:
        frame = await asyncio.wait_for(self.queue.get(), timeout=max(0.1, float(timeout)))
        if frame is None:
            raise RuntimeError("Browser screencast stopped.")
        return frame

    async def pop_frame(self) -> dict[str, Any] | None:
        try:
            frame = self.queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if frame is None:
            raise RuntimeError("Browser screencast stopped.")
        return frame

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self._drop_queued_frames()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)
        with contextlib.suppress(Exception):
            await self.session.send("Page.stopScreencast")
        for task in list(self._ack_tasks):
            task.cancel()
        if self._ack_tasks:
            await asyncio.gather(*self._ack_tasks, return_exceptions=True)
            self._ack_tasks.clear()
        with contextlib.suppress(Exception):
            await self.session.detach()

    def _on_frame(self, params: dict[str, Any]) -> None:
        if self.stopped:
            return
        task = asyncio.create_task(self._handle_frame(params or {}))
        self._ack_tasks.add(task)
        task.add_done_callback(self._ack_tasks.discard)

    async def _handle_frame(self, params: dict[str, Any]) -> None:
        try:
            data = params.get("data") or ""
            if data:
                metadata = dict(params.get("metadata") or {})
                size = self._jpeg_size(data)
                if size:
                    metadata["jpegWidth"], metadata["jpegHeight"] = size
                metadata["expectedWidth"] = self._expected_width
                metadata["expectedHeight"] = self._expected_height
                self._queue_latest(
                    {
                        "browser_id": self.browser_id,
                        "mime": self.mime,
                        "image": data,
                        "metadata": metadata,
                    }
                )
        finally:
            session_id = params.get("sessionId")
            if session_id is not None and not self.stopped:
                with contextlib.suppress(Exception):
                    await self.session.send(
                        "Page.screencastFrameAck",
                        {"sessionId": int(session_id)},
                    )

    def _queue_latest(self, frame: dict[str, Any]) -> None:
        self._drop_queued_frames()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(frame)

    @staticmethod
    def _jpeg_size(data: str) -> tuple[int, int] | None:
        try:
            raw = base64.b64decode(data, validate=False)
        except Exception:
            return None
        if len(raw) < 10 or raw[:2] != b"\xff\xd8":
            return None
        index = 2
        standalone_markers = {0x01, *range(0xD0, 0xD8)}
        size_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while index < len(raw) - 9:
            if raw[index] != 0xFF:
                index += 1
                continue
            while index < len(raw) and raw[index] == 0xFF:
                index += 1
            if index >= len(raw):
                return None
            marker = raw[index]
            index += 1
            if marker in standalone_markers:
                continue
            if index + 2 > len(raw):
                return None
            segment_length = int.from_bytes(raw[index : index + 2], "big")
            if segment_length < 2 or index + segment_length > len(raw):
                return None
            if marker in size_markers and segment_length >= 7:
                height = int.from_bytes(raw[index + 3 : index + 5], "big")
                width = int.from_bytes(raw[index + 5 : index + 7], "big")
                return width, height
            index += segment_length
        return None

    def _drop_queued_frames(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return


class BrowserRuntime:
    def __init__(self, context_id: str):
        self.context_id = str(context_id)
        self._core = _BrowserRuntimeCore(self.context_id)
        self._worker = DeferredTask(thread_name=f"BrowserRuntime-{self.context_id}")
        self._closed = False

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._closed and method != "close":
            raise RuntimeError("Browser runtime is closed.")

        async def runner():
            fn = getattr(self._core, method)
            return await fn(*args, **kwargs)

        return await self._worker.execute_inside(runner)

    async def close(self, delete_profile: bool = False) -> None:
        if self._closed:
            return
        try:
            await self.call("close", delete_profile=delete_profile)
        finally:
            self._closed = True
            self._worker.kill(terminate_thread=True)


class _BrowserRuntimeCore:
    def __init__(self, context_id: str):
        self.context_id = context_id
        self.safe_context_id = _safe_context_id(context_id)
        self.playwright = None
        self.context = None
        self.pages: dict[int, BrowserPage] = {}
        self.screencasts: dict[str, _BrowserScreencast] = {}
        self.next_browser_id = 1
        self.last_interacted_browser_id: int | None = None
        self._content_helper_source: str | None = None
        self._start_lock: asyncio.Lock | None = None

    @property
    def profile_dir(self) -> Path:
        return Path(files.get_abs_path("tmp/browser/sessions", self.safe_context_id))

    @property
    def downloads_dir(self) -> Path:
        return Path(files.get_abs_path("usr/downloads/browser"))

    async def ensure_started(self) -> None:
        if self.context:
            return

        if self._start_lock is None:
            self._start_lock = asyncio.Lock()

        async with self._start_lock:
            if self.context:
                return
            await self._start()

    async def _start(self) -> None:
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._release_orphaned_profile_singleton()
        browser_config = get_browser_config()
        launch_config = build_browser_launch_config(browser_config)
        configure_playwright_env()
        browser_binary = ensure_playwright_binary(
            full_browser=launch_config["requires_full_browser"]
        )

        self.playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": True,
            "accept_downloads": True,
            "downloads_path": str(self.downloads_dir),
            "viewport": DEFAULT_VIEWPORT,
            "screen": DEFAULT_VIEWPORT,
            "no_viewport": False,
            "args": launch_config["args"],
        }
        if launch_config["channel"]:
            launch_kwargs["channel"] = launch_config["channel"]
        else:
            launch_kwargs["executable_path"] = str(browser_binary)
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception:
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            raise
        self.context.set_default_timeout(30000)
        self.context.set_default_navigation_timeout(30000)
        await self.context.add_init_script(self._shadow_dom_script())
        await self.context.add_init_script(path=str(CONTENT_HELPER_PATH))

        for page in list(self.context.pages):
            if page.url == "about:blank":
                try:
                    await page.close()
                except Exception:
                    pass
                continue
            self._register_page(page)

    def _release_orphaned_profile_singleton(self) -> None:
        lock_path = self.profile_dir / "SingletonLock"
        owner_pid = self._profile_singleton_owner_pid(lock_path)
        if owner_pid and self._process_owns_profile(owner_pid):
            PrintStyle.warning(
                f"Stopping orphaned Chromium process {owner_pid} for Browser profile {self.safe_context_id}."
            )
            self._terminate_process(owner_pid)

        for name in CHROME_SINGLETON_FILES:
            singleton_path = self.profile_dir / name
            try:
                if singleton_path.exists() or singleton_path.is_symlink():
                    singleton_path.unlink()
            except OSError as exc:
                PrintStyle.warning(f"Could not remove stale Browser profile lock {singleton_path}: {exc}")

    @staticmethod
    def _profile_singleton_owner_pid(lock_path: Path) -> int | None:
        try:
            target = os.readlink(lock_path)
        except OSError:
            return None
        raw_pid = target.rsplit("-", 1)[-1]
        if not raw_pid.isdigit():
            return None
        return int(raw_pid)

    def _process_owns_profile(self, pid: int) -> bool:
        cmdline_path = Path("/proc") / str(pid) / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            return False
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        return "chrome" in cmdline.lower() and str(self.profile_dir) in cmdline

    @staticmethod
    def _terminate_process(pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as exc:
            PrintStyle.warning(f"Could not stop orphaned Chromium process {pid}: {exc}")
            return

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not Path("/proc", str(pid)).exists():
                return
            time.sleep(0.1)

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            PrintStyle.warning(f"Could not force-stop orphaned Chromium process {pid}: {exc}")

    async def open(self, url: str = "") -> dict[str, Any]:
        await self.ensure_started()
        page = await self.context.new_page()
        browser_page = self._register_page(page)
        self.last_interacted_browser_id = browser_page.id
        target_url = self._initial_url(url)
        if target_url and target_url != "about:blank":
            await self._goto(page, normalize_url(target_url))
        else:
            await self._settle(page)
        return {"id": browser_page.id, "state": await self._state(browser_page.id)}

    def _initial_url(self, url: str = "") -> str:
        raw_url = str(url or "").strip()
        if raw_url:
            return raw_url
        return str(get_browser_config().get(DEFAULT_HOMEPAGE_KEY) or "about:blank").strip() or "about:blank"

    async def list(self) -> dict[str, Any]:
        await self.ensure_started()
        return {
            "browsers": [await self._state(browser_id) for browser_id in sorted(self.pages)],
            "last_interacted_browser_id": self.last_interacted_browser_id,
        }

    async def state(self, browser_id: int | str | None = None) -> dict[str, Any]:
        await self.ensure_started()
        return await self._state(self._resolve_browser_id(browser_id))

    async def navigate(self, browser_id: int | str | None, url: str) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await self._goto(page, normalize_url(url))
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def back(self, browser_id: int | str | None = None) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await page.go_back(wait_until="domcontentloaded", timeout=10000)
        await self._settle(page)
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def forward(self, browser_id: int | str | None = None) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await page.go_forward(wait_until="domcontentloaded", timeout=10000)
        await self._settle(page)
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def reload(self, browser_id: int | str | None = None) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await page.reload(wait_until="domcontentloaded", timeout=15000)
        await self._settle(page)
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def content(
        self,
        browser_id: int | str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await self._ensure_content_helper(page)
        result = await page.evaluate(
            "(payload) => globalThis.__spaceBrowserPageContent__.capture(payload || null)",
            payload or None,
        )
        self.last_interacted_browser_id = resolved_id
        return result or {}

    async def detail(self, browser_id: int | str | None, reference_id: int | str) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await self._ensure_content_helper(page)
        result = await page.evaluate(
            "(ref) => globalThis.__spaceBrowserPageContent__.detail(ref)",
            reference_id,
        )
        self.last_interacted_browser_id = resolved_id
        return result or {}

    async def annotation_target(
        self,
        browser_id: int | str | None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await self._ensure_content_helper(page)
        result = await page.evaluate(
            "(payload) => globalThis.__spaceBrowserPageContent__.annotate(payload || null)",
            payload or None,
        )
        self.last_interacted_browser_id = resolved_id
        return result or {}

    async def evaluate(self, browser_id: int | str | None, script: str) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        result = await page.evaluate(str(script or "undefined"))
        self.last_interacted_browser_id = resolved_id
        return {"result": result, "state": await self._state(resolved_id)}

    async def click(self, browser_id: int | str | None, reference_id: int | str) -> dict[str, Any]:
        return await self._reference_action("click", browser_id, reference_id)

    async def submit(self, browser_id: int | str | None, reference_id: int | str) -> dict[str, Any]:
        return await self._reference_action("submit", browser_id, reference_id)

    async def scroll(self, browser_id: int | str | None, reference_id: int | str) -> dict[str, Any]:
        return await self._reference_action("scroll", browser_id, reference_id)

    async def type(
        self,
        browser_id: int | str | None,
        reference_id: int | str,
        text: str,
    ) -> dict[str, Any]:
        return await self._reference_action("type", browser_id, reference_id, text)

    async def type_submit(
        self,
        browser_id: int | str | None,
        reference_id: int | str,
        text: str,
    ) -> dict[str, Any]:
        return await self._reference_action("typeSubmit", browser_id, reference_id, text)

    async def close_browser(self, browser_id: int | str | None = None) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        await self._stop_screencasts_for_browser(resolved_id)
        page = self._page(resolved_id)
        await page.close()
        self.pages.pop(resolved_id, None)
        if self.last_interacted_browser_id == resolved_id:
            self.last_interacted_browser_id = next(iter(sorted(self.pages)), None)
        return await self.list()

    async def close_all_browsers(self) -> dict[str, Any]:
        await self.ensure_started()
        await self._stop_all_screencasts()
        for browser_id in list(self.pages):
            try:
                await self.pages[browser_id].page.close()
            except Exception:
                pass
        self.pages.clear()
        self.last_interacted_browser_id = None
        return {"browsers": [], "last_interacted_browser_id": None}

    async def screenshot(
        self,
        browser_id: int | str | None = None,
        *,
        quality: int = 70,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        image = await page.screenshot(type="jpeg", quality=max(20, min(95, int(quality))))
        return {
            "browser_id": resolved_id,
            "mime": "image/jpeg",
            "image": base64.b64encode(image).decode("ascii"),
            "state": await self._state(resolved_id),
        }

    async def start_screencast(
        self,
        browser_id: int | str | None = None,
        *,
        quality: int = 78,
        every_nth_frame: int = 1,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        stream_id = uuid.uuid4().hex
        session = await self.context.new_cdp_session(page)
        screencast = _BrowserScreencast(
            stream_id=stream_id,
            browser_id=resolved_id,
            session=session,
            mime="image/jpeg",
        )
        self.screencasts[stream_id] = screencast
        try:
            await screencast.start(
                quality=quality,
                every_nth_frame=every_nth_frame,
                viewport=page.viewport_size or DEFAULT_VIEWPORT,
            )
        except Exception:
            self.screencasts.pop(stream_id, None)
            await screencast.stop()
            raise
        self.last_interacted_browser_id = resolved_id
        return {
            "stream_id": stream_id,
            "browser_id": resolved_id,
            "state": await self._state(resolved_id),
        }

    async def read_screencast_frame(
        self,
        stream_id: str,
        *,
        timeout: float = 1.0,
    ) -> dict[str, Any]:
        screencast = self.screencasts.get(str(stream_id or ""))
        if not screencast:
            raise KeyError("Browser screencast is not active.")
        return await screencast.next_frame(timeout=timeout)

    async def pop_screencast_frame(self, stream_id: str) -> dict[str, Any] | None:
        screencast = self.screencasts.get(str(stream_id or ""))
        if not screencast:
            raise KeyError("Browser screencast is not active.")
        return await screencast.pop_frame()

    async def stop_screencast(self, stream_id: str) -> None:
        screencast = self.screencasts.pop(str(stream_id or ""), None)
        if screencast:
            await screencast.stop()

    async def set_viewport(
        self,
        browser_id: int | str | None,
        width: int,
        height: int,
        restart_screencast: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        viewport = {
            "width": max(320, min(4096, int(width or DEFAULT_VIEWPORT["width"]))),
            "height": max(200, min(4096, int(height or DEFAULT_VIEWPORT["height"]))),
        }
        current_viewport = page.viewport_size or {}
        changed = (
            abs(int(current_viewport.get("width") or 0) - viewport["width"])
            > VIEWPORT_SIZE_TOLERANCE
            or abs(int(current_viewport.get("height") or 0) - viewport["height"])
            > VIEWPORT_SIZE_TOLERANCE
        )
        should_remount_viewport = changed or restart_screencast
        if should_remount_viewport:
            await self._stop_screencasts_for_browser(resolved_id)
        if changed:
            await self._apply_viewport_with_remount(page, viewport)
        elif restart_screencast:
            await self._remount_viewport(page, viewport)
        if should_remount_viewport:
            await self._settle(page, short=True)
        self.last_interacted_browser_id = resolved_id
        return {"state": await self._state(resolved_id), "viewport": viewport}

    async def _apply_viewport_with_remount(self, page: Any, viewport: dict[str, int]) -> None:
        await page.set_viewport_size(viewport)
        await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)
        await self._remount_viewport(page, viewport)

    async def _remount_viewport(self, page: Any, viewport: dict[str, int]) -> None:
        nudged_viewport = self._nudged_viewport(viewport)
        await page.set_viewport_size(nudged_viewport)
        await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)
        await page.set_viewport_size(viewport)

    @staticmethod
    def _nudged_viewport(viewport: dict[str, int]) -> dict[str, int]:
        return _nudged_viewport(viewport)

    async def mouse(
        self,
        browser_id: int | str | None,
        event_type: str,
        x: float,
        y: float,
        button: str = "left",
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        event_type = str(event_type or "click").lower()
        if event_type == "move":
            await page.mouse.move(float(x), float(y))
        elif event_type == "down":
            await page.mouse.down(button=button)
        elif event_type == "up":
            await page.mouse.up(button=button)
        else:
            await page.mouse.click(float(x), float(y), button=button)
            await self._settle(page, short=True)
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def wheel(
        self,
        browser_id: int | str | None,
        x: float,
        y: float,
        delta_x: float = 0,
        delta_y: float = 0,
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await page.mouse.move(float(x), float(y))
        await page.mouse.wheel(float(delta_x), float(delta_y))
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def keyboard(
        self,
        browser_id: int | str | None,
        *,
        key: str = "",
        text: str = "",
    ) -> dict[str, Any]:
        await self.ensure_started()
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        if text:
            await page.keyboard.type(str(text))
        elif key:
            await page.keyboard.press(str(key))
        await self._settle(page, short=True)
        self.last_interacted_browser_id = resolved_id
        return await self._state(resolved_id)

    async def close(self, delete_profile: bool = False) -> None:
        await self._stop_all_screencasts()
        for browser_id in list(self.pages):
            try:
                await self.pages[browser_id].page.close()
            except Exception:
                pass
        self.pages.clear()
        if self.context:
            try:
                await self.context.close()
            except Exception as exc:
                PrintStyle.warning(f"Browser context close failed: {exc}")
            self.context = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as exc:
                PrintStyle.warning(f"Playwright stop failed: {exc}")
            self.playwright = None
        self.last_interacted_browser_id = None
        if delete_profile:
            shutil.rmtree(self.profile_dir, ignore_errors=True)

    async def _reference_action(
        self,
        helper_method: str,
        browser_id: int | str | None,
        reference_id: int | str,
        text: str | None = None,
    ) -> dict[str, Any]:
        resolved_id = self._resolve_browser_id(browser_id)
        page = self._page(resolved_id)
        await self._ensure_content_helper(page)
        if text is None:
            action = await page.evaluate(
                "(args) => globalThis.__spaceBrowserPageContent__[args.method](args.ref)",
                {"method": helper_method, "ref": reference_id},
            )
        else:
            action = await page.evaluate(
                "(args) => globalThis.__spaceBrowserPageContent__[args.method](args.ref, args.text)",
                {"method": helper_method, "ref": reference_id, "text": text},
            )
        await self._settle(page, short=False)
        self.last_interacted_browser_id = resolved_id
        return {"action": action or {}, "state": await self._state(resolved_id)}

    async def _goto(self, page: Any, url: str) -> None:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeoutError:
            PrintStyle.warning(f"Browser navigation timed out after DOM handoff: {url}")
        except PlaywrightError as exc:
            PrintStyle.warning(f"Browser navigation showed a native error page for {url}: {exc}")
        await self._settle(page)

    async def _settle(self, page: Any, short: bool = False) -> None:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=1000 if short else 5000,
            )
        except (PlaywrightError, PlaywrightTimeoutError):
            pass
        await asyncio.sleep(0.1 if short else 0.35)

    async def _state(self, browser_id: int) -> dict[str, Any]:
        browser_page = self.pages.get(int(browser_id))
        if not browser_page:
            raise KeyError(f"Browser {browser_id} is not open.")
        page = browser_page.page
        try:
            title = await page.title()
        except Exception:
            title = ""
        try:
            history_length = await page.evaluate("() => globalThis.history?.length || 0")
        except Exception:
            history_length = 0
        return {
            "id": browser_page.id,
            "context_id": self.context_id,
            "currentUrl": page.url,
            "title": title,
            "canGoBack": bool(history_length and int(history_length) > 1),
            "canGoForward": False,
            "loading": False,
        }

    def _register_page(self, page: Any) -> BrowserPage:
        existing = self._browser_id_for_page(page)
        if existing is not None:
            return self.pages[existing]
        browser_id = self.next_browser_id
        self.next_browser_id += 1
        browser_page = BrowserPage(id=browser_id, page=page)
        self.pages[browser_id] = browser_page

        def on_close() -> None:
            self.pages.pop(browser_id, None)

        page.on("close", on_close)
        return browser_page

    def _browser_id_for_page(self, page: Any) -> int | None:
        for browser_id, browser_page in self.pages.items():
            if browser_page.page == page:
                return browser_id
        return None

    def _resolve_browser_id(self, browser_id: int | str | None = None) -> int:
        if browser_id is None or str(browser_id).strip() == "":
            if self.last_interacted_browser_id in self.pages:
                return int(self.last_interacted_browser_id)
            if self.pages:
                return sorted(self.pages)[0]
            raise KeyError("No browser is open. Use action=open first.")
        value = str(browser_id).strip()
        if value.startswith("browser-"):
            value = value.split("-", 1)[1]
        resolved = int(value)
        if resolved not in self.pages:
            raise KeyError(f"Browser {resolved} is not open.")
        return resolved

    def _page(self, browser_id: int) -> Any:
        return self.pages[int(browser_id)].page

    async def _stop_screencasts_for_browser(self, browser_id: int) -> None:
        stream_ids = [
            stream_id
            for stream_id, screencast in self.screencasts.items()
            if screencast.browser_id == int(browser_id)
        ]
        for stream_id in stream_ids:
            await self.stop_screencast(stream_id)

    async def _stop_all_screencasts(self) -> None:
        for stream_id in list(self.screencasts):
            await self.stop_screencast(stream_id)

    async def _ensure_content_helper(self, page: Any) -> None:
        has_helper = await page.evaluate(
            "() => Boolean(globalThis.__spaceBrowserPageContent__?.capture && globalThis.__spaceBrowserPageContent__?.annotate)"
        )
        if has_helper:
            return
        if self._content_helper_source is None:
            self._content_helper_source = CONTENT_HELPER_PATH.read_text(encoding="utf-8")
        await page.evaluate(self._content_helper_source)

    @staticmethod
    def _shadow_dom_script() -> str:
        return """
(() => {
  const original = Element.prototype.attachShadow;
  if (original && !original.__a0BrowserOpenShadowPatch) {
    const patched = function attachShadow(options) {
      return original.call(this, { ...(options || {}), mode: "open" });
    };
    patched.__a0BrowserOpenShadowPatch = true;
    Element.prototype.attachShadow = patched;
  }
})();
"""


_runtimes: dict[str, BrowserRuntime] = {}
_runtime_lock = threading.RLock()


async def get_runtime(context_id: str, *, create: bool = True) -> BrowserRuntime | None:
    context_id = str(context_id or "").strip()
    if not context_id:
        raise ValueError("context_id is required")
    with _runtime_lock:
        runtime = _runtimes.get(context_id)
        if runtime is None and create:
            runtime = BrowserRuntime(context_id)
            _runtimes[context_id] = runtime
        return runtime


async def close_runtime(context_id: str, *, delete_profile: bool = True) -> None:
    context_id = str(context_id or "").strip()
    if not context_id:
        return
    with _runtime_lock:
        runtime = _runtimes.pop(context_id, None)
    if runtime:
        await runtime.close(delete_profile=delete_profile)


def close_runtime_sync(context_id: str, *, delete_profile: bool = True) -> None:
    task = DeferredTask(thread_name="BrowserCleanup")
    task.start_task(close_runtime, context_id, delete_profile=delete_profile)
    try:
        task.result_sync(timeout=30)
    finally:
        task.kill(terminate_thread=True)


async def close_all_runtimes(*, delete_profiles: bool = False) -> None:
    with _runtime_lock:
        runtimes = list(_runtimes.values())
        _runtimes.clear()
    for runtime in runtimes:
        try:
            await runtime.close(delete_profile=delete_profiles)
        except Exception as exc:
            PrintStyle.warning(f"Browser runtime cleanup failed: {exc}")


def close_all_runtimes_sync() -> None:
    task = DeferredTask(thread_name="BrowserCleanupAll")
    task.start_task(close_all_runtimes, delete_profiles=False)
    try:
        task.result_sync(timeout=30)
    finally:
        task.kill(terminate_thread=True)


def known_context_ids() -> list[str]:
    with _runtime_lock:
        return sorted(_runtimes)


async def list_runtime_sessions() -> list[dict[str, Any]]:
    with _runtime_lock:
        runtimes = list(_runtimes.items())

    sessions: list[dict[str, Any]] = []
    for context_id, runtime in runtimes:
        try:
            listing = await runtime.call("list")
        except Exception as exc:
            PrintStyle.warning(f"Browser runtime list failed for context {context_id}: {exc}")
            continue
        sessions.append(
            {
                "context_id": context_id,
                "browsers": listing.get("browsers") or [],
                "last_interacted_browser_id": listing.get("last_interacted_browser_id"),
            }
        )
    return sessions


atexit.register(close_all_runtimes_sync)
