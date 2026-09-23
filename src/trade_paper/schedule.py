"""3x-daily scheduling.  Default slots 10:00 / 13:00 / 15:30 America/New_York
on weekdays -- after the open, midday, before the close.  Crypto trades 24/7
but follows the same cycle for a single, auditable rhythm.

The runner must execute on the user's machine (it holds the API keys), so
this module answers "is a run due?" and prints crontab / Task Scheduler
recipes; it does not install anything itself.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def parse_slot(slot: str) -> time:
    hh, mm = slot.split(":")
    return time(int(hh), int(mm))


def slot_datetimes(day, sched_cfg) -> list[datetime]:
    tz = _tz(sched_cfg.timezone)
    base = datetime(day.year, day.month, day.day, tzinfo=tz)
    return [base.replace(hour=parse_slot(s).hour, minute=parse_slot(s).minute)
            for s in sched_cfg.slots]


def is_due(cfg, now: datetime | None = None, last_run: datetime | None = None) -> bool:
    """True when a scheduled slot has passed since ``last_run`` (or ever)."""
    tz = _tz(cfg.schedule.timezone)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    if cfg.schedule.weekdays_only and now.weekday() >= 5:
        return False
    slots = slot_datetimes(now.date(), cfg.schedule)
    if last_run is not None:
        last_run = last_run.astimezone(tz)
        slots = [s for s in slots if s > last_run]
        # also consider yesterday's slots if the runner was down all day
        if not slots:
            y = now.date() - timedelta(days=1)
            slots = [s for s in slot_datetimes(y, cfg.schedule) if s > last_run]
    return any(s <= now for s in slots)


def cron_lines(cfg, command: str = "trade-paper run") -> list[str]:
    """Crontab entries (server timezone should match schedule.timezone)."""
    lines = []
    for slot in cfg.schedule.slots:
        t = parse_slot(slot)
        days = "1-5" if cfg.schedule.weekdays_only else "*"
        lines.append(f"{t.minute} {t.hour} * * {days} {command} >> ~/.trade-paper.log 2>&1")
    return lines


def describe(cfg) -> str:
    slots = ", ".join(cfg.schedule.slots)
    days = "weekdays" if cfg.schedule.weekdays_only else "every day"
    return (f"3x-daily paper cycle at {slots} {cfg.schedule.timezone} ({days}). "
            f"Install with: trade-paper schedule --print-cron")
