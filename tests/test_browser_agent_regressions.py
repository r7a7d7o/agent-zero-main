import asyncio
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _TestAgentContext:
    @staticmethod
    def get(context_id):
        return None


class _TestResponse(SimpleNamespace):
    def __init__(self, message="", break_loop=False, **kwargs):
        super().__init__(message=message, break_loop=break_loop, **kwargs)


class _TestTool:
    def __init__(
        self,
        agent=None,
        name="",
        method=None,
        args=None,
        message="",
        loop_data=None,
        **kwargs,
    ):
        self.agent = agent
        self.name = name
        self.method = method
        self.args = args or {}
        self.message = message
        self.loop_data = loop_data


class _TestWsHandler:
    def __init__(self, *args, **kwargs):
        self.emitted = []

    async def emit_to(self, sid, event, data, correlation_id=None):
        self.emitted.append((sid, event, data, correlation_id))


class _TestWsResult(dict):
    @staticmethod
    def error(code="", message="", correlation_id=None):
        return _TestWsResult(
            {
                "ok": False,
                "code": code,
                "error": message,
                "correlation_id": correlation_id,
            }
        )


sys.modules.setdefault("agent", SimpleNamespace(AgentContext=_TestAgentContext))
sys.modules.setdefault("helpers.tool", SimpleNamespace(Response=_TestResponse, Tool=_TestTool))
sys.modules.setdefault("helpers.ws", SimpleNamespace(WsHandler=_TestWsHandler))
sys.modules.setdefault("helpers.ws_manager", SimpleNamespace(WsResult=_TestWsResult))
_model_config_stub = ModuleType("plugins._model_config.helpers.model_config")
_model_config_stub.get_presets = lambda: []
_model_config_stub.get_preset_by_name = lambda name: None
_model_config_stub.get_chat_model_config = lambda agent=None: {}
sys.modules.setdefault("plugins._model_config.helpers.model_config", _model_config_stub)


@pytest.fixture
def anyio_backend():
    return "asyncio"

from plugins._browser.helpers.config import (
    build_browser_launch_config,
    get_browser_main_model_summary,
    get_browser_model_preset_options,
    normalize_browser_config,
    resolve_browser_model_selection,
)
from plugins._browser.helpers.extension_manager import (
    _build_web_store_download_url,
    _crx_zip_payload,
    _detect_chrome_prodversion,
    _normalize_chrome_prodversion,
    get_extensions_root,
    parse_chrome_web_store_extension_id,
)
import plugins._browser.helpers.extension_manager as browser_extension_manager_module
from plugins._browser.helpers.runtime import (
    _BrowserRuntimeCore,
    _BrowserScreencast,
    normalize_url,
)
import plugins._browser.helpers.runtime as browser_runtime_module
from plugins._browser.helpers.playwright import (
    get_playwright_binary,
    get_playwright_cache_dir,
)
import plugins._browser.helpers.playwright as browser_playwright_module
import plugins._browser.hooks as browser_hooks_module
import plugins._browser.tools.browser as browser_tool_module
import plugins._browser.api.ws_browser as ws_browser_module


def test_browser_url_normalization_matches_address_bar_hosts():
    assert normalize_url("localhost:3000") == "http://localhost:3000/"
    assert normalize_url("127.0.0.1:8000/path") == "http://127.0.0.1:8000/path"
    assert normalize_url("novinky.cz") == "https://novinky.cz/"
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("about:blank") == "about:blank"


def test_browser_config_normalizes_extension_paths(tmp_path):
    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()

    config = normalize_browser_config(
        {
            "extension_paths": [str(extension_dir), "", "  ", str(extension_dir)],
        }
    )

    assert config == {
        "extension_paths": [str(extension_dir)],
        "default_homepage": "about:blank",
        "autofocus_active_page": True,
        "model_preset": "",
    }


def test_browser_config_normalizes_model_preset():
    assert normalize_browser_config({"model_preset": "  Research  "})["model_preset"] == "Research"
    assert "model" not in normalize_browser_config({"model": "main"})


