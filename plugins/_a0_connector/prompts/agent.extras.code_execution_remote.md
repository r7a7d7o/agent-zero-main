## code_execution_remote guidance

Remote code execution is currently available in this context through the connected CLI.

Execution config:
- code execution timeouts: `{{code_exec_timeouts}}`
- output polling timeouts: `{{output_timeouts}}`
- prompt patterns: `{{prompt_patterns}}`
- dialog patterns: `{{dialog_patterns}}`

- Use this tool for shell-backed execution on the remote CLI machine, not on the Agent Zero server.
- Session ids are frontend-local and persistent across calls. Reuse the same `session` when continuing a workflow.
- Use `runtime=terminal` for shell commands, `runtime=python` for Python snippets, and `runtime=nodejs` for Node.js snippets.
- Use `runtime=output` to poll a running session after a prior call returned before the shell settled.
- Use `runtime=reset` when a session is stuck or you need a clean shell.
- `runtime=input` is only a deprecated compatibility alias for sending one line of keyboard input into a running shell session.
- Frontend execution may still be locally disabled in the CLI session. If so, expect a structured `{ok: false}` error instead of a fallback runtime.
- Prefer concise, self-checking commands. For multi-step work, inspect output and continue in the same session instead of restarting from scratch.

Examples:

```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "terminal",
    "session": 0,
    "code": "pwd && ls -la"
  }
}
```

```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "python",
    "session": 0,
    "code": "import os\nprint(os.getcwd())"
  }
}
```

```json
{
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "output",
    "session": 0
  }
}
```
