"""第二步·节点内部分化（拆分节点候选；纯函数，只产出聚合数；spec-step2 范围 3）。

1. 题目级正确率：每道题一张两格表（答对 / 答错），两格各按表键与表标签加 −2…+2 的扰动；扰动后作答人数（两格之和，扰动误差至多
   2 × NOISE_MAX）≥ min_item_attempts + 2 × NOISE_MAX 才算达标（保证真实 ≥ 20，KGE“同一题少于 20 人不展示得分率”），其余只计数。
   达标题目中，答对、答错两格（扰动后）都 ≥ min_cell + NOISE_MAX 才给 2 位小数的正确率，否则只给分档（≥0.90 / ≤0.10 / 0.10–0.90），
   防“全对 / 全错”的属性泄露。题目 ID 与认知层次属公共层，可以列出。
2. 两群检验：达标题目（每节点按作答人数多到少最多取 max_items_per_node 道）按正确率排序，每群至少 min_items_per_group 题，
   取组间平方和占比 R² 最大的二分；p 值 = 参数自助法（标准正态样本、同样取最佳二分；R² 对平移与缩放不变），固定种子；
   全部可检验节点一族做 BH。参与检验的题目里有只给分档的，两群均值、差值、R² 只给 1 位小数（避免由均值反推单题正确率）。
3. 学生层面（spec 之外的补充）：两群题目都作答过的学生按“这一群答对比例 ≥ pass_rate”分过关与否，四格表求 Loevinger H
   （1 − 实际“难的过关、易的不过关”人数 ÷ 独立时的期望人数，即 phi / phi_max，对嵌套结构敏感）。猜对与失误会把 H 整体压低，
   所以再以群内参照归一：每群题目按 ID 奇偶分两半，两半之间的 H 是“同一能力”时的可达水平；H 比值 = 两群之间的 H ÷ 两群群内 H 的均值。
   难易两档（同一能力、嵌套）比值接近或超过 1；两种独立能力比值接近 0——只有比值 ≤ max_h_ratio 的是拆分候选。
   三张表（两群之间、低群两半、高群两半）任一格不足门槛，结论只写“待定”。
4. 加强证据：只失败低正确率一群 / 只失败高正确率一群的学生，错因编码是否明显分两类（取 |phi| 最大的编码，Bonferroni）。
每张表（题目、学生层面、错因证据）都是每名学生只落一格，防泄露机制与第一步同一（扰动、门槛、取整、不含伪名）。
"""
from __future__ import annotations

import random
from functools import lru_cache
from statistics import NormalDist
from typing import Any, Mapping

from . import stats as S
from .calibrate import (BELOW, INSUFFICIENT, NOISE_MAX, UNDECIDED, Table2, n_approx, p_band, pair_numbers, perturb_cells, r1, r2,
                        rate_numbers, set_key)
from .config import Config
from .inputs import MapView
from .responses import Attempts, Errors
from .taxonomy import Taxonomy

SPLIT = "拆分节点候选"
TIERS = "难度分档（不建议拆分）"
SPLIT_CONCLUSIONS = (SPLIT, TIERS, UNDECIDED, BELOW, INSUFFICIENT)
SUPPORT = "错因两群与题目分群对应"
NO_SUPPORT = "未见错因分群"
NO_ERRORS = "未提供错因输入"
BOOTSTRAP_SEED = 20261017
_STD = NormalDist()


def best_split(rates: list[float], min_group: int) -> tuple[int, float] | None:
    """已排序的正确率 → (低群题数 k, R²)；每群至少 min_group 题；全部相等时无定义。同 R² 取较小的 k"""
    m = len(rates)
    if m < 2 * min_group:
        return None
    mean = sum(rates) / m
    sst = sum((r - mean) ** 2 for r in rates)
    if sst <= 0.0:
        return None
    best: tuple[int, float] | None = None
    for k in range(min_group, m - min_group + 1):
        ml = sum(rates[:k]) / k
        mh = sum(rates[k:]) / (m - k)
        ssb = k * (ml - mean) ** 2 + (m - k) * (mh - mean) ** 2
        if best is None or ssb / sst > best[1] + 1e-12:
            best = (k, ssb / sst)
    return best


