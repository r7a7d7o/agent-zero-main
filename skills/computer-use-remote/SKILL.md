---
name: computer-use-remote
description: Detailed operating guide for using computer_use_remote on the connected local machine. Load this skill before using computer_use_remote for desktop control, screenshots, menus, browser chrome, or other native UI tasks.
version: 1.0.0
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
  - code_execution_remote
---

# Computer Use Remote

## When to Use

Load this skill before using `computer_use_remote` for local desktop and native UI tasks on the connected machine.

For ordinary website browsing, search, form filling, and web downloads, prefer the direct `browser` tool instead. If the user is flexible and the task is browser-only, guide them toward browser tools because they are usually more reliable and token-efficient than screenshot-driven computer use.

## Core Loop

1. Call `start_session` first.
2. Decide from the latest screenshot, not from memory.
3. Interactive actions (`move`, `click`, `scroll`, `key`, `type`) already attach a fresh screenshot after they run.
4. Use `capture` only when you need another screenshot without taking an action.

## Operating Rules

- Only the latest screenshot or a definitive tool result counts as evidence.
- Prefer keyboard actions over pointer actions whenever a reliable keyboard path exists.
- When a menu or popup is open, treat it as the active UI and prefer keyboard navigation over clicking small transient rows by coordinate.
- If a click dismisses a menu or popup without producing the expected next UI, treat that attempt as failed.
- If the same approach has already failed twice without visible progress, switch strategy instead of repeating it.
- Do not infer focus or task completion from chat logs, sidebars, tool summaries, or status text.
- For browser-navigation tasks done through this tool, only claim success if the browser content area visibly shows the destination page or result.
- Use `type(..., submit=true)` only for URL or navigation-style entry where Enter should fire immediately after typing.
- Do not use `submit=true` for ordinary text fields. Type first, then send `enter` separately if needed.

## Control Signals

- Treat user interventions as high-priority control signals.
- If the user says `stop`, `pause`, `abort`, `hold`, `don't continue`, or equivalent, halt immediately and do not use computer-use tools again until the user explicitly resumes.
