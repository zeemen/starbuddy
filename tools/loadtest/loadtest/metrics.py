"""指标（spec 设计要点 1）：各接口 p50 / p95 / p99、错误率、吞吐；只存汇总需要的数（耗时、状态类别），不存请求或响应内容。

分位数用“最近秩”法（nearest-rank）：第 q 分位 = 升序第 ceil(q × n) 个值；报告与测试都按这个定义。
"""
from __future__ import annotations

import json
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
    # ok | http_4xx | http_5xx | transport | rejected（业务拒绝：额度、宵禁等）| skipped
    # | throttled（429 限流）| busy（503 SUBJECT_BUSY：分学科并发闸门被拒，PLT-16）——后两类仍算错误，报表另单列
    # | expected（PLT-20：本栈缺依赖时的预期结果，例如没有洞察服务时的 503 UNAVAILABLE、派生票据的 422 TICKET_INVALID；不算错误，单列）
    outcome: str


NON_ERROR = ("ok", "rejected", "expected")


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

    # PLT-20：可选场景每个请求走的是“真 ID / 真票据”（real，必须 2xx）还是“派生 / 预期路径”（derived，必须是预期结果）
    paths: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))  # (步骤, real|derived, 类别) → 次数
    notes: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))       # (步骤, 核对标记) → 次数，如 resumed_new

    def skip(self, step: str) -> None:
        self.skipped[step] += 1

    def path(self, step: str, kind: str, outcome: str) -> None:
        if kind not in ("real", "derived"):
            raise ValueError("path kind must be real or derived")
        self.paths[(step, kind, outcome)] += 1

    def note(self, step: str, what: str) -> None:
        self.notes[(step, what)] += 1


def path_summary(rec: Recorder) -> dict[str, dict]:
    """按步骤：真 ID / 真票据与派生路径各多少，及两类告警——派生路径里不是预期结果的、真路径里不是 2xx 的（PLT-20 审查 S3、契约 5）"""
    out: dict[str, dict] = {}
    for (step, kind, outcome), n in sorted(rec.paths.items()):
        d = out.setdefault(step, {"real": 0, "derived": 0, "real_not_2xx": 0, "derived_not_expected": 0})
        d[kind] += n
        if kind == "real" and outcome != "ok":
            d["real_not_2xx"] += n
        if kind == "derived" and outcome != "expected":
            d["derived_not_expected"] += n
    for d in out.values():
        d["expected_share"] = round((d["derived"] - d["derived_not_expected"]) / d["derived"], 4) if d["derived"] else None
        d["alert"] = d["real_not_2xx"] > 0 or d["derived_not_expected"] > 0
    return out


def count_log(lines, route: str, field_name: str, value: str, status: int) -> int:
    """BFF 访问日志里（路由模板、某个 note 字段取值、状态码）都对上的行数（只读这三个字段）"""
    n = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("route") == route and rec.get(field_name) == value and rec.get("status") == status:
            n += 1
    return n


def demote_unverified(rec: Recorder, step: str, label: str, verified: int, new_label: str) -> int:
    """记为“预期”的样本里，只有 verified 个能从栈日志核对到；多出来的（从后往前）改记为 http_5xx，并同步状态标签与路径计数。
    返回改记的个数（PLT-20 审查 S3：核对不到就不算预期）"""
    idx = [i for i, s in enumerate(rec.samples) if s.step == step and s.outcome == "expected"]
    extra = max(0, len(idx) - max(0, verified))
    for i in idx[len(idx) - extra:]:
        rec.samples[i] = Sample(rec.samples[i].stage, step, rec.samples[i].ms, "http_5xx")
    if extra:
        rec.codes[(step, label)] -= extra
        if rec.codes[(step, label)] <= 0:
            del rec.codes[(step, label)]
        rec.codes[(step, new_label)] += extra
        moved = min(extra, rec.paths.get((step, "derived", "expected"), 0))
        if moved:
            rec.paths[(step, "derived", "expected")] -= moved
            if rec.paths[(step, "derived", "expected")] <= 0:
                del rec.paths[(step, "derived", "expected")]
            rec.paths[(step, "derived", "http_5xx")] += moved
    return extra


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
        errs = sum(1 for s in ss if s.outcome not in NON_ERROR)
        d = summarize([s.ms for s in ss], errs, len(ss), seconds)
        d["rejected"] = sum(1 for s in ss if s.outcome == "rejected")
        d["throttled_429"] = sum(1 for s in ss if s.outcome == "throttled")
        d["subject_busy_503"] = sum(1 for s in ss if s.outcome == "busy")
        d["expected"] = sum(1 for s in ss if s.outcome == "expected")
        d["codes"] = {code: n for (st, code), n in sorted(rec.codes.items()) if st == step}
        out[step] = d
    return out


GATE_RESULTS = ("admitted", "queued", "rejected", "bypassed")
GATE_BUCKETS = ("0", "lt100", "lt500", "lt2000", "ge2000")
GATE_REASONS = ("full", "timeout", "per_student", "aborted")


def gate_summary(lines) -> dict:
    """BFF 访问日志（每行一个 JSON）里分学科并发闸门的字段（PLT-16）汇总：按学科的放行 / 排队后放行 / 被拒次数、
    被拒原因、排队毫秒分档。只读 gate_* 这几个枚举字段，不读也不留任何别的内容；开关关时没有这些字段，结果为空。"""
    out: dict[str, dict] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        subject, result, bucket = rec.get("gate_subject"), rec.get("gate"), rec.get("gate_queue_ms")
        # 绕过闸门（已计量的重试）不排队，没有分档
        if not isinstance(subject, str) or result not in GATE_RESULTS or (result != "bypassed" and bucket not in GATE_BUCKETS):
            continue
        d = out.setdefault(subject, {"admitted": 0, "queued": 0, "rejected": 0, "bypassed": 0, "reasons": {}, "queue_ms": {}})
        d[result] += 1
        if result != "bypassed":
            d["queue_ms"][bucket] = d["queue_ms"].get(bucket, 0) + 1
        reason = rec.get("gate_reason")
        if result == "rejected" and reason in GATE_REASONS:
            d["reasons"][reason] = d["reasons"].get(reason, 0) + 1
    return dict(sorted(out.items()))


def by_stage(rec: Recorder) -> list[dict]:
    out = []
    for si, (start, end) in sorted(rec.stage_windows.items()):
        ss = [s for s in rec.samples if s.stage == si]
        errs = sum(1 for s in ss if s.outcome not in NON_ERROR)
        d = summarize([s.ms for s in ss], errs, len(ss), end - start)
        d["throttled_429"] = sum(1 for s in ss if s.outcome == "throttled")
        d["subject_busy_503"] = sum(1 for s in ss if s.outcome == "busy")
        d["expected"] = sum(1 for s in ss if s.outcome == "expected")
        d["stage"] = si
        d["seconds"] = round(end - start, 2)
        out.append(d)
    return out
