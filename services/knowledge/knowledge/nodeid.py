"""节点 ID 规范（docs/design/kge/id-and-segments.md，迁移 0011 的 kge.node_id 域）。

页面仓库里的体检要能脱离本仓运行，所以这里固化一份正则与学科前缀；
tests/test_nodeid.py 用 docs/design/kge/check_node_id.py 的解析函数核对它与文档、迁移、知识服务契约逐字一致。
"""
from __future__ import annotations

import re

NODE_ID_REGEX = r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$"
NODE_ID = re.compile(NODE_ID_REGEX)

# platform.subject → 节点 ID 首段（kge.subject_prefix）
SUBJECT_PREFIX: dict[str, str] = {"aya": "MATH", "wenli": "CHN", "zhixing": "MORAL", "echo": "ENG"}


def is_node_id(value: object) -> bool:
    return isinstance(value, str) and NODE_ID.fullmatch(value) is not None


def depth(node_id: str) -> int:
    return node_id.count(".") + 1
