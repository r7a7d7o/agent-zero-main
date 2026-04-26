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

export default async function registerTimeTravelSurface(canvas) {
  canvas.registerSurface({
    id: "time-travel",
    title: "Time Travel",
    icon: "history",
    order: 30,
    modalPath: "/plugins/_time_travel/webui/main.html",
    async open(payload = {}) {
      await waitForElement('[data-surface-id="time-travel"] .time-travel-panel');
      const store = globalThis.Alpine?.store?.("timeTravel");
      await store?.onOpen?.(payload);
    },
    async close() {
      const store = globalThis.Alpine?.store?.("timeTravel");
      store?.cleanup?.();
    },
  });
}
