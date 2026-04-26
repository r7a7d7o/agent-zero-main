import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";

const FRAME_NAME_PREFIX = "a0-office-frame";
const COLLABORA_STATE_VERSION = "2026-04-26.1";
const COLLABORA_STATE_MARKER = "a0.office.collaboraStateVersion";
const SERVICE_WORKER_CLEANUP_MARKER = "a0.office.serviceWorkerCleanupReloaded";

function makeFrameName() {
  const id = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${FRAME_NAME_PREFIX}-${id}`;
}

function parseMessage(data) {
  if (typeof data === "string") {
    try {
      return JSON.parse(data);
    } catch {
      return { MessageId: data };
    }
  }
  return data && typeof data === "object" ? data : {};
}

const model = {
  status: null,
  logs: null,
  recent: [],
  session: null,
  loading: false,
  error: "",
  message: "",
  frameReady: false,
  frameName: FRAME_NAME_PREFIX,
  _root: null,
  _messageBound: false,
  _frameTimer: null,
  _frameRecoveryTimer: null,
  _frameAttempt: 0,
  _frameRecoveryTried: false,
  _frameOrigin: "",
  _mode: "canvas",
  _floatingCleanup: null,

  async init(element = null) {
    return await this.onMount(element, { mode: "canvas" });
  },

  async onMount(element = null, options = {}) {
    if (element) this._root = element;
    this.assignFrameName(element);
    globalThis.requestAnimationFrame?.(() => this.assignFrameName(element));
    if (!this._messageBound) {
      globalThis.addEventListener("message", (event) => this.onPostMessage(event));
      this._messageBound = true;
    }
    this._mode = options?.mode === "modal" ? "modal" : "canvas";
    if (this._mode === "modal") {
      this.setupFloatingModal(element);
    } else {
      this.setupCanvasSurface(element);
    }
    await this.refresh();
    if (this.session && this._root) {
      await this.restartFrameLoad();
    }
  },

  async onOpen(payload = {}) {
    await this.refresh();
    if (payload?.path) {
      await this.openPath(payload.path);
    } else if (this.session && !this.frameReady) {
      await this.restartFrameLoad();
    }
  },

  cleanup() {
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    if (this._mode === "modal") {
      this._root = null;
    }
  },

  async refresh() {
    try {
      this.status = await callJsonApi("/plugins/_office/office_session", { action: "status" });
      const recent = await callJsonApi("/plugins/_office/office_session", { action: "recent" });
      this.recent = recent?.documents || [];
      if (!this.status?.healthy) {
        const logs = await callJsonApi("/plugins/_office/collabora_logs", {});
        this.logs = logs;
      }
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    }
  },

  async retry() {
    this.message = "Retrying Collabora setup...";
    this.status = await callJsonApi("/plugins/_office/office_session", { action: "retry" });
  },

  async create(kind = "document") {
    const defaults = {
      document: ["Document", "docx"],
      spreadsheet: ["Spreadsheet", "xlsx"],
      presentation: ["Presentation", "pptx"],
    };
    const [title, format] = defaults[kind] || defaults.document;
    await this.openSession({
      action: "create",
      kind,
      title,
      format,
      content: "",
    });
  },

  async openPrompt() {
    const path = globalThis.prompt?.("Open Office file path", "/a0/usr/workdir/documents/");
    if (!path) return;
    await this.openPath(path);
  },

  async openPath(path) {
    await this.openSession({ action: "open", path, mode: "edit" });
  },

  async openSession(payload) {
    this.loading = true;
    this.error = "";
    this.message = "";
    try {
      await this.prepareBrowserHostForEditor();
      const response = await callJsonApi("/plugins/_office/office_session", payload);
      if (!response?.ok) {
        this.error = response?.error || "Office session could not be opened.";
        if (response?.status) this.status = response.status;
        return;
      }
      this.clearFrameTimers();
      this.session = response;
      this.frameReady = false;
      this._frameOrigin = "";
      this._frameAttempt = 0;
      this._frameRecoveryTried = false;
      await this.submitFrame();
      this.scheduleFrameWatch();
      await this.refresh();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.loading = false;
    }
  },

  async submitFrame() {
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const session = this.session;
    const frame = this.activeFrame();
    if (!session || !frame?.name) return;
    const form = document.createElement("form");
    form.method = "post";
    form.action = this.frameAction(session.iframe_action);
    form.target = frame.name;
    form.style.display = "none";
    const fields = {
      access_token: session.access_token,
      access_token_ttl: String(session.access_token_ttl),
      ui_defaults: "UIMode=notebookbar;TextRuler=false",
    };
    for (const [name, value] of Object.entries(fields)) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
    form.remove();
  },

  async restartFrameLoad() {
    this.frameReady = false;
    this._frameOrigin = "";
    this._frameAttempt = 0;
    this._frameRecoveryTried = false;
    this.clearFrameTimers();
    await this.submitFrame();
    this.scheduleFrameWatch();
  },

  frameAction(action) {
    const url = new URL(action, globalThis.location.origin);
    url.searchParams.set("a0_frame_attempt", String(this._frameAttempt));
    return url.pathname + url.search;
  },

  scheduleFrameWatch() {
    this.clearFrameTimers();
    this._frameTimer = setTimeout(() => {
      if (this.session && !this.frameReady) {
        this.message = "Still opening the editor...";
        this._frameRecoveryTimer = setTimeout(() => this.recoverFrameLoad(), 3000);
      }
    }, 20000);
  },

  async recoverFrameLoad() {
    if (!this.session || this.frameReady || this._frameRecoveryTried) return;
    this._frameRecoveryTried = true;
    this._frameAttempt += 1;
    this.resetCollaboraBrowserState({ force: true });
    this.message = "Still opening the editor... trying a fresh editor load.";
    await this.submitFrame();
    this._frameTimer = setTimeout(() => {
      if (this.session && !this.frameReady) {
        this.message = "Still opening the editor...";
      }
    }, 25000);
  },

  clearFrameTimers() {
    if (this._frameTimer) {
      clearTimeout(this._frameTimer);
      this._frameTimer = null;
    }
    if (this._frameRecoveryTimer) {
      clearTimeout(this._frameRecoveryTimer);
      this._frameRecoveryTimer = null;
    }
  },

  beforeHostHidden() {
    if (this.session) {
      this.save();
    }
    this.frameReady = false;
    this._frameOrigin = "";
    this.clearFrameTimers();
    const frame = this.activeFrame();
    if (frame) {
      frame.src = "about:blank";
    }
  },

  postToFrame(message) {
    const frame = this.activeFrame();
    const targetOrigin = this._frameOrigin || this.session?.post_message_origin || globalThis.location.origin;
    frame?.contentWindow?.postMessage(JSON.stringify(message), targetOrigin);
  },

  save() {
    this.postToFrame({
      MessageId: "Action_Save",
      Values: {
        DontTerminateEdit: true,
        DontSaveIfUnmodified: true,
      },
    });
  },

  closeFile() {
    this.save();
    this.session = null;
    this.frameReady = false;
    this._frameOrigin = "";
    this._frameAttempt = 0;
    this._frameRecoveryTried = false;
    this.clearFrameTimers();
  },

  onPostMessage(event) {
    if (!this.session) return;
    if (!this.isAllowedFrameOrigin(event.origin)) return;
    this._frameOrigin = event.origin;
    const message = parseMessage(event.data);
    const id = message.MessageId || message.messageId || "";
    if (id === "App_LoadingStatus" && message.Values?.Status === "Frame_Ready") {
      this.frameReady = true;
      this.clearFrameTimers();
      if (this.message === "Still opening the editor...") this.message = "";
      if (this.message === "Still opening the editor... trying a fresh editor load.") this.message = "";
      this.postToFrame({ MessageId: "Host_PostmessageReady" });
    } else if (id === "UI_Close") {
      this.session = null;
    } else if (id === "Action_Save_Resp") {
      this.message = message.Values?.success === false ? "Save did not complete." : "Saved";
    }
  },

  isAllowedFrameOrigin(origin) {
    const allowed = new Set([
      globalThis.location.origin,
      this.session?.post_message_origin,
      this.loopbackCounterpart(globalThis.location.origin),
      this.loopbackCounterpart(this.session?.post_message_origin),
    ].filter(Boolean));
    return allowed.has(origin);
  },

  loopbackCounterpart(origin) {
    if (!origin) return "";
    try {
      const url = new URL(origin);
      if (url.hostname === "127.0.0.1") {
        url.hostname = "localhost";
        return url.origin;
      }
      if (url.hostname === "localhost") {
        url.hostname = "127.0.0.1";
        return url.origin;
      }
    } catch {
      return "";
    }
    return "";
  },

  assignFrameName(element = null) {
    const root = element || this._root;
    if (!root) return this.frameName || FRAME_NAME_PREFIX;
    if (!root.dataset.officeFrameName) {
      root.dataset.officeFrameName = makeFrameName();
    }
    const frame = root.querySelector?.("iframe[data-office-frame]");
    if (frame) {
      frame.setAttribute("name", root.dataset.officeFrameName);
      frame.name = root.dataset.officeFrameName;
      try {
        frame.contentWindow.name = root.dataset.officeFrameName;
      } catch {}
    }
    this.frameName = root.dataset.officeFrameName;
    return this.frameName;
  },

  activeFrame() {
    this.assignFrameName();
    return this._root?.querySelector?.("iframe[data-office-frame]") || null;
  },

  setupFloatingModal(element = null) {
    this._floatingCleanup?.();
    const root = element || globalThis.document?.querySelector(".office-panel");
    const modal = root?.closest?.(".modal");
    const inner = modal?.querySelector?.(".modal-inner");
    const body = modal?.querySelector?.(".modal-bd");
    const header = modal?.querySelector?.(".modal-header");
    if (!modal || !inner || !header) return;
    modal.classList.add("modal-floating");
    inner.classList.add("office-modal", "modal-no-backdrop");
    body?.classList?.add("office-modal-body");

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
      const maxWidth = Math.max(340, globalThis.innerWidth - viewportGap * 2);
      const maxHeight = Math.max(360, globalThis.innerHeight - viewportGap * 2);
      if (bounds.width > maxWidth) inner.style.width = `${maxWidth}px`;
      if (bounds.height > maxHeight) inner.style.height = `${maxHeight}px`;
      const next = clampPosition(left, top);
      inner.style.left = `${next.left}px`;
      inner.style.top = `${next.top}px`;
      inner.style.maxWidth = `${Math.max(340, globalThis.innerWidth - next.left - viewportGap)}px`;
      inner.style.maxHeight = `${Math.max(360, globalThis.innerHeight - next.top - viewportGap)}px`;
    };
    clampGeometry();
    globalThis.addEventListener("resize", clampGeometry);
    if (globalThis.ResizeObserver) {
      resizeObserver = new ResizeObserver(clampGeometry);
      resizeObserver.observe(inner);
    }

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
        header.releasePointerCapture?.(header.__officePanelPointerId || 0);
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
      header.__officePanelPointerId = event.pointerId;
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
    };
  },

  setupCanvasSurface(element = null) {
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    if (element) this._root = element;
  },

  async prepareBrowserHostForEditor() {
    await this.cleanupLegacyOfficeServiceWorkers();
    this.resetCollaboraBrowserState();
  },

  async cleanupLegacyOfficeServiceWorkers() {
    const serviceWorker = globalThis.navigator?.serviceWorker;
    if (!serviceWorker?.getRegistrations) return;
    let removedController = false;
    try {
      const registrations = await serviceWorker.getRegistrations();
      const currentOrigin = globalThis.location.origin;
      const officePath = "/office/";
      for (const registration of registrations) {
        const scope = new URL(registration.scope);
        if (scope.origin !== currentOrigin) continue;
        const scopePath = scope.pathname.endsWith("/") ? scope.pathname : `${scope.pathname}/`;
        const affectsOffice = scopePath === "/" || scopePath.startsWith(officePath) || officePath.startsWith(scopePath);
        if (!affectsOffice) continue;
        const scriptUrl = registration.active?.scriptURL || "";
        if (scriptUrl.endsWith("/js/sw.js") && scopePath === "/js/") continue;
        removedController = await registration.unregister() || removedController;
      }
      const controllerUrl = serviceWorker.controller?.scriptURL || "";
      if (removedController && controllerUrl.startsWith(currentOrigin)) {
        const alreadyReloaded = sessionStorage.getItem(SERVICE_WORKER_CLEANUP_MARKER) === "1";
        if (!alreadyReloaded) {
          sessionStorage.setItem(SERVICE_WORKER_CLEANUP_MARKER, "1");
          globalThis.location.reload();
        }
      }
    } catch (error) {
      console.warn("Office service worker cleanup skipped", error);
    }
  },

  resetCollaboraBrowserState(options = {}) {
    const force = Boolean(options.force);
    try {
      if (!force && localStorage.getItem(COLLABORA_STATE_MARKER) === COLLABORA_STATE_VERSION) {
        return;
      }
      const exactKeys = new Set([
        "UIDefaults",
        "WSDFeedbackCount",
        "WSDFeedbackTimestamp",
      ]);
      const collaboraKeyPattern = /^(text|spreadsheet|presentation|drawing)\.[A-Za-z0-9_.-]+$/;
      for (const key of Object.keys(localStorage)) {
        if (exactKeys.has(key) || collaboraKeyPattern.test(key)) {
          localStorage.removeItem(key);
        }
      }
      localStorage.setItem(COLLABORA_STATE_MARKER, COLLABORA_STATE_VERSION);
    } catch (error) {
      console.warn("Office browser state cleanup skipped", error);
    }
  },
};

export const store = createStore("office", model);
