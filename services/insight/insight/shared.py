"""与其他模块共用的规则：只读引用，不复制实现。

- 陈述—判断一致性检查：复用 KGE-04 第一步已合入的规则引擎 services/learning/learning/mastery/consistency.py
  （纯标准库，不连库）。接入方式与知识服务接模型路由相同（services/knowledge/knowledge/compiler/full.py）：把
  services/learning 加进 sys.path 后导入。部署时洞察服务的制品要带上这一个目录（lessons“第二步计划”）。
- 认知层次：同一模块的 COGNITIVE_LEVELS（迁移 0011 的取值，由低到高）。
- 禁用推断词表：docs/design/sie/sie_lexicon.py（同一个词表、同一套规范化），经 consistency.default_forbidden_matcher 取匹配函数。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[3]
LEARNING_DIR = ROOT / "services" / "learning"
if str(LEARNING_DIR) not in sys.path:
    sys.path.append(str(LEARNING_DIR))

from learning.mastery import consistency  # noqa: E402  只读引用 KGE-04 的规则引擎
from learning.mastery.config import COGNITIVE_LEVELS  # noqa: E402

Forbidden = Callable[[str], Sequence[str]]

# platform.mastery_state 的取值（迁移 0001），由低到高；unseen = 有计入证据但尚未过“理解”
GATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")
GATE_RANK = {g: i for i, g in enumerate(GATES)}
STAR_GATES = ("transfer", "retain")  # 星亮 = 进入“迁移”（ADR 11 §3）

_forbidden: Forbidden | None = None


def forbidden_matcher() -> Forbidden:
    """禁用推断匹配函数（返回命中的类别 ID 列表）。找不到词表即报错：检查不能缺席"""
    global _forbidden
    if _forbidden is None:
        _forbidden = consistency.default_forbidden_matcher()
    return _forbidden


__all__ = ["COGNITIVE_LEVELS", "GATES", "GATE_RANK", "STAR_GATES", "consistency", "forbidden_matcher", "Forbidden"]
