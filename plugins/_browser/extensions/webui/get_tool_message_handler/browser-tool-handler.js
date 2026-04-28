import {
  createActionButton,
  copyToClipboard,
} from "/components/messages/action-buttons/simple-action-buttons.js";
import { store as stepDetailStore } from "/components/modals/process-step-detail/step-detail-store.js";
import { store as speechStore } from "/components/chat/speech/speech-store.js";
import { store as rightCanvasStore } from "/components/canvas/right-canvas-store.js";
import { store as browserStore } from "/plugins/_browser/webui/browser-store.js";
import {
  buildDetailPayload,
  cleanStepTitle,
  drawProcessStep,
} from "/js/messages.js";

const BROWSER_MODAL = "/plugins/_browser/webui/main.html";
const AUTO_OPEN_WINDOW_MS = 10 * 60 * 1000;
const autoOpenedBrowsers = new Set();

export default async function registerBrowserToolHandler(extData) {
  if (extData?.tool_name === "browser") {
    extData.handler = drawBrowserTool;
  }
}

async function openBrowserCanvas(payload = {}) {
  if (rightCanvasStore?.open) {
    await rightCanvasStore.open("browser", payload);
    return;
  }

  if (window.ensureModalOpen) {
    await window.ensureModalOpen(BROWSER_MODAL);
    return;
  }
  if (window.openModal) {
    await window.openModal(BROWSER_MODAL);
  }
}

async function browserAllowsToolAutofocus() {
  try {
    if (browserStore.allowsToolAutofocus) {
      return await browserStore.allowsToolAutofocus();
    }
  } catch (error) {
    console.warn("Browser autofocus setting could not be checked", error);
  }
  return true;
}

function parseBrowserResult(content) {
  if (!content || typeof content !== "string") return {};
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function browserIdFromResult(result = {}, kvps = {}) {
  return (
    result.id
    || result.browser_id
    || result.state?.id
    || result.last_interacted_browser_id
    || kvps.browser_id
    || null
  );
}

function browserContextIdFromResult(result = {}, kvps = {}) {
  return (
    result.context_id
    || result.state?.context_id
    || kvps.context_id
    || kvps.contextId
    || null
  );
}

function isFreshToolMessage(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) return true;
  const messageMs = value > 10_000_000_000 ? value : value * 1000;
  return Math.abs(Date.now() - messageMs) <= AUTO_OPEN_WINDOW_MS;
}

function shouldAutoOpenBrowser(args, result) {
  if (!isFreshToolMessage(args?.timestamp)) return false;
  const action = String(args?.kvps?.action || "").trim().toLowerCase().replace("-", "_");
  if (["list", "content", "detail", "close", "close_all"].includes(action)) return false;
  return Boolean(browserIdFromResult(result, args?.kvps || {}) || action === "open" || action === "navigate");
}

function autoOpenBrowserCanvas(args, result) {
  if (!shouldAutoOpenBrowser(args, result)) return;
  const kvps = args?.kvps || {};
  const browserId = browserIdFromResult(result, kvps);
  const key = `${args.id || ""}:${kvps.action || ""}:${browserId || ""}:${result.currentUrl || result.state?.currentUrl || kvps.url || ""}`;
  const persistedKey = `a0.browser.autoOpened.${key}`;
  if (autoOpenedBrowsers.has(key) || sessionStorage.getItem(persistedKey)) return;
  autoOpenedBrowsers.add(key);
  sessionStorage.setItem(persistedKey, "1");
  requestAnimationFrame(async () => {
    if (!(await browserAllowsToolAutofocus())) return;
    void openBrowserCanvas({
      browserId,
      contextId: browserContextIdFromResult(result, kvps),
      source: "tool",
    });
  });
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
  const args = arguments[0];
  const title = cleanStepTitle(heading);
  const displayKvps = { ...kvps };
  const headerLabels = [
    kvps?._tool_name && { label: kvps._tool_name, class: "tool-name-badge" },
  ].filter(Boolean);
  const contentText = String(content ?? "");
  const browserResult = parseBrowserResult(contentText);
  const browserButton = createActionButton(
    "visibility",
    "Browser",
    () => openBrowserCanvas({
      browserId: browserIdFromResult(browserResult, kvps),
      contextId: browserContextIdFromResult(browserResult, kvps),
      source: "tool",
    }),
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
          buildDetailPayload(args, { headerLabels }),
        ),
      ),
      createActionButton("speak", "", () => speechStore.speak(contentText)),
      createActionButton("copy", "", () => copyToClipboard(contentText)),
    );
  }

  const result = drawProcessStep({
    id,
    title,
    code: "WWW",
    classes: undefined,
    kvps: displayKvps,
    content,
    actionButtons: actionButtons.filter(Boolean),
    log: args,
  });
  autoOpenBrowserCanvas(args, browserResult);
  return result;
}
