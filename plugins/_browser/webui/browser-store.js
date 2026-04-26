import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { getNamespacedClient } from "/js/websocket.js";
import { store as chatInputStore } from "/components/chat/input/input-store.js";
import { store as pluginSettingsStore } from "/components/plugins/plugin-settings-store.js";

const websocket = getNamespacedClient("/ws");
websocket.addHandlers(["ws_webui"]);

const EXTENSIONS_ROOT = "/a0/usr/plugins/_browser/extensions";
const BROWSER_SUBSCRIBE_TIMEOUT_MS = 60000;
const BROWSER_FIRST_INSTALL_TIMEOUT_MS = 300000;
const BROWSER_CONFIG_REFRESH_MS = 15000;
const VIEWPORT_SYNC_DEBOUNCE_MS = 220;
const VIEWPORT_SYNC_SIZE_TOLERANCE = 4;
const ANNOTATION_DRAG_THRESHOLD = 6;
const ANNOTATION_MAX_COMMENTS = 24;
const ANNOTATION_DOM_LIMIT = 1200;

function makeViewerToken() {
  return globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

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

function normalizeBool(value, fallback = true) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Boolean(value);
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on", "enabled"].includes(normalized)) return true;
  if (["0", "false", "no", "off", "disabled"].includes(normalized)) return false;
  return fallback;
}

