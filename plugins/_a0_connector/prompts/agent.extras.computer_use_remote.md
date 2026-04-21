## computer_use_remote guidance

Computer use is currently available in this context.
Backend: `{{backend}}`
Trust mode: `{{trust_mode}}`
Features: `{{features}}`
Support note: `{{support_reason}}`

- Use this for local desktop and native UI tasks on the connected machine.
- If the task is browser-only and the user is flexible, prefer `browser_agent` because it is usually more reliable and token-efficient than screenshot-driven desktop control.
- Use `start_session` before interactive desktop actions. `status` is for inspection; `stop_session` ends the session.
- Base every decision on the latest screenshot or a definitive tool result, not memory.
- Successful `start_session`, `move`, `click`, `scroll`, `key`, and `type` calls already attach a fresh screenshot.
- Use `capture` only when you need a screen refresh without taking another action.
- Prefer accessibility and semantic UI paths first: application shortcuts, command palettes, menu accelerators, address/search bars, focus traversal, selection shortcuts, and other keyboard-accessible controls.
- Prefer `key` and `type` over pointer actions whenever there is a plausible keyboard or accessibility path. Use `tab`, `shift+tab`, arrow keys, hotkeys, text search, and submit keys before reaching for the mouse.
- For viewport movement, try keyboard scrolling first: `page_down`, `page_up`, `space`, `shift+space`, arrow keys, `home`, or `end`. Use `scroll` when the desired scrollable region is already active or a keyboard route cannot target it; prefer `scroll` over click-dragging or clicking scrollbars.
- Treat `move` and `click` as last-resort actions for controls that cannot be reached or activated reliably through accessibility, hotkeys, keyboard navigation, or browser/app-native tooling.
- Before clicking, make sure the latest screenshot makes the target unambiguous and that a keyboard/accessibility route has already been tried or ruled out. Use one deliberate click, then reassess from the fresh screenshot.
- Treat menus and popups as transient UI. If a click dismisses one without visible progress, treat that attempt as failed and switch to a non-pointer strategy.
- If the same approach has already failed twice without visible progress, stop repeating it and try a different strategy.
- For browser work done through this tool, only claim success when the page content area visibly shows the expected destination or result.
- Use `type(..., submit=true)` only for navigation-style entry such as an address bar or command box. For ordinary text fields, type first and send `enter` separately only if needed.
- In `free_run`, do not expect a fresh approval prompt. If silent restore is no longer valid, expect `COMPUTER_USE_REARM_REQUIRED`.
- Treat user interventions as high-priority control signals. If the user says `stop`, `pause`, `abort`, `hold`, `don't continue`, or equivalent, stop using computer-use tools until the user explicitly resumes.
