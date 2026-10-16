"""KGE-09 地图数据校准第一步（specs/kge09-map-calibration/spec.md）：前置边经验检验与隐藏依赖候选。

离线、只出聚合候选：输入是地图快照（知识服务 knowledge.atoms 的快照白名单格式，JSON）与按学生伪名、按节点的掌握状态（只在内存里），
输出只有聚合统计与候选清单（calibration-report.json、worklist.md），不含伪名、不含人数低于门槛的格子。
不改地图、不写库、不连库、不调用模型；纯标准库，零新依赖。本步只接受合成样例（synthetic=true）。
"""
from __future__ import annotations

GENERATOR = "kge-calibrate@0.2.0"   # 0.2.0：星亮口径、表键扰动与粗分档（契约 B1/B2、安全 B1–B3 审查）
SYNTHETIC_NOTICE = "合成，不是试点结论"
