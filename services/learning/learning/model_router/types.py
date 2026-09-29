"""模型路由的数据类型（specs/model-router-sprint1/spec.md 设计要点 1）。

调用方声明任务类型与数据级别，不指定模型；路由返回真实的 model_version 与 source_version。
请求里的 messages 与返回的 output 只在内存里经过路由，不进入调用记录与日志（设计要点 4）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Task(str, enum.Enum):
    """九类任务（《底层大模型选型建议 V1.0》，ADR 04 增补）"""

    T1 = "T1"  # 教学对话
    T2 = "T2"  # 解题与判分推理
    T3 = "T3"  # 拍照识题与手写识别
    T4 = "T4"  # 作文批改
    T5 = "T5"  # 英语口语评测
    T6 = "T6"  # 语音交互
    T7 = "T7"  # 知识库检索（向量）
    T8 = "T8"  # 后台批量生产
    T9 = "T9"  # 内容安全与人设守卫


# 骨架只有文本对话类协议；T5、T6（语音）与 T7（向量）只做配置校验，调用时拒绝（spec 设计要点 5）
CHAT_TASKS = frozenset({Task.T1, Task.T2, Task.T3, Task.T4, Task.T8, Task.T9})


class DataClass(str, enum.Enum):
    """数据级别（ADR 09 §2）"""

    L0 = "L0"  # 非个人数据：内容、代码、合成数据
    L1 = "L1"  # 去标识化数据
    L2 = "L2"  # 个人信息
    L3 = "L3"  # 敏感个人信息

    @property
    def rank(self) -> int:
        return int(self.value[1])


class Residency(str, enum.Enum):
    DOMESTIC = "domestic"
    FOREIGN = "foreign"


class RouterError(Exception):
    """路由错误的基类；message 只含固定文本与编码，不含请求内容"""

    code = "ROUTER_ERROR"


class ConfigError(RouterError):
    code = "ROUTE_CONFIG_INVALID"


class RouteRejected(RouterError):
    """请求本身不合要求（缺计量字段、数据级别不允许、任务不支持）；不重试"""

    code = "ROUTE_REJECTED"


class RouteUnavailable(RouterError):
    """没有可用的、数据级别允许的供应商；绝不回落到不允许的供应商"""

    code = "ROUTE_UNAVAILABLE"

    def __init__(self, message: str, attempts: tuple["Attempt", ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class RouteRequest:
    task: Task
    data_class: DataClass
    subject: str | None
    prompt_template: str
    prompt_version: str
    messages: tuple[dict[str, Any], ...]
    usage_id: str
    idempotency_key: str
    deadline_ms: int | None = None

    def __repr__(self) -> str:  # 不让 messages 进异常栈与日志
        return (f"RouteRequest(task={self.task.value}, data_class={self.data_class.value}, subject={self.subject!r}, "
                f"prompt={self.prompt_template}@{self.prompt_version}, messages=<{len(self.messages)} redacted>)")


@dataclass(frozen=True)
class Attempt:
    vendor: str
    model_version: str
    status: str  # ok | timeout | rate_limited | server | auth | client | bad_response | skipped_breaker_open
    elapsed_ms: int


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class RouteResult:
    output: str
    model_version: str
    source_version: str
    attempts: tuple[Attempt, ...]
    usage: Usage = field(default_factory=Usage)

    def __repr__(self) -> str:
        return f"RouteResult(model_version={self.model_version}, source_version={self.source_version}, output=<redacted>)"
