---
name: scheduler-tasks
description: Use for complex Agent Zero scheduler work, including creating, updating, deleting, running, waiting for, timezone-correcting, or auditing scheduled, planned, and adhoc tasks.
---

# Scheduler Tasks

Use the `scheduler` tool to manage saved tasks. Always inspect existing tasks before creating, updating, deleting, or running one.

## Actions

- `list_tasks`: optional `state[]`, `type[]`, `next_run_within`, `next_run_after`
- `find_task_by_name`: `name`
- `show_task`: `uuid`
- `run_task`: `uuid`, optional `context`
- `update_task`: `uuid`, optional `name`, `system_prompt`, `prompt`, `attachments[]`, `schedule`, `timezone`, `plan[]`, `state`, `dedicated_context`
- `delete_task`: `uuid`
- `create_scheduled_task`: `name`, `system_prompt`, `prompt`, optional `attachments[]`, `schedule`, `timezone`, `dedicated_context`
- `create_adhoc_task`: `name`, `system_prompt`, `prompt`, optional `attachments[]`, `dedicated_context`
- `create_planned_task`: `name`, `system_prompt`, `prompt`, optional `attachments[]`, `plan[]`, `dedicated_context`
- `wait_for_task`: `uuid`

## Schedule Fields

Schedules use cron-like fields:

- `minute`
- `hour`
- `day`
- `month`
- `weekday`
- `timezone`

Use IANA timezones such as `Europe/Rome`. Omit timezone to use the current user timezone. Planned task datetimes should be ISO strings such as `2026-05-09T18:25:00`.

## Safety

- Do not create recursive task prompts that schedule more tasks.
- Do not run a task just because it is scheduled; run only if the user asks.
- Created tasks use a dedicated context unless `dedicated_context` is explicitly `false`.
- For destructive operations, identify the task by UUID after lookup.

## Example

```json
{
  "tool_name": "scheduler",
  "tool_args": {
    "action": "find_task_by_name",
    "name": "daily backup"
  }
}
```
