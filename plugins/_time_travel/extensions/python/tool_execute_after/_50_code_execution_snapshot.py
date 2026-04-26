from __future__ import annotations

import time
from typing import Any

from helpers.extension import Extension
from plugins._time_travel.helpers.time_travel import snapshot_for_agent


DEBOUNCE_SECONDS = 2.0
_LAST_SNAPSHOT_BY_CONTEXT: dict[str, float] = {}


class TimeTravelCodeExecutionSnapshot(Extension):
    async def execute(self, tool_name: str = "", response: Any = None, **kwargs: Any):
        if tool_name != "code_execution_tool" or not self.agent:
            return

        context_id = str(getattr(getattr(self.agent, "context", None), "id", "") or "")
        now = time.monotonic()
        if context_id and now - _LAST_SNAPSHOT_BY_CONTEXT.get(context_id, 0.0) < DEBOUNCE_SECONDS:
            return
        if context_id:
            _LAST_SNAPSHOT_BY_CONTEXT[context_id] = now

        tool = getattr(getattr(self.agent, "loop_data", None), "current_tool", None)
        args = getattr(tool, "args", {}) if tool else {}
        runtime = str(args.get("runtime") or "") if isinstance(args, dict) else ""
        if runtime == "output":
            return

        snapshot_for_agent(
            self.agent,
            trigger="code_execution",
            metadata={
                "tool_name": tool_name,
                "runtime": runtime,
            },
        )
