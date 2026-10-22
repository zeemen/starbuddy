"""模型路由骨架（冲刺 1-F，specs/model-router-sprint1/spec.md）的测试。全部本机、不联网、合成数据（规则 8）。"""
from __future__ import annotations

import copy
import json
import logging
import random

import httpx
import pytest
import yaml

from learning.model_router import (DEFAULT_PATH, ConfigError, DataClass, FakeProvider, LogSink, MemorySink, ModelRouter,
                                   OpenAICompatProvider, ProviderError, ProviderReply, Residency, RouteRejected, RouteRequest,
                                   RouteUnavailable, Task, Usage, egress_allowed, load, parse)

SECRET_PROMPT = "合成题干-不应出现在日志里-7f3a"
SECRET_OUTPUT = "合成输出-不应出现在日志里-9c1e"


def repo_doc() -> dict:
    with open(DEFAULT_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def vendor(org: str, residency: str = "domestic", protocol: str = "openai_compat", whitelist: str = "test_only", env: str = "X") -> dict:
    return {"org": org, "platform": org, "residency": residency, "protocol": protocol, "whitelist": whitelist,
            "base_url_env": f"MODEL_{env}_URL", "allowed_hosts": ["127.0.0.1", "api.example.invalid"]}


def doc_with(env: str, vendors: dict, routes: dict, budgets: dict | None = None) -> dict:
    """staging、pilot、prod 要求每个已配路由的任务都有成本护栏（LT-02）：不显式给 budgets 时按路由补一份合成额度"""
    if budgets is None and env in ("staging", "pilot", "prod"):
        budgets = {t: {"daily_calls": 1000, "daily_tokens": 10**7} for t in routes if t in Task.__members__}
    section = {"vendors": vendors, "routes": routes}
    if budgets is not None:
        section["budgets"] = budgets
    return {"version": 1, "defaults": {"max_retries": 2, "breaker": {"failure_threshold": 5, "cooldown_seconds": 30}},
            "environments": {env: section}}


def target(v: str, model: str = "m", version: str = "2026-09-01", pinned: bool = False) -> dict:
    t = {"vendor": v, "model": model, "version": version}
    if pinned:
        t["api_name"] = f"{model}-{version}"
    return t


def req(task: Task = Task.T1, dc: DataClass = DataClass.L2, **kw) -> RouteRequest:
    base = dict(task=task, data_class=dc, subject="aya", prompt_template="tutor-aya-hint", prompt_version="1.0",
                messages=({"role": "user", "content": SECRET_PROMPT},), usage_id="usage-0001-synthetic",
                idempotency_key="idem-0001-synthetic")
    base.update(kw)
    return RouteRequest(**base)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    import asyncio

    import learning.model_router.router as router_mod
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(router_mod, "_default_sleep", fake_sleep)
    return slept


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def fakes(cfg, behaviors: dict | None = None) -> dict[str, FakeProvider]:
    behaviors = behaviors or {}
    return {vid: FakeProvider(v, behaviors.get(vid)) for vid, v in cfg.vendors.items()}


def ok_reply(text: str = SECRET_OUTPUT):
    async def b(t, m, k):
        return ProviderReply(output=text, usage=Usage(3, 5), reported_model=t.api_name)
    return b


def failing(kind: str):
    async def b(t, m, k):
        raise ProviderError(kind)
    return b


# ---------------- 配置与启动校验 ----------------

def test_repo_config_loads_every_environment():
    for env in ("dev", "test"):
        cfg = load(DEFAULT_PATH, env)
        assert Task.T1 in cfg.routes and cfg.vendors["fake-x"].residency is Residency.FOREIGN
    for env in ("pilot", "prod"):
        assert load(DEFAULT_PATH, env).routes == {}, "仓库里不提交 pilot、prod 的路由（必停项）"
    # staging（LT-02）：只有变量名与占位，占位未填就拒绝启动（正例见 tests/test_model_router_staging.py）
    with pytest.raises(ConfigError, match="占位"):
        load(DEFAULT_PATH, "staging")


BAD_CONFIGS = {
    "pilot 出现境外供应商": ("pilot", {"a": vendor("a", whitelist="approved"), "x": vendor("x", "foreign", whitelist="approved")},
                        {"T8": {"data_classes": ["L0"], "primary": target("x"), "backup": target("a")}}),
    "prod 用未批准的供应商": ("prod", {"a": vendor("a", whitelist="pending")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}),
    "prod 用测试专用的供应商": ("prod", {"a": vendor("a", whitelist="test_only")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}),
    "pilot 用假协议": ("pilot", {"a": vendor("a", protocol="fake", whitelist="approved")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}),
    "latest 别名": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {**target("a"), "model": "chat-latest"}}}),
    "api_name 用 latest": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {**target("a"), "api_name": "fake-chat-latest"}}}),
    "没有锁定版本": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {"vendor": "a", "model": "m", "version": "stable"}}}),
    "主备同一厂商同一平台": ("test", {"a": vendor("same"), "b": vendor("same")},
                    {"T1": {"data_classes": ["L0"], "primary": target("a"), "backup": target("b")}}),
    "可达 L2 的任务配了境外": ("test", {"a": vendor("a"), "x": vendor("x", "foreign")},
                       {"T1": {"data_classes": ["L0", "L2"], "primary": target("a"), "backup": target("x")}}),
    "未登记的供应商": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("ghost")}}),
    "检索配了备选": ("test", {"a": vendor("a"), "b": vendor("b")}, {"T7": {"data_classes": ["L0"], "primary": target("a"), "backup": target("b")}}),
    "打开双跑": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("a"), "dual_run": True}}),
    "地址直接写进配置": ("test", {"a": {**vendor("a"), "base_url_env": "https://api.example.invalid"}},
                  {"T1": {"data_classes": ["L0"], "primary": target("a")}}),
    "没有声明数据级别": ("test", {"a": vendor("a")}, {"T1": {"primary": target("a")}}),
    "未知任务类型": ("test", {"a": vendor("a")}, {"T10": {"data_classes": ["L0"], "primary": target("a")}}),
    "假供应商标为已批准": ("test", {"a": vendor("a", protocol="fake", whitelist="approved")}, {"T1": {"data_classes": ["L0"], "primary": target("a")}}),
}


