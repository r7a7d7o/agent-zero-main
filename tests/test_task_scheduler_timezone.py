from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import task_scheduler
from helpers.task_scheduler import ScheduledTask, TaskSchedule


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 5, 9, 10, 0, tzinfo=timezone.utc)
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


def test_scheduled_task_next_run_uses_schedule_timezone(monkeypatch):
    monkeypatch.setattr(task_scheduler, "datetime", FixedDateTime)
    task = ScheduledTask.create(
        name="rome morning",
        system_prompt="",
        prompt="remind me",
        schedule=TaskSchedule(
            minute="30",
            hour="9",
            day="10",
            month="5",
            weekday="*",
            timezone="Europe/Rome",
        ),
        timezone="Europe/Rome",
    )

    assert task.get_next_run() == datetime(2026, 5, 10, 7, 30, tzinfo=timezone.utc)


def test_scheduled_task_normalizes_legacy_local_timezone(monkeypatch):
    monkeypatch.setattr(task_scheduler, "datetime", FixedDateTime)
    monkeypatch.setattr(
        task_scheduler,
        "Localization",
        SimpleNamespace(get=lambda: SimpleNamespace(get_timezone=lambda: "Europe/Rome")),
    )
    task = ScheduledTask.create(
        name="legacy local",
        system_prompt="",
        prompt="remind me",
        schedule=TaskSchedule(
            minute="30",
            hour="9",
            day="10",
            month="5",
            weekday="*",
            timezone="local",
        ),
    )

    assert task.schedule.timezone == "Europe/Rome"
    assert task.get_next_run() == datetime(2026, 5, 10, 7, 30, tzinfo=timezone.utc)
