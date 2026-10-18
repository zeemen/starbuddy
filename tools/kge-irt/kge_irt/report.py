"""报表：irt-report.json（题目级、节点级聚合）与 irt-report.md（首段“合成，不是试点结论”）。

**不是真相源**：输出只是评估报告（JSON 顶层 not_a_source_of_truth = true），不得写入 kge.item_tag 或 platform.*，不得被知识服务、
适配器、审核台导入；“先验难度疑似标错”只是候选，仅供教研参考——修改先验难度走 KGE-02 打标流程，不得照抄或换算 IRT 值。

防泄露（规则 13；沿用 KGE-09 的表键扰动与取整，PLT-17 的“每生一个点”与粒度教训；扰动、取整、出口检查与写出函数直接复用
kge_calibrate，不另立口径）：
- 题目：表键 = 拟合段作答过该题的学生集合；答对、答错两格各加 −2…+2 扰动；扰动后人数 ≥ min_respondents + 4（真实 ≥ 20）
  且两格都 ≥ min_outcome_cell + 2 才给参数，否则“样本不足”加可识别性告警；人数向下取整到 10。
- 参数与区间端点：四舍五入到格点再按表键加 −2…+2 格；1PL 与 2PL 难度各用独立扰动（共用会精确公开两者之差，见 README）。难度格点按单人影响上界逐题加粗
  （difficulty_grid：作答人数少、区分度低的题更粗），只由公开值与扰动后计数决定。
- 评估指标：每生一个点；组内学生（扰动后）≥ min_eval_students + 2 才给；粒度 = 取值范围 ÷ 扰动后人数（向上取到格点），再加 −2…+2 格；
  AUC 的正例占比门槛也只看扰动后的两类按人加权总量（留扰动余量）。
- 对照判定：每生配对差 + 配对自助法区间（固定种子），差值与区间端点各只加一次扰动；判定只由公开值算出。
- 秩相关、先验难度疑似标错只由公开值算出（不再接触精确值）。
- 不输出学生能力、后验、伪名，也不输出作答次数与实际拟合比例；出口检查：渲染后的文本里出现任何输入伪名即拒绝写出。
写出：目标目录必须不存在或为空；每个文件 O_CREAT | O_EXCL | O_NOFOLLOW、权限 0600 新建（kge_calibrate.report.write_files）。
同一输入逐字节一致：键排序、数值按格点保留位数、列表按 ID 排序、不写时间与路径。
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterable

from kge_calibrate.calibrate import INSUFFICIENT, NOISE_MAX, n_approx, noise, perturb_cells, r1, r2, set_key
from kge_calibrate.report import FILE_MODE, OutputRefused, assert_no_pseudonyms, check_out_dir, render_json, write_files

from . import GENERATOR, SYNTHETIC_NOTICE
from . import irt as M
from . import metrics as X
from .analysis import ALL, ANY, METHOD_NAMES, METHODS, Analysis, BlockResult, block_students_noisy
from .config import Config
from .inputs import ItemsView, Responses

SCHEMA_VERSION = "kge-irt-report/0.2"
REPORT_NAME = "irt-report.json"
MD_NAME = "irt-report.md"
OUTPUT_NAMES = (REPORT_NAME, MD_NAME)
STATUS = "评估报告：不替换先验难度、不改掌握计算、不写库；是否试点替换先验难度另起必停任务、由人决定"
USAGE = ("本报表不是真相源：不得写入 kge.item_tag 或 platform.*，不得被知识服务、适配器、审核台导入；"
         "修改先验难度走 KGE-02 打标流程，不得照抄或换算本报表的 IRT 值，按候选修改先验另起 [NEEDS-HUMAN] 任务")
GRIDS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1)
DIFFICULTY_MULTIPLES = (1, 2, 5, 10)
AUC_RANGE = 4.0          # 按人加权的 AUC：单人影响 ≤ 1 / (人数 × min(正例占比, 负例占比))；占比 ≥ AUC_MIN_SHARE 时 ≤ 4 / 人数
AUC_MIN_SHARE = 0.25
METRICS = ("log_loss", "brier", "auc")
PAIRS = tuple((irt, other) for irt in ("irt_1pl", "irt_2pl") for other in ("bkt", "bkt_platt", "prior_only", "prior_ability")) + (
    ("irt_2pl", "irt_1pl"),)
W_FEW = "作答人数不足"
W_EXTREME = "接近全对或全错（答对或答错一格不足门槛）"
W_A_BOUND = "区分度接近参数界"
W_B_BOUND = "难度接近参数界"
W_NOT_FITTED = "块内学生不足，不拟合"
W_NOT_CONVERGED = "未收敛（到最大迭代数）"
BETTER, WORSE, SIMILAR = "IRT 更好", "IRT 更差", "差别不大"
MISMATCH = "先验难度疑似标错（候选，仅供教研参考）"
BOOT_NOTE = "自助法区间可能偏窄：次数与每次轮数有限（见配置 bootstrap.draws / max_iterations），且不含模型误设的不确定性"
BKT_NOTE = ("未校准的 BKT 基线把掌握概率经观测模型直接当作答对概率，没有按题目难度调整，校准偏差会抬高它的对数损失；"
            "Platt 校准只修正整体偏差，仍与题目无关")
__all__ = ["FILE_MODE", "OUTPUT_NAMES", "OutputRefused", "build_report", "check_out_dir", "render", "run", "write_files"]


def decimals(step: float) -> int:
    s = f"{step:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def coarse(v: float, step: float, key: int, slot: str, noisy: bool = True) -> float:
    """四舍五入到格点，再按表键与格位加 −2…+2 格（noisy=False 只给测试核对口径）"""
    k = round(v / step) + (noise(key, slot) if noisy else 0)
    out = round(k * step, decimals(step))
    return 0.0 if out == 0 else out


def grid_for(spread: float, n: int) -> float:
    """指标粒度：取值范围 ÷ 人数，向上取到格点（最粗 0.1）"""
    need = spread / max(1, n)
    for g in GRIDS:
        if g >= need - 1e-12:
            return g
    return GRIDS[-1]


def difficulty_grid(n: int, x: int, a: float, cfg: Config) -> float:
    """难度格点：单人影响上界 ≈ 1 / (a n p (1 − p))，取 difficulty_step × {1, 2, 5, 10} 中第一个 ≥ 2 × 影响的值（只用公开值与扰动后计数）"""
    p = min(max(x / n, 1.0 / n), 1.0 - 1.0 / n)
    influence = 1.0 / (max(a, cfg.fit.a_lo) * n * p * (1.0 - p))
    for m in DIFFICULTY_MULTIPLES:
        g = round(cfg.difficulty_step * m, 6)
        if g >= 2.0 * influence - 1e-12:
            return g
    return round(cfg.difficulty_step * DIFFICULTY_MULTIPLES[-1], 6)


# ---------- 题目与块 ----------
def item_cells(b: BlockResult, j: int, noisy: bool) -> tuple[tuple[int, int], int]:
    """(扰动后的答对、答错两格, 表键)"""
    x, n = b.data.item_counts()[j]
    key = set_key(b.data.respondents(j))
    cells = (x, n - x)
    if noisy:
        cells = perturb_cells(cells, key, f"irt/item/{b.node}/{b.data.items[j]}/")   # type: ignore[assignment]
    return cells, key


def _bound_warnings(rec: dict[str, Any], cfg: Config) -> list[str]:
    out = []
    fc, nb = cfg.fit, cfg.near_bound_steps
    a = rec["2pl"]["discrimination"]
    if a <= fc.a_lo + nb * cfg.discrimination_step + 1e-9 or a >= fc.a_hi - nb * cfg.discrimination_step - 1e-9:
        out.append(W_A_BOUND)
    step = rec["difficulty_step"]
    for kind in ("1pl", "2pl"):
        b = rec[kind]["difficulty"]
        if b <= fc.b_lo + nb * step + 1e-9 or b >= fc.b_hi - nb * step - 1e-9:
            out.append(W_B_BOUND)
            break
    return out


def item_record(b: BlockResult, j: int | None, item_id: str, iv: ItemsView, cfg: Config, noisy: bool,
                a_common: float | None = None) -> dict[str, Any]:
    it = iv.items[item_id]
    rec: dict[str, Any] = {"item_id": item_id, "cognitive_level": it.cognitive_level, "item_type": it.item_type,
                           "prior_difficulty": it.prior, "family_code": it.family_code}
    if j is None:                                  # 拟合段没人答过
        rec.update(n_approx=INSUFFICIENT, disclosed=False, warnings=[W_FEW])
        return rec
    cells, key = item_cells(b, j, noisy)
    margin = NOISE_MAX if noisy else 0
    n = cells[0] + cells[1]
    enough = n >= cfg.min_respondents + 2 * margin
    outcome = min(cells) >= cfg.min_outcome_cell + margin
    warnings = []
    if not enough:
        warnings.append(W_FEW)
    elif not outcome:
        warnings.append(W_EXTREME)
    rec["n_approx"] = n_approx(n) if enough else INSUFFICIENT
    disclosed = b.fitted and enough and outcome
    rec["disclosed"] = disclosed
    if disclosed:
        as_ = cfg.discrimination_step
        tag = f"irt/{b.node}/{item_id}/"
        a2, b2 = b.params["2pl"][j]
        a2_pub = coarse(a2, as_, key, tag + "2pl/a", noisy)
        a_ref = min(a2_pub, a_common if a_common is not None else a2_pub)
        ds = difficulty_grid(n, cells[0], a_ref, cfg)
        rec["difficulty_step"] = ds
        # 1PL 与 2PL 难度各用独立扰动：共用一个扰动会让两者之差（取整后）精确公开，强攻击者据此定格（实测见 README / lessons）
        rec["1pl"] = {"difficulty": coarse(b.params["1pl"][j][1], ds, key, tag + "1pl/b", noisy)}
        two: dict[str, Any] = {"difficulty": coarse(b2, ds, key, tag + "2pl/b", noisy), "discrimination": a2_pub}
        if b.boot:
            lo_q, hi_q = (1.0 - cfg.bootstrap_confidence) / 2.0, (1.0 + cfg.bootstrap_confidence) / 2.0
            for name, idx, step in (("difficulty", 1, ds), ("discrimination", 0, as_)):
                xs = [d[j][idx] for d in b.boot]
                lo = coarse(M.percentile(xs, lo_q), step, key, f"{tag}2pl/{name}/lo", noisy)
                hi = coarse(M.percentile(xs, hi_q), step, key, f"{tag}2pl/{name}/hi", noisy)
                two[f"{name}_ci"] = [min(lo, two[name]), max(hi, two[name])]   # 只对公开值排序，不再接触精确值
        rec["2pl"] = two
        warnings += _bound_warnings(rec, cfg)
    rec["warnings"] = warnings
    return rec


def block_record(b: BlockResult, iv: ItemsView, cfg: Config, noisy: bool) -> dict[str, Any]:
    n = block_students_noisy(b.data) if noisy else len(b.data.students)
    margin = NOISE_MAX if noisy else 0
    enough = n >= cfg.min_respondents + margin
    rec: dict[str, Any] = {"node": b.node, "fitted": b.fitted, "students_approx": n_approx(n) if enough else INSUFFICIENT}
    index = {it: j for j, it in enumerate(b.data.items)}
    ids = sorted(i for i, it in iv.items.items() if it.node_id == b.node)
    a_common = None
    warnings = []
    if not b.fitted:
        warnings.append(W_NOT_FITTED)
    else:
        key = set_key(b.data.students)
        a_common = coarse(b.params["1pl"][0][0] if b.params["1pl"] else 1.0, cfg.discrimination_step, key, f"irt/{b.node}/1pl/a", noisy)
        rec["models"] = {"1pl": {"converged": b.fits["1pl"].converged, "common_discrimination": a_common},
                         "2pl": {"converged": b.fits["2pl"].converged},
                         "prior_ability": {"converged": b.fits["prior"].converged}}
        if enough:                                 # 未收敛告警只在扰动后块人数达标时给出
            for kind, label in (("1pl", "1PL"), ("2pl", "2PL"), ("prior", "先验难度 + 能力")):
                if not b.fits[kind].converged:
                    warnings.append(f"{label} {W_NOT_CONVERGED}")
    rec["items"] = [item_record(b, index.get(i), i, iv, cfg, noisy, a_common) for i in ids]
    rec["warnings"] = warnings
    return rec


# ---------- 评估 ----------
def _resamples(students: list[str], scope: str, subset: str, cfg: Config) -> list[list[int]]:
    rng = random.Random(f"kge-irt/compare/{cfg.compare_seed}/{scope}/{subset}")
    n = len(students)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(cfg.compare_draws)]


def eval_record(scope: str, subset: str, rows: list[tuple[str, bool, dict[str, float]]], cfg: Config, noisy: bool) -> dict[str, Any]:
    students = sorted({pid for pid, _, _ in rows})
    key = set_key(students)
    slot = f"irt/eval/{scope}/{subset}/"
    n = max(0, len(students) + (noise(key, slot + "n") if noisy else 0))
    margin = NOISE_MAX if noisy else 0
    rec: dict[str, Any] = {"group": scope, "student_group": subset}
    if n < cfg.min_eval_students + margin:
        rec.update(students_approx=INSUFFICIENT, disclosed=False)
        return rec
    rec.update(students_approx=n_approx(n), disclosed=True)
    ll_spread = -math.log(cfg.prob_clip)
    spread = {"log_loss": ll_spread, "brier": 1.0, "auc": AUC_RANGE}
    steps = {m: grid_for(spread[m], n) for m in METRICS}
    steps["paired_difference"] = grid_for(2.0 * ll_spread, n)
    rec["steps"] = steps
    # AUC：两类按人加权总量各加扰动，较小一类 ≥ 0.25 × 两类之和 + 余量才给（门槛不看精确值）
    k: dict[str, int] = {}
    for pid, _, _ in rows:
        k[pid] = k.get(pid, 0) + 1
    w_pos = sum(1.0 / k[pid] for pid, y, _ in rows if y)
    w_neg = sum(1.0 / k[pid] for pid, y, _ in rows if not y)
    if noisy:
        w_pos += noise(key, slot + "auc/pos")
        w_neg += noise(key, slot + "auc/neg")
    auc_ok = min(w_pos, w_neg) >= AUC_MIN_SHARE * (w_pos + w_neg) + margin
    out: dict[str, Any] = {}
    points = {}
    for method in METHODS:
        sub = [(pid, y, pred[method]) for pid, y, pred in rows]
        pts = X.student_points(sub, cfg.prob_clip)
        points[method] = pts
        auc = X.weighted_auc(X.per_student_auc_rows(sub)[0]) if auc_ok else None
        out[method] = {"log_loss": coarse(X.student_mean(pts, 0), steps["log_loss"], key, slot + method + "/log_loss", noisy),
                       "brier": coarse(X.student_mean(pts, 1), steps["brier"], key, slot + method + "/brier", noisy),
                       "auc": INSUFFICIENT if auc is None else coarse(auc, steps["auc"], key, slot + method + "/auc", noisy)}
    rec["metrics"] = out
    rec["comparisons"] = comparisons(scope, subset, students, points, key, steps["paired_difference"], cfg, noisy)
    return rec


def comparisons(scope: str, subset: str, students: list[str], points: dict[str, dict[str, tuple[float, float, int]]], key: int,
                step: float, cfg: Config, noisy: bool) -> list[dict[str, Any]]:
    """每生配对差 d = 对照 − IRT（该生后段平均对数损失；正 = IRT 更好）：均值与配对自助法区间各只加一次扰动；判定只看公开值"""
    lo_q, hi_q = (1.0 - cfg.bootstrap_confidence) / 2.0, (1.0 + cfg.bootstrap_confidence) / 2.0
    res = _resamples(students, scope, subset, cfg)
    out = []
    for irt, other in PAIRS:
        d = [points[other][s][0] - points[irt][s][0] for s in students]
        mean = sum(d) / len(d)
        boots = [sum(d[i] for i in idx) / len(idx) for idx in res]
        slot = f"irt/eval/{scope}/{subset}/cmp/{irt}/{other}/"
        m_pub = coarse(mean, step, key, slot + "mean", noisy)
        lo = min(m_pub, coarse(M.percentile(boots, lo_q), step, key, slot + "lo", noisy))
        hi = max(m_pub, coarse(M.percentile(boots, hi_q), step, key, slot + "hi", noisy))
        verdict = (BETTER if lo > 0 and m_pub >= cfg.min_logloss_gain - 1e-9 else
                   WORSE if hi < 0 and m_pub <= -cfg.min_logloss_gain + 1e-9 else SIMILAR)
        out.append({"irt": irt, "versus": other, "log_loss_gain": m_pub, "gain_ci": [lo, hi], "verdict": verdict})
    return out


# ---------- 先验难度对照（只用公开值） ----------
def _published(blocks: list[dict[str, Any]], kind: str, node: str | None = None) -> list[dict[str, Any]]:
    return [{"node": blk["node"], **it} for blk in blocks if node is None or blk["node"] == node for it in blk["items"]
            if it["disclosed"] and it["prior_difficulty"] is not None]


def rank_correlation(blocks: list[dict[str, Any]], cfg: Config) -> dict[str, Any]:
    def rho(items: list[dict[str, Any]], kind: str) -> Any:
        if len(items) < cfg.min_items_for_rank:
            return INSUFFICIENT
        v = X.spearman([it["prior_difficulty"] for it in items], [it[kind]["difficulty"] for it in items])
        return None if v is None else r2(v)
    return {"overall": {k: rho(_published(blocks, k), k) for k in ("1pl", "2pl")},
            "by_node": [{"node": blk["node"], **{k: rho(_published(blocks, k, blk["node"]), k) for k in ("1pl", "2pl")}}
                        for blk in blocks]}


def prior_mismatch(blocks: list[dict[str, Any]], cfg: Config) -> dict[str, Any]:
    """按节点：公开的 2PL 难度对先验难度做 Theil–Sen 稳健直线；残差绝对值 > 门槛且 2PL 自助法区间不覆盖直线上的值 → 候选"""
    out = []
    for blk in blocks:
        items = _published(blocks, "2pl", blk["node"])
        rec: dict[str, Any] = {"node": blk["node"]}
        line = X.theil_sen([it["prior_difficulty"] for it in items], [it["2pl"]["difficulty"] for it in items]) \
            if len(items) >= cfg.min_items_for_rank else None
        if line is None:
            rec.update(line=INSUFFICIENT, candidates=[])
            out.append(rec)
            continue
        c0, c1 = line
        cands = []
        for it in items:
            expected = c0 + c1 * it["prior_difficulty"]
            resid = it["2pl"]["difficulty"] - expected
            ci = it["2pl"].get("difficulty_ci")
            outside = ci is None or not (ci[0] <= expected <= ci[1])
            if abs(resid) > cfg.prior_mismatch_logits and outside:
                cands.append({"item_id": it["item_id"], "prior_difficulty": it["prior_difficulty"], "difficulty_2pl": it["2pl"]["difficulty"],
                              "difficulty_2pl_ci": ci, "expected_difficulty": r1(expected), "residual": r1(resid), "conclusion": MISMATCH})
        rec.update(line={"intercept": r2(c0), "slope": r2(c1)}, candidates=cands)
        out.append(rec)
    return {"by_node": out}


# ---------- 报表 ----------
def build_report(iv: ItemsView, resp: Responses, an: Analysis, cfg: Config, noisy: bool = True) -> dict[str, Any]:
    blocks = [block_record(b, iv, cfg, noisy) for b in an.blocks]
    evals = [eval_record(scope, subset, rows, cfg, noisy) for (scope, subset), rows in an.rows.items()]
    key_all = set_key(sorted(an.all_students))
    n_all = len(an.all_students) + (noise(key_all, "irt/cohort/n") if noisy else 0)
    items = [it for blk in blocks for it in blk["items"]]
    warn_counts: dict[str, int] = {}
    for it in items:
        for w in it["warnings"]:
            warn_counts[w] = warn_counts.get(w, 0) + 1
    overall = next((e for e in evals if e["group"] == ALL and e["student_group"] == ANY), {"disclosed": False})
    mismatch = prior_mismatch(blocks, cfg)
    fc = cfg.fit
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "notice": SYNTHETIC_NOTICE if (resp.synthetic or iv.synthetic) else "",
        "not_a_source_of_truth": True,
        "usage": USAGE,
        "status": STATUS,
        "config": cfg.provenance(),
        "items_snapshot": {"subject": iv.subject, "map_version": iv.map_version, "snapshot_digest": iv.digest,
                           "items": len(iv.items), "nodes": len(iv.nodes)},
        "cohort": {"students_approx": n_approx(n_all) if n_all >= cfg.min_eval_students + NOISE_MAX else INSUFFICIENT,
                   "fit_fraction_range": [cfg.min_fit_fraction, cfg.max_fit_fraction]},
        "method": {
            "split": ("时间切分：作答表 segment = fit 的作答拟合、segment = eval 的作答评估（切分点与掌握估计基线同一时刻，由导出负责）；"
                      "每名学生每道题只有一行（独立作答的第一次），后段只含该生在拟合段没答过的题；报表只写配置的拟合比例范围"),
            "blocking": "按节点（kge.item_tag.main_node_id，只计主节点，次要节点不计）分块，各块独立拟合；题目族（family_code）内不共享参数",
            "model": "P(答对) = 1 / (1 + exp(−a(θ − b)))，θ ~ N(0, 1)；1PL = 块内共同区分度，2PL = 各题区分度",
            "estimation": (f"边际最大似然 + EM，Gauss–Hermite {fc.quadrature_points} 点；M 步 Fisher 得分 + 步长减半（广义 EM，目标单调不降）；"
                           f"先验 b ~ N(0, {fc.b_prior_sd}²)、log a ~ N({fc.log_a_prior_mean}, {fc.log_a_prior_sd}²)；"
                           f"参数界 b ∈ [{fc.b_lo}, {fc.b_hi}]、a ∈ [{fc.a_lo}, {fc.a_hi}]；收敛阈值 {fc.tolerance}，最多 {fc.max_iterations} 轮；"
                           "初值由拟合段正确率确定，无随机初始化"),
            "bootstrap": (f"2PL：块内学生有放回重抽 {cfg.bootstrap_draws} 次（固定种子 {cfg.bootstrap_seed}），每次从全样本估计出发、"
                          f"至多 {cfg.bootstrap_max_iterations} 轮；{round(cfg.bootstrap_confidence * 100)}% 插值分位数区间。{BOOT_NOTE}"),
            "methods": {m: METHOD_NAMES[m] for m in METHODS},
            "baseline": (f"后段每次作答的预测 = p_mastery × (1 − p_slip) + (1 − p_mastery) × p_guess(题型)，p_mastery 取同一切分点上"
                         f"该生在该题单元（主节点 × 认知层次）的掌握估计，没有记录的用 p_init；数值取自 {cfg.bkt.source}"
                         f"（{cfg.bkt.config_version}，{'合成占位、未签字' if cfg.bkt.synthetic or not cfg.bkt.signed else '已签字'}）。{BKT_NOTE}"),
            "bkt_platt": "BKT 基线概率的 logit 上做逻辑回归校准；按记录键把后段学生分两半交叉拟合（用另一半学生的后段作答拟合）",
            "prior_only": "P(答对) = σ(β0_节点 + β1 ×（先验难度 − 0.5）)，在拟合段汇总上拟合（按节点截距、共同斜率）",
            "prior_ability": "与 1PL 相同，但难度 b = c0 + c1 ×（先验难度 − 0.5），只估 c0、c1 与共同区分度",
            "no_prior": "没有先验难度的题不进两种先验对照；为保持配对，也不进其余方法的评估（题数见 evaluation.items_without_prior）",
            "prediction": "IRT 与“先验难度 + 能力”用后验预测（拟合段作答给出的能力后验在求积节点上平均）；冷启动学生（拟合段在本块没有作答）用先验 N(0, 1)",
            "metrics": (f"每生一个点：对数损失与 Brier 先按学生平均再对学生平均；AUC 按 1 / 该生后段作答数加权（同分记一半）；"
                        f"概率截断到 [{cfg.prob_clip}, {1 - cfg.prob_clip}]；答对、答错两类按人加权总量各加扰动后，较小一类不足两类之和的 "
                        f"{AUC_MIN_SHARE} 加余量时 AUC 只写“样本不足”；有拟合数据与冷启动两组分开报告，各自过人数门槛"),
            "guard": (f"题目：答对、答错两格各加 −{NOISE_MAX}…+{NOISE_MAX} 的表键扰动，扰动后人数 ≥ {cfg.min_respondents + 2 * NOISE_MAX}"
                      f"（真实 ≥ {cfg.min_respondents}）且两格都 ≥ {cfg.min_outcome_cell + NOISE_MAX} 才给参数；区分度按 {cfg.discrimination_step} "
                      f"的格点、难度按逐题格点（{cfg.difficulty_step} 的 1 / 2 / 5 / 10 倍，按单人影响上界加粗）取整后再加 −{NOISE_MAX}…+{NOISE_MAX} 格，"
                      f"1PL 与 2PL 难度各自独立扰动；评估组内学生（扰动后）≥ {cfg.min_eval_students + NOISE_MAX} 才给，"
                      f"指标粒度 = 取值范围 ÷ 扰动后人数（向上取到格点），再加 −{NOISE_MAX}…+{NOISE_MAX} 格；人数向下取整到 10；"
                      "不输出学生能力、伪名、作答次数与实际拟合比例；秩相关、疑似标错与对照结论只由公开值算出"),
            "comparison": (f"每生配对差（对照 − IRT 的该生平均对数损失）的均值与 {round(cfg.bootstrap_confidence * 100)}% 配对自助法区间"
                           f"（按学生重抽 {cfg.compare_draws} 次，固定种子 {cfg.compare_seed}），各只加一次 ±{NOISE_MAX} 格扰动"
                           f"（粒度 = 2 × ln(1/{cfg.prob_clip}) ÷ 扰动后人数）；公开区间下端 > 0 且均值 ≥ {cfg.min_logloss_gain} 才写“{BETTER}”，"
                           f"上端 < 0 且均值 ≤ −{cfg.min_logloss_gain} 写“{WORSE}”，否则“{SIMILAR}”"),
            "prior_mismatch": (f"按节点：公开的 2PL 难度对先验难度做 Theil–Sen 稳健直线，残差绝对值 > {cfg.prior_mismatch_logits} 且 2PL 自助法区间"
                               f"不覆盖直线上的值，列为“{MISMATCH}”；本工具不改先验"),
        },
        "blocks": blocks,
        "evaluation": {"groups": evals, "items_without_prior": an.no_prior_items},
        "rank_correlation": rank_correlation(blocks, cfg),
        "prior_mismatch": mismatch,
        "summary": {"items": len(items), "items_with_parameters": sum(1 for it in items if it["disclosed"]),
                    "warnings": dict(sorted(warn_counts.items())),
                    "blocks_fitted": sum(1 for b in blocks if b["fitted"]),
                    "prior_mismatch_candidates": sum(len(r["candidates"]) for r in mismatch["by_node"]),
                    "overall_comparisons": ({f"{c['irt']} vs {c['versus']}": c["verdict"] for c in overall["comparisons"]}
                                            if overall.get("disclosed") else INSUFFICIENT)},
    }


# ---------- Markdown ----------
def _fmt(v: Any, step: float) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:.{decimals(step)}f}"
    return str(v)


def _ci(v: list[float], step: float) -> str:
    return f"[{_fmt(v[0], step)}, {_fmt(v[1], step)}]"


def render_md(report: dict[str, Any], cfg: Config) -> str:
    c, snap = report["config"], report["items_snapshot"]
    lines = ["# IRT 试点评估报告（KGE-10 第一步）", ""]
    if report["notice"]:
        lines += [f"**{report['notice']}。** 输入是固定种子的合成队列，结果只用来检验工具本身，不能当作 IRT 是否优于现行掌握估计的依据。", ""]
    fr = report["cohort"]["fit_fraction_range"]
    lines += [f"> **{report['usage']}。**",
              f"> {report['status']}。",
              f"> 只含题目级与节点级聚合：{report['method']['guard']}。",
              f"> 配置 {c['config_version']}（指纹 {c['fingerprint']}，{c['signoff']}）；基线掌握配置 {c['baseline_config']['source']}"
              f"（{c['baseline_config']['config_version']}）；题目快照 {snap['subject']} {snap['map_version']}"
              f"（摘要 {snap['snapshot_digest'][:16]}，{snap['items']} 道题、{snap['nodes']} 个节点）；"
              f"学生约 {report['cohort']['students_approx']} 人，拟合段占比在配置范围 [{fr[0]}, {fr[1]}] 内。", ""]

    ev = report["evaluation"]
    lines += ["## 一、预测对照（后段作答）", "",
              f"没有先验难度、不进评估的题：{ev['items_without_prior']} 道。{BKT_NOTE}。", ""]
    for g in ev["groups"]:
        title = ("全部节点" if g["group"] == ALL else g["group"]) + f" · {g['student_group']}"
        lines += [f"### {title}", ""]
        if not g["disclosed"]:
            lines += [f"（{INSUFFICIENT}）", ""]
            continue
        st = g["steps"]
        lines += [f"学生约 {g['students_approx']} 人；粒度：对数损失 {st['log_loss']}、Brier {st['brier']}、AUC {st['auc']}、"
                  f"配对差 {st['paired_difference']}（各值已加 ±{NOISE_MAX} 格扰动）。", "",
                  "| 方法 | 对数损失 | Brier | AUC |", "|---|---|---|---|"]
        for m in METHODS:
            v = g["metrics"][m]
            lines.append(f"| {METHOD_NAMES[m]} | {_fmt(v['log_loss'], st['log_loss'])} | {_fmt(v['brier'], st['brier'])} | {_fmt(v['auc'], st['auc'])} |")
        lines += ["", "对照（每生配对差 = 对照 − IRT 的对数损失，正数表示 IRT 更好；方括号为配对自助法区间）："]
        for cmp in g["comparisons"]:
            sd = st["paired_difference"]
            lines.append(f"- {METHOD_NAMES[cmp['irt']]} 对 {METHOD_NAMES[cmp['versus']]}：{_fmt(cmp['log_loss_gain'], sd)} "
                         f"{_ci(cmp['gain_ci'], sd)}，{cmp['verdict']}")
        lines.append("")

    as_ = cfg.discrimination_step
    lines += ["## 二、题目参数", "",
              f"只列公开了参数的题（作答人数与答对、答错两格都达门槛）；其余只计数，见第四节。区间为 2PL 自助法 {round(cfg.bootstrap_confidence * 100)}% 区间"
              f"（{BOOT_NOTE}）。难度格点逐题给出。", ""]
    for blk in report["blocks"]:
        lines += [f"### {blk['node']}", ""]
        if not blk["fitted"]:
            lines += [f"（{W_NOT_FITTED}；块内学生约 {blk['students_approx']}）", ""]
            continue
        md = blk["models"]
        lines += [f"块内学生约 {blk['students_approx']} 人；1PL 共同区分度 {_fmt(md['1pl']['common_discrimination'], as_)}；"
                  f"收敛：1PL {'是' if md['1pl']['converged'] else '否'}、2PL {'是' if md['2pl']['converged'] else '否'}、"
                  f"先验难度 + 能力 {'是' if md['prior_ability']['converged'] else '否'}。", ""]
        shown = [it for it in blk["items"] if it["disclosed"]]
        if not shown:
            lines += ["（没有公开参数的题）", ""]
            continue
        lines += ["| 题目 | 层次 | 先验难度 | 作答人数 | 难度格点 | 1PL 难度 | 2PL 难度 | 2PL 难度区间 | 2PL 区分度 | 2PL 区分度区间 | 告警 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for it in shown:
            two, ds = it["2pl"], it["difficulty_step"]
            prior = "无" if it["prior_difficulty"] is None else f"{it['prior_difficulty']:.3f}"
            lines.append(f"| {it['item_id']} | {it['cognitive_level']} | {prior} | 约 {it['n_approx']} | {ds} | {_fmt(it['1pl']['difficulty'], ds)} | "
                         f"{_fmt(two['difficulty'], ds)} | {_ci(two['difficulty_ci'], ds) if 'difficulty_ci' in two else '—'} | "
                         f"{_fmt(two['discrimination'], as_)} | {_ci(two['discrimination_ci'], as_) if 'discrimination_ci' in two else '—'} | "
                         f"{'；'.join(it['warnings']) or '—'} |")
        lines.append("")

    rc, pm = report["rank_correlation"], report["prior_mismatch"]
    lines += ["## 三、先验难度与 IRT 难度", "",
              f"Spearman 秩相关（公开了参数且有先验难度的题）：全部 1PL {rc['overall']['1pl']}、2PL {rc['overall']['2pl']}；"
              + "；".join(f"{r['node']} 1PL {r['1pl']}、2PL {r['2pl']}" for r in rc["by_node"]) + "。", "",
              f"先验难度疑似标错：按节点做稳健直线，残差超过 {cfg.prior_mismatch_logits} 且 2PL 自助法区间不覆盖直线上的值的题列出。"
              "**候选仅供教研参考**：本工具不改先验难度；修改走 KGE-02 打标流程，不得照抄或换算这里的 IRT 值；按候选修改先验另起 [NEEDS-HUMAN] 任务。", ""]
    n_c = 0
    for r in pm["by_node"]:
        if not isinstance(r["line"], dict):
            lines.append(f"- {r['node']}：稳健直线{INSUFFICIENT}")
            continue
        lines.append(f"- {r['node']}：2PL 难度 ≈ {r['line']['intercept']} + {r['line']['slope']} × 先验难度；候选 {len(r['candidates'])} 道")
        for cd in r["candidates"]:
            n_c += 1
            lines.append(f"  - {cd['item_id']}：先验难度 {cd['prior_difficulty']:.3f}，2PL 难度 {cd['difficulty_2pl']}"
                         f"（区间 {cd['difficulty_2pl_ci']}），按直线应约 {cd['expected_difficulty']}，残差 {cd['residual']}")
    lines += ["", f"候选共 {n_c} 道。", ""]

    lines += ["## 四、可识别性告警", ""]
    any_w = False
    for blk in report["blocks"]:
        for w in blk["warnings"]:
            lines.append(f"- {blk['node']}：{w}")
            any_w = True
        grouped: dict[str, list[str]] = {}
        for it in blk["items"]:
            for w in it["warnings"]:
                grouped.setdefault(w, []).append(it["item_id"])
        for w, ids in sorted(grouped.items()):
            lines.append(f"- {blk['node']} · {w}（{len(ids)} 道）：{'、'.join(ids)}")
            any_w = True
    if not any_w:
        lines.append("（无）")
    lines += ["", "## 五、口径与限制", "",
              f"- {report['method']['estimation']}。",
              f"- {report['method']['bootstrap']}。",
              f"- 基线：{report['method']['baseline']}。",
              f"- BKT + Platt：{report['method']['bkt_platt']}。",
              f"- 只用先验难度：{report['method']['prior_only']}；先验难度 + 能力：{report['method']['prior_ability']}；{report['method']['no_prior']}。",
              f"- {report['method']['metrics']}。",
              f"- 判定：{report['method']['comparison']}（门槛待教研确认）。",
              "- 学生能力只以后验形式在内存里用于预测，不输出；表键扰动不是差分隐私（残余风险见 tools/kge-irt/README.md）。",
              "", f"详细数值见同目录 {REPORT_NAME}。", ""]
    return "\n".join(lines)


def render(report: dict[str, Any], cfg: Config) -> dict[str, str]:
    return {REPORT_NAME: render_json(report), MD_NAME: render_md(report, cfg)}


def run(iv: ItemsView, resp: Responses, an: Analysis, cfg: Config, pseudonyms: Iterable[str] = ()) -> dict[str, str]:
    files = render(build_report(iv, resp, an, cfg), cfg)
    assert_no_pseudonyms(files, frozenset(resp.pseudonyms) | frozenset(pseudonyms))
    return files