@pytest.mark.parametrize("name", sorted(BAD_CONFIGS))
def test_bad_config_refuses_to_start(name):
    env, vendors, routes = BAD_CONFIGS[name]
    with pytest.raises(ConfigError):
        parse(doc_with(env, vendors, routes), env)


def test_valid_pilot_config_is_accepted():
    cfg = parse(doc_with("pilot", {"a": vendor("a", whitelist="approved"), "b": vendor("b", whitelist="approved")},
                         {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True), "backup": target("b", pinned=True)}}), "pilot")
    assert cfg.routes[Task.T1].backup is not None


@pytest.mark.parametrize("env", ["staging", "pilot", "prod"])
def test_strict_env_requires_budget_for_every_route(env):
    """安全审查建议 2（LT-02）：staging、pilot、prod 每个已配路由的任务都必须有成本护栏，否则拒绝启动"""
    vendors = {"a": vendor("a", whitelist="approved")}
    routes = {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)},
              "T2": {"data_classes": ["L2"], "primary": target("a", model="r", pinned=True)}}
    for budgets in ({}, {"T1": {"daily_calls": 1, "daily_tokens": 1}}):
        with pytest.raises(ConfigError, match="成本护栏"):
            parse(doc_with(env, vendors, routes, budgets), env)
    assert parse(doc_with(env, vendors, routes), env).budgets.keys() == {Task.T1, Task.T2}
    # dev、test 可以不配（不设上限）
    assert parse(doc_with("test", vendors, routes, {}), "test").budgets == {}


MORE_BAD = {
    "YAML 不带引号的版本号（1.10 读成 1.1）": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {**target("a"), "version": 1.1}}}),
    "供应商 ID 带大写（不合契约）": ("test", {"Fake_A": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("Fake_A")}}),
    "模型名带斜杠（不合契约）": ("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {**target("a"), "model": "qwen/plus"}}}),
    "pilot 没写 api_name": ("pilot", {"a": vendor("a", whitelist="approved")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}),
    "pilot 的 api_name 不带版本": ("pilot", {"a": vendor("a", whitelist="approved")},
                                {"T1": {"data_classes": ["L2"], "primary": {**target("a"), "api_name": "chat"}}}),
    "pilot 没登记 allowed_hosts": ("pilot", {"a": {**vendor("a", whitelist="approved"), "allowed_hosts": []}},
                                 {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)}}),
    "staging 可达 L2 却用未批准的供应商": ("staging", {"a": vendor("a", whitelist="pending")}, {"T1": {"data_classes": ["L2"], "primary": target("a")}}),
    "供应商有未知键": ("test", {"a": {**vendor("a"), "base_url": "https://x.invalid"}}, {}),
    # 供应商一级的校验对“登记了但没有路由引用”的供应商同样生效（不只靠 check_invariants 核对被引用的；LT-02 变异对照组修复后补）
    "pilot 登记了未使用的境外供应商": ("pilot", {"a": vendor("a", whitelist="approved"), "x": vendor("x", "foreign", whitelist="approved")},
                               {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)}}),
    "pilot 登记了未使用的未批准供应商": ("pilot", {"a": vendor("a", whitelist="approved"), "p": vendor("p", whitelist="pending")},
                                {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)}}),
    "staging 登记了未使用、没有 allowed_hosts 的供应商": ("staging", {"a": vendor("a", whitelist="approved"),
                                                       "b": {**vendor("b", whitelist="approved"), "allowed_hosts": []}},
                                               {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)}}),
    "未使用的供应商 ID 不合契约": ("test", {"Fake_A": vendor("a")}, {}),
}


