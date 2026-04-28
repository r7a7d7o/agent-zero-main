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
    list_runtime_sessions,
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


SMALL_JPEG_10X10 = (
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
    assert 'import { getContext, setContext } from "/index.js";' in js
    assert "setContext(contextId)" in js
    assert "chatsStore.setSelected?.(contextId)" in js
    assert "this.contextId = existingContextId;" in js
    assert "this.contextId = contextId;" in js
    assert "let targetContextId = requestedContextId;" in js
    assert "targetContextId = await this.ensureContextId();" in js
    assert "contextId: targetContextId" in js
    assert "No active chat context is selected." not in js


def test_browser_canvas_startup_waits_for_raw_viewport_settle():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "const CANVAS_VIEWPORT_SETTLE_MS = 520;" in js
    assert "const SURFACE_VIEWPORT_STABLE_FRAMES = 4;" in js
    assert "const SURFACE_VIEWPORT_MAX_WAIT_MS = 1200;" in js
    assert "surfaceViewportMeasurement()" in js
    assert "rawWidth" in js
    assert "rawHeight" in js
    assert "const key = `${viewport.rawWidth}x${viewport.rawHeight}`;" in js
    assert "Date.now() - this._surfaceOpenedAt >= CANVAS_VIEWPORT_SETTLE_MS" in js
    assert "while (Date.now() - startedAt <= SURFACE_VIEWPORT_MAX_WAIT_MS)" in js
    assert "this._openPromise && this._openSignature === openSignature" in js
    assert "isCurrentSurfaceOpen(surfaceSequence)" in js
    assert "isCanvasSurfaceVisible(element)" in js
    assert "scheduleViewportSyncForSurface" in js


def test_browser_canvas_surface_open_waits_for_visible_panel():
    js = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "right_canvas_register_surfaces"
        / "register-browser.js"
    ).read_text(encoding="utf-8")

    assert "waitForVisibleCanvasPanel" in js
    assert "isVisibleCanvasPanel(panel)" in js
    assert 'panel.closest(".browser-canvas-surface")' in js
    assert 'panel.querySelector(".browser-stage")' in js
    assert "getBoundingClientRect" in js
    assert "stableCount >= 2" in js
    assert "forceCanvasWidthNudgeAfterBrowserMount" not in js


def test_browser_canvas_nudges_width_after_first_accepted_frame():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "_canvasSurfaceReadySequence" in js
    assert "_canvasFirstFrameAcceptedSequence" in js
    assert "_canvasFirstFrameNudgeSequence" in js
    assert "scheduleCanvasWidthNudgeAfterFirstFrame()" in js
    assert "this._canvasSurfaceReadySequence = surfaceSequence;" in js
    assert "const surfaceSequence = this._surfaceOpenSequence;" in js
    assert "this._canvasFirstFrameAcceptedSequence = surfaceSequence;" in js
    assert "forceRightCanvasWidthNudge()" in js
    assert "await nextAnimationFrame();" in js
    assert "globalThis.Alpine" not in js
    assert 'import { store as rightCanvasStore } from "/components/canvas/right-canvas-store.js";' in js
    assert "const canvas = rightCanvasStore;" in js
    assert 'canvas.activeSurfaceId !== "browser"' in js
    assert "canvas.setWidth?.(nudgedWidth, { persist: false })" in js
    assert "this.queueViewportSync(true)" in js
    frame_assignment_index = js.index("this.frameSrc = frameSrc;")
    frame_nudge_schedule_index = js.index(
        "this.scheduleCanvasWidthNudgeAfterFirstFrame();",
        frame_assignment_index,
    )
    assert frame_assignment_index < frame_nudge_schedule_index


def test_browser_canvas_restarts_stream_after_page_navigation():
    js = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )

    assert "async restartCanvasStreamAfterPageChange()" in js
    assert '["navigate", "back", "forward", "reload"].includes(commandName)' in js
    assert "await this.restartCanvasStreamAfterPageChange();" in js
    assert "await this.waitForSurfaceViewport({ sequence: surfaceSequence });" in js
    assert "await this.syncViewport(true, { restartStream: true });" in js
    reconnect_index = js.index("contextId: this.activeBrowserContextId")
    restart_index = js.index("await this.restartCanvasStreamAfterPageChange();")
    assert reconnect_index < restart_index


