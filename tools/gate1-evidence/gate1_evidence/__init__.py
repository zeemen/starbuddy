"""Gate 1 证据管线第一步（SIE-04，specs/gate1-evidence-sie04/spec.md）：合成纵向队列与四项报表。

只用合成数据：不连库、不读留出集、不新增迁移。证据包标“合成，不是试点结论”。
"""
from __future__ import annotations

GENERATOR = "gate1-evidence@0.2.0"   # 0.2.0：合成队列补中圈指标原料，效度检验覆盖全部 8 个静默指标（SIE-05）
SYNTHETIC_NOTICE = "合成，不是试点结论"
