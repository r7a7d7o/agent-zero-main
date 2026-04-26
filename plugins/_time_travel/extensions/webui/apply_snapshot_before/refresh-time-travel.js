export default function refreshTimeTravelOnContextChange(ctx) {
  const store = globalThis.Alpine?.store?.("timeTravel");
  const canvas = globalThis.Alpine?.store?.("rightCanvas");
  if (!store || !canvas?.isOpen || canvas.activeSurfaceId !== "time-travel") return;
  const nextContextId = String(ctx?.snapshot?.context || "");
  if (nextContextId && nextContextId !== store.contextId) {
    store.scheduleRefresh({ contextId: nextContextId, reason: "context-change" });
  }
}
