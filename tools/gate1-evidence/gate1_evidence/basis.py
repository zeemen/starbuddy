"""考试预测的中值口径（G1-3 的 MAE 与分组误差都用它；合成管线与 RUNBOOK 的真实核对共用这一个函数）。

中值 = 预测依据（insight/predict.py 的 Prediction.basis，即 0034 exam_prediction.basis）里的得分率均值 mean × 满分，保留一位小数。
它是区间中心（未截断）；截断到 [0, 满分] 后的区间可能不对称，所以不用区间中点。
"""
from __future__ import annotations

import math
from typing import Any, Mapping


class BasisError(ValueError):
    """预测依据缺少得分率均值或取值不对"""


def predicted_median(basis: Mapping[str, Any], full_score: int) -> float:
    mean = basis.get("mean") if isinstance(basis, Mapping) else None
    if (not isinstance(mean, (int, float)) or isinstance(mean, bool) or not math.isfinite(mean) or not 0 <= mean <= 1
            or not isinstance(full_score, int) or isinstance(full_score, bool) or full_score < 1):
        raise BasisError("prediction basis must carry a mean in [0, 1] and a positive full score")
    return round(float(mean) * full_score, 1)
