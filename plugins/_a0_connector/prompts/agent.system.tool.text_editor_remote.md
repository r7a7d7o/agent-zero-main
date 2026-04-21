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
- `patch`: requires either `patch_text` or `edits`

## Notes
- Paths are evaluated on the **remote machine's filesystem**, not the Agent Zero server.
- The transport uses `connector_file_op` and `connector_file_op_result` with a shared `op_id`.
- `patch_text` uses context chunks and does not require fresh line numbers.
- `patch_text` line rules: `@@ existing line` anchors the hunk; `+new` inserts after the anchor when there are no context or delete lines; `-old` then `+new` replaces the next matching old line after the anchor, or the anchor line itself when `@@` is the old target line.
- For replacements, do not repeat the same old line as both a space-context line and a `-old` line.
- Every non-header content line in `patch_text` must start with exactly one prefix: space for kept context, `+` for added content, or `-` for removed content. Do not emit raw unprefixed content lines.
- Do not stack multiple `@@` lines for one insert. Use one anchor, then the `+` lines to insert.
- `edits` uses 1-based line ranges and may require rereading after line-count changes.
