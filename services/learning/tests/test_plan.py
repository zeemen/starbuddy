"""今日计划 POST /internal/v1/plan（冲刺 4-H 统一壳，specs/unified-shell-sprint4/spec.md 第 6 节；契约 learning-internal.v1，待签字）。

- 学习服务读该生当前的掌握快照组装 PlanRequest（v1.0），调适配器 /plan，按契约校验结果；
- 能力点经已发布地图版本的 kge.skill_link 换成知识节点 ID；
- 响应只有任务类型、目标节点、优先级、预计分钟、rationale_code；没有 p_mastery、confidence、evidence_ids、learner_context；
- 某科没配适配器、501、超时、结果不合契约 → 这一科 unavailable，其他科照常；
- 不写任何表（非消耗类）。
全部合成数据：地图、节点、学生都是占位（规则 8）。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL, CREDENTIAL, settings
from learning.app import create_app

MAP = "5b8e1c52-0000-4000-8000-0000000000a1"
NODE = "MATH.SYN.T1.N1"
FORBIDDEN = {"p_mastery", "confidence", "evidence_ids", "learner_context", "skill_nodes", "item_refs", "task_id"}


@pytest.fixture(scope="module")
async def synthetic_map(pools):
    """一个已发布的合成数学地图：K-SYN-0001 → MATH.SYN.T1.N1；K-SYN-0002 没有对应节点。
    发布与冻结的守卫只允许 kge.publish_map_version 发布，这里用复制模式（superuser、只在一次性测试库）直接写合成行。"""
    _, admin = pools
    async with admin.acquire() as c:
        if await c.fetchval("SELECT 1 FROM kge.map_version WHERE id = $1::uuid", MAP):
            return
        if await c.fetchval("SELECT 1 FROM kge.map_version WHERE subject = 'aya' AND status = 'published'"):
            pytest.skip("测试库里已有别的已发布数学地图")
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at)
                               VALUES ($1::uuid, 'aya', 2022, '0.0.9', 'published', now())""", MAP)
            await c.execute("""INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
                                 ($1::uuid, 'MATH.SYN', 'domain', NULL, NULL, '（合成）领域'),
                                 ($1::uuid, 'MATH.SYN.T1', 'topic', 'MATH.SYN', NULL, '（合成）主题'),
                                 ($1::uuid, $2, 'node', 'MATH.SYN.T1', 'kp', '（合成）知识节点')""", MAP, NODE)
            await c.execute("""INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                               VALUES ($1::uuid, $2, 'aya', 'K-SYN-0001', 1)""", MAP, NODE)


async def seed_mastery(admin, student: str) -> None:
    async with admin.acquire() as c:
        await c.execute(
            """INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, p_mastery, confidence,
                                                      algorithm_version, evidence_ids, next_retest_at)
               VALUES ($1::uuid, 'aya', 'K-SYN-0001', 'stable', 0.81, 0.7, 'mastery-v0@0.1.0', ARRAY[gen_random_uuid()],
                       '2026-10-11T08:00:00+08:00'),
                      ($1::uuid, 'aya', 'K-SYN-0002', 'execute', 0.55, 0.6, 'mastery-v0@0.1.0', ARRAY[gen_random_uuid()], NULL)""",
            student)


