## text_editor_remote guidance

Remote file editing is currently available in this context through the connected CLI.
Current access mode: `{{access_mode}}`

- Use `text_editor_remote` when the user asks you to edit files on their local machine while connected via the CLI.
- Paths are evaluated on the remote CLI machine's filesystem, not on the Agent Zero server.
- Prefer `read` before `patch` so you have current line numbers and freshness metadata.
- `read` is always the safest first step for inspecting the local file.
{{write_guidance}}

Examples:

```json
{
  "tool_name": "text_editor_remote",
  "tool_args": {
    "op": "read",
    "path": "/path/on/remote/machine/file.py",
    "line_from": 1,
    "line_to": 50
  }
}
```
{{write_examples}}