def test_browser_model_selection_uses_presets(monkeypatch):
    import plugins._browser.helpers.config as browser_config_module
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(
        browser_config_module,
        "get_browser_config",
        lambda agent=None: {"model_preset": "Research", "extension_paths": []},
    )
    monkeypatch.setattr(
        model_config,
        "get_preset_by_name",
        lambda name: {
            "name": "Research",
            "chat": {"provider": "openrouter", "name": "example/model"},
        } if name == "Research" else None,
    )

    selection = resolve_browser_model_selection(SimpleNamespace())

    assert selection["source_kind"] == "preset"
    assert selection["config"] == {"provider": "openrouter", "name": "example/model"}


def test_browser_model_selection_falls_back_to_main_for_missing_preset(monkeypatch):
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(model_config, "get_preset_by_name", lambda name: None)
    monkeypatch.setattr(
        model_config,
        "get_chat_model_config",
        lambda agent=None: {"provider": "openrouter", "name": "main/model"},
    )

    selection = resolve_browser_model_selection(SimpleNamespace(), {"model_preset": "Missing"})

    assert selection["source_kind"] == "main"
    assert selection["preset_status"] == "missing"
    assert selection["config"] == {"provider": "openrouter", "name": "main/model"}


def test_browser_model_preset_options_include_missing_selected(monkeypatch):
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(
        model_config,
        "get_presets",
        lambda: [{"name": "Balance", "chat": {"provider": "openrouter", "name": "model"}}],
    )

    options = get_browser_model_preset_options(settings={"model_preset": "Deleted"})

    assert options[-1]["name"] == "Deleted"
    assert options[-1]["missing"] is True


def test_browser_main_model_summary_shows_current_model(monkeypatch):
    from plugins._model_config.helpers import model_config

    monkeypatch.setattr(
        model_config,
        "get_chat_model_config",
        lambda agent=None: {"provider": "openrouter", "name": "example/main"},
    )

    assert get_browser_main_model_summary() == "openrouter / example/main"


def test_browser_launch_config_uses_full_chromium_for_all_sessions(tmp_path):
    default_launch = build_browser_launch_config(
        {
            "extension_paths": [],
        }
    )

    assert default_launch["browser_mode"] == "chromium"
    assert default_launch["channel"] is None
    assert default_launch["requires_full_browser"] is True
    assert not any(arg.startswith("--load-extension=") for arg in default_launch["args"])
    assert "--headless=new" not in default_launch["args"]

    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()

    launch = build_browser_launch_config(
        {
            "extension_paths": [str(extension_dir)],
        }
    )

    assert launch["browser_mode"] == "chromium"
    assert launch["channel"] is None
    assert launch["requires_full_browser"] is True
    assert launch["extensions"]["active"] is True
    assert any(arg.startswith("--load-extension=") for arg in launch["args"])
    assert "--headless=new" not in launch["args"]


def test_browser_playwright_cache_uses_persistent_usr_path(monkeypatch, tmp_path):
    monkeypatch.delenv("A0_BROWSER_PLAYWRIGHT_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        browser_playwright_module.files,
        "get_abs_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    legacy_binary = (
        tmp_path
        / "tmp"
        / "playwright"
        / "chromium-1169"
        / "chrome-linux"
        / "chrome"
    )
    legacy_binary.parent.mkdir(parents=True)
    legacy_binary.write_text("#!/bin/sh\n", encoding="utf-8")

    assert get_playwright_cache_dir() == str(
        tmp_path / "usr" / "plugins" / "_browser" / "playwright"
    )
    assert get_playwright_binary() == legacy_binary


def test_browser_extension_storage_uses_plugin_user_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser_extension_manager_module.files,
        "get_abs_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )

    assert get_extensions_root() == tmp_path / "usr" / "plugins" / "_browser" / "extensions"


