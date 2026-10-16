"""到达过程与请求序列（spec 设计要点 1）：固定种子、泊松到达、分阶段加压；同一种子逐项相同。

- 每个阶段给“并发会话数” C 与时长 D；到达率 λ = C / 平均会话时长（Little 定律），到达间隔服从指数分布（泊松过程）；
  执行时每阶段另设会话并发上限 C（到达时满员就排队，排队时间单独计）；
- 每个到达的旅程类型按配比抽取；旅程内步骤之间的思考时间服从指数分布；
- 幂等键、合成学生编号都由（种子、到达序号、步骤）派生：两次运行的请求序列（到达、步骤、幂等键）逐项一致，
  digest() 给出序列的 sha256，报告里写计划值与实际执行值，二者必须相等。
"""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .scenarios import DEFAULT_MIX, JOURNEYS, PlannedRequest, journey_steps, keyed

NS = uuid.UUID("7d1b8a52-5c0e-4f31-9a55-000000000505")


@dataclass(frozen=True)
class Stage:
    concurrency: int
    duration_s: float

    @property
    def name(self) -> str:
        return f"c{self.concurrency}"


@dataclass(frozen=True)
class Profile:
    seed: int = 20260930
    stages: tuple[Stage, ...] = (Stage(5, 10.0), Stage(10, 10.0))
    mean_session_s: float = 6.0     # 用于由并发数推到达率
    think_mean_s: float = 0.2       # 步骤之间的平均思考时间
    mix: tuple[tuple[str, float], ...] = DEFAULT_MIX
    practice: bool = False          # 学生旅程另走再练与复测的列表、取题（PLT-16；不开时序列与以前逐项相同）

    def steps(self, kind: str) -> tuple[str, ...]:
        return journey_steps(kind, self.practice)

    def __post_init__(self) -> None:
        if not self.stages or any(s.concurrency < 1 or s.duration_s <= 0 for s in self.stages):
            raise ValueError("stages must be non-empty with positive concurrency and duration")
        if self.mean_session_s <= 0 or self.think_mean_s < 0:
            raise ValueError("session and think times must be positive")
        if not self.mix or any(k not in JOURNEYS or w < 0 for k, w in self.mix) or sum(w for _, w in self.mix) <= 0:
            raise ValueError("mix must name known journeys with non-negative weights")


# 预置档：small 用于样例与开发（并发 ≤ 20、时长 ≤ 30 s）；full 是 spec 的 10 → 50 → 100 → 200（只在人手动传参时跑）
PROFILES = {
    "small": (Stage(5, 10.0), Stage(10, 10.0)),
    "full": (Stage(10, 120.0), Stage(50, 120.0), Stage(100, 120.0), Stage(200, 120.0)),
}


def parse_stages(text: str) -> tuple[Stage, ...]:
    """'10:120,50:120' → 阶段序列；也接受预置档名 small / full"""
    if text in PROFILES:
        return PROFILES[text]
    out = []
    for part in text.split(","):
        c, _, d = part.strip().partition(":")
        out.append(Stage(int(c), float(d)))
    return tuple(out)


@dataclass(frozen=True)
class Arrival:
    index: int
    stage: int          # 阶段序号
    at_s: float         # 相对压测开始的到达时刻
    kind: str           # student | parent | meter
    slot: int           # 该旅程类型内的第几个合成对象（学生、家长、计量学生各自编号）
    think_s: tuple[float, ...]


def sid(*parts: object) -> str:
    """确定性 ID，UUIDv4 形态（计量与契约的幂等键要求 v4 形式）"""
    raw = uuid.uuid5(NS, "|".join(str(p) for p in parts))
    return str(uuid.UUID(bytes=raw.bytes, version=4))


def plan(profile: Profile) -> list[Arrival]:
    rng = random.Random(profile.seed)
    kinds = [k for k, _ in profile.mix]
    weights = [w for _, w in profile.mix]
    slots = {k: 0 for k in JOURNEYS}
    out: list[Arrival] = []
    t0 = 0.0
    for si, st in enumerate(profile.stages):
        rate = st.concurrency / profile.mean_session_s
        t = t0
        while True:
            t += rng.expovariate(rate)
            if t >= t0 + st.duration_s:
                break
            kind = rng.choices(kinds, weights)[0]
            n = len(profile.steps(kind))
            think = tuple(round(rng.expovariate(1 / profile.think_mean_s), 4) if profile.think_mean_s > 0 else 0.0
                          for _ in range(n))
            out.append(Arrival(len(out), si, round(t, 4), kind, slots[kind], think))
            slots[kind] += 1
        t0 += st.duration_s
    return out


def slot_counts(arrivals: Iterable[Arrival]) -> dict[str, int]:
    out = {k: 0 for k in JOURNEYS}
    for a in arrivals:
        out[a.kind] = max(out[a.kind], a.slot + 1)
    return out


def step_key(seed: int, a: Arrival, step: str) -> str | None:
    return sid("key", seed, a.index, step) if keyed(step) else None


def requests(profile: Profile, arrivals: Iterable[Arrival] | None = None) -> list[PlannedRequest]:
    arrivals = plan(profile) if arrivals is None else arrivals
    return [PlannedRequest(a.index, i, step, step_key(profile.seed, a, step))
            for a in arrivals for i, step in enumerate(profile.steps(a.kind))]


def digest(reqs: Iterable[PlannedRequest]) -> str:
    rows = sorted((r.arrival, r.seq, r.step, r.key or "") for r in reqs)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def describe(profile: Profile) -> dict:
    d = asdict(profile)
    d["stages"] = [{"concurrency": s.concurrency, "duration_s": s.duration_s} for s in profile.stages]
    d["mix"] = [{"journey": k, "weight": w} for k, w in profile.mix]
    return d
