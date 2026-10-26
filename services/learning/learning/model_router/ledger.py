"""调用记录（spec 设计要点 4）：只有元数据，不含提示词、对话原文、作答内容与素材（ADR 04 增补第 6 条）。

三种落地：结构化日志（LogSink，完整兜底）、内存（MemorySink，测试用）与数据库（DbSink，写 platform.model_call_log，迁移 0060；
specs/plt-model-call-log 决定记录第 1、2 条）。TeeSink 把一条记录交给多个落地，单个落地出错不影响其他的。
usage_id 与幂等键只记 sha256 截断，便于和计量账本对账而不暴露原值。

DbSink（决定记录第 2 条：有界队列异步批量）：
- write() 在调用路径上只做字段格式核对（与 0060 的列约束同一口径）与入队，不等数据库；格式不合格计“写库失败”、队列满计“丢弃”，
  两者都不影响路由结果，结构化日志仍有完整的一条；
- 后台任务每 flush_seconds 秒或攒够一批时批量写（单独的小连接池，executemany，一批一个事务；取连接到提交整段限时 write_timeout）；写库失败只计数并记一行
  只含行数与异常类名的日志（不记异常消息：约束报错的 DETAIL 会带出行内容）；
- aclose() 退出时尽力刷写：限时内把队列写完，写不完的计“丢弃”。
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from .config import Price
from .types import Attempt

log = logging.getLogger("starbuddy.model_router")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def estimate_cost(price: Price | None, input_tokens: int, output_tokens: int) -> float | None:
    """元；没有单价就不估算（None）。保留 6 位小数，避免浮点尾数进日志"""
    if price is None:
        return None
    return round((input_tokens * price.input_per_million + output_tokens * price.output_per_million) / 1_000_000, 6)


@dataclass(frozen=True)
class CallRecord:
    env: str
    task: str
    data_class: str
    subject: str | None
    usage_ref: str
    idempotency_ref: str
    source_version: str
    outcome: str  # ok | unavailable | rejected | budget_exceeded | error
    model_version: str | None
    attempts: tuple[Attempt, ...]
    input_tokens: int
    output_tokens: int
    elapsed_ms: int
    model_mismatch: bool = False  # 供应商报告的模型名与配置不一致（按配置记录，告警）
    reported_model: str | None = None  # 供应商报告的模型名（元数据，截断到 128 字符、只留安全字符）
    # 成本估算（LT-02 决定记录第 4 条）：按路由表 pricing 的单价（元 / 百万 token）× 供应商报告的 token；
    # 没有单价（缺省为空）或调用没成功则为 None。只是估算，不参与计量与扣次
    estimated_cost_cny: float | None = None


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


class TeeSink:
    """一条调用记录交给多个落地；单个落地出任何错只丢它自己这一条（记一行只含落地类名的日志），其余照写、路由结果不变"""

    def __init__(self, *sinks: Any) -> None:
        self.sinks = sinks

    def write(self, record: CallRecord) -> None:
        for s in self.sinks:
            try:
                s.write(record)
            except Exception:  # noqa: BLE001
                try:
                    log.warning(json.dumps({"event": "ledger_sink_failed", "sink": type(s).__name__[:48]}))
                except Exception:  # noqa: BLE001 —— 日志本身也坏了：放弃这一行，不影响调用
                    pass


# ---------------------------------------------------------------- 数据库落地（platform.model_call_log，迁移 0060）
# 列顺序与 0060 的列级 INSERT 授权一致（id、recorded_at 取缺省；没有任何学生 / 账号 / 原文类的列）
MODEL_CALL_COLUMNS = ("occurred_at", "env", "task", "data_class", "subject", "vendor", "model", "model_release", "model_version",
                      "prompt_template", "prompt_version", "source_version", "outcome", "attempt_count", "fallback", "attempt_statuses",
                      "input_tokens", "output_tokens", "estimated_cost_cny", "elapsed_ms", "model_mismatch", "usage_ref",
                      "idempotency_ref")
INSERT_SQL = ("INSERT INTO platform.model_call_log (" + ", ".join(MODEL_CALL_COLUMNS) + ") VALUES ("
              + ", ".join(f"${i}::platform.subject" if c == "subject" else f"${i}" for i, c in enumerate(MODEL_CALL_COLUMNS, 1)) + ")")
# 启动核对（学习服务 app.py）：表在、当前登录角色对全部写入列有 INSERT 权限
TABLE = "platform.model_call_log"

# 与 0060 的列约束同一口径（写库前先在进程里核对，不合格的不入队，免得一条坏行拖垮一批）
_ENVS = frozenset({"dev", "test", "staging", "pilot", "prod"})
_TASK = re.compile(r"^T[1-9]$")
_DATA_CLASSES = frozenset({"L0", "L1", "L2", "L3"})
_SUBJECTS = frozenset({"wenli", "aya", "zhixing", "echo"})
_VENDOR = r"[a-z0-9][a-z0-9-]{0,47}"
_MODEL = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_MODEL_VERSION = re.compile(rf"^model:({_VENDOR})/({_MODEL})@({_MODEL})$")
_SOURCE_VERSION = re.compile(r"^prompt:([a-z0-9][a-z0-9-]{0,47})@([A-Za-z0-9][A-Za-z0-9._-]{0,31})$")
_OUTCOMES = frozenset({"ok", "unavailable", "rejected", "budget_exceeded", "error"})
_ATTEMPT_STATUSES = frozenset({"ok", "timeout", "rate_limited", "server", "auth", "client", "bad_response", "skipped_breaker_open"})
_REF = re.compile(r"^[0-9a-f]{16}$")
_INT_MAX = 2**31 - 1
_COST_MAX = Decimal("99999999.999999")  # numeric(14, 6)
MAX_ATTEMPTS = 64


def _int(v: Any, hi: int = _INT_MAX) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= hi:
        raise ValueError("整数字段越界")
    return v


def to_row(record: CallRecord, occurred_at: datetime) -> tuple[Any, ...]:
    """调用记录 → 一行（列顺序同 MODEL_CALL_COLUMNS）。字段格式不合 0060 的约束抛 ValueError（不带取值）。

    - 供应商、模型、版本：成功时拆 model_version；失败时拆最后一次尝试的目标；没有任何尝试（拒绝、无候选、护栏）为空；
    - 提示词模板与版本：从 source_version 拆出（只有名字与版本号）；source_version 为 invalid 时两列为空；
    - 是否切备：尝试过不止一个目标（含主选熔断跳过、换到备选）。"""
    if record.env not in _ENVS or not _TASK.match(record.task) or record.data_class not in _DATA_CLASSES:
        raise ValueError("环境、任务或数据级别不合格")
    if record.subject is not None and record.subject not in _SUBJECTS:
        raise ValueError("学科不合格")
    if record.outcome not in _OUTCOMES:
        raise ValueError("结果不合格")
    if not _REF.match(record.usage_ref or "") or not _REF.match(record.idempotency_ref or ""):
        raise ValueError("摘要不合格")
    attempts = tuple(record.attempts)
    if len(attempts) > MAX_ATTEMPTS or any(a.status not in _ATTEMPT_STATUSES for a in attempts):
        raise ValueError("尝试次数或尝试状态不合格")
    if (record.outcome == "ok") != (record.model_version is not None):
        raise ValueError("只有成功的调用带 model_version")
    target = record.model_version if record.model_version is not None else (attempts[-1].model_version if attempts else None)
    vendor = model = release = None
    if target is not None:
        m = _MODEL_VERSION.match(target)
        if not m:
            raise ValueError("model_version 不合格")
        vendor, model, release = m.groups()
    if record.source_version == "invalid":
        template = version = None
    else:
        s = _SOURCE_VERSION.match(record.source_version or "")
        if not s:
            raise ValueError("source_version 不合格")
        template, version = s.groups()
    cost = None
    if record.estimated_cost_cny is not None:
        if isinstance(record.estimated_cost_cny, bool) or not isinstance(record.estimated_cost_cny, (int, float)):
            raise ValueError("估算成本不合格")
        cost = Decimal(f"{float(record.estimated_cost_cny):.6f}")
        if not Decimal(0) <= cost <= _COST_MAX:
            raise ValueError("估算成本越界")
    if occurred_at.tzinfo is None:
        raise ValueError("时间必须带时区")
    return (occurred_at, record.env, record.task, record.data_class, record.subject, vendor, model, release, record.model_version,
            template, version, record.source_version, record.outcome, len(attempts),
            len({a.model_version for a in attempts}) > 1, [a.status for a in attempts],
            _int(record.input_tokens), _int(record.output_tokens), cost, _int(record.elapsed_ms), bool(record.model_mismatch),
            record.usage_ref, record.idempotency_ref)


def _utcnow() -> datetime:
    return datetime.fromtimestamp(time.time(), timezone.utc)


def _bump(hook: Callable[[int], None] | None, n: int) -> None:
    if hook is None or n <= 0:
        return
    try:
        hook(n)
    except Exception:  # noqa: BLE001 —— 计数出错只丢这一次计数
        pass


class DbSink:
    """有界队列 + 后台批量写 platform.model_call_log（决定记录第 2 条）。write() 不等数据库、不抛异常。"""

    def __init__(self, pool: Any, *, max_queue: int = 1000, batch_size: int = 100, flush_seconds: float = 1.0,
                 write_timeout: float = 5.0, on_written: Callable[[int], None] | None = None,
                 on_dropped: Callable[[int], None] | None = None, on_failed: Callable[[int], None] | None = None,
                 now: Callable[[], datetime] = _utcnow) -> None:
        if max_queue < 1 or batch_size < 1 or flush_seconds <= 0 or write_timeout <= 0:
            raise ValueError("队列长度、批量、刷写间隔与写库时限都必须为正")
        self._pool = pool
        self._queue: collections.deque[tuple[Any, ...]] = collections.deque()
        self.max_queue, self.batch_size, self.flush_seconds, self.write_timeout = max_queue, batch_size, flush_seconds, write_timeout
        self._on_written, self._on_dropped, self._on_failed = on_written, on_dropped, on_failed
        self._now = now
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._inflight = 0

    @property
    def pending(self) -> int:
        return len(self._queue)

    def write(self, record: CallRecord) -> None:
        try:
            row = to_row(record, self._now())
        except Exception:  # noqa: BLE001 —— 格式不合格：不入队，计写库失败（结构化日志里仍有完整的一条）
            _bump(self._on_failed, 1)
            return
        if self._closing or len(self._queue) >= self.max_queue:
            _bump(self._on_dropped, 1)
            return
        self._queue.append(row)
        if self._wake is not None and len(self._queue) >= self.batch_size:
            self._wake.set()

    def start(self) -> None:
        """在事件循环里起后台写库任务（学习服务 lifespan 里调用）"""
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self.flush_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self.flush()
            if self._closing:
                return

    async def flush(self) -> None:
        """把当前队列按批写完（每批一个事务）；写库失败的那批只计数，不重试、不回队"""
        while self._queue:
            batch = [self._queue.popleft() for _ in range(min(self.batch_size, len(self._queue)))]
            await self._insert(batch)

    async def _write(self, batch: list[tuple[Any, ...]]) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(INSERT_SQL, batch)

    async def _insert(self, batch: list[tuple[Any, ...]]) -> None:
        self._inflight = len(batch)
        try:
            # 时限包住整段：取连接（池满、库连不上时 acquire 会一直等）、开事务、写入、提交，任何一步超时都算这批写库失败
            await asyncio.wait_for(self._write(batch), self.write_timeout)
        except Exception as e:  # noqa: BLE001 —— 连接断开、超时、约束、权限：只计数与记类名，不记异常消息（会带出行内容）
            self._inflight = 0
            _bump(self._on_failed, len(batch))
            try:
                log.warning(json.dumps({"event": "model_call_log_write_failed", "rows": len(batch), "error_class": type(e).__name__[:48]}))
            except Exception:  # noqa: BLE001
                pass
        else:
            self._inflight = 0
            _bump(self._on_written, len(batch))
        # 被取消（退出时超时）不在这里清零：aclose 把正在写的这批计为丢弃

    async def aclose(self, timeout: float = 5.0) -> None:
        """退出时尽力刷写：不再收新记录，限时内把队列写完；超时还没写完的（含正在写的那批）计丢弃"""
        self._closing = True
        task = self._task
        if task is None:
            try:
                await asyncio.wait_for(self.flush(), timeout)
            except Exception:  # noqa: BLE001 —— 含超时：下面按剩余行数计丢弃
                pass
        else:
            assert self._wake is not None
            self._wake.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except Exception:  # noqa: BLE001 —— 含超时
                task.cancel()
                try:
                    await task
                except BaseException:  # noqa: BLE001 —— 取消或任务里的任何错误：下面按剩余行数计丢弃
                    pass
        _bump(self._on_dropped, len(self._queue) + self._inflight)
        self._queue.clear()
        self._inflight = 0