def test_browser_extension_manager_parses_web_store_urls():
    extension_id = "a" * 32

    assert parse_chrome_web_store_extension_id(extension_id) == extension_id
    assert (
        parse_chrome_web_store_extension_id(
            f"https://chromewebstore.google.com/detail/example/{extension_id}"
        )
        == extension_id
    )
    assert (
        parse_chrome_web_store_extension_id(
            f"https://chrome.google.com/webstore/detail/example/{extension_id}?hl=en"
        )
        == extension_id
    )


def test_browser_extension_manager_extracts_crx3_zip_payload():
    payload = b"PK\x03\x04zip-payload"
    header = b"metadata"
    crx = b"Cr24" + (3).to_bytes(4, "little") + len(header).to_bytes(4, "little") + header + payload

    assert _crx_zip_payload(crx) == payload


def test_browser_extension_manager_uses_modern_chrome_prodversion(monkeypatch):
    extension_id = "a" * 32

    assert _normalize_chrome_prodversion("Google Chrome 147.0.7727.55") == "147.0.7727.55"
    assert _normalize_chrome_prodversion("Chromium 124") == "124.0.0.0"

    monkeypatch.setenv("A0_BROWSER_EXTENSION_PRODVERSION", "147.0.7727.55")
    assert _detect_chrome_prodversion() == "147.0.7727.55"

    url = _build_web_store_download_url(extension_id, prodversion=_detect_chrome_prodversion())
    assert "prod=chromecrx" in url
    assert "prodversion=147.0.7727.55" in url
    assert "prodversion=120.0.0.0" not in url


def test_browser_extension_menu_exposes_agent_and_url_paths():
    html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html").read_text(
        encoding="utf-8"
    )
    skill = PROJECT_ROOT / "skills" / "a0-browser-ext" / "SKILL.md"

    assert "Create New Extension with A0" in html
    assert "+ Create New with A0" not in html
    assert "Input a Chrome Web Store URL" in html
    assert "My Browser Extensions" not in html
    assert "Browser LLM Preset" in html
    assert "Chrome Extensions" in html
    assert "Installed extensions" in html
    assert "No extensions installed yet." not in html
    assert "Browser Extension Settings" not in html
    assert "<span>Settings</span>" in html
    assert "hasExtensionInstallUrl()" in html
    assert "malicious or buggy extensions" in html
    assert skill.exists()


def test_browser_viewer_allows_slow_extension_startup():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "const BROWSER_SUBSCRIBE_TIMEOUT_MS = 60000;" in js
    assert "const BROWSER_FIRST_INSTALL_TIMEOUT_MS = 300000;" in js
    assert "? BROWSER_FIRST_INSTALL_TIMEOUT_MS" in js
    assert ": BROWSER_SUBSCRIBE_TIMEOUT_MS" in js
    assert "Installing Chromium for the first Browser run" in js


def test_browser_viewer_creates_chat_when_no_context_is_selected():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "async ensureContextId()" in js
    assert "async createChatContextForBrowser()" in js
    assert 'callJsonApi("/chat_create"' in js
    assert "globalThis.setContext(contextId)" in js
    assert "await this.ensureContextId();" in js
    assert "No active chat context is selected." not in js


def test_browser_canvas_startup_waits_for_raw_viewport_settle():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "const CANVAS_VIEWPORT_SETTLE_MS = 260;" in js
    assert "surfaceViewportMeasurement()" in js
    assert "rawWidth" in js
    assert "rawHeight" in js
    assert "const key = `${viewport.rawWidth}x${viewport.rawHeight}`;" in js
    assert "Date.now() - this._surfaceOpenedAt >= CANVAS_VIEWPORT_SETTLE_MS" in js


def test_browser_ui_spinners_have_browser_local_animation():
    main_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html").read_text(
        encoding="utf-8"
    )
    config_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "config.html").read_text(
        encoding="utf-8"
    )

    assert ":class=\"{ spinning: $store.browserPage.extensionActionLoading }\"" in main_html
    assert "@keyframes browser-spin" in main_html
    assert "@keyframes browser-config-spin" in config_html


