const SPACE_AGENT_URL = "https://space-agent.ai/";

export default async function registerSpaceAgentAction(canvas) {
  canvas.registerSurface({
    id: "space-agent",
    title: "Space Agent",
    image: "/public/space-agent-icon-512.webp",
    order: 32,
    actionOnly: true,
    open() {
      globalThis.open(SPACE_AGENT_URL, "_blank", "noopener,noreferrer");
    },
  });
}
