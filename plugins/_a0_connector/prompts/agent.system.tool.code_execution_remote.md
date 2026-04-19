# code_execution_remote tool

This tool runs shell-backed execution on the **remote machine where the CLI is running**.
Detailed usage guidance is injected separately only when the current context has a
subscribed CLI, so the base system prompt stays small when remote execution is not in play.

## Requirements
- A CLI client must be connected to this context via the shared `/ws` namespace.
- The CLI client must support `connector_exec_op`.
- Frontend execution may be locally disabled in the CLI session; in that case the result is
  a structured `{ok: false}` error and no fallback runtime is used.

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
- The transport uses `connector_exec_op` and `connector_exec_op_result` with shared `op_id`.
