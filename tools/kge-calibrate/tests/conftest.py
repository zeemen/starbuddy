"""测试用洞察服务的 venv 运行（make kge-calibrate-test；本工具纯标准库，只借它的 pytest）：把本工具目录加进 sys.path。
变异脚本在临时目录里跑本工具的副本；仓库里其他服务的源码（学习服务门槛取值、知识服务快照白名单）用 KGE_CALIBRATE_REPO 指回仓库，只读核对。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("KGE_CALIBRATE_REPO") or TOOL.parents[1])
if str(TOOL) in sys.path:
    sys.path.remove(str(TOOL))
sys.path.insert(0, str(TOOL))
