"""简化 BKT、置信度与时间衰减（spec 第 5 节；KGE 第六章）。自写，不引入 pyBKT（规则 10）。

- 每一步：先按做对 / 做错求后验，做对再加学习转移；再按证据强度 a ∈ [0, 1] 在旧值与目标值之间插值。
  a = |证据权重| × 等级权重 × 来源权重 × 错因系数，全部 ≤ 1，所以权重的大小顺序原样保留在更新幅度里；a = 0 时不变。
  做错不加学习转移：保证“做错只下调”（方向校验，第 3 节）。
- 置信度 = 质量 / (质量 + k)，质量 = Σ 强度 × 判分置信度；提示后做错不增加质量（置信度不变）。
- 时间衰减只降置信度：不降 p_mastery，不退门槛（第 5 节）。
"""
from __future__ import annotations

import math
from datetime import datetime

from .config import MasteryConfig


def bkt_step(p: float, correct: bool, strength: float, cfg: MasteryConfig, item_type: str | None) -> float:
    if strength <= 0:
        return p
    b = cfg["bkt"]
    slip, guess, learn = b["p_slip"], cfg.p_guess(item_type), b["p_learn"]
    if correct:
        post = p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
        target = post + (1 - post) * learn
    else:
        post = p * slip / (p * slip + (1 - p) * (1 - guess))
        target = post
    out = p + min(1.0, strength) * (target - p)
    return min(1.0, max(0.0, out))


def confidence_of(mass: float, cfg: MasteryConfig) -> float:
    return mass / (mass + cfg["confidence_formula"]["k"]) if mass > 0 else 0.0


def decayed_confidence(confidence: float, last_at: datetime | None, now: datetime, cfg: MasteryConfig) -> float:
    """长期没有新证据时按半衰期降低置信度；now 早于最后证据时不衰减。"""
    if last_at is None:
        return confidence
    d = cfg["confidence_decay"]
    idle = (now - last_at).total_seconds() - d["grace_seconds"]
    if idle <= 0:
        return confidence
    return confidence * 0.5 ** (idle / d["half_life_seconds"])


def decay_crossing_seconds(confidence: float, cfg: MasteryConfig) -> float | None:
    """从最后一条证据起，置信度衰减到 review_confidence_floor 以下所需的秒数；已经在下限以下时为 0。"""
    floor = cfg["review_confidence_floor"]
    if confidence <= 0:
        return None
    d = cfg["confidence_decay"]
    if confidence < floor:
        return 0.0
    return d["grace_seconds"] + d["half_life_seconds"] * math.log2(confidence / floor)
