"""兜底检索命中 → 结构化“补页面信号”（KGE 第四章：兜底检索命中是补页面的信号）。

查询文本可能来自学生对话（契约：node.resolve 的 text 为 L2），所以信号里：
- 不记原始查询文本，也不记分词结果；只记查询的带密钥摘要（HMAC-SHA256 截 16 位）与查询词个数——同一部署内
  同一问法可以聚合计数，但不能由摘要反推原文（密钥不落盘，默认每个进程随机生成）；
- 不记任何学生标识（接口里根本没有学生参数）；
- 字段白名单固定（SIGNAL_FIELDS），序列化前逐键核对。
信号的去处：`SignalSink`（内存、JSON Lines 文件）；按节点与原因聚合后交教研排补页面的优先级。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from .tokenize import normalize

SIGNAL_FORMAT = "kge-gap-signal/1"
REASONS = ("no_node", "low_confidence", "no_page", "page_low_coverage")
SIGNAL_FIELDS = ("format", "node_id", "reason", "query_digest", "query_terms", "hits", "top_score")


@dataclass(frozen=True)
class GapSignal:
    node_id: str | None      # 定位到的节点（没有定位到时为空）
    reason: str              # REASONS 之一
    query_digest: str        # HMAC-SHA256(密钥, 规范化查询)[:16]
    query_terms: int         # 查询词个数（不含停用词）
    hits: tuple[str, ...]    # 兜底命中的来源键（page:<ID>@<版本>#<段落> 或 atom:<原子键>）
    top_score: float

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError("未登记的兜底原因")

    def to_dict(self) -> dict:
        d = {"format": SIGNAL_FORMAT, "node_id": self.node_id, "reason": self.reason, "query_digest": self.query_digest,
             "query_terms": self.query_terms, "hits": list(self.hits), "top_score": self.top_score}
        if tuple(d) != SIGNAL_FIELDS:
            raise ValueError("补页面信号字段不在白名单")
        return d


class QueryDigester:
    def __init__(self, key: bytes | None = None) -> None:
        if key is not None and len(key) < 16:
            raise ValueError("摘要密钥至少 16 字节")
        self._key = key if key is not None else secrets.token_bytes(32)

    def digest(self, text: str) -> str:
        return hmac.new(self._key, normalize(text).strip().encode("utf-8"), hashlib.sha256).hexdigest()[:16]


class SignalSink(Protocol):
    def emit(self, signal: GapSignal) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.signals: list[GapSignal] = []

    def emit(self, signal: GapSignal) -> None:
        self.signals.append(signal)


class JsonlSink:
    """追加写 JSON Lines（每行一条信号）；文件由部署方放在服务日志目录，随日志轮转"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, signal: GapSignal) -> None:
        line = json.dumps(signal.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)


def summarize(signals: Iterable[GapSignal]) -> list[dict]:
    """按（节点，原因）聚合：次数、不同问法数（按摘要去重）。排序确定"""
    agg: dict[tuple[str, str], tuple[int, set[str]]] = {}
    for s in signals:
        k = (s.node_id or "", s.reason)
        n, ds = agg.get(k, (0, set()))
        ds.add(s.query_digest)
        agg[k] = (n + 1, ds)
    return [{"node_id": k[0] or None, "reason": k[1], "count": n, "distinct_queries": len(ds)}
            for k, (n, ds) in sorted(agg.items(), key=lambda kv: (-kv[1][0], kv[0]))]
