"""调用记录（spec 设计要点 4）：只有元数据，不含提示词、对话原文、作答内容与素材（ADR 04 增补第 6 条）。

本任务只定义接口与两种落地（结构化日志、内存）；落库表随计量 v2 的 usage_ledger 一起设计（spec 待人确认 #2）。
usage_id 与幂等键只记 sha256 截断，便于和计量账本对账而不暴露原值。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from typing import Protocol

from .types import Attempt

log = logging.getLogger("starbuddy.model_router")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CallRecord:
    env: str
    task: str
    data_class: str
    subject: str | None
    usage_ref: str
    idempotency_ref: str
    source_version: str
    outcome: str  # ok | unavailable | rejected
    model_version: str | None
    attempts: tuple[Attempt, ...]
    input_tokens: int
    output_tokens: int
    elapsed_ms: int
    model_mismatch: bool = False  # 供应商报告的模型名与配置不一致（按配置记录，告警）


class LedgerSink(Protocol):
    def write(self, record: CallRecord) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def write(self, record: CallRecord) -> None:
        self.records.append(record)


class LogSink:
    def write(self, record: CallRecord) -> None:
        d = asdict(record)
        level = logging.WARNING if record.model_mismatch or record.outcome != "ok" else logging.INFO
        log.log(level, json.dumps({"event": "model_call", **d}, ensure_ascii=False, sort_keys=True))
