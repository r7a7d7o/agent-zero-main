---
name: computer-use-remote
description: Beta local desktop control through a connected A0 CLI host; use for screenshots, menus, browser chrome, and native UI tasks.
version: 1.1.0
author: Agent Zero Team
tags: ["computer-use", "desktop", "local-ui", "screenshots", "native-ui"]
trigger_patterns:
  - "computer use"
  - "computer-use"
  - "computer_use_remote"
  - "local desktop control"
  - "control local browser"
  - "click on screen"
  - "native ui"
allowed_tools:
  - computer_use_remote
---

# Computer Use Remote

This skill unlocks the beta `computer_use_remote` tool for connected local desktop control through A0 CLI.

## When to Use

Load this skill before using `computer_use_remote` for local desktop and native UI tasks on the connected machine.

If the task is browser-only and the user is flexible, prefer direct browser tooling because it is usually more reliable and token-efficient than screenshot-driven desktop control.

If the task needs shell execution on the CLI host, load `code-execution-remote` separately rather than treating desktop control and shell execution as one affordance.

## Tool Contract

Use:

```json
{
  "tool_name": "computer_use_remote",
  "tool_args": {
    "action": "start_session"
  }
}
```

Arguments:

- `action`: `start_session`, `status`, `capture`, `move`, `click`, `scroll`, `key`, `type`, `stop_session`
- `session_id`: optional after `start_session`
- `move`: `x`, `y` normalized to `[0,1]`
- `click`: optional `x`, `y`, optional `button` (`left`, `right`, `middle`), optional `count`
- `scroll`: `dx`, `dy`
- `key`: `key` or `keys`
- `type`: `text`, optional `submit` boolean

Availability, backend support, and trust mode are checked when the tool runs. If no CLI is connected or local computer use is disabled, tell the user what to enable instead of using the server environment.

If any tool result contains `COMPUTER_USE_REARM_REQUIRED` or `status=rearm required`, stop the computer-use sequence immediately. Do not retry `start_session`, do not call `capture`, and do not use shell, vision, or screenshot fallbacks to bypass it. Tell the user that the A0 CLI has Computer Use configured but the installed desktop-control backend is not armed; they should run `/computer-use rearm` in the A0 CLI and approve the platform permission prompt if shown.

## Core Loop

1. Call `start_session` first.
2. Decide from the latest screenshot, not from memory.
3. Interactive actions (`move`, `click`, `scroll`, `key`, `type`) already attach a fresh screenshot after they run.
4. Use `status` for state without starting a session.
5. Use `capture` only when you need another screenshot without taking an action.

## Operating Rules

- Only the latest screenshot or a definitive tool result counts as evidence.
- The current API uses normalized global screen coordinates; do not assume window ids, element indexes, background-safe input, or semantic click targets unless the runtime explicitly advertises them.
- Prefer accessibility and semantic UI paths first: shortcuts, command palettes, menu accelerators, address/search bars, focus traversal, and other keyboard-accessible controls.
- Prefer `key` and `type` over pointer actions whenever a reliable keyboard path exists.
- When a menu or popup is open, treat it as the active UI and prefer keyboard navigation over clicking small transient rows by coordinate.
- If a click dismisses a menu or popup without producing the expected next UI, treat that attempt as failed.
- If the same approach has already failed twice without visible progress, switch strategy instead of repeating it.
- Do not infer focus or task completion from chat logs, sidebars, tool summaries, or status text.
- For browser-navigation tasks done through this tool, only claim success if the browser content area visibly shows the destination page or result.
- If the attached screenshot appears unchanged after a state-changing action, use one explicit `capture` to verify before repeating the same action.
- Use `type(..., submit=true)` only for URL or navigation-style entry where Enter should fire immediately after typing.
- Do not use `submit=true` for ordinary text fields. Type first, then send `enter` separately if needed.

## Pointer And Scrolling

- Try keyboard scrolling first: `page_down`, `page_up`, `space`, `shift+space`, arrows, `home`, or `end`.
- Use `scroll` when the desired pane is already active or keyboard scrolling cannot target it.
- Treat `move` and `click` as last-resort actions for controls that cannot be reached through keyboard, accessibility, browser, or app-native tooling.
- Before clicking, make sure the latest screenshot makes the target unambiguous. Use one deliberate click, then reassess from the fresh screenshot.

## Control Signals

- Treat user interventions as high-priority control signals.
- If the user says `stop`, `pause`, `abort`, `hold`, `don't continue`, or equivalent, halt immediately and do not use computer-use tools again until the user explicitly resumes.