def test_browser_entry_points_prefer_canvas_and_modal_dock_handoff():
    button_html = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "chat-input-bottom-actions-start"
        / "browser-button.html"
    ).read_text(encoding="utf-8")
    tool_handler = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "get_tool_message_handler"
        / "browser-tool-handler.js"
    ).read_text(encoding="utf-8")
    after_loop_handler = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "set_messages_after_loop"
        / "auto-open-browser-results.js"
    ).read_text(encoding="utf-8")
    register_js = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "right_canvas_register_surfaces"
        / "register-browser.js"
    ).read_text(encoding="utf-8")
    browser_store = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )
    canvas_store = (PROJECT_ROOT / "webui" / "components" / "canvas" / "right-canvas-store.js").read_text(
        encoding="utf-8"
    )
    modals_js = (PROJECT_ROOT / "webui" / "js" / "modals.js").read_text(encoding="utf-8")

    assert "Open Browser" in button_html
    assert "$store.rightCanvas ? $store.rightCanvas.open('browser')" in button_html
    assert "window.ensureModalOpen ? window.ensureModalOpen('/plugins/_browser/webui/main.html')" in button_html
    assert "$store.rightCanvas.toggle('browser')" not in button_html
    assert 'defaultOpenMode: "modal"' not in register_js
    assert "beginDockHandoff()" in register_js
    assert "beginSurfaceHandoff" in register_js
    assert "finishDockHandoff()" in register_js
    assert "cancelDockHandoff()" in register_js
    assert "defaultOpenMode" not in canvas_store
    assert "await surface.beginDockHandoff?.(payload)" in canvas_store
    assert "await this.closeDockSourceModal(payload, modalPath)" in canvas_store
    assert "delete openPayload.closeSourceModal" in canvas_store
    assert "await surface.finishDockHandoff?.({ ...openPayload, opened })" in canvas_store
    assert "await surface.cancelDockHandoff?.(payload)" in canvas_store
    assert "async closeDockSourceModal" in canvas_store

    assert "sourceModalPath: modal.path" in modals_js
    assert "closeSourceModal: async () =>" in modals_js
    assert "const closed = await closeModal(modal.path)" in modals_js
    assert "const fallbackClosed = await closeModal()" in modals_js
    assert "button.disabled = true" in modals_js

    assert "beginSurfaceHandoff()" in browser_store
    assert "finishSurfaceHandoff()" in browser_store
    assert "cancelSurfaceHandoff()" in browser_store
    assert "releaseSurfaceBindings()" in browser_store
    assert "this.releaseSurfaceBindings();" in browser_store

    for js in (tool_handler, after_loop_handler):
        assert "async function openBrowserCanvas" in js
        assert "openBrowserModal" not in js
        assert js.index('await rightCanvasStore.open("browser", payload);') < js.index("if (window.ensureModalOpen)")

    assert "function autoOpenBrowserCanvas" in tool_handler

    for js in (tool_handler, after_loop_handler, register_js, browser_store, modals_js):
        assert "globalThis.Alpine" not in js
        assert "Alpine?.store" not in js
        assert "Alpine.store" not in js


def test_browser_canvas_uses_plain_panel_without_debug_probe():
    panel_html = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "right-canvas-panels"
        / "browser-panel.html"
    ).read_text(encoding="utf-8")
    browser_store = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js").read_text(
        encoding="utf-8"
    )
    register_js = (
        PROJECT_ROOT
        / "plugins"
        / "_browser"
        / "extensions"
        / "webui"
        / "right_canvas_register_surfaces"
        / "register-browser.js"
    ).read_text(encoding="utf-8")
    canvas_store = (PROJECT_ROOT / "webui" / "components" / "canvas" / "right-canvas-store.js").read_text(
        encoding="utf-8"
    )
    modals_js = (PROJECT_ROOT / "webui" / "js" / "modals.js").read_text(encoding="utf-8")

    assert 'x-component path="/plugins/_browser/webui/browser-panel.html" mode="canvas"' in panel_html
    assert "browserCanvasRemountKey" not in panel_html
    assert "remountBrowserCanvasOnce" not in panel_html
    assert "data-remount-slot" not in panel_html

    for js in (browser_store, register_js, canvas_store, modals_js):
        assert "__a0BrowserDebug" not in js
        assert "browserDebug" not in js
        assert "emitBrowserDebug" not in js
        assert ".debug(" not in js


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
    assert ':key="$store.browserPage.browserTabKey(browser)"' in main_html
    assert "browser.context_id" in main_html
    assert ':title="$store.browserPage.browserTabTooltip(browser)"' in main_html
    assert "browser-tab-context" not in main_html
    assert 'handleSelectedContextChange($store.chats?.selected)' in main_html
    assert "activeBrowserContextId" in browser_store
    assert "sameBrowserTab" in browser_store
    assert "applyBrowserListing" in browser_store
    assert "browserTabTooltip(browser)" in browser_store
    assert "browserChatTitle(browser = {})" in browser_store
    assert "contextId.slice" not in browser_store
    assert '"browser_viewer_sessions"' in browser_store
    assert "$store.browserPage.openNewBrowser()" in main_html
    assert "browser-select" not in main_html
    assert "browser-live-dot" not in main_html
    assert "async openNewBrowser()" in browser_store
    assert "browserTabTitle(browser)" in browser_store
    assert "Scan with A0" in browser_store
    assert "Review with A0" not in browser_store
    assert "Using ${this.mainModelSummary}" in browser_store