class PlanAdapter:
    """按 v1.0 契约回 PlanResponse 的假适配器；可以改状态码、延时、篡改结果。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        self.delay = 0.0
        self.mutate = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {ADAPTER_CREDENTIAL}"
        assert request.url.path.endswith("/plan")
        body = json.loads(request.content)
        self.requests.append(body)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "NOT_IMPLEMENTED", "retryable": False})
        out = {
            "contract_version": "1.0.0",
            "result": {"tasks": [
                {"task_id": str(uuid.uuid4()), "kind": "practice", "skill_nodes": ["aya:K-SYN-0002"], "priority": 0.4,
                 "estimated_minutes": 20, "rationale_code": "high_exam_weight", "item_refs": ["aya:SYN-UNV2"]},
                {"task_id": str(uuid.uuid4()), "kind": "retest", "skill_nodes": ["aya:K-SYN-0001"], "priority": 0.9,
                 "estimated_minutes": 10, "rationale_code": "retest_scheduled"},
            ]},
            "evidence_ids": [], "confidence": 1.0, "source_version": "algo:aya-priority@0.1.0",
            "model_version": "algo:aya-priority@0.1.0", "human_review_required": False, "semantics": "formal",
        }
        if self.mutate:
            out = self.mutate(out) or out
        return httpx.Response(200, json=out)


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


@pytest.fixture
async def plan_api(pools, synthetic_map, candidate):
    app_pool, _ = pools
    adapter = PlanAdapter()
    app = create_app(settings(plan_timeout=0.3), pool=app_pool, transport=httpx.MockTransport(adapter.handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            client.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield client, adapter


def query(student: str, subjects=("aya", "wenli")) -> dict:
    return {"request_id": str(uuid.uuid4()), "student_ref": student, "subjects": list(subjects)}


def keys_in(v) -> set[str]:
    if isinstance(v, dict):
        return set(v) | {k for x in v.values() for k in keys_in(x)}
    if isinstance(v, list):
        return {k for x in v for k in keys_in(x)}
    return set()


async def table_counts(admin) -> dict:
    async with admin.acquire() as c:
        return {t: await c.fetchval(f"SELECT count(*) FROM {t}") for t in (
            "platform.mastery_estimate", "platform.evidence_event", "platform.attempt", "platform.skill_observation",
            "platform.usage_reservation", "platform.usage_ledger")}


async def test_plan_groups_by_subject_sorts_and_maps_nodes(plan_api, pools, student):
    client, adapter = plan_api
    _, admin = pools
    await seed_mastery(admin, student)
    before = await table_counts(admin)
    r = await client.post("/internal/v1/plan", json=query(student))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["subject"] for s in body["subjects"]] == ["aya", "wenli"]
    aya, wenli = body["subjects"]
    assert wenli == {"subject": "wenli", "status": "unavailable", "tasks": []}  # 没配适配器
    assert aya["status"] == "ok"
    assert [t["priority"] for t in aya["tasks"]] == [0.9, 0.4]  # 按优先级从高到低
    assert aya["tasks"][0] == {"kind": "retest", "target_node_ids": [NODE], "priority": 0.9, "estimated_minutes": 10,
                               "rationale_code": "retest_scheduled"}
    assert aya["tasks"][1]["target_node_ids"] == []  # 地图里没有对应节点：空，由前端显示中性标题
    assert not (keys_in(body) & FORBIDDEN)
    # 发给适配器的是 v1.0 PlanRequest：掌握快照来自平台当前行，含复测时间；非消耗类不带 usage_id
    sent = adapter.requests[-1]
    assert sent["context"]["contract_version"] == "1.0.0" and "usage_id" not in sent["context"]
    assert sent["context"]["student_ref"] == student and sent["context"]["subject"] == "aya"
    snap = {m["skill_node"]: m for m in sent["mastery"]}
    assert snap["aya:K-SYN-0001"]["state"] == "stable" and "next_retest_at" in snap["aya:K-SYN-0001"]
    assert snap["aya:K-SYN-0002"]["state"] == "execute" and "next_retest_at" not in snap["aya:K-SYN-0002"]
    assert "learner_context" not in sent
    assert await table_counts(admin) == before  # 不写任何表


@pytest.mark.parametrize("case", ["501", "timeout", "extra_field", "wrong_version", "bad_rationale", "too_long"])
async def test_one_subject_failing_is_unavailable(plan_api, student, case):
    client, adapter = plan_api
    if case == "501":
        adapter.status = 501
    elif case == "timeout":
        adapter.delay = 2.0
    elif case == "extra_field":
        adapter.mutate = lambda o: o["result"]["tasks"][0].__setitem__("narrative", "（合成）")
    elif case == "wrong_version":
        adapter.mutate = lambda o: o.__setitem__("contract_version", "0.9.0")
    elif case == "too_long":  # 预计分钟超过 600：整科不可用，不截断（审查 L3）
        adapter.mutate = lambda o: o["result"]["tasks"][0].__setitem__("estimated_minutes", 601)
    else:
        adapter.mutate = lambda o: o["result"]["tasks"][0].__setitem__("rationale_code", "该复习了")
    r = await client.post("/internal/v1/plan", json=query(student, ("aya",)))
    assert r.status_code == 200, r.text
    assert r.json() == {"subjects": [{"subject": "aya", "status": "unavailable", "tasks": []}]}


@pytest.mark.parametrize("mutate", [
    lambda q: q["subjects"].append("echo"),                 # 英语搁置
    lambda q: q.__setitem__("mastery", []),                 # 调用方不能自带掌握快照
    lambda q: q.__setitem__("subjects", []),
    lambda q: q.__setitem__("student_ref", "not-a-uuid"),
    lambda q: q.__setitem__("account_id", str(uuid.uuid4())),
])
async def test_invalid_query_is_400(plan_api, student, mutate):
    client, adapter = plan_api
    q = query(student)
    mutate(q)
    r = await client.post("/internal/v1/plan", json=q)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert adapter.requests == []


async def test_requires_service_token(plan_api, student):
    client, _ = plan_api
    r = await client.post("/internal/v1/plan", json=query(student), headers={"authorization": "Bearer wrong-token-000000000000000000000"})
    assert r.status_code == 401


async def test_candidate_contract_not_enabled_means_unavailable(plan_api, student, monkeypatch):
    client, adapter = plan_api
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT")
    r = await client.post("/internal/v1/plan", json=query(student, ("aya",)))
    assert r.json() == {"subjects": [{"subject": "aya", "status": "unavailable", "tasks": []}]}
    assert adapter.requests == []
