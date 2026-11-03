"""指标：编辑距离（CER / WER）、分位数、相关系数、音素一致性。纯函数，只用标准库。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


# ---------- 编辑距离 ----------
def align(ref: Sequence[str], hyp: Sequence[str], sub_cost: int = 1) -> list[tuple[int | None, int | None]]:
    """Levenshtein 对齐（缺省替换、删除、插入代价都是 1）。返回按参考顺序的 (参考下标, 转写下标) 对：
    (i, j) 匹配或替换，(i, None) 删除，(None, j) 插入。回溯时优先对角线、再删除、再插入，结果确定。
    sub_cost > 2 时替换永远不如“删除 + 插入”，等价于最长公共子序列对齐（音素一致性用它，让相同标签尽量对上）。"""
    n, m = len(ref), len(hyp)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        row, prev = cost[i], cost[i - 1]
        for j in range(1, m + 1):
            sub = prev[j - 1] + (ref[i - 1] != hyp[j - 1]) * sub_cost
            row[j] = min(sub, prev[j] + 1, row[j - 1] + 1)
    out: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else sub_cost):
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    out.reverse()
    return out


@dataclass(frozen=True)
class EditCounts:
    sub: int
    dele: int
    ins: int
    ref_len: int

    @property
    def errors(self) -> int:
        return self.sub + self.dele + self.ins

    @property
    def rate(self) -> float | None:
        """单条错误率 =（替换 + 删除 + 插入）/ 参考长度；参考为空时无定义（None），插入仍计入合计。"""
        if self.ref_len == 0:
            return None
        return self.errors / self.ref_len


def edit_counts(ref: Sequence[str], hyp: Sequence[str]) -> EditCounts:
    s = d = ins = 0
    for i, j in align(ref, hyp):
        if i is None:
            ins += 1
        elif j is None:
            d += 1
        elif ref[i] != hyp[j]:
            s += 1
    return EditCounts(s, d, ins, len(ref))


def corpus_rate(counts: Iterable[EditCounts]) -> float | None:
    """合计错误率 = Σ 错误 / Σ 参考长度（按单位加权，不是逐条错误率的平均）；Σ 参考长度为 0 时 None。"""
    errs = total = 0
    for c in counts:
        errs += c.errors
        total += c.ref_len
    return errs / total if total else None


# ---------- 分位数 ----------
def quantile(values: Sequence[float], p: float) -> float | None:
    """最近秩法：排序后取第 ceil(p·n) 个（至少第 1 个）。空序列返回 None。"""
    if not values:
        return None
    if not 0 < p <= 1:
        raise ValueError("p 必须在 (0, 1]")
    xs = sorted(values)
    k = max(1, math.ceil(p * len(xs)))
    return xs[k - 1]


# ---------- 相关系数 ----------
def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson 相关；n < 2 或任一方方差为 0 时 None。"""
    n = len(x)
    if n != len(y):
        raise ValueError("长度不一致")
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ranks(xs: Sequence[float]) -> list[float]:
    """平均秩（并列取平均，秩从 1 开始）"""
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    out = [0.0] * len(xs)
    k = 0
    while k < len(order):
        e = k
        while e + 1 < len(order) and xs[order[e + 1]] == xs[order[k]]:
            e += 1
        avg = (k + e) / 2 + 1
        for t in range(k, e + 1):
            out[order[t]] = avg
        k = e + 1
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman 相关 = 平均秩上的 Pearson"""
    return pearson(ranks(x), ranks(y))


# ---------- 音素一致性 ----------
PHONE_SUB_COST = 3


@dataclass(frozen=True)
class PhoneJudgement:
    phone: str
    ok: bool


@dataclass(frozen=True)
class PhoneCounts:
    """与专家标注对齐后的计数。正类 = 发音错误。"""
    agree: int = 0
    total: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __add__(self, o: "PhoneCounts") -> "PhoneCounts":
        return PhoneCounts(self.agree + o.agree, self.total + o.total, self.tp + o.tp, self.fp + o.fp, self.fn + o.fn)

    @property
    def accuracy(self) -> float | None:
        return self.agree / self.total if self.total else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None


def phone_counts(expert: Sequence[PhoneJudgement], vendor: Sequence[PhoneJudgement]) -> PhoneCounts:
    """按音素标签做最长公共子序列对齐（align 的 sub_cost=PHONE_SUB_COST）：
    - 标签相同的对齐对：比较对 / 错判断；
    - 专家音素没有对上（删除，或对齐到不同标签）：算供应商“没判”——不计一致，专家标错时计漏检（fn）；
    - 供应商多出的音素（插入）：不计。
    音素级准确率 = 一致数 / 专家音素数；错误检出召回 = tp /（tp + fn）；精确 = tp /（tp + fp）。"""
    agree = tp = fp = fn = 0
    for i, j in align([p.phone for p in expert], [p.phone for p in vendor], PHONE_SUB_COST):
        if i is None:
            continue
        e = expert[i]
        v = vendor[j] if j is not None and vendor[j].phone == e.phone else None
        if v is None:
            if not e.ok:
                fn += 1
            continue
        if v.ok == e.ok:
            agree += 1
        if not e.ok and not v.ok:
            tp += 1
        elif not e.ok and v.ok:
            fn += 1
        elif e.ok and not v.ok:
            fp += 1
    return PhoneCounts(agree, len(expert), tp, fp, fn)