def test_browser_extension_settings_stay_user_facing():
    config_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "config.html").read_text(
        encoding="utf-8"
    )

    assert "Choose which installed Chrome extensions Browser loads." in config_html
    assert "Installed extensions" in config_html
    assert "<textarea" not in config_html
    assert "Enabled extension directories" not in config_html
    assert "Chrome Web Store URL installs" not in config_html
    assert "Browser caches Playwright Chromium" not in config_html


def test_browser_viewer_uses_tabs_for_session_switching():
    main_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html").read_text(
        encoding="utf-8"
    )
    browser_store = (
        PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js"
    ).read_text(encoding="utf-8")

    assert 'class="browser-session-tabs" role="tablist"' in main_html
    assert 'class="browser-tab"' in main_html
    assert 'class="browser-new-tab"' in main_html
    assert "$store.browserPage.openNewBrowser()" in main_html
    assert "browser-select" not in main_html
    assert "browser-live-dot" not in main_html
    assert "async openNewBrowser()" in browser_store
    assert "browserTabTitle(browser)" in browser_store
    assert "Scan with A0" in browser_store
    assert "Review with A0" not in browser_store
    assert "Using ${this.mainModelSummary}" in browser_store


def test_browser_viewer_uses_cdp_screencast_transport():
    ws_browser = (PROJECT_ROOT / "plugins" / "_browser" / "api" / "ws_browser.py").read_text(
        encoding="utf-8"
    )
    main_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html").read_text(
        encoding="utf-8"
    )
    runtime = (
        PROJECT_ROOT / "plugins" / "_browser" / "helpers" / "runtime.py"
    ).read_text(encoding="utf-8")
    browser_store = (
        PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js"
    ).read_text(encoding="utf-8")

    assert 'runtime.call("screenshot"' in ws_browser
    assert "SCREENCAST_QUALITY = 92" in ws_browser
    assert "initial_viewport = self._viewport_from_data(data)" in ws_browser
    assert '"set_viewport"' in ws_browser
    assert "start_screencast" in ws_browser
    assert "pop_screencast_frame" in ws_browser
    assert "stop_screencast" in ws_browser
    assert '"Page.startScreencast"' in runtime
    assert '"Page.screencastFrame"' in runtime
    assert '"Page.screencastFrameAck"' in runtime
    assert '"Page.stopScreencast"' in runtime
    assert '"Emulation.setDeviceMetricsOverride"' in runtime
    assert '"Emulation.setVisibleSize"' in runtime
    assert "asyncio.Queue(maxsize=1)" in runtime
    assert "await self._stop_screencasts_for_browser(resolved_id)" in runtime
    assert "queueFrameRender" in browser_store
    assert "requestAnimationFrame" in browser_store
    assert "viewport_width: initialViewport?.width" in browser_store
    assert "viewport_height: initialViewport?.height" in browser_store
    assert "this.frameState = data.state || null" not in browser_store
    assert "overflow: hidden;" in main_html
    assert "object-fit: fill;" in main_html
    assert "image-rendering: auto;" in main_html


def test_browser_annotate_mode_ui_and_prompt_hooks():
    panel_html = (
        PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html"
    ).read_text(encoding="utf-8")
    browser_store = (
        PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js"
    ).read_text(encoding="utf-8")

    assert "Annotate" in panel_html
    assert "Annotating" in panel_html
    assert "browser-annotation-layer" in panel_html
    assert "browser-annotation-tray" in panel_html
    assert "Draft to chat" in panel_html
    assert "Send now" in panel_html
    assert "@pointerdown.stop.prevent=\"$store.browserPage.startAnnotationSelection($event)\"" in panel_html
    assert "@keydown.window=\"$store.browserPage.handleKeydown($event)\"" in panel_html
    assert "annotationComments: []" in browser_store
    assert '"browser_viewer_annotation"' in browser_store
    assert 'event?.key === "." && (event.metaKey || event.ctrlKey)' in browser_store
    assert "Browser annotations" in browser_store
    assert "Comment:" in browser_store
    assert "Coordinates:" in browser_store
    assert "Selector:" in browser_store
    assert "DOM:" in browser_store
    assert "value=\\\"[redacted]\\\"" in browser_store


