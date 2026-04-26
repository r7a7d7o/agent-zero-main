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

export default async function registerOfficeSurface(canvas) {
  canvas.registerSurface({
    id: "office",
    title: "Office",
    icon: "description",
    order: 20,
    modalPath: "/plugins/_office/webui/main.html",
    async open(payload = {}) {
      const panel = await waitForElement('[data-surface-id="office"] .office-panel');
      const office = globalThis.Alpine?.store?.("office");
      await office?.onMount?.(panel, { mode: "canvas" });
      await office?.onOpen?.(payload);
    },
    async close() {
      const office = globalThis.Alpine?.store?.("office");
      office?.beforeHostHidden?.();
    },
  });
}
