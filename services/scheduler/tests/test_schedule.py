"""时间表表达式与“下一次运行时间”的纯函数测试（时区、月末、闰年、周一、跨日、与其他地区夏令时无关）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scheduler.schedule import TZ, Schedule, ScheduleError, next_run, parse_schedule, upcoming

SH = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def sh(*a: int) -> datetime:
    return datetime(*a, tzinfo=SH)


@pytest.mark.parametrize("text, want", [
    ("every 5m", Schedule("every", "every 5m", every_minutes=5)),
    ("every 1m", Schedule("every", "every 1m", every_minutes=1)),
    ("every 720m", Schedule("every", "every 720m", every_minutes=720)),
    ("hourly", Schedule("hourly", "hourly", minute=0)),
    ("hourly :07", Schedule("hourly", "hourly :07", minute=7)),
    ("daily 02:30", Schedule("daily", "daily 02:30", hour=2, minute=30)),
    ("daily 23:59", Schedule("daily", "daily 23:59", hour=23, minute=59)),
    ("weekly mon 03:00", Schedule("weekly", "weekly mon 03:00", weekday=0, hour=3)),
    ("weekly sun 00:00", Schedule("weekly", "weekly sun 00:00", weekday=6)),
])
def test_parse(text, want):
    assert parse_schedule(text) == want


@pytest.mark.parametrize("text", ["", "every 0m", "every 7m", "every 1440m", "every 5", "every 5 m", "hourly :60", "hourly 07",
                                  "daily 24:00", "daily 2:30", "daily 02:60", "weekly monday 03:00", "weekly mon 3:00",
                                  "monthly 1 03:00", " daily 02:30", "daily 02:30 ", "DAILY 02:30", 5, None])
def test_parse_rejects(text):
    with pytest.raises(ScheduleError):
        parse_schedule(text)  # type: ignore[arg-type]


def test_after_must_be_aware():
    with pytest.raises(ValueError):
        next_run(parse_schedule("daily 02:30"), datetime(2026, 10, 12, 1, 0))


def test_every_minutes_aligned_and_strictly_after():
    s = parse_schedule("every 5m")
    assert next_run(s, sh(2026, 10, 12, 10, 3, 59)) == sh(2026, 10, 12, 10, 5)
    assert next_run(s, sh(2026, 10, 12, 10, 5)) == sh(2026, 10, 12, 10, 10)            # 恰好在点上：下一个
    assert next_run(s, sh(2026, 10, 12, 10, 5, 0, 1)) == sh(2026, 10, 12, 10, 10)
    assert next_run(s, sh(2026, 10, 12, 23, 57)) == sh(2026, 10, 13, 0, 0)               # 跨日
    assert next_run(parse_schedule("every 90m"), sh(2026, 10, 12, 22, 31)) == sh(2026, 10, 13, 0, 0)
    assert next_run(parse_schedule("every 720m"), sh(2026, 10, 12, 12, 0)) == sh(2026, 10, 13, 0, 0)


def test_timezone_is_shanghai_whatever_the_input_zone():
    s = parse_schedule("daily 02:30")
    # UTC 18:00 = 上海次日 02:00 → 当天（上海）02:30
    got = next_run(s, datetime(2026, 10, 11, 18, 0, tzinfo=UTC))
    assert got == sh(2026, 10, 12, 2, 30) and got.utcoffset() == timedelta(hours=8)
    assert got == datetime(2026, 10, 11, 18, 30, tzinfo=UTC)
    # UTC 18:30 恰好是上海 02:30：下一次是明天
    assert next_run(s, datetime(2026, 10, 11, 18, 30, tzinfo=UTC)) == sh(2026, 10, 13, 2, 30)
    # 纽约时间输入：同一时刻，同一结果
    ny = datetime(2026, 10, 11, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert next_run(s, ny) == next_run(s, ny.astimezone(UTC)) == sh(2026, 10, 12, 2, 30)
    assert all(t.tzinfo is TZ for t in upcoming(s, ny, 5))


def test_hourly():
    s = parse_schedule("hourly :07")
    assert next_run(s, sh(2026, 10, 12, 10, 6, 59)) == sh(2026, 10, 12, 10, 7)
    assert next_run(s, sh(2026, 10, 12, 10, 7)) == sh(2026, 10, 12, 11, 7)
    assert next_run(s, sh(2026, 10, 12, 23, 30)) == sh(2026, 10, 13, 0, 7)
    assert next_run(s, sh(2026, 12, 31, 23, 8)) == sh(2027, 1, 1, 0, 7)                 # 跨年
    assert next_run(parse_schedule("hourly"), sh(2026, 10, 12, 10, 0)) == sh(2026, 10, 12, 11, 0)


@pytest.mark.parametrize("after, want", [
    (sh(2026, 1, 31, 3, 0), sh(2026, 2, 1, 2, 30)),       # 1 月末
    (sh(2026, 2, 28, 2, 31), sh(2026, 3, 1, 2, 30)),      # 平年 2 月末
    (sh(2028, 2, 28, 2, 31), sh(2028, 2, 29, 2, 30)),     # 闰年
    (sh(2028, 2, 29, 2, 30), sh(2028, 3, 1, 2, 30)),
    (sh(2026, 4, 30, 23, 59), sh(2026, 5, 1, 2, 30)),     # 30 天的月
    (sh(2026, 12, 31, 2, 30), sh(2027, 1, 1, 2, 30)),     # 年末
    (sh(2026, 10, 12, 2, 29, 59), sh(2026, 10, 12, 2, 30)),
])
def test_daily_month_end(after, want):
    assert next_run(parse_schedule("daily 02:30"), after) == want


@pytest.mark.parametrize("after, want", [
    (sh(2026, 10, 12, 2, 0), sh(2026, 10, 12, 3, 0)),     # 2026-10-12 是周一，03:00 之前
    (sh(2026, 10, 12, 3, 0), sh(2026, 10, 19, 3, 0)),     # 恰好在点上：下周一
    (sh(2026, 10, 13, 9, 0), sh(2026, 10, 19, 3, 0)),     # 周二
    (sh(2026, 10, 18, 23, 59), sh(2026, 10, 19, 3, 0)),   # 周日深夜
    (sh(2026, 12, 29, 0, 0), sh(2027, 1, 4, 3, 0)),       # 跨年、跨月末
    (sh(2027, 2, 27, 12, 0), sh(2027, 3, 1, 3, 0)),       # 2 月末后的周一
])
def test_weekly_monday(after, want):
    got = next_run(parse_schedule("weekly mon 03:00"), after)
    assert got == want and got.weekday() == 0


def test_dst_independent_constant_spacing():
    """上海不实行夏令时：全年任何一天，每天任务相隔恰好 24 小时、每周任务恰好 7 天、每 5 分钟恰好 5 分钟——
    包括美国、欧洲夏令时切换的日子（按 UTC 绝对时间比较）"""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    daily = upcoming(parse_schedule("daily 02:30"), start, 400)
    assert all((b - a) == timedelta(days=1) for a, b in zip(daily, daily[1:]))
    weekly = upcoming(parse_schedule("weekly mon 03:00"), start, 60)
    assert all((b - a) == timedelta(days=7) and b.weekday() == 0 for a, b in zip(weekly, weekly[1:]))
    hourly = upcoming(parse_schedule("hourly :07"), datetime(2026, 3, 7, tzinfo=UTC), 24 * 4)  # 跨 2026-03-08 美国切换
    assert all((b - a) == timedelta(hours=1) for a, b in zip(hourly, hourly[1:]))
    every = upcoming(parse_schedule("every 5m"), datetime(2026, 10, 24, 22, tzinfo=UTC), 12 * 12)  # 跨 2026-10-25 欧洲切换
    assert all((b - a) == timedelta(minutes=5) for a, b in zip(every, every[1:]))
    for t in daily + weekly:
        assert t.utcoffset() == timedelta(hours=8)


def test_upcoming_strictly_increasing_and_on_schedule():
    for text in ("every 5m", "hourly :07", "daily 02:30", "weekly mon 03:00", "every 90m"):
        s = parse_schedule(text)
        ts = upcoming(s, sh(2026, 10, 12, 1, 23, 45), 50)
        assert all(a < b for a, b in zip(ts, ts[1:]))
        assert all(t.second == 0 and t.microsecond == 0 for t in ts)
        if s.kind != "every":
            assert all(t.minute == s.minute for t in ts)
        if s.kind in ("daily", "weekly"):
            assert all(t.hour == s.hour for t in ts)
        if s.kind == "every":
            assert all((t.hour * 60 + t.minute) % s.every_minutes == 0 for t in ts)
