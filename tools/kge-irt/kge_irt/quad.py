"""Gauss–Hermite 求积（固定节点数，纯标准库、确定性）。

物理学家 Hermite 权 e^{−x²} 的节点与权重用 Newton 迭代求正交归一 Hermite 多项式的零点（Numerical Recipes 的 gauher 写法），
再换成标准正态：θ = √2 x，权重 ÷ √π（和为 1）。n 点规则对次数 ≤ 2n − 1 的多项式精确（测试核对 E θ² = 1、E θ⁴ = 3 与 n = 3 的闭式）。
"""
from __future__ import annotations

import math
from functools import lru_cache

_EPS = 3.0e-14
_PIM4 = math.pi ** -0.25
_MAXIT = 200


class QuadratureError(ValueError):
    """求积节点没有收敛"""


def gauss_hermite(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """物理学家 Hermite（权 e^{−x²}）的 n 个节点与权重，节点升序"""
    if not (isinstance(n, int) and 1 <= n <= 200):
        raise QuadratureError("节点数必须是 1–200 的整数")
    x = [0.0] * n
    w = [0.0] * n
    m = (n + 1) // 2
    z = 0.0
    for i in range(m):
        if i == 0:
            z = math.sqrt(2 * n + 1) - 1.85575 * (2 * n + 1) ** -0.16667
        elif i == 1:
            z -= 1.14 * n ** 0.426 / z
        elif i == 2:
            z = 1.86 * z - 0.86 * x[0]
        elif i == 3:
            z = 1.91 * z - 0.91 * x[1]
        else:
            z = 2.0 * z - x[i - 2]
        pp = 0.0
        for _ in range(_MAXIT):
            p1, p2 = _PIM4, 0.0
            for j in range(1, n + 1):
                p3, p2 = p2, p1
                p1 = z * math.sqrt(2.0 / j) * p2 - math.sqrt((j - 1) / j) * p3
            pp = math.sqrt(2.0 * n) * p2
            z1 = z
            z = z1 - p1 / pp
            if abs(z - z1) <= _EPS:
                break
        else:
            raise QuadratureError("Gauss–Hermite 节点没有收敛")
        x[i], x[n - 1 - i] = z, -z
        w[i] = w[n - 1 - i] = 2.0 / (pp * pp)
    order = sorted(range(n), key=lambda k: x[k])
    return tuple(0.0 if abs(x[k]) < 1e-15 else x[k] for k in order), tuple(w[k] for k in order)


@lru_cache(maxsize=32)
def normal_quadrature(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """标准正态 N(0, 1) 的 n 点求积：(θ 节点升序, 权重和为 1)"""
    x, w = gauss_hermite(n)
    s = math.sqrt(math.pi)
    return tuple(math.sqrt(2.0) * v for v in x), tuple(v / s for v in w)
