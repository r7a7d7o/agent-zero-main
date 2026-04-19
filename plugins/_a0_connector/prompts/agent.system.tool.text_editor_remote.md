# text_editor_remote tool

This tool allows you to read, write, and patch files on the **remote machine where the CLI is running**.
This is different from `text_editor` which operates on the Agent Zero server's filesystem.
Detailed usage guidance is injected separately only when the current context has a
subscribed CLI, so the base system prompt stays small when remote editing is not in play.

## Requirements
- A CLI client must be connected to this context via the shared `/ws` namespace.
- The CLI client must have enabled remote file editing support.

## Operations
- `read`: optional `line_from`, `line_to`
- `write`: requires `content`
- `patch`: requires `edits`

## Notes
- Paths are evaluated on the **remote machine's filesystem**, not the Agent Zero server.
- The transport uses `connector_file_op` and `connector_file_op_result` with a shared `op_id`.