def test_browser_tabs_close_without_confirmation_or_busy_lock():
    main_html = (PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-panel.html").read_text(
        encoding="utf-8"
    )
    browser_store = (
        PROJECT_ROOT / "plugins" / "_browser" / "webui" / "browser-store.js"
    ).read_text(encoding="utf-8")
    close_start = main_html.index('class="browser-tab-close"')
    close_end = main_html.index("</button>", close_start)
    close_markup = main_html[close_start:close_end]

    assert '@click.stop="$store.browserPage.closeBrowser(browser.id, browser.context_id)"' in main_html
    assert "@pointerdown.stop" in close_markup
    assert "$confirmClick" not in main_html
    assert "isBusy()" not in close_markup
    assert ':disabled="$store.browserPage.isClosingBrowser(browser.id, browser.context_id)"' in close_markup
    assert "--browser-tab-close-size: 32px;" in main_html
    assert "async closeBrowser(id, contextId = \"\")" in browser_store
    assert "isClosingBrowser(id, contextId = \"\")" in browser_store
    assert "_closingBrowserIds" in browser_store
    assert "_commandInFlightCount" in browser_store


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
    assert "restart_stream: restartStream" in browser_store
    assert 'restart_screencast=bool(data.get("restart_stream"))' in ws_browser
    assert "restart_screencast: bool = False" in runtime
    assert "should_remount_viewport = changed or restart_screencast" in runtime
    assert "VIEWPORT_REMOUNT_PAUSE_SECONDS = 0.05" in runtime
    assert "await self._apply_cdp_viewport_with_remount" in runtime
    assert "await self._apply_viewport_with_remount(page, viewport)" in runtime
    assert "await self._remount_viewport(page, viewport)" in runtime
    assert "await asyncio.sleep(VIEWPORT_REMOUNT_PAUSE_SECONDS)" in runtime
    assert "def _nudged_viewport(viewport: dict[str, int])" in runtime
    assert 'await this.syncViewport(true, { restartStream: this._mode === "canvas" });' in browser_store
    assert "this.frameState = data.state || null" not in browser_store
    assert "function loadFrameDimensions(src)" in browser_store
    assert "frameMatchesViewport(dimensions = null, viewport = null)" in browser_store
    assert "requestViewportSyncAfterRejectedFrame()" in browser_store
    assert "FRAME_FALLBACK_SCREENSHOT_SECONDS" not in ws_browser
    assert '"frame_source": "state"' in ws_browser
    assert '"frame_source"] = "screencast"' in ws_browser
    assert "fallback_screenshot" not in ws_browser
    assert "canvas_wheel_screenshot" not in ws_browser
    assert "surface_mode: this._mode" not in browser_store
    assert "overflow: hidden;" in main_html
    assert "object-fit: fill;" in main_html
    assert "image-rendering: auto;" in main_html


