"""指标（spec 设计要点 1）：各接口 p50 / p95 / p99、错误率、吞吐；只存汇总需要的数（耗时、状态类别），不存请求或响应内容。

分位数用“最近秩”法（nearest-rank）：第 q 分位 = 升序第 ceil(q × n) 个值；报告与测试都按这个定义。
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from .scenarios import ENDPOINTS


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if not 0 < q <= 1:
        raise ValueError("q must be in (0, 1]")
    s = sorted(values)
    return s[max(1, math.ceil(q * len(s))) - 1]


@dataclass
class Sample:
    stage: int
    step: str
    ms: float
    outcome: str  # ok | http_4xx | http_5xx | transport | rejected（业务拒绝：额度、宵禁等）| skipped


@dataclass
class Recorder:
    samples: list[Sample] = field(default_factory=list)
    stage_windows: dict[int, list[float]] = field(default_factory=dict)  # 阶段 → [开始, 结束]（相对秒）
    queue_wait_ms: list[float] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    codes: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))  # (步骤, 状态码或错误类别) → 次数

    def add(self, stage: int, step: str, ms: float, outcome: str, code: str) -> None:
        self.samples.append(Sample(stage, step, ms, outcome))
        self.codes[(step, code)] += 1

    def skip(self, step: str) -> None:
        self.skipped[step] += 1


def summarize(values: list[float], errors: int, total: int, seconds: float) -> dict:
    return {
        "count": total, "errors": errors, "error_rate": round(errors / total, 4) if total else None,
        "p50_ms": _r(percentile(values, 0.50)), "p95_ms": _r(percentile(values, 0.95)), "p99_ms": _r(percentile(values, 0.99)),
        "max_ms": _r(max(values) if values else None),
        "throughput_rps": round(total / seconds, 3) if seconds > 0 else None,
    }


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def by_endpoint(rec: Recorder, seconds: float) -> dict[str, dict]:
    groups: dict[str, list[Sample]] = defaultdict(list)
    for s in rec.samples:
        groups[s.step].append(s)
    out = {}
    order = {k: i for i, k in enumerate(ENDPOINTS)}
    for step, ss in sorted(groups.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])):
        errs = sum(1 for s in ss if s.outcome not in ("ok", "rejected"))
        d = summarize([s.ms for s in ss], errs, len(ss), seconds)
        d["rejected"] = sum(1 for s in ss if s.outcome == "rejected")
        d["codes"] = {code: n for (st, code), n in sorted(rec.codes.items()) if st == step}
        out[step] = d
    return out


def by_stage(rec: Recorder) -> list[dict]:
    out = []
    for si, (start, end) in sorted(rec.stage_windows.items()):
        ss = [s for s in rec.samples if s.stage == si]
        errs = sum(1 for s in ss if s.outcome not in ("ok", "rejected"))
        d = summarize([s.ms for s in ss], errs, len(ss), end - start)
        d["stage"] = si
        d["seconds"] = round(end - start, 2)
        out.append(d)
    return out
