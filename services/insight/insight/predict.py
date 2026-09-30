"""考前预测与考后对账（SIE V1.1 第五章；spec 设计要点 2；迁移 0034）：纯函数，不连库、不读系统时钟。

预测：范围节点 × 掌握 × 地区考情权重 × 题型 → 得分率均值与不确定度 → 80% 区间（z 取配置），换算成分数。
- 掌握：每个范围节点取 KGE 掌握行（KGE-04 第二步前是合成掌握行）；p 用 p_mastery，缺省按门槛映射（配置 gate_p）；
  向先验收缩：p̂ = (n·p + k·p₀) / (n + k)，方差 p̂(1−p̂) / (n + k + 1)（n = 该节点的去重证据数，k = prior_strength）。
  没有掌握行的节点按先验、n = 0 计入：证据越少区间越宽（有性质测试）。
- 地区考情权重：policy.* 洞察服务没有读权限（lessons），由调用方注入，缺省等权；题型系数缺省 1（KGE-04 #4 题型来源待定）。
- 区间 = clip(μ ± z·sqrt(Σ wᵢ² varᵢ + base_sd²)) × 满分，保留一位小数；只给区间，不给概率、不造精确能力分。
- 口径：七八年级 100 分制、九年级 120 分制分开（配置 scales 按满分标注口径，只在同口径之间比较）；满分按 1–300 校验，
  契约响应的分数上限是 150（lessons 契约差异），超出的考试不出预测。
对账：成绩对冻结区间判断区间内 / 外；偏差节点来自调用方给的逐节点得分率（上传解析，KGE-05；没有就为空）；
偏差方向只用契约四值：区间内为空；区间外取调用方给的方向，没给时低于区间默认“此前诊断偏乐观”，高于区间没有合适的方向（待人确认），
不出对账卡。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from .claims import NODE_ID, MasteryRow
from .sie03_policy import DIRECTIONS, IntervalParams

ALGORITHM = "sie-predict"
ALGORITHM_VERSION = "0.1.0"
MODEL_VERSION = f"algo:{ALGORITHM}@{ALGORITHM_VERSION}"
RECONCILE_MODEL_VERSION = "algo:sie-reconcile@0.1.0"
# 契约 ExamSpec.exam_type → 0022 exam_record.exam_type（mock 对应一模、二模两个值）
EXAM_TYPES: dict[str, tuple[str, ...]] = {"monthly": ("monthly",), "midterm": ("midterm",), "final": ("final",),
                                          "mock": ("mock1", "mock2"), "other": ("other",)}
MAX_SCOPE = 64  # 0022 exam_record.scope_node_ids 的上限（契约是 400，lessons 契约差异）


class PredictInputError(ValueError):
    """输入不合要求（不带内容）"""


class MasterySource(Protocol):
    """范围节点的当前掌握行（KGE-04 第二步：platform.mastery_estimate 的节点键行）。落地前由调用方注入合成掌握行"""

    async def rows(self, student: str, subject: str, node_ids: Sequence[str]) -> Sequence[MasteryRow]: ...


class NoMasterySource:
    """缺省：还没有节点键的掌握行（KGE-04 第二步未落地）→ 没有证据，insight.predict 回“证据不足”（不编造区间）"""

    async def rows(self, student: str, subject: str, node_ids: Sequence[str]) -> Sequence[MasteryRow]:  # noqa: ARG002
        return ()


@dataclass(frozen=True)
class NodeEstimate:
    node_id: str
    p: float           # 收缩后的掌握概率
    n: int             # 去重证据数
    weight: float      # 归一后的权重
    type_factor: float
    var: float

    def as_basis(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "p": round(self.p, 4), "n": self.n, "w": round(self.weight, 4),
                "t": round(self.type_factor, 3), "sd": round(math.sqrt(self.var), 4)}


@dataclass(frozen=True)
class Prediction:
    low: float
    high: float
    mean: float
    sd: float
    node_count: int
    evidence_count: int
    evidence_ids: tuple[str, ...]
    nodes: tuple[NodeEstimate, ...]
    scale: str
    confidence: float

    def basis(self, full_score: int, params_version: str, weights_source: str) -> dict[str, Any]:
        """0034 exam_prediction.basis：计算依据快照（不对学生家长展示、不导出）"""
        return {"algorithm": MODEL_VERSION, "params": params_version, "weights": weights_source, "scale": self.scale,
                "full_score": full_score, "mean": round(self.mean, 4), "sd": round(self.sd, 4),
                "node_count": self.node_count, "evidence_count": self.evidence_count, "nodes": [n.as_basis() for n in self.nodes]}


def scale_of(full_score: int, scales: Mapping[int, str]) -> str:
    return scales.get(full_score, "custom")


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, x))


def _node_input(rows: Sequence[MasteryRow], params: IntervalParams) -> tuple[float, int, tuple[str, ...]]:
    """一个节点的全部掌握行（不同认知层次）→ 证据加权的 p 与去重证据数"""
    if not rows:
        return params.prior_p, 0, ()
    ev: dict[str, None] = {}
    num = den = 0.0
    for r in sorted(rows, key=lambda x: (x.cognitive_level, x.basis_id)):
        p = r.p_mastery if r.p_mastery is not None else params.gate_p[r.gate]
        k = max(1, len(set(r.evidence_ids)))
        num += p * k
        den += k
        for e in r.evidence_ids:
            ev.setdefault(e, None)
    return num / den, len(ev), tuple(ev)


def predict_interval(full_score: int, scope: Sequence[str], rows: Sequence[MasteryRow], params: IntervalParams,
                     scales: Mapping[int, str], weights: Mapping[str, float] | None = None,
                     type_factors: Mapping[str, float] | None = None) -> Prediction:
    if not isinstance(full_score, int) or isinstance(full_score, bool) or not 1 <= full_score <= 300:
        raise PredictInputError("full score must be an integer in 1..300")
    nodes = list(dict.fromkeys(scope))
    if not nodes or len(nodes) != len(scope) or len(nodes) > MAX_SCOPE or not all(NODE_ID.fullmatch(n) for n in nodes):
        raise PredictInputError("scope must be 1..64 distinct node ids")
    for r in rows:
        r.check()
    by_node: dict[str, list[MasteryRow]] = {n: [] for n in nodes}
    for r in rows:
        if r.node_id in by_node:
            by_node[r.node_id].append(r)
    raw_w = {n: float((weights or {}).get(n, 1.0)) for n in nodes}
    if any(not math.isfinite(w) or w < 0 for w in raw_w.values()) or sum(raw_w.values()) <= 0:
        raise PredictInputError("weights must be non-negative and not all zero")
    total_w = sum(raw_w.values())
    k = params.prior_strength
    estimates = []
    all_ev: dict[str, None] = {}
    for n in nodes:
        p_raw, cnt, ev = _node_input(by_node[n], params)
        for e in ev:
            all_ev.setdefault(e, None)
        p_hat = (cnt * p_raw + k * params.prior_p) / (cnt + k)
        t = float((type_factors or {}).get(n, 1.0))
        if not math.isfinite(t) or not 0 < t <= 2:
            raise PredictInputError("type factor must be in (0, 2]")
        var = p_hat * (1 - p_hat) / (cnt + k + 1)
        estimates.append(NodeEstimate(n, p_hat, cnt, raw_w[n] / total_w, t, var))
    mean = sum(e.weight * _clip(e.p * e.type_factor) for e in estimates)
    sd = math.sqrt(sum(e.weight ** 2 * e.var * e.type_factor ** 2 for e in estimates) + params.base_sd ** 2)
    lo_f, hi_f = _clip(mean - params.z * sd), _clip(mean + params.z * sd)
    low = math.floor(lo_f * full_score * 10) / 10
    high = min(float(full_score), math.ceil(hi_f * full_score * 10) / 10)
    confidence = round(_clip(1 - (high - low) / full_score), 3)
    return Prediction(low, high, mean, sd, len(nodes), len(all_ev), tuple(all_ev), tuple(estimates),
                      scale_of(full_score, scales), confidence)


# ---------- 对账 ----------
@dataclass(frozen=True)
class ReconcilePlan:
    in_interval: bool
    position: str                   # inside / below / above
    deviation_nodes: tuple[str, ...]
    directions: tuple[str, ...]
    blocked: str | None = None


def reconcile_plan(raw_score: float, low: float, high: float, basis: Mapping[str, Any], z: float,
                   node_results: Mapping[str, float] | None = None, directions: Sequence[str] | None = None) -> ReconcilePlan:
    """区间内外、偏差节点、偏差方向（契约四值）。node_results：节点 → 实得得分率（0–1，来自上传解析；没有就不判偏差节点）"""
    position = "inside" if low <= raw_score <= high else ("below" if raw_score < low else "above")
    scope = {n["node_id"]: n for n in basis.get("nodes", []) if isinstance(n, dict) and "node_id" in n}
    dev = []
    for node, frac in sorted((node_results or {}).items()):
        if node not in scope:
            raise PredictInputError("node result outside the exam scope")
        if not isinstance(frac, (int, float)) or isinstance(frac, bool) or not 0 <= frac <= 1:
            raise PredictInputError("node result must be a fraction")
        est = scope[node]
        if abs(float(frac) - float(est["p"]) * float(est.get("t", 1))) > z * float(est["sd"]) + 1e-9:
            dev.append(node)
    given = tuple(dict.fromkeys(directions or ()))
    if any(d not in DIRECTIONS for d in given):
        raise PredictInputError("deviation directions must be one of the four contract values")
    if position == "inside":
        return ReconcilePlan(True, position, tuple(dev), ())
    if given:
        return ReconcilePlan(False, position, tuple(dev), tuple(d for d in DIRECTIONS if d in given))
    if position == "below":
        return ReconcilePlan(False, position, tuple(dev), ("diagnosis_optimistic",))
    return ReconcilePlan(False, position, tuple(dev), (), blocked="no_direction_for_above_interval")


@dataclass(frozen=True)
class SentenceClaim:
    claim_id: str
    student_id: str
    template_key: str
    template_approved: bool
    status: str
    visibility: tuple[str, ...]
    expires_at: datetime
    directions: tuple[str, ...] = ()   # 判断 value 里记下的偏差方向（契约审查 M2）


def check_sentences(sentences: Sequence[Mapping[str, Any]], claims: Mapping[str, SentenceClaim], student: str,
                    audience: Sequence[str], now: datetime) -> list[str]:
    """对账卡的每句：只引用该学生、模板已审核、未过期、成立或假设、可见范围覆盖读者的判断（与 0034 守卫同一条件；规则 11）。
    返回不合格的句子序号（空表示通过）"""
    bad = []
    if not 1 <= len(sentences) <= 20 or not audience:
        return ["count"]
    for i, s in enumerate(sentences):
        keys = set(s)
        c = claims.get(str(s.get("claim_id")))
        ok = (keys in ({"claim_id", "template_key"}, {"claim_id", "template_key", "direction"})
              and ("direction" not in s or (s["direction"] in DIRECTIONS and c is not None and s["direction"] in c.directions))
              and c is not None and c.student_id == student and c.template_key == s.get("template_key") and c.template_approved
              and c.status in ("hypothesis", "established") and c.expires_at > now and set(audience) <= set(c.visibility))
        if not ok:
            bad.append(str(i))
    return bad