def test_browser_runtime_and_content_helper_expose_annotation_target():
    runtime = (
        PROJECT_ROOT / "plugins" / "_browser" / "helpers" / "runtime.py"
    ).read_text(encoding="utf-8")
    helper = (
        PROJECT_ROOT / "plugins" / "_browser" / "assets" / "browser-page-content.js"
    ).read_text(encoding="utf-8")

    assert "async def annotation_target" in runtime
    assert "globalThis.__spaceBrowserPageContent__.annotate(payload || null)" in runtime
    assert "function annotate(payload = null)" in helper
    assert "annotate," in helper
    assert "sanitizeAnnotationDom" in helper
    assert "password" in helper


@pytest.mark.anyio
async def test_browser_screencast_acknowledges_and_drops_stale_frames():
    first_image = (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
        "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
        "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAKAAoDASIAAhEBAxEB/8QAFQAB"
        "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhADE"
        "AAAAKf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAA"
        "AAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECA"
        "QE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAA"
        "AAAAAAAAAAAAAA/9oACAEBAAE/ISf/2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAA"
        "AAAAAAAAAAP/aAAgBAwEBPxAk/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEB"
        "PxAk/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxAn/9k="
    )

    class FakeSession:
        def __init__(self):
            self.handlers = {}
            self.sent = []
            self.detached = False

        def on(self, event, handler):
            self.handlers[event] = handler

        async def send(self, method, params=None):
            self.sent.append((method, params or {}))

        async def detach(self):
            self.detached = True

    session = FakeSession()
    screencast = _BrowserScreencast(
        stream_id="stream",
        browser_id=7,
        session=session,
        mime="image/jpeg",
    )

    await screencast.start(quality=92, every_nth_frame=1, viewport={"width": 1118, "height": 662})
    session.handlers["Page.screencastFrame"](
        {"data": first_image, "metadata": {"deviceWidth": 10}, "sessionId": 1}
    )
    session.handlers["Page.screencastFrame"](
        {"data": "second", "metadata": {"deviceWidth": 200}, "sessionId": 2}
    )
    await asyncio.sleep(0)

    frame = await screencast.next_frame(timeout=0.1)

    assert frame["browser_id"] == 7
    assert frame["image"] == "second"
    assert frame["metadata"]["deviceWidth"] == 200
    assert ("Emulation.setDeviceMetricsOverride", {
        "width": 1118,
        "height": 662,
        "deviceScaleFactor": 1,
        "mobile": False,
        "dontSetVisibleSize": True,
    }) in session.sent
    assert ("Emulation.setVisibleSize", {"width": 1118, "height": 662}) in session.sent
    assert ("Page.screencastFrameAck", {"sessionId": 1}) in session.sent
    assert ("Page.screencastFrameAck", {"sessionId": 2}) in session.sent

    await screencast.stop()

    assert ("Page.stopScreencast", {}) in session.sent
    assert session.detached is True


def test_browser_docker_installs_full_chromium_to_persistent_cache():
    script = (
        PROJECT_ROOT / "docker" / "run" / "fs" / "ins" / "install_playwright.sh"
    ).read_text(encoding="utf-8")

    assert "PLAYWRIGHT_BROWSERS_PATH=/a0/usr/plugins/_browser/playwright" in script
    assert "playwright install chromium" in script
    assert "--only-shell" not in script


