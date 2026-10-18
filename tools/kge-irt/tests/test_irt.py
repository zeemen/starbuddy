"""KGE-10 IRT 试点评估第一步（specs/kge10-irt-pilot）：求积、似然与梯度数值核对、EM 单调不降、参数恢复、评估指标手算、
样本不足与取整守卫、表键扰动、伪名不外泄、差分（删一人重跑）定不了格、输入与配置守卫、逐字节一致（含与仓库样例一致）。
全程合成数据，不连库。"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import random
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from conftest import REPO, TOOL, load_synthetic

from kge_calibrate.calibrate import INSUFFICIENT, NOISE_MAX, n_approx, noise, set_key
from kge_irt import SYNTHETIC_NOTICE
from kge_irt import irt as M
from kge_irt import metrics as X
from kge_irt import report as R
from kge_irt import synth as Y
from kge_irt.__main__ import main
from kge_irt.analysis import ALL, ANY, COLD, METHODS, WARM, analyze, bkt_predict, fold_of
from kge_irt.config import PENDING, ConfigError, load_config, parse_baseline_ref, parse_config, require_signed_baseline
from kge_irt.inputs import (InputRefused, Item, ItemsView, Responses, load_items, meta_synthetic, parse_baseline, parse_items,
                            parse_responses)
from kge_irt.quad import gauss_hermite, normal_quadrature

CFG = load_config()
SAMPLE = TOOL / "sample"
# 仓库样例 irt-report.json 的 sha256（样例过期时按 Makefile 注释重生 sample/irt-report.md，并更新这里）
SAMPLE_JSON_SHA256 = "8d20721127c58e72b611dcf1e74df0cab221cecd2f37a209944ad2fc307b9418"
CONFIG_DOC = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
Q15 = normal_quadrature(15)


# ---------------- 求积 ----------------
def test_gauss_hermite_closed_form_and_moments():
    th, w = normal_quadrature(3)
    assert th[0] == pytest.approx(-math.sqrt(3)) and th[1] == 0.0 and th[2] == pytest.approx(math.sqrt(3))
    assert w == pytest.approx((1 / 6, 2 / 3, 1 / 6))
    x, wx = gauss_hermite(2)
    assert x == pytest.approx((-1 / math.sqrt(2), 1 / math.sqrt(2))) and wx == pytest.approx((math.sqrt(math.pi) / 2,) * 2)
    for n in (5, 15, 21):
        th, w = normal_quadrature(n)
        assert list(th) == sorted(th) and all(v > 0 for v in w)
        for k in range(0, 2 * n):           # n 点规则对次数 ≤ 2n − 1 精确：E θ^k = (k − 1)!!（偶数），0（奇数）
            want = 0.0 if k % 2 else math.prod(range(k - 1, 0, -2))
            scale = sum(wi * abs(t) ** k for t, wi in zip(th, w))
            assert sum(wi * t ** k for t, wi in zip(th, w)) == pytest.approx(want, rel=1e-8, abs=1e-12 * scale + 1e-12), (n, k)


def test_softplus_sigmoid_are_stable():
    assert M.softplus(800.0) == 800.0 and M.softplus(-800.0) == 0.0
    assert M.sigmoid(800.0) == 1.0 and M.sigmoid(-800.0) == 0.0 and M.sigmoid(0.0) == 0.5
    for z in (-5.0, -0.3, 0.0, 2.0, 7.0):
        assert M.softplus(z) == pytest.approx(math.log1p(math.exp(z)))
        assert -M.softplus(-z) == pytest.approx(math.log(M.sigmoid(z)))


# ---------------- 小块：似然与梯度 ----------------
def small_block() -> M.BlockData:
    rng = random.Random(7)
    fit = {}
    for i in range(40):
        th = rng.gauss(0, 1)
        resp = []
        for it, (a, b) in zip(("A", "B", "C", "D"), ((1.5, -0.5), (0.7, 0.4), (1.0, 1.0), (2.0, 0.0))):
            if rng.random() < 0.8:
                resp.append((it, rng.random() < M.sigmoid(a * (th - b))))
        fit[f"syn-{chr(97 + i % 26)}{chr(97 + i // 26)}aaaaaaaa"] = resp
    return M.block_data("MATH.X.Y", ["A", "B", "C", "D"], [0.3, 0.55, 0.8, None], fit)


def brute_loglik(bd: M.BlockData, st: M.Structure, phi, quad) -> float:
    th, w = quad
    ab = M.item_params(st, phi)
    total = 0.0
    for s, k in zip(bd.students, bd.pattern_of):
        lik = 0.0
        for t, wq in zip(th, w):
            v = wq
            for j, x in bd.patterns[k]:
                p = 1 / (1 + math.exp(-ab[j][0] * (t - ab[j][1])))
                v *= p if x else 1 - p
            lik += v
        total += math.log(lik)
    return total


@pytest.mark.parametrize("kind", M.KINDS)
def test_marginal_likelihood_matches_brute_force(kind):
    bd = small_block()
    st = M.structure(kind, bd.priors, CFG.fit)
    rng = random.Random(3)
    phi = [v + rng.uniform(-0.3, 0.3) for v in M.initial_phi(st, bd)]
    es = M.e_step(bd, st, phi, Q15)
    assert es.loglik == pytest.approx(brute_loglik(bd, st, phi, Q15), rel=1e-10)
    for j in range(len(bd.items)):          # 期望计数：n_jq 之和 = 作答人数，r ≤ n
        assert sum(es.n[j]) == pytest.approx(bd.item_counts()[j][1])
        assert sum(es.r[j]) <= sum(es.n[j]) + 1e-9


@pytest.mark.parametrize("kind", M.KINDS)
def test_gradient_matches_finite_differences(kind):
    """M 步梯度 = M 步目标的数值导数；且（Fisher 恒等式）= 边际对数似然 + 对数先验的数值导数"""
    bd = small_block()
    st = M.structure(kind, bd.priors, CFG.fit)
    rng = random.Random(11)
    phi = [v + rng.uniform(-0.2, 0.2) for v in M.initial_phi(st, bd)]
    es = M.e_step(bd, st, phi, Q15)
    h = 1e-6
    for comp in st.components:
        g, info = M.m_gradient(st, comp, phi, es, Q15)
        for i, k in enumerate(comp[0]):
            up, dn = list(phi), list(phi)
            up[k] += h
            dn[k] -= h
            fd_q = (M.m_objective(st, comp, up, es, Q15) - M.m_objective(st, comp, dn, es, Q15)) / (2 * h)
            fd_ml = ((M.e_step(bd, st, up, Q15).loglik + M.log_prior(st, up)) - (M.e_step(bd, st, dn, Q15).loglik + M.log_prior(st, dn))) / (2 * h)
            assert g[i] == pytest.approx(fd_q, rel=1e-5, abs=1e-6), (kind, k)
            assert g[i] == pytest.approx(fd_ml, rel=1e-5, abs=1e-6), (kind, k)
        P = len(comp[0])
        for u in range(P):
            assert info[u][u] > 0
            for v in range(P):
                assert info[u][v] == pytest.approx(info[v][u])


def test_gradient_and_information_include_prior():
    """没有作答时（期望计数全 0），梯度与信息只剩先验：g = −(φ − μ)/sd²，信息 = diag(1/sd²)"""
    bd = small_block()
    for kind in M.KINDS:
        st = M.structure(kind, bd.priors, CFG.fit)
        phi = [0.3] * st.size
        zero = tuple((0.0,) * 15 for _ in bd.items)
        es = M.EStep(0.0, zero, zero)
        for comp in st.components:
            g, info = M.m_gradient(st, comp, phi, es, Q15)
            for i, k in enumerate(comp[0]):
                sd2 = st.prior_sd[k] ** 2
                assert g[i] == pytest.approx(-(0.3 - st.prior_mean[k]) / sd2)
                assert info[i][i] == pytest.approx(1.0 / sd2)


def test_solve_small_systems():
    assert M.solve([[2.0, 1.0], [1.0, 3.0]], [3.0, 5.0]) == pytest.approx([0.8, 1.4])
    assert M.solve([[0.0, 1.0], [1.0, 0.0]], [2.0, 3.0]) == pytest.approx([3.0, 2.0])       # 需要换主元
    with pytest.raises(ZeroDivisionError):
        M.solve([[0.0, 0.0], [0.0, 0.0]], [1.0, 1.0])


@pytest.mark.parametrize("kind", M.KINDS)
def test_em_monotone_and_deterministic(kind):
    bd = small_block()
    st = M.structure(kind, bd.priors, CFG.fit)
    f1 = M.fit_em(bd, st, Q15, CFG.fit)
    f2 = M.fit_em(bd, st, Q15, CFG.fit)
    assert f1 == f2 and f1.converged
    assert all(b >= a - 1e-9 for a, b in zip(f1.trace, f1.trace[1:])), f1.trace
    assert f1.trace[-1] > f1.trace[0]
    tight = dataclasses.replace(CFG.fit, a_lo=0.9, a_hi=1.1, b_lo=-0.2, b_hi=0.3)     # 截断生效时也单调
    st2 = M.structure(kind, bd.priors, tight)
    f3 = M.fit_em(bd, st2, Q15, tight)
    assert all(b >= a - 1e-9 for a, b in zip(f3.trace, f3.trace[1:]))
    for a, b in M.item_params(st2, f3.phi):
        assert 0.9 - 1e-12 <= a <= 1.1 + 1e-12
        if kind != "prior":
            assert -0.2 <= b <= 0.3


def test_em_monotone_on_synthetic_blocks(synthetic):
    for b in synthetic["analysis"].blocks:
        assert b.fitted
        for kind, f in b.fits.items():
            assert f.converged and len(f.trace) >= 3
            assert all(y >= x - 1e-7 for x, y in zip(f.trace, f.trace[1:])), (b.node, kind)


def test_m_step_never_decreases_objective():
    bd = small_block()
    for kind in M.KINDS:
        st = M.structure(kind, bd.priors, CFG.fit)
        phi = M.initial_phi(st, bd)
        es = M.e_step(bd, st, phi, Q15)
        new = M.m_step(st, phi, es, Q15, CFG.fit)
        for comp in st.components:
            assert M.m_objective(st, comp, new, es, Q15) >= M.m_objective(st, comp, phi, es, Q15) - 1e-12
        assert new != list(phi)


def test_structure_shapes_and_initial_values():
    bd = small_block()
    s1, s2, sp = (M.structure(k, bd.priors, CFG.fit) for k in ("1pl", "2pl", "prior"))
    assert (s1.size, s2.size, sp.size) == (5, 8, 3) and len(s2.components) == 4 and len(s1.components) == 1
    assert s1.components[0][0] == (0, 1, 2, 3, 4) and sp.components[0][0] == (0, 1, 2)       # 共同区分度参与估计
    assert M.fit_em(bd, s1, Q15, CFG.fit).phi[0] != 0.0
    assert sp.b_of[3] == ((1, 1.0), (2, 0.0)) and sp.b_of[0][1][1] == pytest.approx(-0.2)   # 无先验记 0.5
    phi = M.initial_phi(s2, bd)
    for j, (x, n) in enumerate(bd.item_counts()):
        p = min(0.95, max(0.05, x / n))
        assert phi[2 * j + 1] == pytest.approx(-math.log(p / (1 - p))) and phi[2 * j] == 0.0
    with pytest.raises(ValueError):
        M.structure("3pl", bd.priors, CFG.fit)


def test_block_data_patterns_and_resampling():
    bd = small_block()
    assert sum(bd.weights) == len(bd.students) == 40 and len(bd.patterns) == len(set(bd.patterns))
    assert all(len(bd.respondents(j)) == bd.item_counts()[j][1] for j in range(4))
    w1 = M.resample_weights(bd, random.Random("x"))
    w2 = M.resample_weights(bd, random.Random("x"))
    assert w1 == w2 and sum(w1) == 40 and w1 != bd.weights


def test_percentile_interpolates():
    assert M.percentile([1, 2, 3, 4], 0.5) == 2.5 and M.percentile([5], 0.9) == 5
    assert M.percentile([0, 10], 0.05) == pytest.approx(0.5) and M.percentile([3, 1, 2], 1.0) == 3


def test_prior_logistic_recovers_coefficients():
    rows = []
    for k in range(11):
        x = k / 10
        for node, c0 in (("A", 0.4), ("B", -0.6)):           # 按节点截距、共同斜率
            p = M.sigmoid(c0 - 3.0 * (x - 0.5))
            rows.append((node, x, round(p * 100000), 100000))
    inter, slope = M.fit_prior_by_node(rows)
    assert inter["A"] == pytest.approx(0.4, abs=1e-3) and inter["B"] == pytest.approx(-0.6, abs=1e-3)
    assert slope == pytest.approx(-3.0, abs=1e-3)
    beta = M.fit_logistic([((1.0,), 30.0, 100.0)])
    assert beta[0] == pytest.approx(math.log(0.3 / 0.7), abs=1e-4)
    assert M.logit(0.0, 0.01) == pytest.approx(math.log(0.01 / 0.99)) and M.logit(0.5, 0.01) == 0.0


def test_platt_cross_fit_by_record_key():
    """Platt 校准两折交叉拟合：每折用另一折学生拟合；系统性偏差被修正"""
    from kge_irt.analysis import platt
    rng = random.Random(4)
    rows = []
    for i in range(400):
        pid = Y.pseudonym(77, i)
        for _ in range(5):
            p = rng.uniform(0.05, 0.95)
            rows.append((pid, rng.random() < p, {"bkt": 0.5 * p + 0.4}))          # 被压缩、偏高的基线
    platt(rows, CFG)
    assert {fold_of(r[0]) for r in rows} == {0, 1}
    ll = lambda m: sum(X.log_loss_one(y, pr[m], 0.01) for _, y, pr in rows) / len(rows)   # noqa: E731
    assert ll("bkt_platt") < ll("bkt") - 0.02
    other = [r for r in rows if fold_of(r[0]) == 0]
    flipped = [(pid, not y, pr) if fold_of(pid) == 0 else (pid, y, pr) for pid, y, pr in rows]
    before = [r[2]["bkt_platt"] for r in other]
    platt(flipped, CFG)                                                          # 改本折的答案，不影响本折的预测
    assert [r[2]["bkt_platt"] for r in other] == pytest.approx(before)


# ---------------- 评估指标手算 ----------------
def test_metrics_hand_values():
    rows = [("s1", True, 0.8), ("s1", False, 0.4), ("s2", True, 0.6)]
    pts = X.student_points(rows, 0.01)
    assert pts["s1"][0] == pytest.approx((-math.log(0.8) - math.log(0.6)) / 2) and pts["s1"][2] == 2
    assert pts["s1"][1] == pytest.approx((0.04 + 0.16) / 2) and pts["s2"][1] == pytest.approx(0.16)
    assert X.student_mean(pts, 0) == pytest.approx(((-math.log(0.8) - math.log(0.6)) / 2 - math.log(0.6)) / 2)
    assert X.log_loss_one(True, 0.0, 0.01) == pytest.approx(-math.log(0.01))
    assert X.log_loss_one(False, 1.0, 0.01) == pytest.approx(-math.log(0.01))


def brute_auc(rows):
    num = den = 0.0
    for s1, y1, w1 in rows:
        for s2, y2, w2 in rows:
            if y1 and not y2:
                den += w1 * w2
                num += w1 * w2 * (1.0 if s1 > s2 else 0.5 if s1 == s2 else 0.0)
    return num / den


def test_weighted_auc_matches_pairs_with_ties():
    rows = [(0.9, True, 1.0), (0.4, False, 0.5), (0.4, True, 0.5), (0.2, False, 1.0), (0.7, False, 1.0)]
    assert X.weighted_auc(rows) == pytest.approx(brute_auc(rows))
    rng = random.Random(5)
    rows = [(round(rng.random(), 1), rng.random() < 0.5, rng.choice((0.25, 0.5, 1.0))) for _ in range(60)]
    assert X.weighted_auc(rows) == pytest.approx(brute_auc(rows))
    assert X.weighted_auc([(0.3, True, 1.0)]) is None
    plain = [(0.1, False, 1.0), (0.2, True, 1.0), (0.3, False, 1.0), (0.4, True, 1.0)]
    assert X.weighted_auc(plain) == pytest.approx(0.75)
    w, share = X.per_student_auc_rows([("a", True, 0.3), ("a", False, 0.2), ("b", True, 0.9)])
    assert [r[2] for r in w] == [0.5, 0.5, 1.0] and share == pytest.approx(0.75)


def test_rank_statistics():
    assert X.ranks([3, 1, 2, 2]) == [4.0, 1.0, 2.5, 2.5]
    assert X.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert X.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert X.spearman([1, 2, 3], [5, 5, 5]) is None
    assert X.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)
    c0, c1 = X.theil_sen([0, 1, 2, 3, 4], [1, 3, 5, 7, 100])      # 一个离群点不影响稳健直线
    assert (c0, c1) == (1.0, 2.0)
    assert X.theil_sen([1, 1], [2, 3]) is None


def _learning():
    """学习服务的掌握纯函数（只在测试里 import，做同一合成输入上的对照；纯标准库）"""
    p = str(REPO / "services/learning")
    if p not in sys.path:
        sys.path.insert(0, p)
    from learning.mastery import Evidence, Unit, load_config as load_mastery, replay
    return Evidence, Unit, load_mastery, replay


def test_bkt_baseline_formula_and_reference_values():
    ref = CFG.bkt
    assert bkt_predict(0.0, ref, "syn-choice") == pytest.approx(ref.p_guess("syn-choice"))
    assert bkt_predict(1.0, ref, None) == pytest.approx(1 - ref.p_slip)
    assert bkt_predict(0.5, ref, "syn-fill") == pytest.approx(0.5 * (1 - ref.p_slip) + 0.5 * ref.p_guess_default)
    _, _, load_mastery, _ = _learning()
    mcfg = load_mastery(REPO / CFG.baseline_source, env="test")
    for t in (None, "", "syn-choice", "syn-fill", "syn-solve", "nope"):
        assert ref.p_guess(t) == mcfg.p_guess(t)                          # 按题型取 p_guess，与学习服务同一取法
    b = mcfg["bkt"]
    assert (ref.p_init, ref.p_learn, ref.p_slip) == (b["p_init"], b["p_learn"], b["p_slip"])
    w = mcfg.weights
    assert ref.strength(True) == pytest.approx(abs(w["independent"]) * 1.0 * mcfg["source_weights"]["practice"])
    assert ref.strength(False) == pytest.approx(abs(w["independent_wrong"]) * mcfg["source_weights"]["practice"] * mcfg.penalty(None))
    assert ref.synthetic and not ref.signed
    service = (REPO / "services/learning/learning/mastery/bkt.py").read_text(encoding="utf-8")
    mine = (TOOL / "kge_irt/synth.py").read_text(encoding="utf-8")
    for line in ("post = p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)", "target = post + (1 - post) * learn",
                 "post = p * slip / (p * slip + (1 - p) * (1 - guess))", "out = p + min(1.0, strength) * (target - p)"):
        assert line in service and line in mine


def test_synthetic_baseline_matches_learning_service_replay(synthetic):
    """同一合成输入：合成基线的掌握概率 = 学习服务 replay（独立作答、A 级、日常练习、同族只计第一次）的 p_mastery"""
    Evidence, Unit, load_mastery, replay = _learning()
    mcfg = load_mastery(REPO / CFG.baseline_source, env="test")
    iv, base = synthetic["items"], synthetic["baseline"]
    lines = synthetic["inputs"]["responses.csv"].split("\n")[2:]
    pids = sorted(synthetic["responses"].pseudonyms)[:40]
    fit_rows: dict[tuple[str, str, str], list[Any]] = {}
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for line in lines:
        if not line:
            continue
        pid, item, node, corr, seq, seg = line.split(",")
        if seg != "fit" or pid not in pids:
            continue
        it = iv.items[item]
        ev = Evidence(evidence_id=f"e{seq}", recorded_at=t0 + timedelta(seconds=int(seq)), item_id=item, attempt_id=f"a{seq}",
                      family_id=it.family_code, item_type=it.item_type, main_node_id=node, cognitive_level=it.cognitive_level,
                      evidence_kind="independent", evidence_level="A", hint_level=0, verdict="correct" if corr == "1" else "incorrect")
        fit_rows.setdefault((pid, node, it.cognitive_level), []).append(ev)
    checked = skipped_family = 0
    now = t0 + timedelta(days=400)
    for (pid, node, level), evs in sorted(fit_rows.items()):
        res = replay(evs, Unit(node, level), mcfg, now).result
        mine = base.p_mastery.get((node, level), {}).get(pid, CFG.bkt.p_init)
        assert mine == pytest.approx(res.p_mastery, abs=2e-6), (node, level)
        checked += 1
        skipped_family += len({e.family_id for e in evs}) < len(evs)
    assert checked >= 200 and skipped_family >= 50                         # 覆盖了“同族只计第一次”


# ---------------- 合成队列：参数恢复与对照结论 ----------------
def test_synthetic_parameter_recovery(synthetic):
    tr = Y.truth()["items"]
    errs_b, errs_a, errs_b_pub, errs_a_pub = [], [], [], []
    rep = {it["item_id"]: it for blk in synthetic["report"]["blocks"] for it in blk["items"]}
    for b in synthetic["analysis"].blocks:
        for j, item in enumerate(b.data.items):
            if not rep[item]["disclosed"]:
                continue
            a, bb = b.params["2pl"][j]
            errs_b.append(abs(bb - tr[item]["b"]))
            errs_a.append(abs(a - tr[item]["a"]))
            errs_b_pub.append(abs(rep[item]["2pl"]["difficulty"] - tr[item]["b"]))
            errs_a_pub.append(abs(rep[item]["2pl"]["discrimination"] - tr[item]["a"]))
    assert len(errs_b) >= 30
    assert sum(errs_b) / len(errs_b) <= CFG.max_difficulty_mae and sum(errs_a) / len(errs_a) <= CFG.max_discrimination_mae
    assert sum(errs_b_pub) / len(errs_b_pub) <= CFG.max_difficulty_mae and sum(errs_a_pub) / len(errs_a_pub) <= CFG.max_discrimination_mae
    rasch = next(b for b in synthetic["analysis"].blocks if b.node == Y.N_RASCH)
    assert rasch.params["1pl"][0][0] == pytest.approx(1.0, abs=0.15)          # 1PL 成立的节点：共同区分度接近 1
    mixed = next(b for b in synthetic["analysis"].blocks if b.node == Y.N_MIXED)
    a_hat = [a for a, _ in mixed.params["2pl"]]
    a_true = [a for a, _ in Y.TRUE_PARAMS[Y.N_MIXED]]
    assert X.spearman(a_hat, a_true) > 0.7                                    # 2PL 区分出区分度高低


def test_synthetic_comparison_directions_match_truth(synthetic):
    """真值是静态 IRT：IRT 应优于 BKT 基线与只用先验难度；先验难度标错的题全部找出、无误报；秩相关为正但不完美"""
    rep = synthetic["report"]
    overall = next(g for g in rep["evaluation"]["groups"] if g["group"] == ALL and g["student_group"] == ANY)
    verdict = {(c["irt"], c["versus"]): c["verdict"] for c in overall["comparisons"]}
    for irt in ("irt_1pl", "irt_2pl"):
        for other in ("bkt", "bkt_platt", "prior_only"):
            assert verdict[(irt, other)] == R.BETTER, (irt, other)
    for c in overall["comparisons"]:
        assert c["gain_ci"][0] <= c["log_loss_gain"] <= c["gain_ci"][1]
        if c["verdict"] == R.BETTER:
            assert c["gain_ci"][0] > 0 and c["log_loss_gain"] >= CFG.min_logloss_gain
    ll = {m: overall["metrics"][m]["log_loss"] for m in METHODS}
    assert ll["irt_2pl"] < ll["prior_ability"] < ll["prior_only"] < ll["bkt"] and ll["bkt_platt"] < ll["bkt"]
    flagged = {c["item_id"] for r in rep["prior_mismatch"]["by_node"] for c in r["candidates"]}
    assert set(Y.MISLABELED) <= flagged and len(flagged - set(Y.MISLABELED)) <= CFG.max_false_prior_mismatch
    pm = {r["node"]: r for r in rep["prior_mismatch"]["by_node"]}
    assert pm[Y.N_SPARSE]["line"] == INSUFFICIENT and isinstance(pm[Y.N_RASCH]["line"], dict)
    for r in pm.values():
        for c in r["candidates"]:
            assert not (c["difficulty_2pl_ci"][0] <= c["expected_difficulty"] <= c["difficulty_2pl_ci"][1])
    assert 0.3 < rep["rank_correlation"]["overall"]["2pl"] < 0.95
    by_node = {r["node"]: r for r in rep["rank_correlation"]["by_node"]}
    assert by_node[Y.N_SPARSE]["2pl"] == INSUFFICIENT and by_node[Y.N_RASCH]["2pl"] > 0.9   # 题数不足不给；先验未标错的节点秩相关高


def test_eval_rows_and_baseline_predictions(synthetic):
    """评估口径：每条后段作答一行、六种方法同一批；BKT 基线按该题单元（节点 × 认知层次）的掌握概率与题型的 p_guess
    （没有记录的用 p_init）；有拟合数据 / 冷启动分组；拟合段没人答过的题、没有先验难度的题不评估"""
    d = synthetic
    an, base, iv, resp = d["analysis"], d["baseline"], d["items"], d["responses"]
    with_rec = without = 0
    rows_by_node = {n: an.rows[(n, WARM)] + an.rows[(n, COLD)] for n in Y.NODES}
    for node in Y.NODES:
        fitted = {i for rs in resp.fit.get(node, {}).values() for i, _ in rs}
        warm = {p for p, rs in resp.fit.get(node, {}).items() if rs}
        expect = []                                                  # 按评估作答的顺序逐行重算 BKT 预测
        for pid, rs in resp.eval.get(node, {}).items():
            for item, _ in rs:
                it = iv.items[item]
                if item in fitted and it.prior is not None:
                    pm = base.p_mastery.get((node, it.cognitive_level), {})
                    expect.append((pid in warm, bkt_predict(pm.get(pid, CFG.bkt.p_init), CFG.bkt, it.item_type)))
                    with_rec += pid in pm
                    without += pid not in pm
        ids = {id(r[2]) for r in rows_by_node[node]}
        mine = [r[2]["bkt"] for r in an.rows[(ALL, ANY)] if id(r[2]) in ids]
        assert mine == pytest.approx([v for _, v in expect])
        assert len(an.rows[(node, WARM)]) == sum(1 for w, _ in expect if w)
        for _, _, pred in rows_by_node[node]:
            assert set(pred) == set(METHODS) and all(0.0 < v < 1.0 for v in pred.values())
    assert with_rec > 100 and without > 20
    for method in ("prior_ability", "irt_1pl", "irt_2pl"):          # 用学生自己的能力后验：同一题不同学生的预测不同
        assert len({round(pred[method], 9) for _, _, pred in an.rows[(Y.N_MIXED, WARM)]}) >= 100, method
    assert len(an.rows[(ALL, ANY)]) == len(an.rows[(ALL, WARM)]) + len(an.rows[(ALL, COLD)])
    assert len(an.rows[(ALL, ANY)]) == sum(len(v) for v in rows_by_node.values())
    assert len(an.rows[(Y.N_SPARSE, COLD)]) > 0 and an.no_prior_items == len(Y.NO_PRIOR)
    drop = "IRT-T2N2-Q01"
    fit = {n: {p: tuple(r for r in rs if r[0] != drop) for p, rs in m.items()} for n, m in resp.fit.items()}
    an2 = analyze(iv, dataclasses.replace(resp, fit=fit), base, CFG, draws=0)
    assert sum(1 for rs in resp.eval[Y.N_SPARSE].values() for i, _ in rs if i == drop) >= 1
    n2 = sum(len(an2.rows[(Y.N_SPARSE, s)]) for s in (WARM, COLD))
    n1 = sum(len(an.rows[(Y.N_SPARSE, s)]) for s in (WARM, COLD))
    assert n2 == n1 - sum(1 for rs in resp.eval[Y.N_SPARSE].values() for i, _ in rs if i == drop)


def test_synthetic_identifiability_warnings(synthetic):
    rep = {it["item_id"]: it for blk in synthetic["report"]["blocks"] for it in blk["items"]}
    ext = rep[Y.truth()["extreme_item"]]
    assert not ext["disclosed"] and R.W_EXTREME in ext["warnings"] and isinstance(ext["n_approx"], int)
    sparse = [it for i, it in rep.items() if i.startswith("IRT-T2N2")]
    assert any(R.W_FEW in it["warnings"] and it["n_approx"] == INSUFFICIENT for it in sparse)
    assert any(R.W_EXTREME in it["warnings"] for it in sparse) and any(it["disclosed"] for it in sparse)
    for it in rep.values():
        assert it["disclosed"] == ("2pl" in it) == ("1pl" in it)
        if not it["disclosed"]:
            assert it["warnings"]


def test_bootstrap_intervals(synthetic):
    rep = synthetic["report"]
    n = 0
    for blk in rep["blocks"]:
        for it in blk["items"]:
            if it["disclosed"]:
                two = it["2pl"]
                assert two["difficulty_ci"][0] <= two["difficulty"] <= two["difficulty_ci"][1]
                assert two["discrimination_ci"][0] <= two["discrimination"] <= two["discrimination_ci"][1]
                n += two["difficulty_ci"][0] < two["difficulty_ci"][1]
    assert n >= 25
    b = synthetic["analysis"].blocks[0]
    assert len(b.boot) == CFG.bootstrap_draws
    again = M.bootstrap(b.data, M.structure("2pl", b.data.priors, CFG.fit), b.fits["2pl"], Q15, CFG.fit, 3, CFG.bootstrap_seed,
                        CFG.bootstrap_max_iterations)
    assert again == b.boot[:3]                                                  # 固定种子：与数据无关、逐次一致


# ---------------- 防泄露：取整、门槛与扰动 ----------------
def test_coarse_and_grid():
    assert R.coarse(0.234, 0.1, 0, "x", noisy=False) == 0.2 and R.coarse(-0.05, 0.1, 0, "x", noisy=False) == 0.0
    vals = {R.coarse(1.0, 0.1, set_key([f"syn-{i:a>10}"]), "s") for i in range(200)}
    assert vals == {0.8, 0.9, 1.0, 1.1, 1.2}
    assert R.grid_for(4.6, 480) == 0.01 and R.grid_for(1.0, 480) == 0.005 and R.grid_for(4.6, 20) == 0.1
    assert R.grid_for(1.0, 100) == 0.01 and R.grid_for(1.0, 1000) == 0.001
    for n in (20, 37, 99, 480, 2000):
        for spread in (1.0, 4.0, -math.log(CFG.prob_clip)):
            g = R.grid_for(spread, n)
            assert g >= spread / n - 1e-12 or g == R.GRIDS[-1]
    assert R.decimals(0.005) == 3 and R.decimals(0.1) == 1


def _bd_from_counts(right: int, wrong: int, offset: int = 0) -> M.BlockData:
    pids = [Y.pseudonym(offset, i) for i in range(right + wrong)]
    fit = {p: [("Q1", k < right)] for k, p in enumerate(pids)}
    return M.block_data("MATH.X.Y", ["Q1"], [0.5], fit)


def test_item_disclosure_threshold_keeps_margin():
    """扰动后人数 ≥ 24 且两格 ≥ 22 才给参数；扰动后落在门槛与门槛 + 余量之间的也只写“样本不足”"""
    from kge_irt.analysis import BlockResult
    iv = ItemsView("aya", "0.1.0", "d", {"Q1": Item("Q1", "MATH.X.Y", "apply", None, 0.5, None)}, frozenset({"MATH.X.Y"}))
    hit_n = hit_cell = moved = False
    for off in range(0, 60):
        for right, wrong in ((11, 11), (22, 22), (21, 30)):
            bd = _bd_from_counts(right, wrong, off)
            b = BlockResult("MATH.X.Y", bd, True, {}, {"1pl": [(1.0, 0.0)], "2pl": [(1.0, 0.0)]}, None)
            cells, key = R.item_cells(b, 0, True)
            assert key == set_key(bd.students) and R.item_cells(b, 0, False)[0] == (right, wrong)   # 表键 = 作答学生集合
            assert all(abs(c - t) <= NOISE_MAX for c, t in zip(cells, (right, wrong)))
            moved |= cells != (right, wrong)
            rec = R.item_record(b, 0, "Q1", iv, CFG, True)
            want_n = sum(cells) >= CFG.min_respondents + 2 * NOISE_MAX
            want = want_n and min(cells) >= CFG.min_outcome_cell + NOISE_MAX
            assert rec["disclosed"] == want and (rec["n_approx"] == INSUFFICIENT) == (not want_n)
            hit_n |= CFG.min_respondents <= sum(cells) < CFG.min_respondents + 2 * NOISE_MAX
            hit_cell |= CFG.min_outcome_cell <= min(cells) < CFG.min_outcome_cell + NOISE_MAX and want_n
            exact = R.item_record(b, 0, "Q1", iv, CFG, False)
            assert exact["disclosed"] == (right + wrong >= 20 and min(right, wrong) >= 20)
    assert hit_n and hit_cell and moved
    two = M.block_data("MATH.X.Y", ["Q1", "Q2"], [0.5, 0.5], {Y.pseudonym(9, 0): [("Q1", True)], Y.pseudonym(9, 1): [("Q2", False)],
                                                             Y.pseudonym(9, 2): [("Q1", False), ("Q2", True)]})
    assert sorted(two.respondents(0)) == sorted([Y.pseudonym(9, 0), Y.pseudonym(9, 2)])          # 不在表里的人不进表键


def test_noise_is_on_and_tracks_members(synthetic):
    d = synthetic
    exact = R.build_report(d["items"], d["responses"], d["analysis"], CFG, noisy=False)
    noisy = d["report"]
    moved = total = 0
    for bn, be in zip(noisy["blocks"], exact["blocks"]):
        for a, b in zip(bn["items"], be["items"]):
            if a["disclosed"] and b["disclosed"]:
                for kind in ("1pl", "2pl"):
                    total += 1
                    moved += a[kind]["difficulty"] != b[kind]["difficulty"]
    assert total >= 50 and moved >= total // 3
    for end in (0, 1):                      # 区间两端各自加扰动
        for name in ("difficulty_ci", "discrimination_ci"):
            diff = [a["2pl"][name][end] != b["2pl"][name][end] for bn, be in zip(noisy["blocks"], exact["blocks"])
                    for a, b in zip(bn["items"], be["items"]) if a["disclosed"] and b["disclosed"]]
            assert sum(diff) >= len(diff) // 5, (end, name)
    for k in (0, 1, "mean"):                # 配对差的均值与区间两端都加扰动
        diff = [(cn["log_loss_gain"] if k == "mean" else cn["gain_ci"][k]) != (ce["log_loss_gain"] if k == "mean" else ce["gain_ci"][k])
                for gn, ge in zip(noisy["evaluation"]["groups"], exact["evaluation"]["groups"]) if gn["disclosed"] and ge["disclosed"]
                for cn, ce in zip(gn["comparisons"], ge["comparisons"])]
        assert sum(diff) >= len(diff) // 5, k
    for k in R.METRICS:                     # 三个指标都加扰动
        assert any(gn["metrics"][m][k] != ge["metrics"][m][k] for gn, ge in zip(noisy["evaluation"]["groups"], exact["evaluation"]["groups"])
                   for m in METHODS if gn["disclosed"] and ge["disclosed"]), k
    b = d["analysis"].blocks[0]
    resp = b.data.respondents(0)
    assert set_key(resp) != set_key(resp[1:]) and set_key(resp) == set_key(list(reversed(resp)))
    for bn, blk in zip(noisy["blocks"], d["analysis"].blocks):      # 1PL 共同区分度按块表键加扰动
        key = set_key(blk.data.students)
        want = R.coarse(blk.params["1pl"][0][0], CFG.discrimination_step, key, f"irt/{blk.node}/1pl/a")
        assert bn["models"]["1pl"]["common_discrimination"] == want
    assert any(noise(set_key(blk.data.students), f"irt/{blk.node}/1pl/a") != 0 for blk in d["analysis"].blocks)
    for bn, be in zip(noisy["blocks"], exact["blocks"]):
        for a, b2 in zip(bn["items"], be["items"]):
            if a["disclosed"] and b2["disclosed"] and a["difficulty_step"] == b2["difficulty_step"]:
                s = a["difficulty_step"]
                d1 = round((a["1pl"]["difficulty"] - b2["1pl"]["difficulty"]) / s)
                d2 = round((a["2pl"]["difficulty"] - b2["2pl"]["difficulty"]) / s)
                moved += 1000 * (d1 != d2)                                  # 1PL 与 2PL 难度各自独立扰动
    assert moved >= 1000


def test_counts_are_banded_and_block_fit_uses_noisy_count(synthetic):
    from kge_irt.analysis import block_enough, block_students_noisy
    for blk in synthetic["report"]["blocks"]:
        for it in blk["items"]:
            assert it["n_approx"] == INSUFFICIENT or it["n_approx"] % 10 == 0
        assert blk["students_approx"] == INSUFFICIENT or blk["students_approx"] % 10 == 0
    assert synthetic["report"]["cohort"]["students_approx"] == 480
    bd = small_block()
    assert block_students_noisy(bd) - len(bd.students) in range(-NOISE_MAX, NOISE_MAX + 1)
    assert block_enough(bd, CFG) == (block_students_noisy(bd) >= CFG.fit.min_block_students + NOISE_MAX)
    assert not block_enough(bd, CFG)            # 40 人 < 50 + 2
    hit = moved = False
    for off in range(80):
        b2 = _bd_from_counts(25, 26, off)
        n = block_students_noisy(b2)
        moved |= n != 51
        assert block_enough(b2, CFG) == (n >= CFG.fit.min_block_students + NOISE_MAX)
        hit |= CFG.fit.min_block_students <= n < CFG.fit.min_block_students + NOISE_MAX
    assert hit and moved


def test_eval_group_threshold_and_auc_share():
    hit = moved = False
    for off in range(60):
        rows = [(Y.pseudonym(100 + off, i), i % 2 == 0, {m: 0.5 for m in METHODS}) for i in range(21)]
        rec = R.eval_record("g", "s", rows, CFG, True)
        key = set_key(sorted({r[0] for r in rows}))
        n = 21 + noise(key, "irt/eval/g/s/n")
        assert rec["disclosed"] == (n >= CFG.min_eval_students + NOISE_MAX)
        assert rec["students_approx"] == (n_approx(n) if rec["disclosed"] else INSUFFICIENT)
        hit |= CFG.min_eval_students <= n < CFG.min_eval_students + NOISE_MAX
        moved |= n >= CFG.min_eval_students + NOISE_MAX
    assert hit and moved
    rows = [(Y.pseudonym(2, i), i % 10 == 0, {m: 0.5 + 0.001 * i for m in METHODS}) for i in range(200)]
    rec = R.eval_record("g", "s", rows, CFG, True)
    assert rec["disclosed"] and all(rec["metrics"][m]["auc"] == INSUFFICIENT for m in METHODS)   # 正例占比 0.1 < 0.25
    assert rec["steps"]["log_loss"] >= -math.log(CFG.prob_clip) / 200


def test_auc_share_threshold_uses_noisy_weights_at_boundary():
    """正例占比恰在门槛（0.25）：是否给 AUC 只由扰动后的两类按人加权总量决定（留余量），精确占比恰好达标也可能不给"""
    shown = hidden = 0
    for off in range(80):
        rows = [(Y.pseudonym(300 + off, i), i < 50, {m: 0.3 + 0.002 * i for m in METHODS}) for i in range(200)]
        rec = R.eval_record("g", "s", rows, CFG, True)
        key = set_key(sorted({r[0] for r in rows}))
        wp = 50 + noise(key, "irt/eval/g/s/auc/pos")
        wn = 150 + noise(key, "irt/eval/g/s/auc/neg")
        want = min(wp, wn) >= R.AUC_MIN_SHARE * (wp + wn) + NOISE_MAX
        assert (rec["metrics"]["bkt"]["auc"] != INSUFFICIENT) == want
        shown += want
        hidden += not want
    assert hidden > 0                                                    # 精确占比 = 0.25 时多数不给
    rows = [(Y.pseudonym(5, i), i % 2 == 0, {m: 0.5 for m in METHODS}) for i in range(200)]
    assert R.eval_record("g", "s", rows, CFG, True)["metrics"]["bkt"]["auc"] != INSUFFICIENT


def _points(values: dict[str, float], n: int = 100) -> tuple[list[str], dict[str, dict[str, tuple[float, float, int]]]]:
    students = [Y.pseudonym(8, i) for i in range(n)]
    rng = random.Random(1)
    jitter = {s: rng.uniform(-0.01, 0.01) for s in students}
    return students, {m: {s: (v + (jitter[s] if m == "bkt" else 0.0), 0.0, 1) for s in students} for m, v in values.items()}


def test_comparisons_paired_and_published():
    vals = dict(zip(METHODS, (0.80, 0.70, 0.66, 0.60, 0.59, 0.57)))
    students, pts = _points(vals)
    out = {(c["irt"], c["versus"]): c for c in R.comparisons("g", "s", students, pts, 0, 0.01, CFG, False)}
    assert out[("irt_2pl", "bkt")]["verdict"] == R.BETTER and out[("irt_2pl", "bkt")]["log_loss_gain"] == pytest.approx(0.23, abs=0.01)
    assert out[("irt_2pl", "irt_1pl")]["verdict"] == R.BETTER                     # 0.02 ≥ 门槛 0.02，区间不含 0
    assert out[("irt_1pl", "prior_ability")]["verdict"] == R.SIMILAR             # 0.01 < 门槛
    assert out[("irt_1pl", "bkt_platt")]["gain_ci"][0] > 0
    for c in out.values():
        assert c["gain_ci"][0] <= c["log_loss_gain"] <= c["gain_ci"][1]
    vals["irt_1pl"] = 0.75
    students, pts = _points(vals)
    out = {(c["irt"], c["versus"]): c["verdict"] for c in R.comparisons("g", "s", students, pts, 0, 0.01, CFG, False)}
    assert out[("irt_1pl", "prior_only")] == R.WORSE
    # 配对：两组均值相同但逐人差异正负各半 → 区间含 0，不下结论
    students = [Y.pseudonym(9, i) for i in range(100)]
    pts = {m: {s: (0.6, 0.0, 1) for s in students} for m in METHODS}
    pts["irt_2pl"] = {s: (0.6 + (0.3 if i % 2 else -0.3), 0.0, 1) for i, s in enumerate(students)}
    out = {(c["irt"], c["versus"]): c for c in R.comparisons("g", "s", students, pts, 0, 0.01, CFG, False)}
    assert out[("irt_2pl", "bkt")]["verdict"] == R.SIMILAR and out[("irt_2pl", "bkt")]["gain_ci"][0] < 0 < out[("irt_2pl", "bkt")]["gain_ci"][1]
    # 均值超过门槛但配对区间含 0：仍不下结论
    pts["irt_2pl"] = {s: (0.55 + (0.6 if i % 2 else -0.6), 0.0, 1) for i, s in enumerate(students)}
    c = {(c["irt"], c["versus"]): c for c in R.comparisons("g", "s", students, pts, 0, 0.01, CFG, False)}[("irt_2pl", "bkt")]
    assert c["log_loss_gain"] >= CFG.min_logloss_gain and c["gain_ci"][0] < 0 and c["verdict"] == R.SIMILAR


def test_prior_mismatch_needs_residual_and_interval():
    """按节点：残差够大且自助法区间不覆盖直线上的值才列候选；区间覆盖时不列"""
    def it(i, prior, b, ci):
        return {"item_id": f"Q{i}", "prior_difficulty": prior, "disclosed": True, "2pl": {"difficulty": b, "difficulty_ci": ci}}
    items = [it(i, 0.1 * i, -2.0 + 0.5 * i, [-2.1 + 0.5 * i, -1.9 + 0.5 * i]) for i in range(1, 8)]
    items.append(it(8, 0.35, 2.0, [1.9, 2.1]))                 # 残差大、区间窄 → 候选
    items.append(it(9, 0.45, -1.5, [-3.0, 3.0]))               # 残差大、区间宽到覆盖直线 → 不列
    items.append(it(10, 0.55, 2.0, [-0.5, 2.5]))               # 残差 > 1、区间覆盖 → 不列
    out = R.prior_mismatch([{"node": "N", "items": items}], CFG)["by_node"][0]
    assert [c["item_id"] for c in out["candidates"]] == ["Q8"] and out["line"]["slope"] == pytest.approx(5.0, abs=0.5)
    items[7]["2pl"]["difficulty"] = -2.0                        # 负残差同样列出
    items[7]["2pl"]["difficulty_ci"] = [-2.1, -1.9]
    out = R.prior_mismatch([{"node": "N", "items": items}], CFG)["by_node"][0]
    assert [c["item_id"] for c in out["candidates"]] == ["Q8"]
    few = R.prior_mismatch([{"node": "N", "items": items[:3]}], CFG)["by_node"][0]
    assert few["line"] == INSUFFICIENT and few["candidates"] == []


def test_not_converged_warning_only_when_block_count_disclosed():
    from kge_irt.analysis import BlockResult, block_students_noisy
    iv = ItemsView("aya", "0.1.0", "d", {"Q1": Item("Q1", "MATH.X.Y", "apply", None, 0.5, None)}, frozenset({"MATH.X.Y"}))
    nc = M.Fit("1pl", (0.0, 0.0), False, 5, (0.0,), 0.0)
    seen = set()
    for off in range(40):
        bd = _bd_from_counts(10, 10, off)
        b = BlockResult("MATH.X.Y", bd, True, {"1pl": nc, "2pl": nc, "prior": nc}, {"1pl": [(1.0, 0.0)], "2pl": [(1.0, 0.0)]}, None)
        rec = R.block_record(b, iv, CFG, True)
        shown = block_students_noisy(bd) >= CFG.min_respondents + NOISE_MAX
        assert any(R.W_NOT_CONVERGED in w for w in rec["warnings"]) == shown
        seen.add(shown)
    assert seen == {True, False}


def test_difficulty_grid_coarsens_for_small_or_flat_items():
    fine = R.difficulty_grid(270, 135, 1.0, CFG)
    assert fine == CFG.difficulty_step
    assert R.difficulty_grid(44, 22, 0.5, CFG) > fine                       # 人少、区分度低 → 更粗
    assert R.difficulty_grid(44, 22, 0.1, CFG) >= R.difficulty_grid(44, 22, 0.5, CFG)
    for n, x, a in ((24, 12, 1.0), (60, 50, 0.8), (300, 290, 2.0), (44, 22, 3.0)):
        g = R.difficulty_grid(n, x, a, CFG)
        p = min(max(x / n, 1 / n), 1 - 1 / n)
        assert g >= min(2.0 / (max(a, CFG.fit.a_lo) * n * p * (1 - p)), 10 * CFG.difficulty_step) - 1e-9
        assert round(g / CFG.difficulty_step) in R.DIFFICULTY_MULTIPLES


# ---------------- 差分：删一人重跑 ----------------
def _without(d: dict[str, Any], pid: str) -> tuple[Responses, Any]:
    r = d["responses"]
    drop = lambda seg: {n: {p: v for p, v in m.items() if p != pid} for n, m in seg.items()}   # noqa: E731
    resp = dataclasses.replace(r, fit=drop(r.fit), eval=drop(r.eval), pseudonyms=r.pseudonyms - {pid})
    base = d["baseline"]
    base = dataclasses.replace(base, p_mastery={n: {p: v for p, v in m.items() if p != pid} for n, m in base.p_mastery.items()},
                               pseudonyms=base.pseudonyms - {pid})
    return resp, base


def _shown(n: int, e: int, floor: int) -> int | str:
    m = n + e
    return INSUFFICIENT if m < floor else n_approx(m)


def n_candidates(s0: Any, s1: Any, floor: int, member: bool) -> set[int]:
    """与删前、删后两次公开人数都相容的真实人数（攻击者知道扰动分布与取整规则，不知道表键；在表里时两次扰动独立）"""
    noises = range(-NOISE_MAX, NOISE_MAX + 1)
    out = set()
    for n0 in range(1, 2000):
        n1 = n0 - 1 if member else n0
        if member:
            ok = any(_shown(n0, e, floor) == s0 for e in noises) and any(_shown(n1, e, floor) == s1 for e in noises)
        else:
            ok = any(_shown(n0, e, floor) == s0 and _shown(n1, e, floor) == s1 for e in noises)
        if ok:
            out.add(n0)
    return out


def consistent_with(pub: dict[str, Any], exact_1pl: float, exact_2pl: float, shared: bool = False) -> bool:
    """强攻击者（知道其余所有人的作答与工具源码、不知道表键）：某个假设下的精确难度能否产生这份公开记录。
    1PL、2PL 难度各有独立扰动 δ ∈ [−2, 2]，两者都要能解释；shared=True 时按“共用一个 δ”的设计判（只用于对照）"""
    s = pub["difficulty_step"]
    p1, p2 = round(pub["1pl"]["difficulty"] / s), round(pub["2pl"]["difficulty"] / s)
    e1, e2 = round(exact_1pl / s), round(exact_2pl / s)
    if shared:
        return any(e1 + dl == p1 and e2 + dl == p2 for dl in range(-NOISE_MAX, NOISE_MAX + 1))
    return abs(p1 - e1) <= NOISE_MAX and abs(p2 - e2) <= NOISE_MAX


def flipped_difficulties(d: dict[str, Any], b: Any, pid: str, item: str) -> tuple[float, float]:
    """把此人在这道题上的对错反过来、其余所有人不变，重拟合本块（从原估计出发）：反事实下的精确 1PL / 2PL 难度"""
    cfg = d["cfg"]
    fit = dict(d["responses"].fit[b.node])
    fit[pid] = tuple((i, (not x) if i == item else x) for i, x in fit[pid])
    bd = M.block_data(b.node, b.data.items, b.data.priors, fit)
    j = b.data.items.index(item)
    out = []
    for kind in ("1pl", "2pl"):
        st = M.structure(kind, bd.priors, cfg.fit)
        f = M.fit_em(bd, st, Q15, cfg.fit, phi0=b.fits[kind].phi)
        out.append(M.item_params(st, f.phi)[j][1])
    return out[0], out[1]


@pytest.fixture(scope="module")
def differential():
    d = load_synthetic(draws=8)
    cfg = dataclasses.replace(d["cfg"], bootstrap_draws=8, compare_draws=50)
    d["cfg"] = cfg
    d["r0"] = R.build_report(d["items"], d["responses"], d["analysis"], cfg)
    return d


def _victims(d: dict[str, Any]) -> list[str]:
    """三名学生：两名答过小样本题（人数刚过门槛、单人影响最大），一名按伪名排序的第一个"""
    sparse = [it["item_id"] for blk in d["r0"]["blocks"] if blk["node"] == Y.N_SPARSE for it in blk["items"] if it["disclosed"]]
    fit = d["responses"].fit[Y.N_SPARSE]
    hit = sorted(p for p, rs in fit.items() if any(i in sparse for i, _ in rs))
    return [hit[0], hit[-1], sorted(d["responses"].pseudonyms)[0]]


def test_differential_deletion_cannot_recover_individual(differential):
    """安全（同 KGE-09 B1，强攻击者模型）：删一人重跑两次，攻击者知道其余所有人的精确作答。逐条记录：
    - 题目人数：与两次输出相容的真实人数至少两种；
    - 评估指标：由两次输出解出的“此人的平均对数损失 / Brier”区间覆盖取值范围的 90% 以上；此人对 AUC 的真实影响不超过扰动范围；
    - 难度见 test_strong_attacker_cannot_pin_difficulty。"""
    d = differential
    cfg = d["cfg"]
    r0 = d["r0"]
    items0 = {(blk["node"], it["item_id"]): it for blk in r0["blocks"] for it in blk["items"]}
    ok_checks = auc_checks = 0
    for pid in _victims(d):
        resp1, base1 = _without(d, pid)
        an1 = analyze(d["items"], resp1, base1, cfg)
        r1 = R.build_report(d["items"], resp1, an1, cfg)
        items1 = {(blk["node"], it["item_id"]): it for blk in r1["blocks"] for it in blk["items"]}
        for key, it0 in items0.items():
            it1 = items1[key]
            node, item = key
            member = any(i == item for i, _ in d["responses"].fit.get(node, {}).get(pid, ()))
            if it0["n_approx"] != INSUFFICIENT or it1["n_approx"] != INSUFFICIENT:
                assert len(n_candidates(it0["n_approx"], it1["n_approx"], cfg.min_respondents + 2 * NOISE_MAX, member)) >= 2, key
                ok_checks += 1
        e1 = {(g["group"], g["student_group"]): g for g in r1["evaluation"]["groups"]}
        for g0 in r0["evaluation"]["groups"]:
            gk = (g0["group"], g0["student_group"])
            g1 = e1.get(gk)
            rows = d["analysis"].rows[gk]
            n0 = len({p for p, _, _ in rows})
            if pid not in {p for p, _, _ in rows} or not (g0["disclosed"] and g1 and g1["disclosed"]):
                continue
            for m in METHODS:
                for metric, spread in (("log_loss", -math.log(cfg.prob_clip)), ("brier", 1.0)):
                    s0, s1 = g0["steps"][metric], g1["steps"][metric]
                    h0, h1 = (NOISE_MAX + 0.5) * s0, (NOISE_MAX + 0.5) * s1
                    lo = n0 * (g0["metrics"][m][metric] - h0) - (n0 - 1) * (g1["metrics"][m][metric] + h1)
                    hi = n0 * (g0["metrics"][m][metric] + h0) - (n0 - 1) * (g1["metrics"][m][metric] - h1)
                    cover = max(0.0, min(hi, spread) - max(lo, 0.0)) / spread
                    assert cover >= 0.9, (gk, m, metric, lo, hi)
                    ok_checks += 1
                if g0["metrics"][m]["auc"] != INSUFFICIENT:
                    sub0 = [(p, y, pr[m]) for p, y, pr in rows]
                    sub1 = [r for r in sub0 if r[0] != pid]
                    a0 = X.weighted_auc(X.per_student_auc_rows(sub0)[0])
                    a1 = X.weighted_auc(X.per_student_auc_rows(sub1)[0])
                    assert abs(a0 - a1) <= NOISE_MAX * g0["steps"]["auc"], (gk, m)   # 单人影响在扰动范围内
                    auc_checks += 1
    assert ok_checks >= 200 and auc_checks >= 30


def test_strong_attacker_cannot_pin_difficulty(differential):
    """S1：知道其余所有人作答的攻击者，对此人在某题上的“答对 / 答错”逐一假设、精确重算该题难度（计入真实位移），
    看哪些假设能由同一个扰动 δ 产生公开的 1PL / 2PL 难度。真实情况必须相容；只有真实情况相容（被定格）的检查不超过 10%"""
    d = differential
    blocks = {b.node: b for b in d["analysis"].blocks}
    items0 = {(blk["node"], it["item_id"]): it for blk in d["r0"]["blocks"] for it in blk["items"]}
    checks = pinned = 0
    for pid in _victims(d)[:2]:
        for node, b in blocks.items():
            for item, x in d["responses"].fit.get(node, {}).get(pid, ()):
                pub = items0[(node, item)]
                if not pub["disclosed"]:
                    continue
                j = b.data.items.index(item)
                truth = (b.params["1pl"][j][1], b.params["2pl"][j][1])
                assert consistent_with(pub, *truth), (node, item)
                alt = flipped_difficulties(d, b, pid, item)
                checks += 1
                pinned += not consistent_with(pub, *alt)
    assert checks >= 30 and pinned <= 0.1 * checks, (pinned, checks)


def test_differential_without_noise_would_pin():
    """对照：没有扰动时，人数跨门槛（24 → 23）能唯一定出真实人数；难度只取整不加扰动时，单人影响跨过半格就能定出对错——
    说明上面的断言不是空转"""
    floor = CFG.min_respondents + 2 * NOISE_MAX
    no_noise = {n0 for n0 in range(1, 400) if _shown(n0, 0, floor) == 20 and _shown(n0 - 1, 0, floor) == INSUFFICIENT}
    assert no_noise == {24}
    assert len(n_candidates(20, INSUFFICIENT, floor, True)) >= 2
    pub = {"difficulty_step": 0.1, "1pl": {"difficulty": 0.3}, "2pl": {"difficulty": 0.3}}
    assert consistent_with(pub, 0.3, 0.3) and consistent_with(pub, 0.12, 0.12)       # 有扰动：相差 2 格仍相容
    assert consistent_with(pub, 0.3, 0.2) and not consistent_with(pub, 0.3, 0.2, shared=True)   # 共用扰动会公开两者之差
    exact = lambda v: round(v / 0.1) == round(0.3 / 0.1)                                # noqa: E731  不加扰动：差一格就分得出
    assert exact(0.3) and not exact(0.24)


def test_absent_member_keeps_noise(differential):
    """不在表里的人删掉，表键不变、扰动相同：这类题的难度差至多一格（只来自块内耦合的间接影响）"""
    d = differential
    pid = _victims(d)[0]
    resp1, base1 = _without(d, pid)
    r1 = R.build_report(d["items"], resp1, analyze(d["items"], resp1, base1, d["cfg"]), d["cfg"])
    items1 = {it["item_id"]: it for blk in r1["blocks"] for it in blk["items"]}
    seen = 0
    for blk in d["r0"]["blocks"]:
        for it in blk["items"]:
            mine = [i for i, _ in d["responses"].fit.get(blk["node"], {}).get(pid, ())]
            if it["item_id"] in mine or not (it["disclosed"] and items1[it["item_id"]]["disclosed"]):
                continue
            assert it["n_approx"] == items1[it["item_id"]]["n_approx"]
            if it["difficulty_step"] != items1[it["item_id"]]["difficulty_step"]:
                continue
            for kind in ("1pl", "2pl"):
                D = round((it[kind]["difficulty"] - items1[it["item_id"]][kind]["difficulty"]) / it["difficulty_step"])
                assert abs(D) <= 1
            seen += 1
    assert seen >= 5


# ---------------- 输出：伪名、个人层字段 ----------------
def test_pseudonyms_and_student_values_never_in_output(synthetic):
    files = synthetic["files"]
    assert set(files) == set(R.OUTPUT_NAMES)
    for text in files.values():
        assert not re.search(r"(syn|pil)-[a-z]{10,24}\b", text)            # 没有伪名形状的字串（题型编码 syn-choice 不是伪名）
        for pid in sorted(synthetic["responses"].pseudonyms | synthetic["baseline"].pseudonyms):
            assert pid not in text
    keys: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            keys.update(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(files[R.REPORT_NAME]))
    assert not keys & {"theta", "ability", "posterior", "pseudonym", "students", "n", "count", "responses", "seq", "fit_fraction",
                       "fit_fraction_approx"}
    assert json.loads(files[R.REPORT_NAME])["not_a_source_of_truth"] is True                  # 契约 B2
    assert not re.search(r"(?<![\d.])\d+\.\d{4,}", files[R.REPORT_NAME])          # 没有 4 位以上的小数
    assert not re.search(r"(?<![\d.])\d+\.\d{4,}", files[R.MD_NAME])


def test_leak_check_refuses(synthetic):
    with pytest.raises(R.OutputRefused):
        R.assert_no_pseudonyms({"irt-report.md": "x syn-abcdefghij y"}, ["syn-abcdefghij"])
    d = synthetic
    fake = dataclasses.replace(d["responses"], pseudonyms=d["responses"].pseudonyms | {"IRT-T1N1-Q01"})
    with pytest.raises(R.OutputRefused):
        R.run(d["items"], fake, d["analysis"], CFG)                 # 伪装成伪名的字串出现在输出里 → 拒绝
    with pytest.raises(R.OutputRefused):
        R.run(d["items"], d["responses"], d["analysis"], CFG, ["MATH.IRT.T1.N1"])   # 基线的伪名同样检查


def test_report_shape_and_notice(synthetic):
    rep = synthetic["report"]
    assert rep["notice"] == SYNTHETIC_NOTICE and rep["config"]["signoff"] == PENDING
    assert rep["items_snapshot"]["items"] == 36 and rep["items_snapshot"]["snapshot_digest"] == synthetic["items"].digest
    assert [b["node"] for b in rep["blocks"]] == sorted(Y.NODES)
    assert [(g["group"], g["student_group"]) for g in rep["evaluation"]["groups"]] == \
        [(ALL, ANY), (ALL, WARM), (ALL, COLD)] + [(n, s) for n in sorted(Y.NODES) for s in (WARM, COLD)]
    assert rep["cohort"]["fit_fraction_range"] == [CFG.min_fit_fraction, CFG.max_fit_fraction]
    assert rep["summary"]["items"] == 36 and rep["summary"]["items_with_parameters"] >= 30
    assert rep["evaluation"]["items_without_prior"] == len(Y.NO_PRIOR)
    assert rep["not_a_source_of_truth"] is True and "kge.item_tag" in rep["usage"] and "KGE-02" in rep["usage"]
    assert rep["config"]["baseline_config"]["source"] == CFG.baseline_source
    nonsyn = dataclasses.replace(synthetic["responses"], synthetic=False)
    nonsyn_iv = dataclasses.replace(synthetic["items"], synthetic=False)
    assert R.build_report(nonsyn_iv, nonsyn, synthetic["analysis"], CFG)["notice"] == ""
    assert R.build_report(nonsyn_iv, synthetic["responses"], synthetic["analysis"], CFG)["notice"] == SYNTHETIC_NOTICE
    assert synthetic["responses"].synthetic and synthetic["baseline"].synthetic        # 取自元数据行
    assert meta_synthetic("# format=x synthetic=true subject=aya\n") and not meta_synthetic("# format=x synthetic=false\n")
    assert not meta_synthetic("pseudonym,x\n")


def test_markdown_first_paragraph_and_sections(synthetic):
    md = synthetic["files"][R.MD_NAME]
    paras = [p for p in md.split("\n\n") if p.strip()]
    assert paras[0].startswith("# ") and paras[1].startswith(f"**{SYNTHETIC_NOTICE}。**")
    for h in ("## 一、预测对照", "## 二、题目参数", "## 三、先验难度与 IRT 难度", "## 四、可识别性告警", "## 五、口径与限制"):
        assert h in md
    assert PENDING in md and "不改掌握计算" in md and "irt-report.json" in md
    assert paras[2].startswith("> **本报表不是真相源") and "候选仅供教研参考" in md and "KGE-02" in md
    for item in Y.MISLABELED:
        assert f"  - {item}：" in md
    assert "- [ ]" not in md and "确认先验难度需复核修改" not in md                 # 不出勾选口径
    assert "自助法区间可能偏窄" in md and "未校准的 BKT 基线" in md and "冷启动" in md


def test_byte_identical(synthetic):
    """同一输入逐字节一致：生成器固定种子；独立重跑一遍分析见 test_cli_end_to_end（命令行输出与本会话的报表逐字节相同）"""
    d = synthetic
    again = R.run(d["items"], d["responses"], d["analysis"], d["cfg"], d["baseline"].pseudonyms)
    assert again == synthetic["files"] and again[R.REPORT_NAME].endswith("}\n")
    assert Y.render_inputs() == synthetic["inputs"]


def test_matches_committed_sample(synthetic):
    """样例只放 irt-report.md；JSON 用 sha256 固定（同 PLT-17）。样例过期时按 Makefile 注释重生，并更新 SAMPLE_JSON_SHA256"""
    assert sorted(p.name for p in SAMPLE.iterdir()) == [R.MD_NAME]
    assert synthetic["files"][R.MD_NAME] == (SAMPLE / R.MD_NAME).read_text(encoding="utf-8"), "样例过期"
    assert hashlib.sha256(synthetic["files"][R.REPORT_NAME].encode("utf-8")).hexdigest() == SAMPLE_JSON_SHA256


# ---------------- 输入守卫 ----------------
def _items_doc(**kw) -> dict[str, Any]:
    doc = json.loads(Y.items_json())
    doc.update(kw)
    return doc


def test_items_guards():
    iv = parse_items(_items_doc())
    assert len(iv.items) == 36 and iv.nodes == frozenset(Y.NODES)
    bad = [_items_doc(synthetic=False), _items_doc(format="x"), _items_doc(subject="xx"), _items_doc(map_version="1.0"),
           _items_doc(items=[]), {**_items_doc(), "extra": 1}]
    base = _items_doc()["items"]
    for change in ({"item_id": "123e4567-e89b-12d3-a456-426614174000"}, {"item_id": "题目一"}, {"node_id": "CHN.A.B"},
                   {"node_id": "bad"}, {"prior_difficulty": 1.2}, {"prior_difficulty": 0.12345}, {"prior_difficulty": True},
                   {"family_code": "族"}, {"extra": 1}, {"item_id": "13812345678"}, {"family_code": "13812345678"},
                   {"item_type": "13812345678"}, {"item_type": "a@b.cn"}, {"family_code": "x110101199001011234"},
                   {"cognitive_level": "memorize"}, {"cognitive_level": None}):
        items = [dict(base[0], **change)] + base[1:]
        bad.append(_items_doc(items=items))
    bad.append(_items_doc(items=[base[0], base[0]]))
    for doc in bad:
        with pytest.raises(InputRefused):
            parse_items(doc)
    ok = _items_doc(items=[dict(base[0], prior_difficulty=None, family_code=None, item_type=None)] + base[1:])
    it = parse_items(ok).items[base[0]["item_id"]]
    assert it.prior is None and it.family_code is None and it.item_type is None and it.cognitive_level in ("understand", "apply")


def test_load_items_file_guards(tmp_path):
    p = tmp_path / "items.json"
    p.write_text(Y.items_json(), encoding="utf-8")
    assert len(load_items(p).items) == 36
    with pytest.raises(InputRefused):
        load_items(tmp_path)                        # 不是普通文件
    q = tmp_path / "items.yaml"
    q.write_text("x", encoding="utf-8")
    with pytest.raises(InputRefused):
        load_items(q)
    r = tmp_path / "bad.json"
    r.write_text("{", encoding="utf-8")
    with pytest.raises(InputRefused):
        load_items(r)


def _resp_text() -> str:
    return Y.render_inputs()["responses.csv"]


def _refused_resp(text: str, iv) -> None:
    with pytest.raises(InputRefused):
        parse_responses(text, iv, CFG.min_fit_fraction, CFG.max_fit_fraction)


def test_response_guards(synthetic):
    iv = synthetic["items"]
    text = synthetic["inputs"]["responses.csv"]
    head, cols, first, rest = text.split("\n", 3)
    pid, item, node, corr, seq, seg = first.split(",")
    _refused_resp(text.replace("synthetic=true", "synthetic=false", 1), iv)
    _refused_resp(text.replace("subject=aya", "subject=wenli", 1), iv)
    _refused_resp(text.replace("format=kge-irt-responses/1", "format=x", 1), iv)
    _refused_resp("\n".join([head, cols + ",name", first + ",x", rest]), iv)               # 个人信息列名
    _refused_resp("\n".join([head, cols + ",extra", first + ",x", rest]), iv)              # 不认识的列
    for row in (f"syn-ABCDEFGHIJ,{item},{node},{corr},{seq},{seg}", f"pil-abcdefghij,{item},{node},{corr},{seq},{seg}",
                f"{pid},13812345678,{node},{corr},{seq},{seg}", f"{pid},NOPE-ITEM,{node},{corr},{seq},{seg}",
                f"{pid},{item},MATH.IRT.T1.N2,{corr},{seq},{seg}" if node != "MATH.IRT.T1.N2" else f"{pid},{item},MATH.IRT.T1.N1,{corr},{seq},{seg}",
                f"{pid},{item},{node},2,{seq},{seg}", f"{pid},{item},{node},{corr},-1,{seg}", f"{pid},{item},{node},{corr},1.5,{seg}",
                f"{pid},{item},{node},{corr},{seq},train", f"{pid},{item},{node},{corr},{seq},{seg},x"):
        _refused_resp("\n".join([head, cols, row, rest]), iv)
    second, after = rest.split("\n", 1)
    seq2 = second.split(",")[4]
    _refused_resp("\n".join([head, cols, first, ",".join([pid, item, node, corr, seq2, seg]), after]), iv)    # 同一伪名同一题两行
    _refused_resp("\n".join([head, cols, first, second.rsplit(",", 2)[0] + f",{seq}," + second.rsplit(",", 1)[1],
                             rest.split("\n", 1)[1]]), iv)                                              # seq 重复
    _refused_resp("\n".join([head, cols, first.rsplit(",", 1)[0] + ",eval", rest]), iv)                 # fit / eval 交错
    _refused_resp("\n".join([head, cols]), iv)
    with pytest.raises(InputRefused):
        parse_responses(text, iv, 0.75, 0.9)                                                            # 拟合比例超出配置范围


def test_baseline_guards(synthetic):
    iv, resp = synthetic["items"], synthetic["responses"]
    text = synthetic["inputs"]["baseline.csv"]
    head, cols, first, rest = text.split("\n", 3)
    pid, node, level, p = first.split(",")
    assert cols == "pseudonym,node_id,cognitive_level,p_mastery"
    parse_baseline(text, iv, resp)
    other_level = "apply" if level != "apply" else "understand"
    two = parse_baseline("\n".join([head, cols, first, f"{pid},{node},{other_level},{p}"]), iv, resp)
    assert len(two.p_mastery) == 2                                              # 同一节点不同认知层次是不同单元
    for t in (text.replace("synthetic=true", "synthetic=false", 1), text.replace("format=kge-irt-baseline/1", "format=x", 1),
              "\n".join([head, cols, f"{pid},{node},{level},1.5", rest]), "\n".join([head, cols, f"{pid},{node},{level},nan", rest]),
              "\n".join([head, cols, first, first, rest]), "\n".join([head, cols + ",phone", first + ",1", rest]),
              "\n".join([head, cols, f"{pid},MATH.NOPE.X1,{level},{p}", rest]), "\n".join([head, cols, f"{pid},{node},memorize,{p}", rest]),
              "\n".join([head, "pseudonym,node_id,p_mastery", f"{pid},{node},{p}"])):
        with pytest.raises(InputRefused):
            parse_baseline(t, iv, resp)
    other = dataclasses.replace(resp, pseudonyms=frozenset({"syn-zzzzzzzzzz"}))
    with pytest.raises(InputRefused):
        parse_baseline(text, iv, other)                                                                  # 与作答表伪名不相交


# ---------------- 配置守卫 ----------------
def _cfg(path: str, value: Any) -> dict[str, Any]:
    doc = json.loads(json.dumps(CONFIG_DOC))
    cur = doc
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value
    return doc


def test_config_guards():
    parse_config(CONFIG_DOC)
    assert CFG.signoff == PENDING and "待教研确认" in CONFIG_DOC["$comment"]
    for path, value in (("disclosure.min_respondents", 19), ("disclosure.min_outcome_cell", 10), ("disclosure.min_eval_students", 5),
                        ("fit.min_block_students", 10), ("blocking.unit", "topic"), ("blocking.share_family_parameters", True),
                        ("fit.difficulty_bounds", [0.5, 4.0]), ("fit.discrimination_bounds", [1.2, 4.0]), ("fit.difficulty_bounds", [2.0, 1.0]),
                        ("baseline.source", "docs/x.json"), ("baseline.source", "services/learning/config/mastery/../mastery/synthetic-test.json"),
                        ("baseline.source", "services/learning/config/mastery/nope.json"), ("baseline.p_slip", 0.1),
                        ("split.min_fit_fraction", 0.95), ("schema_version", "x"), ("signoff", " "), ("fit.quadrature_points", 3),
                        ("bootstrap.draws", 5), ("comparison.bootstrap_draws", 5), ("disclosure.difficulty_step", 0.01), ("extra", 1),
                        ("fit.extra", 1)):
        with pytest.raises(ConfigError):
            parse_config(_cfg(path, value))
    with pytest.raises(ConfigError):          # 草案掌握配置（bkt 为空）不能用作基线
        parse_config(_cfg("baseline.source", "services/learning/config/mastery/aya.draft.json"))
    assert parse_config(_cfg("fit.tolerance", 0.0001)).fingerprint != CFG.fingerprint


def test_baseline_reference_guards():
    src = json.loads((REPO / CFG.baseline_source).read_text(encoding="utf-8"))
    ref = parse_baseline_ref("services/learning/config/mastery/x.json", src)
    assert ref == dataclasses.replace(CFG.bkt, source="services/learning/config/mastery/x.json")
    bad = json.loads(json.dumps(src))
    bad["bkt"]["p_slip"] = 0.9
    with pytest.raises(ConfigError):
        parse_baseline_ref("s", bad)                         # p_slip + p_guess 必须小于 1
    for k, v in (("p_init", None), ("p_learn", 1.5)):
        bad = json.loads(json.dumps(src))
        bad["bkt"][k] = v
        with pytest.raises(ConfigError):
            parse_baseline_ref("s", bad)
    bad = json.loads(json.dumps(src))
    del bad["evidence_weights"]
    with pytest.raises(ConfigError):
        parse_baseline_ref("s", bad)
    # 契约建议 10：非合成输入必须用已签字、synthetic=false 的掌握配置
    require_signed_baseline(CFG, True)
    with pytest.raises(ConfigError):
        require_signed_baseline(CFG, False)
    signed = dataclasses.replace(CFG, bkt=dataclasses.replace(CFG.bkt, synthetic=False, signed=True))
    require_signed_baseline(signed, False)
    for b in (dataclasses.replace(CFG.bkt, synthetic=True, signed=True), dataclasses.replace(CFG.bkt, synthetic=False, signed=False)):
        with pytest.raises(ConfigError):
            require_signed_baseline(dataclasses.replace(CFG, bkt=b), False)
    good = json.loads(json.dumps(src))
    good.update(synthetic=False, signed_by="x", signed_at="2026-01-01T00:00:00+08:00")
    r2 = parse_baseline_ref("s", good)
    assert r2.signed and not r2.synthetic
    good["signed_at"] = ""
    assert not parse_baseline_ref("s", good).signed


def test_analyze_refuses_unsigned_baseline_for_non_synthetic(synthetic):
    d = synthetic
    nonsyn = dataclasses.replace(d["responses"], synthetic=False)
    with pytest.raises(ConfigError):
        analyze(d["items"], nonsyn, d["baseline"], CFG, draws=0)


# ---------------- 命令行与输出目录 ----------------
def test_cli_end_to_end(tmp_path, synthetic, capsys):
    assert main(["synth", "--out", str(tmp_path / "in")]) == 0
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == ["baseline.csv", "items.json", "responses.csv"]
    for name in ("items.json", "responses.csv", "baseline.csv"):
        assert (tmp_path / "in" / name).read_text(encoding="utf-8") == synthetic["inputs"][name]
    args = ["run", "--items", str(tmp_path / "in/items.json"), "--responses", str(tmp_path / "in/responses.csv"),
            "--baseline", str(tmp_path / "in/baseline.csv"), "--out", str(tmp_path / "out")]
    assert main(args) == 0
    for name in R.OUTPUT_NAMES:
        path = tmp_path / "out" / name
        assert stat.S_IMODE(os.stat(path).st_mode) == R.FILE_MODE
        assert path.read_text(encoding="utf-8") == synthetic["files"][name]
    capsys.readouterr()
    assert main(["synth", "--out", str(tmp_path / "out")]) == 2             # 输出目录非空：拒绝（不覆盖）
    assert "拒绝写出" in capsys.readouterr().out


def test_cli_refusals(tmp_path, capsys):
    assert main(["synth", "--out", str(tmp_path / "in")]) == 0
    text = (tmp_path / "in/responses.csv").read_text(encoding="utf-8")
    (tmp_path / "in/responses.csv").write_text(text.replace("synthetic=true", "synthetic=false", 1), encoding="utf-8")
    rc = main(["run", "--items", str(tmp_path / "in/items.json"), "--responses", str(tmp_path / "in/responses.csv"),
               "--baseline", str(tmp_path / "in/baseline.csv"), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "拒绝输入" in out and "syn-" not in out and not (tmp_path / "out").exists()
    (tmp_path / "link").symlink_to(tmp_path / "in")
    assert main(["synth", "--out", str(tmp_path / "link")]) == 2
    assert main(["synth", "--out", str(tmp_path / "x"), "--size", "10"]) == 2
    bad = tmp_path / "cfg.json"
    bad.write_text(json.dumps(_cfg("disclosure.min_respondents", 5)), encoding="utf-8")
    rc = main(["run", "--items", str(tmp_path / "in/items.json"), "--responses", str(tmp_path / "in/responses.csv"),
               "--baseline", str(tmp_path / "in/baseline.csv"), "--out", str(tmp_path / "out2"), "--config", str(bad)])
    assert rc == 2 and "配置不合法" in capsys.readouterr().out


def test_reuses_kge_calibrate_guards_without_copies():
    """守卫、扰动与写出直接复用 kge-calibrate（不另立口径）：本工具源码里没有自己的伪名正则、扰动表或 O_EXCL 写法"""
    import kge_calibrate.calibrate as C
    import kge_calibrate.report as KR
    assert R.write_files is KR.write_files and R.check_out_dir is KR.check_out_dir and R.noise is C.noise
    src = "".join(p.read_text(encoding="utf-8") for p in (TOOL / "kge_irt").glob("*.py"))
    assert "os.O_EXCL" not in src and "NOISE_TABLE =" not in src and "[a-z]{10,24}" not in src and "FORBIDDEN_COLUMN" not in src
    assert "hashlib.sha256(b\"kge-calibrate/record-key" not in src
