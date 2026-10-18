"""KGE-10 IRT 试点评估第一步（specs/kge10-irt-pilot）：离线 MML-EM 拟合 1PL / 2PL，时间切分评估（对数损失、Brier、AUC），
对照现行掌握估计基线与“只用先验难度”；自助法区间、秩相关与可识别性告警。

只出题目级与节点级聚合（irt-report.json、irt-report.md），不含学生能力值、不含伪名；不改先验难度、不改掌握计算、不写库、不连库、
不调用模型；纯标准库，零新依赖。只接受合成样例（synthetic=true）。

输入守卫、表键扰动、取整与输出写法复用 tools/kge-calibrate（KGE-09），不另立口径：本包把它的目录加进 sys.path
（变异测试在临时副本里跑时，用 KGE_IRT_REPO 指回仓库）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(os.environ.get("KGE_IRT_REPO") or Path(__file__).resolve().parents[3])
_CALIBRATE = _REPO / "tools/kge-calibrate"
if str(_CALIBRATE) not in sys.path:
    sys.path.append(str(_CALIBRATE))

from kge_calibrate import SYNTHETIC_NOTICE  # noqa: E402  （同一句标注，不另写）

GENERATOR = "kge-irt@0.2.0"   # 0.2.0：审查修改（基线口径对齐、配对判定、AUC 扰动门槛、逐题难度格点等）
__all__ = ["GENERATOR", "SYNTHETIC_NOTICE"]
