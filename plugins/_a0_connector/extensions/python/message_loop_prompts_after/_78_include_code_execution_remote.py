from __future__ import annotations

from agent import LoopData
from helpers.extension import Extension

from plugins._a0_connector.helpers.exec_config import build_exec_config
from plugins._a0_connector.helpers.ws_runtime import (
    remote_file_metadata_for_sid,
    select_remote_exec_target_sid,
)


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
        if not context_id:
            return

        sid = select_remote_exec_target_sid(context_id, require_writes=False)
        if not sid:
            return

        metadata = remote_file_metadata_for_sid(sid)
        if metadata is None:
            access_mode = "Read&Write (legacy/unknown)"
            write_runtime_guidance = (
                "- `runtime=terminal`, `python`, `nodejs`, and `input` are expected to be "
                "available, but this CLI did not advertise an explicit F3 access mode.\n"
                "- Use shell syntax that matches the remote host (for example, PowerShell on "
                "Windows)."
            )
            write_runtime_examples = """```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "terminal",
    "session": 0,
    "code": "pwd"
  }
}
```

```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "python",
    "session": 0,
    "code": "import os\\nprint(os.getcwd())"
  }
}
```"""
        elif metadata.get("write_enabled"):
            access_mode = "Read&Write"
            write_runtime_guidance = (
                "- `runtime=terminal`, `python`, `nodejs`, and `input` may modify files on "
                "the remote CLI machine. Use them only when shell-backed execution is the "
                "right tool for the job.\n"
                "- Use shell syntax that matches the remote host (for example, PowerShell on "
                "Windows)."
            )
            write_runtime_examples = """```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "terminal",
    "session": 0,
    "code": "pwd"
  }
}
```

```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "python",
    "session": 0,
    "code": "import os\\nprint(os.getcwd())"
  }
}
```"""
        else:
            access_mode = "Read only"
            write_runtime_guidance = (
                "- `runtime=terminal`, `python`, `nodejs`, and `input` are disabled while "
                "local access is Read only. Press F3 to switch the host machine to Read&Write "
                "before starting new shell-backed work that could modify files."
            )
            write_runtime_examples = ""

        exec_config = build_exec_config(agent=self.agent)
        code_exec_timeouts = exec_config.get("code_exec_timeouts")
        output_timeouts = exec_config.get("output_timeouts")
        prompt_patterns = exec_config.get("prompt_patterns")
        dialog_patterns = exec_config.get("dialog_patterns")

        prompt = self.agent.read_prompt(
            "agent.extras.code_execution_remote.md",
            access_mode=access_mode,
            write_runtime_guidance=write_runtime_guidance,
            write_runtime_examples=write_runtime_examples,
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