@lru_cache(maxsize=256)
def null_r2(m: int, min_group: int, draws: int) -> tuple[float, ...]:
    """零假设（单峰：正态）下最佳二分 R² 的自助分布（固定种子，与数据无关）"""
    rng = random.Random(BOOTSTRAP_SEED * 1_000_003 + m * 1009 + min_group)
    out = []
    for _ in range(draws):
        xs = sorted(_STD.inv_cdf(min(1.0 - 1e-12, max(1e-12, rng.random()))) for _ in range(m))
        bs = best_split(xs, min_group)
        out.append(0.0 if bs is None else bs[1])
    return tuple(out)


def bimodality_p(r2_obs: float, m: int, min_group: int, draws: int) -> float:
    null = null_r2(m, min_group, draws)
    return (1 + sum(1 for x in null if x >= r2_obs - 1e-12)) / (draws + 1)


def rate_band(rate: float) -> str:
    """只给分档时的正确率档位（两端各一档，中间一档）"""
    if rate <= 0.10:
        return "≤0.10"
    if rate >= 0.90:
        return "≥0.90"
    return "0.10–0.90"


def item_rates(attempts: Attempts, node: str, cfg: Config, noisy: bool) -> tuple[list[dict[str, Any]], int]:
    """达标的题目（按题目 ID 排序）：{item_id, x, n, exact}（扰动后计数，只在内存里；exact = 两格都够大、可给 2 位小数）；
    以及不达标的题目数。两格都扰动，作答人数的扰动误差至多 2 × NOISE_MAX，门槛相应加两份余量"""
    margin = NOISE_MAX if noisy else 0
    shown, withheld = [], 0
    for item, resp in sorted(attempts.by_node.get(node, {}).items()):
        right = sum(1 for v in resp.values() if v)
        cells = (right, len(resp) - right)
        if noisy:
            cells = perturb_cells(cells, set_key(resp), f"item/{node}/{item}/")
        n = cells[0] + cells[1]
        if n >= cfg.split.min_item_attempts + 2 * margin:
            shown.append({"item_id": item, "x": cells[0], "n": n, "exact": min(cells) >= cfg.min_cell + margin})
        else:
            withheld += 1
    return shown, withheld


def item_record(it: dict[str, Any], attempts: Attempts, z: float) -> dict[str, Any]:
    rec: dict[str, Any] = {"item_id": it["item_id"]}
    if it["item_id"] in attempts.levels:
        rec["cognitive_level"] = attempts.levels[it["item_id"]]
    if it["exact"]:
        rec.update(rate_numbers(it["x"], it["n"], z))
    else:
        rec.update(n_approx=n_approx(it["n"]), rate_band=rate_band(it["x"] / it["n"]))
    return rec


def loevinger_h(t: Table2) -> float | None:
    """两个“过关与否”的 Loevinger H（= phi / phi_max）：过关率低的一方算“难”，Guttman 反向格 = 难的过关、易的不过关；
    H = 1 − 反向格人数 ÷ 独立时反向格的期望人数。四格：a 都过，b 只过前者，c 只过后者，d 都不过"""
    n = t.n
    if n == 0:
        return None
    p1, p2 = (t.a + t.b) / n, (t.a + t.c) / n
    obs, expected = (t.b, n * p1 * (1 - p2)) if p1 <= p2 else (t.c, n * p2 * (1 - p1))
    if expected <= 0.0:
        return None
    return 1.0 - obs / expected


def _passes(attempts: Attempts, node: str, items: list[str], pass_rate: float) -> dict[str, bool]:
    """作答过这些题目中至少一道的学生 → 答对比例是否 ≥ pass_rate"""
    tot: dict[str, list[int]] = {}
    for it in items:
        for pid, ok in attempts.by_node[node][it].items():
            t = tot.setdefault(pid, [0, 0])
            t[0] += ok
            t[1] += 1
    return {pid: r / n >= pass_rate for pid, (r, n) in tot.items()}


def _table(cells: tuple[int, int, int, int], key: int, tag: str, noisy: bool) -> Table2:
    if noisy:
        cells = perturb_cells(cells, key, tag)   # type: ignore[assignment]
    return Table2(*cells, key=key)


