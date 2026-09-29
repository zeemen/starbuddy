"""模型路由（spec 设计要点 3）：按任务取主备 → 按数据级别过滤 → 跳过熔断打开的 → 超时重试（退避）→ 换备选 → 没有就报错。

出境的两道防线：启动校验（config.parse / check_invariants）与这里的运行时过滤 `egress_allowed`。任何熔断、重试路径都只在
过滤后的候选里走，绝不回落到不允许的供应商（ADR 04 第 6 条）。每次调用都写一条只含元数据的调用记录，出任何异常也写。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping

from ..config import SUBJECTS
from .breaker import CircuitBreaker
from .config import SYNTHETIC_ENVS, RouterConfig, Target, Vendor, check_invariants
from .ledger import CallRecord, LedgerSink, LogSink, digest
from .providers import BREAKER_FAILURES, Provider, ProviderError, build_provider
from .types import (CHAT_TASKS, Attempt, DataClass, Residency, RouteRejected, RouteRequest, RouteResult, RouteUnavailable, Usage)

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
# 与插件 API 契约 SourceVersion 一致：prompt:<名称 [a-z0-9][a-z0-9-]{0,47}>@<版本 [A-Za-z0-9][A-Za-z0-9._-]{0,31}>（契约审查应改 1）
_PROMPT = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}$")
_PROMPT_VERSION = re.compile(r"^v?\d+(\.\d+){0,3}$")
SOURCE_VERSION = re.compile(r"^prompt:[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
_SAFE_MODEL = re.compile(r"[^A-Za-z0-9._:/@-]")
DEFAULT_DEADLINE_MS = 180_000  # 不传 deadline_ms 时的总时限（安全审查建议 10）
BACKOFF_BASE = 0.2  # 秒；第 n 次重试前等 0.2 × 2^(n-1)，或供应商给的 Retry-After（上限 30 秒）


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def egress_allowed(vendor: Vendor, data_class: DataClass, env: str) -> bool:
    """运行时第二道防线：境内总是允许；境外只在 dev、test（只放合成数据）、且只接 L0"""
    if vendor.residency is Residency.DOMESTIC:
        return True
    return env in SYNTHETIC_ENVS and data_class is DataClass.L0


class ModelRouter:
    def __init__(self, config: RouterConfig, providers: Mapping[str, Provider], *, sink: LedgerSink | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        check_invariants(config)
        self._cfg = config
        self._providers = dict(providers)
        self._sink = sink or LogSink()
        self._clock = clock
        self._sleep = sleep or _default_sleep
        self.breaker = CircuitBreaker(config.failure_threshold, config.cooldown_seconds, clock)

    @classmethod
    def from_config(cls, config: RouterConfig, environ: Mapping[str, str] | None = None, **kw) -> "ModelRouter":
        used = {t.vendor for r in config.routes.values() for t in r.targets}
        return cls(config, {v: build_provider(config.vendors[v], environ, config.env) for v in sorted(used)}, **kw)

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()

    def _validate(self, req: RouteRequest) -> None:
        if not req.usage_id or not _KEY.match(req.usage_id) or not req.idempotency_key or not _KEY.match(req.idempotency_key):
            raise RouteRejected("缺少或不合格式的 usage_id / idempotency_key（CLAUDE.md 规则 4）")
        if not _PROMPT.match(req.prompt_template or "") or not _PROMPT_VERSION.match(req.prompt_version or ""):
            raise RouteRejected("缺少或不合格式的 prompt_template / prompt_version（ADR 04 第 5 条，契约 SourceVersion）")
        if req.subject is not None and req.subject not in SUBJECTS:
            raise RouteRejected("subject 只收平台学科编码")
        if not req.messages:
            raise RouteRejected("messages 为空")
        if req.deadline_ms is not None and not 0 < req.deadline_ms <= 600_000:
            raise RouteRejected("deadline_ms 超出范围")
        if req.task not in CHAT_TASKS:
            raise RouteRejected(f"任务 {req.task.value} 在骨架里没有适配器")

    def candidates(self, req: RouteRequest) -> tuple[Target, ...]:
        """数据级别允许的候选（按主、备顺序）；不看熔断"""
        route = self._cfg.routes.get(req.task)
        if route is None:
            raise RouteUnavailable(f"任务 {req.task.value} 没有配置路由")
        if req.data_class not in route.data_classes:
            raise RouteRejected(f"任务 {req.task.value} 不接 {req.data_class.value} 数据")
        return tuple(t for t in route.targets if egress_allowed(self._cfg.vendors[t.vendor], req.data_class, self._cfg.env))

    async def route(self, req: RouteRequest) -> RouteResult:
        started = self._clock()
        source_version = f"prompt:{req.prompt_template}@{req.prompt_version}"
        attempts: list[Attempt] = []
        written = False

        def finish(outcome: str, model_version: str | None, usage: Usage, mismatch: bool = False, reported: str | None = None) -> None:
            nonlocal written
            written = True
            self._sink.write(CallRecord(
                env=self._cfg.env, task=req.task.value, data_class=req.data_class.value,
                subject=req.subject if req.subject in SUBJECTS else None,
                usage_ref=digest(req.usage_id or ""), idempotency_ref=digest(req.idempotency_key or ""),
                source_version=source_version if SOURCE_VERSION.match(source_version) else "invalid",
                outcome=outcome, model_version=model_version, attempts=tuple(attempts),
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                elapsed_ms=int((self._clock() - started) * 1000), model_mismatch=mismatch, reported_model=reported))

        try:
            return await self._route(req, started, source_version, attempts, finish)
        finally:
            if not written:  # 取消或未预期的异常：也留一条记录
                finish("error", None, Usage())

    async def _route(self, req: RouteRequest, started: float, source_version: str, attempts: list[Attempt], finish) -> RouteResult:
        try:
            self._validate(req)
            targets = self.candidates(req)
        except RouteRejected:
            finish("rejected", None, Usage())
            raise
        except RouteUnavailable:
            finish("unavailable", None, Usage())
            raise
        route = self._cfg.routes[req.task]
        deadline = started + (req.deadline_ms or DEFAULT_DEADLINE_MS) / 1000

        for target in targets:
            vendor = self._cfg.vendors[target.vendor]
            if not egress_allowed(vendor, req.data_class, self._cfg.env):  # 防御：过滤后的候选必须仍然允许
                raise AssertionError("egress filter bypassed")
            provider = self._providers[target.vendor]
            for n in range(route.max_retries + 1):
                ticket = self.breaker.acquire(target.vendor, req.task.value)
                if ticket is None:
                    attempts.append(Attempt(target.vendor, target.model_version, "skipped_breaker_open", 0))
                    break
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self.breaker.release(ticket)
                    break
                timeout = min(route.timeout_seconds, remaining)
                t0 = self._clock()
                kind: str | None = None
                retry_after: float | None = None
                try:
                    reply = await asyncio.wait_for(
                        provider.complete(target, req.messages, idempotency_key=req.idempotency_key, timeout=timeout), timeout)
                except asyncio.CancelledError:
                    self.breaker.release(ticket)
                    raise
                except ProviderError as e:
                    kind, retry_after = e.kind, e.retry_after
                except asyncio.TimeoutError:
                    kind = "timeout"
                except Exception:  # noqa: BLE001 适配器的任何意外都算供应商返回异常，保证释放试探名额（安全审查应改 3）
                    kind = "bad_response"
                if kind is None:
                    attempts.append(Attempt(target.vendor, target.model_version, "ok", int((self._clock() - t0) * 1000)))
                    self.breaker.success(ticket)
                    reported = _SAFE_MODEL.sub("", reply.reported_model)[:128] if reply.reported_model else None
                    mismatch = reported is not None and reported != target.api_name
                    finish("ok", target.model_version, reply.usage, mismatch, reported)
                    return RouteResult(output=reply.output, model_version=target.model_version, source_version=source_version,
                                       attempts=tuple(attempts), usage=reply.usage, model_mismatch=mismatch, reported_model=reported)
                attempts.append(Attempt(target.vendor, target.model_version, kind, int((self._clock() - t0) * 1000)))
                if kind in BREAKER_FAILURES:
                    self.breaker.failure(ticket)
                else:
                    self.breaker.release(ticket)
                if kind not in ("timeout", "rate_limited", "server") or n == route.max_retries:
                    break
                wait = retry_after if retry_after is not None else BACKOFF_BASE * (2 ** n)
                if deadline - self._clock() <= wait:
                    break
                await self._sleep(wait)
        finish("unavailable", None, Usage())
        raise RouteUnavailable(f"任务 {req.task.value} 没有可用的、数据级别允许的供应商", tuple(attempts))
