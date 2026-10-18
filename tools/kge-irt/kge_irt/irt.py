"""1PL / 2PL 的边际最大似然 + EM（Gauss–Hermite 固定节点；纯函数、确定性，只在内存里）。

模型：P(答对 | θ) = σ(a (θ − b))，θ ~ N(0, 1)。参数在 φ 空间里估计：α = log a，b 原样。三种结构（同一套 E 步 / M 步）：
- 2pl：每题 (α_j, b_j)；
- 1pl：块内共同 α，每题 b_j；
- prior（“先验难度 + 能力”对照）：共同 α，b_j = c0 + c1 ×（先验难度_j − 0.5；无先验记 0）。
E 步：每种作答模式（同一块里答过的题与对错完全相同的学生合并，按人数加权）在求积节点上的后验；期望计数 n_jq（答过 j 的后验质量）
与 r_jq（其中答对的）。M 步：对 Q 函数 + 对数先验做 Fisher 得分，步长减半、只接受不降的步（广义 EM），参数超界截断；
所以 EM 目标（边际对数似然 + 对数先验）单调不降（测试逐轮核对）。初值由拟合段正确率确定，无随机初始化。
学生能力只以后验的形式在内存里用于预测（后验预测：Σ_q 后验_q × P_j(θ_q)），从不输出。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from .config import FitConfig

KINDS = ("1pl", "2pl", "prior")
SLOPE_BOUND = 20.0
Quad = tuple[tuple[float, ...], tuple[float, ...]]


def softplus(t: float) -> float:
    return t + math.log1p(math.exp(-t)) if t > 0 else math.log1p(math.exp(t))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


# ---------- 块数据 ----------
@dataclass(frozen=True)
class BlockData:
    """一个块（节点）里拟合段的作答：题目（ID 排序）、作答模式（去重、排序）、每名学生的模式下标与每种模式的人数"""
    node: str
    items: tuple[str, ...]
    priors: tuple[float | None, ...]
    patterns: tuple[tuple[tuple[int, bool], ...], ...]
    students: tuple[str, ...]
    pattern_of: tuple[int, ...]
    weights: tuple[float, ...]

    def item_counts(self) -> list[tuple[int, int]]:
        """每道题拟合段的 (答对人数, 作答人数)"""
        out = [[0, 0] for _ in self.items]
        for pat, wt in zip(self.patterns, self.weights):
            for j, x in pat:
                out[j][0] += int(wt) * x
                out[j][1] += int(wt)
        return [(a, b) for a, b in out]

    def respondents(self, j: int) -> list[str]:
        """拟合段作答过第 j 题的学生（只在内存里用于表键）"""
        hit = {k for k, pat in enumerate(self.patterns) if any(i == j for i, _ in pat)}
        return [s for s, k in zip(self.students, self.pattern_of) if k in hit]


def block_data(node: str, items: Sequence[str], priors: Sequence[float | None],
               fit: Mapping[str, Sequence[tuple[str, bool]]]) -> BlockData:
    index = {it: j for j, it in enumerate(items)}
    by_student = {p: tuple(sorted((index[i], x) for i, x in resp)) for p, resp in sorted(fit.items()) if resp}
    patterns = tuple(sorted(set(by_student.values())))
    pidx = {pat: k for k, pat in enumerate(patterns)}
    students = tuple(by_student)
    pattern_of = tuple(pidx[by_student[s]] for s in students)
    counts = [0.0] * len(patterns)
    for k in pattern_of:
        counts[k] += 1.0
    return BlockData(node, tuple(items), tuple(priors), patterns, students, pattern_of, tuple(counts))


def resample_weights(bd: BlockData, rng: random.Random) -> tuple[float, ...]:
    """自助法：块内学生有放回重抽同样人数 → 每种作答模式的权重"""
    n = len(bd.students)
    counts = [0.0] * len(bd.patterns)
    for _ in range(n):
        counts[bd.pattern_of[rng.randrange(n)]] += 1.0
    return tuple(counts)


# ---------- 参数结构 ----------
@dataclass(frozen=True)
class Structure:
    kind: str
    size: int
    alpha_of: tuple[tuple[tuple[int, float], ...], ...]   # 每题 α 的线性组合（参数下标, 系数）
    b_of: tuple[tuple[tuple[int, float], ...], ...]       # 每题 b 的线性组合
    prior_mean: tuple[float, ...]
    prior_sd: tuple[float, ...]
    lo: tuple[float, ...]
    hi: tuple[float, ...]
    components: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]   # 互不相干的参数组：(参数下标, 题目下标)


def structure(kind: str, priors: Sequence[float | None], fc: FitConfig) -> Structure:
    J = len(priors)
    la_lo, la_hi = math.log(fc.a_lo), math.log(fc.a_hi)
    if kind == "2pl":
        alpha = tuple(((2 * j, 1.0),) for j in range(J))
        bees = tuple(((2 * j + 1, 1.0),) for j in range(J))
        mean = tuple(v for _ in range(J) for v in (fc.log_a_prior_mean, 0.0))
        sd = tuple(v for _ in range(J) for v in (fc.log_a_prior_sd, fc.b_prior_sd))
        lo = tuple(v for _ in range(J) for v in (la_lo, fc.b_lo))
        hi = tuple(v for _ in range(J) for v in (la_hi, fc.b_hi))
        comps = tuple(((2 * j, 2 * j + 1), (j,)) for j in range(J))
        return Structure(kind, 2 * J, alpha, bees, mean, sd, lo, hi, comps)
    if kind == "1pl":
        alpha = tuple(((0, 1.0),) for _ in range(J))
        bees = tuple(((1 + j, 1.0),) for j in range(J))
        return Structure(kind, J + 1, alpha, bees, (fc.log_a_prior_mean,) + (0.0,) * J, (fc.log_a_prior_sd,) + (fc.b_prior_sd,) * J,
                         (la_lo,) + (fc.b_lo,) * J, (la_hi,) + (fc.b_hi,) * J, ((tuple(range(J + 1)), tuple(range(J))),))
    if kind == "prior":
        alpha = tuple(((0, 1.0),) for _ in range(J))
        bees = tuple(((1, 1.0), (2, 0.0 if p is None else p - 0.5)) for p in priors)
        return Structure(kind, 3, alpha, bees, (fc.log_a_prior_mean, 0.0, 0.0), (fc.log_a_prior_sd, fc.b_prior_sd, fc.slope_prior_sd),
                         (la_lo, fc.b_lo, -SLOPE_BOUND), (la_hi, fc.b_hi, SLOPE_BOUND), (((0, 1, 2), tuple(range(J))),))
    raise ValueError(f"模型只有 {KINDS}")


def item_params(st: Structure, phi: Sequence[float]) -> list[tuple[float, float]]:
    """每题 (a, b)"""
    return [(math.exp(sum(c * phi[k] for k, c in al)), sum(c * phi[k] for k, c in be)) for al, be in zip(st.alpha_of, st.b_of)]


def _clip(st: Structure, phi: list[float]) -> list[float]:
    return [min(h, max(lo, v)) for v, lo, h in zip(phi, st.lo, st.hi)]


def initial_phi(st: Structure, bd: BlockData) -> list[float]:
    """确定的初值：b_j = −logit(拟合段正确率，截到 [0.05, 0.95])，α = 0；prior 结构取各题初值的均值与 0 斜率"""
    bs = []
    for x, n in bd.item_counts():
        p = min(0.95, max(0.05, x / n)) if n else 0.5
        bs.append(-math.log(p / (1.0 - p)))
    if st.kind == "2pl":
        phi = [v for b in bs for v in (0.0, b)]
    elif st.kind == "1pl":
        phi = [0.0] + bs
    else:
        phi = [0.0, sum(bs) / len(bs), 0.0]
    return _clip(st, phi)


def log_prior(st: Structure, phi: Sequence[float], idx: Sequence[int] | None = None) -> float:
    ks = range(st.size) if idx is None else idx
    return -sum((phi[k] - st.prior_mean[k]) ** 2 / (2.0 * st.prior_sd[k] ** 2) for k in ks)


# ---------- E 步 ----------
@dataclass(frozen=True)
class EStep:
    loglik: float                         # 边际对数似然（按模式权重）
    n: tuple[tuple[float, ...], ...]       # n[j][q]
    r: tuple[tuple[float, ...], ...]       # r[j][q]
    posts: tuple[tuple[float, ...], ...] | None = None   # 每种模式的后验（只在预测时要）


def _log_probs(ab: Sequence[tuple[float, float]], theta: Sequence[float]) -> tuple[list[list[float]], list[list[float]]]:
    lp, lq = [], []
    for a, b in ab:
        zs = [a * (t - b) for t in theta]
        lp.append([-softplus(-z) for z in zs])
        lq.append([-softplus(z) for z in zs])
    return lp, lq


def e_step(bd: BlockData, st: Structure, phi: Sequence[float], quad: Quad, weights: Sequence[float] | None = None,
           want_post: bool = False) -> EStep:
    theta, w = quad
    wts = bd.weights if weights is None else weights
    logw = [math.log(v) for v in w]
    lp, lq = _log_probs(item_params(st, phi), theta)
    Q = len(theta)
    N = [[0.0] * Q for _ in bd.items]
    R = [[0.0] * Q for _ in bd.items]
    total = 0.0
    posts = []
    for pat, wt in zip(bd.patterns, wts):
        if wt == 0.0 and not want_post:
            continue
        ll = logw
        for j, x in pat:
            v = lp[j] if x else lq[j]
            ll = [p + q for p, q in zip(ll, v)]
        m = max(ll)
        e = [math.exp(v - m) for v in ll]
        s = sum(e)
        post = [v / s for v in e]
        if want_post:
            posts.append(tuple(post))
        if wt == 0.0:
            continue
        total += wt * (m + math.log(s))
        wp = [wt * v for v in post]
        for j, x in pat:
            N[j] = [p + q for p, q in zip(N[j], wp)]
            if x:
                R[j] = [p + q for p, q in zip(R[j], wp)]
    return EStep(total, tuple(map(tuple, N)), tuple(map(tuple, R)), tuple(posts) if want_post else None)


# ---------- M 步 ----------
def m_objective(st: Structure, comp: tuple[tuple[int, ...], tuple[int, ...]], phi: Sequence[float], es: EStep, quad: Quad) -> float:
    """一个参数组的 M 步目标：Σ_{j∈组} Σ_q [r log P + (n − r) log(1 − P)] + 组内参数的对数先验"""
    theta = quad[0]
    pidx, items = comp
    ab = item_params(st, phi)
    total = log_prior(st, phi, pidx)
    for j in items:
        a, b = ab[j]
        for t, n, r in zip(theta, es.n[j], es.r[j]):
            z = a * (t - b)
            total += -r * softplus(-z) - (n - r) * softplus(z)
    return total


def m_gradient(st: Structure, comp: tuple[tuple[int, ...], tuple[int, ...]], phi: Sequence[float], es: EStep,
               quad: Quad) -> tuple[list[float], list[list[float]]]:
    """M 步目标对组内参数的梯度与 Fisher 信息（期望信息，正定）"""
    theta = quad[0]
    pidx, items = comp
    pos = {k: i for i, k in enumerate(pidx)}
    P = len(pidx)
    g = [0.0] * P
    info = [[0.0] * P for _ in range(P)]
    ab = item_params(st, phi)
    for j in items:
        a, b = ab[j]
        for t, n, r in zip(theta, es.n[j], es.r[j]):
            z = a * (t - b)
            p = sigmoid(z)
            e = r - n * p
            W = n * p * (1.0 - p)
            dz: dict[int, float] = {}
            for k, c in st.alpha_of[j]:
                dz[pos[k]] = dz.get(pos[k], 0.0) + c * z
            for k, c in st.b_of[j]:
                dz[pos[k]] = dz.get(pos[k], 0.0) - a * c
            for u, du in dz.items():
                g[u] += e * du
                row = info[u]
                for v, dv in dz.items():
                    row[v] += W * du * dv
    for k in pidx:
        i = pos[k]
        sd2 = st.prior_sd[k] ** 2
        g[i] -= (phi[k] - st.prior_mean[k]) / sd2
        info[i][i] += 1.0 / sd2
    return g, info


def solve(A: list[list[float]], y: list[float]) -> list[float]:
    """小方阵的高斯消元（部分主元）"""
    n = len(y)
    M = [row[:] + [y[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-300:
            raise ZeroDivisionError("信息矩阵奇异")
        M[c], M[piv] = M[piv], M[c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            if f:
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    x = [0.0] * n
    for c in range(n - 1, -1, -1):
        x[c] = (M[c][n] - sum(M[c][k] * x[k] for k in range(c + 1, n))) / M[c][c]
    return x


def m_step(st: Structure, phi: Sequence[float], es: EStep, quad: Quad, fc: FitConfig) -> list[float]:
    """每个参数组做至多 m_step_iterations 步 Fisher 得分；每步减半直到目标不降（否则不动）"""
    cur = list(phi)
    for comp in st.components:
        pidx = comp[0]
        for _ in range(fc.m_step_iterations):
            g, info = m_gradient(st, comp, cur, es, quad)
            step = solve(info, g)
            f0 = m_objective(st, comp, cur, es, quad)
            t = 1.0
            moved = False
            for _ in range(30):
                cand = cur[:]
                for i, k in enumerate(pidx):
                    cand[k] = cur[k] + t * step[i]
                cand = _clip(st, cand)
                if m_objective(st, comp, cand, es, quad) >= f0:
                    moved = max(abs(cand[k] - cur[k]) for k in pidx) > 0.0
                    cur = cand
                    break
                t *= 0.5
            if not moved or max(abs(t * s) for s in step) < fc.tolerance * 0.1:
                break
    return cur


# ---------- EM ----------
@dataclass(frozen=True)
class Fit:
    kind: str
    phi: tuple[float, ...]
    converged: bool
    iterations: int
    trace: tuple[float, ...]     # 每轮 E 步时的 EM 目标（边际对数似然 + 对数先验）
    loglik: float


def fit_em(bd: BlockData, st: Structure, quad: Quad, fc: FitConfig, phi0: Sequence[float] | None = None,
           max_iterations: int | None = None, weights: Sequence[float] | None = None) -> Fit:
    phi = list(initial_phi(st, bd) if phi0 is None else phi0)
    limit = fc.max_iterations if max_iterations is None else max_iterations
    trace = []
    converged = False
    it = 0
    es = e_step(bd, st, phi, quad, weights)
    for it in range(1, limit + 1):
        trace.append(es.loglik + log_prior(st, phi))
        new = m_step(st, phi, es, quad, fc)
        delta = max(abs(a - b) for a, b in zip(new, phi))
        phi = new
        es = e_step(bd, st, phi, quad, weights)
        if delta < fc.tolerance:
            converged = True
            break
    trace.append(es.loglik + log_prior(st, phi))
    return Fit(st.kind, tuple(phi), converged, it, tuple(trace), es.loglik)


def posteriors(bd: BlockData, st: Structure, fit: Fit, quad: Quad) -> dict[str, tuple[float, ...]]:
    """每名学生（拟合段在本块有作答的）在求积节点上的后验：只在内存里用于预测，不输出"""
    es = e_step(bd, st, fit.phi, quad, want_post=True)
    assert es.posts is not None
    return {s: es.posts[k] for s, k in zip(bd.students, bd.pattern_of)}


def predictive(post: Sequence[float], a: float, b: float, theta: Sequence[float]) -> float:
    """后验预测的答对概率"""
    return sum(p * sigmoid(a * (t - b)) for p, t in zip(post, theta))


def percentile(xs: Sequence[float], q: float) -> float:
    """线性插值分位数（相邻两秩插值，type 7）"""
    s = sorted(xs)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


def bootstrap(bd: BlockData, st: Structure, fit: Fit, quad: Quad, fc: FitConfig, draws: int, seed: int,
              max_iterations: int) -> list[list[tuple[float, float]]]:
    """块内学生有放回重抽 draws 次（种子与块名派生，与数据无关），每次从全样本估计出发重跑 EM；返回每次的各题 (a, b)"""
    rng = random.Random(f"kge-irt/bootstrap/{seed}/{st.kind}/{bd.node}")
    out = []
    for _ in range(draws):
        w = resample_weights(bd, rng)
        f = fit_em(bd, st, quad, fc, phi0=fit.phi, max_iterations=max_iterations, weights=w)
        out.append(item_params(st, f.phi))
    return out


# ---------- 逻辑回归（“只用先验难度”、BKT 的 Platt 校准） ----------
def fit_logistic(rows: Sequence[tuple[Sequence[float], float, float]], iterations: int = 100, ridge: float = 1e-6) -> list[float]:
    """P(答对) = σ(β · x)，rows = (特征向量, 答对数, 作答数)；Newton（加极小岭），确定性"""
    P = len(rows[0][0])
    beta = [0.0] * P
    for _ in range(iterations):
        g = [-ridge * b for b in beta]
        H = [[ridge if i == j else 0.0 for j in range(P)] for i in range(P)]
        for x, r, n in rows:
            p = sigmoid(sum(b * v for b, v in zip(beta, x)))
            e = r - n * p
            w = n * p * (1.0 - p)
            for i in range(P):
                if x[i]:
                    g[i] += e * x[i]
                    row = H[i]
                    for j in range(P):
                        if x[j]:
                            row[j] += w * x[i] * x[j]
        d = solve(H, g)
        beta = [b + v for b, v in zip(beta, d)]
        if max(abs(v) for v in d) < 1e-10:
            break
    return beta


def fit_prior_by_node(rows: Sequence[tuple[str, float, int, int]]) -> tuple[dict[str, float], float]:
    """“只用先验难度”：P(答对) = σ(β0_节点 + β1 ×（先验难度 − 0.5）)，按节点给截距、共同斜率；
    rows = (节点, 先验难度, 拟合段答对人数, 作答人数)，先验难度为空的题不进来"""
    nodes = sorted({r[0] for r in rows})
    pos = {n: i for i, n in enumerate(nodes)}
    feats = []
    for node, prior, x, n in rows:
        v = [0.0] * (len(nodes) + 1)
        v[pos[node]] = 1.0
        v[-1] = prior - 0.5
        feats.append((v, float(x), float(n)))
    beta = fit_logistic(feats)
    return {n: beta[pos[n]] for n in nodes}, beta[-1]


def logit(p: float, eps: float) -> float:
    q = min(1.0 - eps, max(eps, p))
    return math.log(q / (1.0 - q))