def cross_table(lp: Mapping[str, bool], hp: Mapping[str, bool], node: str, noisy: bool,
                tag: str = "cross") -> tuple[Table2, list[str], list[str]]:
    """两组题目都作答过的学生：a = 都过关，b = 只过前者，c = 只过后者，d = 都不过；另返回只失败前者 / 只失败后者的学生。
    tag 区分同一节点的不同表（两群之间、低群内两半、高群内两半）"""
    both = sorted(set(lp) & set(hp))
    a = sum(1 for p in both if lp[p] and hp[p])
    b = sum(1 for p in both if lp[p] and not hp[p])
    c = sum(1 for p in both if not lp[p] and hp[p])
    fail_low = [p for p in both if not lp[p] and hp[p]]
    fail_high = [p for p in both if lp[p] and not hp[p]]
    return _table((a, b, c, len(both) - a - b - c), set_key(both), f"{tag}/{node}/", noisy), fail_low, fail_high


def within_h(attempts: Attempts, node: str, items: list[str], pass_rate: float, tag: str, min_cell: int,
             noisy: bool) -> float | None:
    """一群题目内部的参照 H：按题目 ID 奇偶分成两半，两半过关与否的 Loevinger H（同一能力时的“可达上限”）；四格不足返回 None"""
    t, _, _ = cross_table(_passes(attempts, node, items[0::2], pass_rate), _passes(attempts, node, items[1::2],
                          pass_rate), node, noisy, tag)
    if min(t.cells()) < min_cell + (NOISE_MAX if noisy else 0):
        return None
    return loevinger_h(t)


def error_evidence(errors: Errors | None, tax: Taxonomy | None, subject: str, node: str, fail_low: list[str], fail_high: list[str],
                   cfg: Config, noisy: bool) -> dict[str, Any]:
    if errors is None or tax is None:
        return {"conclusion": NO_ERRORS}
    ep = errors.episodes.get(node, {})
    r1 = [p for p in fail_low if ep.get(p)]
    r2_ = [p for p in fail_high if ep.get(p)]
    key = set_key(r1 + r2_)
    margin = NOISE_MAX if noisy else 0
    best: tuple[float, str, Table2] | None = None
    tested = 0
    for k in tax.subject_codes(subject):
        a = sum(1 for p in r1 if k in ep[p])
        c = sum(1 for p in r2_ if k in ep[p])
        t = _table((a, len(r1) - a, c, len(r2_) - c), key, f"err/{node}/{k}/", noisy)
        if min(t.cells()) < cfg.min_cell + margin:
            continue
        tested += 1
        ph = S.phi(*t.cells())
        if ph is not None and (best is None or abs(ph) > abs(best[0]) + 1e-12):
            best = (ph, k, t)
    if best is None:
        return {"conclusion": INSUFFICIENT, "reason": f"只失败一群的学生按编码分的四格不足门槛 {cfg.min_cell}"}
    ph, k, t = best
    p_bonf = min(1.0, S.chi2_p(ph, t.n) * tested)
    ok = p_bonf <= cfg.split.fdr_q and abs(ph) >= cfg.split.error_min_phi
    return {"conclusion": SUPPORT if ok else NO_SUPPORT, "code": k, "codes_tested": tested, "p_adj_band": p_band(p_bonf),
            "rows": ["只失败低正确率一群", "只失败高正确率一群"], **pair_numbers(*t.cells(), S.z_for(cfg.confidence))}


