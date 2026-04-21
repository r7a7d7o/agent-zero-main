from __future__ import annotations

from agent import LoopData
from helpers.extension import Extension

from plugins._a0_connector.helpers.ws_runtime import (
    remote_file_metadata_for_sid,
    select_remote_file_target_sid,
)


class IncludeTextEditorRemote(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        context_id = getattr(self.agent.context, "id", "")
        if not context_id:
            return

        sid = select_remote_file_target_sid(context_id)
        if not sid:
            return

        metadata = remote_file_metadata_for_sid(sid)
        if metadata is None:
            access_mode = "Read&Write (legacy/unknown)"
            write_guidance = (
                "- Writes and patches are expected to be available, but this CLI did not "
                "advertise an explicit F3 access mode.\n"
                "- Prefer `patch_text` for context-anchored edits when supported."
            )
            write_examples = """```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "write",
    "path": "/path/on/remote/machine/file.py",
    "content": "import os\\nprint('hello')\\n"
  }
}
```

```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "patch",
    "path": "/path/on/remote/machine/file.py",
    "patch_text": "*** Begin Patch\\n*** Update File: /path/on/remote/machine/file.py\\n@@ def main():\\n+    setup()\\n*** End Patch"
  }
}
```"""
        elif metadata.get("write_enabled"):
            access_mode = "Read&Write"
            write_guidance = (
                "- Use `write` only when replacing or creating the full file is the right operation.\n"
                "- Use `patch` with `patch_text` for context-anchored edits, especially after inserts/deletes or when line numbers may have shifted.\n"
                "- Use `patch` with `edits` only for surgical line-range edits based on the latest remote read.\n"
                "- Freshness-aware line patching may reject stale edits. If a line patch requires a reread, read the file again and then retry with updated ranges."
            )
            write_examples = """```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "write",
    "path": "/path/on/remote/machine/file.py",
    "content": "import os\\nprint('hello')\\n"
  }
}
```

```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "patch",
    "path": "/path/on/remote/machine/file.py",
    "patch_text": "*** Begin Patch\\n*** Update File: /path/on/remote/machine/file.py\\n@@ def main():\\n+    setup()\\n*** End Patch"
  }
}
```

```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "patch",
    "path": "/path/on/remote/machine/file.py",
    "patch_text": "*** Begin Patch\\n*** Update File: /path/on/remote/machine/file.py\\n@@ def main():\\n-    old_helper()\\n+    new_helper()\\n*** End Patch"
  }
}
```

```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "patch",
    "path": "/path/on/remote/machine/file.py",
    "edits": [
      {"from": 5, "to": 5, "content": "    if x == 2:\\n"}
    ]
  }
}
```"""
        else:
            access_mode = "Read only"
            write_guidance = (
                "- Writes and patches are disabled in this CLI session. Press F3 to switch the host machine to Read&Write before attempting `write` or `patch`."
            )
            write_examples = ""

        prompt = self.agent.read_prompt(
            "agent.extras.text_editor_remote.md",
            access_mode=access_mode,
            write_guidance=write_guidance,
            write_examples=write_examples,
        )
        loop_data.extras_temporary["text_editor_remote"] = prompt
