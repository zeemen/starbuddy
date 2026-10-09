"""单模型失效降级（冲刺 5，specs/load-cost-baseline-sprint5 设计要点 2）：故障注入 + 断言。全部本机、合成数据（规则 8、9）。

一、路由层（不需要数据库）：仓库路由表 test 节的每条路由 × 五种故障（整家不可用、超时、5xx 比例、慢响应、限流），
    三种情形——主失效（熔断打开、切备选、数据级别过滤不放宽）、主备全失效（没有输出、不回落到不允许的供应商）、
    恢复（冷却后半开试探回到主模型、熔断关闭）。另断言运行时出境过滤：L1 请求在主模型失效时不去境外备选。
二、端到端（一次性 Postgres + 学习服务 + 三科适配器真实进程 + 内部路由接口 + 计量驱动桩，假供应商注入故障）：
    批改一律经计量驱动桩直接运行 BFF 的 meteredCall（真实计量语义）。每科每个用模型的方法（清单
    tools/loadtest/loadtest/degradation.py；静态扫描适配器源码核对清单完整）：
    主失效 → 照常出结果、记备选快照、不到境外、规则 3 五字段齐全；
    主备全失效 → 不给分、不写证据；BFF 归为结果未知、同键退避重试仍未知 → 不结算不释放 → 过期回收，settled 不变、reserved = 0；
      过期后同键重试只查不执行（replay 查无、键封存）；
    恢复 → 冷却后回到主模型；超时（结果未知）→ 同键重试拿到同一结果，只写一次、只扣一次；
    备选“乱答”（输出不合格）→ 待复核、不自动给分、证据里没有模型派生的分数与片段；不用模型的方法不受影响。
    其余故障类型（整家不可用以外的 5xx 比例、限流、超时挂起）只在路由层覆盖。
    没有 node、没装三科适配器或 BFF 依赖时跳过（LEARNING_TEST_STRICT=1 时失败）。
故障注入复用 tools/loadtest/loadtest/faults.py，经 FakeProvider 的行为注入（不改 model_router/providers.py）。
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
import uvicorn
import yaml

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "tools/loadtest", ROOT / "tools/e2e-cohort"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from learning.model_router import (DEFAULT_PATH, DataClass, FakeProvider, MemorySink, ModelRouter, ProviderReply,  # noqa: E402
                                   RouteRequest, RouteUnavailable, Task, Usage, parse)
from learning.model_router.breaker import CircuitBreaker  # noqa: E402
from loadtest.degradation import MODEL_POLICIES, POLICIES, adapter_model_calls  # noqa: E402
from loadtest.faults import FaultController, FaultSpec  # noqa: E402
from loadtest.faults import faulty_behavior  # noqa: E402

VENDORS = ("fake-a", "fake-b", "fake-x")
FOREIGN = {"fake-x"}
FAULTS = {
    "unavailable": FaultSpec(mode="unavailable"),
    "timeout": FaultSpec(mode="timeout"),
    "error_ratio": FaultSpec(mode="error_ratio", error_ratio=1.0),
    "slow": FaultSpec(mode="slow", extra_latency_ms=500),
    "rate_limited": FaultSpec(mode="rate_limited", retry_after_s=0.0),
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def no_sleep(_s: float) -> None:
    await asyncio.sleep(0)


def fast_doc() -> dict:
    """仓库路由表，只把超时调短（慢响应与超时要在测试里几十毫秒内见分晓）；供应商、路由与数据级别不动"""
    with open(DEFAULT_PATH, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    doc = copy.deepcopy(doc)
    doc["defaults"]["timeout_seconds"] = {"default": 0.05}
    return doc


def router_with(controller: FaultController, clock: Clock, doc: dict | None = None, env: str = "test"):
    cfg = parse(doc or fast_doc(), env)

    async def ok(target, messages, key):
        return ProviderReply(output='{"points": []}', usage=Usage(10, 5), reported_model=target.api_name)

    providers = {v: FakeProvider(cfg.vendors[v], faulty_behavior(controller, v, ok)) for v in cfg.vendors}
    sink = MemorySink()
    return ModelRouter(cfg, providers, sink=sink, clock=clock, sleep=no_sleep), providers, sink, cfg


def rreq(task: Task, dc: DataClass) -> RouteRequest:
    return RouteRequest(task=task, data_class=dc, subject="zhixing", prompt_template="degradation-probe", prompt_version="1.0",
                        messages=({"role": "user", "content": "合成探针"},), usage_id=f"usage-{uuid.uuid4()}",
                        idempotency_key=f"idem-{uuid.uuid4()}")


def routes_under_test():
    cfg = parse(fast_doc(), "test")
    for task, route in cfg.routes.items():
        if route.backup is None:
            continue
        # 每条路由取它允许的最高数据级别（出境过滤最严的一档）
        yield task, max(route.data_classes, key=lambda c: c.rank)


ROUTES = list(routes_under_test())


# ============================ 一、路由层 ============================
@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("task,dc", ROUTES, ids=[f"{t.value}-{d.value}" for t, d in ROUTES])
async def test_router_primary_down_opens_breaker_and_uses_allowed_backup(task, dc, fault):
    ctl, clock = FaultController(), Clock()
    router, providers, sink, cfg = router_with(ctl, clock)
    route = cfg.routes[task]
    ctl.set(route.primary.vendor, FAULTS[fault])
    for _ in range(3):  # 每个请求主模型失败 1 + max_retries 次；阈值 5 → 第二个请求后打开
        res = await router.route(rreq(task, dc))
        assert res.model_version == route.backup.model_version
    assert router.breaker.state(route.primary.vendor, task.value) == "open"
    assert sink.records[-1].attempts[0].status == "skipped_breaker_open", "熔断打开后不再打主模型"
    touched = {a.vendor for r in sink.records for a in r.attempts}
    assert touched <= {route.primary.vendor, route.backup.vendor}
    if dc.rank >= 1:
        assert not touched & FOREIGN and not providers["fake-x"].calls, "L1 及以上不到境外供应商"


@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("task,dc", ROUTES, ids=[f"{t.value}-{d.value}" for t, d in ROUTES])
async def test_router_all_down_is_unavailable_then_recovers_via_half_open(task, dc, fault):
    ctl, clock = FaultController(), Clock()
    router, providers, sink, cfg = router_with(ctl, clock)
    route = cfg.routes[task]
    for v in (route.primary.vendor, route.backup.vendor):
        ctl.set(v, FAULTS[fault])
    for _ in range(3):
        with pytest.raises(RouteUnavailable):
            await router.route(rreq(task, dc))
    assert all(r.outcome == "unavailable" and r.model_version is None and r.input_tokens == 0 for r in sink.records)
    assert {router.breaker.state(v, task.value) for v in (route.primary.vendor, route.backup.vendor)} == {"open"}
    if dc.rank >= 1:
        assert not providers["fake-x"].calls
    # 恢复：故障解除，冷却后半开试探回到主模型，熔断关闭
    ctl.clear()
    with pytest.raises(RouteUnavailable):
        await router.route(rreq(task, dc))  # 冷却未到：仍然全部跳过
    clock.t += cfg.cooldown_seconds + 1
    res = await router.route(rreq(task, dc))
    assert res.model_version == route.primary.model_version
    assert router.breaker.state(route.primary.vendor, task.value) == "closed"


async def test_router_runtime_egress_filter_not_relaxed_under_failure():
    """L0、L1 可达的路由把境外供应商配成备选（启动校验允许：L2 以上才强制境内）：主模型失效时，L1 请求也不去境外备选"""
    doc = fast_doc()
    doc["environments"]["test"]["routes"]["T1"] = {
        "data_classes": ["L0", "L1"],
        "primary": {"vendor": "fake-a", "model": "fake-chat", "version": "2026-09-01"},
        "backup": {"vendor": "fake-x", "model": "fake-chat", "version": "2026-09-01"},
    }
    ctl, clock = FaultController(), Clock()
    router, providers, sink, _ = router_with(ctl, clock, doc)
    ctl.set("fake-a", FaultSpec(mode="unavailable"))
    for _ in range(3):
        with pytest.raises(RouteUnavailable):
            await router.route(rreq(Task.T1, DataClass.L1))
    assert not providers["fake-x"].calls, "主模型失效也不放宽出境过滤"
    # 合成数据（L0）在 test 环境本来就允许境外：同一路由可以切过去（对照）
    res = await router.route(rreq(Task.T1, DataClass.L0))
    assert res.model_version == "model:fake-x/fake-chat@2026-09-01"


def test_policy_list_covers_every_adapter_model_call():
    """降级清单完整：适配器源码里每个经路由接口的模型调用都在清单里，清单里用模型的方法在源码里都有"""
    found = adapter_model_calls()
    listed: dict[str, set[str]] = {}
    for p in MODEL_POLICIES:
        listed.setdefault(p.subject, set()).add(p.task)
    assert {s: t for s, t in found.items() if t} == listed
    assert not found["aya"], "数学适配器不调模型（验证器判分）"
    assert {(p.subject, p.method) for p in POLICIES if p.status == "not_applicable"} >= {("aya", "grade"), ("aya", "diagnose")}


# ============================ 二、端到端 ============================
# 批改一律经计量驱动桩（tools/loadtest/node/meter-harness.mts）直接运行 BFF 的 meteredCall：计量语义与 BFF 相同
# （结果未知按 1/2/4/8 s 的比例缩短版退避同键重试，仍未知不结算不释放；过期后同键重试只查不执行）。
RETRY_SCALE = 0.02        # 退避 20/40/80/160 ms：口径同 BFF，只按比例缩短
HARNESS_TIMEOUT_MS = 3000  # BFF 为 15 000；缩短后用慢响应构造“结果未知”
SLOW_MS = 4500             # 大于 HARNESS_TIMEOUT_MS：第一次请求超时（结果未知），学习服务其实会写成功


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        pass


def _missing() -> str | None:
    from e2e_cohort.stack import missing_prerequisites
    from loadtest.harness import available

    return missing_prerequisites() or available()


def _port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, lifespan: str):
    port = _port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan=lifespan, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline or not t.is_alive():
            pytest.fail("本机服务没有启动")
        time.sleep(0.05)
    return server, t, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def e2e_pg():
    why = _missing()
    if why:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            pytest.fail(why)
        pytest.skip(why)
    from pgharness import EphemeralPG

    from e2e_cohort.stack import register_content
    from loadtest.seed import setup_sql

    try:
        pg = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    pg.start()
    try:
        pg.psql(setup_sql())
        register_content(pg)
        yield pg
    finally:
        pg.stop()


class E2E:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)

    def meter_now_ms(self) -> int:
        from loadtest.seed import METER_NOW_MS_SQL

        return int(self.pg.psql(METER_NOW_MS_SQL).strip())


@pytest.fixture(scope="module")
async def e2e(e2e_pg):
    from e2e_cohort.replay import load_zhixing_items
    from e2e_cohort.scripted import ScriptedModel
    from e2e_cohort.stack import ADAPTER_TOKENS, LEARNING_CREDENTIAL, ROUTE_TOKENS, adapters
    from learning.app import create_app
    from learning.config import AdapterTarget, Settings
    from learning.model_router import load
    from loadtest.answers import for_student
    from loadtest.harness import meter_harness
    from loadtest.seed import METER_NOW_MS_SQL, family, seed_families
    from pgharness import LOGIN_ROLE

    # learning-internal v1 未签字：只在这个一次性栈里开 LEARNING_INTERNAL_V1=1（插件契约 1.0.0 已签字，不再需要候选版开关）
    prev = os.environ.get("LEARNING_INTERNAL_V1")
    os.environ["LEARNING_INTERNAL_V1"] = "1"
    items = load_zhixing_items()
    points = {k: v["points"] for k, v in items.items()}
    model = ScriptedModel(items)
    for slot in range(40):
        a = for_student(20260930, slot, points)
        model.register("zhixing", a.zhixing_item, a.zhixing_text, a.pattern)
        model.register("wenli", a.wenli_item, a.wenli_text, a.pattern)
    ctl, clock = FaultController(), Clock()
    garbage = {"on": False}

    async def scripted(target, messages, key):
        out = "这不是约定的判定结构" if garbage["on"] and target.vendor == "fake-b" else model.reply(messages[-1]["content"])
        return ProviderReply(output=out, usage=Usage(100, 50), reported_model=target.api_name)

    cfg = load(DEFAULT_PATH, "test")
    providers = {v: FakeProvider(cfg.vendors[v], faulty_behavior(ctl, v, scripted)) for v in cfg.vendors}
    sink = MemorySink()
    router = ModelRouter(cfg, providers, sink=sink, clock=clock, sleep=no_sleep)
    route_app = create_app(Settings(env="test", database_url="unused", service_credential=LEARNING_CREDENTIAL,
                                    route_clients=dict(ROUTE_TOKENS)), model_router=router)
    servers = []
    admin = await asyncpg.create_pool(**e2e_pg.dsn("postgres"), min_size=1, max_size=3)
    bff = await asyncpg.create_pool(**e2e_pg.dsn("bff_app"), min_size=1, max_size=4)
    scheduler = await asyncpg.create_pool(**e2e_pg.dsn("loadtest_scheduler"), min_size=1, max_size=1)
    fams = [family(20260930, "student", i) for i in range(40)]
    await seed_families(admin, bff, fams)
    try:
        route_server, route_thread, route_url = _serve(route_app, "off")
        servers.append((route_server, route_thread))
        with adapters(route_url) as procs:
            # 学习服务在本机回环上单独起（自己的连接池，lifespan 在服务线程里），计量驱动桩经 HTTP 调它
            settings = Settings(env="test", database_url=f"postgresql://{LOGIN_ROLE}@/{e2e_pg.db}?host={e2e_pg.work}",
                                service_credential=LEARNING_CREDENTIAL,
                                adapters={s: AdapterTarget(p.url, ADAPTER_TOKENS[s]) for s, p in procs.items()})
            learning_server, learning_thread, learning_url = _serve(create_app(settings), "on")
            servers.append((learning_server, learning_thread))
            with meter_harness(f"postgresql://bff_app@/{e2e_pg.db}?host={e2e_pg.work}", learning_url, LEARNING_CREDENTIAL,
                               lambda: int(e2e_pg.psql(METER_NOW_MS_SQL).strip()), retry_scale=RETRY_SCALE,
                               timeout_ms=HARNESS_TIMEOUT_MS) as harness_url:
                async with httpx.AsyncClient(base_url=learning_url, timeout=60, trust_env=False) as api, \
                        httpx.AsyncClient(timeout=120, trust_env=False) as hc:
                    api.headers["authorization"] = f"Bearer {LEARNING_CREDENTIAL}"
                    yield E2E(pg=e2e_pg, ctl=ctl, clock=clock, router=router, providers=providers, sink=sink, api=api, hc=hc,
                              harness_url=harness_url, admin=admin, bff=bff, scheduler=scheduler, families=fams, points=points,
                              garbage=garbage)
    finally:
        for server, t in servers:
            server.should_exit = True
            t.join(timeout=10)
        await admin.close()
        await bff.close()
        await scheduler.close()
        if prev is None:
            os.environ.pop("LEARNING_INTERNAL_V1", None)
        else:
            os.environ["LEARNING_INTERNAL_V1"] = prev


_slots = iter(range(40))


@pytest.fixture
def fresh(e2e):
    """每个用例：清故障、换新熔断器、路由时钟前移、取一名新的合成学生"""
    e2e.ctl.clear()
    e2e.garbage["on"] = False
    e2e.router.breaker = CircuitBreaker(e2e.router._cfg.failure_threshold, e2e.router._cfg.cooldown_seconds, e2e.clock)
    e2e.clock.t += 10_000
    for p in e2e.providers.values():
        p.calls.clear()
    e2e.sink.records.clear()
    return e2e


def next_family(e2e):
    return e2e.families[next(_slots)]


def request_for(e2e, fam, subject: str) -> tuple[dict, str]:
    from loadtest.answers import for_student
    from loadtest.envelopes import grade_request

    a = for_student(20260930, fam.slot, e2e.points)
    item, text = {"aya": (a.aya_item, a.aya_text), "zhixing": (a.zhixing_item, a.zhixing_text), "wenli": (a.wenli_item, a.wenli_text)}[subject]
    return grade_request(subject, fam.student, item, text, str(uuid.uuid4()), "2026-10-15T10:00:00+08:00"), f"{subject}:{item}"


async def metered_grade(e2e, fam, subject: str, key: str | None = None, body: dict | None = None) -> tuple[dict, dict, str]:
    """经 BFF 的 meteredCall 批改一道题。返回（meteredCall 结果与学习服务调用次数、请求体、幂等键）"""
    from loadtest.harness import metered_grade as call

    if body is None:
        body, item_ref = request_for(e2e, fam, subject)
    else:
        item_ref = body["attempt"]["item_ref"]
    key = key or str(uuid.uuid4())
    out = await call(e2e.hc, e2e.harness_url, account=fam.student_account, student=fam.student, key=key, body=body, item_ref=item_ref)
    return out, body, key


async def expire_reservations(e2e) -> int:
    """快进计量时钟过预留有效期（批改 10 分钟），以定时任务角色跑过期，再把驱动桩的时钟对齐"""
    from loadtest import meter
    from loadtest.harness import clock_sync
    from loadtest.seed import skew_sql

    e2e.pg.psql(skew_sql("15 minutes"))
    n = await meter.expire_all(e2e.scheduler)
    await clock_sync(e2e.hc, e2e.harness_url, e2e.meter_now_ms())
    return n


async def rows(e2e, student: str) -> dict:
    async with e2e.admin.acquire() as c:
        out = {}
        for t in ("evidence_event", "grade_result", "grade_dimension", "attempt"):
            if t == "grade_dimension":
                out[t] = await c.fetchval("""SELECT count(*) FROM platform.grade_dimension d JOIN platform.grade_result g
                                             ON g.id = d.grade_result_id WHERE g.student_id = $1::uuid""", student)
            else:
                out[t] = await c.fetchval(f"SELECT count(*) FROM platform.{t} WHERE student_id = $1::uuid", student)
        return out


async def bucket(e2e, student: str) -> dict:
    r = await e2e.admin.fetchrow("""SELECT e.settled::int, e.reserved::int FROM platform.entitlement e
                                     WHERE e.student_id = $1::uuid AND e.resource_type = 'mistake_analysis'""", student)
    return dict(r)


async def assert_provenance(e2e, fam, grade_result_id: str) -> None:
    """规则 3 五字段（复用 tools/e2e-cohort 的 GRADE_FIELDS 与落库检查 check_grade_rows）"""
    from e2e_cohort.checks import GRADE_FIELDS, Member, Population, check_grade_rows

    async with e2e.admin.acquire() as c:
        row = await c.fetchrow(f"SELECT {', '.join(GRADE_FIELDS)} FROM platform.grade_result WHERE id = $1::uuid", grade_result_id)
        chk = await check_grade_rows(c, Population([Member(fam.student, fam.student_account, fam.household, (fam.guardian,))]))
    assert row is not None and all(row[f] is not None for f in GRADE_FIELDS) and len(row["evidence_ids"]) >= 1, dict(row or {})
    assert 0 <= float(row["confidence"]) <= 1 and row["model_version"] and row["source_version"]
    assert chk.passed, chk.detail


def policy(subject: str):
    return next(p for p in MODEL_POLICIES if p.subject == subject)


@pytest.mark.parametrize("subject", [p.subject for p in MODEL_POLICIES])
async def test_e2e_primary_down_switches_to_domestic_backup(fresh, subject):
    e2e, p = fresh, policy(subject)
    fam = next_family(e2e)
    route = e2e.router._cfg.routes[Task(p.task)]
    e2e.ctl.set(route.primary.vendor, FaultSpec(mode="unavailable"))
    for _ in range(3):
        out, _, _ = await metered_grade(e2e, fam, subject)
        res = out["result"]
        assert res["status"] == "ok", out
        g = await e2e.admin.fetchval("SELECT model_version::text FROM platform.grade_result WHERE id = $1::uuid",
                                     res["result"]["grade_result_id"])
        assert g == route.backup.model_version, "判分记录的是真正出结果的备选快照"
        await assert_provenance(e2e, fam, res["result"]["grade_result_id"])
    assert e2e.router.breaker.state(route.primary.vendor, p.task) == "open"
    assert any(a.status == "skipped_breaker_open" for rec in e2e.sink.records for a in rec.attempts)
    assert not e2e.providers["fake-x"].calls, f"{p.data_class} 数据不到境外供应商"
    assert {rec.data_class for rec in e2e.sink.records} == {p.data_class} and {rec.subject for rec in e2e.sink.records} == {subject}
    assert await bucket(e2e, fam.student) == {"settled": 1, "reserved": 0}, "同一题 24 小时内复看不重复扣次（BFF dedup）"


@pytest.mark.parametrize("subject", [p.subject for p in MODEL_POLICIES])
async def test_e2e_all_down_no_score_unknown_then_expiry_rolls_back(fresh, subject):
    """主备全失效：学习服务 5xx（BFF 归为结果未知）→ 退避同键重试仍未知 → 不结算、不释放 → 过期回收，settled 不变；
    过期后同键重试只查不执行（replay 查无，键被封存）→ 前端换新键"""
    e2e, p = fresh, policy(subject)
    fam = next_family(e2e)
    for v in ("fake-a", "fake-b"):
        e2e.ctl.set(v, FaultSpec(mode="unavailable"))
    before, b0 = await rows(e2e, fam.student), await bucket(e2e, fam.student)
    out, body, key = await metered_grade(e2e, fam, subject)
    assert out["result"]["status"] == "unknown", out
    assert out["learning_calls"] == 5, "1 次 + 退避 4 次（1/2/4/8 s 口径，按比例缩短）"
    assert await rows(e2e, fam.student) == before, "不给分：不写证据、作答与判分行"
    assert await bucket(e2e, fam.student) == {"settled": b0["settled"], "reserved": 1}, "结果未知：不结算、不释放"
    assert not e2e.providers["fake-x"].calls
    assert {rec.outcome for rec in e2e.sink.records} == {"unavailable"} and {rec.data_class for rec in e2e.sink.records} == {p.data_class}
    assert await expire_reservations(e2e) >= 1
    assert await bucket(e2e, fam.student) == {"settled": b0["settled"], "reserved": 0}, "过期回收：不扣次、无未结预留"
    state = await e2e.admin.fetchval("SELECT state::text FROM platform.usage_reservation WHERE idempotency_key = $1", key)
    assert state == "expired"
    # 恢复后同键重试：预留已过期 → 只查重放（查无并封存）→ 不会出现一次没有预留的执行
    e2e.ctl.clear()
    again, _, _ = await metered_grade(e2e, fam, subject, key=key, body=body)
    assert again["result"]["status"] == "expired_retry_with_new_key" and again["learning_calls"] == 0, again
    assert await rows(e2e, fam.student) == before
    direct = await e2e.api.post("/internal/v1/grade", json={"request": {**body, "context": {
        **body["context"], "request_id": str(uuid.uuid4()), "student_ref": fam.student, "idempotency_key": key,
        "usage_id": again["result"]["usageId"]}}, "ingest": {"channel": "keypad"}})
    assert direct.status_code == 409 and direct.json()["code"] == "IDEMPOTENCY_KEY_SEALED", direct.text


@pytest.mark.parametrize("subject", [p.subject for p in MODEL_POLICIES])
async def test_e2e_recovery_returns_to_primary_after_cooldown(fresh, subject):
    e2e, p = fresh, policy(subject)
    fam = next_family(e2e)
    route = e2e.router._cfg.routes[Task(p.task)]
    for v in ("fake-a", "fake-b"):
        e2e.ctl.set(v, FaultSpec(mode="unavailable"))
    out, _, _ = await metered_grade(e2e, fam, subject)
    assert out["result"]["status"] == "unknown"
    assert e2e.router.breaker.state(route.primary.vendor, p.task) == "open"
    await expire_reservations(e2e)  # 未知的那笔过期回收（否则同一题的内容指纹 24 小时内处于 dedup_in_progress）
    e2e.ctl.clear()
    e2e.clock.t += e2e.router._cfg.cooldown_seconds + 1
    out, _, _ = await metered_grade(e2e, fam, subject)
    res = out["result"]
    assert res["status"] == "ok" and res["charged"] is True, out
    g = await e2e.admin.fetchval("SELECT model_version::text FROM platform.grade_result WHERE id = $1::uuid", res["result"]["grade_result_id"])
    assert g == route.primary.model_version
    assert e2e.router.breaker.state(route.primary.vendor, p.task) == "closed"
    await assert_provenance(e2e, fam, res["result"]["grade_result_id"])


@pytest.mark.parametrize("subject", [p.subject for p in MODEL_POLICIES])
async def test_e2e_timeout_is_unknown_then_same_key_retry_settles_once(fresh, subject):
    """超时（结果未知）：主模型慢到超过 BFF 的请求时限 → 第一次请求超时 → 同键退避重试拿到学习服务的同一结果 →
    只写一次、只扣一次（其余故障类型只在路由层覆盖，见 lessons）"""
    e2e, p = fresh, policy(subject)
    fam = next_family(e2e)
    route = e2e.router._cfg.routes[Task(p.task)]
    e2e.ctl.set(route.primary.vendor, FaultSpec(mode="slow", extra_latency_ms=SLOW_MS))
    before, b0 = await rows(e2e, fam.student), await bucket(e2e, fam.student)
    out, _, _ = await metered_grade(e2e, fam, subject)
    res = out["result"]
    assert res["status"] == "ok" and res["charged"] is True, out
    assert out["learning_calls"] >= 2, "第一次超时按结果未知处理，同键重试"
    after = await rows(e2e, fam.student)
    assert after["grade_result"] == before["grade_result"] + 1, "同键重试不重复写入"
    assert await bucket(e2e, fam.student) == {"settled": b0["settled"] + 1, "reserved": 0}
    await assert_provenance(e2e, fam, res["result"]["grade_result_id"])


@pytest.mark.parametrize("subject", [p.subject for p in MODEL_POLICIES])
async def test_e2e_backup_garbage_goes_to_human_review_not_auto_score(fresh, subject):
    """宁可不答不乱答：主失效后备选给出不合格输出 → 转人工复核：待复核或总分为空；有分的维度只来自验证器或 Rubric 规则；
    证据里没有模型派生的分数。是否扣次见 lessons D10（维持现状：扣）"""
    e2e = fresh
    fam = next_family(e2e)
    e2e.ctl.set("fake-a", FaultSpec(mode="unavailable"))
    e2e.garbage["on"] = True
    out, _, _ = await metered_grade(e2e, fam, subject)
    res = out["result"]
    assert res["status"] == "ok", out
    s = res["result"]
    assert s["human_review_required"] is True
    async with e2e.admin.acquire() as c:
        g = await c.fetchrow("SELECT verdict, score_total, evidence_ids FROM platform.grade_result WHERE id = $1::uuid", s["grade_result_id"])
        assert g["verdict"] == "needs_review" or g["score_total"] is None, dict(g)
        dims = await c.fetch("SELECT judged_by, score FROM platform.grade_dimension WHERE grade_result_id = $1::uuid", s["grade_result_id"])
        assert all(d["judged_by"] in ("verifier", "rubric_rule") for d in dims if d["score"] is not None), [dict(d) for d in dims]
        ev = await c.fetch("SELECT kind, payload::text AS payload FROM platform.evidence_event WHERE evidence_id = ANY($1::uuid[])",
                           g["evidence_ids"])
    # 证据只有作答原文；没有模型定位的采分点片段（scoring_point_span 来自模型输出），也没有任何分数字段
    assert ev and not any(e["kind"] == "scoring_point_span" for e in ev), [e["kind"] for e in ev]
    assert not any(k in e["payload"] for e in ev for k in ('"score', '"level"', '"judgment"')), [e["kind"] for e in ev]
    await assert_provenance(e2e, fam, s["grade_result_id"])


async def test_e2e_methods_without_model_are_unaffected(fresh):
    """数学批改（验证器）在三家假供应商全部不可用时照常出结果"""
    e2e = fresh
    fam = next_family(e2e)
    for v in VENDORS:
        e2e.ctl.set(v, FaultSpec(mode="unavailable"))
    out, _, _ = await metered_grade(e2e, fam, "aya")
    assert out["result"]["status"] == "ok" and out["result"]["charged"] is True, out
    assert not any(p.calls for p in e2e.providers.values()), "数学批改不调模型"
    await assert_provenance(e2e, fam, out["result"]["result"]["grade_result_id"])
