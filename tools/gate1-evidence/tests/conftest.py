"""测试用洞察服务的 venv 运行（make gate1-test）：把本工具与洞察服务目录加进 sys.path（与 Makefile 的 PYTHONPATH 写法等价）。
变异脚本在临时目录里跑本工具的副本，用 GATE1_INSIGHT_DIR 指回仓库里的洞察服务（只读引用，不复制实现）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
INSIGHT = Path(os.environ.get("GATE1_INSIGHT_DIR") or TOOL.parents[1] / "services" / "insight")
for p in (str(INSIGHT), str(TOOL)):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
