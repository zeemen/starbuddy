"""模型路由（spec 设计要点 3）：按任务取主备 → 按数据级别过滤 → 跳过熔断打开的 → 超时重试 → 换备选 → 没有就报错。

出境的两道防线：启动校验（config.parse）与这里的运行时过滤 `egress_allowed`。任何熔断、重试路径都只在过滤后的候选里走，
绝不回落到不允许的供应商（ADR 04 第 6 条）。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping

from .breaker import CircuitBreaker
from .config import NO_EGRESS_ENVS, RouterConfig, Target, Vendor
from .ledger import CallRecord, LedgerSink, LogSink, digest
from .providers import BREAKER_FAILURES, Provider, ProviderError, build_provider
from .types import (CHAT_TASKS, Attempt, DataClass, Residency, RouteRejected, RouteRequest, RouteResult, RouteUnavailable, Usage)

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_PROMPT = re.compile(r"^[a-z0-9][a-z0-9._/-]{1,63}$")
_PROMPT_VERSION = re.compile(r"^v?\d+(\.\d+){0,3}$")


def egress_allowed(vendor: Vendor, data_class: DataClass, env: str) -> bool:
    """运行时第二道防线：境内总是允许；境外只在 dev、test、staging 环境、且只接 L0（合成数据）"""
    if vendor.residency is Residency.DOMESTIC:
        return True
    return env not in NO_EGRESS_ENVS and data_class is DataClass.L0


class ModelRouter:
    def __init__(self, config: RouterConfig, providers: Mapping[str, Provider], *, sink: LedgerSink | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = config
        self._providers = dict(providers)
        self._sink = sink or LogSink()
        self._clock = clock
        self.breaker = CircuitBreaker(config.failure_threshold, config.cooldown_seconds, clock)

    @classmethod
    def from_config(cls, config: RouterConfig, environ: Mapping[str, str] | None = None, **kw) -> "ModelRouter":
        used = {t.vendor for r in config.routes.values() for t in r.targets}
        return cls(config, {v: build_provider(config.vendors[v], environ) for v in sorted(used)}, **kw)

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()

    def _validate(self, req: RouteRequest) -> None:
        if not req.usage_id or not _KEY.match(req.usage_id) or not req.idempotency_key or not _KEY.match(req.idempotency_key):
            raise RouteRejected("缺少或不合格式的 usage_id / idempotency_key（CLAUDE.md 规则 4）")
        if not _PROMPT.match(req.prompt_template or "") or not _PROMPT_VERSION.match(req.prompt_version or ""):
            raise RouteRejected("缺少或不合格式的 prompt_template / prompt_version（ADR 04 第 5 条）")
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

        def finish(outcome: str, model_version: str | None, usage: Usage, mismatch: bool = False) -> None:
            self._sink.write(CallRecord(
                env=self._cfg.env, task=req.task.value, data_class=req.data_class.value, subject=req.subject,
                usage_ref=digest(req.usage_id or ""), idempotency_ref=digest(req.idempotency_key or ""),
                source_version=source_version, outcome=outcome, model_version=model_version, attempts=tuple(attempts),
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                elapsed_ms=int((self._clock() - started) * 1000), model_mismatch=mismatch))

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
        deadline = None if req.deadline_ms is None else started + req.deadline_ms / 1000

        for target in targets:
            vendor = self._cfg.vendors[target.vendor]
            # 防御：过滤后的候选必须仍然允许（变异测试守住这条）
            if not egress_allowed(vendor, req.data_class, self._cfg.env):
                raise AssertionError("egress filter bypassed")
            task_key = req.task.value
            if not self.breaker.allow(target.vendor, task_key):
                attempts.append(Attempt(target.vendor, target.model_version, "skipped_breaker_open", 0))
                continue
            provider = self._providers[target.vendor]
            for _ in range(route.max_retries + 1):
                remaining = None if deadline is None else deadline - self._clock()
                if remaining is not None and remaining <= 0:
                    break
                timeout = route.timeout_seconds if remaining is None else min(route.timeout_seconds, remaining)
                t0 = self._clock()
                try:
                    reply = await asyncio.wait_for(
                        provider.complete(target, req.messages, idempotency_key=req.idempotency_key, timeout=timeout), timeout)
                except asyncio.CancelledError:
                    self.breaker.release_probe(target.vendor, task_key)
                    raise
                except (ProviderError, asyncio.TimeoutError) as e:
                    kind = e.kind if isinstance(e, ProviderError) else "timeout"
                    attempts.append(Attempt(target.vendor, target.model_version, kind, int((self._clock() - t0) * 1000)))
                    if kind in BREAKER_FAILURES:
                        self.breaker.record_failure(target.vendor, task_key)
                    else:
                        self.breaker.release_probe(target.vendor, task_key)
                    if not (isinstance(e, ProviderError) and e.retryable or kind == "timeout"):
                        break
                    if not self.breaker.allow(target.vendor, task_key):
                        break
                    continue
                attempts.append(Attempt(target.vendor, target.model_version, "ok", int((self._clock() - t0) * 1000)))
                self.breaker.record_success(target.vendor, task_key)
                mismatch = reply.reported_model is not None and reply.reported_model != target.api_name
                finish("ok", target.model_version, reply.usage, mismatch)
                return RouteResult(output=reply.output, model_version=target.model_version, source_version=source_version,
                                   attempts=tuple(attempts), usage=reply.usage)
        finish("unavailable", None, Usage())
        raise RouteUnavailable(f"任务 {req.task.value} 没有可用的、数据级别允许的供应商", tuple(attempts))
