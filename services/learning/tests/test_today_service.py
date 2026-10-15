"""PLT-11 今日计划跨科编排：学习服务库上测试（specs/today-plan-orchestration-plt11/spec.md 验收）。一次性 Postgres，全部合成数据。

沿用错题再练、复测的一次性库、合成地图与合成题（SYNP-*）、假知识服务与假适配器（tests/test_practice_service.py），掌握重算用复测测试的
合成配置（当日复测间隔 0）；假适配器另实现插件 API v1.0 的 /plan（合成任务），合成地图上另挂两条能力点 → 节点（只在本模块的一次性库）。
覆盖：三个来源汇成跨科有序列表（去重、优先级、预算截断、截掉的计数）；任一来源未启用或失败只跳过它（source_unavailable）；
接口前后 platform.*、kge.*、insight.* 每张表的行数与内容校验和不变（不写、不改任何表，不计量）；各科计划每页只算一次、
与 /internal/v1/plan 逐字段一致；条目不合格只标该来源不可用；未启用标 source_disabled；不调模型（只有 /plan，没有模型路由）；响应合 today-internal.v0.yaml、
没有掌握、置信度、证据 ID；鉴权只认 BFF 令牌；没配 LEARNING_TODAY_CONFIG、没开 LEARNING_INTERNAL_V1 时 501。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL, CREDENTIAL
from learning.app import create_app
from test_practice_service import (INSIGHT, KNOWLEDGE, N1, N2, N3, ORCH, PV, SECRET, Fakes, candidate_contract, clock,  # noqa: F401
                                   new_student, no_forbidden, plock, ppg, ppools, practice_settings, reset_clock)
from test_retest_service import engine, reach_execute

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

OPENAPI = Path(__file__).resolve().parents[1] / "openapi/today-internal.v0.yaml"
FIXTURE = Path(__file__).resolve().parent / "fixtures/today-config-synthetic.json"
FORBIDDEN_KEYS = {"p_mastery", "confidence", "evidence_ids", "evidence_id", "learner_context", "skill_nodes", "item_refs", "task_id",
                  "cognitive_level", "empty_reason", "reason", "priority", "state", "star_lit"}


class TFakes(Fakes):
    """再练、复测测试的假知识服务与假适配器，另实现 /plan（插件 API v1.0 PlanResponse 形状；任务可改、状态码可改）。"""

    def __init__(self) -> None:
        super().__init__()
        self.plan_status = 200
        self.plan_requests: list[dict] = []
        self.plan_tasks = [
            {"task_id": str(uuid.uuid4()), "kind": "retest", "skill_nodes": ["aya:K-SYN-0001"], "priority": 0.9,
             "estimated_minutes": 10, "rationale_code": "retest_scheduled"},
            {"task_id": str(uuid.uuid4()), "kind": "practice", "skill_nodes": ["aya:K-SYN-0002"], "priority": 0.6,
             "estimated_minutes": 12, "rationale_code": "high_exam_weight"},
            {"task_id": str(uuid.uuid4()), "kind": "lesson", "skill_nodes": ["aya:K-SYN-0099"], "priority": 0.3,  # 地图里没有对应节点
             "estimated_minutes": 5, "rationale_code": "prerequisite_of_many"},
        ]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/plan"):
            assert request.headers["authorization"] == f"Bearer {ADAPTER_CREDENTIAL}"
            self._count("plan")
            body = json.loads(request.content)
            self.plan_requests.append(body)
            if self.plan_status != 200:
                return httpx.Response(self.plan_status, json={"code": "NOT_IMPLEMENTED", "retryable": False})
            subject = body["context"]["subject"]
            tasks = [{**t, "skill_nodes": [s.replace("aya:", f"{subject}:", 1) for s in t["skill_nodes"]]} for t in self.plan_tasks]
            return httpx.Response(200, json={
                "contract_version": "1.0.0", "result": {"tasks": tasks}, "evidence_ids": [], "confidence": 1.0,
                "source_version": "algo:aya-priority@0.1.0", "model_version": "algo:aya-priority@0.1.0",
                "human_review_required": False, "semantics": "formal"})
        return await super().handler(request)


@pytest.fixture(scope="module", autouse=True)
async def skill_links(ppools):
    """合成地图上挂两条能力点 → 节点（K-SYN-0001 → N1、K-SYN-0002 → N3），让计划任务带上知识节点（只在本模块的一次性库）。"""
    _, admin = ppools
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                               VALUES ($1::uuid, $2, 'aya', 'K-SYN-0001', 1), ($1::uuid, $3, 'aya', 'K-SYN-0002', 1)
                               ON CONFLICT DO NOTHING""", PV, N1, N3)


