from __future__ import annotations

from agent import LoopData
from helpers.extension import Extension

from plugins._a0_connector.helpers.ws_runtime import (
    computer_use_metadata_for_sid,
    select_computer_use_target_sid,
)


class IncludeComputerUseRemote(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        context_id = getattr(self.agent.context, "id", "")
        if not context_id:
            return

        sid = select_computer_use_target_sid(context_id)
        if not sid:
            return

        metadata = computer_use_metadata_for_sid(sid)
        if not metadata or not metadata.get("supported") or not metadata.get("enabled"):
            return

        backend_id = str(metadata.get("backend_id") or "").strip() or "unknown"
        backend_family = str(metadata.get("backend_family") or "").strip()
        backend = backend_id if not backend_family else f"{backend_id}/{backend_family}"
        trust_mode = str(metadata.get("trust_mode") or "").strip() or "unknown"
        support_reason = str(metadata.get("support_reason") or "").strip() or "No support details available."

        features_value = metadata.get("features")
        if isinstance(features_value, (list, tuple)):
            features = ", ".join(str(item).strip() for item in features_value if str(item).strip())
        else:
            features = ""
        if not features:
            features = "none advertised"

        prompt = self.agent.read_prompt(
            "agent.extras.computer_use_remote.md",
            backend=backend,
            trust_mode=trust_mode,
            features=features,
            support_reason=support_reason,
        )
        loop_data.extras_temporary["computer_use_remote"] = prompt
