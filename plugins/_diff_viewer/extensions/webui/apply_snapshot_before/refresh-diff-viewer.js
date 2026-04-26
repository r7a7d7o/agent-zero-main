export default function refreshDiffViewerOnContextChange(ctx) {
  const diffViewer = globalThis.Alpine?.store?.("diffViewer");
  const canvas = globalThis.Alpine?.store?.("rightCanvas");
  if (!diffViewer || !canvas?.isOpen || canvas.activeSurfaceId !== "diff") return;
  const nextContextId = String(ctx?.snapshot?.context || "");
  if (nextContextId && nextContextId !== diffViewer.contextId) {
    diffViewer.scheduleRefresh({ contextId: nextContextId, reason: "context-change" });
  }
}
