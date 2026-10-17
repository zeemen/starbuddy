"""模考配置位（PLT-18 spec 范围第 2、3 条；全部待教研签字，见 spec 待人确认 1）。

- grace_seconds：截止宽限（截止时间 = 开始时间 + 限时 + 宽限），只由服务端在开始时计算；
- review_confidence_below：判分置信度低于它的题列为“待复核”（与 human_review_required 并列）；
- ticket_ttl_minutes：会话票据从签发起的有效期（不替代截止时间，截止只由状态机判断）。
未签字（含合成占位）只允许 LEARNING_ENV=test / dev，沿用再练、复测配置的做法。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FORMAT = "starbuddy-mock-exam-config/1"
ALLOWED_ENVS = ("test", "dev")
KEYS = frozenset({"format", "synthetic", "signed_by", "signed_at", "grace_seconds", "review_confidence_below", "ticket_ttl_minutes"})
RANGES = {"grace_seconds": (0, 600), "ticket_ttl_minutes": (1, 24 * 60)}
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "mock_exam"
SYNTHETIC_PATH = CONFIG_DIR / "config.synthetic.json"


class MockExamConfigError(ValueError):
    """配置不合法：拒绝加载。"""


class MockExamConfigUnsigned(MockExamConfigError):
    """未签字（含合成占位）的配置出现在 test、dev 以外的环境。"""


@dataclass(frozen=True)
class MockExamConfig:
    grace_seconds: int
    review_confidence_below: float
    ticket_ttl_minutes: int
    synthetic: bool = False
    signed_by: str | None = None
    signed_at: str | None = None

    @property
    def signed(self) -> bool:
        return bool(self.signed_by) and bool(self.signed_at) and not self.synthetic


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise MockExamConfigError(msg)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse(doc: Mapping[str, Any], *, env: str) -> MockExamConfig:
    _need(isinstance(doc, Mapping), "配置必须是对象")
    _need(set(doc) == KEYS, "配置键不对（多了或少了）")
    _need(doc["format"] == FORMAT, "配置格式不认识")
    for k, (lo, hi) in RANGES.items():
        _need(_int(doc[k]) and lo <= doc[k] <= hi, f"{k} 必须是 [{lo}, {hi}] 内的整数")
    c = doc["review_confidence_below"]
    _need(isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1, "review_confidence_below 必须在 [0, 1] 内")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    for k in ("signed_by", "signed_at"):
        _need(doc[k] is None or (isinstance(doc[k], str) and 0 < len(doc[k]) <= 200), f"{k} 必须为空或非空字符串")
    _need(doc["signed_at"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})", doc["signed_at"]) is not None,
          "signed_at 必须是带时区的时间")
    _need(not (doc["synthetic"] and (doc["signed_by"] or doc["signed_at"])), "合成占位配置不能带签字")
    cfg = MockExamConfig(grace_seconds=doc["grace_seconds"], review_confidence_below=float(c),
                         ticket_ttl_minutes=doc["ticket_ttl_minutes"], synthetic=doc["synthetic"],
                         signed_by=doc["signed_by"], signed_at=doc["signed_at"])
    if env not in ALLOWED_ENVS and not cfg.signed:
        raise MockExamConfigUnsigned("未签字或合成的模考配置只允许 LEARNING_ENV=test / dev（待教研签字）")
    return cfg


def load(spec: str, *, env: str) -> MockExamConfig:
    """spec = "synthetic"（config/mock_exam/config.synthetic.json）或配置文件路径。"""
    path = SYNTHETIC_PATH if spec == "synthetic" else Path(spec)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise MockExamConfigError("模考配置文件读不到或不是合法 JSON") from None
    return parse(doc, env=env)
