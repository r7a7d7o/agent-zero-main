import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { getContext } from "/index.js";
import { store as fileBrowserStore } from "/components/modals/file-browser/file-browser-store.js";

const REFRESH_DEBOUNCE_MS = 180;

function lineType(text) {
  if (text.startsWith("@@")) return "hunk";
  if (text.startsWith("+++") || text.startsWith("---") || text.startsWith("diff --git") || text.startsWith("index ")) {
    return "meta";
  }
  if (text.startsWith("+")) return "add";
  if (text.startsWith("-")) return "del";
  if (text.startsWith("\\ No newline")) return "note";
  return "context";
}

function dirname(path) {
  const clean = String(path || "").replace(/\/+$/, "");
  const index = clean.lastIndexOf("/");
  return index > 0 ? clean.slice(0, index) : "";
}

const model = {
  loading: false,
  error: "",
  payload: null,
  contextId: "",
  workspacePath: "",
  expanded: {},
  _root: null,
  _mode: "canvas",
  _refreshTimer: null,
  _floatingCleanup: null,
  _requestSeq: 0,

  async init(element = null) {
    await this.onMount(element, { mode: "canvas" });
  },

  async onMount(element = null, options = {}) {
    if (element) this._root = element;
    this._mode = options?.mode === "modal" ? "modal" : "canvas";
    if (this._mode === "modal") {
      this.setupFloatingModal(element);
    } else {
      this.setupCanvasSurface(element);
    }
    this.contextId = this.resolveContextId();
    if (!this.payload && !this.loading) {
      await this.refresh({ contextId: this.contextId });
    }
  },

  async onOpen(payload = {}) {
    const nextContextId = String(payload.contextId || payload.context_id || this.resolveContextId() || "");
    await this.refresh({ contextId: nextContextId });
  },

  cleanup() {
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    this._floatingCleanup?.();
    this._floatingCleanup = null;
  },

  setupFloatingModal(element = null) {
    this._floatingCleanup?.();
    const root = element || globalThis.document?.querySelector(".diff-viewer-panel");
    const modal = root?.closest?.(".modal");
    const inner = modal?.querySelector?.(".modal-inner");
    const body = modal?.querySelector?.(".modal-bd");
    const header = modal?.querySelector?.(".modal-header");
    if (!modal || !inner || !header) return;
    modal.classList.add("modal-floating");
    inner.classList.add("diff-viewer-modal", "modal-no-backdrop");
    body?.classList?.add("diff-viewer-modal-body");

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
        header.releasePointerCapture?.(header.__diffViewerPanelPointerId || 0);
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
      header.__diffViewerPanelPointerId = event.pointerId;
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

  resolveContextId() {
    const urlContext = new URLSearchParams(globalThis.location?.search || "").get("ctxid");
    return getContext?.() || urlContext || globalThis.Alpine?.store?.("chats")?.selected || "";
  },

  scheduleRefresh(options = {}) {
    if (this._refreshTimer) clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      this.refresh(options).catch((error) => {
        console.error("Diff refresh failed", error);
      });
    }, REFRESH_DEBOUNCE_MS);
  },

  async refresh(options = {}) {
    const contextId = String(options.contextId || options.context_id || this.resolveContextId() || "");
    const seq = ++this._requestSeq;
    this.loading = true;
    this.error = "";
    try {
      const response = await callJsonApi("/plugins/_diff_viewer/diff", { context_id: contextId });
      if (seq !== this._requestSeq) return;
      if (!response?.ok) {
        throw new Error(response?.error || "Could not load diff.");
      }
      this.payload = response;
      this.contextId = String(response.context_id || contextId || "");
      this.workspacePath = String(response.workspace_path || "");
      this.reconcileExpanded();
    } catch (error) {
      if (seq !== this._requestSeq) return;
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      if (seq === this._requestSeq) this.loading = false;
    }
  },

  reconcileExpanded() {
    const next = {};
    let index = 0;
    for (const group of this.visibleGroups()) {
      for (const file of group.files || []) {
        const key = this.fileKey(group, file);
        next[key] = this.expanded[key] ?? index < 4;
        index += 1;
      }
    }
    this.expanded = next;
  },

  visibleGroups() {
    return (this.payload?.groups || []).filter((group) => Array.isArray(group.files) && group.files.length > 0);
  },

  hasChanges() {
    return this.visibleGroups().length > 0;
  },

  groupTitle(kind) {
    const labels = {
      staged: "Staged",
      unstaged: "Unstaged",
      untracked: "Untracked",
    };
    return labels[kind] || kind;
  },

  statusLabel(file) {
    return String(file?.status || "changed").replaceAll("_", " ");
  },

  fileKey(group, file) {
    return `${group?.kind || "diff"}:${file?.old_path || ""}:${file?.path || ""}`;
  },

  isExpanded(group, file) {
    return this.expanded[this.fileKey(group, file)] !== false;
  },

  toggleFile(group, file) {
    const key = this.fileKey(group, file);
    this.expanded[key] = !this.isExpanded(group, file);
  },

  expandAll() {
    const next = {};
    for (const group of this.visibleGroups()) {
      for (const file of group.files || []) {
        next[this.fileKey(group, file)] = true;
      }
    }
    this.expanded = next;
  },

  collapseAll() {
    const next = {};
    for (const group of this.visibleGroups()) {
      for (const file of group.files || []) {
        next[this.fileKey(group, file)] = false;
      }
    }
    this.expanded = next;
  },

  patchLines(file) {
    const patch = String(file?.patch || "");
    if (!patch) return [];
    const textLines = patch.endsWith("\n") ? patch.slice(0, -1).split("\n") : patch.split("\n");
    return textLines.map((text, index) => ({
      id: `${index}-${text.slice(0, 20)}`,
      text,
      type: lineType(text),
    }));
  },

  fileTitle(file) {
    if (file?.old_path && file.old_path !== file.path) {
      return `${file.old_path} -> ${file.path}`;
    }
    return file?.path || file?.old_path || "";
  },

  formatSigned(value, sign) {
    const number = Number(value) || 0;
    return `${sign}${number.toLocaleString()}`;
  },

  fullPath(file) {
    const relativePath = String(file?.path || file?.old_path || "").replace(/^\/+/, "");
    const base = String(this.workspacePath || "").replace(/\/+$/, "");
    return relativePath ? `${base}/${relativePath}` : base;
  },

  async openContainingFolder(file) {
    const parent = dirname(this.fullPath(file));
    await fileBrowserStore.open(parent || this.workspacePath || "$WORK_DIR");
  },

  async copyPath(file) {
    const path = this.fullPath(file);
    try {
      await navigator.clipboard.writeText(path);
      globalThis.justToast?.("Path copied", "success", 1200, "diff-viewer-copy");
    } catch (_error) {
      globalThis.prompt?.("Copy path", path);
    }
  },
};

export const store = createStore("diffViewer", model);
