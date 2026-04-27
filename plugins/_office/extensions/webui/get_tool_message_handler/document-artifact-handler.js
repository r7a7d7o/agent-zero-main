import {
  createActionButton,
  copyToClipboard,
} from "/components/messages/action-buttons/simple-action-buttons.js";
import { store as stepDetailStore } from "/components/modals/process-step-detail/step-detail-store.js";
import { store as speechStore } from "/components/chat/speech/speech-store.js";
import {
  buildDetailPayload,
  cleanStepTitle,
  drawProcessStep,
} from "/js/messages.js";

const AUTO_OPEN_WINDOW_MS = 10 * 60 * 1000;
const autoOpenedDocuments = new Set();

export default async function registerDocumentArtifactHandler(extData) {
  if (extData?.tool_name === "document_artifact") {
    extData.handler = drawDocumentArtifactTool;
  }
}

async function openOfficeCanvas(kvps = {}) {
  const canvas = globalThis.Alpine?.store?.("rightCanvas")
    || (await import("/components/canvas/right-canvas-store.js")).store;
  await canvas?.open?.("office", {
    path: kvps.path || "",
    file_id: kvps.file_id || "",
    source: "tool",
  });
}

function parseDocumentResult(content) {
  if (!content || typeof content !== "string") return {};
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function documentFromArgs(args, result = {}) {
  const kvps = args?.kvps || {};
  const document = result.document && typeof result.document === "object"
    ? result.document
    : {};
  return {
    file_id: kvps.file_id || document.file_id || "",
    path: kvps.path || document.path || "",
    title: kvps.title || kvps.basename || document.basename || "",
    format: kvps.format || kvps.extension || document.extension || "",
    version: kvps.version || document.version || "",
  };
}

function shouldAutoOpenDocument(args, document) {
  const kvps = args?.kvps || {};
  if (kvps.canvas_surface && kvps.canvas_surface !== "office") return false;
  if (!document?.path) return false;
  const action = String(kvps.action || "").trim().toLowerCase();
  if (["status", "version_history", "inspect", "read", "extract"].includes(action)) return false;
  return isFreshToolMessage(args?.timestamp);
}

function isFreshToolMessage(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) return true;
  const messageMs = value > 10_000_000_000 ? value : value * 1000;
  return Math.abs(Date.now() - messageMs) <= AUTO_OPEN_WINDOW_MS;
}

function autoOpenOfficeCanvas(args) {
  const document = documentFromArgs(args, parseDocumentResult(args?.content));
  if (!shouldAutoOpenDocument(args, document)) return;
  const key = `${args.id || ""}:${document.file_id || ""}:${document.path || ""}:${document.version || ""}`;
  const persistedKey = `a0.office.autoOpened.${key}`;
  if (hasOpenedDocument(key, persistedKey)) return;
  requestAnimationFrame(() => {
    void openOfficeCanvas(document);
  });
}

function hasOpenedDocument(key, persistedKey) {
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

function drawDocumentArtifactTool({
  id,
  type,
  heading,
  content,
  kvps,
  timestamp,
  agentno = 0,
  ...additional
}) {
  const args = arguments[0];
  const title = cleanStepTitle(heading);
  const displayKvps = { ...kvps };
  const contentText = String(content ?? "");
  const documentResult = parseDocumentResult(contentText);
  const document = documentFromArgs(args, documentResult);
  const headerLabels = [
    kvps?._tool_name && { label: kvps._tool_name, class: "tool-name-badge" },
    document?.format && { label: String(document.format).toUpperCase(), class: "tool-name-badge" },
  ].filter(Boolean);

  const actionButtons = [
    createActionButton("description", "Office", () => openOfficeCanvas(document)),
  ];

  if (document?.path) {
    actionButtons.push(
      createActionButton("content_copy", "Path", () => copyToClipboard(document.path)),
    );
  }

  if (contentText.trim()) {
    actionButtons.push(
      createActionButton("detail", "", () =>
        stepDetailStore.showStepDetail(buildDetailPayload(args, { headerLabels })),
      ),
      createActionButton("speak", "", () => speechStore.speak(contentText)),
      createActionButton("copy", "", () => copyToClipboard(contentText)),
    );
  }

  const result = drawProcessStep({
    id,
    title,
    code: "DOC",
    classes: undefined,
    kvps: displayKvps,
    content,
    actionButtons: actionButtons.filter(Boolean),
    log: args,
  });
  autoOpenOfficeCanvas(args);
  return result;
}
