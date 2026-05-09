---
name: text-editor-remote
description: Guide safe use of text_editor_remote for reading, writing, and patching files on the connected A0 CLI host machine.
version: 1.0.0
author: Agent Zero Team
tags: ["agent-zero", "a0", "cli", "connector", "remote-files", "file-editing"]
trigger_patterns:
  - "text_editor_remote"
  - "remote file editing"
  - "edit my local files through a0 cli"
  - "read files on the cli host"
  - "patch files on the cli host"
  - "connected local files"
  - "connected local machine files"
  - "local files not docker"
  - "a0 cli files"
  - "cli host files"
allowed_tools:
  - text_editor_remote
---

# Text Editor Remote

## Boundary

Use `text_editor_remote` only for file work on the machine where A0 CLI is running. These paths and files belong to the CLI host, not the Agent Zero server or Docker container.

If the task belongs inside Agent Zero's own runtime, use the normal server-side file tools instead.

## Access Modes

- Read&Write: reads, writes, and patches may modify the CLI host. Keep changes narrow and intentional.
- Read only: inspect files only. If writes are blocked, tell the user to switch local file access to Read&Write with F3.

## Editing Flow

- Start with `read` when inspecting a file or preparing line-based edits.
- Use `write` only when replacing or creating the whole file is truly the right operation.
- Prefer `patch` with `patch_text` for context-anchored edits, especially after inserts/deletes or when line numbers may have shifted.
- Use `patch` with `edits` only for small line-range edits based on the latest remote read.
- If freshness-aware line patching rejects an edit as stale, reread the file and retry with updated ranges.

## Patch Text Rules

- `patch_text` supports update hunks for one file.
- Use one `@@ existing line` anchor, then `+new line` entries for insertion.
- For replacement, use `@@ before target` followed by `-old` and `+new`, or use `@@ old target` followed by the same replacement pair.
- Do not repeat the same old line as both context and deletion in one hunk.
- Every non-header content line must begin with exactly one prefix: space for context, `+` for additions, or `-` for removals.
- Do not stack multiple `@@` anchors for one insert.

## Failure Handling

- If no CLI is connected, ask the user to connect A0 CLI to this Agent Zero instance.
- If writes are blocked, tell the user to switch local file access to Read&Write with F3.
- If a request times out or the CLI disconnects, summarize the failure and wait for reconnection.
