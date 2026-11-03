"""时间表表达式与“下一次运行时间”（纯函数，不连库、不读墙钟）。

表达式（时区固定 Asia/Shanghai，spec 默认设计第 3 条）：
  every <N>m          每 N 分钟（N 整除 1440：每天的时刻表相同，零点对齐；例如 every 5m → 每个整 5 分）
  hourly              每小时整点；hourly :MM 每小时第 MM 分
  daily HH:MM         每天 HH:MM
  weekly <周几> HH:MM  每周（mon … sun）HH:MM；spec 只用到周一

next_run(schedule, after) 返回严格晚于 after 的第一个运行时刻（带时区，Asia/Shanghai）。按上海的日历日期与钟点计算后再挂时区：
上海不实行夏令时，结果与运行机器的本地时区、其他地区的夏令时切换无关；月末、闰年按日历日期自然进位。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ_NAME = "Asia/Shanghai"
TZ = ZoneInfo(TZ_NAME)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MINUTES_PER_DAY = 1440

_EVERY = re.compile(r"every ([1-9][0-9]{0,3})m")
_HOURLY = re.compile(r"hourly(?: :([0-5][0-9]))?")
_DAILY = re.compile(r"daily ([01][0-9]|2[0-3]):([0-5][0-9])")
_WEEKLY = re.compile(r"weekly (mon|tue|wed|thu|fri|sat|sun) ([01][0-9]|2[0-3]):([0-5][0-9])")


class ScheduleError(ValueError):
    """时间表表达式不合法"""


@dataclass(frozen=True)
class Schedule:
    kind: str           # every | hourly | daily | weekly
    text: str
    every_minutes: int = 0
    hour: int = 0
    minute: int = 0
    weekday: int = 0    # 0 = 周一（date.weekday()）


def parse_schedule(text: str) -> Schedule:
    if not isinstance(text, str):
        raise ScheduleError("时间表必须是字符串")
    if m := _EVERY.fullmatch(text):
        n = int(m.group(1))
        if n > MINUTES_PER_DAY // 2 or MINUTES_PER_DAY % n:
            raise ScheduleError("every <N>m 的 N 必须整除 1440 且不超过 720")
        return Schedule("every", text, every_minutes=n)
    if m := _HOURLY.fullmatch(text):
        return Schedule("hourly", text, minute=int(m.group(1) or 0))
    if m := _DAILY.fullmatch(text):
        return Schedule("daily", text, hour=int(m.group(1)), minute=int(m.group(2)))
    if m := _WEEKLY.fullmatch(text):
        return Schedule("weekly", text, weekday=WEEKDAYS.index(m.group(1)), hour=int(m.group(2)), minute=int(m.group(3)))
    raise ScheduleError(f"不认识的时间表表达式：{text!r}（every <N>m、hourly[ :MM]、daily HH:MM、weekly <周几> HH:MM）")


def _at(d: date, hour: int, minute: int) -> datetime:
    return datetime.combine(d, time(hour, minute), TZ)


def next_run(s: Schedule, after: datetime) -> datetime:
    """严格晚于 after 的下一次运行时刻（Asia/Shanghai）"""
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("after 必须带时区")
    local = after.astimezone(TZ)
    today = local.date()
    if s.kind == "every":
        m = local.hour * 60 + local.minute
        slot = (m // s.every_minutes + 1) * s.every_minutes
        return _at(today + timedelta(days=slot // MINUTES_PER_DAY), (slot % MINUTES_PER_DAY) // 60, slot % 60)
    if s.kind == "hourly":
        cand = _at(today, local.hour, s.minute)
        if cand <= local:
            nxt = local.replace(minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(hours=1)
            cand = _at(nxt.date(), nxt.hour, s.minute)
        return cand
    if s.kind == "daily":
        cand = _at(today, s.hour, s.minute)
        return cand if cand > local else _at(today + timedelta(days=1), s.hour, s.minute)
    if s.kind == "weekly":
        d = today + timedelta(days=(s.weekday - today.weekday()) % 7)
        cand = _at(d, s.hour, s.minute)
        return cand if cand > local else _at(d + timedelta(days=7), s.hour, s.minute)
    raise ScheduleError(f"未知的时间表类别：{s.kind}")


def upcoming(s: Schedule, after: datetime, n: int) -> list[datetime]:
    """接下来 n 次运行时刻（--dry-run 列计划用）"""
    out: list[datetime] = []
    t = after
    for _ in range(n):
        t = next_run(s, t)
        out.append(t)
    return out
