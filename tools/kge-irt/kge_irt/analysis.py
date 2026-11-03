"""分析（只在内存里，精确值）：按节点分块拟合 1PL / 2PL / “先验难度 + 能力”，2PL 自助法，“只用先验难度”（按节点截距）的逻辑回归，
BKT 基线与它的 Platt 校准，以及后段每次作答在六种方法下的预测概率。报表（report.py）只从这里取聚合、经取整与表键扰动后才输出；
学生后验与伪名不出本模块的调用方。

时间切分：拟合只用 segment = fit 的作答；评估只用 segment = eval 的作答。每名学生每道题只有一行（独立作答的第一次），所以后段只含
该生在拟合段没答过的题。评估只用拟合段有作答、所在块已拟合、且有先验难度的题（六种方法同一批作答；没有先验难度的题从两种先验对照中
剔除，为保持配对，也不进其余方法的评估，报表给出题数）。
分组：全部学生，以及“有拟合数据”（拟合段在该块有作答）与“冷启动”（没有）两组分开报告，各自过人数门槛。
块内学生（扰动后）少于 fit.min_block_students + 2 的块不拟合（报表只写“样本不足”）。
BKT 的 Platt 校准：基线概率的 logit 上做逻辑回归；为不用到被评估学生自己的答案，按记录键把后段学生分两半交叉拟合（用另一半学生
的后段作答拟合、预测这一半）——拟合段没有切分点前的逐次掌握概率，不能在拟合段上校准（与审查建议的偏差，README 写明）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kge_calibrate.calibrate import NOISE_MAX, noise, record_key, set_key

from . import irt as M
from .config import BaselineRef, Config, require_signed_baseline
from .inputs import Baseline, ItemsView, Responses
from .quad import normal_quadrature

METHODS = ("bkt", "bkt_platt", "prior_only", "prior_ability", "irt_1pl", "irt_2pl")
METHOD_NAMES = {"bkt": "现行掌握估计（BKT 基线，未校准）", "bkt_platt": "BKT 基线 + Platt 校准", "prior_only": "只用先验难度（按节点截距）",
                "prior_ability": "先验难度 + 能力", "irt_1pl": "IRT 1PL", "irt_2pl": "IRT 2PL"}
KIND_OF = {"prior_ability": "prior", "irt_1pl": "1pl", "irt_2pl": "2pl"}
ALL = "全部"
ANY, WARM, COLD = "全部学生", "有拟合数据", "冷启动"


@dataclass(frozen=True)
class BlockResult:
    node: str
    data: M.BlockData
    fitted: bool
    fits: Mapping[str, M.Fit]                                  # 1pl / 2pl / prior
    params: Mapping[str, list[tuple[float, float]]]             # 各题 (a, b)
    boot: list[list[tuple[float, float]]] | None                # 2PL 自助法每次的各题 (a, b)


@dataclass(frozen=True)
class Analysis:
    blocks: tuple[BlockResult, ...]
    prior_coef: tuple[Mapping[str, float], float]
    rows: Mapping[tuple[str, str], list[tuple[str, bool, dict[str, float]]]]   # (范围, 学生组) → (伪名, 是否答对, 方法 → 预测)
    all_students: frozenset[str]
    no_prior_items: int                                           # 已拟合块里没有先验难度、不进评估的题数（公共层）


def bkt_predict(p_mastery: float, ref: BaselineRef, item_type: str | None = None) -> float:
    """现行掌握估计的作答预测（与 BKT 观测模型同一口径，p_guess 按题型）：p (1 − slip) + (1 − p) guess"""
    return p_mastery * (1.0 - ref.p_slip) + (1.0 - p_mastery) * ref.p_guess(item_type)


def block_students_noisy(bd: M.BlockData) -> int:
    """块内学生数加表键扰动（−2…+2；表键 = 块内学生集合，同 KGE-09）：是否拟合与报表人数都只看它，删一人跨门槛也定不了格"""
    return max(0, len(bd.students) + noise(set_key(bd.students), f"irt/block/{bd.node}/n"))


def block_enough(bd: M.BlockData, cfg: Config) -> bool:
    return block_students_noisy(bd) >= cfg.fit.min_block_students + NOISE_MAX


def fit_block(node: str, iv: ItemsView, resp: Responses, cfg: Config, draws: int) -> BlockResult:
    quad = normal_quadrature(cfg.fit.quadrature_points)
    fit_rows = resp.fit.get(node, {})
    used = sorted({i for r in fit_rows.values() for i, _ in r})
    bd = M.block_data(node, used, [iv.items[i].prior for i in used], fit_rows)
    if not block_enough(bd, cfg) or not used:
        return BlockResult(node, bd, False, {}, {}, None)
    fits, params = {}, {}
    for kind in ("1pl", "2pl", "prior"):
        st = M.structure(kind, bd.priors, cfg.fit)
        f = M.fit_em(bd, st, quad, cfg.fit)
        fits[kind] = f
        params[kind] = M.item_params(st, f.phi)
    st2 = M.structure("2pl", bd.priors, cfg.fit)
    boot = M.bootstrap(bd, st2, fits["2pl"], quad, cfg.fit, draws, cfg.bootstrap_seed, cfg.bootstrap_max_iterations) if draws else None
    return BlockResult(node, bd, True, fits, params, boot)


def groups(nodes: list[str]) -> list[tuple[str, str]]:
    return [(ALL, ANY), (ALL, WARM), (ALL, COLD)] + [(n, s) for n in nodes for s in (WARM, COLD)]


def analyze(iv: ItemsView, resp: Responses, base: Baseline, cfg: Config, draws: int | None = None) -> Analysis:
    require_signed_baseline(cfg, resp.synthetic and iv.synthetic and base.synthetic)
    quad = normal_quadrature(cfg.fit.quadrature_points)
    theta, w = quad
    ref = cfg.bkt
    n_draws = cfg.bootstrap_draws if draws is None else draws
    blocks = tuple(fit_block(node, iv, resp, cfg, n_draws) for node in sorted(iv.nodes))
    prior_rows = [(b.node, p, x, n) for b in blocks if b.fitted for (x, n), p in zip(b.data.item_counts(), b.data.priors)
                  if p is not None and n]
    coef = M.fit_prior_by_node(prior_rows) if prior_rows else ({}, 0.0)
    rows: dict[tuple[str, str], list[tuple[str, bool, dict[str, float]]]] = {g: [] for g in groups(sorted(iv.nodes))}
    no_prior = 0
    for b in blocks:
        if not b.fitted:
            continue
        no_prior += sum(1 for p in b.data.priors if p is None)
        index = {it: j for j, it in enumerate(b.data.items)}
        posts = {kind: M.posteriors(b.data, M.structure(kind, b.data.priors, cfg.fit), b.fits[kind], quad) for kind in b.fits}
        for pid, rs in resp.eval.get(b.node, {}).items():
            warm = pid in posts["2pl"]
            for item, y in rs:
                j = index.get(item)
                if j is None or b.data.priors[j] is None:
                    continue                       # 拟合段没人答过的题、没有先验难度的题：六种方法都不评估
                it = iv.items[item]
                pm = base.p_mastery.get((b.node, it.cognitive_level), {})
                pred = {"bkt": bkt_predict(pm.get(pid, ref.p_init), ref, it.item_type),
                        "prior_only": M.sigmoid(coef[0].get(b.node, 0.0) + coef[1] * (it.prior - 0.5))}
                for method, kind in KIND_OF.items():
                    a, bb = b.params[kind][j]
                    pred[method] = M.predictive(posts[kind].get(pid, w), a, bb, theta)
                row = (pid, y, pred)
                for g in ((ALL, ANY), (ALL, WARM if warm else COLD), (b.node, WARM if warm else COLD)):
                    rows[g].append(row)
    platt(rows[(ALL, ANY)], cfg)
    return Analysis(blocks, coef, rows, resp.pseudonyms, no_prior)


def fold_of(pid: str) -> int:
    return record_key(pid) % 2


def platt(rows: list[tuple[str, bool, dict[str, float]]], cfg: Config) -> None:
    """BKT 基线的 Platt 校准（两折交叉拟合，按记录键分折）：写入每行的 pred["bkt_platt"]"""
    eps = cfg.prob_clip
    for f in (0, 1):
        train = [((1.0, M.logit(pred["bkt"], eps)), float(y), 1.0) for pid, y, pred in rows if fold_of(pid) != f]
        beta = M.fit_logistic(train) if train else [0.0, 1.0]
        for pid, _, pred in rows:
            if fold_of(pid) == f:
                pred["bkt_platt"] = M.sigmoid(beta[0] + beta[1] * M.logit(pred["bkt"], eps))
