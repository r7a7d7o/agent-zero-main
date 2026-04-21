# computer_use_remote tool

Use the connected CLI host machine as a local desktop target.

This tool is only usable when the current context has a subscribed CLI with enabled local computer use.
Detailed operating guidance is injected separately only when that condition is true, so the base system prompt stays small when computer use is not in play.

## Requirements
- A CLI client must be connected to this context via the shared `/ws` namespace.
- The CLI must advertise `computer_use_remote` support and local computer use must be enabled there.

## Arguments
- `action`: one of `start_session`, `status`, `capture`, `move`, `click`, `scroll`, `key`, `type`, `stop_session`
- `session_id`: optional for actions after `start_session`

Action-specific fields:
- `move`: `x`, `y` normalized to `[0,1]`
- `click`: optional `x`, `y`, plus optional `button` (`left`, `right`, `middle`) and `count`
- `scroll`: `dx`, `dy`
- `key`: `key` or `keys`
- `type`: `text`, optional `submit` boolean

## Runtime Notes
- Successful `start_session`, `move`, `click`, `scroll`, `key`, and `type` calls automatically attach a fresh screenshot.
- `status` reports the current computer-use state without starting a session.
- Prefer accessibility, semantic UI controls, hotkeys, focus traversal, and other keyboard paths before pointer actions.
- For viewport movement, prefer keyboard scrolling first; use `scroll` when a wheel-style scroll is the most reliable way to move an already-focused viewport or pane.
- Use `move` and `click` only as a last resort when no reliable accessibility or keyboard route is available and the latest screenshot makes the target unambiguous.
