"""再练配置位（spec 第 7 节；全部待数学、道法教研签字，待人确认 #12）。

- 数值：lookback_days、round_delay_hours[]、max_rounds、variants_per_round、exclude_window_days、queue_cap、ticket_ttl_minutes；
- 开关：prefer_new_family、include_diagnosis_attempts、subjects_enabled。
未签字（含合成占位）只允许 LEARNING_ENV=test / dev 使用；其他环境这一模块不启动（接口回 501），沿用掌握配置“未签字不上线”的做法。
仓库里不写签字值：SYNTHETIC 只是测试占位，不是建议值。

轮次口径（第 1 节）：round_delay_hours[k] 是“第 k + 1 轮”在最近一次错题之后多少小时开放；第 1 轮在错题落库后立即可做，
所以 round_delay_hours[0] 必须是 0，长度等于 max_rounds，且不递减。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORMAT = "starbuddy-practice-config/1"
ALLOWED_ENVS = ("test", "dev")            # 未签字配置可用的环境
PRACTICE_SUBJECTS = ("aya", "zhixing", "wenli")   # 英语不涉及；语文留开关（默认关）
NUMBERS = ("lookback_days", "max_rounds", "variants_per_round", "exclude_window_days", "queue_cap", "ticket_ttl_minutes")
SWITCHES = ("prefer_new_family", "include_diagnosis_attempts")
KEYS = frozenset({"format", "synthetic", "signed_by", "signed_at", "round_delay_hours", "subjects_enabled", *NUMBERS, *SWITCHES})
# 数值的合法范围（只是形状校验，不是建议值）
RANGES = {"lookback_days": (1, 365), "max_rounds": (1, 10), "variants_per_round": (1, 20), "exclude_window_days": (0, 365),
          "queue_cap": (1, 100), "ticket_ttl_minutes": (1, 24 * 60)}
MAX_DELAY_HOURS = 24 * 60


class PracticeConfigError(ValueError):
    """配置不合法：拒绝启动。"""


class PracticeConfigUnsigned(PracticeConfigError):
    """未签字（含合成占位）的配置出现在 test、dev 以外的环境：这一模块不启动（接口 501），服务其余部分照常。"""


@dataclass(frozen=True)
class PracticeConfig:
    lookback_days: int
    round_delay_hours: tuple[int, ...]
    max_rounds: int
    variants_per_round: int
    exclude_window_days: int
    queue_cap: int
    ticket_ttl_minutes: int
    prefer_new_family: bool
    include_diagnosis_attempts: bool
    subjects_enabled: tuple[str, ...]
    synthetic: bool = False
    signed_by: str | None = None
    signed_at: str | None = None

    @property
    def signed(self) -> bool:
        return bool(self.signed_by) and bool(self.signed_at) and not self.synthetic


# 占位默认（spec 第 7 节）：只用于测试，不是建议值
SYNTHETIC: dict[str, Any] = {
    "format": FORMAT, "synthetic": True, "signed_by": None, "signed_at": None,
    "lookback_days": 30, "round_delay_hours": [0, 48, 120], "max_rounds": 3, "variants_per_round": 1,
    "exclude_window_days": 30, "queue_cap": 20, "ticket_ttl_minutes": 60,
    "prefer_new_family": True, "include_diagnosis_attempts": True, "subjects_enabled": ["aya", "zhixing"],
}


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise PracticeConfigError(msg)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse(doc: Mapping[str, Any], *, env: str) -> PracticeConfig:
    """校验并返回配置。形状不对、数值越界、轮次口径不一致，或未签字 / 合成配置出现在 test、dev 以外的环境，一律拒绝。"""
    _need(isinstance(doc, Mapping), "配置必须是对象")
    _need(set(doc) == KEYS, "配置键不对（多了或少了）")
    _need(doc["format"] == FORMAT, "配置格式不认识")
    for k in NUMBERS:
        lo, hi = RANGES[k]
        _need(_int(doc[k]) and lo <= doc[k] <= hi, f"{k} 必须是 [{lo}, {hi}] 内的整数")
    for k in SWITCHES + ("synthetic",):
        _need(isinstance(doc[k], bool), f"{k} 必须是布尔值")
    delays = doc["round_delay_hours"]
    _need(isinstance(delays, list) and all(_int(h) and 0 <= h <= MAX_DELAY_HOURS for h in delays),
          "round_delay_hours 必须是非负整数小时的数组")
    _need(len(delays) == doc["max_rounds"], "round_delay_hours 的长度必须等于 max_rounds")
    _need(delays[0] == 0, "第 1 轮在错题落库后立即可做：round_delay_hours[0] 必须是 0")
    _need(all(a <= b for a, b in zip(delays, delays[1:])), "round_delay_hours 不能递减")
    subjects = doc["subjects_enabled"]
    _need(isinstance(subjects, list) and len(set(subjects)) == len(subjects) and set(subjects) <= set(PRACTICE_SUBJECTS),
          f"subjects_enabled 只能取 {PRACTICE_SUBJECTS}，不能重复")
    for k in ("signed_by", "signed_at"):
        _need(doc[k] is None or (isinstance(doc[k], str) and 0 < len(doc[k]) <= 200), f"{k} 必须为空或非空字符串")
    _need(doc["signed_at"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})", doc["signed_at"]) is not None,
          "signed_at 必须是带时区的时间")
    _need(not (doc["synthetic"] and (doc["signed_by"] or doc["signed_at"])), "合成占位配置不能带签字")
    cfg = PracticeConfig(
        lookback_days=doc["lookback_days"], round_delay_hours=tuple(delays), max_rounds=doc["max_rounds"],
        variants_per_round=doc["variants_per_round"], exclude_window_days=doc["exclude_window_days"], queue_cap=doc["queue_cap"],
        ticket_ttl_minutes=doc["ticket_ttl_minutes"], prefer_new_family=doc["prefer_new_family"],
        include_diagnosis_attempts=doc["include_diagnosis_attempts"], subjects_enabled=tuple(subjects),
        synthetic=doc["synthetic"], signed_by=doc["signed_by"], signed_at=doc["signed_at"])
    if env not in ALLOWED_ENVS and not cfg.signed:
        raise PracticeConfigUnsigned("未签字或合成的再练配置只允许 LEARNING_ENV=test / dev（待教研签字，待人确认 #12）")
    return cfg


def load(spec: str, *, env: str) -> PracticeConfig:
    """spec = "synthetic"（合成占位）或配置文件路径。"""
    if spec == "synthetic":
        return parse(SYNTHETIC, env=env)
    try:
        doc = json.loads(Path(spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PracticeConfigError("再练配置文件读不到或不是合法 JSON") from None
    return parse(doc, env=env)
