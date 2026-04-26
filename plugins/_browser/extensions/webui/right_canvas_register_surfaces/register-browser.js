function waitForElement(selector, timeoutMs = 3000) {
  const found = document.querySelector(selector);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    const timeout = globalThis.setTimeout(() => {
      observer.disconnect();
      resolve(null);
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

export default async function registerBrowserSurface(canvas) {
  canvas.registerSurface({
    id: "browser",
    title: "Browser",
    icon: "language",
    order: 10,
    modalPath: "/plugins/_browser/webui/main.html",
    async open() {
      const panel = await waitForElement('[data-surface-id="browser"] .browser-panel');
      const browser = globalThis.Alpine?.store?.("browserPage");
      if (panel && browser?.onOpen) {
        await browser.onOpen(panel, { mode: "canvas" });
      }
    },
    async close() {
      const browser = globalThis.Alpine?.store?.("browserPage");
      await browser?.cleanup?.();
    },
  });
}
