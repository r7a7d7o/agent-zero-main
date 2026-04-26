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
    source: "tool",
  });
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
  const headerLabels = [
    kvps?._tool_name && { label: kvps._tool_name, class: "tool-name-badge" },
    kvps?.format && { label: String(kvps.format).toUpperCase(), class: "tool-name-badge" },
  ].filter(Boolean);

  const actionButtons = [
    createActionButton("description", "Office", () => openOfficeCanvas(kvps)),
  ];

  if (kvps?.path) {
    actionButtons.push(
      createActionButton("content_copy", "Path", () => copyToClipboard(kvps.path)),
    );
  }

  if (contentText.trim()) {
    actionButtons.push(
      createActionButton("history", "Versions", () =>
        stepDetailStore.showStepDetail(buildDetailPayload(args, { headerLabels })),
      ),
      createActionButton("detail", "", () =>
        stepDetailStore.showStepDetail(buildDetailPayload(args, { headerLabels })),
      ),
      createActionButton("speak", "", () => speechStore.speak(contentText)),
      createActionButton("copy", "", () => copyToClipboard(contentText)),
    );
  }

  return drawProcessStep({
    id,
    title,
    code: "DOC",
    classes: undefined,
    kvps: displayKvps,
    content,
    actionButtons: actionButtons.filter(Boolean),
    log: args,
  });
}
