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
    refresh: true,
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
    last_modified: kvps.last_modified || document.last_modified || "",
  };
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
    createActionButton("desktop_windows", "Desktop", () => openOfficeCanvas(document)),
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
  return result;
}
