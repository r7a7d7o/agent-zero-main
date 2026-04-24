import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { getNamespacedClient } from "/js/websocket.js";
import { store as chatInputStore } from "/components/chat/input/input-store.js";
import { store as fileBrowserStore } from "/components/modals/file-browser/file-browser-store.js";
import { store as pluginSettingsStore } from "/components/plugins/plugin-settings-store.js";

const websocket = getNamespacedClient("/ws");
websocket.addHandlers(["ws_webui"]);

const EXTENSIONS_ROOT_FALLBACK = "/a0/usr/browser-extensions";
const BROWSER_SUBSCRIBE_TIMEOUT_MS = 60000;

function firstOk(response) {
  const result = response?.results?.find((item) => item?.ok);
  if (result) {
    const data = result.data || {};
    if (data.browser_error) {
      throw new Error(data.browser_error.error || data.browser_error.code || "Browser request failed");
    }
    return data;
  }
  const error = response?.results?.find((item) => !item?.ok)?.error;
  if (error) throw new Error(error.error || error.code || "Browser request failed");
  return {};
}

const model = {
  loading: true,
  error: "",
  status: null,
  contextId: "",
  browsers: [],
  activeBrowserId: null,
  address: "",
  frameSrc: "",
  frameState: null,
  connected: false,
  addressFocused: false,
  _frameOff: null,
  _stateOff: null,
  _lastFrameAt: 0,
  _floatingCleanup: null,
  _stageElement: null,
  _stageResizeObserver: null,
  _viewportSyncTimer: null,
  _lastViewportKey: "",
  extensionMenuOpen: false,
  extensionInstallUrl: "",
  extensionActionLoading: false,
  extensionActionMessage: "",
  extensionActionError: "",
  extensionsRoot: "",
  extensionsList: [],

  async refreshStatus() {
    this.status = await callJsonApi("/plugins/_browser/status", {});
  },

  async refreshExtensionsList() {
    const response = await callJsonApi("/plugins/_browser/extensions", { action: "list" });
    if (response?.ok) {
      this.extensionsRoot = response.root || EXTENSIONS_ROOT_FALLBACK;
      this.extensionsList = Array.isArray(response.extensions) ? response.extensions : [];
    }
  },

  toggleExtensionsMenu() {
    this.extensionMenuOpen = !this.extensionMenuOpen;
    if (this.extensionMenuOpen) {
      this.extensionActionMessage = "";
      this.extensionActionError = "";
      void this.refreshExtensionsList();
    }
  },

  closeExtensionsMenu() {
    this.extensionMenuOpen = false;
  },

  resolveContextId() {
    const urlContext = new URLSearchParams(globalThis.location?.search || "").get("ctxid");
    const selectedChat = globalThis.Alpine?.store?.("chats")?.selected;
    return globalThis.getContext?.() || urlContext || selectedChat || "";
  },

  async openExtensionsSettings() {
    if (!pluginSettingsStore?.openConfig) {
      this.error = "Browser settings are unavailable.";
      return;
    }
    try {
      this.closeExtensionsMenu();
      await pluginSettingsStore.openConfig("_browser");
      await this.refreshAfterSettingsClose();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    }
  },

  async refreshAfterSettingsClose() {
    this.loading = true;
    this.error = "";
    try {
      await this.refreshStatus();
      await this.refreshExtensionsList();
      this.connected = false;
      this.browsers = [];
      this.setActiveBrowserId(null);
      this.address = "";
      this.frameState = null;
      this.frameSrc = "";
      if (this.contextId) {
        await this.connectViewer();
      }
    } finally {
      this.loading = false;
    }
  },

  async openExtensionsFolder() {
    this.closeExtensionsMenu();
    try {
      if (!this.extensionsRoot) {
        await this.refreshExtensionsList();
      }
      void fileBrowserStore.open(this.extensionsRoot || EXTENSIONS_ROOT_FALLBACK);
    } catch (error) {
      this.extensionActionError = error instanceof Error ? error.message : String(error);
    }
  },

  createExtensionWithAgent() {
    this._prefillAgentPrompt(
      [
        "Use the a0-browser-ext skill to create a new Chrome extension for Agent Zero's Browser.",
        "Start by asking me for the extension name, purpose, target websites, and required permissions.",
        `Create it under ${this.extensionsRoot || EXTENSIONS_ROOT_FALLBACK}/<extension-slug> and keep permissions minimal.`,
      ].join("\n")
    );
  },

  askAgentInstallExtension() {
    const url = String(this.extensionInstallUrl || "").trim();
    this._prefillAgentPrompt(
      [
        "Use the a0-browser-ext skill to install and review a Chrome Web Store extension for Agent Zero's Browser.",
        url ? `Chrome Web Store URL or id: ${url}` : "Ask me for the Chrome Web Store URL or extension id first.",
        "Explain the permissions and any sandbox risk before enabling it.",
      ].join("\n")
    );
  },

  async installExtensionFromUrl() {
    const url = String(this.extensionInstallUrl || "").trim();
    this.extensionActionMessage = "";
    this.extensionActionError = "";
    if (!url) {
      this.extensionActionError = "Paste a Chrome Web Store URL or extension id first.";
      return;
    }

    this.extensionActionLoading = true;
    try {
      const response = await callJsonApi("/plugins/_browser/extensions", {
        action: "install_web_store",
        url,
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Install failed.");
      }
      this.extensionInstallUrl = "";
      this.extensionActionMessage = `Installed ${response.name || response.id}. Browser sessions restart when extension settings change.`;
      await this.refreshStatus();
      await this.refreshExtensionsList();
    } catch (error) {
      this.extensionActionError = error instanceof Error ? error.message : String(error);
    } finally {
      this.extensionActionLoading = false;
    }
  },

  _prefillAgentPrompt(prompt) {
    chatInputStore.message = prompt;
    chatInputStore.adjustTextareaHeight?.();
    chatInputStore.focus?.();
    this.closeExtensionsMenu();
  },

  async onOpen(element = null) {
    this.loading = true;
    this.error = "";
    this.setupFloatingModal(element);
    this.contextId = this.resolveContextId();
    try {
      await this.refreshStatus();
      await this.connectViewer();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.loading = false;
    }
  },

  async connectViewer() {
    if (!this.contextId) {
      this.connected = false;
      this.error = "No active chat context is selected.";
      return;
    }
    this.error = "";
    await this._bindSocketEvents();
    const response = await websocket.request(
      "browser_viewer_subscribe",
      {
        context_id: this.contextId,
        browser_id: this.activeBrowserId,
      },
      { timeoutMs: BROWSER_SUBSCRIBE_TIMEOUT_MS },
    );
      const data = firstOk(response);
      this.browsers = data.browsers || [];
      this.setActiveBrowserId(data.active_browser_id || this.activeBrowserId || null);
      this.connected = true;
      this.queueViewportSync(true);
  },

  async _bindSocketEvents() {
    if (!this._frameOff) {
      const frameHandler = ({ data }) => {
        if (data?.context_id !== this.contextId) return;
        this.browsers = data.browsers || this.browsers;
        this.setActiveBrowserId(data.browser_id || data.state?.id || this.activeBrowserId);
        this.frameState = data.state || null;
        if (!this.addressFocused && data.state?.currentUrl) {
          this.address = data.state.currentUrl;
        }
        this.frameSrc = data.image ? `data:${data.mime || "image/jpeg"};base64,${data.image}` : "";
        if (!data.image && !data.state) {
          this.setActiveBrowserId(null);
          this.frameState = null;
          this.frameSrc = "";
        }
        this._lastFrameAt = Date.now();
      };
      await websocket.on("browser_viewer_frame", frameHandler);
      this._frameOff = () => websocket.off("browser_viewer_frame", frameHandler);
    }
    if (!this._stateOff) {
      const stateHandler = ({ data }) => {
        if (data?.context_id !== this.contextId) return;
        this.browsers = data.browsers || [];
        this.setActiveBrowserId(data.last_interacted_browser_id || this.firstBrowserId());
        this.queueViewportSync(true);
      };
      await websocket.on("browser_viewer_state", stateHandler);
      this._stateOff = () => websocket.off("browser_viewer_state", stateHandler);
    }
  },

  async command(command, extra = {}) {
    this.error = "";
    const previousActiveBrowserId = this.activeBrowserId;
    try {
      const response = await websocket.request(
        "browser_viewer_command",
        {
          context_id: this.contextId,
          browser_id: this.activeBrowserId,
          command,
          ...extra,
        },
        { timeoutMs: 20000 },
      );
      const data = firstOk(response);
      this.browsers = data.browsers || this.browsers;
      const result = data.result || {};
      this.setActiveBrowserId(
        result.id
        || result.state?.id
        || result.last_interacted_browser_id
        || data.last_interacted_browser_id
        || this.firstBrowserId()
      );
      if (!this.activeBrowserId) {
        this.frameState = null;
        this.frameSrc = "";
      }
      if (result.state?.currentUrl || result.currentUrl) {
        this.address = result.state?.currentUrl || result.currentUrl;
      }
      const activeChanged = this.activeBrowserId && this.activeBrowserId !== previousActiveBrowserId;
      if ((command === "open" || command === "close" || activeChanged) && this.contextId && this.activeBrowserId) {
        await this.connectViewer();
      }
      this.queueViewportSync(true);
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    }
  },

  async go() {
    const url = String(this.address || "").trim();
    if (!url) return;
    this.addressFocused = false;
    globalThis.document?.activeElement?.blur?.();
    if (this.activeBrowserId) {
      await this.command("navigate", { url });
    } else {
      await this.command("open", { url });
    }
  },

  onAddressFocus() {
    this.addressFocused = true;
  },

  onAddressBlur() {
    this.addressFocused = false;
    if (this.frameState?.currentUrl && !String(this.address || "").trim()) {
      this.address = this.frameState.currentUrl;
    }
  },

  async selectBrowser(id) {
    if (String(id || "").trim() === "") {
      await this.command("open", { url: "about:blank" });
      return;
    }
    this.setActiveBrowserId(id);
    if (this.contextId) {
      await this.connectViewer();
    }
  },

  firstBrowserId() {
    const first = Array.isArray(this.browsers) ? this.browsers[0] : null;
    return first?.id || null;
  },

  setActiveBrowserId(id) {
    const previous = this.activeBrowserId;
    const numeric = Number(id) || null;
    const exists = !numeric || !Array.isArray(this.browsers) || this.browsers.some((browser) => Number(browser.id) === numeric);
    this.activeBrowserId = exists ? numeric : null;
    if (this.activeBrowserId !== previous) {
      this._lastViewportKey = "";
    }
  },

  pointerCoordinatesFor(event, element = null) {
    const target = element || event?.currentTarget;
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    const naturalWidth = target.naturalWidth || rect.width;
    const naturalHeight = target.naturalHeight || rect.height;
    return {
      x: ((event.clientX - rect.left) / Math.max(1, rect.width)) * naturalWidth,
      y: ((event.clientY - rect.top) / Math.max(1, rect.height)) * naturalHeight,
    };
  },

  currentViewportSize() {
    const stage = this._stageElement;
    if (!stage) return null;
    const width = Math.floor(stage.clientWidth || 0);
    const height = Math.floor(stage.clientHeight || 0);
    if (width < 80 || height < 80) return null;
    return {
      width: Math.max(320, width),
      height: Math.max(200, height),
    };
  },

  queueViewportSync(force = false) {
    if (this._viewportSyncTimer) {
      globalThis.clearTimeout(this._viewportSyncTimer);
    }
    this._viewportSyncTimer = globalThis.setTimeout(() => {
      this._viewportSyncTimer = null;
      void this.syncViewport(force);
    }, force ? 0 : 80);
  },

  async syncViewport(force = false) {
    if (!this.contextId || !this.activeBrowserId) return;
    const viewport = this.currentViewportSize();
    if (!viewport) return;
    const key = `${this.activeBrowserId}:${viewport.width}x${viewport.height}`;
    if (!force && this._lastViewportKey === key) return;
    try {
      await websocket.emit("browser_viewer_input", {
        context_id: this.contextId,
        browser_id: this.activeBrowserId,
        input_type: "viewport",
        width: viewport.width,
        height: viewport.height,
      });
      this._lastViewportKey = key;
    } catch (error) {
      this._lastViewportKey = "";
      console.warn("Browser viewport sync failed", error);
    }
  },

  async sendMouse(eventType, event) {
    if (!this.activeBrowserId || !event?.currentTarget) return;
    const pointer = this.pointerCoordinatesFor(event);
    if (!pointer) return;
    await websocket.emit("browser_viewer_input", {
      context_id: this.contextId,
      browser_id: this.activeBrowserId,
      input_type: "mouse",
      event_type: eventType,
      x: pointer.x,
      y: pointer.y,
      button: "left",
    });
  },

  async sendWheel(event) {
    if (!this.activeBrowserId || !event) return;
    const image = event.currentTarget?.querySelector?.(".browser-frame") || event.target?.closest?.(".browser-frame");
    const pointer = this.pointerCoordinatesFor(event, image);
    if (!pointer) return;
    await websocket.emit("browser_viewer_input", {
      context_id: this.contextId,
      browser_id: this.activeBrowserId,
      input_type: "wheel",
      x: pointer.x,
      y: pointer.y,
      delta_x: Number(event.deltaX || 0),
      delta_y: Number(event.deltaY || 0),
    });
  },

  async sendKey(event) {
    if (!this.activeBrowserId) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const editable = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target?.tagName);
    if (editable) return;
    event.preventDefault();
    const printable = event.key && event.key.length === 1;
    await websocket.emit("browser_viewer_input", {
      context_id: this.contextId,
      browser_id: this.activeBrowserId,
      input_type: "keyboard",
      key: printable ? "" : event.key,
      text: printable ? event.key : "",
    });
  },

  async cleanup() {
    if (this.contextId) {
      try {
        await websocket.emit("browser_viewer_unsubscribe", { context_id: this.contextId });
      } catch {}
    }
    this._frameOff?.();
    this._stateOff?.();
    this._frameOff = null;
    this._stateOff = null;
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    this._stageResizeObserver?.disconnect?.();
    this._stageResizeObserver = null;
    this._stageElement = null;
    if (this._viewportSyncTimer) {
      globalThis.clearTimeout(this._viewportSyncTimer);
      this._viewportSyncTimer = null;
    }
    this._lastViewportKey = "";
    this.extensionMenuOpen = false;
    this.extensionActionLoading = false;
    this.connected = false;
  },

  setupFloatingModal(element = null) {
    this._floatingCleanup?.();
    const root = element || globalThis.document?.querySelector(".browser-panel");
    const modal = root?.closest?.(".modal");
    const inner = modal?.querySelector?.(".modal-inner");
    const body = modal?.querySelector?.(".modal-bd");
    const header = modal?.querySelector?.(".modal-header");
    const stage = root?.querySelector?.(".browser-stage");
    if (!modal || !inner || !header) return;
    modal.classList.add("modal-floating");
    inner.classList.add("browser-modal");
    body?.classList?.add("browser-modal-body");
    this._stageElement = stage || null;

    const rect = inner.getBoundingClientRect();
    inner.style.left = `${Math.max(8, rect.left)}px`;
    inner.style.top = `${Math.max(8, rect.top)}px`;
    inner.style.transform = "none";

    let drag = null;
    let resizeObserver = null;
    const viewportGap = 8;
    const clampPosition = (left, top) => {
      const bounds = inner.getBoundingClientRect();
      const maxLeft = Math.max(viewportGap, globalThis.innerWidth - bounds.width - viewportGap);
      const maxTop = Math.max(viewportGap, globalThis.innerHeight - bounds.height - viewportGap);
      return {
        left: Math.min(Math.max(viewportGap, left), maxLeft),
        top: Math.min(Math.max(viewportGap, top), maxTop),
      };
    };
    const clampGeometry = () => {
      const bounds = inner.getBoundingClientRect();
      const left = Math.max(viewportGap, bounds.left);
      const top = Math.max(viewportGap, bounds.top);
      const maxWidth = Math.max(320, globalThis.innerWidth - viewportGap * 2);
      const maxHeight = Math.max(300, globalThis.innerHeight - viewportGap * 2);
      if (bounds.width > maxWidth) {
        inner.style.width = `${maxWidth}px`;
      }
      if (bounds.height > maxHeight) {
        inner.style.height = `${maxHeight}px`;
      }
      const next = clampPosition(left, top);
      inner.style.left = `${next.left}px`;
      inner.style.top = `${next.top}px`;
      inner.style.maxWidth = `${Math.max(320, globalThis.innerWidth - next.left - viewportGap)}px`;
      inner.style.maxHeight = `${Math.max(300, globalThis.innerHeight - next.top - viewportGap)}px`;
      this.queueViewportSync();
    };
    clampGeometry();
    globalThis.addEventListener("resize", clampGeometry);
    if (globalThis.ResizeObserver) {
      resizeObserver = new ResizeObserver(clampGeometry);
      resizeObserver.observe(inner);
      if (stage) {
        this._stageResizeObserver?.disconnect?.();
        this._stageResizeObserver = new ResizeObserver(() => this.queueViewportSync());
        this._stageResizeObserver.observe(stage);
      }
    }
    globalThis.requestAnimationFrame(() => this.queueViewportSync(true));

    const onPointerMove = (event) => {
      if (!drag) return;
      const next = clampPosition(
        drag.left + event.clientX - drag.x,
        drag.top + event.clientY - drag.y,
      );
      inner.style.left = `${next.left}px`;
      inner.style.top = `${next.top}px`;
      clampGeometry();
    };
    const onPointerUp = () => {
      drag = null;
      globalThis.removeEventListener("pointermove", onPointerMove);
      globalThis.removeEventListener("pointerup", onPointerUp);
      try {
        header.releasePointerCapture?.(header.__browserPanelPointerId || 0);
      } catch {}
    };
    const onPointerDown = (event) => {
      if (event.button !== 0) return;
      if (event.target?.closest?.("button, input, select, textarea, a")) return;
      const current = inner.getBoundingClientRect();
      drag = {
        x: event.clientX,
        y: event.clientY,
        left: current.left,
        top: current.top,
      };
      header.__browserPanelPointerId = event.pointerId;
      header.setPointerCapture?.(event.pointerId);
      globalThis.addEventListener("pointermove", onPointerMove);
      globalThis.addEventListener("pointerup", onPointerUp);
      event.preventDefault();
    };
    header.addEventListener("pointerdown", onPointerDown);

    this._floatingCleanup = () => {
      header.removeEventListener("pointerdown", onPointerDown);
      globalThis.removeEventListener("pointermove", onPointerMove);
      globalThis.removeEventListener("pointerup", onPointerUp);
      globalThis.removeEventListener("resize", clampGeometry);
      resizeObserver?.disconnect?.();
      this._stageResizeObserver?.disconnect?.();
      this._stageResizeObserver = null;
    };
  },

  get activeTitle() {
    return this.frameState?.title || "Browser";
  },

  get activeUrl() {
    return this.frameState?.currentUrl || this.address || "about:blank";
  },
};

export const store = createStore("browserPage", model);