@pytest.mark.parametrize("name", sorted(MORE_BAD))
def test_more_bad_configs_refuse_to_start(name):
    env, vendors, routes = MORE_BAD[name]
    with pytest.raises(ConfigError):
        parse(doc_with(env, vendors, routes), env)


def test_hand_built_config_cannot_bypass_invariants():
    import dataclasses
    good = parse(doc_with("pilot", {"a": vendor("a", whitelist="approved")},
                          {"T1": {"data_classes": ["L2"], "primary": target("a", pinned=True)}}), "pilot")
    foreign = dataclasses.replace(good.vendors["a"], residency=Residency.FOREIGN)
    bad = dataclasses.replace(good, vendors={"a": foreign})
    with pytest.raises(ConfigError):
        ModelRouter(bad, {"a": FakeProvider(foreign)}, sink=MemorySink())
    with pytest.raises(TypeError):
        good.vendors["b"] = foreign  # type: ignore[index]  配置只读


def test_load_for_deployment_reads_env_from_environment():
    from learning.model_router.config import load_for_deployment
    assert load_for_deployment(DEFAULT_PATH, {"LEARNING_ENV": "test"}).env == "test"
    for bad in ({}, {"LEARNING_ENV": "production"}):
        with pytest.raises(ConfigError):
            load_for_deployment(DEFAULT_PATH, bad)


def test_config_errors_do_not_echo_secrets():
    with pytest.raises(ConfigError) as e:
        parse(doc_with("test", {"a": {**vendor("a"), "base_url_env": "https://user:pw@example.invalid"}}, {}), "test")
    assert "pw@" not in str(e.value)


# ---------------- 运行时 ----------------

async def test_same_request_switches_vendor_by_config():
    """v2.0 验收：可按配置切换供应商"""
    doc = repo_doc()
    cfg_a = parse(doc, "test")
    doc2 = copy.deepcopy(doc)
    r = doc2["environments"]["test"]["routes"]["T1"]
    r["primary"], r["backup"] = r["backup"], r["primary"]
    cfg_b = parse(doc2, "test")
    ra = await ModelRouter(cfg_a, fakes(cfg_a), sink=MemorySink()).route(req())
    rb = await ModelRouter(cfg_b, fakes(cfg_b), sink=MemorySink()).route(req())
    assert ra.model_version == "model:fake-a/fake-chat@2026-09-01"
    assert rb.model_version == "model:fake-b/fake-chat@2026-09-01"
    assert ra.source_version == "prompt:tutor-aya-hint@1.0"


