"""真实模型冒烟（LT-02 范围第 6 条）：`make model-smoke SMOKE_ENV=staging MAX_CALLS=<n>`，**由人在有凭据的 staging 执行环境里运行**，不进 make check、不进 CI。

做什么：按路由表当前环境的配置（先整节校验：占位未填、未批准、缺 allowed_hosts、未带日期快照名、缺成本护栏一律拒绝），对每个不同的路由目标
（主、备分别算）各发一次合成的 L0 请求（不重试），确认地址、Key、快照名、出网白名单都通；只打印元数据（状态、耗时、token、
model_version、供应商报告的模型名是否一致），不打印回复原文。

去重键（契约审查建议 4）：（供应商 ID，模型，版本，发给供应商的快照名 api_name）四项都相同才算同一个目标——同一快照被多个任务引用
（例如 staging 的 T2 与 T4 主选、T8 与 T1 的目标）只冒烟一次，挂在第一个用到它的对话类任务（T 编号最小）上，用那一处登记的推理参数；
推理参数（params）与任务的数据级别不进去重键（冒烟只验证连通与快照名，统一用 L0）。L0 不在路由数据级别里的任务跳过。

限次：调用次数 = 不同路由目标的个数（staging 现为 5 个），必须显式给 --max-calls，预计超过就拒绝；硬上限 HARD_MAX_CALLS。
环境：只收 staging 与 dev（dev 可对本机桩演练）；pilot、prod、test 一律拒绝（test 用假供应商，跑 make learning-test）。
凭据：只从执行环境的变量读取（路由表登记的变量名），不进命令行参数、不进日志；错误信息不带地址与 Key。
退出码：0 全部成功；1 有目标失败；2 拒绝运行。
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from learning.model_router import (DEFAULT_PATH, ConfigError, DataClass, MemorySink, ModelRouter, RouteRejected, RouteRequest,  # noqa: E402
                                   RouteUnavailable, Task, parse)
from learning.model_router.config import Route, RouterConfig, Target  # noqa: E402
from learning.model_router.types import CHAT_TASKS  # noqa: E402

SMOKE_ENVS = ("staging", "dev")
HARD_MAX_CALLS = 12
PROMPT_TEMPLATE, PROMPT_VERSION = "model-smoke", "1"
# 合成冒烟消息（L0：不含任何个人信息；不是人设 Prompt）
MESSAGES = ({"role": "system", "content": "这是平台连通性冒烟测试，内容全部是合成数据。"},
            {"role": "user", "content": "请只回复两个字母：OK"})


class SmokeRefused(Exception):
    pass


def plan(doc: Any, env: str) -> tuple[RouterConfig, list[tuple[Task, Target]]]:
    """整节校验后，列出每个不同的路由目标（任务取第一个用到它的对话类任务）"""
    if env not in SMOKE_ENVS:
        raise SmokeRefused(f"{env} 环境不跑真实模型冒烟：只收 {'、'.join(SMOKE_ENVS)}（pilot、prod 拒绝；test 用假供应商，跑 make learning-test）")
    try:
        cfg = parse(doc, env)
    except ConfigError as e:
        raise SmokeRefused(f"模型路由配置在 {env} 环境不可用：{e}") from None
    seen: set[tuple[str, str, str, str]] = set()
    out: list[tuple[Task, Target]] = []
    for task in sorted(cfg.routes, key=lambda t: int(t.value[1:])):
        r = cfg.routes[task]
        if task not in CHAT_TASKS or DataClass.L0 not in r.data_classes:
            continue
        for t in r.targets:
            key = (t.vendor, t.model, t.version, t.api_name)
            if key not in seen:
                seen.add(key)
                out.append((task, t))
    if not out:
        raise SmokeRefused(f"{env} 环境没有可冒烟的对话类路由")
    return cfg, out


def single_target(cfg: RouterConfig, task: Task, target: Target) -> RouterConfig:
    """只含一个目标的路由（L0、不重试、不设备选）：主、备分别验证；ModelRouter 构造时仍核对不变量"""
    route = Route(task=task, data_classes=frozenset({DataClass.L0}), primary=target, backup=None,
                  timeout_seconds=cfg.routes[task].timeout_seconds, max_retries=0)
    return dataclasses.replace(cfg, routes={task: route}, budgets={})


RouterFactory = Callable[[RouterConfig, MemorySink], ModelRouter]


def run(env: str, *, max_calls: int | None, routes_path: Path = DEFAULT_PATH, environ: Mapping[str, str] | None = None,
        factory: RouterFactory | None = None, out: Callable[[str], None] = print) -> int:
    try:
        if env not in SMOKE_ENVS:
            raise SmokeRefused(f"{env} 环境不跑真实模型冒烟：只收 {'、'.join(SMOKE_ENVS)}（pilot、prod 拒绝；test 用假供应商，跑 make learning-test）")
        if max_calls is None:
            raise SmokeRefused("必须显式给 --max-calls（由人定调用次数）")
        if not 1 <= max_calls <= HARD_MAX_CALLS:
            raise SmokeRefused(f"--max-calls 应在 1–{HARD_MAX_CALLS} 之间")
        doc = yaml.safe_load(Path(routes_path).read_text(encoding="utf-8"))
        cfg, targets = plan(doc, env)
        out(f"model-smoke: {env} 环境预计调用 {len(targets)} 次（每个路由目标一次，L0 合成消息，不重试）")
        if len(targets) > max_calls:
            raise SmokeRefused(f"预计调用 {len(targets)} 次，超过 --max-calls {max_calls}")
    except SmokeRefused as e:
        out(f"model-smoke: 拒绝运行：{e}")
        return 2
    source = os.environ if environ is None else environ

    def default_factory(c: RouterConfig, sink: MemorySink) -> ModelRouter:
        return ModelRouter.from_config(c, source, sink=sink)

    make = factory or default_factory
    failed = 0
    nonce = uuid.uuid4().hex[:12]
    for i, (task, target) in enumerate(targets):
        sink = MemorySink()
        try:
            router = make(single_target(cfg, task, target), sink)
        except ConfigError as e:
            out(f"  {target.model_version}（{task.value}）：未就绪：{e}")
            failed += 1
            continue

        async def go(r: ModelRouter = router, t: Task = task, n: int = i) -> Any:
            try:
                return await r.route(RouteRequest(task=t, data_class=DataClass.L0, subject=None, prompt_template=PROMPT_TEMPLATE,
                                                  prompt_version=PROMPT_VERSION, messages=MESSAGES, usage_id=f"smoke-{nonce}-{n}",
                                                  idempotency_key=f"smoke-{nonce}-{n}-k", deadline_ms=60_000))
            finally:
                await r.aclose()

        try:
            res = asyncio.run(go())
        except (RouteRejected, RouteUnavailable) as e:
            kinds = ",".join(a.status for a in getattr(e, "attempts", ())) or e.code
            out(f"  {target.model_version}（{task.value}）：失败（{kinds}）")
            failed += 1
            continue
        rec = sink.records[-1] if sink.records else None
        tokens = f"{rec.input_tokens}+{rec.output_tokens}" if rec else "?"
        elapsed = rec.elapsed_ms if rec else -1
        note = f"；供应商报告的模型名不一致：{res.reported_model}" if res.model_mismatch else ""
        out(f"  {res.model_version}（{task.value}）：ok，{elapsed} ms，token {tokens}，回复 {len(res.output)} 字{note}")
        failed += int(res.model_mismatch)
    out(f"model-smoke: {'通过' if not failed else f'失败（{failed}/{len(targets)} 个目标）'}；回复原文不打印、不落盘")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="model_smoke.py", description="真实模型冒烟（人执行、限次；不进 make check）")
    ap.add_argument("--env", required=True)
    ap.add_argument("--max-calls", type=int, default=None)
    a = ap.parse_args(argv)
    return run(a.env, max_calls=a.max_calls)


if __name__ == "__main__":
    sys.exit(main())
