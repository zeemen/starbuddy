"""复测与验证题的配置位（KGE-05 spec 范围第 2、3 条；全部待教研签字）。

- ticket_ttl_minutes：复测票据有效期；queue_cap：列表最多几条；
- verify_items_per_task：“我其实会”的自述验证任务（verify_self_report）做满几道验证题算这一条做完（SIE 默认 2 道；
  KGE-04 每个异议只建 1 条任务，道数不落库，见 kge-04 lessons 偏差 3）。道数的来源是洞察服务的异议配置（“我其实会”要几道验证题）：
  部署时这里的值须与洞察服务配置一致（两边各存一份，目前没有自动核对，待人确认）；只统计用本任务票据作答的题（tasks.verify_answers）；
- followup_enabled / followup_after_days：Gate 1“8 周延迟复测”的复测点。现有复测轮只有 0 / 3 / 14 天（KGE-04），
  8 周不在里面；这里只是可配置项，默认关、天数为空，数值等教研签字（spec 范围第 3 条）。打开时只在列表上多出读时算出的条目，
  不建任务、不加 retest_task.reason、不改掌握计算；
- subjects_enabled：开放的学科（只能是再练开放的学科的子集，由 service 再核一次）。
未签字（含合成占位）只允许 LEARNING_ENV=test / dev；其他环境这一模块不启动（接口 501），沿用再练配置的做法。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORMAT = "starbuddy-retest-config/1"
ALLOWED_ENVS = ("test", "dev")
RETEST_SUBJECTS = ("aya", "zhixing", "wenli")
KEYS = frozenset({"format", "synthetic", "signed_by", "signed_at", "subjects_enabled", "ticket_ttl_minutes", "queue_cap",
                  "verify_items_per_task", "followup_enabled", "followup_after_days"})
RANGES = {"ticket_ttl_minutes": (1, 24 * 60), "queue_cap": (1, 100), "verify_items_per_task": (1, 5), "followup_after_days": (1, 365)}


class RetestConfigError(ValueError):
    """配置不合法：拒绝启动。"""


class RetestConfigUnsigned(RetestConfigError):
    """未签字（含合成占位）的配置出现在 test、dev 以外的环境：这一模块不启动（接口 501），服务其余部分照常。"""


@dataclass(frozen=True)
class RetestConfig:
    ticket_ttl_minutes: int
    queue_cap: int
    verify_items_per_task: int
    followup_enabled: bool
    followup_after_days: int | None
    subjects_enabled: tuple[str, ...]
    synthetic: bool = False
    signed_by: str | None = None
    signed_at: str | None = None

    @property
    def signed(self) -> bool:
        return bool(self.signed_by) and bool(self.signed_at) and not self.synthetic


# 占位默认：只用于测试，不是建议值。8 周复测点默认关、天数为空（待教研签字）
SYNTHETIC: dict[str, Any] = {
    "format": FORMAT, "synthetic": True, "signed_by": None, "signed_at": None,
    "ticket_ttl_minutes": 60, "queue_cap": 20, "verify_items_per_task": 2,
    "followup_enabled": False, "followup_after_days": None, "subjects_enabled": ["aya", "zhixing"],
}


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise RetestConfigError(msg)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse(doc: Mapping[str, Any], *, env: str) -> RetestConfig:
    _need(isinstance(doc, Mapping), "配置必须是对象")
    _need(set(doc) == KEYS, "配置键不对（多了或少了）")
    _need(doc["format"] == FORMAT, "配置格式不认识")
    for k in ("ticket_ttl_minutes", "queue_cap", "verify_items_per_task"):
        lo, hi = RANGES[k]
        _need(_int(doc[k]) and lo <= doc[k] <= hi, f"{k} 必须是 [{lo}, {hi}] 内的整数")
    for k in ("synthetic", "followup_enabled"):
        _need(isinstance(doc[k], bool), f"{k} 必须是布尔值")
    days = doc["followup_after_days"]
    lo, hi = RANGES["followup_after_days"]
    _need(days is None or (_int(days) and lo <= days <= hi), f"followup_after_days 必须为空或 [{lo}, {hi}] 内的整数")
    # 开关打开就必须给天数；关着时天数可以空着（数值等教研签字）
    _need(not doc["followup_enabled"] or days is not None, "followup_enabled 打开时必须给 followup_after_days")
    subjects = doc["subjects_enabled"]
    _need(isinstance(subjects, list) and len(set(subjects)) == len(subjects) and set(subjects) <= set(RETEST_SUBJECTS),
          f"subjects_enabled 只能取 {RETEST_SUBJECTS}，不能重复")
    for k in ("signed_by", "signed_at"):
        _need(doc[k] is None or (isinstance(doc[k], str) and 0 < len(doc[k]) <= 200), f"{k} 必须为空或非空字符串")
    _need(doc["signed_at"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})", doc["signed_at"]) is not None,
          "signed_at 必须是带时区的时间")
    _need(not (doc["synthetic"] and (doc["signed_by"] or doc["signed_at"])), "合成占位配置不能带签字")
    cfg = RetestConfig(ticket_ttl_minutes=doc["ticket_ttl_minutes"], queue_cap=doc["queue_cap"],
                       verify_items_per_task=doc["verify_items_per_task"], followup_enabled=doc["followup_enabled"],
                       followup_after_days=days, subjects_enabled=tuple(subjects), synthetic=doc["synthetic"],
                       signed_by=doc["signed_by"], signed_at=doc["signed_at"])
    if env not in ALLOWED_ENVS and not cfg.signed:
        raise RetestConfigUnsigned("未签字或合成的复测配置只允许 LEARNING_ENV=test / dev（待教研签字）")
    return cfg


def load(spec: str, *, env: str) -> RetestConfig:
    """spec = "synthetic"（合成占位）或配置文件路径。"""
    if spec == "synthetic":
        return parse(SYNTHETIC, env=env)
    try:
        doc = json.loads(Path(spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RetestConfigError("复测配置文件读不到或不是合法 JSON") from None
    return parse(doc, env=env)
