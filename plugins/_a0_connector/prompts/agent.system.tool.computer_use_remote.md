# computer_use_remote tool

Use the connected CLI host machine as a local desktop target.

## Preferred Scope
- Use this for local desktop and native UI tasks on the connected machine.
- For ordinary website browsing, search, form filling, and web downloads, prefer `browser_agent`.
- If the user is flexible and the task is browser-only, briefly guide them toward browser tools because they are usually more reliable and token-efficient than screenshot-driven computer use.
- Before doing real computer-use work, load the `computer-use-remote` skill and follow it.

## Requirements
- A CLI client must be connected to this context via the shared `/ws` namespace.
- The CLI must advertise `computer_use_remote` support and local computer use must be enabled there.
- In `free_run`, do not expect a fresh approval prompt. If restore is no longer valid, the tool will surface `COMPUTER_USE_REARM_REQUIRED`.

## Minimal Rules
- Treat user interventions as high-priority control signals.
- If the user says `stop`, `pause`, `abort`, `hold`, `don't continue`, or equivalent, halt immediately and do not use computer-use tools again until the user explicitly resumes.
- Call `start_session` first. It automatically attaches the current screen.
- Decide from the latest screenshot, not from memory.
- Interactive actions (`move`, `click`, `scroll`, `key`, `type`) automatically attach a fresh screenshot after they run.
- Use `capture` only when you need another screen refresh without taking an action.
- Prefer keyboard actions over pointer actions whenever a reliable keyboard path exists.

## Arguments
- `action`: one of `start_session`, `status`, `capture`, `move`, `click`, `scroll`, `key`, `type`, `stop_session`
- `session_id`: optional for actions after `start_session`

Action-specific fields:
- `move`: `x`, `y` normalized to `[0,1]`
- `click`: optional `x`, `y`, plus optional `button` (`left`, `right`, `middle`) and `count`
- `scroll`: `dx`, `dy`
- `key`: `key` or `keys`
- `type`: `text`, optional `submit` boolean