function nextAnimationFrame() {
  return new Promise((resolve) => {
    const schedule = globalThis.requestAnimationFrame || ((callback) => globalThis.setTimeout(callback, 16));
    schedule(() => resolve());
  });
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
  annotating: false,
  annotationComments: [],
  annotationDraft: null,
  annotationDraftText: "",
  annotationDragRect: null,
  annotationBusy: false,
  annotationError: "",
  connected: false,
  switchingBrowserId: null,
  commandInFlight: false,
  addressFocused: false,
  _frameOff: null,
  _stateOff: null,
  _lastFrameAt: 0,
  _pendingFrameSrc: "",
  _frameRenderHandle: null,
  _frameRenderCancel: null,
  _floatingCleanup: null,
  _stageElement: null,
  _stageResizeObserver: null,
  _viewportSyncTimer: null,
  _lastViewportKey: "",
  _lastViewport: null,
  _annotationPointer: null,
  _annotationSequence: 0,
  _mode: "",
  _surfaceMounted: false,
  _surfaceSwitching: false,
  _connectSequence: 0,
  _viewerToken: "",
  extensionMenuOpen: false,
  extensionInstallUrl: "",
  extensionActionLoading: false,
  extensionActionMessage: "",
  extensionActionError: "",
  extensionsRoot: "",
  extensionsList: [],
  extensionsListLoading: false,
  extensionToggleLoadingPath: "",
  modelPreset: "",
  modelPresetOptions: [],
  mainModelSummary: "",
  modelPresetSaving: false,
  browserInstallExpected: false,
  defaultHomepage: "about:blank",
  autofocusActivePage: true,
  _configLoadedAt: 0,
  _configRefreshPromise: null,

  async refreshStatus() {
    this.status = await callJsonApi("/plugins/_browser/status", {});
    this.browserInstallExpected = Boolean(this.status?.playwright?.install_required);
  },

  async refreshExtensionsList() {
    this.extensionsListLoading = true;
    try {
      const response = await callJsonApi("/plugins/_browser/extensions", {
        action: "list",
        context_id: this.contextId,
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Could not load browser extensions.");
      }
      this.applyExtensionPayload(response);
    } catch (error) {
      this.extensionActionError = error instanceof Error ? error.message : String(error);
    } finally {
      this.extensionsListLoading = false;
    }
  },

  applyExtensionPayload(response = {}) {
    this.extensionsRoot = response.root || EXTENSIONS_ROOT;
    this.extensionsList = Array.isArray(response.extensions) ? response.extensions : [];
    this.defaultHomepage = String(response.default_homepage || "about:blank").trim() || "about:blank";
    this.autofocusActivePage = normalizeBool(response.autofocus_active_page, true);
    this.modelPreset = String(response.model_preset || "");
    this.mainModelSummary = String(response.main_model_summary || "");
    this.modelPresetOptions = Array.isArray(response.model_preset_options)
      ? response.model_preset_options
      : [];
    this._configLoadedAt = Date.now();
  },

  async ensureBrowserConfigLoaded(force = false) {
    if (!force && this._configLoadedAt && Date.now() - this._configLoadedAt < BROWSER_CONFIG_REFRESH_MS) {
      return;
    }
    if (this._configRefreshPromise) {
      await this._configRefreshPromise;
      return;
    }
    this._configRefreshPromise = (async () => {
      const response = await callJsonApi("/plugins/_browser/extensions", {
        action: "list",
        context_id: this.contextId || this.resolveContextId(),
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Could not load browser settings.");
      }
      this.applyExtensionPayload(response);
    })();
    try {
      await this._configRefreshPromise;
    } finally {
      this._configRefreshPromise = null;
    }
  },

  async allowsToolAutofocus() {
    try {
      await this.ensureBrowserConfigLoaded();
    } catch (error) {
      console.warn("Browser autofocus setting could not be loaded", error);
    }
    return this.autofocusActivePage !== false;
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

  createExtensionWithAgent() {
    this._prefillAgentPrompt(
      [
        "Use the a0-browser-ext skill to create a new Chrome extension for Agent Zero's Browser.",
        "Start by asking me for the extension name, purpose, target websites, and required permissions.",
        `Create it under ${this.extensionsRoot || EXTENSIONS_ROOT}/<extension-slug> and keep permissions minimal.`,
      ].join("\n")
    );
  },

  askAgentInstallExtension() {
    const url = String(this.extensionInstallUrl || "").trim();
    const prompt = url
      ? [
          "Use the a0-browser-ext skill to review and optionally install this Chrome Web Store extension for Agent Zero's Browser.",
          `Chrome Web Store URL or id: ${url}`,
          "Explain the permissions and any sandbox risk before enabling it.",
        ].join("\n")
      : [
          "Use the a0-browser-ext skill to help me install and review a Chrome Web Store extension for Agent Zero's Browser.",
          "Ask me for the Chrome Web Store URL or extension id first.",
          "Explain the permissions and any sandbox risk before enabling it.",
        ].join("\n");
    this._prefillAgentPrompt(prompt);
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
        context_id: this.contextId,
        url,
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Install failed.");
      }
      this.applyExtensionPayload(response);
      this.extensionInstallUrl = "";
      this.extensionActionMessage = `Installed ${response.name || response.id}.`;
      await this.refreshAfterSettingsClose();
    } catch (error) {
      this.extensionActionError = error instanceof Error ? error.message : String(error);
    } finally {
      this.extensionActionLoading = false;
    }
  },

  async setExtensionEnabled(extension, enabled, input = null) {
    const path = String(extension?.path || "");
    if (!path) return;
    const previous = Boolean(extension?.enabled);
    this.extensionActionMessage = "";
    this.extensionActionError = "";
    this.extensionToggleLoadingPath = path;
    try {
      const response = await callJsonApi("/plugins/_browser/extensions", {
        action: "set_extension_enabled",
        context_id: this.contextId,
        path,
        enabled: Boolean(enabled),
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Could not update extension.");
      }
      this.applyExtensionPayload(response);
      this.extensionActionMessage = `${enabled ? "Enabled" : "Disabled"} ${extension.name || "extension"}.`;
      await this.refreshAfterSettingsClose();
    } catch (error) {
      if (input) input.checked = previous;
      this.extensionActionError = error instanceof Error ? error.message : String(error);
    } finally {
      this.extensionToggleLoadingPath = "";
    }
  },

  async setBrowserModelPreset(value) {
    const presetName = String(value || "");
    this.modelPreset = presetName;
    this.extensionActionMessage = "";
    this.extensionActionError = "";
    this.modelPresetSaving = true;
    try {
      const response = await callJsonApi("/plugins/_browser/extensions", {
        action: "set_model_preset",
        context_id: this.contextId,
        model_preset: presetName,
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Could not update browser model preset.");
      }
      this.applyExtensionPayload(response);
      this.extensionActionMessage = "Browser model preset updated.";
    } catch (error) {
      this.extensionActionError = error instanceof Error ? error.message : String(error);
      await this.refreshExtensionsList();
    } finally {
      this.modelPresetSaving = false;
    }
  },

  modelPresetSummary() {
    if (!this.modelPreset) {
      return this.mainModelSummary ? `Using ${this.mainModelSummary}` : "Using Main Model";
    }
    const option = this.modelPresetOptions.find((preset) => preset?.name === this.modelPreset);
    return option?.summary || option?.label || this.modelPreset;
  },

  hasExtensionInstallUrl() {
    return Boolean(String(this.extensionInstallUrl || "").trim());
  },

  extensionAssistantActionLabel() {
    return "Scan with A0";
  },

  extensionVersionLabel(extension) {
    const version = String(extension?.version || "").trim();
    return version ? `v${version}` : "Unpacked extension";
  },

  _prefillAgentPrompt(prompt) {
    chatInputStore.message = prompt;
    chatInputStore.adjustTextareaHeight?.();
    chatInputStore.focus?.();
    this.closeExtensionsMenu();
  },

  async onOpen(element = null, options = {}) {
    this.loading = true;
    this.error = "";
    const requestedBrowserId = this.normalizeBrowserId(options.browserId ?? options.browser_id);
    const nextMode = options?.mode === "modal" ? "modal" : "canvas";
    this.prepareSurfaceOpen(nextMode, requestedBrowserId);
    if (nextMode === "modal") {
      this.setupFloatingModal(element);
    } else {
      this.setupCanvasSurface(element);
    }
    this.contextId = this.resolveContextId();
    try {
      await this.refreshStatus();
      const viewport = await this.waitForSurfaceViewport();
      this.resetRenderedFrameIfViewportChanged(viewport, requestedBrowserId);
      await this.connectViewer({ browserId: requestedBrowserId, initialViewport: viewport });
      await this.syncViewportAfterSurfaceOpen();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.loading = false;
    }
  },

  prepareSurfaceOpen(nextMode, requestedBrowserId = null) {
    const previousMode = this._mode;
    const modeChanged = this._surfaceMounted && previousMode && previousMode !== nextMode;
    const targetBrowserId = requestedBrowserId || this.activeBrowserId || this.firstBrowserId();
    this._mode = nextMode;
    this._surfaceMounted = true;
    this._lastViewportKey = "";
    if (!modeChanged && (this.frameSrc || !targetBrowserId)) return;

    this.resetRenderedFrame();
    this.resetViewportTracking();
    this._surfaceSwitching = Boolean(targetBrowserId);
    this.switchingBrowserId = targetBrowserId;
  },

  resetViewportTracking() {
    this._lastViewportKey = "";
    this._lastViewport = null;
  },

  resetRenderedFrame() {
    this.cancelFrameRender();
    this.frameSrc = "";
    this._lastFrameAt = 0;
  },

  resetRenderedFrameIfViewportChanged(viewport = null, requestedBrowserId = null) {
    if (!viewport || !this.frameSrc || !this._lastViewport) return;
    const targetBrowserId = requestedBrowserId || this.activeBrowserId || this.firstBrowserId();
    if (!this.sameBrowserId(this._lastViewport.browserId, targetBrowserId)) return;
    const changed = Math.abs(this._lastViewport.width - viewport.width) > VIEWPORT_SYNC_SIZE_TOLERANCE
      || Math.abs(this._lastViewport.height - viewport.height) > VIEWPORT_SYNC_SIZE_TOLERANCE;
    if (!changed) return;

    this.resetRenderedFrame();
    this.resetViewportTracking();
    this._surfaceSwitching = true;
    this.switchingBrowserId = targetBrowserId;
  },

  async waitForSurfaceViewport() {
    let lastKey = "";
    let stableCount = 0;
    for (let index = 0; index < 24; index += 1) {
      await nextAnimationFrame();
      const viewport = this.currentViewportSize();
      if (!viewport) continue;
      const key = `${viewport.width}x${viewport.height}`;
      if (key === lastKey) {
        stableCount += 1;
        if (stableCount >= 2) return viewport;
      } else {
        stableCount = 0;
        lastKey = key;
      }
    }
    return this.currentViewportSize();
  },

  async syncViewportAfterSurfaceOpen() {
    if (!this.connected || !this.activeBrowserId) return;
    await this.waitForSurfaceViewport();
    await this.syncViewport(true);
    if (this._mode !== "canvas") return;
    globalThis.setTimeout?.(() => this.queueViewportSync(true), 240);
    globalThis.setTimeout?.(() => this.queueViewportSync(true), 420);
  },

  async connectViewer(options = {}) {
    if (!this.contextId) {
      this.connected = false;
      this.error = "No active chat context is selected.";
      this.switchingBrowserId = null;
      this._surfaceSwitching = false;
      return;
    }
    const requestedBrowserId = this.normalizeBrowserId(options.browserId ?? this.activeBrowserId);
    const sequence = this._connectSequence + 1;
    const viewerToken = makeViewerToken();
    this._connectSequence = sequence;
    this._viewerToken = viewerToken;
    this.error = "";
    await this._bindSocketEvents();
    if (sequence !== this._connectSequence || viewerToken !== this._viewerToken) {
      return;
    }
    const initialViewport = options.initialViewport || this.currentViewportSize();
    let response;
    try {
      response = await websocket.request(
        "browser_viewer_subscribe",
        {
          context_id: this.contextId,
          browser_id: requestedBrowserId,
          viewer_id: viewerToken,
          viewport_width: initialViewport?.width,
          viewport_height: initialViewport?.height,
        },
        {
          timeoutMs: this.browserInstallExpected
            ? BROWSER_FIRST_INSTALL_TIMEOUT_MS
            : BROWSER_SUBSCRIBE_TIMEOUT_MS,
        },
      );
    } catch (error) {
      if (sequence === this._connectSequence && viewerToken === this._viewerToken) {
        this.switchingBrowserId = null;
        this._surfaceSwitching = false;
        throw error;
      }
      return;
    }
    if (sequence !== this._connectSequence || viewerToken !== this._viewerToken) {
      return;
    }
    const data = firstOk(response);
    this.browsers = data.browsers || [];
    this.setActiveBrowserId(data.active_browser_id || requestedBrowserId || this.activeBrowserId || null);
	    this.connected = true;
	    this.browserInstallExpected = false;
	  },

  async _bindSocketEvents() {
    if (!this._frameOff) {
      const frameHandler = ({ data }) => {
        if (data?.context_id !== this.contextId) return;
        if (data?.viewer_id && data.viewer_id !== this._viewerToken) return;
        const incomingBrowserId = this.normalizeBrowserId(data.browser_id || data.state?.id);
        this.browsers = data.browsers || this.browsers;
        if (incomingBrowserId && !this.activeBrowserId) {
          this.setActiveBrowserId(incomingBrowserId);
        }
        if (incomingBrowserId && this.activeBrowserId && !this.sameBrowserId(incomingBrowserId, this.activeBrowserId)) {
          return;
        }
        if (data.state) {
          this.frameState = data.state;
        }
        if (!this.addressFocused && data.state?.currentUrl) {
          this.address = data.state.currentUrl;
        }
        if (data.image) {
          this.queueFrameRender(`data:${data.mime || "image/jpeg"};base64,${data.image}`);
          if (this.sameBrowserId(this.switchingBrowserId, incomingBrowserId || this.activeBrowserId)) {
            this.switchingBrowserId = null;
          }
          this._surfaceSwitching = false;
        } else {
          this.cancelFrameRender();
          if (!data.state) {
            this.frameSrc = "";
          }
        }
        if (!data.image && !data.state) {
          if (!this.activeBrowserId) {
            this.setActiveBrowserId(null);
            this.frameState = null;
            this.frameSrc = "";
          }
        }
        this._lastFrameAt = Date.now();
      };
      await websocket.on("browser_viewer_frame", frameHandler);
      this._frameOff = () => websocket.off("browser_viewer_frame", frameHandler);
    }
	    if (!this._stateOff) {
	      const stateHandler = ({ data }) => {
	        if (data?.context_id !== this.contextId) return;
	        if (data?.viewer_id && data.viewer_id !== this._viewerToken) return;
	        this.browsers = data.browsers || [];
        const command = String(data.command || "").toLowerCase();
        const commandBrowserId = this.normalizeBrowserId(data.browser_id);
        const result = data.result || {};
        const resultState = this.stateFromCommandResult(result);
        const preferredBrowserId = this.normalizeBrowserId(
          result.id
          || result.state?.id
          || data.last_interacted_browser_id
          || this.activeBrowserId
          || this.firstBrowserId()
        );
	        if (
	          !this.activeBrowserId
	          || command === "open"
	          || command === "close"
	          || this.sameBrowserId(commandBrowserId, this.activeBrowserId)
	        ) {
	          this.setActiveBrowserId(preferredBrowserId);
	        }
	        this.applyActiveFrameState(resultState || this.browserById(this.activeBrowserId));
	        this.applySnapshot(data.snapshot);
	      };
      await websocket.on("browser_viewer_state", stateHandler);
      this._stateOff = () => websocket.off("browser_viewer_state", stateHandler);
    }
  },

  queueFrameRender(frameSrc) {
    this._pendingFrameSrc = frameSrc;
    if (this._frameRenderHandle) return;
    const schedule = globalThis.requestAnimationFrame?.bind(globalThis);
    if (schedule) {
      this._frameRenderCancel = globalThis.cancelAnimationFrame?.bind(globalThis) || null;
      this._frameRenderHandle = schedule(() => this.flushFrameRender());
      return;
    }
    this._frameRenderCancel = globalThis.clearTimeout?.bind(globalThis) || null;
    this._frameRenderHandle = globalThis.setTimeout(() => this.flushFrameRender(), 16);
  },

  flushFrameRender() {
    this._frameRenderHandle = null;
    this._frameRenderCancel = null;
    this.frameSrc = this._pendingFrameSrc || "";
    this._pendingFrameSrc = "";
  },

  cancelFrameRender() {
    if (this._frameRenderHandle && this._frameRenderCancel) {
      this._frameRenderCancel(this._frameRenderHandle);
    }
    this._frameRenderHandle = null;
    this._frameRenderCancel = null;
    this._pendingFrameSrc = "";
  },

  async command(command, extra = {}) {
    this.error = "";
    this.annotationError = "";
    this.commandInFlight = true;
    const previousActiveBrowserId = this.activeBrowserId;
    try {
      const response = await websocket.request(
        "browser_viewer_command",
        {
          context_id: this.contextId,
          browser_id: this.activeBrowserId,
          viewer_id: this._viewerToken,
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
      this.applyActiveFrameState(this.stateFromCommandResult(result) || this.browserById(this.activeBrowserId));
      if (!this.activeBrowserId) {
        this.frameState = null;
        this.frameSrc = "";
      }
      if (result.state?.currentUrl || result.currentUrl) {
        this.address = result.state?.currentUrl || result.currentUrl;
      }
      this.applySnapshot(data.snapshot);
      if (["navigate", "back", "forward", "reload", "close"].includes(String(command || "").toLowerCase())) {
        this.clearAnnotationsForBrowser(previousActiveBrowserId);
        this.cancelAnnotationDraft();
      }
      const activeChanged = this.activeBrowserId && this.activeBrowserId !== previousActiveBrowserId;
      if ((command === "open" || command === "close" || activeChanged) && this.contextId && this.activeBrowserId) {
        await this.connectViewer({ browserId: this.activeBrowserId });
	      }
	    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.commandInFlight = false;
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
    const targetId = this.normalizeBrowserId(id);
    if (!targetId) {
      await this.openNewBrowser();
      return;
    }
    if (this.sameBrowserId(targetId, this.activeBrowserId) && this.connected && !this.isSwitchingBrowser()) {
      return;
    }
    const browser = this.browserById(targetId);
    this.error = "";
    this.switchingBrowserId = targetId;
    this.cancelFrameRender();
    this.frameSrc = "";
    this.frameState = browser || null;
    if (!this.addressFocused && browser?.currentUrl) {
      this.address = browser.currentUrl;
    }
    this.setActiveBrowserId(targetId);
    if (this.contextId) {
      try {
        await this.connectViewer({ browserId: targetId });
      } catch (error) {
        if (this.sameBrowserId(this.switchingBrowserId, targetId)) {
          this.switchingBrowserId = null;
        }
        this.error = error instanceof Error ? error.message : String(error);
      }
    }
  },

  async openNewBrowser() {
    await this.command("open");
  },

  isActiveBrowser(browser) {
    return Number(browser?.id) === Number(this.activeBrowserId);
  },

  browserTabTitle(browser) {
    const title = String(browser?.title || "").trim();
    const url = String(browser?.currentUrl || "").trim();
    return title || url || "about:blank";
  },

  browserTabLabel(browser) {
    const id = browser?.id ? `#${browser.id}` : "Browser";
    return `${id} ${this.browserTabTitle(browser)}`;
  },

  firstBrowserId() {
    const first = Array.isArray(this.browsers) ? this.browsers[0] : null;
    return first?.id || null;
  },

  normalizeBrowserId(id) {
    return Number(id) || null;
  },

  sameBrowserId(left, right) {
    const leftId = this.normalizeBrowserId(left);
    const rightId = this.normalizeBrowserId(right);
    return Boolean(leftId && rightId && leftId === rightId);
  },

  browserById(id) {
    const numeric = this.normalizeBrowserId(id);
    if (!numeric || !Array.isArray(this.browsers)) return null;
    return this.browsers.find((browser) => Number(browser?.id) === numeric) || null;
  },

  stateFromCommandResult(result = {}) {
    if (result?.state?.id || result?.state?.currentUrl || result?.state?.title) {
      return result.state;
    }
    if (result?.id || result?.currentUrl || result?.title) {
      return result;
    }
    return null;
  },

  applyActiveFrameState(nextState = null) {
    if (!nextState) return;
    const stateId = this.normalizeBrowserId(nextState.id);
    if (stateId && this.activeBrowserId && !this.sameBrowserId(stateId, this.activeBrowserId)) {
      return;
    }
    const previousUrl = String(this.frameState?.currentUrl || "");
    const nextUrl = String(nextState.currentUrl || "");
    this.frameState = nextState;
    if (previousUrl && nextUrl && previousUrl !== nextUrl) {
      this.cancelAnnotationDraft();
    }
    if (!this.addressFocused && nextState.currentUrl) {
      this.address = nextState.currentUrl;
    }
  },

	  applySnapshot(snapshot = null) {
	    if (!snapshot?.image) return;
	    const snapshotId = this.normalizeBrowserId(snapshot.browser_id || snapshot.state?.id);
	    if (snapshotId && this.activeBrowserId && !this.sameBrowserId(snapshotId, this.activeBrowserId)) {
	      return;
	    }
	    if (snapshot.state) {
	      this.applyActiveFrameState(snapshot.state);
	    }
	    this.queueFrameRender(`data:${snapshot.mime || "image/jpeg"};base64,${snapshot.image}`);
	    if (this.sameBrowserId(this.switchingBrowserId, snapshotId || this.activeBrowserId)) {
	      this.switchingBrowserId = null;
	    }
	    this._surfaceSwitching = false;
	  },

  isSwitchingBrowser() {
    return Boolean(this.switchingBrowserId && this.sameBrowserId(this.switchingBrowserId, this.activeBrowserId));
  },

  isBusy() {
    return Boolean(this.loading || this.commandInFlight || this._surfaceSwitching || this.isSwitchingBrowser());
  },

  setActiveBrowserId(id) {
    const previous = this.activeBrowserId;
    const numeric = this.normalizeBrowserId(id);
    const exists = !numeric || !Array.isArray(this.browsers) || this.browsers.some((browser) => Number(browser.id) === numeric);
    this.activeBrowserId = exists ? numeric : null;
    if (this.activeBrowserId !== previous) {
      this._lastViewportKey = "";
      this._lastViewport = null;
      this.cancelAnnotationDraft();
    }
  },

  pointerCoordinatesFor(event, element = null) {
    const target = element || event?.currentTarget;
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    const naturalWidth = target.naturalWidth || rect.width;
    const naturalHeight = target.naturalHeight || rect.height;
    let contentLeft = rect.left;
    let contentTop = rect.top;
    let contentWidth = rect.width;
    let contentHeight = rect.height;

    const objectFit = globalThis.getComputedStyle?.(target)?.objectFit || "";
    if (
      target.matches?.(".browser-frame")
      && ["contain", "scale-down"].includes(objectFit)
      && naturalWidth > 0
      && naturalHeight > 0
      && rect.width > 0
      && rect.height > 0
    ) {
      const naturalRatio = naturalWidth / naturalHeight;
      const rectRatio = rect.width / rect.height;
      if (naturalRatio > rectRatio) {
        contentWidth = rect.width;
        contentHeight = rect.width / naturalRatio;
        contentTop = rect.top + (rect.height - contentHeight) / 2;
      } else {
        contentHeight = rect.height;
        contentWidth = rect.height * naturalRatio;
        contentLeft = rect.left + (rect.width - contentWidth) / 2;
      }
    }

    const relativeX = (event.clientX - contentLeft) / Math.max(1, contentWidth);
    const relativeY = (event.clientY - contentTop) / Math.max(1, contentHeight);
    return {
      x: Math.max(0, Math.min(naturalWidth, relativeX * naturalWidth)),
      y: Math.max(0, Math.min(naturalHeight, relativeY * naturalHeight)),
    };
  },

  handleKeydown(event) {
    const annotateShortcut = event?.key === "." && (event.metaKey || event.ctrlKey) && !event.altKey;
    if (annotateShortcut && this._surfaceMounted) {
      event.preventDefault();
      event.stopPropagation?.();
      this.toggleAnnotationMode();
      return;
    }

    if (this.annotating) {
      if (event?.key === "Escape") {
        event.preventDefault();
        if (this.annotationDraft || this.annotationDragRect) {
          this.cancelAnnotationDraft();
        } else {
          this.toggleAnnotationMode(false);
        }
      }
      return;
    }

    void this.sendKey(event);
  },

  handleStageWheel(event) {
    if (this.annotating) return;
    void this.sendWheel(event);
  },

  toggleAnnotationMode(force = null) {
    const nextValue = force === null ? !this.annotating : Boolean(force);
    if (nextValue && !this.canAnnotate()) return;

    this.annotating = nextValue;
    this.annotationError = "";
    this.closeExtensionsMenu();
    if (!nextValue) {
      this.cancelAnnotationDraft();
      this.annotationDragRect = null;
      this._annotationPointer = null;
    } else {
      this._stageElement?.focus?.({ preventScroll: true });
    }
  },

  canAnnotate() {
    return Boolean(this.activeBrowserId && this.frameSrc && !this.isBusy());
  },

  activeAnnotationUrl() {
    return String(this.frameState?.currentUrl || this.address || "about:blank");
  },

  visibleAnnotations() {
    const browserId = this.normalizeBrowserId(this.activeBrowserId);
    const url = this.activeAnnotationUrl();
    return this.annotationComments.filter((annotation) => (
      this.sameBrowserId(annotation.browserId, browserId)
      && String(annotation.url || "") === url
    ));
  },

  nextAnnotationIndex() {
    return this.visibleAnnotations().length + 1;
  },

  clearVisibleAnnotations() {
    this.clearAnnotationsForBrowser(this.activeBrowserId, this.activeAnnotationUrl());
  },

  clearAnnotationsForBrowser(browserId, url = null) {
    const numericBrowserId = this.normalizeBrowserId(browserId);
    if (!numericBrowserId) return;
    this.annotationComments = this.annotationComments.filter((annotation) => {
      if (!this.sameBrowserId(annotation.browserId, numericBrowserId)) return true;
      return url ? String(annotation.url || "") !== String(url) : false;
    });
  },

  annotationBoxStyle(rect = {}) {
    const viewport = this.currentViewportSize() || this._lastViewport || {};
    const width = Math.max(1, Number(viewport.width || rect.width || 1));
    const height = Math.max(1, Number(viewport.height || rect.height || 1));
    const normalized = this.clampAnnotationRect(rect);
    return [
      `left: ${(normalized.x / width) * 100}%`,
      `top: ${(normalized.y / height) * 100}%`,
      `width: ${(Math.max(1, normalized.width) / width) * 100}%`,
      `height: ${(Math.max(1, normalized.height) / height) * 100}%`,
    ].join("; ");
  },

  annotationPopoverStyle() {
    const rect = this.annotationDraft?.rect || this.annotationDragRect || {};
    const viewport = this.currentViewportSize() || this._lastViewport || {};
    const width = Math.max(1, Number(viewport.width || 1));
    const height = Math.max(1, Number(viewport.height || 1));
    const popoverWidth = Math.min(320, Math.max(240, width - 20));
    const popoverHeight = 190;
    const nextLeft = Math.min(
      Math.max(10, Number(rect.x || 0) + Number(rect.width || 0) + 10),
      Math.max(10, width - popoverWidth - 10),
    );
    const nextTop = Math.min(
      Math.max(10, Number(rect.y || 0) + Number(rect.height || 0) + 10),
      Math.max(10, height - popoverHeight - 10),
    );
    return [
      `left: ${(nextLeft / width) * 100}%`,
      `top: ${(nextTop / height) * 100}%`,
      `width: min(${popoverWidth}px, calc(100% - 20px))`,
    ].join("; ");
  },

  annotationDraftTitle() {
    if (!this.annotationDraft) return "Annotation";
    return this.annotationDraft.kind === "area" ? "Area annotation" : "Element annotation";
  },

  stagePointForEvent(event) {
    const image = this._stageElement?.querySelector?.(".browser-frame") || null;
    return this.pointerCoordinatesFor(event, image);
  },

  normalizeAnnotationRect(start = {}, end = {}) {
    const x1 = Number(start.x || 0);
    const y1 = Number(start.y || 0);
    const x2 = Number(end.x || x1);
    const y2 = Number(end.y || y1);
    return this.clampAnnotationRect({
      x: Math.min(x1, x2),
      y: Math.min(y1, y2),
      width: Math.abs(x2 - x1),
      height: Math.abs(y2 - y1),
    });
  },

  clampAnnotationRect(rect = {}) {
    const viewport = this.currentViewportSize() || this._lastViewport || {};
    const viewportWidth = Math.max(1, Number(viewport.width || rect.x + rect.width || 1));
    const viewportHeight = Math.max(1, Number(viewport.height || rect.y + rect.height || 1));
    const x = Math.max(0, Math.min(viewportWidth, Number(rect.x || 0)));
    const y = Math.max(0, Math.min(viewportHeight, Number(rect.y || 0)));
    const width = Math.max(1, Math.min(viewportWidth - x, Number(rect.width || 1)));
    const height = Math.max(1, Math.min(viewportHeight - y, Number(rect.height || 1)));
    return {
      x: Math.round(x),
      y: Math.round(y),
      width: Math.round(width),
      height: Math.round(height),
    };
  },

  startAnnotationSelection(event) {
    if (!this.annotating || this.annotationBusy || !this.canAnnotate()) return;
    const point = this.stagePointForEvent(event);
    if (!point) return;
    this.cancelAnnotationDraft();
    this.annotationError = "";
    this._annotationPointer = {
      id: event.pointerId,
      start: point,
      last: point,
    };
    this.annotationDragRect = this.clampAnnotationRect({
      x: point.x,
      y: point.y,
      width: 1,
      height: 1,
    });
    event.currentTarget?.setPointerCapture?.(event.pointerId);
  },

  moveAnnotationSelection(event) {
    if (!this.annotating || !this._annotationPointer) return;
    if (event.pointerId !== this._annotationPointer.id) return;
    const point = this.stagePointForEvent(event);
    if (!point) return;
    this._annotationPointer.last = point;
    this.annotationDragRect = this.normalizeAnnotationRect(this._annotationPointer.start, point);
  },

  async finishAnnotationSelection(event) {
    if (!this.annotating || !this._annotationPointer) return;
    if (event.pointerId !== this._annotationPointer.id) return;
    const pointer = this._annotationPointer;
    this._annotationPointer = null;
    event.currentTarget?.releasePointerCapture?.(event.pointerId);
    const endPoint = this.stagePointForEvent(event) || pointer.last || pointer.start;
    const rect = this.normalizeAnnotationRect(pointer.start, endPoint);
    this.annotationDragRect = null;
    const isDrag = rect.width >= ANNOTATION_DRAG_THRESHOLD || rect.height >= ANNOTATION_DRAG_THRESHOLD;
    const point = {
      x: Math.round(endPoint.x),
      y: Math.round(endPoint.y),
    };
    const payload = {
      kind: isDrag ? "area" : "element",
      point,
      rect: isDrag ? rect : null,
      viewport: this.currentViewportSize(),
      url: this.activeAnnotationUrl(),
      title: this.activeTitle,
    };
    await this.createAnnotationDraft(payload, isDrag ? rect : {
      x: point.x - 10,
      y: point.y - 10,
      width: 20,
      height: 20,
    });
  },

  cancelAnnotationSelection(event = null) {
    if (event && this._annotationPointer?.id === event.pointerId) {
      event.currentTarget?.releasePointerCapture?.(event.pointerId);
    }
    this._annotationPointer = null;
    this.annotationDragRect = null;
  },

  cancelAnnotationDraft() {
    this.annotationDraft = null;
    this.annotationDraftText = "";
    this.annotationDragRect = null;
  },

  async createAnnotationDraft(payload, fallbackRect) {
    if (!this.activeBrowserId || !this.contextId) return;
    const sequence = this._annotationSequence + 1;
    const browserId = this.activeBrowserId;
    const url = this.activeAnnotationUrl();
    const title = this.activeTitle;
    this._annotationSequence = sequence;
    this.annotationBusy = true;
    this.annotationError = "";
    try {
      const response = await websocket.request(
        "browser_viewer_annotation",
        {
          context_id: this.contextId,
          browser_id: browserId,
          viewer_id: this._viewerToken,
          payload,
        },
        { timeoutMs: 10000 },
      );
      if (sequence !== this._annotationSequence) return;
      const data = firstOk(response);
      const metadata = data.annotation || {};
      this.annotationDraft = {
        id: makeViewerToken(),
        browserId,
        url,
        title,
        kind: metadata.kind || payload.kind,
        rect: this.annotationRectFromMetadata(metadata, fallbackRect),
        metadata,
        createdAt: Date.now(),
      };
      this.annotationDraftText = "";
    } catch (error) {
      this.annotationError = error instanceof Error ? error.message : String(error);
      this.error = this.annotationError;
    } finally {
      if (sequence === this._annotationSequence) {
        this.annotationBusy = false;
      }
    }
  },

  annotationRectFromMetadata(metadata = {}, fallbackRect = {}) {
    const targetRect = metadata?.target?.rect || metadata?.rect || null;
    return this.clampAnnotationRect(targetRect || fallbackRect);
  },

  addAnnotationComment() {
    const comment = String(this.annotationDraftText || "").trim();
    if (!this.annotationDraft || !comment) return;
    if (this.visibleAnnotations().length >= ANNOTATION_MAX_COMMENTS) {
      this.annotationError = `Keep each batch to ${ANNOTATION_MAX_COMMENTS} annotations or fewer.`;
      this.error = this.annotationError;
      return;
    }
    this.annotationComments = [
      ...this.annotationComments,
      {
        ...this.annotationDraft,
        comment,
        index: this.nextAnnotationIndex(),
      },
    ];
    this.cancelAnnotationDraft();
  },

  removeAnnotationComment(annotationId) {
    this.annotationComments = this.annotationComments.filter((annotation) => annotation.id !== annotationId);
  },

  annotationChipLabel(annotation) {
    const prefix = annotation?.kind === "area" ? "Area" : "Element";
    return `${prefix} ${annotation?.index || ""}`.trim();
  },

  formatAnnotationRect(rect = {}) {
    const normalized = this.clampAnnotationRect(rect);
    return `x=${normalized.x}, y=${normalized.y}, width=${normalized.width}, height=${normalized.height}`;
  },

  redactAnnotationText(value) {
    return String(value || "")
      .replace(/(<input\b(?=[^>]*\btype=(["'])?password\2?)[^>]*?)\svalue=(["'])[\s\S]*?\3/giu, "$1 value=\"[redacted]\"")
      .replace(/\b(password|passcode|token|secret|value)=((["'])[\s\S]{1,240}?\3)/giu, "$1=\"[redacted]\"");
  },

  formatAnnotationMetadata(metadata = {}) {
    const lines = [];
    const target = metadata.target || {};
    const selector = target.selector || metadata.selector || "";
    const summary = target.summary || metadata.summary || "";
    const dom = this.redactAnnotationText(target.dom || metadata.dom || "").slice(0, ANNOTATION_DOM_LIMIT);

    if (selector) {
      lines.push(`Selector: ${selector}`);
    }
    if (target.tagName || target.role || target.id || target.name || target.classes) {
      lines.push([
        "Element:",
        target.tagName ? `<${String(target.tagName).toLowerCase()}>` : "",
        target.role ? `role=${target.role}` : "",
        target.id ? `id=${target.id}` : "",
        target.name ? `name=${target.name}` : "",
        target.classes ? `class=${target.classes}` : "",
      ].filter(Boolean).join(" "));
    }
    if (summary) {
      lines.push(`Summary: ${summary}`);
    }
    if (Array.isArray(metadata.elements) && metadata.elements.length) {
      lines.push("Intersecting elements:");
      metadata.elements.slice(0, 8).forEach((element, index) => {
        const elementLabel = [
          `${index + 1}.`,
          element.tagName ? `<${String(element.tagName).toLowerCase()}>` : "",
          element.selector || "",
          element.summary || "",
        ].filter(Boolean).join(" ");
        lines.push(elementLabel);
      });
    }
    if (dom) {
      lines.push(`DOM: ${dom}`);
    }
    return lines.join("\n");
  },

  buildAnnotationsPrompt() {
    const annotations = this.visibleAnnotations();
    if (!annotations.length) return "";
    const lines = [
      "Browser annotations",
      `Page title: ${this.activeTitle}`,
      `Page URL: ${this.activeAnnotationUrl()}`,
      `Browser id: ${this.activeBrowserId}`,
      "",
    ];
    annotations.forEach((annotation, index) => {
      lines.push(
        `Annotation ${index + 1}`,
        `Comment: ${annotation.comment}`,
        `Selection kind: ${annotation.kind}`,
        `Coordinates: ${this.formatAnnotationRect(annotation.rect)}`,
      );
      const metadata = this.formatAnnotationMetadata(annotation.metadata);
      if (metadata) {
        lines.push(metadata);
      }
      lines.push("");
    });
    return lines.join("\n").trim();
  },

  draftAnnotationsToChat() {
    const prompt = this.buildAnnotationsPrompt();
    if (!prompt) return;
    const existingMessage = String(chatInputStore.message || "").trim();
    chatInputStore.message = existingMessage ? `${existingMessage}\n\n${prompt}` : prompt;
    chatInputStore.adjustTextareaHeight?.();
    chatInputStore.focus?.();
    this.clearVisibleAnnotations();
    this.toggleAnnotationMode(false);
  },

  async sendAnnotationsToChat() {
    const prompt = this.buildAnnotationsPrompt();
    if (!prompt) return;
    chatInputStore.message = prompt;
    chatInputStore.adjustTextareaHeight?.();
    try {
      if (typeof chatInputStore.sendMessage === "function") {
        await chatInputStore.sendMessage();
      } else if (typeof globalThis.sendMessage === "function") {
        await globalThis.sendMessage();
      } else {
        chatInputStore.focus?.();
        return;
      }
      this.clearVisibleAnnotations();
      this.toggleAnnotationMode(false);
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    }
  },

  currentViewportSize() {
    const stage = this._stageElement;
    if (!stage) return null;
    const rect = stage.getBoundingClientRect?.();
    const width = Math.round(rect?.width || stage.clientWidth || 0);
    const height = Math.round(rect?.height || stage.clientHeight || 0);
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
    }, force ? 0 : VIEWPORT_SYNC_DEBOUNCE_MS);
  },

	  async syncViewport(force = false) {
	    if (!this.contextId || !this.activeBrowserId) return;
	    const viewport = this.currentViewportSize();
	    if (!viewport) return;
	    const key = `${this.activeBrowserId}:${viewport.width}x${viewport.height}`;
	    if (
	      this._lastViewportKey === key
	      || (
	        !force
	        && this._lastViewport
	        && this.sameBrowserId(this._lastViewport.browserId, this.activeBrowserId)
	        && Math.abs(this._lastViewport.width - viewport.width) <= VIEWPORT_SYNC_SIZE_TOLERANCE
	        && Math.abs(this._lastViewport.height - viewport.height) <= VIEWPORT_SYNC_SIZE_TOLERANCE
	      )
	    ) return;
	    try {
	      await websocket.emit("browser_viewer_input", {
	        context_id: this.contextId,
	        browser_id: this.activeBrowserId,
	        viewer_id: this._viewerToken,
	        input_type: "viewport",
	        width: viewport.width,
	        height: viewport.height,
	      });
	      this._lastViewportKey = key;
	      this._lastViewport = {
	        browserId: this.activeBrowserId,
	        width: viewport.width,
	        height: viewport.height,
	      };
	    } catch (error) {
      this._lastViewportKey = "";
      this._lastViewport = null;
      console.warn("Browser viewport sync failed", error);
    }
  },

  async sendMouse(eventType, event) {
    if (this.annotating) return;
    if (!this.activeBrowserId || !event?.currentTarget) return;
    const pointer = this.pointerCoordinatesFor(event);
    if (!pointer) return;
    const payload = {
      context_id: this.contextId,
      browser_id: this.activeBrowserId,
      viewer_id: this._viewerToken,
      input_type: "mouse",
      event_type: eventType,
      x: pointer.x,
      y: pointer.y,
      button: "left",
    };
    if (eventType === "click") {
      try {
	        const response = await websocket.request("browser_viewer_input", payload, { timeoutMs: 10000 });
	        const data = firstOk(response);
	        this.applyActiveFrameState(data.state);
	        this.applySnapshot(data.snapshot);
	      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error);
      }
      return;
    }
    await websocket.emit("browser_viewer_input", payload);
  },

	  async sendWheel(event) {
    if (!this.activeBrowserId || !event) return;
    const image = event.currentTarget?.querySelector?.(".browser-frame") || event.target?.closest?.(".browser-frame");
    const pointer = this.pointerCoordinatesFor(event, image);
    if (!pointer) return;
    const payload = {
      context_id: this.contextId,
      browser_id: this.activeBrowserId,
      viewer_id: this._viewerToken,
      input_type: "wheel",
      x: pointer.x,
      y: pointer.y,
      delta_x: Number(event.deltaX || 0),
      delta_y: Number(event.deltaY || 0),
    };
	    try {
	      await websocket.emit("browser_viewer_input", payload);
	    } catch (error) {
	      this.error = error instanceof Error ? error.message : String(error);
	    }
	  },

  async sendKey(event) {
    if (this.annotating) return;
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
    this._connectSequence += 1;
    this._viewerToken = "";
    this.switchingBrowserId = null;
    this._surfaceMounted = false;
    this._surfaceSwitching = false;
    this.commandInFlight = false;
    this.annotating = false;
    this.annotationBusy = false;
    this.annotationError = "";
    this.cancelAnnotationDraft();
    this.cancelAnnotationSelection();
    if (this.contextId) {
      try {
        await websocket.emit("browser_viewer_unsubscribe", { context_id: this.contextId });
      } catch {}
    }
    this._frameOff?.();
    this._stateOff?.();
    this._frameOff = null;
    this._stateOff = null;
    this.resetRenderedFrame();
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    this._stageResizeObserver?.disconnect?.();
    this._stageResizeObserver = null;
    this._stageElement = null;
    if (this._viewportSyncTimer) {
      globalThis.clearTimeout(this._viewportSyncTimer);
      this._viewportSyncTimer = null;
    }
    this.resetViewportTracking();
    this.extensionMenuOpen = false;
    this.extensionActionLoading = false;
    this.extensionsListLoading = false;
    this.extensionToggleLoadingPath = "";
    this.modelPresetSaving = false;
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

  setupCanvasSurface(element = null) {
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    this._stageResizeObserver?.disconnect?.();
    const root = element || globalThis.document?.querySelector(".browser-panel");
    const stage = root?.querySelector?.(".browser-stage");
    this._stageElement = stage || null;
    if (stage && globalThis.ResizeObserver) {
      this._stageResizeObserver = new ResizeObserver(() => this.queueViewportSync());
      this._stageResizeObserver.observe(stage);
    }
    globalThis.requestAnimationFrame?.(() => this.queueViewportSync(true));
  },

  get activeTitle() {
    return this.frameState?.title || "Browser";
  },

  get activeUrl() {
    return this.frameState?.currentUrl || this.address || "about:blank";
  },

  loadingMessage() {
    if (this.browserInstallExpected) {
      const cacheDir = this.status?.playwright?.cache_dir || "/a0/usr/plugins/_browser/playwright";
      return `Installing Chromium for the first Browser run. This can take a few minutes; future starts reuse ${cacheDir}.`;
    }
    return "Loading";
  },
};

export const store = createStore("browserPage", model);