def test_browser_navigation_errors_stay_inside_native_browser_page():
    runtime = (
        PROJECT_ROOT / "plugins" / "_browser" / "helpers" / "runtime.py"
    ).read_text(encoding="utf-8")

    assert "Error as PlaywrightError" in runtime
    assert "except PlaywrightError as exc:" in runtime
    assert "Browser navigation showed a native error page" in runtime
    assert "await self._settle(page)" in runtime
    assert "except (PlaywrightError, PlaywrightTimeoutError):" in runtime


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
    first_image = SMALL_JPEG_10X10

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
    assert frame["metadata"]["expectedWidth"] == 1118
    assert frame["metadata"]["expectedHeight"] == 662
    metrics_calls = [
        params
        for method, params in session.sent
        if method == "Emulation.setDeviceMetricsOverride"
    ]
    visible_calls = [
        params
        for method, params in session.sent
        if method == "Emulation.setVisibleSize"
    ]
    assert metrics_calls[:3] == [
        {
            "width": 1118,
            "height": 662,
            "deviceScaleFactor": 1,
            "mobile": False,
            "dontSetVisibleSize": True,
        },
        {
            "width": 1119,
            "height": 662,
            "deviceScaleFactor": 1,
            "mobile": False,
            "dontSetVisibleSize": True,
        },
        {
            "width": 1118,
            "height": 662,
            "deviceScaleFactor": 1,
            "mobile": False,
            "dontSetVisibleSize": True,
        },
    ]
    assert visible_calls[:3] == [
        {"width": 1118, "height": 662},
        {"width": 1119, "height": 662},
        {"width": 1118, "height": 662},
    ]
    start_index = next(
        index
        for index, (method, _params) in enumerate(session.sent)
        if method == "Page.startScreencast"
    )
    cdp_viewport_indices = [
        index
        for index, (method, _params) in enumerate(session.sent)
        if method.startswith("Emulation.")
    ]
    assert max(cdp_viewport_indices) < start_index
    assert ("Page.screencastFrameAck", {"sessionId": 1}) in session.sent
    assert ("Page.screencastFrameAck", {"sessionId": 2}) in session.sent

    await screencast.stop()

    assert ("Page.stopScreencast", {}) in session.sent
    assert session.detached is True


@pytest.mark.anyio
async def test_browser_screencast_passes_wrong_viewport_frames_to_frontend_validator():
    class FakeSession:
        def __init__(self):
            self.handlers = {}
            self.sent = []

        def on(self, event, handler):
            self.handlers[event] = handler

        async def send(self, method, params=None):
            self.sent.append((method, params or {}))

        async def detach(self):
            pass

    session = FakeSession()
    screencast = _BrowserScreencast(
        stream_id="stream",
        browser_id=7,
        session=session,
        mime="image/jpeg",
    )

    await screencast.start(quality=92, every_nth_frame=1, viewport={"width": 1118, "height": 662})
    for session_id in range(1, 14):
        session.handlers["Page.screencastFrame"](
            {"data": SMALL_JPEG_10X10, "metadata": {}, "sessionId": session_id}
        )
    await asyncio.sleep(0)

    frame = await screencast.pop_frame()

    assert frame is not None
    assert frame["image"] == SMALL_JPEG_10X10
    assert frame["metadata"]["jpegWidth"] == 10
    assert frame["metadata"]["jpegHeight"] == 10
    assert frame["metadata"]["expectedWidth"] == 1118
    assert frame["metadata"]["expectedHeight"] == 662
    assert ("Page.screencastFrameAck", {"sessionId": 13}) in session.sent

    await screencast.stop()


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
async def test_browser_runtime_sessions_are_context_qualified(monkeypatch):
    class FakeRuntime:
        async def call(self, method, *args, **kwargs):
            assert method == "list"
            return {
                "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "https://example.com/"}],
                "last_interacted_browser_id": 1,
            }

    with browser_runtime_module._runtime_lock:
        previous_runtimes = dict(browser_runtime_module._runtimes)
        browser_runtime_module._runtimes.clear()
        browser_runtime_module._runtimes["ctx-a"] = FakeRuntime()
    try:
        sessions = await list_runtime_sessions()
    finally:
        with browser_runtime_module._runtime_lock:
            browser_runtime_module._runtimes.clear()
            browser_runtime_module._runtimes.update(previous_runtimes)

    assert sessions == [
        {
            "context_id": "ctx-a",
            "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "https://example.com/"}],
            "last_interacted_browser_id": 1,
        }
    ]


@pytest.mark.anyio
async def test_browser_viewer_command_returns_tabs_from_all_contexts(monkeypatch):
    class FakeRuntime:
        async def call(self, method, *args, **kwargs):
            if method == "list":
                return {
                    "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "about:blank"}],
                    "last_interacted_browser_id": 1,
                }
            raise AssertionError(method)

    async def fake_get_runtime(context_id, create=True):
        assert context_id == "ctx-a"
        return FakeRuntime()

    async def fake_list_runtime_sessions():
        return [
            {
                "context_id": "ctx-a",
                "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "about:blank"}],
                "last_interacted_browser_id": 1,
            },
            {
                "context_id": "ctx-b",
                "browsers": [{"id": 1, "context_id": "ctx-b", "currentUrl": "https://example.org/"}],
                "last_interacted_browser_id": 1,
            },
        ]

    monkeypatch.setattr(ws_browser_module, "get_runtime", fake_get_runtime)
    monkeypatch.setattr(ws_browser_module, "list_runtime_sessions", fake_list_runtime_sessions)

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    result = await handler.process(
        "browser_viewer_command",
        {"context_id": "ctx-a", "command": "list"},
        "sid-1",
    )

    assert result["all_browsers"] is True
    assert result["active_browser_context_id"] == "ctx-a"
    assert [browser["context_id"] for browser in result["browsers"]] == ["ctx-a", "ctx-b"]


