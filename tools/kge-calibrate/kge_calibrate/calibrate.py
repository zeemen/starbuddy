"""前置边检验与隐藏依赖候选（纯函数，只产出聚合数）。

结论只是候选：不改地图、不写库；落到审核台、两人确认与次版本发布另起任务（spec“定位”）。

防泄露守卫（spec 范围 1；安全审查 B1“差分泄露”）：
- 输出不含任何精确计数：人数一律向下取整到 10（n_approx），比率、区间、差值、phi、对数优势比保留 2 位，p 值只给分档；
  同一次运行内跨表相减也得不到精确数（因为没有精确数可减）。
- 表键扰动（参照统计机构“cell key”做法，确定性、逐字节一致）：每名学生一个由伪名派生的记录键（伪名在试点导出时由库内一次性 salt
  生成，键不可预知）；一张四格表的表键 = 表内全体学生记录键之和，四格各加一个由表键决定的扰动（−2…+2）。报表的全部数值与结论
  都由扰动后的四格算出。删掉表里任何一人，表键就整体换掉、四格的扰动全部重抽，两次输出相减分不出这个人落在哪一格；
  不在表里的人删掉，这张表的输出不变。
- 门槛：扰动后的四格每格都 ≥ min_cell + NOISE_MAX 才出数值与结论（保证真实每格 ≥ min_cell），否则只写“样本不足”。
测试可以关掉扰动（noisy=False）核对统计口径；报表入口 report.build_report 永远开着。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from . import stats as S
from .config import Config
from .inputs import Edge, MapView, Mastery

HOLDS = "成立"
NOT_HOLDS = "不成立候选"
INSUFFICIENT = "样本不足"
UNDECIDED = "待定"
WEAK_ONLY = "仅报告（弱前置）"
CANDIDATE = "新增前置边候选"
BELOW = "未达阈值"
P_BANDS = ((0.001, "<0.001"), (0.01, "<0.01"), (0.05, "<0.05"))
P_TOP = "≥0.05"
# 扰动分布（无偏）：(扰动, 累计概率)
NOISE_TABLE = ((-2, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (2, 1.0))
NOISE_MAX = 2
KEY_MOD = 1 << 64


def r2(x: float) -> float:
    """比率、区间等统一保留 2 位（-0.0 写成 0.0）"""
    v = round(float(x), 2)
    return 0.0 if v == 0 else v


def r1(x: float) -> float:
    v = round(float(x), 1)
    return 0.0 if v == 0 else v


def n_approx(n: int) -> int:
    """人数粗分档：向下取整到 10"""
    return (n // 10) * 10


def p_band(p: float) -> str:
    for cut, label in P_BANDS:
        if p < cut:
            return label
    return P_TOP


def record_key(pid: str) -> int:
    """学生的记录键：由伪名派生（只在内存里用）"""
    return int.from_bytes(hashlib.sha256(b"kge-calibrate/record-key\x00" + pid.encode("utf-8")).digest()[:8], "big")


def set_key(pids: Iterable[str]) -> int:
    return sum(record_key(p) for p in pids) % KEY_MOD


def noise(key: int, slot: str) -> int:
    """由表键与格位决定的扰动（−2…+2）"""
    h = hashlib.sha256(f"kge-calibrate/noise\x00{key}\x00{slot}".encode("utf-8")).digest()
    u = int.from_bytes(h[:8], "big") / KEY_MOD
    for value, cum in NOISE_TABLE:
        if u < cum:
            return value
    return NOISE_TABLE[-1][0]


@dataclass(frozen=True)
class Table2:
    """两节点的四格表（只在两节点都已观测的学生上）：a = 都掌握，b = 只掌握 u，c = 只掌握 v，d = 都未掌握；key = 表键"""
    a: int
    b: int
    c: int
    d: int
    key: int = 0

    @property
    def n(self) -> int:
        return self.a + self.b + self.c + self.d

    def cells(self) -> tuple[int, int, int, int]:
        return self.a, self.b, self.c, self.d


def table2(mastery: Mastery, u: str, v: str, only: Iterable[str] | None = None) -> Table2:
    mu, mv = mastery.observed.get(u, {}), mastery.observed.get(v, {})
    keys = set(mu) & set(mv)
    if only is not None:
        keys &= set(only)
    a = b = c = d = 0
    for k in keys:
        x, y = mu[k], mv[k]
        if x and y:
            a += 1
        elif x:
            b += 1
        elif y:
            c += 1
        else:
            d += 1
    return Table2(a, b, c, d, set_key(keys))


def perturb(t: Table2) -> Table2:
    """四格各加一个由表键决定的扰动；不小于 0"""
    a, b, c, d = (max(0, x + noise(t.key, slot)) for x, slot in zip(t.cells(), "abcd"))
    return replace(t, a=a, b=b, c=c, d=d)


def disclosable(t: Table2, min_cell: int, margin: int = 0) -> bool:
    return min(t.cells()) >= min_cell + margin


# ---------- 由计数到输出数值（报表与差分测试共用同一函数） ----------
def edge_numbers(x1: int, n1: int, x2: int, n2: int, z: float) -> dict[str, Any]:
    """前置边一条记录里由计数决定的全部数值（不含 p 分档与观测率）：只含粗分档人数与 2 位比率、区间"""
    l1, u1 = S.wilson(x1, n1, z)
    l2, u2 = S.wilson(x2, n2, z)
    d, lo, hi = S.newcombe(x1, n1, x2, n2, z)
    return {"a_mastered": {"n_approx": n_approx(n1), "b_rate": r2(x1 / n1), "b_rate_ci": [r2(l1), r2(u1)]},
            "a_not_mastered": {"n_approx": n_approx(n2), "b_rate": r2(x2 / n2), "b_rate_ci": [r2(l2), r2(u2)]},
            "diff": r2(d), "diff_ci": [r2(lo), r2(hi)]}


def pair_numbers(a: int, b: int, c: int, d: int, z: float) -> dict[str, Any]:
    """节点对一条记录里由四格计数决定的全部数值（不含 p 分档）"""
    n = a + b + c + d
    ph = S.phi(a, b, c, d)
    lor = S.log_odds_ratio(a, b, c, d, z)
    return {"n_approx": n_approx(n), "u_rate": r2((a + b) / n), "v_rate": r2((a + c) / n),
            "phi": None if ph is None else r2(ph), "log_odds_ratio": None if lor is None else r2(lor[0]),
            "log_odds_ratio_ci": None if lor is None else [r2(lor[1]), r2(lor[2])]}


# ---------- 前置边检验 ----------
def _observed_rate(mastery: Mastery, node: str, mastered: bool, n_group: int, do_perturb: bool) -> float:
    """组内在 B 上有观测的比例：分母是 A 上有观测、A 掌握与否相同的学生数（扰动时同样按表键加扰动）"""
    members = [k for k, m in mastery.observed.get(node, {}).items() if m == mastered]
    total = len(members) + (noise(set_key(members), "obs") if do_perturb else 0)
    return min(1.0, n_group / max(1, total))


def _edge_raw(edge: Edge, mastery: Mastery, cfg: Config, do_perturb: bool) -> dict[str, Any]:
    t = table2(mastery, edge.from_id, edge.to_id)
    if do_perturb:
        t = perturb(t)
    n1, x1 = t.a + t.b, t.a      # A 已掌握组：人数、其中 B 掌握
    n2, x2 = t.c + t.d, t.c      # A 未掌握组
    out: dict[str, Any] = {"from": edge.from_id, "to": edge.to_id, "strength": edge.strength}
    if not disclosable(t, cfg.min_cell, NOISE_MAX if do_perturb else 0):
        out["_eligible"] = False
        return out
    out.update(_eligible=True, _n=(x1, n1, x2, n2), _p=S.two_proportion_p(x1, n1, x2, n2),
               _obs=(_observed_rate(mastery, edge.from_id, True, n1, do_perturb),
                     _observed_rate(mastery, edge.from_id, False, n2, do_perturb)))
    return out


def _strong_conclusion(d: float, lo: float, hi: float, hi_adj: float, p_adj: float, cfg: Config) -> tuple[str, str]:
    if lo > 0.0 and p_adj <= cfg.prereq_fdr_q and d >= cfg.min_effect:
        return HOLDS, "差值显著为正"
    if lo <= 0.0 <= hi:
        return NOT_HOLDS, "差值区间包含 0：A 未掌握者在 B 上并不显著更差"
    if hi < 0.0:
        return NOT_HOLDS, "差值区间整体小于 0：A 未掌握者在 B 上反而更好（反向）"
    if hi_adj < cfg.min_effect:
        return NOT_HOLDS, "按族校正后的差值区间整体低于效应量阈值：A 未掌握者在 B 上只略差（可能只是总体能力相关）"
    return UNDECIDED, "差值区间在 0 以上、跨过效应量阈值，或校正后不显著"


def check_edges(mp: MapView, mastery: Mastery, cfg: Config, noisy: bool = True) -> list[dict[str, Any]]:
    """每条前置边一条记录（按 from、to 排序）。强前置与弱前置各为一族做 BH；弱前置只报告不出候选"""
    raws = [_edge_raw(e, mastery, cfg, noisy) for e in mp.edges]
    z = S.z_for(cfg.confidence)
    adj: dict[int, float] = {}
    z_adj: dict[str, float] = {}
    for strength in ("strong", "weak"):
        fam = [i for i, r in enumerate(raws) if r["strength"] == strength and r["_eligible"]]
        for i, q in zip(fam, S.bh_adjust([raws[i]["_p"] for i in fam])):
            adj[i] = q
        z_adj[strength] = S.z_for(1.0 - (1.0 - cfg.confidence) / max(1, len(fam)))   # 按族校正的置信水平
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raws):
        rec: dict[str, Any] = {"from": r["from"], "to": r["to"], "strength": r["strength"],
                               "from_topic": mp.nodes[r["from"]], "to_topic": mp.nodes[r["to"]]}
        if not r["_eligible"]:
            rec.update(conclusion=INSUFFICIENT, reason=f"四格（A 掌握与否 × B 掌握与否）有一格人数不足门槛 {cfg.min_cell}", disclosed=False)
            out.append(rec)
            continue
        x1, n1, x2, n2 = r["_n"]
        d, lo, hi = S.newcombe(x1, n1, x2, n2, z)
        if r["strength"] == "strong":
            hi_adj = S.newcombe(x1, n1, x2, n2, z_adj["strong"])[2]
            conclusion, reason = _strong_conclusion(d, lo, hi, hi_adj, adj[i], cfg)
        else:
            conclusion, reason = WEAK_ONLY, "弱前置只报告，不出候选"
        nums = edge_numbers(x1, n1, x2, n2, z)
        nums["a_mastered"]["b_observed_rate"] = r1(r["_obs"][0])
        nums["a_not_mastered"]["b_observed_rate"] = r1(r["_obs"][1])
        rec.update(conclusion=conclusion, reason=reason, disclosed=True, p_adj_band=p_band(adj[i]), **nums)
        out.append(rec)
    return out


# ---------- 隐藏依赖 ----------
def reach(pairs: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    """前置边的传递闭包：节点 → 它（经一条或多条前置边）能到达的全部节点"""
    succ: dict[str, set[str]] = {}
    for f, t in pairs:
        succ.setdefault(f, set()).add(t)
    out: dict[str, frozenset[str]] = {}
    for start in sorted(succ):
        seen: set[str] = set()
        stack = sorted(succ[start])
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(sorted(succ.get(x, ())))
        out[start] = frozenset(seen)
    return out


def ancestors_map(closure: Mapping[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """节点 → 它的全部（传递）前置；一次算好、反复查（不在节点对循环里重算）"""
    acc: dict[str, set[str]] = {}
    for a, rs in closure.items():
        for r in rs:
            acc.setdefault(r, set()).add(a)
    return {k: frozenset(v) for k, v in acc.items()}


def shared_prerequisites(anc: Mapping[str, frozenset[str]], u: str, v: str, mp: MapView, mastery: Mastery) -> list[str]:
    """共同前置：只算地图里可计算、且有观测的节点（不可计算或无人观测的祖先无从分层）"""
    common = anc.get(u, frozenset()) & anc.get(v, frozenset())
    return sorted(a for a in common if a in mp.nodes and mastery.observed.get(a))


def scan_pairs(mp: MapView, mastery: Mastery, cfg: Config, noisy: bool = True) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """同一主题内未连边、不传递可达的节点对。返回 (每对一条记录，按主题与节点排序；排除计数——都是节点对数，不是人数)"""
    closure = reach(mp.all_prereq)
    anc = ancestors_map(closure)
    direct = {frozenset(p) for p in mp.all_prereq}
    by_topic: dict[str, list[str]] = {}
    for nid, topic in mp.nodes.items():
        by_topic.setdefault(topic, []).append(nid)
    excluded = {"direct_edge": 0, "transitive": 0, "topic_over_limit": 0}
    raws: list[dict[str, Any]] = []
    for topic in sorted(by_topic):
        ids = sorted(by_topic[topic])
        total = len(ids) * (len(ids) - 1) // 2
        if total > cfg.max_pairs_per_topic:
            excluded["topic_over_limit"] += total
            continue
        for i, u in enumerate(ids):
            for v in ids[i + 1:]:
                if frozenset((u, v)) in direct:
                    excluded["direct_edge"] += 1
                    continue
                if v in closure.get(u, ()) or u in closure.get(v, ()):
                    excluded["transitive"] += 1
                    continue
                shared = shared_prerequisites(anc, u, v, mp, mastery)
                only = None
                if shared and cfg.condition_on_shared:
                    only = _all_mastered(mastery, shared)
                t = table2(mastery, u, v, only)
                if noisy:
                    t = perturb(t)
                raws.append({"u": u, "v": v, "topic": topic, "shared": shared, "conditioned": only is not None, "_t": t})
    margin = NOISE_MAX if noisy else 0
    eligible = [i for i, r in enumerate(raws) if disclosable(r["_t"], cfg.min_cell, margin)]
    z = S.z_for(cfg.confidence)
    pvals: list[float] = []
    for i in eligible:
        t = raws[i]["_t"]
        ph = S.phi(*t.cells())
        raws[i]["_phi"] = ph
        pvals.append(1.0 if ph is None else S.chi2_p(ph, t.n))
    adjusted = dict(zip(eligible, S.bh_adjust(pvals)))
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raws):
        rec: dict[str, Any] = {"u": r["u"], "v": r["v"], "topic": r["topic"], "shared_prerequisites": r["shared"],
                               "conditioned_on_shared": r["conditioned"]}
        if i not in adjusted:
            rec.update(conclusion=INSUFFICIENT, reason=f"四格最小格不足门槛 {cfg.min_cell}", disclosed=False)
            out.append(rec)
            continue
        t, ph, p_adj = r["_t"], r["_phi"], adjusted[i]
        ok = ph is not None and ph >= cfg.min_phi and p_adj <= cfg.hidden_fdr_q
        rec.update(conclusion=CANDIDATE if ok else BELOW,
                   reason="phi 达到阈值且校正后显著（方向不定，两向都列）" if ok else "phi 未达阈值或校正后不显著",
                   disclosed=True, p_adj_band=p_band(p_adj), **pair_numbers(*t.cells(), z))
        if ok:
            rec["directions"] = [[r["u"], r["v"]], [r["v"], r["u"]]]
        out.append(rec)
    return out, excluded


def _all_mastered(mastery: Mastery, nodes: list[str]) -> set[str]:
    """在这些节点上全部已观测且全部掌握的学生（只在内存里用）"""
    sets = [{k for k, m in mastery.observed.get(n, {}).items() if m} for n in nodes]
    return set.intersection(*sets) if sets else set()
