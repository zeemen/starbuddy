"""KGE-09 地图数据校准（specs/kge09-map-calibration）：第一步（spec.md）前置边经验检验与隐藏依赖候选；
第二步（spec-step2.md）易错候选（未覆盖的高频错因、错因难归类信号）与节点内部分化（拆分节点候选）。

离线、只出聚合候选：输入是地图快照（知识服务 knowledge.atoms 的快照白名单格式，JSON）、按学生伪名的掌握状态、作答聚合行与错因行
（个人层，只在内存里），以及错因分类表与易错点编码对照（公共层）；输出只有聚合统计与候选清单（calibration-report.json、worklist.md），
不含伪名、不含人数低于门槛的格子。不改地图、不写库、不连库、不调用模型；纯标准库，零新依赖。只接受合成样例（synthetic=true）。
"""
from __future__ import annotations

GENERATOR = "kge-calibrate@0.3.0"   # 0.3.0：第二步（易错候选、难归类、节点内部分化）；0.2.0：星亮口径、表键扰动与粗分档
SYNTHETIC_NOTICE = "合成，不是试点结论"
