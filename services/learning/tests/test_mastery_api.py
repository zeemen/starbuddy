"""KGE-04 第二步 PR 3：learning-internal 1.1.0 的三个接口（specs/kge-04-mastery-step2/spec.md 第 3 节）。

- `POST /internal/v1/mastery/get`、`/mastery/explain`：形状照搬 knowledge-service v0.9（响应按知识服务契约校验），信封五字段齐全；
  只认当前地图里的节点与层次；star_lit 按 star_level_rule；证据链的 weight ∈ [0, 1]、hint_level 不超过契约上限 5；
  门槛条件编码不外露；只接受服务令牌且 caller 必须是 orchestrator。
  只接受编排层令牌（BFF、洞察令牌 401）。
- `POST /internal/v1/verification`：只接受洞察服务令牌（BFF 令牌 401）；`/verification/cancel` 撤销（补偿）只认洞察令牌、幂等、不改掌握；建 verify_self_report 任务、不改掌握、幂等、不返回题目；
  学科没有配置或单元不在当前地图里 409 MASTERY_UNAVAILABLE。
一次性 Postgres（本模块独占，执行全部迁移；合成地图、题目与配置同 test_mastery_db）。全部数据合成。
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from conftest import CREDENTIAL, settings
from learning.app import create_app
from learning.config import Settings
from learning.contract import internal_contract, knowledge_contract
from learning.mastery import recompute as R
from test_mastery_db import (DAY, HOUR, LEVEL, N1, N2, SIGNED, T0, U1, V1, answer, current, engine, history, invariants,  # noqa: F401
                             item, mpg, mpools, normal_path, rc, student, tasks)

INSIGHT_TOKEN = "synthetic-insight-to-learning-credential-0001"
ORCH_TOKEN = "synthetic-orchestrator-to-learning-credential-01"
N9 = "MATH.SYN.T1.N9"      # 不在任何地图里
N3 = "MATH.SYN.T1.N3"      # 方法类节点：不参与掌握计算


def _ctx(student: str, caller: str = "orchestrator") -> dict:
    return {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": caller, "data_class": "L2", "student_ref": student}


@pytest.fixture
async def app_client(mpools):
    app_pool, _ = mpools
    eng = engine(app_pool)
    app = create_app(settings(insight_client_credential=INSIGHT_TOKEN, orchestrator_client_credential=ORCH_TOKEN), pool=app_pool,
                     transport=httpx.MockTransport(lambda r: httpx.Response(500)), mastery=eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            yield client, eng


def _bff(client, path, body):
    return client.post(path, json=body, headers={"authorization": f"Bearer {CREDENTIAL}"})


def _orch(client, path, body):
    return client.post(path, json=body, headers={"authorization": f"Bearer {ORCH_TOKEN}"})


def _insight(client, body, token=INSIGHT_TOKEN):
    return client.post("/internal/v1/verification", json=body, headers={"authorization": f"Bearer {token}"})


# ---------- mastery.get / mastery.explain ----------
async def test_get_and_explain_after_execute(app_client, mpools, student):
    client, eng = app_client
    _, admin = mpools
    answers = await normal_path(admin, eng, student, upto=2)   # 执行：apply 有记录，understand 没有
    cur = await current(admin, student)
    r = await _orch(client, "/internal/v1/mastery/get", {"context": _ctx(student), "node_ids": [N1, N2, N3, N9]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert knowledge_contract().validate("MasteryGetResponse", body) == []
    # 只认当前地图里的可计算节点：方法节点与不存在的节点不回；每个节点按其全部层次各一行，没有记录的层次是 unseen
    got = [(s["node_id"], s["cognitive_level"], s["gate"]) for s in body["result"]["states"]]
    assert got == [(N1, "understand", "unseen"), (N1, "apply", "execute"), (N2, "apply", "unseen")]
    apply_ = body["result"]["states"][1]
    assert apply_["p_mastery"] == pytest.approx(cur["p"]) and apply_["star_lit"] is False
    assert apply_["next_review_at"] is not None
    assert "p_mastery" not in body["result"]["states"][0] and "next_review_at" not in body["result"]["states"][0]
    # 信封五字段
    assert set(body["evidence_ids"]) == set(cur["evidence_ids"]) == {ev for _, ev in answers}
    assert body["confidence"] == pytest.approx(cur["conf"])
    assert body["source_version"] == f"algo:{SIGNED['aya'].algorithm_version};graph:aya-map@1.0.0"
    assert body["model_version"] == "algo:kge-mastery@0.1.0" and body["human_review_required"] is False

    r = await _orch(client, "/internal/v1/mastery/explain", {"context": _ctx(student), "node_id": N1})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert knowledge_contract().validate("MasteryExplainResponse", ex) == []
    chain = ex["result"]["chain"]
    assert [c["evidence_id"] for c in chain] == [ev for _, ev in answers]           # 按服务器记录时间
    assert all(c["evidence_kind"] == "independent" and c["evidence_level"] == "A" and c["hint_level"] == 0 for c in chain)
    assert all(0 < c["weight"] <= 1 for c in chain)
    # 门槛条件编码只在服务内（第一步 #17）
    text = json.dumps(ex)
    assert "families_min" not in text and "conditions" not in text and "p_floor" not in text
    # 大写学生 ID 与小写结果相同
    up = await _orch(client, "/internal/v1/mastery/get", {"context": _ctx(student.upper()), "node_ids": [N1]})
    assert up.status_code == 200 and up.json()["result"] == {"states": body["result"]["states"][:2]}


async def test_star_lit_by_star_level_rule(app_client, mpools, student):
    client, eng = app_client
    _, admin = mpools
    await normal_path(admin, eng, student, upto=4)   # 迁移（星亮）：N1 的星亮层次按缺省取最高层次 apply
    r = await _orch(client, "/internal/v1/mastery/get", {"context": _ctx(student), "node_ids": [N1]})
    states = r.json()["result"]["states"]
    assert [(s["cognitive_level"], s["gate"], s["star_lit"]) for s in states] == [
        ("understand", "unseen", True), ("apply", "transfer", True)]   # 星亮是节点属性，同一节点各行相同


async def test_no_records_and_unknown_student_look_the_same(app_client):
    client, _ = app_client
    r = await _orch(client, "/internal/v1/mastery/get", {"context": _ctx(str(uuid.uuid4())), "node_ids": [N1]})
    assert r.status_code == 200
    b = r.json()
    assert [s["gate"] for s in b["result"]["states"]] == ["unseen", "unseen"]
    assert b["evidence_ids"] == [] and b["confidence"] == 0
    r = await _orch(client, "/internal/v1/mastery/explain", {"context": _ctx(str(uuid.uuid4())), "node_id": N1})
    assert r.status_code == 200 and r.json()["result"] == {"node_id": N1, "chain": []}


async def test_explain_hint_level_capped_at_contract_max(app_client, mpools, student):
    """提示阶梯 6 级后做错：计入（下调），证据链里 hint_level 按契约上限记 5。"""
    client, eng = app_client
    _, admin = mpools
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-h6")
    _, ev = await answer(admin, student, "aya", it, T0, correct=False, kind="hinted", hint=6)
    await rc(eng, student, now=T0)
    r = await _orch(client, "/internal/v1/mastery/explain", {"context": _ctx(student), "node_id": N1})
    chain = r.json()["result"]["chain"]
    assert [(c["evidence_id"], c["hint_level"], c["evidence_kind"]) for c in chain] == [(ev, 5, "hinted")]
    assert 0 < chain[0]["weight"] <= 1


async def test_mastery_auth_and_caller(app_client, student):
    client, _ = app_client
    body = {"context": _ctx(student), "node_ids": [N1]}
    for path in ("/internal/v1/mastery/get", "/internal/v1/mastery/explain"):
        b = body if path.endswith("get") else {"context": _ctx(student), "node_id": N1}
        # 只认编排层令牌：没有令牌、BFF 服务令牌、洞察服务令牌一律 401
        assert (await client.post(path, json=b)).status_code == 401
        assert (await _bff(client, path, b)).status_code == 401
        assert (await client.post(path, json=b, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})).status_code == 401
        assert (await _orch(client, path, b)).status_code == 200
        bad = json.loads(json.dumps(b))
        bad["context"]["caller"] = "learning"
        r = await _orch(client, path, bad)
        assert r.status_code == 403 and r.json()["code"] == "CALLER_MISMATCH"
        assert internal_contract("1.0.0").validate("Problem", r.json()) == []
    r = await _orch(client, "/internal/v1/mastery/get", {**body, "subject": "aya"})
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    r = await _orch(client, "/internal/v1/mastery/get", {"context": {**_ctx(student), "caller": "bff"}, "node_ids": [N1]})
    assert r.status_code == 400   # 契约里调用方没有 bff


# ---------- 验证任务 ----------
def _vreq(student: str, dispute: str, **over) -> dict:
    b = {"request_id": str(uuid.uuid4()), "dispute_ref": dispute, "student_ref": student, "subject": "aya", "node_id": N1,
         "cognitive_level": LEVEL, "reason": "verify_self_report", "item_count": 2}
    b.update(over)
    return b


async def test_verification_creates_task_without_changing_mastery(app_client, mpools, student):
    client, eng = app_client
    _, admin = mpools
    await normal_path(admin, eng, student, upto=2)
    before = await history(admin, student)
    dispute = str(uuid.uuid4())
    r = await _insight(client, _vreq(student, dispute))
    assert r.status_code == 200, r.text
    t = r.json()
    assert internal_contract("1.0.0").validate("VerificationTask", t) == []
    assert set(t) == {"task_id", "node_id", "cognitive_level", "status", "due_at", "item_count", "created"}   # 没有题目
    assert t["created"] is True and t["status"] == "open" and (t["node_id"], t["cognitive_level"], t["item_count"]) == (N1, LEVEL, 2)
    rows = [x for x in await tasks(admin, student) if x["reason"] == "verify_self_report"]
    assert [x["id"] for x in rows] == [t["task_id"]]
    assert await history(admin, student) == before                     # 不改掌握
    again = await _insight(client, _vreq(student, dispute.upper()))   # 同一异议（大小写不同）：同一条
    assert again.status_code == 200 and again.json()["task_id"] == t["task_id"] and again.json()["created"] is False


async def test_verification_only_accepts_insight_token(app_client, mpools, student):
    client, _ = app_client
    _, admin = mpools
    body = _vreq(student, str(uuid.uuid4()))
    # BFF 服务令牌、没有令牌、错误令牌都不能建任务：学生只能由洞察服务（从异议记录）给出
    assert (await _bff(client, "/internal/v1/verification", body)).status_code == 401
    assert (await client.post("/internal/v1/verification", json=body)).status_code == 401
    assert (await _insight(client, body, token="x" * 48)).status_code == 401
    assert [x for x in await tasks(admin, student) if x["reason"] == "verify_self_report"] == []


async def test_verification_rejections(app_client, student):
    client, _ = app_client
    d = str(uuid.uuid4())
    for over in ({"reason": "scheduled"}, {"item_count": 6}, {"student_ref": student.upper()}, {"extra": 1}, {"subject": "echo"}):
        r = await _insight(client, _vreq(student, d, **over))
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", over
    r = await _insight(client, _vreq(student, d, subject="wenli", node_id="CHN.SYN.T1.N1"))   # 语文没有掌握配置
    assert r.status_code == 409 and r.json()["code"] == "MASTERY_UNAVAILABLE" and r.json()["retryable"] is False
    r = await _insight(client, _vreq(student, d, node_id=N9))                                 # 不在当前地图里
    assert r.status_code == 409 and r.json()["code"] == "MASTERY_UNAVAILABLE"


async def test_cancel_verification_is_idempotent_and_scoped(app_client, mpools, student):
    """撤销（洞察服务事务回滚时的补偿）：只认洞察令牌；只撤该学生 open 的自述验证任务；幂等；不改掌握"""
    client, eng = app_client
    _, admin = mpools
    await normal_path(admin, eng, student, upto=2)
    before = await history(admin, student)
    t = (await _insight(client, _vreq(student, str(uuid.uuid4())))).json()
    body = {"request_id": str(uuid.uuid4()), "task_id": t["task_id"], "student_ref": student}
    url = "/internal/v1/verification/cancel"
    assert (await _bff(client, url, body)).status_code == 401
    assert (await _orch(client, url, body)).status_code == 401
    other = {**body, "student_ref": str(uuid.uuid4())}                   # 不属于这个学生：与不存在同一个 404
    r = await client.post(url, json=other, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert r.status_code == 404 and r.json()["code"] == "TASK_NOT_FOUND"
    r = await client.post(url, json={**body, "task_id": str(uuid.uuid4())}, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert r.status_code == 404
    r = await client.post(url, json=body, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert r.status_code == 200 and r.json() == {"task_id": t["task_id"], "status": "cancelled", "cancelled": True}
    assert internal_contract("1.0.0").validate("VerificationCancelResult", r.json()) == []
    again = await client.post(url, json=body, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert again.status_code == 200 and again.json()["cancelled"] is False and again.json()["status"] == "cancelled"
    assert [x["status"] for x in await tasks(admin, student) if x["id"] == t["task_id"]] == ["cancelled"]
    assert await history(admin, student) == before                     # 不改掌握
    # 复测轮任务不能经它撤销
    rung = next(x for x in await tasks(admin, student, "open") if x["reason"] == "scheduled")
    r = await client.post(url, json={**body, "task_id": rung["id"]}, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert r.status_code == 404
    bad = await client.post(url, json={**body, "reason": "scheduled"}, headers={"authorization": f"Bearer {INSIGHT_TOKEN}"})
    assert bad.status_code == 400


async def test_verification_unconfigured_token_rejects_everything(mpools, student):
    app_pool, _ = mpools
    app = create_app(settings(), pool=app_pool, transport=httpx.MockTransport(lambda r: httpx.Response(500)), mastery=engine(app_pool))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            assert (await _insight(client, _vreq(student, str(uuid.uuid4())))).status_code == 401
            # 没配编排层令牌：掌握读取一律 401（BFF 服务令牌也不行）
            assert (await _bff(client, "/internal/v1/mastery/get", {"context": _ctx(student), "node_ids": [N1]})).status_code == 401


def test_insight_token_config():
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused"}
    assert Settings.from_env(base).insight_client_credential is None
    assert Settings.from_env({**base, "LEARNING_INSIGHT_CLIENT_TOKEN": INSIGHT_TOKEN}).insight_client_credential == INSIGHT_TOKEN
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_INSIGHT_CLIENT_TOKEN": "short"})
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_INSIGHT_CLIENT_TOKEN": CREDENTIAL})   # 不能与 BFF 服务令牌相同
    assert Settings.from_env({**base, "LEARNING_ORCHESTRATOR_CLIENT_TOKEN": ORCH_TOKEN}).orchestrator_client_credential == ORCH_TOKEN
    for bad in ({"LEARNING_ORCHESTRATOR_CLIENT_TOKEN": "short"}, {"LEARNING_ORCHESTRATOR_CLIENT_TOKEN": CREDENTIAL},
                {"LEARNING_ORCHESTRATOR_CLIENT_TOKEN": INSIGHT_TOKEN, "LEARNING_INSIGHT_CLIENT_TOKEN": INSIGHT_TOKEN}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**base, **bad})
