"""今日计划跨科编排的配置位（PLT-11 spec 范围第 1 条；全部待人签字）。

- default_budget_minutes：请求没给 time_budget_minutes 时的时间预算（分钟），必须在 [20, 40]（完整方案“今日 20–40 分钟计划”）；
- source_priority：来源优先级（retest = 复测与验证题、practice = 错题再练、plan = 各科今日计划），三者各出现一次；
- entry_minutes：复测、验证题、再练条目的预计分钟（这两个来源的条目本身不带时长；plan 任务用适配器给的 estimated_minutes）。
未签字（含合成占位）只允许 LEARNING_ENV=test / dev；其他环境这一模块不启动（接口 501），沿用复测配置的做法。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORMAT = "starbuddy-today-config/1"
ALLOWED_ENVS = ("test", "dev")
SOURCES = ("retest", "practice", "plan")
ENTRY_KINDS = ("retest", "verify", "practice")
BUDGET_RANGE = (20, 40)                 # 今日 20–40 分钟（完整方案 v1.1）；请求里的预算也只能在这个范围
ENTRY_MINUTES_RANGE = (1, 60)
KEYS = frozenset({"format", "synthetic", "signed_by", "signed_at", "default_budget_minutes", "source_priority", "entry_minutes"})


class TodayConfigError(ValueError):
    """配置不合法：拒绝启动。"""


class TodayConfigUnsigned(TodayConfigError):
    """未签字（含合成占位）的配置出现在 test、dev 以外的环境：这一模块不启动（接口 501），服务其余部分照常。"""


@dataclass(frozen=True)
class TodayConfig:
    default_budget_minutes: int
    source_priority: tuple[str, ...]
    entry_minutes: Mapping[str, int]
    synthetic: bool = False
    signed_by: str | None = None
    signed_at: str | None = None

    @property
    def signed(self) -> bool:
        return bool(self.signed_by) and bool(self.signed_at) and not self.synthetic


# 合成占位：只用于测试，不是建议值（数值待人签字）。优先级照 spec 的合成口径：复测 / 验证 > 再练 > 计划
SYNTHETIC: dict[str, Any] = {
    "format": FORMAT, "synthetic": True, "signed_by": None, "signed_at": None,
    "default_budget_minutes": 30,
    "source_priority": ["retest", "practice", "plan"],
    "entry_minutes": {"retest": 5, "verify": 5, "practice": 8},
}


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise TodayConfigError(msg)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse(doc: Mapping[str, Any], *, env: str) -> TodayConfig:
    _need(isinstance(doc, Mapping), "配置必须是对象")
    _need(set(doc) == KEYS, "配置键不对（多了或少了）")
    _need(doc["format"] == FORMAT, "配置格式不认识")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    lo, hi = BUDGET_RANGE
    _need(_int(doc["default_budget_minutes"]) and lo <= doc["default_budget_minutes"] <= hi,
          f"default_budget_minutes 必须是 [{lo}, {hi}] 内的整数")
    pr = doc["source_priority"]
    _need(isinstance(pr, list) and sorted(pr) == sorted(SOURCES), f"source_priority 必须是 {SOURCES} 的一个排列")
    em = doc["entry_minutes"]
    lo, hi = ENTRY_MINUTES_RANGE
    _need(isinstance(em, Mapping) and set(em) == set(ENTRY_KINDS), f"entry_minutes 的键必须是 {ENTRY_KINDS}")
    for k in ENTRY_KINDS:
        _need(_int(em[k]) and lo <= em[k] <= hi, f"entry_minutes.{k} 必须是 [{lo}, {hi}] 内的整数")
    for k in ("signed_by", "signed_at"):
        _need(doc[k] is None or (isinstance(doc[k], str) and 0 < len(doc[k]) <= 200), f"{k} 必须为空或非空字符串")
    _need(doc["signed_at"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})", doc["signed_at"]) is not None,
          "signed_at 必须是带时区的时间")
    _need(not (doc["synthetic"] and (doc["signed_by"] or doc["signed_at"])), "合成占位配置不能带签字")
    cfg = TodayConfig(default_budget_minutes=doc["default_budget_minutes"], source_priority=tuple(pr),
                      entry_minutes={k: em[k] for k in ENTRY_KINDS}, synthetic=doc["synthetic"],
                      signed_by=doc["signed_by"], signed_at=doc["signed_at"])
    if env not in ALLOWED_ENVS and not cfg.signed:
        raise TodayConfigUnsigned("未签字或合成的今日编排配置只允许 LEARNING_ENV=test / dev（待人签字）")
    return cfg


def load(spec: str, *, env: str) -> TodayConfig:
    """spec = "synthetic"（合成占位）或配置文件路径。"""
    if spec == "synthetic":
        return parse(SYNTHETIC, env=env)
    try:
        doc = json.loads(Path(spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise TodayConfigError("今日编排配置文件读不到或不是合法 JSON") from None
    return parse(doc, env=env)
