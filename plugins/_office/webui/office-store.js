import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { getNamespacedClient } from "/js/websocket.js";

const officeSocket = getNamespacedClient("/ws");
officeSocket.addHandlers(["ws_webui"]);

const SAVE_MESSAGE_MS = 1800;
const INPUT_PUSH_DELAY_MS = 650;
const DESKTOP_HEARTBEAT_MS = 3500;
const DESKTOP_RESIZE_DELAY_MS = 80;
const XPRA_DESKTOP_PRIME_INTERVAL_MS = 220;
const XPRA_DESKTOP_PRIME_ATTEMPTS = 120;
const SYSTEM_DESKTOP_FILE_ID = "system-desktop";
const MAX_HISTORY = 80;

function currentContextId() {
  try {
    return globalThis.getContext?.() || "";
  } catch {
    return "";
  }
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let amount = size;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  const digits = amount >= 10 || index === 0 ? 0 : 1;
  return `${amount.toFixed(digits)} ${units[index]}`;
}

function basename(path = "") {
  const value = String(path || "").split("?")[0].split("#")[0];
  return value.split("/").filter(Boolean).pop() || "Untitled";
}

function extensionOf(path = "") {
  const name = basename(path).toLowerCase();
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index + 1) : "";
}

function uniqueTabId(session = {}) {
  return String(session.file_id || session.session_id || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value = "") {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function markdownToHtml(markdown = "") {
  const normalized = String(markdown || "").replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  const html = [];
  let paragraph = [];
  let list = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    html.push(`<ul>${list.map((line) => `<li>${inlineMarkdown(line)}</li>`).join("")}</ul>`);
    list = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(4, heading[1].length);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  if (!html.length || /\n\s*$/.test(normalized)) {
    html.push("<p><br></p>");
  }
  return html.join("") || "<p></p>";
}

function htmlToMarkdown(root) {
  if (!root) return "";

  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const tag = node.tagName.toLowerCase();
    const childText = () => Array.from(node.childNodes).map(walk).join("");

    if (tag === "br") return "\n";
    if (tag === "strong" || tag === "b") return `**${childText().trim()}**`;
    if (tag === "em" || tag === "i") return `*${childText().trim()}*`;
    if (tag === "code") return `\`${childText().trim()}\``;
    if (tag === "a") {
      const href = node.getAttribute("href") || "";
      const label = childText().trim() || href;
      return href ? `[${label}](${href})` : label;
    }
    if (/^h[1-6]$/.test(tag)) return `\n${"#".repeat(Number(tag[1]))} ${childText().trim()}\n\n`;
    if (tag === "li") return `- ${childText().trim()}\n`;
    if (tag === "ul" || tag === "ol") return `\n${childText()}\n`;
    if (tag === "tr") {
      const cells = Array.from(node.children).map((cell) => cell.textContent?.trim() || "");
      return `| ${cells.join(" | ")} |\n`;
    }
    if (tag === "table") return `\n${Array.from(node.querySelectorAll("tr")).map(walk).join("")}\n`;
    if (tag === "p" || tag === "div" || tag === "section" || tag === "article") {
      const text = childText().trim();
      return text ? `${text}\n\n` : "";
    }
    return childText();
  };

  return Array.from(root.childNodes)
    .map(walk)
    .join("")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}

function textToPageHtml(text = "") {
  const paragraphs = String(text || "")
    .replace(/\r\n?/g, "\n")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const lines = paragraphs.length ? paragraphs : [""];
  const pages = [];
  for (let index = 0; index < lines.length; index += 18) {
    pages.push(lines.slice(index, index + 18));
  }
  return pages
    .map((page, index) => (
      `<section class="office-docx-page" data-page="${index + 1}">`
      + page.map((line) => `<p>${escapeHtml(line)}</p>`).join("")
      + "</section>"
    ))
    .join("");
}

function nativeTilesToHtml(tiles = []) {
  return tiles
    .filter((tile) => tile?.image)
    .map((tile) => {
      const twips = encodeURIComponent(JSON.stringify(tile.twips || {}));
      const width = Number(tile.width || 1);
      const height = Number(tile.height || 1);
      return (
        `<section class="office-docx-page is-native-tile" data-tile-index="${Number(tile.index || 0)}" data-twips="${twips}">`
        + `<img src="${escapeHtml(tile.image)}" width="${width}" height="${height}" alt="" draggable="false">`
        + "</section>"
      );
    })
    .join("");
}

function docxEditorText(element) {
  if (!element) return "";
  const pages = Array.from(element.querySelectorAll(".office-docx-page"));
  if (!pages.length) return element.innerText || "";
  return pages
    .map((page) => Array.from(page.querySelectorAll("p"))
      .map((p) => p.innerText.trim())
      .filter(Boolean)
      .join("\n"))
    .filter(Boolean)
    .join("\n\n");
}

function editorContainsFocus(element) {
  const active = document.activeElement;
  return Boolean(element && active && (element === active || element.contains(active)));
}

function isEditableInputTarget(target) {
  const element = target?.nodeType === 1 ? target : target?.parentElement;
  const editable = element?.closest?.("input, textarea, select, [contenteditable='true'], [contenteditable=''], [role='textbox']");
  if (!editable) return false;
  if (editable.tagName !== "INPUT") return true;
  const type = String(editable.getAttribute("type") || "text").toLowerCase();
  return !["button", "checkbox", "color", "file", "image", "radio", "range", "reset", "submit"].includes(type);
}

function placeCaretAtEnd(element) {
  if (!element) return;
  if (element.tagName === "TEXTAREA" || element.tagName === "INPUT") {
    const length = element.value?.length || 0;
    element.selectionStart = length;
    element.selectionEnd = length;
    return;
  }
  const selection = globalThis.getSelection?.();
  const range = document.createRange?.();
  if (!selection || !range) return;
  range.selectNodeContents(element);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

function normalizeDocument(doc = {}) {
  const path = doc.path || "";
  const extension = String(doc.extension || extensionOf(path)).toLowerCase();
  return {
    ...doc,
    extension,
    title: doc.title || doc.basename || basename(path),
    basename: doc.basename || basename(path),
    path,
  };
}

function normalizeSession(payload = {}) {
  const document = normalizeDocument(payload.document || payload);
  const extension = String(payload.extension || document.extension || "").toLowerCase();
  return {
    ...payload,
    document,
    extension,
    file_id: payload.file_id || document.file_id || "",
    path: document.path || payload.path || "",
    title: payload.title || document.title || document.basename || basename(document.path),
    tab_id: uniqueTabId(payload),
    text: String(payload.text || ""),
    tiles: Array.isArray(payload.tiles) ? payload.tiles : [],
    preview: payload.preview || document.preview || {},
    native: payload.native || {},
    desktop: payload.desktop || null,
    desktop_session_id: payload.desktop_session_id || payload.desktop?.session_id || "",
    dirty: false,
  };
}

async function callOffice(action, payload = {}) {
  return await callJsonApi("/plugins/_office/office_session", {
    action,
    ctxid: currentContextId(),
    ...payload,
  });
}

async function requestOffice(eventType, payload = {}, timeoutMs = 5000) {
  const response = await officeSocket.request(eventType, {
    ctxid: currentContextId(),
    ...payload,
  }, { timeoutMs });
  const results = Array.isArray(response?.results) ? response.results : [];
  const first = results.find((item) => item?.ok === true && isOfficeSocketData(item?.data))
    || results.find((item) => item?.ok === true);
  if (!first) {
    const error = results.find((item) => item?.error)?.error;
    throw new Error(error?.error || error?.code || `${eventType} failed`);
  }
  if (first.data?.office_error) {
    const error = first.data.office_error;
    throw new Error(error.error || error.code || `${eventType} failed`);
  }
  return first.data || {};
}

function isOfficeSocketData(data) {
  if (!data || typeof data !== "object") return false;
  return (
    Object.prototype.hasOwnProperty.call(data, "office_error")
    || Object.prototype.hasOwnProperty.call(data, "ok")
    || Object.prototype.hasOwnProperty.call(data, "session_id")
    || Object.prototype.hasOwnProperty.call(data, "document")
    || Object.prototype.hasOwnProperty.call(data, "tiles")
    || Object.prototype.hasOwnProperty.call(data, "native")
    || Object.prototype.hasOwnProperty.call(data, "desktop")
    || Object.prototype.hasOwnProperty.call(data, "closed")
  );
}

const model = {
  status: null,
  recent: [],
  openDocuments: [],
  tabs: [],
  activeTabId: "",
  session: null,
  loading: false,
  saving: false,
  dirty: false,
  error: "",
  message: "",
  sourceMode: false,
  editorText: "",
  zoom: 1,
  _root: null,
  _mode: "canvas",
  _saveMessageTimer: null,
  _inputTimer: null,
  _history: [],
  _historyIndex: -1,
  _rendering: false,
  _pendingFocus: false,
  _pendingFocusEnd: true,
  _focusAttempts: 0,
  _richEditor: null,
  _docxEditor: null,
  _nativeEventQueue: Promise.resolve(),
  _floatingCleanup: null,
  _desktopHeartbeatTimer: null,
  _desktopHeartbeatSessionId: "",
  _desktopHeartbeatTabId: "",
  _desktopHeartbeatMisses: 0,
  _desktopResizeCleanup: null,
  _desktopResizeTarget: null,
  _desktopResizeTimer: null,
  _desktopResizeKey: "",
  _desktopResizeSuspended: false,
  _desktopResizePending: false,
  _desktopPrimeTimer: null,
  _desktopPrimeAttempts: 0,
  _desktopKeyboardActive: false,
  _desktopKeyboardCleanup: null,
  _desktopClipboardCleanup: null,
  _desktopStarting: null,

  async init(element = null) {
    return await this.onMount(element, { mode: "canvas" });
  },

  async onMount(element = null, options = {}) {
    if (element) this._root = element;
    this._mode = options?.mode === "modal" ? "modal" : "canvas";
    if (this._mode === "modal") this.setupFloatingModal(element);
    await this.refresh();
    await this.ensureDesktopSession({ select: !this.session });
    this.ensureActiveTab();
    this.queueRender();
  },

  async onOpen(payload = {}) {
    await this.refresh();
    if (payload?.path || payload?.file_id) {
      await this.openSession({
        path: payload.path || "",
        file_id: payload.file_id || "",
      });
    } else {
      await this.ensureDesktopSession({ select: !this.session });
    }
    this.restoreDesktopFrames();
    this.requestDesktopViewportSync({ force: true });
  },

  beforeHostHidden(options = {}) {
    this.flushInput();
    this.unloadDesktopFrames();
  },

  cleanup() {
    this.flushInput();
    this.stopDesktopMonitor();
    this.stopDesktopResizeObserver();
    this.stopXpraDesktopPrime();
    this.stopDesktopKeyboardBridge();
    this.stopDesktopClipboardBridge();
    this._floatingCleanup?.();
    this._floatingCleanup = null;
    if (this._mode === "modal") this._root = null;
  },

  bindEditorElement(element, type) {
    if (type === "markdown") this._richEditor = element;
    if (type === "docx") this._docxEditor = element;
    this.queueRender();
  },

  async refresh() {
    try {
      const [status, recent, openDocuments] = await Promise.all([
        callOffice("status"),
        callOffice("recent"),
        callOffice("open_documents"),
      ]);
      this.status = status || {};
      this.recent = (recent?.documents || []).map(normalizeDocument);
      this.openDocuments = (openDocuments?.documents || []).map(normalizeDocument);
      this.error = "";
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    }
  },

  async ensureDesktopSession(options = {}) {
    const existing = this.tabs.find((tab) => this.isDesktopSession(tab));
    if (existing && !options.force) {
      if (options.select) this.selectTab(existing.tab_id, { focus: false });
      this.updateDesktopMonitor();
      return existing;
    }
    if (this._desktopStarting) return await this._desktopStarting;

    this._desktopStarting = (async () => {
      try {
        const response = await callOffice("desktop");
        if (response?.ok === false) throw new Error(response.error || "Desktop session could not be opened.");
        const session = normalizeSession(response);
        const existingIndex = this.tabs.findIndex((tab) => this.isDesktopSession(tab));
        let desktopTabId = session.tab_id;
        if (existingIndex >= 0) {
          desktopTabId = this.tabs[existingIndex].tab_id;
          this.tabs.splice(existingIndex, 1, { ...this.tabs[existingIndex], ...session, tab_id: desktopTabId });
        } else {
          this.tabs.unshift(session);
        }
        this.tabs = this.tabs.map((tab) => (
          this.hasOfficialOffice(tab)
            ? {
              ...tab,
              desktop: session.desktop,
              desktop_session_id: session.desktop_session_id,
              session_id: this.isDesktopSession(tab) ? session.session_id : tab.session_id,
            }
            : tab
        ));
        if (options.select || !this.session) {
          this.selectTab(desktopTabId, { focus: false });
        } else {
          this.updateDesktopMonitor();
        }
        return { ...session, tab_id: desktopTabId };
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error);
        return null;
      } finally {
        this._desktopStarting = null;
      }
    })();
    return await this._desktopStarting;
  },

  async create(kind = "document", format = "") {
    const fmt = String(format || (kind === "spreadsheet" ? "xlsx" : kind === "presentation" ? "pptx" : "md")).toLowerCase();
    const title = this.defaultTitle(kind, fmt);
    await this.openSession({
      action: "create",
      kind,
      format: fmt,
      title,
    });
  },

  async openPrompt() {
    let defaultPath = "/a0/usr/workdir/";
    try {
      const home = await callOffice("home");
      defaultPath = home?.path || defaultPath;
    } catch {
      // The prompt still works with the static fallback.
    }
    const path = globalThis.prompt?.("Path", defaultPath);
    if (!path) return;
    await this.openPath(path);
  },

  async openPath(path) {
    await this.openSession({ path: String(path || "") });
  },

  async openSession(payload = {}) {
    this.loading = true;
    this.error = "";
    try {
      const response = await callOffice(payload.action || "open", payload);
      if (response?.ok === false) {
        this.error = response.error || "Document could not be opened.";
        return null;
      }
      const session = normalizeSession(response);
      this.installSession(session);
      await this.refresh();
      return session;
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
      return null;
    } finally {
      this.loading = false;
    }
  },

  installSession(session) {
    if (this.isDesktopOfficeDocument(session)) {
      this.installDesktopDocumentSession(session);
      return;
    }
    const existingIndex = this.tabs.findIndex((tab) => (
      (session.file_id && tab.file_id === session.file_id)
      || (session.path && tab.path === session.path)
    ));
    if (existingIndex >= 0) {
      this.tabs.splice(existingIndex, 1, { ...this.tabs[existingIndex], ...session, tab_id: this.tabs[existingIndex].tab_id });
      this.activeTabId = this.tabs[existingIndex].tab_id;
    } else {
      this.tabs.push(session);
      this.activeTabId = session.tab_id;
    }
    this.selectTab(this.activeTabId);
  },

  installDesktopDocumentSession(session) {
    this.tabs = this.tabs.filter((tab) => this.isVisibleOfficeTab(tab));
    let desktopTab = this.tabs.find((tab) => this.isDesktopSession(tab));
    if (!desktopTab) {
      desktopTab = {
        ...session,
        tab_id: SYSTEM_DESKTOP_FILE_ID,
        file_id: SYSTEM_DESKTOP_FILE_ID,
        extension: "desktop",
        title: "Desktop",
        path: session.desktop?.desktop_path || "/desktop/session",
        mode: "desktop",
        document: {
          file_id: SYSTEM_DESKTOP_FILE_ID,
          path: session.desktop?.desktop_path || "/desktop/session",
          basename: "Desktop",
          title: "Desktop",
          extension: "desktop",
          preview: {},
        },
        dirty: false,
      };
      this.tabs.unshift(desktopTab);
    }
    const desktopTabId = desktopTab.tab_id;
    this.session = { ...session, tab_id: session.tab_id || uniqueTabId(session) };
    this.activeTabId = desktopTabId;
    this.sourceMode = false;
    this.editorText = "";
    this.dirty = false;
    this.resetHistory("");
    this.queueRender({ focus: true });
    this.updateDesktopMonitor();
  },

  selectTab(tabId, options = {}) {
    const tab = this.tabs.find((item) => item.tab_id === tabId) || this.tabs[0] || null;
    this.session = tab;
    this.activeTabId = tab?.tab_id || "";
    this.sourceMode = false;
    this.editorText = String(tab?.text || "");
    this.dirty = Boolean(tab?.dirty);
    this.resetHistory(this.editorText);
    this.queueRender({ focus: Boolean(tab) && options.focus !== false });
    this.updateDesktopMonitor();
  },

  ensureActiveTab() {
    if (this.session && this.tabs.some((tab) => tab.tab_id === this.session.tab_id)) return;
    if (this.tabs.length) this.selectTab(this.tabs[0].tab_id, { focus: false });
  },

  isActiveTab(tab) {
    return Boolean(tab && tab.tab_id === this.activeTabId);
  },

  async closeFile() {
    if (!this.session) return;
    if (this.isDesktopOfficeDocument(this.session) && !this.tabs.some((tab) => tab.tab_id === this.session.tab_id)) {
      await this.closeDesktopDocumentSession(this.session);
      return;
    }
    await this.closeTab(this.session.tab_id);
  },

  async closeDesktopDocumentSession(session) {
    try {
      await callOffice("desktop_save", {
        desktop_session_id: session.desktop_session_id || session.session_id,
        file_id: session.file_id || "",
      }).catch(() => null);
      await callOffice("close", {
        session_id: session.store_session_id || "",
        file_id: session.file_id || "",
      });
    } catch (error) {
      console.warn("Desktop document close skipped", error);
    }
    this.session = null;
    this.activeTabId = "";
    this.editorText = "";
    this.dirty = false;
    const desktopTab = this.tabs.find((tab) => this.isDesktopSession(tab));
    if (desktopTab) {
      this.selectTab(desktopTab.tab_id, { focus: false });
    } else {
      await this.ensureDesktopSession({ select: true });
    }
    await this.refresh();
  },

  async closeTab(tabId) {
    const tab = this.tabs.find((item) => item.tab_id === tabId);
    if (!tab) return;
    if (this.isDesktopSession(tab)) {
      this.selectTab(tab.tab_id, { focus: false });
      return;
    }
    if (!this.hasOfficialOffice(tab) && (tab.dirty || (this.isActiveTab(tab) && this.dirty))) {
      const shouldSave = globalThis.confirm?.("Save changes?") ?? true;
      if (shouldSave) await this.save();
    }
    try {
      if (this.hasOfficialOffice(tab)) {
        await callOffice("desktop_save", {
          desktop_session_id: tab.desktop_session_id || tab.session_id,
          file_id: tab.file_id || "",
        }).catch(() => null);
      } else if (tab.session_id) {
        await requestOffice("office_close", { session_id: tab.session_id }, 2500).catch(() => null);
      }
      await callOffice("close", {
        session_id: tab.store_session_id || "",
        file_id: tab.file_id || "",
      });
    } catch (error) {
      console.warn("Document close skipped", error);
    }
    this.tabs = this.tabs.filter((item) => item.tab_id !== tabId);
    if (this.activeTabId === tabId) {
      this.session = null;
      this.activeTabId = "";
      this.editorText = "";
      this.dirty = false;
      this.ensureActiveTab();
    }
    this.updateDesktopMonitor();
    await this.ensureDesktopSession({ select: !this.session });
    await this.refresh();
  },

  async save() {
    if (!this.session || this.saving) return;
    if (this.isDesktopSession()) return;
    if (this.hasOfficialOffice()) {
      this.saving = true;
      this.error = "";
      try {
        const response = await callOffice("desktop_save", {
          desktop_session_id: this.session.desktop_session_id || this.session.session_id,
          file_id: this.session.file_id || "",
        });
        if (response?.ok === false) throw new Error(response.error || "Save failed.");
        const document = normalizeDocument(response.document || this.session.document || {});
        const updated = {
          ...this.session,
          dirty: false,
          document,
          path: document.path || this.session.path,
          file_id: document.file_id || this.session.file_id,
          version: document.version || response.version || this.session.version,
        };
        this.replaceActiveSession(updated);
        this.dirty = false;
        this.setMessage("Saved");
        await this.refresh();
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error);
      } finally {
        this.saving = false;
      }
      return;
    }
    if (this.hasNativeDocxTiles()) await this.awaitNativeEvents();
    if (!this.hasNativeDocxTiles()) this.syncEditorText();
    this.saving = true;
    this.error = "";
    try {
      let response;
      const payload = { session_id: this.session.session_id };
      if (!this.hasNativeDocxTiles()) payload.text = this.editorText;
      try {
        response = await requestOffice("office_save", payload, 10000);
      } catch (_socketError) {
        response = await callOffice("save", payload);
      }
      if (response?.ok === false) throw new Error(response.error || "Save failed.");
      const document = normalizeDocument(response.document || this.session.document || {});
      const updated = {
        ...this.session,
        text: this.editorText,
        dirty: false,
        document,
        path: document.path || this.session.path,
        file_id: document.file_id || this.session.file_id,
        tiles: Array.isArray(response.tiles) ? response.tiles : this.session.tiles,
        native: response.native || this.session.native || {},
        version: document.version || response.version || this.session.version,
      };
      this.replaceActiveSession(updated);
      this.dirty = false;
      this.setMessage("Saved");
      await this.refresh();
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.saving = false;
    }
  },

  async exportPdf() {
    if (!this.session) return;
    if (this.isDesktopSession()) return;
    this.loading = true;
    this.error = "";
    try {
      const response = await callOffice("export", {
        file_id: this.session.file_id,
        path: this.session.path,
        target_format: "pdf",
      });
      if (response?.ok === false) throw new Error(response.error || "Export failed.");
      this.setMessage(response.path ? `Exported ${response.path}` : "Exported");
    } catch (error) {
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      this.loading = false;
    }
  },

  replaceActiveSession(next) {
    if (!this.session) return;
    this.session = next;
    const index = this.tabs.findIndex((tab) => tab.tab_id === next.tab_id);
    if (index >= 0 && this.isVisibleOfficeTab(next)) this.tabs.splice(index, 1, next);
    this.queueRender();
    this.updateDesktopMonitor();
  },

  setMessage(value) {
    this.message = value;
    if (this._saveMessageTimer) globalThis.clearTimeout(this._saveMessageTimer);
    this._saveMessageTimer = globalThis.setTimeout(() => {
      this.message = "";
      this._saveMessageTimer = null;
    }, SAVE_MESSAGE_MS);
  },

  resetHistory(text) {
    this._history = [String(text || "")];
    this._historyIndex = 0;
  },

  pushHistory(text) {
    const value = String(text || "");
    if (this._history[this._historyIndex] === value) return;
    this._history = this._history.slice(0, this._historyIndex + 1);
    this._history.push(value);
    if (this._history.length > MAX_HISTORY) this._history.shift();
    this._historyIndex = this._history.length - 1;
  },

  undo() {
    if (this._historyIndex <= 0) return;
    this._historyIndex -= 1;
    this.applyEditorText(this._history[this._historyIndex], true);
  },

  redo() {
    if (this._historyIndex >= this._history.length - 1) return;
    this._historyIndex += 1;
    this.applyEditorText(this._history[this._historyIndex], true);
  },

  canUndo() {
    return this._historyIndex > 0;
  },

  canRedo() {
    return this._historyIndex < this._history.length - 1;
  },

  applyEditorText(text, markDirty = false) {
    this.editorText = String(text || "");
    if (this.session) {
      this.session.text = this.editorText;
      this.session.dirty = markDirty || this.session.dirty;
    }
    if (markDirty) this.markDirty();
    this.queueRender({ force: true, focus: true });
  },

  markDirty() {
    this.dirty = true;
    if (this.session) this.session.dirty = true;
  },

  onSourceInput() {
    this.markDirty();
    this.pushHistory(this.editorText);
    this.scheduleInputPush();
  },

  onRichInput(element) {
    if (this._rendering) return;
    this.editorText = htmlToMarkdown(element);
    this.markDirty();
    this.pushHistory(this.editorText);
    this.scheduleInputPush();
  },

  onDocxInput(element) {
    if (this.hasNativeDocxTiles()) return;
    if (this._rendering) return;
    this.editorText = docxEditorText(element);
    this.markDirty();
    this.pushHistory(this.editorText);
    this.scheduleInputPush();
  },

  syncEditorText() {
    if (!this.session) return;
    if (this.hasOfficialOffice()) return;
    if (this.hasNativeDocxTiles()) return;
    if (this.isMarkdown() && !this.sourceMode && this._richEditor) {
      this.editorText = htmlToMarkdown(this._richEditor);
    } else if (this.isDocx() && this._docxEditor) {
      this.editorText = docxEditorText(this._docxEditor);
    }
    this.session.text = this.editorText;
  },

  scheduleInputPush() {
    if (!this.session?.session_id) return;
    if (this._inputTimer) globalThis.clearTimeout(this._inputTimer);
    this._inputTimer = globalThis.setTimeout(() => {
      this._inputTimer = null;
      this.flushInput();
    }, INPUT_PUSH_DELAY_MS);
  },

  flushInput() {
    if (!this.session?.session_id) return;
    if (this.hasOfficialOffice()) return;
    this.syncEditorText();
    requestOffice("office_input", {
      session_id: this.session.session_id,
      text: this.editorText,
    }, 3000).catch(() => {});
  },

  toggleSource() {
    if (!this.isMarkdown()) return;
    if (!this.sourceMode) this.syncEditorText();
    this.sourceMode = !this.sourceMode;
    this.queueRender({ force: true, focus: true });
  },

  format(command) {
    if (!this.session) return;
    if (this.sourceMode) {
      this.applySourceFormat(command);
      return;
    }
    const editor = this.isDocx() ? this._docxEditor : this._richEditor;
    editor?.focus?.();
    const uno = this.unoCommand(command);
    if (this.isDocx() && uno) {
      void this.dispatchUnoCommand(uno.command, uno.arguments);
      if (this.hasNativeDocxTiles()) {
        this.markDirty();
        return;
      }
    }
    if (command === "bold") document.execCommand?.("bold");
    if (command === "italic") document.execCommand?.("italic");
    if (command === "underline") document.execCommand?.("underline");
    if (command === "list") document.execCommand?.("insertUnorderedList");
    if (command === "numbered") document.execCommand?.("insertOrderedList");
    if (command === "alignLeft") document.execCommand?.("justifyLeft");
    if (command === "alignCenter") document.execCommand?.("justifyCenter");
    if (command === "alignRight") document.execCommand?.("justifyRight");
    if (command === "table") {
      document.execCommand?.(
        "insertHTML",
        false,
        '<table><tbody><tr><th>Column</th><th>Value</th></tr><tr><td></td><td></td></tr></tbody></table>',
      );
    }
    this.syncEditorText();
    this.markDirty();
    this.pushHistory(this.editorText);
    this.scheduleInputPush();
  },

  unoCommand(command) {
    const commands = {
      bold: { command: ".uno:Bold" },
      italic: { command: ".uno:Italic" },
      underline: { command: ".uno:Underline" },
      list: { command: ".uno:DefaultBullet" },
      numbered: { command: ".uno:DefaultNumbering" },
      alignLeft: { command: ".uno:LeftPara" },
      alignCenter: { command: ".uno:CenterPara" },
      alignRight: { command: ".uno:RightPara" },
    };
    return commands[command] || null;
  },

  async dispatchUnoCommand(command, argumentsPayload = null) {
    if (!this.session?.session_id || !command) return null;
    return await this.queueNativeEvent(async () => {
      try {
        let response;
        try {
          response = await requestOffice("office_command", {
            session_id: this.session.session_id,
            command,
            arguments: argumentsPayload,
            notify: true,
          }, 5000);
        } catch (_socketError) {
          response = await callOffice("command", {
            session_id: this.session.session_id,
            command,
            arguments: argumentsPayload,
            notify: true,
          });
        }
        if (response?.ok === false) throw new Error(response.error || `${command} failed.`);
        if (response?.metadata && this.session) {
          this.session.native = { ...(this.session.native || {}), ...response.metadata, available: true };
        }
        if (Array.isArray(response?.tiles) && this.session) {
          this.session.tiles = response.tiles;
          this.queueRender({ force: true, focus: true });
        }
        return response;
      } catch (error) {
        console.warn("LibreOffice command skipped", command, error);
        return null;
      }
    });
  },

  applySourceFormat(command) {
    const textarea = this._root?.querySelector?.("[data-office-source]");
    if (!textarea) return;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || start;
    const selected = this.editorText.slice(start, end);
    let replacement = selected;
    if (command === "bold") replacement = `**${selected || "text"}**`;
    if (command === "italic") replacement = `*${selected || "text"}*`;
    if (command === "list") replacement = (selected || "item").split("\n").map((line) => `- ${line.replace(/^[-*]\s+/, "")}`).join("\n");
    if (command === "numbered") replacement = (selected || "item").split("\n").map((line, index) => `${index + 1}. ${line.replace(/^\d+\.\s+/, "")}`).join("\n");
    if (command === "table") replacement = "| Column | Value |\n| --- | --- |\n|  |  |";
    if (replacement === selected) return;
    this.editorText = `${this.editorText.slice(0, start)}${replacement}${this.editorText.slice(end)}`;
    this.onSourceInput();
    globalThis.requestAnimationFrame?.(() => {
      textarea.focus();
      textarea.selectionStart = start;
      textarea.selectionEnd = start + replacement.length;
    });
  },

  zoomIn() {
    this.zoom = Math.min(1.6, Math.round((this.zoom + 0.1) * 10) / 10);
  },

  zoomOut() {
    this.zoom = Math.max(0.7, Math.round((this.zoom - 0.1) * 10) / 10);
  },

  zoomLabel() {
    return `${Math.round(this.zoom * 100)}%`;
  },

  queueRender(options = {}) {
    const force = Boolean(options.force);
    if (options.focus) {
      this._pendingFocus = true;
      this._pendingFocusEnd = options.end !== false;
      this._focusAttempts = 0;
    }
    const render = () => {
      this.renderEditors(force);
      if (this._pendingFocus && this.focusEditor({ end: this._pendingFocusEnd })) {
        this._pendingFocus = false;
        this._focusAttempts = 0;
      } else if (this._pendingFocus && this._focusAttempts < 6) {
        this._focusAttempts += 1;
        globalThis.setTimeout(render, 45);
      }
    };
    if (globalThis.requestAnimationFrame) {
      globalThis.requestAnimationFrame(render);
    } else {
      globalThis.setTimeout(render, 0);
    }
  },

  renderEditors(force = false) {
    if (!this.session) return;
    if (this.hasOfficialOffice()) return;
    this._rendering = true;
    try {
      if (this._richEditor && this.isMarkdown() && (!editorContainsFocus(this._richEditor) || force)) {
        this._richEditor.innerHTML = markdownToHtml(this.editorText);
      }
      if (this._docxEditor && this.isDocx() && this.hasNativeDocxTiles() && (!editorContainsFocus(this._docxEditor) || force)) {
        this._docxEditor.innerHTML = nativeTilesToHtml(this.session.tiles || []);
      } else if (this._docxEditor && this.isDocx() && (!editorContainsFocus(this._docxEditor) || force)) {
        this._docxEditor.innerHTML = textToPageHtml(this.editorText);
      }
    } finally {
      this._rendering = false;
    }
  },

  focusEditor(options = {}) {
    if (!this.session || this.isPreviewOnly()) return false;
    if (this.hasOfficialOffice()) {
      return this.focusDesktopFrame(this.desktopFrame(), { arm: true });
    }
    const source = this._root?.querySelector?.("[data-office-source]");
    const editor = this.sourceMode ? source : (this.isDocx() ? this._docxEditor : this._richEditor);
    if (!editor) return false;
    editor.focus?.({ preventScroll: true });
    if (!editorContainsFocus(editor)) return false;
    if (options.end !== false) placeCaretAtEnd(editor);
    return true;
  },

  isMarkdown(tab = this.session) {
    const ext = String(tab?.extension || tab?.document?.extension || "").toLowerCase();
    return ext === "md";
  },

  isDocx(tab = this.session) {
    const ext = String(tab?.extension || tab?.document?.extension || "").toLowerCase();
    return ext === "docx";
  },

  isBinaryOffice(tab = this.session) {
    const ext = String(tab?.extension || tab?.document?.extension || "").toLowerCase();
    return ext === "docx" || ext === "xlsx" || ext === "pptx";
  },

  hasOfficialOffice(tab = this.session) {
    return Boolean(tab?.desktop?.available && tab.desktop.url);
  },

  isDesktopSession(tab = this.session) {
    return Boolean(
      tab
      && (
        tab.file_id === SYSTEM_DESKTOP_FILE_ID
        || tab.extension === "desktop"
        || tab.mode === "desktop"
      )
    );
  },

  isDesktopOfficeDocument(tab = this.session) {
    return Boolean(tab && this.hasOfficialOffice(tab) && !this.isDesktopSession(tab) && this.isBinaryOffice(tab));
  },

  isVisibleOfficeTab(tab = {}) {
    return Boolean(this.isDesktopSession(tab) || this.isMarkdown(tab));
  },

  visibleTabs() {
    return this.tabs.filter((tab) => this.isVisibleOfficeTab(tab));
  },

  officialOfficeUrl(tab = this.session) {
    return tab?.desktop?.url || "";
  },

  desktopFrames() {
    const frames = Array.from(document.querySelectorAll("[data-office-desktop-frame]"));
    const rootFrame = this._root?.querySelector?.("[data-office-desktop-frame]");
    if (rootFrame && !frames.includes(rootFrame)) frames.push(rootFrame);
    return frames;
  },

  isUsableDesktopFrame(frame) {
    if (!frame?.contentWindow) return false;
    const rect = frame.getBoundingClientRect?.();
    return Boolean(rect && rect.width >= 120 && rect.height >= 80);
  },

  desktopFrame(preferred = null) {
    if (this.isUsableDesktopFrame(preferred)) return preferred;
    const rootFrame = this._root?.querySelector?.("[data-office-desktop-frame]");
    if (this.isUsableDesktopFrame(rootFrame)) return rootFrame;
    const frames = this.desktopFrames();
    return frames
      .filter((frame) => this.isUsableDesktopFrame(frame))
      .sort((left, right) => {
        const leftRect = left.getBoundingClientRect();
        const rightRect = right.getBoundingClientRect();
        return (rightRect.width * rightRect.height) - (leftRect.width * leftRect.height);
      })[0] || null;
  },

  unloadDesktopFrames() {
    this.stopDesktopResizeObserver();
    this.stopXpraDesktopPrime();
    for (const frame of this.desktopFrames()) {
      if (!frame?.getAttribute) continue;
      const current = frame.getAttribute("src") || "";
      if (!current || current === "about:blank") continue;
      frame.dataset.officeDesktopUnloaded = "true";
      frame.setAttribute("src", "about:blank");
    }
  },

  restoreDesktopFrames() {
    const url = this.officialOfficeUrl();
    if (!url) return;
    for (const frame of this.desktopFrames()) {
      if (!frame?.getAttribute) continue;
      const current = frame.getAttribute("src") || "";
      if (current && current !== "about:blank" && frame.dataset.officeDesktopUnloaded !== "true") continue;
      delete frame.dataset.officeDesktopUnloaded;
      frame.setAttribute("src", url);
    }
  },

  afterDesktopHostShown() {
    if (!this.hasOfficialOffice()) return;
    this._desktopResizeKey = "";
    this._desktopResizeSuspended = false;
    this._desktopResizePending = false;
    this.restoreDesktopFrames();
    this.requestDesktopViewportSync({ force: true, frame: this.desktopFrame() });
    for (const delay of [720, 1280]) {
      globalThis.setTimeout(() => {
        this.requestDesktopViewportSync({ force: true, frame: this.desktopFrame() });
      }, delay);
    }
  },

  beforeDesktopHostHandoff() {
    this.stopDesktopResizeObserver();
    this.stopXpraDesktopPrime();
    this._desktopResizeKey = "";
    this._desktopResizeSuspended = true;
    this._desktopResizePending = true;
  },

  cancelDesktopHostHandoff() {
    this._desktopResizeSuspended = false;
    this._desktopResizePending = false;
    this.requestDesktopViewportSync({ force: true, frame: this.desktopFrame() });
  },

  onDesktopFrameLoaded(event = null) {
    if (event?.target?.getAttribute?.("src") === "about:blank") return;
    this.error = "";
    this.queueDesktopFrameFocus(event?.target || null);
    this.requestDesktopViewportSync({ force: true, frame: event?.target || null });
  },

  queueDesktopFrameFocus(frame = null) {
    for (const delay of [0, 80, 260]) {
      globalThis.setTimeout(() => {
        if (!this.hasOfficialOffice()) return;
        if (isEditableInputTarget(document.activeElement)) return;
        this.focusDesktopFrame(frame || this.desktopFrame(), { arm: true });
      }, delay);
    }
  },

  focusDesktopFrame(frame = null, options = {}) {
    const target = this.desktopFrame(frame);
    if (!target) return false;
    if (options.arm !== false) this._desktopKeyboardActive = true;
    try {
      target.setAttribute("tabindex", "0");
      target.focus?.({ preventScroll: true });
      target.contentWindow?.focus?.();
      if (target.contentDocument?.body && !target.contentDocument.body.hasAttribute("tabindex")) {
        target.contentDocument.body.tabIndex = -1;
      }
      target.contentDocument?.body?.focus?.({ preventScroll: true });
      if (target.contentWindow?.client) target.contentWindow.client.capture_keyboard = true;
    } catch {
      target.focus?.({ preventScroll: true });
    }
    return Boolean(document.activeElement === target || target.contentDocument?.hasFocus?.());
  },

  updateDesktopMonitor() {
    if (!this.hasOfficialOffice()) {
      this.stopDesktopMonitor();
      this.stopDesktopResizeObserver();
      this._desktopKeyboardActive = false;
      return;
    }
    const sessionId = this.session?.desktop_session_id || this.session?.session_id || "";
    const tabId = this.session?.tab_id || "";
    if (
      sessionId
      && tabId
      && this._desktopHeartbeatTimer
      && this._desktopHeartbeatSessionId === sessionId
      && this._desktopHeartbeatTabId === tabId
    ) return;
    this.startDesktopMonitor();
    this.startDesktopResizeObserver();
  },

  startDesktopResizeObserver() {
    if (!this.hasOfficialOffice()) {
      this.stopDesktopResizeObserver();
      return;
    }
    const frame = this.desktopFrame();
    const target = frame?.parentElement || frame;
    if (!target) {
      this.stopDesktopResizeObserver();
      return;
    }
    if (this._desktopResizeCleanup && this._desktopResizeTarget === target) return;
    this.stopDesktopResizeObserver();

    const resize = () => this.queueDesktopResize();
    const resizeStart = () => this.suspendDesktopResize();
    const resizeEnd = () => this.resumeDesktopResize();
    const cleanup = [];
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(resize);
      observer.observe(target);
      cleanup.push(() => observer.disconnect());
    }
    globalThis.addEventListener?.("resize", resize);
    cleanup.push(() => globalThis.removeEventListener?.("resize", resize));
    globalThis.addEventListener?.("right-canvas-resize-start", resizeStart);
    cleanup.push(() => globalThis.removeEventListener?.("right-canvas-resize-start", resizeStart));
    globalThis.addEventListener?.("right-canvas-resize-end", resizeEnd);
    cleanup.push(() => globalThis.removeEventListener?.("right-canvas-resize-end", resizeEnd));
    this._desktopResizeTarget = target;
    this._desktopResizeCleanup = () => cleanup.splice(0).reverse().forEach((entry) => entry());
    resize();
  },

  stopDesktopResizeObserver() {
    if (this._desktopResizeTimer) {
      globalThis.clearTimeout(this._desktopResizeTimer);
    }
    this._desktopResizeTimer = null;
    this._desktopResizeCleanup?.();
    this._desktopResizeCleanup = null;
    this._desktopResizeTarget = null;
    this._desktopResizeKey = "";
    this._desktopResizeSuspended = false;
    this._desktopResizePending = false;
  },

  suspendDesktopResize() {
    this._desktopResizeSuspended = true;
    if (this._desktopResizeTimer) {
      globalThis.clearTimeout(this._desktopResizeTimer);
      this._desktopResizeTimer = null;
    }
  },

  resumeDesktopResize() {
    const hadPendingResize = this._desktopResizePending;
    this._desktopResizeSuspended = false;
    this._desktopResizePending = false;
    if (hadPendingResize || this.hasOfficialOffice()) {
      this.queueDesktopResize({ force: true });
    }
  },

  shouldDeferDesktopResize() {
    return Boolean(
      this._desktopResizeSuspended
      || document.body?.classList?.contains("right-canvas-resizing")
      || document.querySelector?.(".modal-inner.office-modal.is-resizing")
    );
  },

  requestDesktopViewportSync(options = {}) {
    const run = (force = false) => {
      this.syncDesktopViewport({ ...options, force });
    };
    if (globalThis.requestAnimationFrame) {
      globalThis.requestAnimationFrame(() => run(Boolean(options.force)));
    } else {
      globalThis.setTimeout(() => run(Boolean(options.force)), 0);
    }
    for (const delay of [140, 420]) {
      globalThis.setTimeout(() => run(false), delay);
    }
  },

  syncDesktopViewport(options = {}) {
    if (!this.hasOfficialOffice()) return false;
    const frame = this.desktopFrame(options.frame || null);
    if (!frame) return false;
    this.startDesktopResizeObserver();
    this.primeXpraDesktopFrame({ reset: true, frame });
    this.queueDesktopResize({
      force: Boolean(options.force),
      serverResize: options.serverResize !== false,
      frame,
    });
    this.updateDesktopMonitor();
    return true;
  },

  primeXpraDesktopFrame(options = {}) {
    if (options.reset) {
      this.stopXpraDesktopPrime();
      this._desktopPrimeAttempts = 0;
    }
    if (this.applyXpraDesktopFrameMode(options.frame || null)) return;
    if (this._desktopPrimeAttempts >= XPRA_DESKTOP_PRIME_ATTEMPTS) return;
    this._desktopPrimeAttempts += 1;
    if (this._desktopPrimeTimer) globalThis.clearTimeout(this._desktopPrimeTimer);
    this._desktopPrimeTimer = globalThis.setTimeout(() => {
      this._desktopPrimeTimer = null;
      this.primeXpraDesktopFrame();
    }, XPRA_DESKTOP_PRIME_INTERVAL_MS);
  },

  stopXpraDesktopPrime() {
    if (this._desktopPrimeTimer) globalThis.clearTimeout(this._desktopPrimeTimer);
    this._desktopPrimeTimer = null;
  },

  applyXpraDesktopFrameMode(preferredFrame = null, options = {}) {
    const frame = this.desktopFrame(preferredFrame);
    const remoteWindow = frame?.contentWindow;
    if (!remoteWindow) return false;
    const requestServerResize = options.requestServerResize === true;
    const requestRefresh = options.requestRefresh !== false;
    try {
      const remoteDocument = frame.contentDocument || remoteWindow.document;
      this.installXpraDesktopFrameCss(remoteDocument);
      this.installXpraDesktopFramePatches(remoteWindow, remoteDocument);
      const client = remoteWindow.client;
      if (!client) return false;
      this.installXpraDesktopClientPatches(remoteWindow, client);
      this.installXpraDesktopCursorPatches(remoteWindow, remoteDocument, client);
      this.installXpraDesktopKeyboardBridge(frame, remoteWindow, remoteDocument, client);
      this.installXpraDesktopClipboardBridge(frame, remoteWindow, remoteDocument, client);
      const container = client.container || remoteDocument?.querySelector?.("#screen");
      if (!container) return false;

      client.server_is_desktop = true;
      client.server_resize_exact = true;
      remoteDocument?.body?.classList?.add("desktop");

      const windows = Object.values(client.id_to_window || {});
      if (!client.connected || !windows.length) return false;

      const width = Math.round(container.clientWidth || remoteWindow.innerWidth || 0);
      const height = Math.round(container.clientHeight || remoteWindow.innerHeight || 0);
      if (width > 0 && height > 0) {
        client.desktop_width = width;
        client.desktop_height = height;
      }
      if (requestServerResize && width > 0 && height > 0 && typeof client._screen_resized === "function") {
        client.desktop_width = 0;
        client.desktop_height = 0;
        client.__a0AllowScreenResize = true;
        try {
          client._screen_resized(new remoteWindow.Event("resize"));
        } finally {
          client.__a0AllowScreenResize = false;
        }
      }

      for (const xpraWindow of windows) {
        this.normalizeXpraDesktopWindow(xpraWindow, width, height);
        xpraWindow.screen_resized?.();
        this.normalizeXpraDesktopWindow(xpraWindow, width, height);
        xpraWindow.updateCSSGeometry?.();
        this.fitXpraDesktopWindowElement(xpraWindow, width, height);
        this.installXpraDesktopWheelBridge(remoteWindow, xpraWindow);
        if (requestRefresh && xpraWindow.wid != null) client.request_refresh?.(xpraWindow.wid);
      }
      return true;
    } catch (error) {
      console.warn("Xpra desktop viewport prime skipped", error);
      return false;
    }
  },

  normalizeXpraDesktopWindow(xpraWindow, width, height) {
    if (!xpraWindow) return;
    const normalizedWidth = Math.max(1, Math.round(Number(width || 0)));
    const normalizedHeight = Math.max(1, Math.round(Number(height || 0)));
    xpraWindow.x = 0;
    xpraWindow.y = 0;
    xpraWindow.w = normalizedWidth;
    xpraWindow.h = normalizedHeight;
    xpraWindow.resizable = false;
    xpraWindow.decorations = false;
    xpraWindow.decorated = false;
    xpraWindow.metadata = { ...(xpraWindow.metadata || {}), decorations: false };
    xpraWindow._set_decorated?.(false);
    xpraWindow.configure_border_class?.();
    xpraWindow.leftoffset = 0;
    xpraWindow.rightoffset = 0;
    xpraWindow.topoffset = 0;
    xpraWindow.bottomoffset = 0;
  },

  fitXpraDesktopWindowElement(xpraWindow, width, height) {
    const cssWidth = `${Math.max(1, Number(width || 0))}px`;
    const cssHeight = `${Math.max(1, Number(height || 0))}px`;
    const windowElement = xpraWindow?.div;
    const canvas = xpraWindow?.canvas;
    windowElement?.style?.setProperty("left", "0px", "important");
    windowElement?.style?.setProperty("top", "0px", "important");
    windowElement?.style?.setProperty("position", "absolute", "important");
    windowElement?.style?.setProperty("width", cssWidth, "important");
    windowElement?.style?.setProperty("height", cssHeight, "important");
    windowElement?.style?.setProperty("transform", "none", "important");
    windowElement?.style?.setProperty("margin", "0", "important");
    canvas?.style?.setProperty("width", cssWidth, "important");
    canvas?.style?.setProperty("height", cssHeight, "important");
    canvas?.style?.setProperty("display", "block", "important");
    canvas?.style?.setProperty("margin", "0", "important");
  },

  installXpraDesktopWheelBridge(remoteWindow, xpraWindow) {
    const canvas = xpraWindow?.canvas;
    if (!remoteWindow || !canvas || canvas.__a0XpraWheelBridgeInstalled) return;
    if (typeof xpraWindow.mouse_scroll_cb !== "function") return;
    canvas.__a0XpraWheelBridgeInstalled = true;
    canvas.addEventListener("wheel", (event) => {
      event.stopImmediatePropagation?.();
      event.stopPropagation?.();
      event.preventDefault?.();
      const normalizedEvent = this.xpraDesktopWheelEvent(remoteWindow, canvas, event);
      xpraWindow.mouse_scroll_cb(normalizedEvent, xpraWindow);
    }, { passive: false, capture: true });
  },

  xpraDesktopWheelEvent(remoteWindow, canvas, event) {
    const finite = (value, fallback = 0) => {
      const number = Number(value);
      return Number.isFinite(number) ? number : fallback;
    };
    const deltaMode = finite(event.deltaMode, 0);
    const lineHeight = 16;
    const pageHeight = Math.max(1, remoteWindow.innerHeight || canvas.clientHeight || 800);
    const deltaScale = deltaMode === 1 ? lineHeight : deltaMode === 2 ? pageHeight : 1;
    const deltaX = finite(event.deltaX) * deltaScale;
    const deltaY = finite(event.deltaY) * deltaScale;
    const deltaZ = finite(event.deltaZ) * deltaScale;
    const wheelDeltaX = finite(event.wheelDeltaX, -deltaX);
    const wheelDeltaY = finite(event.wheelDeltaY, -deltaY);
    const wheelDelta = finite(event.wheelDelta, wheelDeltaY || wheelDeltaX);
    const getModifierState = (key) => {
      if (typeof event.getModifierState === "function") return event.getModifierState(key);
      const normalizedKey = String(key || "").toLowerCase();
      if (normalizedKey === "alt") return Boolean(event.altKey);
      if (normalizedKey === "control") return Boolean(event.ctrlKey);
      if (normalizedKey === "meta") return Boolean(event.metaKey);
      if (normalizedKey === "shift") return Boolean(event.shiftKey);
      return false;
    };
    const normalizedEvent = Object.create(event);
    Object.defineProperties(normalizedEvent, {
      target: { value: event.target || canvas },
      currentTarget: { value: canvas },
      clientX: { value: finite(event.clientX) },
      clientY: { value: finite(event.clientY) },
      pageX: { value: finite(event.pageX, finite(event.clientX)) },
      pageY: { value: finite(event.pageY, finite(event.clientY)) },
      screenX: { value: finite(event.screenX) },
      screenY: { value: finite(event.screenY) },
      offsetX: { value: finite(event.offsetX) },
      offsetY: { value: finite(event.offsetY) },
      movementX: { value: finite(event.movementX) },
      movementY: { value: finite(event.movementY) },
      button: { value: finite(event.button) },
      buttons: { value: finite(event.buttons) },
      which: { value: finite(event.which) },
      detail: { value: finite(event.detail) },
      deltaX: { value: deltaX },
      deltaY: { value: deltaY },
      deltaZ: { value: deltaZ },
      deltaMode: { value: 0 },
      wheelDeltaX: { value: wheelDeltaX },
      wheelDeltaY: { value: wheelDeltaY },
      wheelDelta: { value: wheelDelta },
      altKey: { value: Boolean(event.altKey) },
      ctrlKey: { value: Boolean(event.ctrlKey) },
      metaKey: { value: Boolean(event.metaKey) },
      shiftKey: { value: Boolean(event.shiftKey) },
      getModifierState: { value: getModifierState },
      preventDefault: { value: () => event.preventDefault?.() },
      stopPropagation: { value: () => event.stopPropagation?.() },
      stopImmediatePropagation: { value: () => event.stopImmediatePropagation?.() },
    });
    return normalizedEvent;
  },

  installXpraDesktopFrameCss(remoteDocument) {
    if (!remoteDocument || remoteDocument.getElementById("a0-xpra-desktop-frame-css")) return;
    const style = remoteDocument.createElement("style");
    style.id = "a0-xpra-desktop-frame-css";
    style.textContent = `
      html, body, #screen {
        width: 100% !important;
        height: 100% !important;
        overflow: hidden !important;
      }
      #float_menu,
      .windowhead,
      .windowbuttons {
        display: none !important;
      }
      #shadow_pointer {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
      }
      .window,
      .window.border,
      .window.desktop,
      .undecorated,
      .undecorated.border,
      .undecorated.desktop {
        left: 0 !important;
        top: 0 !important;
        position: absolute !important;
        width: 100% !important;
        height: 100% !important;
        transform: none !important;
        margin: 0 !important;
        border: 0 !important;
        border-radius: 0 !important;
        box-shadow: none !important;
      }
      .window canvas,
      .undecorated canvas {
        display: block !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 !important;
        border: 0 !important;
        border-radius: 0 !important;
        box-shadow: none !important;
      }
    `;
    remoteDocument.head?.appendChild(style);
  },

  installXpraDesktopCursorPatches(remoteWindow, remoteDocument, client) {
    if (!remoteWindow || !remoteDocument || !client) return;
    const hideShadowPointer = () => {
      const pointer = remoteDocument.getElementById?.("shadow_pointer");
      pointer?.style?.setProperty("display", "none", "important");
      pointer?.style?.setProperty("visibility", "hidden", "important");
      pointer?.style?.setProperty("opacity", "0", "important");
    };
    hideShadowPointer();

    const pointerPacket = remoteWindow.PACKET_TYPES?.pointer_position || "pointer-position";
    if (!client.__a0XpraDesktopCursorPatched) {
      if (typeof client._process_pointer_position === "function") {
        client.__a0OriginalProcessPointerPosition = client._process_pointer_position;
      }
      client._process_pointer_position = function patchedProcessPointerPosition(packet) {
        hideShadowPointer();
        this.__a0LastPointerPosition = packet;
        return false;
      };
      client.__a0XpraDesktopCursorPatched = true;
    }
    if (client.packet_handlers && pointerPacket) {
      client.packet_handlers[pointerPacket] = client._process_pointer_position;
    }
  },

  installXpraDesktopFramePatches(remoteWindow, remoteDocument) {
    if (!remoteWindow || !remoteDocument) return;
    remoteWindow.__a0XpraDesktopFramePatches ||= {};
    const patches = remoteWindow.__a0XpraDesktopFramePatches;
    if (!patches.noWindowList && typeof remoteWindow.noWindowList === "function") {
      const originalNoWindowList = remoteWindow.noWindowList;
      remoteWindow.noWindowList = function patchedNoWindowList(...args) {
        if (!remoteDocument.querySelector("#open_windows")) return undefined;
        return originalNoWindowList.apply(this, args);
      };
      patches.noWindowList = true;
    }
    if (!patches.addWindowListItem && typeof remoteWindow.addWindowListItem === "function") {
      const originalAddWindowListItem = remoteWindow.addWindowListItem;
      remoteWindow.addWindowListItem = function patchedAddWindowListItem(...args) {
        if (!remoteDocument.querySelector("#open_windows_list")) return undefined;
        return originalAddWindowListItem.apply(this, args);
      };
      patches.addWindowListItem = true;
    }
  },

  installXpraDesktopClientPatches(remoteWindow, client) {
    if (!remoteWindow || !client || client.__a0XpraDesktopClientPatched) return;
    if (typeof client._screen_resized === "function") {
      const originalScreenResized = client._screen_resized.bind(client);
      client.__a0OriginalScreenResized = originalScreenResized;
      client._screen_resized = function patchedScreenResized(event) {
        if (client.__a0AllowScreenResize === true) return originalScreenResized(event);
        return false;
      };
    }
    client.__a0XpraDesktopClientPatched = true;
  },

  installXpraDesktopClipboardBridge(frame, remoteWindow, remoteDocument, client) {
    if (!frame || !remoteWindow || !remoteDocument || !client) return;
    this.ensureDesktopClipboardBridge();
    if (remoteWindow.__a0XpraDesktopClipboardBridgeInstalled) return;

    const onPaste = (event) => {
      this.handleDesktopPasteEvent(event, frame, remoteWindow, client);
    };
    const onKeydown = (event) => {
      if (this.isDesktopPasteShortcut(event)) {
        void this.syncHostClipboardToDesktop(frame);
      }
    };
    remoteWindow.addEventListener("paste", onPaste, true);
    remoteDocument.addEventListener("paste", onPaste, true);
    remoteWindow.addEventListener("keydown", onKeydown, true);
    remoteDocument.addEventListener("keydown", onKeydown, true);
    remoteWindow.__a0XpraDesktopClipboardBridgeInstalled = true;
    remoteWindow.__a0XpraDesktopClipboardBridgeCleanup = () => {
      remoteWindow.removeEventListener("paste", onPaste, true);
      remoteDocument.removeEventListener("paste", onPaste, true);
      remoteWindow.removeEventListener("keydown", onKeydown, true);
      remoteDocument.removeEventListener("keydown", onKeydown, true);
      remoteWindow.__a0XpraDesktopClipboardBridgeInstalled = false;
    };
  },

  ensureDesktopClipboardBridge() {
    if (this._desktopClipboardCleanup) return;

    const onPaste = (event) => {
      if (!this._desktopKeyboardActive || !this.hasOfficialOffice()) return;
      if (isEditableInputTarget(event.target)) return;
      const frame = this.desktopFrame();
      const remoteWindow = frame?.contentWindow;
      const client = remoteWindow?.client;
      if (!frame || !remoteWindow || !client) return;
      this.handleDesktopPasteEvent(event, frame, remoteWindow, client);
    };

    document.addEventListener("paste", onPaste, true);
    this._desktopClipboardCleanup = () => {
      document.removeEventListener("paste", onPaste, true);
      this._desktopClipboardCleanup = null;
    };
  },

  stopDesktopClipboardBridge() {
    this._desktopClipboardCleanup?.();
  },

  handleDesktopPasteEvent(event, frame, remoteWindow, client) {
    const text = this.desktopClipboardTextFromEvent(event);
    if (!text) return false;
    if (!this.syncXpraClipboardText(client, text, remoteWindow)) return false;
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
    this.focusDesktopFrame(frame, { arm: true });
    return true;
  },

  desktopClipboardTextFromEvent(event) {
    const data = (event?.originalEvent || event)?.clipboardData;
    if (!data?.getData) return "";
    for (const type of ["text/plain", "text", "Text", "STRING", "UTF8_STRING"]) {
      const value = data.getData(type);
      if (value) return value;
    }
    return "";
  },

  syncXpraClipboardText(client, text, remoteWindow = null) {
    const value = String(text ?? "");
    if (!client || !value || typeof client.send_clipboard_token !== "function") return false;
    const textPlain = remoteWindow?.TEXT_PLAIN || "text/plain";
    const utf8String = remoteWindow?.UTF8_STRING || "UTF8_STRING";
    const utilities = remoteWindow?.Utilities;
    const payload = utilities?.StringToUint8 ? utilities.StringToUint8(value) : value;
    client.clipboard_enabled = true;
    client.clipboard_direction = "both";
    client.clipboard_buffer = value;
    client.clipboard_pending = false;
    client.send_clipboard_token(payload, [textPlain, utf8String, "TEXT", "STRING"]);
    return true;
  },

  async syncHostClipboardToDesktop(frame = null) {
    const target = this.desktopFrame(frame);
    const remoteWindow = target?.contentWindow;
    const client = remoteWindow?.client;
    if (!client || !navigator.clipboard?.readText) return false;
    try {
      const text = await navigator.clipboard.readText();
      return this.syncXpraClipboardText(client, text, remoteWindow);
    } catch {
      return false;
    }
  },

  isDesktopPasteShortcut(event) {
    const key = String(event?.key || "").toLowerCase();
    return key === "v" && (event?.ctrlKey || event?.metaKey) && !event?.altKey;
  },

  installXpraDesktopKeyboardBridge(frame, remoteWindow, remoteDocument, client) {
    if (!frame || !remoteWindow || !remoteDocument || !client) return;
    this.ensureDesktopKeyboardBridge();
    frame.setAttribute("tabindex", "0");
    if (remoteWindow.__a0XpraDesktopKeyboardBridgeInstalled) return;

    const activate = () => this.focusDesktopFrame(frame, { arm: true });
    const events = ["pointerdown", "mousedown", "touchstart", "focusin"];
    for (const eventName of events) {
      remoteDocument.addEventListener(eventName, activate, true);
    }
    remoteWindow.addEventListener("focus", activate, true);
    remoteWindow.__a0XpraDesktopKeyboardBridgeInstalled = true;
    remoteWindow.__a0XpraDesktopKeyboardBridgeCleanup = () => {
      for (const eventName of events) {
        remoteDocument.removeEventListener(eventName, activate, true);
      }
      remoteWindow.removeEventListener("focus", activate, true);
      remoteWindow.__a0XpraDesktopKeyboardBridgeInstalled = false;
    };
  },

  ensureDesktopKeyboardBridge() {
    if (this._desktopKeyboardCleanup) return;

    const deactivateWhenOutsideDesktop = (event) => {
      const target = event.target;
      if (target?.closest?.(".office-desktop-wrap") || target?.matches?.("[data-office-desktop-frame]")) return;
      this._desktopKeyboardActive = false;
    };
    const forwardKeyboardEvent = (event, pressed) => {
      if (!this._desktopKeyboardActive || !this.hasOfficialOffice()) return;
      if (event.defaultPrevented || isEditableInputTarget(event.target)) return;

      const frame = this.desktopFrame();
      if (!frame || document.activeElement === frame) return;
      const client = frame.contentWindow?.client;
      const handler = pressed ? client?._keyb_onkeydown : client?._keyb_onkeyup;
      if (!client?.capture_keyboard || typeof handler !== "function") return;
      if (pressed && this.isDesktopPasteShortcut(event)) {
        void this.syncHostClipboardToDesktop(frame);
      }

      const allowDefault = handler.call(client, event);
      if (!allowDefault) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const onKeydown = (event) => forwardKeyboardEvent(event, true);
    const onKeyup = (event) => forwardKeyboardEvent(event, false);

    document.addEventListener("pointerdown", deactivateWhenOutsideDesktop, true);
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("keyup", onKeyup, true);
    this._desktopKeyboardCleanup = () => {
      document.removeEventListener("pointerdown", deactivateWhenOutsideDesktop, true);
      document.removeEventListener("keydown", onKeydown, true);
      document.removeEventListener("keyup", onKeyup, true);
      this._desktopKeyboardActive = false;
      this._desktopKeyboardCleanup = null;
    };
  },

  stopDesktopKeyboardBridge() {
    this._desktopKeyboardCleanup?.();
  },

  queueDesktopResize(options = {}) {
    if (!this.hasOfficialOffice()) return;
    const token = this.session?.desktop?.token || "";
    const frame = this.desktopFrame(options.frame || null);
    const target = frame?.parentElement || frame;
    if (!token || !target) return;
    const force = Boolean(options.force);
    const serverResize = options.serverResize !== false;
    const rect = target.getBoundingClientRect();
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    if (width < 320 || height < 220) return;
    this.applyXpraDesktopFrameMode(frame, { requestServerResize: false, requestRefresh: false });
    if (!force && this.shouldDeferDesktopResize()) {
      this._desktopResizePending = true;
      return;
    }
    const key = `${token}:${width}x${height}`;
    if (!serverResize) return;
    if (!force && key === this._desktopResizeKey) return;
    if (this._desktopResizeTimer) globalThis.clearTimeout(this._desktopResizeTimer);
    this._desktopResizeTimer = globalThis.setTimeout(async () => {
      this._desktopResizeTimer = null;
      if (!force && this.shouldDeferDesktopResize()) {
        this._desktopResizePending = true;
        return;
      }
      try {
        const params = new URLSearchParams({ token, width: String(width), height: String(height) });
        const response = await fetch(`/desktop/resize?${params.toString()}`, { credentials: "same-origin" });
        if (response.ok) {
          const result = await response.json().catch(() => ({}));
          this._desktopResizeKey = key;
          const activeFrame = this.desktopFrame(frame);
          const activeTarget = activeFrame?.parentElement || activeFrame;
          const activeRect = activeTarget?.getBoundingClientRect?.();
          const activeWidth = Math.round(activeRect?.width || 0);
          const activeHeight = Math.round(activeRect?.height || 0);
          if (activeWidth >= 320 && activeHeight >= 220) {
            const activeKey = `${token}:${activeWidth}x${activeHeight}`;
            if (activeKey !== key) {
              this.queueDesktopResize({ force: true, serverResize: true, frame: activeFrame });
              return;
            }
          }
          if (result?.reload) this.reloadDesktopFrame(activeFrame || frame);
          this.primeXpraDesktopFrame({ reset: true, frame: activeFrame || frame });
        }
      } catch (error) {
        console.warn("Desktop resize skipped", error);
      }
    }, DESKTOP_RESIZE_DELAY_MS);
  },

  reloadDesktopFrame(frame = null) {
    const target = this.desktopFrame(frame);
    if (!target) return;
    const current = target.getAttribute("src") || target.src || this.officialOfficeUrl();
    if (!current) return;
    try {
      const url = new URL(current, window.location.href);
      url.searchParams.set("a0_reload", String(Date.now()));
      target.setAttribute("src", `${url.pathname}${url.search}`);
    } catch {
      target.setAttribute("src", current);
    }
  },

  startDesktopMonitor() {
    this.stopDesktopMonitor();
    if (!this.hasOfficialOffice()) return;
    const tabId = this.session?.tab_id || "";
    const sessionId = this.session?.desktop_session_id || this.session?.session_id || "";
    if (!tabId || !sessionId) return;
    this._desktopHeartbeatSessionId = sessionId;
    this._desktopHeartbeatTabId = tabId;
    this._desktopHeartbeatMisses = 0;

    const tick = async () => {
      if (!this.session || this.session.tab_id !== tabId || !this.hasOfficialOffice()) return;
      try {
        const response = await callOffice("desktop_sync", {
          desktop_session_id: sessionId,
          file_id: this.session.file_id || "",
        });
        if (response?.ok === false) throw new Error(response.error || "Desktop session closed.");
        this._desktopHeartbeatMisses = 0;
        if (response?.document) {
          const document = normalizeDocument(response.document);
          this.replaceActiveSession({
            ...this.session,
            document,
            path: document.path || this.session.path,
            file_id: document.file_id || this.session.file_id,
            version: document.version || this.session.version,
          });
        }
      } catch {
        if (!this.session || this.session.tab_id !== tabId) return;
        this._desktopHeartbeatMisses += 1;
        if (this._desktopHeartbeatMisses >= 2) {
          await this.handleOfficialOfficeClosed(tabId);
        }
      }
    };

    this._desktopHeartbeatTimer = globalThis.setInterval(tick, DESKTOP_HEARTBEAT_MS);
    globalThis.setTimeout(tick, Math.min(1200, DESKTOP_HEARTBEAT_MS));
  },

  stopDesktopMonitor() {
    if (this._desktopHeartbeatTimer) {
      globalThis.clearInterval(this._desktopHeartbeatTimer);
    }
    this._desktopHeartbeatTimer = null;
    this._desktopHeartbeatSessionId = "";
    this._desktopHeartbeatTabId = "";
    this._desktopHeartbeatMisses = 0;
  },

  async handleOfficialOfficeClosed(tabId) {
    const tab = this.tabs.find((item) => item.tab_id === tabId);
    const hiddenDesktopDocument = !tab && this.session?.tab_id === tabId && this.isDesktopOfficeDocument(this.session)
      ? this.session
      : null;
    const target = tab || hiddenDesktopDocument;
    if (!target || target._desktopClosed) return;
    target._desktopClosed = true;
    this.stopDesktopMonitor();
    this.stopDesktopResizeObserver();
    this.stopXpraDesktopPrime();
    this.setMessage("Desktop is restarting");
    await this.ensureDesktopSession({ force: true, select: this.activeTabId === tabId || Boolean(hiddenDesktopDocument) });
    target._desktopClosed = false;
    await this.refresh();
  },

  hasNativeDocxTiles() {
    return Boolean(
      this.isDocx()
      && this.session?.native?.available
      && Array.isArray(this.session?.tiles)
      && this.session.tiles.some((tile) => tile?.image),
    );
  },

  async onNativeDocxClick(event) {
    if (!this.hasNativeDocxTiles()) return;
    const page = event.target?.closest?.(".office-docx-page.is-native-tile");
    const image = page?.querySelector?.("img");
    if (!page || !image) return;
    const twips = this.decodeTileTwips(page);
    const rect = image.getBoundingClientRect();
    const ratioX = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    const ratioY = Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height)));
    const x = Math.round((twips.x || 0) + ratioX * (twips.width || 0));
    const y = Math.round((twips.y || 0) + ratioY * (twips.height || 0));
    this._docxEditor?.focus?.({ preventScroll: true });
    await this.sendNativeMouse({ type: "down", x, y, count: 1, buttons: 1, modifier: 0 });
    await this.sendNativeMouse({ type: "up", x, y, count: 1, buttons: 1, modifier: 0 });
  },

  onNativeDocxKeydown(event) {
    if (!this.hasNativeDocxTiles()) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key || "";
    if (key.length === 1) {
      event.preventDefault();
      void this.sendNativeKey({ text: key });
      return;
    }
    const special = {
      Enter: { text: "\n" },
      Tab: { text: "\t" },
      Backspace: { char_code: 0, key_code: 8 },
      Delete: { char_code: 0, key_code: 127 },
      ArrowLeft: { char_code: 0, key_code: 37 },
      ArrowUp: { char_code: 0, key_code: 38 },
      ArrowRight: { char_code: 0, key_code: 39 },
      ArrowDown: { char_code: 0, key_code: 40 },
    }[key];
    if (!special) return;
    event.preventDefault();
    if (special.text != null) {
      void this.sendNativeKey({ text: special.text });
    } else {
      void this.sendNativeKey({ type: "down", ...special }).then(() => this.sendNativeKey({ type: "up", ...special }));
    }
  },

  decodeTileTwips(page) {
    try {
      return JSON.parse(decodeURIComponent(page?.dataset?.twips || "{}"));
    } catch {
      return {};
    }
  },

  async sendNativeKey(key) {
    if (!this.session?.session_id) return null;
    return await this.queueNativeEvent(async () => {
      const response = await this.sendNativeEvent("office_key", "key", key, "key");
      if (response?.ok) this.markDirty();
      return response;
    });
  },

  async sendNativeMouse(mouse) {
    if (!this.session?.session_id) return null;
    return await this.queueNativeEvent(() => this.sendNativeEvent("office_mouse", "mouse", mouse, "mouse"));
  },

  async queueNativeEvent(task) {
    const run = this._nativeEventQueue.catch(() => null).then(task);
    this._nativeEventQueue = run.catch(() => null);
    return await run;
  },

  async awaitNativeEvents() {
    await this._nativeEventQueue.catch(() => null);
  },

  async sendNativeEvent(socketEvent, apiAction, payload, key) {
    try {
      let response;
      try {
        response = await requestOffice(socketEvent, {
          session_id: this.session.session_id,
          [key]: payload,
        }, 7000);
      } catch (_socketError) {
        response = await callOffice(apiAction, {
          session_id: this.session.session_id,
          [key]: payload,
        });
      }
      if (response?.metadata && this.session) {
        this.session.native = { ...(this.session.native || {}), ...response.metadata, available: true };
      }
      if (Array.isArray(response?.tiles) && this.session) {
        this.session.tiles = response.tiles;
        this.queueRender({ force: true, focus: true });
      }
      return response;
    } catch (error) {
      console.warn("LibreOffice native event skipped", socketEvent, error);
      return null;
    }
  },

  isPreviewOnly() {
    return Boolean(this.session && !this.hasOfficialOffice() && !this.isMarkdown() && !this.isDocx());
  },

  defaultTitle(kind, fmt) {
    const date = new Date().toISOString().slice(0, 10);
    if (fmt === "md") return `Document ${date}`;
    if (fmt === "docx") return `DOCX ${date}`;
    if (kind === "spreadsheet") return `Spreadsheet ${date}`;
    if (kind === "presentation") return `Presentation ${date}`;
    return `Document ${date}`;
  },

  tabTitle(tab = {}) {
    return tab.title || tab.document?.basename || basename(tab.path);
  },

  tabLabel(tab = {}) {
    const title = this.tabTitle(tab);
    return tab.dirty ? `${title} unsaved` : title;
  },

  tabIcon(tab = {}) {
    const ext = String(tab.extension || tab.document?.extension || "").toLowerCase();
    if (this.isDesktopSession(tab)) return "desktop_windows";
    if (ext === "md") return "article";
    if (ext === "docx") return "description";
    if (ext === "xlsx") return "table_chart";
    if (ext === "pptx") return "co_present";
    return "draft";
  },

  documentPath() {
    return this.session?.document?.path || this.session?.path || "";
  },

  documentMeta(doc = this.session?.document || this.session || {}) {
    const parts = [String(doc.extension || "").toUpperCase(), formatBytes(doc.size)].filter(Boolean);
    return parts.join(" · ");
  },

  openCards() {
    return this.visibleTabs()
      .filter((tab) => !this.isDesktopSession(tab))
      .map((tab) => normalizeDocument({
        ...tab.document,
        ...tab,
        open: true,
      }));
  },

  recentCards() {
    const openIds = new Set(this.tabs.map((tab) => tab.file_id).filter(Boolean));
    return this.recent.filter((doc) => !openIds.has(doc.file_id)).slice(0, 8);
  },

  previewKind(doc = {}) {
    const ext = String(doc.extension || "").toLowerCase();
    if (ext === "xlsx") return "spreadsheet";
    if (ext === "pptx") return "presentation";
    if (ext === "md") return "markdown";
    return "document";
  },

  hasPreview(doc = {}) {
    const preview = doc.preview || {};
    return Boolean(
      (Array.isArray(preview.lines) && preview.lines.length)
      || (Array.isArray(preview.rows) && preview.rows.length)
      || (Array.isArray(preview.slides) && preview.slides.length)
    );
  },

  previewLines(doc = {}) {
    const preview = doc.preview || {};
    return (preview.lines || []).slice(0, 8);
  },

  previewRows(doc = {}) {
    const preview = doc.preview || {};
    return (preview.rows || []).slice(0, 6);
  },

  previewSlides(doc = {}) {
    const preview = doc.preview || {};
    return (preview.slides || []).slice(0, 3);
  },

  dashboardTitle(doc = {}) {
    return doc.title || doc.basename || basename(doc.path);
  },

  dashboardMeta(doc = {}) {
    return [String(doc.extension || "").toUpperCase(), doc.open ? "Open" : "", formatBytes(doc.size)].filter(Boolean).join(" · ");
  },

  setupFloatingModal(element = null) {
    const root = element || globalThis.document?.querySelector(".office-panel");
    const inner = root?.closest?.(".modal-inner");
    const body = root?.closest?.(".modal-bd");
    const header = inner?.querySelector?.(".modal-header");
    if (!inner || !body || !header || inner.dataset.officeModalReady === "1") return;

    inner.dataset.officeModalReady = "1";
    inner.classList.add("office-modal", "modal-no-backdrop");
    body.classList.add("office-modal-body");
    header.style.cursor = "move";

    const inset = 8;
    const minWidth = 720;
    const minHeight = 520;
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const cleanup = [];
    let beforeFocusBounds = null;
    let dragging = false;
    let resizing = false;
    let pointerId = 0;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    let startWidth = 0;
    let startHeight = 0;
    let resizeMode = "";

    const currentBounds = () => {
      const rect = inner.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      };
    };

    const normalizedBounds = (bounds) => {
      const maxWidth = Math.max(320, globalThis.innerWidth - inset * 2);
      const maxHeight = Math.max(320, globalThis.innerHeight - inset * 2);
      const safeMinWidth = Math.min(minWidth, maxWidth);
      const safeMinHeight = Math.min(minHeight, maxHeight);
      const width = clamp(bounds.width, safeMinWidth, maxWidth);
      const height = clamp(bounds.height, safeMinHeight, maxHeight);
      return {
        width,
        height,
        left: clamp(bounds.left, inset, Math.max(inset, globalThis.innerWidth - width - inset)),
        top: clamp(bounds.top, inset, Math.max(inset, globalThis.innerHeight - height - inset)),
      };
    };

    const setBounds = (bounds) => {
      const next = normalizedBounds(bounds);
      inner.style.position = "fixed";
      inner.style.transform = "none";
      inner.style.left = `${Math.round(next.left)}px`;
      inner.style.top = `${Math.round(next.top)}px`;
      inner.style.width = `${Math.round(next.width)}px`;
      inner.style.height = `${Math.round(next.height)}px`;
      inner.style.right = "auto";
      inner.style.bottom = "auto";
      inner.style.margin = "0";
    };

    const ensurePosition = () => {
      setBounds(currentBounds());
    };

    const shield = globalThis.document.createElement("div");
    shield.className = "office-modal-input-shield";
    inner.appendChild(shield);
    cleanup.push(() => shield.remove());

    const setShield = (visible, cursor = "") => {
      shield.style.display = visible ? "block" : "none";
      shield.style.cursor = cursor;
    };

    const focusButton = globalThis.document.createElement("button");
    focusButton.type = "button";
    focusButton.className = "modal-dock-button office-modal-focus-button";
    focusButton.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">fullscreen</span>';
    const updateFocusButton = (active) => {
      focusButton.title = active ? "Restore size" : "Focus mode";
      focusButton.setAttribute("aria-label", focusButton.title);
      focusButton.querySelector(".material-symbols-outlined").textContent = active ? "fullscreen_exit" : "fullscreen";
    };
    updateFocusButton(false);
    const closeButton = inner.querySelector(".modal-close");
    if (closeButton) {
      closeButton.insertAdjacentElement("beforebegin", focusButton);
    } else {
      header.appendChild(focusButton);
    }
    cleanup.push(() => focusButton.remove());

    const setFocusMode = (enabled) => {
      ensurePosition();
      if (enabled) {
        beforeFocusBounds = currentBounds();
        inner.classList.add("is-focus-mode");
        setBounds({
          left: inset,
          top: inset,
          width: globalThis.innerWidth - inset * 2,
          height: globalThis.innerHeight - inset * 2,
        });
        updateFocusButton(true);
        return;
      }
      inner.classList.remove("is-focus-mode");
      setBounds(beforeFocusBounds || currentBounds());
      beforeFocusBounds = null;
      updateFocusButton(false);
    };

    const onFocusClick = () => setFocusMode(!inner.classList.contains("is-focus-mode"));
    focusButton.addEventListener("click", onFocusClick);
    cleanup.push(() => focusButton.removeEventListener("click", onFocusClick));

    const onPointerDown = (event) => {
      if (event.button !== 0) return;
      if (event.target?.closest?.("button,a,input,textarea,select")) return;
      if (inner.classList.contains("is-focus-mode")) return;
      ensurePosition();
      const rect = inner.getBoundingClientRect();
      dragging = true;
      pointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      startWidth = rect.width;
      startHeight = rect.height;
      inner.classList.add("is-dragging");
      setShield(true, "move");
      header.setPointerCapture?.(pointerId);
      event.preventDefault();
    };

    const onPointerMove = (event) => {
      if (!dragging || event.pointerId !== pointerId) return;
      setBounds({
        left: startLeft + event.clientX - startX,
        top: startTop + event.clientY - startY,
        width: startWidth,
        height: startHeight,
      });
    };

    const onPointerUp = (event) => {
      if (!dragging || event.pointerId !== pointerId) return;
      dragging = false;
      inner.classList.remove("is-dragging");
      setShield(false);
      header.releasePointerCapture?.(pointerId);
    };

    const createResizeHandle = (mode) => {
      const handle = globalThis.document.createElement("div");
      handle.className = `office-modal-resizer is-${mode}`;
      handle.dataset.officeResize = mode;
      inner.appendChild(handle);
      cleanup.push(() => handle.remove());
      return handle;
    };

    const onResizeDown = (event) => {
      if (event.button !== 0 || inner.classList.contains("is-focus-mode")) return;
      ensurePosition();
      const rect = inner.getBoundingClientRect();
      resizing = true;
      resizeMode = event.currentTarget.dataset.officeResize || "";
      pointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      startWidth = rect.width;
      startHeight = rect.height;
      inner.classList.add("is-resizing");
      this.suspendDesktopResize();
      setShield(true, resizeMode === "right" ? "ew-resize" : resizeMode === "bottom" ? "ns-resize" : "nwse-resize");
      event.currentTarget.setPointerCapture?.(pointerId);
      event.preventDefault();
      event.stopPropagation();
    };

    const onResizeMove = (event) => {
      if (!resizing || event.pointerId !== pointerId) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      setBounds({
        left: startLeft,
        top: startTop,
        width: resizeMode === "bottom" ? startWidth : startWidth + dx,
        height: resizeMode === "right" ? startHeight : startHeight + dy,
      });
    };

    const onResizeUp = (event) => {
      if (!resizing || event.pointerId !== pointerId) return;
      resizing = false;
      resizeMode = "";
      inner.classList.remove("is-resizing");
      setShield(false);
      event.currentTarget.releasePointerCapture?.(pointerId);
      this.resumeDesktopResize();
    };

    header.addEventListener("pointerdown", onPointerDown);
    header.addEventListener("pointermove", onPointerMove);
    header.addEventListener("pointerup", onPointerUp);
    header.addEventListener("pointercancel", onPointerUp);
    cleanup.push(() => header.removeEventListener("pointerdown", onPointerDown));
    cleanup.push(() => header.removeEventListener("pointermove", onPointerMove));
    cleanup.push(() => header.removeEventListener("pointerup", onPointerUp));
    cleanup.push(() => header.removeEventListener("pointercancel", onPointerUp));

    for (const mode of ["right", "bottom", "corner"]) {
      const handle = createResizeHandle(mode);
      handle.addEventListener("pointerdown", onResizeDown);
      handle.addEventListener("pointermove", onResizeMove);
      handle.addEventListener("pointerup", onResizeUp);
      handle.addEventListener("pointercancel", onResizeUp);
      cleanup.push(() => handle.removeEventListener("pointerdown", onResizeDown));
      cleanup.push(() => handle.removeEventListener("pointermove", onResizeMove));
      cleanup.push(() => handle.removeEventListener("pointerup", onResizeUp));
      cleanup.push(() => handle.removeEventListener("pointercancel", onResizeUp));
    }

    const onWindowResize = () => {
      if (inner.classList.contains("is-focus-mode")) {
        setBounds({
          left: inset,
          top: inset,
          width: globalThis.innerWidth - inset * 2,
          height: globalThis.innerHeight - inset * 2,
        });
        return;
      }
      ensurePosition();
    };
    globalThis.addEventListener("resize", onWindowResize);
    cleanup.push(() => globalThis.removeEventListener("resize", onWindowResize));

    if (globalThis.requestAnimationFrame) {
      globalThis.requestAnimationFrame(ensurePosition);
    } else {
      globalThis.setTimeout(ensurePosition, 0);
    }
    this._floatingCleanup = () => {
      cleanup.splice(0).reverse().forEach((entry) => entry());
      inner.classList.remove("is-dragging", "is-resizing", "is-focus-mode");
      this._desktopResizeSuspended = false;
      this._desktopResizePending = false;
      delete inner.dataset.officeModalReady;
    };
  },
};

export const store = createStore("office", model);
