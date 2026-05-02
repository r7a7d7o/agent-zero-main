const SYNC_WINDOW_MS = 10 * 60 * 1000;
const syncedDocumentResults = new Set();

export default async function syncDocumentResultsIntoOpenCanvas(context) {
  if (!context?.results?.length || context.historyEmpty) return;
  if (!isOfficeCanvasAlreadyOpen()) return;

  for (const { args } of context.results) {
    const payload = getDocumentPayload(args);
    if (getToolName(payload) !== "document_artifact") continue;
    if (!shouldSyncOpenOfficeCanvas(args, payload)) continue;

    const document = payload.document && typeof payload.document === "object" ? payload.document : {};
    const path = payload.path || document.path || "";
    const fileId = payload.file_id || document.file_id || "";
    if (!path && !fileId) continue;

    const key = [
      args?.id || "",
      payload.action || "",
      fileId || "",
      path || "",
      payload.version || document.version || "",
    ].join(":");
    if (syncedDocumentResults.has(key)) continue;
    syncedDocumentResults.add(key);

    globalThis.setTimeout(async () => {
      if (!isOfficeCanvasAlreadyOpen()) return;
      const office = globalThis.Alpine?.store?.("office");
      if (!office || isDirtySameDocument(office, { path, file_id: fileId })) return;
      await office.openSession?.({
        path,
        file_id: fileId,
        source: "tool-result-sync",
      });
    }, 0);
  }
}

function getDocumentPayload(args = {}) {
  const contentPayload = parseMaybeJson(args.content);
  const kvpsPayload = args.kvps && typeof args.kvps === "object"
    ? args.kvps
    : parseMaybeJson(args.kvps);
  return {
    ...pickPayloadFields(args),
    ...(contentPayload || {}),
    ...(kvpsPayload || {}),
  };
}

function pickPayloadFields(args = {}) {
  const payload = {};
  for (const key of [
    "_tool_name",
    "tool_name",
    "action",
    "file_id",
    "path",
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

function shouldSyncOpenOfficeCanvas(args = {}, payload = {}) {
  if (!isFresh(args.timestamp, payload.last_modified || payload.document?.last_modified)) return false;
  const action = String(payload.action || "").trim().toLowerCase().replace("-", "_");
  return ["create", "open", "edit", "restore_version"].includes(action);
}

function isOfficeCanvasAlreadyOpen() {
  const canvas = globalThis.Alpine?.store?.("rightCanvas");
  return Boolean(canvas?.isOpen && canvas?.activeSurfaceId === "office");
}

function isDirtySameDocument(office, document = {}) {
  if (!office?.dirty || !office?.session) return false;
  const path = String(document.path || "");
  const fileId = String(document.file_id || "");
  return Boolean(
    (fileId && office.session.file_id === fileId)
      || (path && office.session.path === path),
  );
}

function isFresh(...timestamps) {
  const now = Date.now();
  for (const value of timestamps) {
    const time = parseTimestamp(value);
    if (time && now - time < SYNC_WINDOW_MS) return true;
  }
  return false;
}

function parseTimestamp(value) {
  if (!value) return 0;
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
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