class TApi:
    def __init__(self, client: httpx.AsyncClient, fakes: TFakes, eng) -> None:
        self.c, self.fakes, self.engine = client, fakes, eng

    async def today(self, student, subjects=("aya",), **over):
        body = {"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": list(subjects), **over}
        return await self.c.post("/internal/v1/today", json=body)

    async def ok(self, student, subjects=("aya",), **over) -> dict:
        r = await self.today(student, subjects, **over)
        assert r.status_code == 200, r.text
        return r.json()

    async def queue(self, what, student, subject="aya"):
        r = await self.c.post(f"/internal/v1/{what}/queue", json={"student_ref": student, "subject": subject})
        assert r.status_code == 200, r.text
        return r.json()["entries"]

    async def grade(self, student, item, text):
        from conftest import grade_body, to_v1_body
        from test_practice_service import CV
        b = to_v1_body(grade_body(student, item=item, text=text))
        b["context"]["content_version"] = dict(CV)
        b["attempt"]["occurred_at"] = clock().isoformat()
        r = await self.c.post("/internal/v1/grade", json={"request": b, "ingest": {"channel": "keypad"}})
        assert r.status_code == 200, r.text


def today_settings(**over):
    return practice_settings(**{"retest_config": "synthetic", "today_config": "synthetic", **over})


async def open_api(app_pool, lock_pool, eng, **over):
    fakes = TFakes()
    app = create_app(today_settings(**over), pool=app_pool, transport=httpx.MockTransport(fakes.handler), mastery=eng,
                     practice_clock=clock, practice_lock_pool=lock_pool)
    return app, fakes


