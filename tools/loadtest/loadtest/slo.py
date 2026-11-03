"""PLT-20 SLO 判定（config/slo.yaml，待技术负责人签字）：按路由组给 p50 / p95 / p99、错误率、429、503 SUBJECT_BUSY 各项“通过 / 未达”。

纯函数，不碰网络。判定：值 ≤ 上限为通过（边界算通过）；上限为 null 的项不判；一组全部为 null → 无通过线；组内没有样本 → 无样本（不算未达）。
未签字（signed_by、signed_at 缺一）时 strict 一律为 False：报告照写未达，退出码不因此失败。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .metrics import NON_ERROR, Sample, percentile
from .scenarios import ENDPOINTS

CONFIG = Path(__file__).resolve().parents[1] / "config"
FORMAT = "loadtest-slo/1"
LIMIT_KEYS = ("p50_ms", "p95_ms", "p99_ms", "error_rate", "throttled_429_rate", "subject_busy_503_rate")
KEYS = frozenset({"format", "status", "signed_by", "signed_at", "enforce", "groups"})

PASS, FAIL, NO_LINE, NO_SAMPLES = "pass", "fail", "no_threshold", "no_samples"
LABELS = {PASS: "通过", FAIL: "未达", NO_LINE: "无通过线", NO_SAMPLES: "无样本"}


@dataclass(frozen=True)
class Group:
    name: str
    steps: tuple[str, ...]
    limits: dict[str, float | None]


@dataclass(frozen=True)
class SloConfig:
    status: str
    signed_by: str | None
    signed_at: str | None
    enforce: bool
    groups: tuple[Group, ...]

    @property
    def signed(self) -> bool:
        return bool(self.signed_by) and bool(self.signed_at)

    def strict(self, requested: bool = False) -> bool:
        """严格判定（未达使退出码失败）：只在已签字且（配置 enforce 或命令行要求）时"""
        return self.signed and (self.enforce or requested)


def parse(doc: dict) -> SloConfig:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"slo config format must be {FORMAT}")
    extra = set(doc) - KEYS
    if extra:
        raise ValueError(f"unknown keys in slo config: {sorted(extra)}")
    for k in ("signed_by", "signed_at"):
        if doc.get(k) is not None and not isinstance(doc[k], str):
            raise ValueError(f"{k} must be a string or null")
    if not isinstance(doc.get("enforce", False), bool):
        raise ValueError("enforce must be true or false")
    raw = doc.get("groups")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("groups must be a non-empty mapping")
    seen: dict[str, str] = {}
    groups = []
    for name, g in raw.items():
        if not isinstance(g, dict) or set(g) - {"steps", "limits"}:
            raise ValueError(f"group {name}: only steps and limits")
        steps = g.get("steps")
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
            raise ValueError(f"group {name}: steps must be a non-empty list")
        for s in steps:
            if s not in ENDPOINTS:
                raise ValueError(f"group {name}: unknown step {s}")
            if s in seen:
                raise ValueError(f"step {s} is in both {seen[s]} and {name}")
            seen[s] = name
        limits = g.get("limits") or {}
        if not isinstance(limits, dict) or set(limits) - set(LIMIT_KEYS):
            raise ValueError(f"group {name}: limits keys must be among {LIMIT_KEYS}")
        clean: dict[str, float | None] = {}
        for k in LIMIT_KEYS:
            v = limits.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0
                                  or (k.endswith("_rate") and v > 1)):
                raise ValueError(f"group {name}: {k} must be null, a non-negative number (rates ≤ 1)")
            clean[k] = None if v is None else float(v)
        groups.append(Group(str(name), tuple(steps), clean))
    return SloConfig(str(doc.get("status") or ""), doc.get("signed_by"), doc.get("signed_at"), bool(doc.get("enforce", False)),
                     tuple(groups))


def load(path: Path | None = None) -> SloConfig:
    import yaml

    return parse(yaml.safe_load((path or CONFIG / "slo.yaml").read_text(encoding="utf-8")))


def measure(samples: list[Sample]) -> dict:
    """一组样本的各项值（契约审查 1）：分位数只取 ok 与 rejected（真正被处理的请求）；“预期”单列计数、不进分母；
    错误率与 429 / 503 占比的分母 = 非预期的请求数。count 为 0（全是预期也一样）时各项为 None"""
    expected = sum(1 for s in samples if s.outcome == "expected")
    judged = [s for s in samples if s.outcome != "expected"]
    n = len(judged)
    if n == 0:
        return {"count": 0, "expected": expected, **{k: None for k in LIMIT_KEYS}}
    ms = [s.ms for s in judged if s.outcome in ("ok", "rejected")]
    return {
        "count": n, "expected": expected,
        "p50_ms": percentile(ms, 0.50), "p95_ms": percentile(ms, 0.95), "p99_ms": percentile(ms, 0.99),
        "error_rate": sum(1 for s in judged if s.outcome not in NON_ERROR) / n,
        "throttled_429_rate": sum(1 for s in judged if s.outcome == "throttled") / n,
        "subject_busy_503_rate": sum(1 for s in judged if s.outcome == "busy") / n,
    }


def judge(values: dict, limits: dict[str, float | None]) -> dict:
    """逐项判定 → {verdict, checks: {项: {value, limit, verdict}}}"""
    lined = {k: v for k, v in limits.items() if v is not None}
    if not lined:
        return {"verdict": NO_LINE, "checks": {}}
    if not values.get("count"):
        return {"verdict": NO_SAMPLES, "checks": {k: {"value": None, "limit": v, "verdict": NO_SAMPLES} for k, v in lined.items()}}
    checks = {}
    for k, limit in lined.items():
        v = values[k]
        checks[k] = {"value": None if v is None else round(v, 4 if k.endswith("_rate") else 1), "limit": limit,
                     "verdict": PASS if v is not None and v <= limit else FAIL}
    return {"verdict": FAIL if any(c["verdict"] == FAIL for c in checks.values()) else PASS, "checks": checks}


def evaluate(cfg: SloConfig, samples: Iterable[Sample]) -> dict:
    """全部样本 → 每组判定（总体与按到达阶段）、无通过线的步骤、是否全部通过"""
    samples = list(samples)
    stages = sorted({s.stage for s in samples})
    grouped = {s for g in cfg.groups for s in g.steps}
    out_groups = []
    for g in cfg.groups:
        mine = [s for s in samples if s.step in g.steps]
        overall = measure(mine)
        by_stage = []
        for st in stages:
            vals = measure([s for s in mine if s.stage == st])
            by_stage.append({"stage": st, "count": vals["count"], **judge(vals, g.limits)})
        out_groups.append({"group": g.name, "steps": list(g.steps), "limits": g.limits,
                           "values": {k: (None if v is None else round(v, 4) if k.endswith("_rate") else v) for k, v in overall.items()},
                           **judge(overall, g.limits), "by_stage": by_stage})
    ungrouped = sorted({s.step for s in samples} - grouped)
    failed = [g["group"] for g in out_groups if g["verdict"] == FAIL]
    return {"status": cfg.status, "signed": cfg.signed, "groups": out_groups, "ungrouped_steps": ungrouped,
            "failed_groups": failed, "all_pass": not failed}


def exit_ok(result: dict, strict: bool) -> bool:
    """退出码是否因 SLO 失败：只在严格判定时看 all_pass"""
    return result["all_pass"] or not strict
