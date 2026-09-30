"""自助法 95% 置信区间（按样本有放回重抽，百分位法、最近秩，固定种子）。"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Sequence, TypeVar

T = TypeVar("T")
RESAMPLES = 1000
LEVEL = 0.95
METHOD = "按样本有放回重抽的自助法（百分位，最近秩）"


def r6(x: float | None) -> float | None:
    """报告里的数一律保留 6 位小数（确定性输出）"""
    return None if x is None else round(float(x), 6)


def bootstrap_ci(items: Sequence[T], statistic: Callable[[Sequence[T]], float | None], *, seed: str,
                 resamples: int = RESAMPLES, level: float = LEVEL) -> dict[str, Any]:
    """返回 {value, ci_low, ci_high, n}。点估计无定义时三项都是 None；
    某次重抽样上统计量无定义（例如全抽到空参考）就丢掉那一次，区间用其余的算。
    种子是字符串：random.Random(str) 与 PYTHONHASHSEED 无关，同样输入逐次相同。"""
    n = len(items)
    point = statistic(items) if n else None
    empty = {"value": None, "ci_low": None, "ci_high": None, "n": n}
    if point is None:
        return empty
    rng = random.Random(seed)
    vals: list[float] = []
    for _ in range(resamples):
        v = statistic([items[rng.randrange(n)] for _ in range(n)])
        if v is not None:
            vals.append(v)
    if not vals:
        return {**empty, "value": r6(point)}
    vals.sort()
    m = len(vals)
    alpha = (1 - level) / 2
    lo = vals[int(math.floor(alpha * m))]
    hi = vals[int(math.ceil((1 - alpha) * m)) - 1]
    return {"value": r6(point), "ci_low": r6(lo), "ci_high": r6(hi), "n": n}