def scan_splits(mp: MapView, attempts: Attempts, errors: Errors | None, tax: Taxonomy | None, cfg: Config,
                noisy: bool = True) -> list[dict[str, Any]]:
    """每个可计算节点一条（与数据无关的节点清单）"""
    sc = cfg.split
    z = S.z_for(cfg.confidence)
    raws = []
    for v, topic in mp.nodes.items():
        shown, withheld = item_rates(attempts, v, cfg, noisy)
        used = sorted(shown, key=lambda it: (-it["n"], it["item_id"]))[:sc.max_items_per_node]   # 题数上限
        ordered = sorted(used, key=lambda it: (it["x"] / it["n"], it["item_id"]))
        rates = [it["x"] / it["n"] for it in ordered]
        bs = best_split(rates, sc.min_items_per_group)
        raw: dict[str, Any] = {"node": v, "topic": topic, "shown": shown, "withheld": withheld, "bs": bs,
                               "not_tested": len(shown) - len(used), "coarse": any(not it["exact"] for it in used)}
        if bs is not None:
            k, r2_obs = bs
            raw.update(low=sorted(it["item_id"] for it in ordered[:k]), high=sorted(it["item_id"] for it in ordered[k:]),
                       low_mean=sum(rates[:k]) / k, high_mean=sum(rates[k:]) / (len(rates) - k),
                       p=bimodality_p(r2_obs, len(rates), sc.min_items_per_group, sc.bootstrap_draws))
        raws.append(raw)
    tested = [i for i, r in enumerate(raws) if r["bs"] is not None]
    adjusted = dict(zip(tested, S.bh_adjust([raws[i]["p"] for i in tested])))
    margin = NOISE_MAX if noisy else 0
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raws):
        rec: dict[str, Any] = {"node": r["node"], "topic": r["topic"], "items_withheld": r["withheld"],
                               "items": [item_record(it, attempts, z) for it in r["shown"]]}
        if r["not_tested"]:
            rec["items_not_tested"] = r["not_tested"]
        if i not in adjusted:
            rec.update(conclusion=INSUFFICIENT, tested=False,
                       reason=f"达标题目不足 2 × {sc.min_items_per_group} 道（或正确率全部相同）")
            out.append(rec)
            continue
        p_adj, gap = adjusted[i], r["high_mean"] - r["low_mean"]
        rnd = r1 if r["coarse"] else r2                  # 有只给分档的题目：均值、差值、R² 只给 1 位
        lp = _passes(attempts, r["node"], r["low"], sc.pass_rate)
        hp = _passes(attempts, r["node"], r["high"], sc.pass_rate)
        t, fail_low, fail_high = cross_table(lp, hp, r["node"], noisy)
        cross_ok = min(t.cells()) >= cfg.min_cell + margin
        cross: dict[str, Any] = {"disclosed": cross_ok, "rows": "低正确率一群过关与否", "cols": "高正确率一群过关与否"}
        h = loevinger_h(t) if cross_ok else None
        wl = within_h(attempts, r["node"], r["low"], sc.pass_rate, "within-low", cfg.min_cell, noisy)
        wh = within_h(attempts, r["node"], r["high"], sc.pass_rate, "within-high", cfg.min_cell, noisy)
        ref = None if wl is None or wh is None else (wl + wh) / 2
        ratio = None if h is None or ref is None or ref <= 0.0 else h / ref
        if cross_ok:
            cross.update(pair_numbers(*t.cells(), z))
            cross["loevinger_h"] = None if h is None else r2(h)
        cross["within_h"] = [None if wl is None else r2(wl), None if wh is None else r2(wh)]
        if ratio is not None:
            cross["h_ratio"] = r2(ratio)
        if not (p_adj <= sc.fdr_q and gap >= sc.min_gap):
            conclusion, reason = BELOW, "题目正确率未显著分成两群，或两群之差未达阈值"
        elif ratio is None:
            conclusion, reason = UNDECIDED, (f"题目正确率分成两群，但学生层面四格（两群之间或群内两半）有一格不足门槛 {cfg.min_cell}，"
                                             "或群内两半本身不一致，无法区分两种能力与难易两档")
        elif ratio > sc.max_h_ratio:
            conclusion, reason = TIERS, ("题目正确率分成两群，但两群之间的嵌套程度（Loevinger H）与群内两半相当：更像同一能力的难易两档"
                                         "（猜对与失误会压低 H，所以与群内参照相比）")
        else:
            conclusion, reason = SPLIT, ("题目正确率显著分成两群、两群之差达到阈值，且两群之间的 Loevinger H 远低于群内两半："
                                         "两群题目考的不是同一能力")
        rec.update(conclusion=conclusion, reason=reason, tested=True, low_group=r["low"], high_group=r["high"],
                   low_mean=rnd(r["low_mean"]), high_mean=rnd(r["high_mean"]), gap=rnd(gap), r2=rnd(r["bs"][1]),
                   p_adj_band=p_band(p_adj), cross_group=cross,
                   error_evidence=error_evidence(errors, tax, mp.subject, r["node"], fail_low, fail_high, cfg, noisy))
        out.append(rec)
    return out
