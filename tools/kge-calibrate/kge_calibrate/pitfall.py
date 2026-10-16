"""第二步·易错候选与错因难归类信号（纯函数，只产出聚合数；spec-step2 范围 2）。

单位是学生：一名学生在一个节点上的全部当前错因行合为一条“错因记录”（高置信编码集合；集合为空 = 只有低置信行）。
易错候选：每个可计算节点 v × 该学科错因分类表的每个编码 k 一张四格表——
  行：v 上有高置信错因记录的学生 / 本主题其他节点有、v 上没有的学生（基线）；列：记录里有无 k。
  基线学生每人只取一个节点的记录（按伪名与节点的哈希取最小者，与 v 无关），保证每人在每张表里只落一格（第一步表键扰动的前提）。
难归类：每个可计算节点上“只有低置信错因”的学生占比（两格表）。
防泄露与第一步同一机制：每张表按表键（表内学生记录键之和）与表标签加 −2…+2 的确定性扰动，人数向下取整到 10，比率与区间 2 位，
p 值分档，不含伪名；节点 × 编码的检验只列出可给出的，其余只计数。同一节点的各编码表共用行人数扰动（perturb_pitfall），
“无该编码”两格由两份扰动相减得到，门槛相应加两份余量。只检验分类表里状态“明确”的编码（待定、敏感编码在读入时已丢弃）。
不自动生成新错因编码或易错点文本（spec“不做”）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from . import stats as S
from .calibrate import BELOW, INSUFFICIENT, NOISE_MAX, noise, p_band, perturb_cells, rate_numbers, set_key, two_group_numbers
from .config import Config
from .inputs import MapView
from .responses import Errors
from .taxonomy import Taxonomy

NEW_PITFALL = "新增易错点候选"
COVERED = "已有易错点覆盖"
MAPPING_MISSING = "易错点对照缺失"
UNCLASSIFIABLE = "错因难归类信号"
PITFALL_CONCLUSIONS = (NEW_PITFALL, COVERED, MAPPING_MISSING, BELOW)
LOW_CONCLUSIONS = (UNCLASSIFIABLE, BELOW, INSUFFICIENT)


def baseline_pick(pid: str, nodes: list[str]) -> str:
    """基线学生取哪个节点的记录：伪名与节点的哈希最小者（确定性、与被检验的节点无关）"""
    return min(nodes, key=lambda n: hashlib.sha256(f"kge-calibrate/baseline\x00{pid}\x00{n}".encode("utf-8")).digest())


def _by_topic(mp: MapView) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for nid, topic in mp.nodes.items():
        out.setdefault(topic, []).append(nid)
    return {t: sorted(v) for t, v in sorted(out.items())}


def pitfall_tables(mp: MapView, errors: Errors, codes: tuple[str, ...]) -> list[dict[str, Any]]:
    """每个可计算节点 × 每个编码的真实四格 (a, b, c, d) 与表键（只在内存里）：a = v 上有 k，b = v 上无 k，c = 基线有 k，d = 基线无 k"""
    out: list[dict[str, Any]] = []
    for topic, ids in _by_topic(mp).items():
        high = {n: {p: c for p, c in errors.episodes.get(n, {}).items() if c} for n in ids}
        where: dict[str, list[str]] = {}
        for n in ids:
            for p in high[n]:
                where.setdefault(p, []).append(n)
        pick = {p: baseline_pick(p, ns) for p, ns in where.items()}
        for v in ids:
            hv = high[v]
            base = {p: high[w][p] for p, w in pick.items() if p not in hv}
            key = set_key(list(hv) + list(base))
            for k in codes:
                a = sum(1 for c in hv.values() if k in c)
                c_ = sum(1 for c in base.values() if k in c)
                out.append({"node": v, "topic": topic, "code": k, "cells": (a, len(hv) - a, c_, len(base) - c_), "key": key})
    return out


def _coverage(mp: MapView, pitfall_codes: Mapping[str, frozenset[str]] | None, node: str, code: str) -> tuple[list[str], list[str]]:
    """(覆盖这一编码的关联易错点, 关联了但对照里没有的易错点)"""
    linked = mp.pitfalls_of.get(node, ())
    codes = pitfall_codes or {}
    return sorted(p for p in linked if code in codes.get(p, ())), sorted(p for p in linked if p not in codes)


def _conclusion(d: float, lo: float, share: float, p_adj: float, covering: list[str], unmapped: list[str],
                cfg: Config) -> tuple[str, str]:
    pc = cfg.pitfall
    if lo > 0.0 and p_adj <= pc.fdr_q and share >= pc.min_share and d >= pc.min_lift:
        if covering:
            return COVERED, "该编码在本节点显著偏高，已有关联易错点覆盖"
        if unmapped:
            return MAPPING_MISSING, "该编码在本节点显著偏高；本节点有关联易错点，但易错点编码对照里没有它们，无法判断是否已覆盖"
        return NEW_PITFALL, "该编码在本节点显著偏高（相对本主题基线），且没有关联的易错点覆盖这一编码"
    return BELOW, "未显著偏高，或占比、差值未达阈值"


def perturb_pitfall(cells: tuple[int, int, int, int], key: int, node: str, code: str) -> tuple[int, int, int, int]:
    """同一节点的各编码表共用同一份行人数扰动（种子只带节点，不带编码）：多张表平均也还原不出行人数；
    “有该编码”两格按编码各自扰动，“无该编码”两格 = 扰动后的行人数 − 扰动后的“有”格（误差至多 2 × NOISE_MAX）"""
    a, b, c, d = cells
    n1 = max(0, a + b + noise(key, f"pitfall/{node}/n1"))
    n2 = max(0, c + d + noise(key, f"pitfall/{node}/n2"))
    a2 = min(n1, max(0, a + noise(key, f"pitfall/{node}/{code}/a")))
    c2 = min(n2, max(0, c + noise(key, f"pitfall/{node}/{code}/c")))
    return a2, n1 - a2, c2, n2 - c2


def pitfall_disclosable(cells: tuple[int, int, int, int], min_cell: int, noisy: bool) -> bool:
    """“有”两格加一份扰动余量，“无”两格（两份扰动之差）加两份，保证真实每格 ≥ min_cell"""
    m = NOISE_MAX if noisy else 0
    a, b, c, d = cells
    return min(a, c) >= min_cell + m and min(b, d) >= min_cell + 2 * m


def scan_pitfalls(mp: MapView, errors: Errors, tax: Taxonomy, pitfall_codes: Mapping[str, frozenset[str]] | None,
                  cfg: Config, noisy: bool = True) -> tuple[list[dict[str, Any]], int]:
    """返回 (可给出的节点 × 编码记录，按节点、编码排序；样本不足的检验数)。只检验分类表里状态“明确”的编码"""
    codes = tax.subject_codes(mp.subject)
    raws = []
    for t in pitfall_tables(mp, errors, codes):
        cells = perturb_pitfall(t["cells"], t["key"], t["node"], t["code"]) if noisy else t["cells"]
        raws.append({**t, "cells": cells, "ok": pitfall_disclosable(cells, cfg.min_cell, noisy)})
    eligible = [r for r in raws if r["ok"]]
    pvals = [S.two_proportion_p(r["cells"][0], r["cells"][0] + r["cells"][1], r["cells"][2], r["cells"][2] + r["cells"][3])
             for r in eligible]
    z = S.z_for(cfg.confidence)
    out: list[dict[str, Any]] = []
    for r, p_adj in zip(eligible, S.bh_adjust(pvals)):
        a, b, c, d = r["cells"]
        diff, lo, _hi = S.newcombe(a, a + b, c, c + d, z)
        covering, unmapped = _coverage(mp, pitfall_codes, r["node"], r["code"])
        conclusion, reason = _conclusion(diff, lo, a / (a + b), p_adj, covering, unmapped, cfg)
        out.append({"node": r["node"], "topic": r["topic"], "code": r["code"],
                    "cross_subject_category": tax.codes[mp.subject][r["code"]],
                    "covering_pitfalls": covering, "unmapped_pitfalls": unmapped, "conclusion": conclusion, "reason": reason,
                    "disclosed": True, "p_adj_band": p_band(p_adj),
                    **two_group_numbers(a, a + b, c, c + d, z, ("node_group", "baseline"), "share")})
    return out, len(raws) - len(eligible)


def scan_unclassifiable(mp: MapView, errors: Errors, cfg: Config, noisy: bool = True) -> list[dict[str, Any]]:
    """每个可计算节点一条（与数据无关的节点清单）：只有低置信错因的学生占比"""
    margin = NOISE_MAX if noisy else 0
    raws = []
    for v, topic in mp.nodes.items():
        ep = errors.episodes.get(v, {})
        low = sum(1 for c in ep.values() if not c)
        cells = (low, len(ep) - low)
        if noisy:
            cells = perturb_cells(cells, set_key(ep), f"low/{v}/")
        raws.append({"node": v, "topic": topic, "cells": cells, "ok": min(cells) >= cfg.min_cell + margin})
    m = sum(1 for r in raws if r["ok"])
    z = S.z_for(cfg.confidence)
    z_adj = S.z_for(1.0 - (1.0 - cfg.confidence) / max(1, m))   # 按族校正的置信水平
    out: list[dict[str, Any]] = []
    for r in raws:
        rec: dict[str, Any] = {"node": r["node"], "topic": r["topic"]}
        if not r["ok"]:
            rec.update(conclusion=INSUFFICIENT, reason=f"难归类或其余学生人数不足门槛 {cfg.min_cell}", disclosed=False)
            out.append(rec)
            continue
        low, rest = r["cells"]
        lo_adj = S.wilson(low, low + rest, z_adj)[0]
        hit = lo_adj > cfg.pitfall.low_conf_share
        rec.update(conclusion=UNCLASSIFIABLE if hit else BELOW,
                   reason=("只有低置信错因的学生占比（按族校正的区间下界）高于阈值：错因难归类，请教研查看（不自动提议新编码）" if hit
                           else "难归类占比未确定地高于阈值"),
                   disclosed=True, **rate_numbers(low, low + rest, z, "low_conf_rate"))
        out.append(rec)
    return out