@pytest.fixture
async def api(ppools, plock):
    app_pool, _ = ppools
    eng = engine(app_pool)
    app, fakes = await open_api(app_pool, plock, eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield TApi(c, fakes, eng)


@pytest.fixture
async def student(ppools) -> str:
    _, admin = ppools
    return await new_student(admin)


def _validator(name: str):
    import jsonschema
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": doc["components"]})


def keys_in(v) -> set[str]:
    if isinstance(v, dict):
        return set(v) | {k for x in v.values() for k in keys_in(x)}
    if isinstance(v, list):
        return {k for x in v for k in keys_in(x)}
    return set()


def clean(out: dict) -> None:
    """响应合服务目录 OpenAPI（每层都不许多字段）、没有掌握、置信度、证据 ID 等内部字段与取值。"""
    _validator("TodayList").validate(out)
    rest = {k: v for k, v in out.items() if k != "plan"}
    assert not (keys_in(rest) & FORBIDDEN_KEYS), keys_in(rest) & FORBIDDEN_KEYS
    # 附带的各科计划是契约 PlanDigest 原形状（带 priority，同 /internal/v1/plan）；同样没有掌握、置信度、证据 ID
    assert not (keys_in(out["plan"]) & (FORBIDDEN_KEYS - {"priority"}))
    no_forbidden(out)


async def scenario(api: TApi, student: str) -> None:
    """N1：两族独立做对 → 当日复测 rung0 到期（复测列表 due）；再做错一道 → N1 再练条目 due（与复测同节点）。
    N2：做错（只有一个题目族）→ 再练条目 due（只剩同族）；复测（若 KGE-04 排了衰减）只出新族，不可用。
    N3：做错 → 再练条目不可用（其余候选未准入或版权受限），不进今日列表。"""
    await reach_execute(api, student)
    await api.grade(student, "SYNP-0001", "41")
    await api.grade(student, "SYNP-0006", "41")
    await api.grade(student, "SYNP-0008", "41")


async def db_digest(admin) -> dict[str, tuple[int, str | None]]:
    """platform.*、kge.*、insight.* 每张表（含分区父表）的行数与内容校验和（整行文本排序后取 md5）：
    只比行数发现不了 UPDATE，这里连内容一起比（审查意见 3）。一次性测试库很小，全表扫描可以接受。"""
    async with admin.acquire() as c:
        names = [(r[0], r[1]) for r in await c.fetch(
            """SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname IN ('platform', 'kge', 'insight') AND c.relkind IN ('r', 'p') ORDER BY 1, 2""")]
        out = {}
        for ns, rel in names:
            row = await c.fetchrow(f'SELECT count(*) AS n, md5(string_agg(t::text, \'|\' ORDER BY t::text)) AS h FROM "{ns}"."{rel}" t')
            out[f"{ns}.{rel}"] = (row["n"], row["h"])
    return out


def due(entries, **kw):
    return [e for e in entries if e["status"] == "due" and all(e.get(k) == v for k, v in kw.items())]


# ---------- 汇总：去重、优先级、来源内顺序、条目引用、计划的 rationale_code ----------
async def test_merges_three_sources_with_dedupe_and_priority(api, ppools, student):
    _, admin = ppools
    await scenario(api, student)
    rq, pq = await api.queue("retest", student), await api.queue("practice", student)
    # 前提（合成剧本）：N1 在复测列表与再练列表里都 due；N2 只在再练列表 due
    assert due(rq, node_id=N1) and due(pq, node_id=N1) and due(pq, node_id=N2) and not due(rq, node_id=N2), (rq, pq)
    # 两个列表里都有不是 due 的条目（不可用）：它们不进今日列表
    assert any(e["status"] != "due" for e in rq) and any(e["node_id"] == N3 and e["status"] != "due" for e in pq), (rq, pq)
    before = await db_digest(admin)
    calls = dict(api.fakes.calls)
    out = await api.ok(student, ("aya", "zhixing"), time_budget_minutes=40)
    after = await db_digest(admin)
    assert after == before                                               # 不写、不改任何表（行数与内容；含计量、幂等、封存表）
    # 只多了一次 /plan（数学一科；道法没配适配器不调）：不出题（generate_variation）、不判分、不经模型路由、不查知识服务
    assert {k: v - calls.get(k, 0) for k, v in api.fakes.calls.items() if v != calls.get(k, 0)} == {"plan": 1}
    clean(out)
    items = out["items"]
    retest_items = [i for i in items if i["source"] == "retest"]
    assert [(i["entry_id"], i["kind"], i["node_id"]) for i in retest_items] == [(e["entry_id"], e["kind"], e["node_id"]) for e in due(rq)]
    assert all(i["estimated_minutes"] == 5 and i["rationale_code"] is None and i["subject"] == "aya" for i in retest_items)
    practice_items = [i for i in items if i["source"] == "practice"]
    # 再练与复测同节点（N1）：只留复测；N2 的再练保留，条目引用就是再练列表的 entry_id
    assert [(i["entry_id"], i["node_id"]) for i in practice_items] == [(e["entry_id"], e["node_id"]) for e in due(pq) if e["node_id"] != N1]
    assert N2 in {i["node_id"] for i in practice_items} and N1 not in {i["node_id"] for i in practice_items}
    assert all(i["kind"] == "practice" and i["estimated_minutes"] == 8 for i in practice_items)
    plan_items = [i for i in items if i["source"] == "plan"]
    # 计划里 N1 的 retest 任务被复测条目取代；其余任务按计划原有顺序、带原有 rationale_code、没有条目引用
    assert [(i["kind"], i["node_id"], i["estimated_minutes"], i["rationale_code"], i["entry_id"]) for i in plan_items] == [
        ("practice", N3, 12, "high_exam_weight", None), ("lesson", None, 5, "prerequisite_of_many", None)]
    assert [i["source"] for i in items] == ["retest"] * len(retest_items) + ["practice"] * len(practice_items) + ["plan"] * 2
    assert out["budget_minutes"] == 40
    total = sum(i["estimated_minutes"] for i in items)
    assert total <= 40 and out["truncated_count"] == 0, out
    # 道法：没配适配器（计划未启用，不算失败）；再练、复测在合成配置里开着，列表为空
    st = {(s["source"], s["subject"]): s["status"] for s in out["sources"]}
    assert st == {("retest", "aya"): "ok", ("practice", "aya"): "ok", ("plan", "aya"): "ok",
                  ("retest", "zhixing"): "ok", ("practice", "zhixing"): "ok", ("plan", "zhixing"): "source_disabled"}
    sent = api.fakes.plan_requests[-1]
    # 计划不传预算（与 /internal/v1/plan 同一请求，BFF 按科 sections 用同一份）；截断只在跨科汇总里做
    assert "constraints" not in sent and "usage_id" not in sent["context"]
    # 附带的各科计划与 /internal/v1/plan 对同一数据逐字段一致
    r = await api.c.post("/internal/v1/plan", json={"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": ["aya", "zhixing"]})
    assert r.status_code == 200 and out["plan"] == r.json(), (out["plan"], r.text)
    assert out["plan"]["subjects"][0]["tasks"][0] == {"kind": "retest", "target_node_ids": [N1], "priority": 0.9, "estimated_minutes": 10,
                                                      "rationale_code": "retest_scheduled"}


async def test_budget_truncation_and_count(api, student):
    await scenario(api, student)
    full = await api.ok(student, time_budget_minutes=40)
    assert full["truncated_count"] == 0
    cut = await api.ok(student, time_budget_minutes=20)
    # 依次累加：第一条放不进 20 分钟的及其之后全部截掉，截掉的条数返回；保留的是全量列表的前缀
    n = len(cut["items"])
    assert cut["items"] == full["items"][:n] and cut["truncated_count"] == len(full["items"]) - n > 0
    assert sum(i["estimated_minutes"] for i in cut["items"]) <= 20
    assert sum(i["estimated_minutes"] for i in full["items"][:n + 1]) > 20
    default = await api.ok(student)                                      # 不给预算：取配置（合成 30）
    assert default["budget_minutes"] == 30 and default["items"] == full["items"][:len(default["items"])]


async def test_subject_rotation_within_source(ppools, plock, student):
    """数学、道法的计划都可用时：同一来源内学科轮转（按请求里学科的顺序），各科保持计划原有顺序。"""
    from learning.config import AdapterTarget
    app_pool, _ = ppools
    adapters = {"aya": AdapterTarget("http://aya-adapter.invalid", ADAPTER_CREDENTIAL),
                "zhixing": AdapterTarget("http://zhixing-adapter.invalid", ADAPTER_CREDENTIAL)}
    app, fakes = await open_api(app_pool, plock, None, adapters=adapters)
    fakes.plan_tasks = [
        {"task_id": str(uuid.uuid4()), "kind": "practice", "skill_nodes": ["aya:K-SYN-0099"], "priority": 0.9 - k / 10, "estimated_minutes": 1,
         "rationale_code": f"syn_code_{k}"} for k in range(3)]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            a = TApi(c, fakes, None)
            for order in (("zhixing", "aya"), ("aya", "zhixing")):
                before = fakes.calls.get("plan", 0)
                out = await a.ok(student, order, time_budget_minutes=40)
                clean(out)
                assert [(i["subject"], i["rationale_code"]) for i in out["items"]] == [
                    (s, f"syn_code_{k}") for k in range(3) for s in order]
                assert fakes.calls["plan"] - before == 2                 # 每页每科只调一次适配器 /plan
                assert [s["subject"] for s in out["plan"]["subjects"]] == list(order)


# ---------- 来源失败降级 ----------
async def test_source_failure_skips_only_that_source(api, student, monkeypatch):
    await scenario(api, student)
    from learning.practice.service import PracticeService
    from learning.retest.service import RetestService

    async def boom(self, *a, **kw):
        raise RuntimeError("synthetic source failure")
    monkeypatch.setattr(RetestService, "queue", boom)
    out = await api.ok(student)
    clean(out)
    st = {s["source"]: s["status"] for s in out["sources"]}
    assert st == {"retest": "source_unavailable", "practice": "ok", "plan": "ok"}
    assert {i["source"] for i in out["items"]} == {"practice", "plan"}
    # 复测来源不可用：不再去重——N1 的再练与计划的 retest 任务都回来了
    assert {(i["source"], i["node_id"]) for i in out["items"]} >= {("practice", N1), ("plan", N1)}
    # 计划整体抛错（不只是某科 unavailable）：各科的计划来源都标不可用，其余来源照常
    import learning.today.service as TS
    real_plan = TS.plan_today

    async def plan_boom(*a, **kw):
        raise RuntimeError("synthetic plan failure")
    monkeypatch.setattr(TS, "plan_today", plan_boom)
    out = await api.ok(student, ("aya", "zhixing"))
    clean(out)
    assert {(s["source"], s["subject"]): s["status"] for s in out["sources"] if s["status"] != "ok"} == {
        ("retest", "aya"): "source_unavailable", ("retest", "zhixing"): "source_unavailable",
        ("plan", "aya"): "source_unavailable", ("plan", "zhixing"): "source_disabled"}
    assert {i["source"] for i in out["items"]} == {"practice"} and out["plan"] is None
    monkeypatch.setattr(TS, "plan_today", real_plan)
    monkeypatch.setattr(PracticeService, "queue", boom)
    api.fakes.plan_status = 501
    out = await api.ok(student)
    clean(out)
    assert out["items"] == [] and out["truncated_count"] == 0
    assert {s["status"] for s in out["sources"]} == {"source_unavailable"}
    assert out["plan"] == {"subjects": [{"subject": "aya", "status": "unavailable", "tasks": []}]}


async def test_bad_entry_marks_only_that_source_unavailable(api, student, monkeypatch):
    """复测列表里出现不认识的条目类别（或缺字段）：只把这一来源标 source_unavailable，不整体 500（审查意见 1）。"""
    await scenario(api, student)
    from learning.practice.service import PracticeService
    from learning.retest.service import RetestService
    real_rq, real_pq = RetestService.queue, PracticeService.queue

    async def odd_retest(self, student, subject):
        out = await real_rq(self, student, subject)
        return {**out, "entries": [{**e, "kind": "mystery"} for e in out["entries"]]}

    async def odd_practice(self, student, subject):
        out = await real_pq(self, student, subject)
        return {**out, "entries": [{k: v for k, v in e.items() if k != "node_id"} for e in out["entries"]]}
    monkeypatch.setattr(RetestService, "queue", odd_retest)
    out = await api.ok(student)
    clean(out)
    assert {s["source"]: s["status"] for s in out["sources"]} == {"retest": "source_unavailable", "practice": "ok", "plan": "ok"}
    assert "retest" not in {i["source"] for i in out["items"]}
    monkeypatch.setattr(RetestService, "queue", real_rq)
    monkeypatch.setattr(PracticeService, "queue", odd_practice)
    out = await api.ok(student)
    clean(out)
    assert {s["source"]: s["status"] for s in out["sources"]} == {"retest": "ok", "practice": "source_unavailable", "plan": "ok"}


async def test_subject_not_enabled_is_source_disabled(api, student):
    """配置里没开的学科（合成配置的再练、复测只开数学、道法；语文也没配适配器）：标 source_disabled，不算失败。"""
    out = await api.ok(student, ("wenli",))
    clean(out)
    assert {s["source"]: s["status"] for s in out["sources"]} == {"retest": "source_disabled", "practice": "source_disabled",
                                                                 "plan": "source_disabled"}
    assert out["items"] == [] and out["plan"] == {"subjects": [{"subject": "wenli", "status": "unavailable", "tasks": []}]}


async def test_modules_not_started_are_source_disabled(ppools, plock, student):
    """再练、复测都没配：对应来源标 source_disabled（未启用，不算失败），计划照常（今日编排不要求它们）。"""
    app_pool, _ = ppools
    app, fakes = await open_api(app_pool, plock, None, **NO_PRACTICE)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            out = await TApi(c, fakes, None).ok(student)
    clean(out)
    assert {s["source"]: s["status"] for s in out["sources"]} == {"retest": "source_disabled", "practice": "source_disabled", "plan": "ok"}
    assert [i["source"] for i in out["items"]] == ["plan"] * 3
    assert out["items"][0] == {"subject": "aya", "kind": "retest", "node_id": N1, "estimated_minutes": 10, "source": "plan",
                               "entry_id": None, "rationale_code": "retest_scheduled"}


# ---------- 开关与鉴权 ----------
NO_PRACTICE = {"practice_config": None, "retest_config": None, "practice_ticket_secret": None, "practice_presenter": None}


@pytest.mark.parametrize("case", ["no_config", "internal_v1_off"])
async def test_not_configured_is_501(ppools, plock, student, monkeypatch, case):
    """不设 LEARNING_TODAY_CONFIG：501（再练、复测照常）；设了但没开 LEARNING_INTERNAL_V1：同样 501，不调适配器。"""
    app_pool, _ = ppools
    if case == "no_config":
        app, fakes = await open_api(app_pool, plock, None, today_config=None)
    else:
        monkeypatch.delenv("LEARNING_INTERNAL_V1", raising=False)   # 再练模块要求它，所以这里只配今日编排
        app, fakes = await open_api(app_pool, plock, None, **NO_PRACTICE)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            a = TApi(c, fakes, None)
            r = await a.today(student)
            assert r.status_code == 501 and r.json()["code"] == "TODAY_UNAVAILABLE", r.text
            assert fakes.calls == {}                                     # 不调适配器
            if case == "no_config":
                assert len(await a.queue("practice", student)) == 0 and len(await a.queue("retest", student)) == 0


async def test_unsigned_config_outside_test_dev_disables_module(ppools, plock, monkeypatch):
    """合成（未签字）配置出现在 test / dev 以外：只是这一模块不启动（接口 501），服务其余部分照常启动。"""
    from learning.today import config as TC
    with pytest.raises(TC.TodayConfigUnsigned):
        TC.load(str(FIXTURE), env="pilot")
    app_pool, _ = ppools
    app, fakes = await open_api(app_pool, plock, None, env="dev", today_config=str(FIXTURE), **NO_PRACTICE)
    async with app.router.lifespan_context(app):
        pass
    monkeypatch.setattr(TC, "ALLOWED_ENVS", ("test",))              # 把 dev 当作“test / dev 以外”的环境核对降级路径
    app, fakes = await open_api(app_pool, plock, None, env="dev", today_config=str(FIXTURE), **NO_PRACTICE)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {CREDENTIAL}"
            r = await c.post("/internal/v1/today", json={"request_id": str(uuid.uuid4()), "student_ref": str(uuid.uuid4()), "subjects": ["aya"]})
            assert r.status_code == 501 and r.json()["code"] == "TODAY_UNAVAILABLE"


async def test_only_bff_token(api, student):
    for header in ("", f"Bearer {ORCH}", f"Bearer {INSIGHT}", f"Bearer {ADAPTER_CREDENTIAL}", f"Bearer {KNOWLEDGE.credential}",
                   f"Bearer {SECRET}", f"Bearer {CREDENTIAL}x", CREDENTIAL):
        r = await api.c.post("/internal/v1/today", json={"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": ["aya"]},
                             headers={"authorization": header})
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED", header
    assert api.fakes.calls == {}


async def test_request_validation(api, student):
    for over in ({"time_budget_minutes": 41}, {"time_budget_minutes": 19}, {"subjects": ["echo"]}, {"mastery": []}, {"student_ref": "x"}):
        r = await api.today(student, **over)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", over
    r = await api.c.post("/internal/v1/today", content=b'{"student_ref": "a", "student_ref": "b", "subjects": ["aya"], "request_id": "x"}')
    assert r.status_code == 400
    assert (await api.c.post("/internal/v1/today", content=b"x" * (65 * 1024))).status_code == 413
    assert api.fakes.calls == {}


def test_settings_read_today_config():
    from learning.config import Settings
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused"}
    assert Settings.from_env(base).today_config is None
    assert Settings.from_env({**base, "LEARNING_TODAY_CONFIG": "synthetic"}).today_config == "synthetic"


def test_internal_openapi_matches_api_keys():
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    assert set(doc["paths"]) == {"/internal/v1/today"}
    schema = doc["paths"]["/internal/v1/today"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    from learning.today.service import OPTIONAL_KEYS, QUERY_KEYS
    assert set(schema["required"]) == set(QUERY_KEYS) and set(schema["properties"]) == set(QUERY_KEYS | OPTIONAL_KEYS)
    assert schema["additionalProperties"] is False
    item = doc["components"]["schemas"]["TodayItem"]
    from learning.today.merge import Item
    # task_id 只在 LEARNING_TODAY_SNAPSHOT=1 时出现（PLT-15），不在必填里；其余字段与 Item 一一对应
    assert set(item["required"]) == set(item["properties"]) - {"task_id"} == set(Item.__dataclass_fields__)
    src = (Path(__file__).resolve().parents[1] / "learning/today/api.py").read_text(encoding="utf-8")
    assert re.findall(r'@app\.post\("([^"]+)"\)', src) == ["/internal/v1/today"]