async def test_result_versions_match_plugin_api_contract():
    """契约审查应改 1、2：两个字段直接用契约 plugin-api v1.0 的正则校验"""
    import re
    from pathlib import Path
    spec = yaml.safe_load((Path(__file__).resolve().parents[3] / "packages/contracts/openapi/plugin-api.v1.0.yaml").read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    mv, sv = re.compile(schemas["ModelVersion"]["pattern"]), re.compile(schemas["SourceVersion"]["pattern"])
    cfg = load(DEFAULT_PATH, "test")
    for task in (Task.T1, Task.T2, Task.T4, Task.T9):
        res = await ModelRouter(cfg, fakes(cfg), sink=MemorySink()).route(req(task=task))
        assert mv.match(res.model_version) and sv.match(res.source_version), (res.model_version, res.source_version)


async def test_retries_reuse_idempotency_key_then_fall_back_to_backup():
    cfg = load(DEFAULT_PATH, "test")
    provs = fakes(cfg, {"fake-a": failing("server"), "fake-b": ok_reply()})
    sink = MemorySink()
    res = await ModelRouter(cfg, provs, sink=sink).route(req())
    assert res.model_version.startswith("model:fake-b/")
    assert [a.status for a in res.attempts] == ["server", "server", "server", "ok"]
    assert {k for _, k in provs["fake-a"].calls} == {"idem-0001-synthetic"}, "同一供应商重试用同一个幂等键"
    assert sink.records[-1].outcome == "ok" and sink.records[-1].model_version == res.model_version


async def test_client_error_is_not_retried_and_not_counted_by_breaker():
    cfg = load(DEFAULT_PATH, "test")
    provs = fakes(cfg, {"fake-a": failing("client"), "fake-b": ok_reply()})
    router = ModelRouter(cfg, provs, sink=MemorySink())
    res = await router.route(req())
    assert [a.status for a in res.attempts] == ["client", "ok"]
    for _ in range(6):
        await router.route(req())
    assert router.breaker.state("fake-a", "T1") == "closed", "供应商判为不合法的请求不计入熔断"


async def test_breaker_opens_skips_then_half_opens_and_recovers():
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()
    state = {"fail": True}

    async def flaky(t, m, k):
        if state["fail"]:
            raise ProviderError("timeout")
        return ProviderReply("ok-a", Usage(), t.api_name)

    provs = fakes(cfg, {"fake-a": flaky, "fake-b": ok_reply("ok-b")})
    router = ModelRouter(cfg, provs, sink=MemorySink(), clock=clock)
    await router.route(req())  # 3 次失败
    await router.route(req())  # 再 2 次达到阈值 5，熔断打开
    assert router.breaker.state("fake-a", "T1") == "open"
    before = len(provs["fake-a"].calls)
    res = await router.route(req())
    assert res.attempts[0].status == "skipped_breaker_open" and len(provs["fake-a"].calls) == before
    clock.t += 31
    assert router.breaker.state("fake-a", "T1") == "half_open"
    state["fail"] = False
    res = await router.route(req())
    assert res.output == "ok-a" and router.breaker.state("fake-a", "T1") == "closed"


async def test_half_open_lets_only_one_concurrent_probe():
    import asyncio
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()
    gate = asyncio.Event()
    state = {"fail": True}

    async def a(t, m, k):
        if state["fail"]:
            raise ProviderError("server")
        await gate.wait()
        return ProviderReply("ok-a", Usage(), t.api_name)

    provs = fakes(cfg, {"fake-a": a, "fake-b": ok_reply("ok-b")})
    router = ModelRouter(cfg, provs, sink=MemorySink(), clock=clock)
    for _ in range(2):
        await router.route(req())
    clock.t += 31
    state["fail"] = False
    n = len(provs["fake-a"].calls)
    first = asyncio.create_task(router.route(req()))
    await asyncio.sleep(0)
    second = await router.route(req())  # 试探进行中：第二个请求不能再进 fake-a
    gate.set()
    assert (await first).output == "ok-a" and second.output == "ok-b"
    assert len(provs["fake-a"].calls) == n + 1


async def test_half_open_probe_failure_reopens():
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()
    provs = fakes(cfg, {"fake-a": failing("server"), "fake-b": ok_reply()})
    router = ModelRouter(cfg, provs, sink=MemorySink(), clock=clock)
    for _ in range(2):
        await router.route(req())
    assert router.breaker.state("fake-a", "T1") == "open"
    clock.t += 31
    n = len(provs["fake-a"].calls)
    await router.route(req())
    assert len(provs["fake-a"].calls) == n + 1, "半开只放一个试探"
    assert router.breaker.state("fake-a", "T1") == "open"


async def test_no_allowed_backup_raises_and_never_falls_back():
    cfg = parse(doc_with("test", {"x": vendor("x", "foreign")}, {"T1": {"data_classes": ["L0", "L1"], "primary": target("x")}}), "test")
    provs = fakes(cfg, {"x": ok_reply()})
    sink = MemorySink()
    with pytest.raises(RouteUnavailable):
        await ModelRouter(cfg, provs, sink=sink).route(req(dc=DataClass.L1))
    assert provs["x"].calls == [] and sink.records[-1].outcome == "unavailable"
    # L0 可以到模拟境外供应商（dev、test 合成数据）
    assert (await ModelRouter(cfg, provs, sink=sink).route(req(dc=DataClass.L0))).model_version.startswith("model:x/")


async def test_all_candidates_down_is_unavailable():
    cfg = load(DEFAULT_PATH, "test")
    provs = fakes(cfg, {"fake-a": failing("auth"), "fake-b": failing("rate_limited")})
    with pytest.raises(RouteUnavailable) as e:
        await ModelRouter(cfg, provs, sink=MemorySink()).route(req())
    assert [a.status for a in e.value.attempts] == ["auth", "rate_limited", "rate_limited", "rate_limited"]


async def test_deadline_caps_total_time():
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()

    async def slow(t, m, k):
        clock.t += 1.0
        raise ProviderError("timeout")

    provs = fakes(cfg, {"fake-a": slow, "fake-b": slow})
    with pytest.raises(RouteUnavailable) as e:
        await ModelRouter(cfg, provs, sink=MemorySink(), clock=clock).route(req(deadline_ms=2500))
    assert len(e.value.attempts) == 3


@pytest.mark.parametrize("field,value", [("usage_id", ""), ("idempotency_key", ""), ("prompt_version", ""), ("prompt_version", "latest"),
                                         ("usage_id", "x" * 200), ("messages", ()), ("prompt_template", "tutor/aya-hint"),
                                         ("prompt_template", "Tutor")])
async def test_missing_metering_or_prompt_fields_are_rejected(field, value):
    cfg = load(DEFAULT_PATH, "test")
    provs = fakes(cfg)
    with pytest.raises(RouteRejected):
        await ModelRouter(cfg, provs, sink=MemorySink()).route(req(**{field: value}))
    assert all(not p.calls for p in provs.values())


async def test_data_class_not_declared_for_task_is_rejected():
    cfg = load(DEFAULT_PATH, "test")
    with pytest.raises(RouteRejected):
        await ModelRouter(cfg, fakes(cfg), sink=MemorySink()).route(req(task=Task.T8, dc=DataClass.L2))


@pytest.mark.parametrize("task", [Task.T5, Task.T6, Task.T7])
async def test_non_chat_tasks_are_rejected_in_skeleton(task):
    cfg = load(DEFAULT_PATH, "test")
    with pytest.raises(RouteRejected):
        await ModelRouter(cfg, fakes(cfg), sink=MemorySink()).route(req(task=task, dc=DataClass.L0))


async def test_call_record_and_log_hold_metadata_only(caplog):
    cfg = load(DEFAULT_PATH, "test")
    provs = fakes(cfg, {"fake-a": failing("server"), "fake-b": ok_reply()})
    mem = MemorySink()
    with caplog.at_level(logging.INFO, logger="starbuddy.model_router"):
        await ModelRouter(cfg, provs, sink=LogSink()).route(req())
        await ModelRouter(cfg, provs, sink=mem).route(req())
    text = caplog.text + json.dumps([r.__dict__ for r in mem.records], default=str)
    for secret in (SECRET_PROMPT, SECRET_OUTPUT, "usage-0001-synthetic", "idem-0001-synthetic"):
        assert secret not in text, secret
    assert '"event": "model_call"' in caplog.text and '"outcome": "ok"' in caplog.text
    assert SECRET_PROMPT not in repr(req()) and SECRET_OUTPUT not in repr(await ModelRouter(cfg, provs, sink=mem).route(req()))


async def test_reported_model_mismatch_is_flagged():
    cfg = load(DEFAULT_PATH, "test")

    async def other(t, m, k):
        return ProviderReply("x", Usage(), "some-other-model\n<script>")

    mem = MemorySink()
    res = await ModelRouter(cfg, fakes(cfg, {"fake-a": other}), sink=mem).route(req())
    assert res.model_version == "model:fake-a/fake-chat@2026-09-01"
    assert res.model_mismatch and res.reported_model == "some-other-modelscript", "调用方能收到不一致信号；报告的名字只留安全字符"
    assert mem.records[-1].model_mismatch and mem.records[-1].reported_model == "some-other-modelscript"
    ok = await ModelRouter(cfg, fakes(cfg), sink=mem).route(req())
    assert not ok.model_mismatch


async def test_unexpected_adapter_exception_releases_probe_and_is_recorded():
    """安全审查应改 3：半开试探抛出意外异常时，试探名额要放回（重新打开），调用也要有记录"""
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()
    mode = {"k": "server"}

    async def a(t, m, k):
        if mode["k"] == "server":
            raise ProviderError("server")
        if mode["k"] == "boom":
            raise KeyError("unexpected")
        return ProviderReply("ok-a", Usage(), t.api_name)

    mem = MemorySink()
    router = ModelRouter(cfg, fakes(cfg, {"fake-a": a, "fake-b": ok_reply("ok-b")}), sink=mem, clock=clock)
    for _ in range(2):
        await router.route(req())
    clock.t += 31
    mode["k"] = "boom"
    res = await router.route(req())
    assert res.output == "ok-b" and "bad_response" in [x.status for x in res.attempts]
    assert router.breaker.state("fake-a", "T1") == "open", "试探失败重新打开，而不是卡在半开"
    clock.t += 31
    mode["k"] = "ok"
    assert (await router.route(req())).output == "ok-a", "冷却后还能再试探并恢复"


async def test_record_written_even_when_cancelled():
    import asyncio
    cfg = load(DEFAULT_PATH, "test")
    gate = asyncio.Event()

    async def hang(t, m, k):
        await gate.wait()
        return ProviderReply("x", Usage(), t.api_name)

    mem = MemorySink()
    task = asyncio.create_task(ModelRouter(cfg, fakes(cfg, {"fake-a": hang}), sink=mem).route(req()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mem.records[-1].outcome == "error"


async def test_concurrent_client_error_does_not_free_the_probe():
    """安全审查应改 4：半开时，非试探请求的 client 错误不能释放试探名额"""
    import asyncio
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()
    gate = asyncio.Event()
    mode = {"k": "server"}

    async def a(t, m, k):
        if mode["k"] == "server":
            raise ProviderError("server")
        await gate.wait()
        return ProviderReply("ok-a", Usage(), t.api_name)

    provs = fakes(cfg, {"fake-a": a, "fake-b": ok_reply("ok-b")})
    router = ModelRouter(cfg, provs, sink=MemorySink(), clock=clock)
    for _ in range(2):
        await router.route(req())
    clock.t += 31
    mode["k"] = "hold"
    n = len(provs["fake-a"].calls)
    probe = asyncio.create_task(router.route(req()))
    await asyncio.sleep(0)
    from learning.model_router.breaker import Ticket
    router.breaker.release(Ticket("fake-a", "T1", True))  # 伪造的试探令牌不能释放名额
    router.breaker.release(Ticket("fake-a", "T1", False))
    second = await router.route(req())
    gate.set()
    assert (await probe).output == "ok-a" and second.output == "ok-b"
    assert len(provs["fake-a"].calls) == n + 1


async def test_default_deadline_applies(no_real_sleep):
    cfg = load(DEFAULT_PATH, "test")
    clock = Clock()

    async def slow(t, m, k):
        clock.t += 100
        raise ProviderError("timeout")

    with pytest.raises(RouteUnavailable) as e:
        await ModelRouter(cfg, fakes(cfg, {"fake-a": slow, "fake-b": slow}), sink=MemorySink(), clock=clock).route(req())
    assert len(e.value.attempts) == 2, "不传 deadline_ms 时总时限 180 秒"


async def test_backoff_and_retry_after(no_real_sleep):
    cfg = load(DEFAULT_PATH, "test")

    async def limited(t, m, k):
        raise ProviderError("rate_limited", retry_after=3.0)

    async def down(t, m, k):
        raise ProviderError("server")

    with pytest.raises(RouteUnavailable):
        await ModelRouter(cfg, fakes(cfg, {"fake-a": limited, "fake-b": down}), sink=MemorySink()).route(req())
    assert no_real_sleep == [3.0, 3.0, 0.2, 0.4], "429 按 Retry-After 等；其他可重试错误按 0.2 × 2^n 退避"


@pytest.mark.parametrize("subject", ["math", "AYA", "aya; drop"])
async def test_subject_must_be_platform_code(subject):
    cfg = load(DEFAULT_PATH, "test")
    with pytest.raises(RouteRejected):
        await ModelRouter(cfg, fakes(cfg), sink=MemorySink()).route(req(subject=subject))


# ---------------- 性质测试：任何配置、任何故障序列都不出境 ----------------

def random_doc(rng: random.Random, env: str) -> dict:
    vendors = {}
    strict = env in ("staging", "pilot", "prod")  # LT-02：staging 与 pilot 同口径
    bad = 0.04 if strict else 0.3  # 生产与试点的随机配置大多要合法，才测得到运行时路径；非法的也要有，测启动校验
    for i in range(rng.randint(1, 4)):
        vendors[f"v{i}"] = {"org": f"o{i}" if rng.random() > bad else "o0", "platform": f"p{rng.randint(0, 2)}",
                            "residency": "foreign" if rng.random() < bad else "domestic",
                            "protocol": "fake" if rng.random() < bad / 2 else "openai_compat",
                            "whitelist": ("test_only" if env in ("dev", "test") else "approved") if rng.random() > bad / 2 else "pending",
                            "base_url_env": f"MODEL_V{i}_URL", "allowed_hosts": ["api.example.invalid"]}
    routes = {}
    for t in rng.sample(["T1", "T2", "T3", "T4", "T8", "T9"], rng.randint(1, 4)):
        classes = sorted(rng.sample(["L0", "L1", "L2", "L3"], rng.randint(1, 4))) if rng.random() < 0.6 else ["L0"]
        r = {"data_classes": classes, "primary": target(rng.choice(list(vendors)), pinned=True)}
        if rng.random() < 0.7:
            r["backup"] = target(rng.choice(list(vendors)), model="m2", pinned=True)
        routes[t] = r
    return doc_with(env, vendors, routes)  # staging、pilot、prod 自动补成本护栏（LT-02），额度够大，不影响性质测试


@pytest.mark.parametrize("env", ["dev", "test", "staging", "pilot", "prod"])
async def test_property_never_egress_beyond_allowed(env):
    rng = random.Random(f"model-router-{env}")
    parsed = 0
    for _ in range(400):
        try:
            cfg = parse(random_doc(rng, env), env)
        except ConfigError:
            continue
        parsed += 1
        reached: list[tuple[str, DataClass]] = []

        def make(vid):
            async def b(t, m, k):
                reached.append((vid, current["dc"]))
                kind = rng.choice(["ok", "ok", "timeout", "server", "rate_limited", "auth", "client", "bad_response"])
                if kind != "ok":
                    raise ProviderError(kind)
                return ProviderReply("x", Usage(), t.api_name)
            return b

        current = {"dc": DataClass.L0}
        clock = Clock()
        router = ModelRouter(cfg, {vid: FakeProvider(v, make(vid)) for vid, v in cfg.vendors.items()}, sink=MemorySink(), clock=clock)
        for _ in range(30):
            task = rng.choice(list(cfg.routes))
            current["dc"] = rng.choice(list(DataClass))
            clock.t += rng.choice([0, 0, 5, 40])
            try:
                await router.route(req(task=task, dc=current["dc"]))
            except (RouteUnavailable, RouteRejected):
                pass
        for vid, dc in reached:
            if cfg.vendors[vid].residency is Residency.FOREIGN:
                assert env in ("dev", "test"), f"{env} 到达了境外供应商"
                assert dc is DataClass.L0, f"{env} 把 {dc.value} 发到了境外供应商"
    assert parsed >= 40, f"随机配置几乎都被拒（{parsed}），性质测试没有测到东西"


def test_egress_allowed_table():
    x = parse(doc_with("test", {"x": vendor("x", "foreign")}, {}), "test").vendors["x"]
    a = parse(doc_with("test", {"a": vendor("a")}, {}), "test").vendors["a"]
    for env in ("dev", "test", "staging", "pilot", "prod"):
        for dc in DataClass:
            assert egress_allowed(a, dc, env)
            assert egress_allowed(x, dc, env) == (env in ("dev", "test") and dc is DataClass.L0)


# ---------------- OpenAI 兼容适配器（本机模拟传输，不联网） ----------------

def compat(handler, env=None) -> OpenAICompatProvider:
    v = parse(doc_with("test", {"a": {**vendor("a", env="A"), "api_key_env": "MODEL_A_KEY"}}, {}), "test").vendors["a"]
    environ = env or {"MODEL_A_URL": "http://127.0.0.1:9", "MODEL_A_KEY": "synthetic-key"}
    p = OpenAICompatProvider(v, environ)
    p._client._transport = httpx.MockTransport(handler)  # noqa: SLF001 测试注入
    return p


async def test_openai_compat_sends_snapshot_name_key_and_idempotency_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["idem"] = request.headers.get("idempotency-key")
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 2}})

    cfg_t = parse(doc_with("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": {**target("a"), "params": {"temperature": 0}}}}),
                  "test").routes[Task.T1].primary
    reply = await compat(handler).complete(cfg_t, [{"role": "user", "content": "x"}], idempotency_key="idem-1234", timeout=5)
    assert reply.output == "hi" and reply.usage == Usage(7, 2)
    assert seen == {"auth": "Bearer synthetic-key", "idem": "idem-1234", "path": "/chat/completions",
                    "body": {"model": "m", "messages": [{"role": "user", "content": "x"}], "temperature": 0}}