@pytest.mark.anyio
async def test_browser_viewer_sessions_lists_without_creating_runtime(monkeypatch):
    async def fake_list_runtime_sessions():
        return [
            {
                "context_id": "ctx-a",
                "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "about:blank"}],
                "last_interacted_browser_id": 1,
            }
        ]

    async def fail_get_runtime(*args, **kwargs):
        raise AssertionError("sessions refresh must not create or fetch one runtime")

    monkeypatch.setattr(ws_browser_module, "list_runtime_sessions", fake_list_runtime_sessions)
    monkeypatch.setattr(ws_browser_module, "get_runtime", fail_get_runtime)

    handler = ws_browser_module.WsBrowser(
        SimpleNamespace(),
        threading.RLock(),
        manager=None,
    )

    result = await handler.process(
        "browser_viewer_sessions",
        {"context_id": "ctx-b"},
        "sid-1",
    )

    assert result == {
        "context_id": "ctx-b",
        "browsers": [{"id": 1, "context_id": "ctx-a", "currentUrl": "about:blank"}],
        "all_browsers": True,
    }


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
            "restart_stream": True,
        },
        "sid-1",
    )

    assert result == {
        "state": {"ok": True, "method": "set_viewport", "args": (7, 1280, 720)},
        "snapshot": None,
    }
    assert calls == [
        ("set_viewport", (7, 1280, 720), {"restart_screencast": True})
    ]


@pytest.mark.anyio
async def test_browser_runtime_remounts_same_viewport_when_restarting_screencast():
    viewport_calls = []
    stopped = []
    settled = []

    class FakePage:
        viewport_size = {"width": 1280, "height": 720}

        async def set_viewport_size(self, viewport):
            viewport_calls.append(dict(viewport))

    core = _BrowserRuntimeCore("ctx")
    core.context = object()
    core.pages[7] = browser_runtime_module.BrowserPage(id=7, page=FakePage())

    async def fake_stop_screencasts(browser_id):
        stopped.append(browser_id)

    async def fake_settle(page, short=False):
        settled.append(short)

    async def fake_state(browser_id):
        return {"id": browser_id}

    core._stop_screencasts_for_browser = fake_stop_screencasts
    core._settle = fake_settle
    core._state = fake_state

    result = await core.set_viewport(7, 1280, 720, restart_screencast=True)

    assert result == {
        "state": {"id": 7},
        "viewport": {"width": 1280, "height": 720},
    }
    assert viewport_calls == [
        {"width": 1281, "height": 720},
        {"width": 1280, "height": 720},
    ]
    assert stopped == [7]
    assert settled == [True]


@pytest.mark.anyio
async def test_browser_runtime_remounts_initial_changed_viewport():
    calls = []
    stopped = []
    settled = []

    class FakePage:
        viewport_size = {"width": 1024, "height": 768}

        async def set_viewport_size(self, viewport):
            calls.append(("viewport", dict(viewport)))

    core = _BrowserRuntimeCore("ctx")
    core.context = object()
    core.pages[7] = browser_runtime_module.BrowserPage(id=7, page=FakePage())

    async def fake_stop_screencasts(browser_id):
        stopped.append(browser_id)

    async def fake_settle(page, short=False):
        settled.append(short)

    async def fake_state(browser_id):
        return {"id": browser_id}

    core._stop_screencasts_for_browser = fake_stop_screencasts
    core._settle = fake_settle
    core._state = fake_state

    result = await core.set_viewport(7, 672, 789)

    assert result == {
        "state": {"id": 7},
        "viewport": {"width": 672, "height": 789},
    }
    assert calls == [
        ("viewport", {"width": 672, "height": 789}),
        ("viewport", {"width": 673, "height": 789}),
        ("viewport", {"width": 672, "height": 789}),
    ]
    assert stopped == [7]
    assert settled == [True]


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