def test_browser_runtime_removes_stale_profile_singletons(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browser_runtime_module.files,
        "get_abs_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    core = _BrowserRuntimeCore("stale-profile")
    core.profile_dir.mkdir(parents=True)

    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (core.profile_dir / name).symlink_to("missing-host-999999")

    core._release_orphaned_profile_singleton()

    assert not any(
        (core.profile_dir / name).exists() or (core.profile_dir / name).is_symlink()
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket")
    )


def test_browser_save_plugin_config_restarts_runtimes_on_change(monkeypatch, tmp_path):
    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()
    restarted = []

    monkeypatch.setattr(
        browser_hooks_module,
        "_load_saved_browser_config",
        lambda project_name="", agent_profile="": {
            "extension_paths": [],
        },
    )
    monkeypatch.setattr(
        browser_hooks_module,
        "close_all_runtimes_sync",
        lambda: restarted.append(True),
    )

    result = browser_hooks_module.save_plugin_config(
        {
            "extension_paths": [str(extension_dir)],
        },
        project_name="",
        agent_profile="",
    )

    assert result["extension_paths"] == [str(extension_dir)]
    assert result["model_preset"] == ""
    assert restarted == [True]


def test_browser_save_plugin_config_does_not_restart_runtimes_for_preset_only(monkeypatch):
    restarted = []

    monkeypatch.setattr(
        browser_hooks_module,
        "_load_saved_browser_config",
        lambda project_name="", agent_profile="": {
            "extension_paths": [],
            "model_preset": "",
        },
    )
    monkeypatch.setattr(
        browser_hooks_module,
        "close_all_runtimes_sync",
        lambda: restarted.append(True),
    )

    result = browser_hooks_module.save_plugin_config(
        {
            "extension_paths": [],
            "model_preset": "Research",
        },
        project_name="",
        agent_profile="",
    )

    assert result["model_preset"] == "Research"
    assert restarted == []


@pytest.mark.anyio
async def test_browser_tool_dispatches_direct_actions(monkeypatch):
    calls = []

    class FakeRuntime:
        async def call(self, method, *args):
            calls.append((method, args))
            if method == "content":
                return {"document": "[link 1] Example"}
            return {"ok": True, "method": method, "args": args}

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx"
        return FakeRuntime()

    monkeypatch.setattr(browser_tool_module, "get_runtime", fake_get_runtime)
    agent = SimpleNamespace(context=SimpleNamespace(id="ctx"))
    tool = browser_tool_module.Browser(
        agent=agent,
        name="browser",
        method=None,
        args={},
        message="",
        loop_data=None,
    )

    response = await tool.execute(action="content", browser_id=1)

    assert response.message == "[link 1] Example"
    assert calls == [("content", (1, None))]


@pytest.mark.anyio
async def test_browser_viewer_subscribe_unregisters_stream(monkeypatch):
    class FakeRuntime:
        def __init__(self) -> None:
            self.opened = False

        async def call(self, method, *args):
            if method == "list":
                if self.opened:
                    return {
                        "browsers": [{"id": 1, "currentUrl": "about:blank", "title": ""}],
                        "last_interacted_browser_id": 1,
                    }
                return {"browsers": [], "last_interacted_browser_id": None}
            if method == "open":
                self.opened = True
                return {"id": 1, "state": {"id": 1, "currentUrl": "about:blank"}}
            raise AssertionError(method)

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx"
        return FakeRuntime()

    monkeypatch.setattr(ws_browser_module, "get_runtime", fake_get_runtime)
    monkeypatch.setattr(
        ws_browser_module.AgentContext,
        "get",
        staticmethod(lambda context_id: SimpleNamespace(id=context_id)),
    )

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    result = await handler.process(
        "browser_viewer_subscribe",
        {"context_id": "ctx", "correlationId": "c1"},
        "sid-1",
    )

    assert result["context_id"] == "ctx"
    assert ("sid-1", "ctx") in ws_browser_module.WsBrowser._streams

    await handler.on_disconnect("sid-1")

    assert ("sid-1", "ctx") not in ws_browser_module.WsBrowser._streams


@pytest.mark.anyio
async def test_browser_viewer_viewport_input_dispatches_resize(monkeypatch):
    calls = []

    class FakeRuntime:
        async def call(self, method, *args, **kwargs):
            calls.append((method, args, kwargs))
            return {"ok": True, "method": method, "args": args}

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx"
        assert create is False
        return FakeRuntime()

    monkeypatch.setattr(ws_browser_module, "get_runtime", fake_get_runtime)

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    result = await handler.process(
        "browser_viewer_input",
        {
            "context_id": "ctx",
            "browser_id": 7,
            "input_type": "viewport",
            "width": 1280,
            "height": 720,
        },
        "sid-1",
    )

    assert result == {
        "state": {"ok": True, "method": "set_viewport", "args": (7, 1280, 720)},
        "snapshot": None,
    }
    assert calls == [("set_viewport", (7, 1280, 720), {})]


@pytest.mark.anyio
async def test_browser_viewer_wheel_input_dispatches_scroll(monkeypatch):
    calls = []

    class FakeRuntime:
        async def call(self, method, *args, **kwargs):
            calls.append((method, args, kwargs))
            return {"ok": True, "method": method, "args": args}

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx"
        assert create is False
        return FakeRuntime()

    monkeypatch.setattr(ws_browser_module, "get_runtime", fake_get_runtime)

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    result = await handler.process(
        "browser_viewer_input",
        {
            "context_id": "ctx",
            "browser_id": 3,
            "input_type": "wheel",
            "x": 320,
            "y": 480,
            "delta_x": 0,
            "delta_y": 640,
        },
        "sid-1",
    )

    assert result == {
        "state": {"ok": True, "method": "wheel", "args": (3, 320.0, 480.0, 0.0, 640.0)},
        "snapshot": None,
    }
    assert calls == [("wheel", (3, 320.0, 480.0, 0.0, 640.0), {})]


@pytest.mark.anyio
async def test_browser_viewer_annotation_dispatches_runtime(monkeypatch):
    calls = []

    class FakeRuntime:
        async def call(self, method, *args, **kwargs):
            calls.append((method, args, kwargs))
            return {
                "kind": "element",
                "point": {"x": 320, "y": 180},
                "target": {"tagName": "BUTTON", "selector": "#save"},
            }

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx"
        assert create is False
        return FakeRuntime()

    monkeypatch.setattr(ws_browser_module, "get_runtime", fake_get_runtime)

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    payload = {
        "kind": "element",
        "point": {"x": 320, "y": 180},
        "viewport": {"width": 1280, "height": 720},
    }
    result = await handler.process(
        "browser_viewer_annotation",
        {
            "context_id": "ctx",
            "browser_id": 4,
            "viewer_id": "viewer-1",
            "payload": payload,
        },
        "sid-1",
    )

    assert result == {
        "annotation": {
            "kind": "element",
            "point": {"x": 320, "y": 180},
            "target": {"tagName": "BUTTON", "selector": "#save"},
        },
        "context_id": "ctx",
        "browser_id": 4,
        "viewer_id": "viewer-1",
    }
    assert calls == [("annotation_target", (4, payload), {})]


def test_browser_cleanup_extensions_follow_extensible_path_layout():
    extension = __import__("helpers.extension", fromlist=["_get_extension_classes"])
    remove_classes = extension._get_extension_classes(  # type: ignore[attr-defined]
        "_functions/agent/AgentContext/remove/start"
    )
    reset_classes = extension._get_extension_classes(  # type: ignore[attr-defined]
        "_functions/agent/AgentContext/reset/start"
    )

    assert any(cls.__name__ == "CleanupBrowserRuntimeOnRemove" for cls in remove_classes)
    assert any(cls.__name__ == "CleanupBrowserRuntimeOnReset" for cls in reset_classes)


def test_legacy_browser_dependency_is_removed():
    assert not (PROJECT_ROOT / "plugins" / ("_browser" + "_agent")).exists()
    assert ("browser" + "-use") not in (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    )
