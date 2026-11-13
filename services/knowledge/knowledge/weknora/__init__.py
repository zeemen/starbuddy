"""知识服务的 WeKnora 读取层（KGE-12 C；ADR 16 第 3.2、3.4、3.4a 节）。开关 KNOWLEDGE_WEKNORA_BACKEND 缺省关。

- manifest.py：交付清单（WeKnora 文档 ID → 中台内容 ID → 题目引用 / 编译页，过滤元数据）；
- filters.py：过滤策略（现在只有“允许清单”，默认策略等技术验证 V07）；
- client.py：导入 tools/weknora-probe 的客户端（只在开关开时导入）；
- backend.py：相似题与标准知识检索。

本文件只放不依赖客户端的东西，tools.py 可以在开关关时照常导入。
"""
from __future__ import annotations

ALGO_VERSION = "0.1.0"
STANDARD_MODEL = f"algo:kge-standard-search@{ALGO_VERSION}"
SIMILAR_MODEL = f"algo:kge-item-similar-weknora@{ALGO_VERSION}"


class UpstreamError(RuntimeError):
    """WeKnora 不可用或超时（消息只含编码化原因，不含查询文本与返回内容）"""

    def __init__(self, timeout: bool, status: int | None = None) -> None:
        self.timeout = timeout
        self.status = status
        super().__init__("weknora timeout" if timeout else f"weknora unavailable ({status if status is not None else 'network'})")
