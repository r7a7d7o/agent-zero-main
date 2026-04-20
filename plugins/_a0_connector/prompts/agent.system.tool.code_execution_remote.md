# code_execution_remote tool

This tool runs shell-backed execution on the **remote machine where the CLI is running**.
Detailed usage guidance is injected separately only when the current context has a
subscribed CLI, so the base system prompt stays small when remote execution is not in play.

## Requirements
- A CLI client must be connected to this context via the shared `/ws` namespace.
- The CLI client must support `connector_exec_op`.
- Frontend execution may be locally disabled in the CLI session; in that case the result is
  a structured `{ok: false}` error and no fallback runtime is used.
- Mutating runtimes (`terminal`, `python`, `nodejs`, and `input`) also require the CLI
  session to advertise local access mode `Read&Write` via F3. `output` and `reset` can
  still be used for existing sessions while the CLI is in `Read only`.

## Arguments
- `runtime`: one of `terminal`, `python`, `nodejs`, `output`, `reset`
- `runtime=input` is a temporary deprecated compatibility alias for sending one line of
  keyboard input into a running shell session
- `session`: integer session id (default `0`)

Runtime-specific fields:
- `terminal`, `python`, `nodejs`: require `code`
- `input`: requires `keyboard` (or `code` as fallback)
- `reset`: optional `reason`

## Notes
- Session state is frontend-local and shell-backed.
- `output` is for long-running operations where a prior call returned control before the
  shell reached a prompt.
- Use shell syntax that matches the remote host (for example, PowerShell on Windows).
- The transport uses `connector_exec_op` and `connector_exec_op_result` with shared `op_id`.
