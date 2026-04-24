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

const BROWSER_MODAL = "/plugins/_browser/webui/main.html";

export default async function registerBrowserToolHandler(extData) {
  if (extData?.tool_name === "browser") {
    extData.handler = drawBrowserTool;
  }
}

function drawBrowserTool({
  id,
  type,
  heading,
  content,
  kvps,
  timestamp,
  agentno = 0,
  ...additional
}) {
  const title = cleanStepTitle(heading);
  const displayKvps = { ...kvps };
  const headerLabels = [
    kvps?._tool_name && { label: kvps._tool_name, class: "tool-name-badge" },
  ].filter(Boolean);
  const contentText = String(content ?? "");
  const browserButton = createActionButton(
    "visibility",
    "Browser",
    () => {
      if (window.ensureModalOpen) {
        void window.ensureModalOpen(BROWSER_MODAL);
        return;
      }
      void window.openModal?.(BROWSER_MODAL);
    },
  );
  browserButton.setAttribute("title", "Open Browser");
  browserButton.setAttribute("aria-label", "Open Browser");
  browserButton.setAttribute("data-bs-placement", "top");
  browserButton.setAttribute("data-bs-trigger", "hover");
  const actionButtons = [browserButton];

  if (contentText.trim()) {
    actionButtons.push(
      createActionButton("detail", "", () =>
        stepDetailStore.showStepDetail(
          buildDetailPayload(arguments[0], { headerLabels }),
        ),
      ),
      createActionButton("speak", "", () => speechStore.speak(contentText)),
      createActionButton("copy", "", () => copyToClipboard(contentText)),
    );
  }

  return drawProcessStep({
    id,
    title,
    code: "WWW",
    classes: undefined,
    kvps: displayKvps,
    content,
    actionButtons: actionButtons.filter(Boolean),
    log: arguments[0],
  });
}
