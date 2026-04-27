const AUTO_OPEN_WINDOW_MS = 10 * 60 * 1000;
const autoOpenedDocuments = new Set();

export default async function autoOpenDocumentResults(context) {
  if (!context?.results?.length || context.historyEmpty) return;

  for (const { args } of context.results) {
    const payload = getToolResultPayload(args);
    if (getToolName(payload) !== "document_artifact") continue;

    const document = getDocumentPayload(payload);
    if (!document?.path) continue;
    if (payload.canvas_surface && payload.canvas_surface !== "office") continue;
    if (isReadOnlyAction(payload)) continue;
    if (!isFresh(args?.timestamp, document.last_modified)) continue;

    const key = [
      args?.id || "",
      document.file_id || "",
      document.path,
      document.version || "",
    ].join(":");
    const persistedKey = `a0.office.autoOpened.${key}`;
    if (hasOpened(key, persistedKey)) continue;

    requestAnimationFrame(() => {
      void openOfficeCanvas(document);
    });
  }
}

function getToolResultPayload(args = {}) {
  const topLevelPayload = pickPayloadFields(args);
  const contentPayload = parseMaybeJson(args.content);
  const kvpsPayload = parseMaybeJson(args.kvps);
  return {
    ...topLevelPayload,
    ...(contentPayload || {}),
    ...(kvpsPayload || {}),
  };
}

function pickPayloadFields(args = {}) {
  const payload = {};
  for (const key of [
    "_tool_name",
    "tool_name",
    "tool_result",
    "canvas_surface",
    "action",
    "file_id",
    "path",
    "title",
    "basename",
    "format",
    "extension",
    "version",
    "last_modified",
  ]) {
    if (args[key] != null && args[key] !== "") payload[key] = args[key];
  }
  return payload;
}

function getToolName(payload = {}) {
  return String(payload._tool_name || payload.tool_name || "").trim();
}

function getDocumentPayload(payload = {}) {
  const result = parseMaybeJson(payload.tool_result) || {};
  const document = result.document && typeof result.document === "object"
    ? result.document
    : {};

  return {
    file_id: payload.file_id || document.file_id || "",
    path: payload.path || document.path || "",
    title: payload.title || payload.basename || document.basename || "",
    format: payload.format || payload.extension || document.extension || "",
    version: payload.version || document.version || "",
    last_modified: payload.last_modified || document.last_modified || "",
  };
}

function isReadOnlyAction(payload = {}) {
  const action = String(payload.action || "").trim().toLowerCase();
  return ["status", "version_history", "inspect", "read", "extract"].includes(action);
}

function parseMaybeJson(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  if (typeof value !== "string") return null;

  const trimmed = value.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function isFresh(timestamp, fallbackTimestamp) {
  const messageMs = toMs(timestamp) || toMs(fallbackTimestamp);
  if (!messageMs) return true;
  return Math.abs(Date.now() - messageMs) <= AUTO_OPEN_WINDOW_MS;
}

function toMs(value) {
  if (value == null || value === "") return 0;

  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 10_000_000_000 ? numeric : numeric * 1000;
  }

  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function hasOpened(key, persistedKey) {
  if (autoOpenedDocuments.has(key)) return true;
  autoOpenedDocuments.add(key);

  try {
    if (sessionStorage.getItem(persistedKey)) return true;
    sessionStorage.setItem(persistedKey, "1");
  } catch {
    // Best-effort persistence; the in-memory guard still prevents repeat opens.
  }

  return false;
}

async function openOfficeCanvas(document) {
  const canvas = globalThis.Alpine?.store?.("rightCanvas")
    || (await import("/components/canvas/right-canvas-store.js")).store;
  await canvas?.open?.("office", {
    path: document.path || "",
    file_id: document.file_id || "",
    source: "tool-result",
  });
}