@pytest.mark.parametrize("status,body,kind", [(429, {}, "rate_limited"), (401, {}, "auth"), (503, {}, "server"), (400, {"error": "echo"}, "client"),
                                              (200, {"choices": []}, "bad_response")])
async def test_openai_compat_maps_errors_without_body(status, body, kind):
    t = parse(doc_with("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("a")}}), "test").routes[Task.T1].primary
    with pytest.raises(ProviderError) as e:
        await compat(lambda r: httpx.Response(status, json=body)).complete(t, [{"role": "user", "content": "x"}], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == kind and "echo" not in str(e.value)


async def test_openai_compat_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    t = parse(doc_with("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("a")}}), "test").routes[Task.T1].primary
    with pytest.raises(ProviderError) as e:
        await compat(handler).complete(t, [], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == "timeout"


def test_openai_compat_checks_scheme_and_host():
    v = parse(doc_with("test", {"a": vendor("a", env="A")}, {}), "test").vendors["a"]
    with pytest.raises(ConfigError):
        OpenAICompatProvider(v, {"MODEL_A_URL": "http://127.0.0.1:9"}, env="pilot")  # pilot 只接受 https
    with pytest.raises(ConfigError) as e:
        OpenAICompatProvider(v, {"MODEL_A_URL": "https://evil.example.org/v1"}, env="test")  # 主机名不在登记里
    assert "evil" not in str(e.value)
    with pytest.raises(ConfigError):
        OpenAICompatProvider(v, {"MODEL_A_URL": "https://u:p@api.example.invalid"}, env="test")
    OpenAICompatProvider(v, {"MODEL_A_URL": "https://api.example.invalid/v1"}, env="pilot")


async def test_openai_compat_limits_response_size_and_reads_retry_after():
    t = parse(doc_with("test", {"a": vendor("a")}, {"T1": {"data_classes": ["L0"], "primary": target("a")}}), "test").routes[Task.T1].primary
    # 合法的 JSON，只是超过 1 MiB：没有大小上限时会被当成正常回复（LT-02 变异对照组修复后补：原来的非 JSON 正文本身就解析失败）
    big_json = {"model": "m", "choices": [{"message": {"content": "x" * (2 << 20)}}]}
    big = compat(lambda r: httpx.Response(200, json=big_json))
    with pytest.raises(ProviderError) as e:
        await big.complete(t, [], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == "bad_response"
    small = compat(lambda r: httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "x" * 1000}}]}))
    assert (await small.complete(t, [], idempotency_key="k" * 8, timeout=5)).output == "x" * 1000
    limited = compat(lambda r: httpx.Response(429, headers={"retry-after": "999"}))
    with pytest.raises(ProviderError) as e:
        await limited.complete(t, [], idempotency_key="k" * 8, timeout=5)
    assert e.value.kind == "rate_limited" and e.value.retry_after == 30.0, "Retry-After 封顶 30 秒"


def test_openai_compat_requires_env_values():
    v = parse(doc_with("test", {"a": {**vendor("a", env="A"), "api_key_env": "MODEL_A_KEY"}}, {}), "test").vendors["a"]
    with pytest.raises(ConfigError):
        OpenAICompatProvider(v, {})
    with pytest.raises(ConfigError) as e:
        OpenAICompatProvider(v, {"MODEL_A_URL": "http://127.0.0.1:9"})
    assert "MODEL_A_KEY" in str(e.value)


def test_from_config_builds_only_used_vendors():
    cfg = load(DEFAULT_PATH, "test")
    env = {"MODEL_FAKE_A_URL": "http://127.0.0.1:9", "MODEL_FAKE_B_URL": "http://127.0.0.1:9", "MODEL_FAKE_X_URL": "http://127.0.0.1:9"}
    router = ModelRouter.from_config(cfg, env, sink=MemorySink())
    assert set(router._providers) == {"fake-a", "fake-b", "fake-x"}  # noqa: SLF001
    with pytest.raises(ConfigError):
        ModelRouter.from_config(cfg, {}, sink=MemorySink())
