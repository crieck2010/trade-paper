from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from trade_paper import schedule as sched
from trade_paper.config import PaperConfig


def _cfg(**kw):
    c = PaperConfig()
    for k, v in kw.items():
        setattr(c.schedule, k, v)
    return c


def test_parse_slot():
    t = sched.parse_slot("13:00")
    assert (t.hour, t.minute) == (13, 0)


def test_is_due_after_slot():
    cfg = _cfg(slots=["10:00"], weekdays_only=False)
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 9, 23, 11, 0, tzinfo=tz)  # Wednesday
    assert sched.is_due(cfg, now=now, last_run=None)


def test_not_due_before_slot():
    cfg = _cfg(slots=["15:30"], weekdays_only=False)
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 9, 23, 9, 0, tzinfo=tz)
    assert not sched.is_due(cfg, now=now, last_run=None)


def test_not_due_if_already_ran():
    cfg = _cfg(slots=["10:00"], weekdays_only=False)
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 9, 23, 11, 0, tzinfo=tz)
    last = datetime(2026, 9, 23, 10, 5, tzinfo=tz)
    assert not sched.is_due(cfg, now=now, last_run=last)


def test_weekend_skipped():
    cfg = _cfg(slots=["10:00"], weekdays_only=True)
    tz = ZoneInfo("America/New_York")
    sat = datetime(2026, 9, 26, 11, 0, tzinfo=tz)  # Saturday
    assert not sched.is_due(cfg, now=sat, last_run=None)


def test_cron_lines_three_slots():
    cfg = _cfg()
    lines = sched.cron_lines(cfg)
    assert len(lines) == 3 and all("trade-paper run" in l for l in lines)
    assert "1-5" in lines[0]


def test_describe():
    assert "10:00" in sched.describe(_cfg())
