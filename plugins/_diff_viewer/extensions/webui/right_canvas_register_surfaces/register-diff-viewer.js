function waitForElement(selector, timeoutMs = 3000) {
  const found = document.querySelector(selector);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    const timeout = globalThis.setTimeout(() => {
      observer.disconnect();
      resolve(document.querySelector(selector));
    }, timeoutMs);
    const observer = new MutationObserver(() => {
      const element = document.querySelector(selector);
      if (!element) return;
      globalThis.clearTimeout(timeout);
      observer.disconnect();
      resolve(element);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

export default async function registerDiffViewerSurface(canvas) {
  canvas.registerSurface({
    id: "diff",
    title: "Diff",
    icon: "difference",
    order: 30,
    modalPath: "/plugins/_diff_viewer/webui/main.html",
    async open(payload = {}) {
      await waitForElement('[data-surface-id="diff"] .diff-viewer-panel');
      const diffViewer = globalThis.Alpine?.store?.("diffViewer");
      await diffViewer?.onOpen?.(payload);
    },
    async close() {
      const diffViewer = globalThis.Alpine?.store?.("diffViewer");
      diffViewer?.cleanup?.();
    },
  });
}
