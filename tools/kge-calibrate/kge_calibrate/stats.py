"""统计纯函数（纯标准库，确定性）：Wilson 区间、Newcombe 差值区间、两比例 z 检验、Benjamini–Hochberg、phi、对数优势比。

正态分位数与尾概率用标准库 statistics.NormalDist 与 math.erfc；其余公式在这里自己实现，便于审查与变异测试。
"""
from __future__ import annotations

import math
from statistics import NormalDist

_STD = NormalDist()


class StatsError(ValueError):
    """统计函数的输入不合法"""


def z_for(confidence: float) -> float:
    """双侧置信水平对应的标准正态分位数（0.95 → 1.959963…）"""
    if not (isinstance(confidence, float) and 0.5 < confidence < 1.0):
        raise StatsError("置信水平必须在 (0.5, 1) 之间")
    return _STD.inv_cdf(0.5 + confidence / 2.0)


def _check_count(x: int, n: int) -> None:
    if not (isinstance(x, int) and isinstance(n, int)) or n <= 0 or not 0 <= x <= n:
        raise StatsError("计数必须满足 0 ≤ x ≤ n 且 n > 0")


def wilson(x: int, n: int, z: float) -> tuple[float, float]:
    """比例 x/n 的 Wilson 分数区间"""
    _check_count(x, n)
    p = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    lo = 0.0 if x == 0 else max(0.0, center - half)   # 端点精确为 0 / 1（避免浮点残差）
    hi = 1.0 if x == n else min(1.0, center + half)
    return lo, hi


def newcombe(x1: int, n1: int, x2: int, n2: int, z: float) -> tuple[float, float, float]:
    """差值 p1 − p2 与它的 Newcombe 混合分数区间（Newcombe 1998 方法 10，由两组 Wilson 区间组合）"""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson(x1, n1, z)
    l2, u2 = wilson(x2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


def two_proportion_p(x1: int, n1: int, x2: int, n2: int) -> float:
    """两比例之差的双侧 p 值（合并方差 z 检验）；两组合并后全是 0 或全是 1 时没有差异可检验，返回 1"""
    _check_count(x1, n1)
    _check_count(x2, n2)
    pooled = (x1 + x2) / (n1 + n2)
    var = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)
    if var <= 0.0:
        return 1.0
    z = (x1 / n1 - x2 / n2) / math.sqrt(var)
    return math.erfc(abs(z) / math.sqrt(2.0))


def bh_adjust(pvalues: list[float]) -> list[float]:
    """Benjamini–Hochberg 校正后的 p 值（与输入同序）：p_(i) × m / i 自大到小取累计最小，上限 1"""
    m = len(pvalues)
    for p in pvalues:
        if not (isinstance(p, float) and 0.0 <= p <= 1.0):
            raise StatsError("p 值必须在 [0, 1] 内")
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    adjusted = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        adjusted[i] = min(1.0, running)
    return adjusted


def phi(a: int, b: int, c: int, d: int) -> float | None:
    """四格表 phi 系数。a = 两个都掌握，b = 只掌握第一个，c = 只掌握第二个，d = 都没掌握；有一行或一列为 0 时无定义"""
    for v in (a, b, c, d):
        if not isinstance(v, int) or v < 0:
            raise StatsError("四格计数必须是非负整数")
    den = (a + b) * (c + d) * (a + c) * (b + d)
    if den == 0:
        return None
    return (a * d - b * c) / math.sqrt(den)


def chi2_p(phi_value: float, n: int) -> float:
    """四格表卡方检验（df = 1，不做连续性校正）的 p 值：χ² = n·phi²，P(χ²₁ > x) = erfc(√(x/2))"""
    if n <= 0:
        raise StatsError("n 必须为正")
    return math.erfc(math.sqrt(n * phi_value * phi_value / 2.0))


def log_odds_ratio(a: int, b: int, c: int, d: int, z: float) -> tuple[float, float, float] | None:
    """对数优势比 ln(ad / bc) 与 Woolf 区间；有格为 0 时无定义（本工具只在四格都 ≥ 门槛时调用）"""
    if min(a, b, c, d) <= 0:
        return None
    lor = math.log((a * d) / (b * c))
    se = math.sqrt(1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d)
    return lor, lor - z * se, lor + z * se
