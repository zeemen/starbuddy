"""评估指标与秩统计（纯函数，确定性）：每生一个点的对数损失与 Brier、按人加权的 AUC、Spearman 秩相关、Theil–Sen 稳健直线。

“每生一个点”（PLT-17 的教训）：先算每名学生在后段全部作答上的平均损失，再对学生取平均；AUC 的每次作答权重 = 1 / 该生后段作答数，
所以每名学生在每个指标里的总权重都是 1，删掉任何一人对指标的影响有上界（报表据此定粒度与扰动）。
"""
from __future__ import annotations

import math
from statistics import median
from typing import Iterable, Mapping, Sequence


def clip(p: float, eps: float) -> float:
    return min(1.0 - eps, max(eps, p))


def log_loss_one(y: bool, p: float, eps: float) -> float:
    q = clip(p, eps)
    return -math.log(q if y else 1.0 - q)


def brier_one(y: bool, p: float) -> float:
    return (p - (1.0 if y else 0.0)) ** 2


def student_points(rows: Iterable[tuple[str, bool, float]], eps: float) -> dict[str, tuple[float, float, int]]:
    """(伪名, 是否答对, 预测概率) → 伪名 → (该生平均对数损失, 该生平均 Brier, 该生作答数)。只在内存里用"""
    acc: dict[str, list[float]] = {}
    for pid, y, p in rows:
        a = acc.setdefault(pid, [0.0, 0.0, 0])
        a[0] += log_loss_one(y, p, eps)
        a[1] += brier_one(y, clip(p, eps))
        a[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2], int(v[2])) for k, v in sorted(acc.items())}


def student_mean(points: Mapping[str, tuple[float, float, int]], which: int) -> float:
    return sum(v[which] for v in points.values()) / len(points)


def weighted_auc(rows: Sequence[tuple[float, bool, float]]) -> float | None:
    """(预测分数, 是否答对, 权重) → 加权 AUC（同分记一半）；只有一类时无定义"""
    w_pos = sum(w for _, y, w in rows if y)
    w_neg = sum(w for _, y, w in rows if not y)
    if w_pos <= 0.0 or w_neg <= 0.0:
        return None
    ordered = sorted(rows, key=lambda r: r[0])
    below = 0.0
    total = 0.0
    i = 0
    while i < len(ordered):
        k = i
        gp = gn = 0.0
        while k < len(ordered) and ordered[k][0] == ordered[i][0]:
            if ordered[k][1]:
                gp += ordered[k][2]
            else:
                gn += ordered[k][2]
            k += 1
        total += gp * (below + 0.5 * gn)
        below += gn
        i = k
    return total / (w_pos * w_neg)


def per_student_auc_rows(rows: Iterable[tuple[str, bool, float]]) -> tuple[list[tuple[float, bool, float]], float]:
    """按人加权：每次作答权重 = 1 / 该生作答数。另返回正例（按人加权）占比，用于 AUC 的单人影响上界"""
    rows = list(rows)
    k: dict[str, int] = {}
    for pid, _, _ in rows:
        k[pid] = k.get(pid, 0) + 1
    out = [(p, y, 1.0 / k[pid]) for pid, y, p in rows]
    pos = sum(w for _, y, w in out if y)
    return out, pos / len(k)


def ranks(xs: Sequence[float]) -> list[float]:
    """平均秩（同值取平均，1 起）"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        k = i
        while k + 1 < len(order) and xs[order[k + 1]] == xs[order[i]]:
            k += 1
        avg = (i + k) / 2.0 + 1.0
        for m in range(i, k + 1):
            r[order[m]] = avg
        i = k + 1
    return r


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    return pearson(ranks(x), ranks(y))


def theil_sen(x: Sequence[float], y: Sequence[float]) -> tuple[float, float] | None:
    """稳健直线 y ≈ 截距 + 斜率 × x：斜率 = 两两斜率的中位数，截距 = (y − 斜率 x) 的中位数"""
    slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x)) for j in range(i + 1, len(x)) if x[j] != x[i]]
    if not slopes:
        return None
    s = median(slopes)
    return median([b - s * a for a, b in zip(x, y)]), s
