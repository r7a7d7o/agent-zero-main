import { createStore } from "/js/AlpineStore.js";
import { callJsExtensions } from "/js/extensions.js";

const STORAGE_KEY = "a0.rightCanvas";
const DEFAULT_WIDTH = 720;
const MIN_WIDTH = 420;
const MAX_WIDTH = 900;
const DESKTOP_BREAKPOINT = 1200;
const MOBILE_BREAKPOINT = 768;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function viewportWidth() {
  return Math.max(document.documentElement.clientWidth || 0, globalThis.innerWidth || 0);
}

const model = {
  surfaces: [],
  activeSurfaceId: "",
  isOpen: false,
  width: DEFAULT_WIDTH,
  isOverlayMode: false,
  isMobileMode: false,
  _initialized: false,
  _registering: false,
  _rootElement: null,
  _resizeCleanup: null,
  _lastPayloadBySurface: {},

  async init(element = null) {
    if (element) this._rootElement = element;
    if (this._initialized) {
      this.applyLayoutState();
      return;
    }

    this._initialized = true;
    this.restore();
    this.updateLayoutMode();
    this.applyLayoutState();
    globalThis.addEventListener("resize", () => {
      this.updateLayoutMode();
      this.setWidth(this.width, { persist: false });
      this.applyLayoutState();
    });

    if (!this._registering) {
      this._registering = true;
      await callJsExtensions("right_canvas_register_surfaces", this);
      this._registering = false;
      this.ensureActiveSurface();
      if (this.isOpen && this.activeSurfaceId) {
        globalThis.requestAnimationFrame?.(() => {
          void this.open(this.activeSurfaceId, this._lastPayloadBySurface[this.activeSurfaceId] || {});
        });
      }
    }
  },

  registerSurface(surface) {
    if (!surface?.id) return;
    const normalized = {
      title: surface.id,
      icon: "web_asset",
      image: "",
      order: 100,
      canOpen: () => true,
      open: () => {},
      close: () => {},
      modalPath: "",
      actionOnly: false,
      ...surface,
    };

    const index = this.surfaces.findIndex((item) => item.id === normalized.id);
    if (index >= 0) {
      this.surfaces.splice(index, 1, normalized);
    } else {
      this.surfaces.push(normalized);
    }
    this.surfaces.sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
    if (!this._registering) {
      this.ensureActiveSurface();
    }
  },

  ensureActiveSurface() {
    const panelSurfaces = this.panelSurfaces;
    if (!panelSurfaces.length) {
      this.activeSurfaceId = "";
      return;
    }
    if (!panelSurfaces.some((surface) => surface.id === this.activeSurfaceId)) {
      this.activeSurfaceId = panelSurfaces[0].id;
    }
  },

  async open(surfaceId = "", payload = {}) {
    const targetId = surfaceId || this.activeSurfaceId || this.panelSurfaces[0]?.id || "";
    const surface = this.getSurface(targetId);
    if (!surface) {
      return false;
    }
    if (typeof surface.canOpen === "function" && surface.canOpen(payload) === false) {
      return false;
    }

    if (surface.actionOnly) {
      try {
        await surface.open?.(payload || {});
      } catch (error) {
        console.error(`Canvas action ${targetId} failed`, error);
      }
      return true;
    }

    this.activeSurfaceId = targetId;
    this.isOpen = true;
    this._lastPayloadBySurface[targetId] = payload || {};
    this.persist();
    this.applyLayoutState();

    try {
      await surface.open?.(payload || {});
    } catch (error) {
      console.error(`Canvas surface ${targetId} failed to open`, error);
    }
    return true;
  },

  async close() {
    const surface = this.currentSurface();
    this.isOpen = false;
    this.persist();
    this.applyLayoutState();
    try {
      await surface?.close?.(this._lastPayloadBySurface[this.activeSurfaceId] || {});
    } catch (error) {
      console.error(`Canvas surface ${this.activeSurfaceId} failed to close`, error);
    }
  },

  async dockSurface(surfaceId, payload = {}) {
    const surface = this.getSurface(surfaceId);
    if (!surface) {
      return false;
    }
    const modalPath = payload.modalPath || surface.modalPath || "";
    let handoffStarted = false;
    try {
      await surface.beginDockHandoff?.(payload);
      handoffStarted = true;

      const closed = await this.closeDockSourceModal(payload, modalPath);
      if (closed === false) {
        await surface.cancelDockHandoff?.(payload);
        return false;
      }

      const openPayload = { ...payload, source: "modal" };
      delete openPayload.closeSourceModal;
      const opened = await this.open(surfaceId, openPayload);
      await surface.finishDockHandoff?.({ ...openPayload, opened });
      return opened;
    } catch (error) {
      if (handoffStarted) {
        await surface.cancelDockHandoff?.(payload);
      }
      console.error(`Canvas surface ${surfaceId} failed to dock`, error);
      return false;
    }
  },

  async closeDockSourceModal(payload = {}, modalPath = "") {
    if (typeof payload.closeSourceModal === "function") {
      return (await payload.closeSourceModal()) !== false;
    }

    const sourceModalPath = payload.sourceModalPath || modalPath;
    if (sourceModalPath && globalThis.isModalOpen?.(sourceModalPath)) {
      return (await globalThis.closeModal?.(sourceModalPath)) !== false;
    }
    if (modalPath && modalPath !== sourceModalPath && globalThis.isModalOpen?.(modalPath)) {
      return (await globalThis.closeModal?.(modalPath)) !== false;
    }
    return true;
  },

  async undockSurface(surfaceId = "", payload = {}) {
    const targetId = surfaceId || this.activeSurfaceId;
    const surface = this.getSurface(targetId);
    const modalPath = payload.modalPath || surface?.modalPath || "";
    if (!surface || !modalPath) return false;
    if (this.activeSurfaceId === targetId) {
      this.isOpen = false;
      this.persist();
      this.applyLayoutState();
      try {
        await surface.close?.(this._lastPayloadBySurface[targetId] || {});
      } catch (error) {
        console.error(`Canvas surface ${targetId} failed to close while undocking`, error);
      }
    }
    const modalPromise = globalThis.ensureModalOpen?.(modalPath);
    if (modalPromise?.catch) {
      modalPromise.catch((error) => console.error(`Canvas surface ${targetId} failed to undock`, error));
    }
    return true;
  },

  async undockActiveSurface() {
    return await this.undockSurface(this.activeSurfaceId);
  },

  currentSurfaceCanUndock() {
    return Boolean(this.currentSurface()?.modalPath);
  },

  async toggle(surfaceId = "", payload = {}) {
    const targetId = surfaceId || this.activeSurfaceId || this.panelSurfaces[0]?.id || "";
    if (this.isOpen && targetId === this.activeSurfaceId) {
      await this.close();
      return false;
    }
    return await this.open(targetId, payload);
  },

  async toggleCanvas() {
    if (this.isOpen) {
      await this.close();
      return false;
    }
    return await this.open(this.activeSurfaceId || this.panelSurfaces[0]?.id || "");
  },

  setWidth(px, options = {}) {
    const { persist = true } = options;
    const max = this.maxWidth();
    const next = clamp(Number(px) || DEFAULT_WIDTH, MIN_WIDTH, max);
    this.width = next;
    this.applyLayoutState();
    if (persist) this.persist();
  },

  maxWidth() {
    return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(viewportWidth() * 0.58)));
  },

  defaultWidth() {
    return Math.min(DEFAULT_WIDTH, Math.floor(viewportWidth() * 0.45));
  },

  startResize(event) {
    if (this.isOverlayMode || this.isMobileMode || !this.isOpen) return;
    if (event.button !== 0) return;
    event.preventDefault();

    const onPointerMove = (moveEvent) => {
      const nextWidth = viewportWidth() - moveEvent.clientX;
      this.setWidth(nextWidth);
    };
    const onPointerUp = () => {
      globalThis.removeEventListener("pointermove", onPointerMove);
      globalThis.removeEventListener("pointerup", onPointerUp);
      document.body.classList.remove("right-canvas-resizing");
      this.persist();
    };

    document.body.classList.add("right-canvas-resizing");
    globalThis.addEventListener("pointermove", onPointerMove);
    globalThis.addEventListener("pointerup", onPointerUp);
  },

  persist() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          isOpen: this.isOpen,
          activeSurfaceId: this.activeSurfaceId,
          width: this.width,
        }),
      );
    } catch (error) {
      console.warn("Could not persist right canvas state", error);
    }
  },

  restore() {
    this.width = this.defaultWidth();
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      this.isOpen = Boolean(saved.isOpen);
      this.activeSurfaceId = String(saved.activeSurfaceId || "");
      if (saved.width) this.width = Number(saved.width);
    } catch (error) {
      console.warn("Could not restore right canvas state", error);
    }
    this.setWidth(this.width, { persist: false });
  },

  updateLayoutMode() {
    const width = viewportWidth();
    this.isOverlayMode = width < DESKTOP_BREAKPOINT;
    this.isMobileMode = width <= MOBILE_BREAKPOINT;
  },

  applyLayoutState() {
    this.updateLayoutMode();
    document.documentElement.style.setProperty("--right-canvas-width", `${this.width}px`);
    document.body.classList.toggle("right-canvas-open", this.isOpen);
    document.body.classList.toggle("right-canvas-overlay-mode", this.isOverlayMode);
    document.body.classList.toggle("right-canvas-mobile-mode", this.isMobileMode);
  },

  widthStyle() {
    if (this.isMobileMode) return "";
    if (!this.isOpen) return "width: 0;";
    if (this.isOverlayMode) {
      return `width: min(${this.width}px, calc(100vw - 44px));`;
    }
    return `width: ${this.width}px;`;
  },

  getSurface(id) {
    return this.surfaces.find((surface) => surface.id === id) || null;
  },

  get railSurfaces() {
    return this.surfaces;
  },

  get panelSurfaces() {
    return this.surfaces.filter((surface) => !surface.actionOnly);
  },

  currentSurface() {
    return this.getSurface(this.activeSurfaceId);
  },

  isSurfaceActive(id) {
    return this.activeSurfaceId === id;
  },

  activeTitle() {
    return this.currentSurface()?.title || "Canvas";
  },
};

export const store = createStore("rightCanvas", model);
