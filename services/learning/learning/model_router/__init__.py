"""平台模型路由骨架（冲刺 1-F，specs/model-router-sprint1/spec.md；ADR 04 正文与增补）。

所有模型调用都只经这里：调用方声明任务类型与数据级别，不指定模型。生产与试点环境不出境（用户决定 2026-09-28）。
"""
from .config import DEFAULT_PATH, RouterConfig, load, parse
from .ledger import CallRecord, LogSink, MemorySink
from .providers import FakeProvider, OpenAICompatProvider, ProviderError, ProviderReply
from .router import ModelRouter, egress_allowed
from .types import (Attempt, ConfigError, DataClass, Residency, RouteRejected, RouteRequest, RouteResult, RouteUnavailable, Task,
                    Usage)

__all__ = [
    "DEFAULT_PATH", "RouterConfig", "load", "parse", "CallRecord", "LogSink", "MemorySink", "FakeProvider", "OpenAICompatProvider",
    "ProviderError", "ProviderReply", "ModelRouter", "egress_allowed", "Attempt", "ConfigError", "DataClass", "Residency",
    "RouteRejected", "RouteRequest", "RouteResult", "RouteUnavailable", "Task", "Usage",
]
