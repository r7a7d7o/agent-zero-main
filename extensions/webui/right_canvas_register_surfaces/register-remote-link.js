const REMOTE_LINK_MODAL_PATH = "settings/tunnel/remote-link.html";

export default async function registerRemoteLinkAction(canvas) {
  canvas.registerSurface({
    id: "remote-link",
    title: "Remote Link",
    icon: "share",
    order: 31,
    actionOnly: true,
    async open() {
      if (typeof globalThis.ensureModalOpen === "function") {
        await globalThis.ensureModalOpen(REMOTE_LINK_MODAL_PATH);
        return;
      }
      await globalThis.openModal?.(REMOTE_LINK_MODAL_PATH);
    },
  });
}
