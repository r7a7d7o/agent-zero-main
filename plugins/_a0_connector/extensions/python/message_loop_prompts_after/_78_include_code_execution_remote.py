from __future__ import annotations

from agent import LoopData
from helpers.extension import Extension

from plugins._a0_connector.helpers.exec_config import build_exec_config
from plugins._a0_connector.helpers.ws_runtime import select_remote_exec_target_sid


def _format_timeouts(payload: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in payload.items()) or "none"


def _format_patterns(value: object) -> str:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return ", ".join(items) or "none"


class IncludeCodeExecutionRemote(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        context_id = getattr(self.agent.context, "id", "")
        if not context_id or not select_remote_exec_target_sid(context_id):
            return

        exec_config = build_exec_config(agent=self.agent)
        code_exec_timeouts = exec_config.get("code_exec_timeouts")
        output_timeouts = exec_config.get("output_timeouts")
        prompt_patterns = exec_config.get("prompt_patterns")
        dialog_patterns = exec_config.get("dialog_patterns")

        prompt = self.agent.read_prompt(
            "agent.extras.code_execution_remote.md",
            code_exec_timeouts=_format_timeouts(
                code_exec_timeouts if isinstance(code_exec_timeouts, dict) else {}
            ),
            output_timeouts=_format_timeouts(
                output_timeouts if isinstance(output_timeouts, dict) else {}
            ),
            prompt_patterns=_format_patterns(prompt_patterns),
            dialog_patterns=_format_patterns(dialog_patterns),
        )
        loop_data.extras_temporary["code_execution_remote"] = prompt
