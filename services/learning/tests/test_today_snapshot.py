"""PLT-15 PR1 今日计划快照：学习服务库上测试（specs/today-plan-snapshot-plt15/spec.md 范围第 2 条与验收“开关关时学习服务响应不变”）。

一次性 Postgres（迁移 0055 已执行），全部合成数据；沿用 PLT-11 今日编排测试的假适配器、合成地图与剧本（tests/test_today_service.py）。
覆盖：开关关着时响应与现状逐字段一致、不写快照表；打开时当天首次读取冻结（只写 platform.today_plan_item 这一张表）、每条带 task_id，
同日再读（上游变了、预算变了）返回同一批条目与 task_id；跨天另冻一批；没有条目时不冻结；两次首次读取并发也只冻一批；
两名学生各自一批；快照存的是编排结果（来源、条目引用、推荐理由编码），不存掌握、置信度、证据 ID；快照写入失败回 503；
开关取值与依赖的配置校验；服务内 OpenAPI 的 task_id 字段。
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest

from conftest import CREDENTIAL
from test_practice_service import candidate_contract, new_student, no_forbidden, plock, ppg, ppools, reset_clock  # noqa: F401
from test_retest_service import engine
from test_today_service import (FORBIDDEN_KEYS, N1, N3, TApi, _validator, db_digest, keys_in, open_api, scenario,  # noqa: F401
                                skill_links)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
UUID_LEN = 36


@asynccontextmanager
async def today_api(ppools, plock, *, snapshot: bool, with_engine: bool = True, **over):
    app_pool, _ = ppools
    eng = engine(app_pool) if with_engine else None
    app, fakes = await open_api(app_pool, plock, eng, today_snapshot=snapshot, **over)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield TApi(c, fakes, eng)


@pytest.fixture
async def student(ppools) -> str:
    _, admin = ppools
    return await new_student(admin)


async def rows(admin, student: str) -> list[dict]:
    async with admin.acquire() as c:
        return [dict(r) for r in await c.fetch(
            """SELECT id::text AS id, plan_date, seq, subject::text AS subject, kind, source, node_id, estimated_minutes,
                      entry_ref::text AS entry_ref, rationale_code
                 FROM platform.today_plan_item WHERE student_id = $1::uuid ORDER BY plan_date, seq""", student)]


def strip(out: dict) -> dict:
    return {**{k: v for k, v in out.items() if k != "snapshot"},
            "items": [{k: v for k, v in i.items() if k != "task_id"} for i in out["items"]]}


def clean_on(out: dict, frozen: bool = True) -> None:
    """开关打开时：响应合服务目录 OpenAPI；frozen 时 snapshot = frozen、每条都有 task_id（小写 UUID）；not_frozen 时一条都没有；
    除 task_id、snapshot 外没有掌握、置信度、证据 ID 等内部字段。"""
    _validator("TodayList").validate(out)
    assert out["snapshot"] == ("frozen" if frozen else "not_frozen"), out.get("snapshot")
    if frozen:
        assert all(isinstance(i.get("task_id"), str) and len(i["task_id"]) == UUID_LEN and str(uuid.UUID(i["task_id"])) == i["task_id"]
                   for i in out["items"]), out["items"]
    else:
        assert all("task_id" not in i for i in out["items"]), out["items"]
    rest = {k: v for k, v in out.items() if k != "plan"}
    assert not (keys_in(rest) & (FORBIDDEN_KEYS - {"task_id"}))
    no_forbidden(out)


# ---------- 开关关着：与现状逐字段一致，不写任何表 ----------
async def test_off_is_unchanged_and_on_first_read_only_adds_task_id(ppools, plock, student):
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=False) as off:
        before = await db_digest(admin)
        out_off = await off.ok(student)
        assert await db_digest(admin) == before                           # 关着：不写、不改任何表（含快照表）
        assert all("task_id" not in i for i in out_off["items"]) and out_off["items"] and "snapshot" not in out_off, out_off
        assert await off.ok(student) == out_off
    async with today_api(ppools, plock, snapshot=True) as on:
        before = await db_digest(admin)
        out_on = await on.ok(student)
        after = await db_digest(admin)
    clean_on(out_on)
    # 打开后的首次读取：除每条多一个 task_id 外，与关着时逐字段一致（同一数据、同一天）
    assert strip(out_on) == out_off
    # 只写了快照表这一张（其余 platform.*、kge.*、insight.* 每张表的行数与内容都不变）
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {"platform.today_plan_item"}, changed
    assert after["platform.today_plan_item"][0] - before["platform.today_plan_item"][0] == len(out_on["items"])


# ---------- 冻结与同日再读 ----------
async def test_freeze_then_same_batch_on_reread(ppools, plock, student):
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True) as a:
        await scenario(a, student)                                         # 三个来源都有条目（复测、再练、计划）
        first = await a.ok(student, ("aya", "zhixing"), time_budget_minutes=40)
        clean_on(first)
        saved = await rows(admin, student)
        assert len(saved) == len(first["items"]) and {r["source"] for r in saved} >= {"retest", "practice", "plan"}
        # 快照行与响应逐条对应：序号、学科、类别、来源、节点、预计分钟、条目引用、计划的推荐理由编码；任务 ID 就是行 ID
        today = saved[0]["plan_date"]
        assert [(r["seq"], r["id"]) for r in saved] == [(n, i["task_id"]) for n, i in enumerate(first["items"], start=1)]
        for r, i in zip(saved, first["items"]):
            assert r["plan_date"] == today
            assert (r["subject"], r["kind"], r["node_id"], r["estimated_minutes"], r["entry_ref"], r["rationale_code"]) == (
                i["subject"], i["kind"], i["node_id"], i["estimated_minutes"], i["entry_id"], i["rationale_code"])
            assert r["source"] == ("verify" if i["kind"] == "verify" else i["source"])
        # 上游变了（计划换了一批任务）、预算变了：同一天再读，条目与 task_id 都取快照，不改写；表里不多不少
        a.fakes.plan_tasks = [{"task_id": str(uuid.uuid4()), "kind": "review", "skill_nodes": ["aya:K-SYN-0002"], "priority": 0.5,
                               "estimated_minutes": 3, "rationale_code": "syn_changed"}]
        again = await a.ok(student, ("aya",), time_budget_minutes=20)
        clean_on(again)
        assert again["items"] == first["items"]
        assert await rows(admin, student) == saved
        # 截掉的条数、预算、来源状态与附带的计划仍按这次读取现算（快照不存它们）
        assert again["budget_minutes"] == 20 and [s["subject"] for s in again["plan"]["subjects"]] == ["aya"]
        assert again["plan"]["subjects"][0]["tasks"][0]["rationale_code"] == "syn_changed"


async def test_next_day_freezes_new_batch(ppools, plock, student):
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True) as a:
        first = await a.ok(student)
        # 模拟跨天：以超级用户把已冻结的行挪到昨天（学习服务自己不能改，也不能补写别的日子）
        async with admin.acquire() as c:
            await c.execute("UPDATE platform.today_plan_item SET plan_date = plan_date - 1 WHERE student_id = $1::uuid", student)
        second = await a.ok(student)
        clean_on(second)
    assert strip(second) == strip(first)                                    # 同样的数据算出同样的条目
    assert not ({i["task_id"] for i in second["items"]} & {i["task_id"] for i in first["items"]})   # 但跨天是另一批任务 ID
    saved = await rows(admin, student)
    assert len(saved) == 2 * len(first["items"]) and len({r["plan_date"] for r in saved}) == 2


async def test_empty_list_is_not_frozen(ppools, plock, student):
    """这次没有任何条目（语文：三个来源都未启用）：不写快照，同日稍后有条目时还能冻结。"""
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True) as a:
        out = await a.ok(student, ("wenli",))
        clean_on(out, frozen=False)
        assert out["items"] == [] and await rows(admin, student) == []
        later = await a.ok(student, ("aya",))
        clean_on(later)
        assert later["items"] and len(await rows(admin, student)) == len(later["items"])


async def test_concurrent_first_reads_freeze_one_batch(ppools, plock, student):
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True) as a:
        outs = await asyncio.gather(*(a.ok(student) for _ in range(4)))
    ids = [[i["task_id"] for i in o["items"]] for o in outs]
    assert all(x == ids[0] for x in ids) and ids[0], ids
    assert [r["id"] for r in await rows(admin, student)] == ids[0]


async def test_freeze_waits_on_advisory_lock(ppools, plock, student):
    """确定性核对冻结先取（学生, 当天）咨询锁：别处持锁时首读等到锁超时回 503、不写任何行（并发用例只是概率性发现）。"""
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True) as a, admin.acquire() as c:
        async with c.transaction():
            day = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
            await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"platform.today_plan_item:{student}:{day.isoformat()}")
            # 限时：锁等待上限若被去掉，这里会一直等，按失败计（不拖住变异测试）
            r = await asyncio.wait_for(a.today(student), timeout=15)
            assert r.status_code == 503, r.text
    assert await rows(admin, student) == []


async def test_each_student_own_snapshot(ppools, plock, student):
    _, admin = ppools
    other = await new_student(admin)
    async with today_api(ppools, plock, snapshot=True) as a:
        mine, theirs = await a.ok(student), await a.ok(other)
        assert (await a.ok(student))["items"] == mine["items"]
    assert not ({i["task_id"] for i in mine["items"]} & {i["task_id"] for i in theirs["items"]})
    assert [r["id"] for r in await rows(admin, other)] == [i["task_id"] for i in theirs["items"]]


async def test_snapshot_rows_store_no_inference(ppools):
    """快照表只存编排结果：没有掌握、置信度、证据 ID、理由文本列（规则 3、11）。"""
    _, admin = ppools
    async with admin.acquire() as c:
        cols = {r[0] for r in await c.fetch("""SELECT column_name FROM information_schema.columns
                                                WHERE table_schema = 'platform' AND table_name = 'today_plan_item'""")}
    assert cols == {"id", "student_id", "plan_date", "seq", "subject", "kind", "source", "node_id", "estimated_minutes", "entry_ref",
                    "rationale_code", "created_at"}


# ---------- 来源失败不冻结；按请求学科过滤；已删除学生；推荐理由编码 ----------
async def test_source_unavailable_does_not_freeze_until_recovered(ppools, plock, student, monkeypatch):
    """任一启用的来源失败（source_unavailable）：不冻结，照常返回现算列表、不带 task_id（snapshot = not_frozen）；
    来源恢复后的读取再冻结（审查阻断 2）。未启用（source_disabled）不算失败。"""
    _, admin = ppools
    from learning.retest.service import RetestService
    real = RetestService.queue

    async def boom(self, *a, **kw):
        raise RuntimeError("synthetic source failure")
    async with today_api(ppools, plock, snapshot=True) as a:
        await scenario(a, student)
        monkeypatch.setattr(RetestService, "queue", boom)
        degraded = await a.ok(student)
        clean_on(degraded, frozen=False)
        assert degraded["items"] and {s["status"] for s in degraded["sources"]} >= {"source_unavailable"}
        assert await rows(admin, student) == []
        monkeypatch.setattr(RetestService, "queue", real)
        recovered = await a.ok(student)
        clean_on(recovered)
        assert "retest" in {i["source"] for i in recovered["items"]}
        assert [r["id"] for r in await rows(admin, student)] == [i["task_id"] for i in recovered["items"]]
        # 冻结之后来源再失败：照样返回已冻结的那一批（快照不随来源状态变）
        monkeypatch.setattr(RetestService, "queue", boom)
        again = await a.ok(student)
        clean_on(again)
        assert again["items"] == recovered["items"]


async def test_reread_filters_snapshot_to_requested_subjects(ppools, plock, student):
    """冻结时数学、道法都有条目；再读只请求一科时只给这一科（不返回请求外的学科），顺序仍按快照。"""
    from learning.config import AdapterTarget
    from conftest import ADAPTER_CREDENTIAL
    adapters = {"aya": AdapterTarget("http://aya-adapter.invalid", ADAPTER_CREDENTIAL),
                "zhixing": AdapterTarget("http://zhixing-adapter.invalid", ADAPTER_CREDENTIAL)}
    async with today_api(ppools, plock, snapshot=True, with_engine=False, adapters=adapters) as a:
        a.fakes.plan_tasks = [{"task_id": str(uuid.uuid4()), "kind": "lesson", "skill_nodes": ["aya:K-SYN-0099"], "priority": 0.5,
                               "estimated_minutes": 3, "rationale_code": "prerequisite_of_many"}]
        both = await a.ok(student, ("aya", "zhixing"))
        clean_on(both)
        assert {i["subject"] for i in both["items"]} == {"aya", "zhixing"}
        only = await a.ok(student, ("zhixing",))
        clean_on(only)
        assert only["items"] == [i for i in both["items"] if i["subject"] == "zhixing"] and only["items"]
        flipped = await a.ok(student, ("zhixing", "aya"))
        assert flipped["items"] == both["items"]                             # 学科顺序不改快照顺序


async def test_erased_student_gets_empty_list_and_no_write(ppools, plock, student):
    """已执行删除的学生：不写快照、items 为空（snapshot = not_frozen）；冻结之后才删除的，也不再返回快照（安全审查阻断 1）。"""
    _, admin = ppools
    other = await new_student(admin)
    async with today_api(ppools, plock, snapshot=True) as a:
        frozen = await a.ok(other)
        clean_on(frozen)
        async with admin.acquire() as c:
            await c.execute("UPDATE platform.student_profile SET erased_at = now() WHERE id = ANY($1::uuid[])", [student, other])
        for s in (student, other):
            out = await a.ok(s)
            clean_on(out, frozen=False)
            assert out["items"] == [] and out["truncated_count"] == 0
    assert await rows(admin, student) == []
    assert len(await rows(admin, other)) == len(frozen["items"])           # 删除后没有新写入（删行由删除执行负责）


async def test_unlisted_rationale_code_is_nulled_in_snapshot(ppools, plock, student):
    """推荐理由编码只存 BFF 已登记的（迁移 0055 的 CHECK 同一张表）；其余置空，已登记的照存。"""
    _, admin = ppools
    async with today_api(ppools, plock, snapshot=True, with_engine=False) as a:
        a.fakes.plan_tasks = [
            {"task_id": str(uuid.uuid4()), "kind": "lesson", "skill_nodes": ["aya:K-SYN-0099"], "priority": 0.6, "estimated_minutes": 3,
             "rationale_code": "syn_unlisted_code"},
            {"task_id": str(uuid.uuid4()), "kind": "review", "skill_nodes": ["aya:K-SYN-0099"], "priority": 0.5, "estimated_minutes": 3,
             "rationale_code": "retest_due"}]
        out = await a.ok(student)
        clean_on(out)
    assert [i["rationale_code"] for i in out["items"]] == [None, "retest_due"]
    assert [r["rationale_code"] for r in await rows(admin, student)] == [None, "retest_due"]


# ---------- 失败与开关 ----------
@pytest.mark.parametrize("exc,status,code,retryable", [
    (asyncpg.PostgresError("synthetic snapshot failure"), 503, "TODAY_SNAPSHOT_UNAVAILABLE", True),
    (asyncpg.LockNotAvailableError("synthetic lock timeout"), 503, "TODAY_SNAPSHOT_UNAVAILABLE", True),
    (asyncpg.CheckViolationError("synthetic check violation"), 500, "TODAY_SNAPSHOT_REJECTED", False),
    (asyncpg.UniqueViolationError("synthetic unique violation"), 500, "TODAY_SNAPSHOT_REJECTED", False),
])
async def test_snapshot_failure_status(ppools, plock, student, monkeypatch, exc, status, code, retryable):
    """快照读写失败：约束违反（23xxx）是确定失败（500，不可重试）；锁等待超时等其余错误 503 可重试。都不回列表、不带出错误原文。"""
    _, admin = ppools
    import learning.today.store as TS

    async def boom(*a, **kw):
        raise exc
    async with today_api(ppools, plock, snapshot=True) as a:
        monkeypatch.setattr(TS, "freeze", boom)
        r = await a.today(student)
        assert r.status_code == status, r.text
        body = r.json()
        assert body["code"] == code and body["retryable"] is retryable
        assert (r.headers.get("retry-after") == "2") is retryable
        assert "items" not in body and "synthetic" not in r.text
    assert await rows(admin, student) == []


async def test_store_uses_learning_role_and_lock_timeout(ppools, student, monkeypatch):
    """快照事务以 starbuddy_learning 身份（SET LOCAL ROLE）执行，并设锁等待上限（审查建议）。"""
    app_pool, _ = ppools
    import learning.today.store as TS
    seen: list[str] = []
    real = TS.learning_tx

    from contextlib import asynccontextmanager as acm

    @acm
    async def spy(pool):
        async with real(pool) as c:
            yield c
            seen.append(await c.fetchval("SELECT current_user || '|' || current_setting('lock_timeout')"))
    monkeypatch.setattr(TS, "learning_tx", spy)
    from learning.today import merge as M
    status, _ = await TS.freeze(app_pool, student, [M.Item("aya", "lesson", None, 5, "plan", None, None)], may_freeze=True)
    assert status == TS.CREATED and seen == [f"starbuddy_learning|{TS.LOCK_TIMEOUT}"]


async def test_learning_role_cannot_rewrite_or_backdate(ppools, student):
    """学习服务的登录角色：能写当天的行，不能补写别的日子、不能改、不能删（迁移 0055 的权限与守卫）。"""
    app_pool, _ = ppools
    async with app_pool.acquire() as c:
        await c.execute("""INSERT INTO platform.today_plan_item (student_id, seq, subject, kind, source, estimated_minutes)
                           VALUES ($1::uuid, 1, 'aya', 'lesson', 'plan', 5)""", student)
        with pytest.raises(asyncpg.CheckViolationError):
            await c.execute("""INSERT INTO platform.today_plan_item (student_id, plan_date, seq, subject, kind, source, estimated_minutes)
                               VALUES ($1::uuid, (now() AT TIME ZONE 'Asia/Shanghai')::date - 1, 2, 'aya', 'lesson', 'plan', 5)""", student)
        for sql in ("UPDATE platform.today_plan_item SET seq = 3 WHERE student_id = $1::uuid",
                    "DELETE FROM platform.today_plan_item WHERE student_id = $1::uuid"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(sql, student)


async def test_startup_refuses_without_migration_0055(ppools, plock):
    """开关打开而库里没有 platform.today_plan_item（迁移 0055 未执行）：拒绝启动；开关关着照常启动。"""
    from learning.app import create_app
    from test_today_service import TFakes, today_settings
    app_pool, _ = ppools

    class NoSnapshotTable:
        """把“快照表在不在”的核对答成不在，其余原样交给真实连接池。"""
        async def fetchval(self, sql, *a, **kw):
            return False if "today_plan_item" in sql else await app_pool.fetchval(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(app_pool, name)
    for snapshot in (True, False):
        app = create_app(today_settings(today_snapshot=snapshot), pool=NoSnapshotTable(), transport=httpx.MockTransport(TFakes().handler),
                         practice_lock_pool=plock)
        if snapshot:
            with pytest.raises(RuntimeError, match="0055"):
                async with app.router.lifespan_context(app):
                    pass
        else:
            async with app.router.lifespan_context(app):
                pass


def test_settings_today_snapshot():
    from learning.config import Settings
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused"}
    assert Settings.from_env(base).today_snapshot is False
    assert Settings.from_env({**base, "LEARNING_TODAY_SNAPSHOT": "0", "LEARNING_TODAY_CONFIG": "synthetic"}).today_snapshot is False
    assert Settings.from_env({**base, "LEARNING_TODAY_SNAPSHOT": "1", "LEARNING_TODAY_CONFIG": "synthetic"}).today_snapshot is True
    for bad in ("true", "yes", "2", " 1"):
        with pytest.raises(RuntimeError, match="LEARNING_TODAY_SNAPSHOT"):
            Settings.from_env({**base, "LEARNING_TODAY_SNAPSHOT": bad, "LEARNING_TODAY_CONFIG": "synthetic"})
    with pytest.raises(RuntimeError, match="LEARNING_TODAY_CONFIG"):
        Settings.from_env({**base, "LEARNING_TODAY_SNAPSHOT": "1"})


def test_openapi_task_id_is_optional_uuid():
    import yaml
    from test_today_service import OPENAPI
    item = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]["TodayItem"]
    assert item["properties"]["task_id"] == {**item["properties"]["task_id"], "type": "string", "format": "uuid"}
    assert "task_id" not in item["required"]
