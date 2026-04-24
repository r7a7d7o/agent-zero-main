import { createStore } from "/js/AlpineStore.js";
import { fetchApi } from "/js/api.js";

const MODEL_CONFIG_API = "/plugins/_model_config/model_presets";

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
  if (typeof config.extensions_enabled !== "boolean") {
    config.extensions_enabled = Boolean(config.extensions_enabled);
  }
  config.extension_paths = normalizePathList(config.extension_paths);
  config.model_preset = String(config.model_preset || "").trim();
  delete config.model;
  return config;
}

export const store = createStore("browserConfig", {
  config: null,
  extensionPathsText: "",
  presets: [],
  presetsLoading: false,
  presetsError: "",
  _presetsLoaded: false,

  async init(config) {
    this.bindConfig(config);
    await this.loadPresets();
  },

  cleanup() {
    this.config = null;
    this.extensionPathsText = "";
    this.presetsError = "";
  },

  bindConfig(config) {
    const safeConfig = ensureConfig(config);
    if (!safeConfig) return;
    if (this.config === safeConfig) return;
    this.config = safeConfig;
    this.extensionPathsText = safeConfig.extension_paths.join("\n");
  },

  setExtensionPathsText(value) {
    this.extensionPathsText = String(value || "");
    this.syncExtensionPaths();
  },

  syncExtensionPaths() {
    const safeConfig = ensureConfig(this.config);
    if (!safeConfig) return;
    safeConfig.extension_paths = normalizePathList(this.extensionPathsText);
  },

  hasPaths() {
    return this.pathCount() > 0;
  },

  pathCount() {
    return normalizePathList(this.extensionPathsText).length;
  },

  pathCountLabel() {
    const count = this.pathCount();
    if (!count) return "No extension paths configured";
    return `${count} path${count === 1 ? "" : "s"} configured`;
  },

  extensionModeReady() {
    const safeConfig = ensureConfig(this.config);
    return Boolean(safeConfig?.extensions_enabled && this.pathCount());
  },

  async loadPresets() {
    if (this._presetsLoaded || this.presetsLoading) return;
    this.presetsLoading = true;
    this.presetsError = "";
    try {
      const response = await fetchApi(MODEL_CONFIG_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "get" }),
      });
      const data = await response.json().catch(() => ({}));
      this.presets = Array.isArray(data?.presets)
        ? data.presets.filter((preset) => String(preset?.name || "").trim())
        : [];
      this._presetsLoaded = true;
    } catch (error) {
      this.presets = [];
      this.presetsError = error instanceof Error ? error.message : String(error);
    } finally {
      this.presetsLoading = false;
    }
  },

  selectedPreset() {
    const selected = String(this.config?.model_preset || "").trim();
    if (!selected) return null;
    return this.presets.find((preset) => preset?.name === selected) || null;
  },

  presetOptions() {
    const selected = String(this.config?.model_preset || "").trim();
    const options = this.presets.map((preset) => ({
      ...preset,
      label: preset.name,
      missing: false,
    }));
    if (selected && this._presetsLoaded && !options.some((preset) => preset.name === selected)) {
      options.push({
        name: selected,
        label: `${selected} (missing)`,
        missing: true,
      });
    }
    return options;
  },

  selectedPresetSummary() {
    const selected = String(this.config?.model_preset || "").trim();
    if (!selected) return "Using the effective Main Model.";

    const preset = this.selectedPreset();
    if (!preset) return `Preset "${selected}" is not available. Browser will fall back to the Main Model.`;

    const chat = preset.chat || {};
    const parts = [chat.provider, chat.name].filter((item) => String(item || "").trim());
    return parts.length ? parts.join(" / ") : "This preset has no Main Model; Browser will fall back to the Main Model.";
  },

  selectedPresetMissing() {
    const selected = String(this.config?.model_preset || "").trim();
    return Boolean(selected && this._presetsLoaded && !this.selectedPreset());
  },

  openPresets() {
    void globalThis.openModal?.("/plugins/_model_config/webui/main.html");
  },
});
