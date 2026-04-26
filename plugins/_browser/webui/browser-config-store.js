import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";

const BROWSER_EXTENSIONS_API = "/plugins/_browser/extensions";

function normalizePathList(value) {
  const source = Array.isArray(value)
    ? value
    : String(value || "").split(/\r?\n/);
  const seen = new Set();
  const paths = [];
  for (const item of source) {
    const path = String(item || "").trim();
    if (!path || seen.has(path)) continue;
    seen.add(path);
    paths.push(path);
  }
  return paths;
}

function ensureConfig(config) {
  if (!config || typeof config !== "object") return null;
  config.extension_paths = normalizePathList(config.extension_paths);
  config.default_homepage = String(config.default_homepage || "about:blank").trim() || "about:blank";
  config.autofocus_active_page = normalizeBoolean(config.autofocus_active_page, true);
  config.model_preset = String(config.model_preset || "").trim();
  delete config.model;
  return config;
}

function normalizeBoolean(value, fallback = true) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Boolean(value);
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on", "enabled"].includes(normalized)) return true;
  if (["0", "false", "no", "off", "disabled"].includes(normalized)) return false;
  return fallback;
}

export const store = createStore("browserConfig", {
  config: null,
  extensionsList: [],
  extensionsLoading: false,
  extensionsError: "",

  async init(config) {
    this.bindConfig(config);
    await this.loadExtensionsList();
  },

  cleanup() {
    this.config = null;
    this.extensionsList = [];
    this.extensionsError = "";
  },

  bindConfig(config) {
    const safeConfig = ensureConfig(config);
    if (!safeConfig) return;
    if (this.config === safeConfig) return;
    this.config = safeConfig;
  },

  setAutofocusActivePage(enabled) {
    const safeConfig = ensureConfig(this.config);
    if (!safeConfig) return;
    safeConfig.autofocus_active_page = Boolean(enabled);
  },

  autofocusLabel() {
    return this.config?.autofocus_active_page === false ? "Off" : "On";
  },

  hasPaths() {
    return this.pathCount() > 0;
  },

  pathCount() {
    return normalizePathList(this.config?.extension_paths).length;
  },

  pathCountLabel() {
    const count = this.pathCount();
    if (!count) return "No extensions enabled";
    return `${count} extension${count === 1 ? "" : "s"} enabled`;
  },

  extensionModeReady() {
    return this.pathCount() > 0;
  },

  async loadExtensionsList() {
    if (this.extensionsLoading) return;
    this.extensionsLoading = true;
    this.extensionsError = "";
    try {
      const response = await callJsonApi(BROWSER_EXTENSIONS_API, { action: "list" });
      if (!response?.ok) {
        throw new Error(response?.error || "Could not load browser extensions.");
      }
      this.extensionsList = Array.isArray(response.extensions) ? response.extensions : [];
    } catch (error) {
      this.extensionsList = [];
      this.extensionsError = error instanceof Error ? error.message : String(error);
    } finally {
      this.extensionsLoading = false;
    }
  },

  extensionEnabled(extension) {
    const path = typeof extension === "string" ? extension : extension?.path;
    return normalizePathList(this.config?.extension_paths).includes(String(path || ""));
  },

  setExtensionEnabled(extension, enabled) {
    const path = String((typeof extension === "string" ? extension : extension?.path) || "").trim();
    if (!path) return;
    const safeConfig = ensureConfig(this.config);
    if (!safeConfig) return;
    const paths = normalizePathList(safeConfig.extension_paths);
    if (enabled && !paths.includes(path)) {
      paths.push(path);
    } else if (!enabled) {
      const index = paths.indexOf(path);
      if (index >= 0) paths.splice(index, 1);
    }
    safeConfig.extension_paths = paths;
  },

  extensionVersionLabel(extension) {
    const version = String(extension?.version || "").trim();
    return version ? `v${version}` : "Unpacked extension";
  },
});
