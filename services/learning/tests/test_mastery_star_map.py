"""H5 星图 v1 PR 1：learning-internal 1.1.0 草稿内追加的 `POST /internal/v1/star-map/nodes`（specs/star-map-v1-h5/spec.md 第 2 节）。

- 只认 BFF 服务令牌（编排层、洞察服务令牌一律 401）；请求体 caller 必须是 bff（不符 403 CALLER_MISMATCH）；
- 星 = 当前已发布地图里全部有效的 level = node 节点（统一壳 #9 默认；撤下的节点、细目不成星）；不参与掌握计算的节点
  （方法、素材、没有认知层次）一律 unseen、不亮、basis_id 为 null；
- state = 星亮层次（gates.star_basis_level，与 star_lit 同一口径）上的门槛；basis_id = 决定 state 的当前掌握记录；无记录 unseen；
- 不返回 p_mastery、confidence、证据 ID、各层次明细；按学生过滤；地图未发布 200 no_map；地图已发布但没有配置 409 MASTERY_UNAVAILABLE。
一次性 Postgres（本模块独占，执行全部迁移；合成地图、题目与配置同 test_mastery_db）。全部数据合成。
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from conftest import CREDENTIAL, settings
from learning.app import create_app
from learning.contract import internal_contract
from learning.mastery import parse_config
from test_mastery_db import (N1, N2, N3, V1, Z1, _doc, current, engine, history, invariants,  # noqa: F401
                             mpg, mpools, normal_path, student)

INSIGHT_TOKEN = "synthetic-insight-to-learning-credential-0001"
ORCH_TOKEN = "synthetic-orchestrator-to-learning-credential-01"
URL = "/internal/v1/star-map/nodes"
NMAT = "MATH.SYN.T1.N6"     # 素材类节点（level = node，不参与掌握计算）：成星，恒为 unseen
NKP0 = "MATH.SYN.T1.N7"     # 没有认知层次的知识点：成星，恒为 unseen
NDEP = "MATH.SYN.T1.N8"     # 撤下（deprecated）的可计算节点：不成星
NDET = "MATH.SYN.T1.N1.D1"  # 细目（考点）：不成星
FORBIDDEN = ("p_mastery", "confidence", "evidence_ids", "cognitive_level", "levels", "next_review_at", "next_retest_at")
# 不参与掌握计算的星（方法节点 N3 来自 test_mastery_db 的种子）：无论学生有什么记录都是 unseen、不亮、无依据
NONCOMP = [{"node_id": n, "state": "unseen", "star_lit": False, "basis_id": None} for n in (N3, NMAT, NKP0)]


@pytest.fixture(scope="module")
async def extra_nodes(mpools):
    _, admin = mpools
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")   # 一次性测试库里直接写合成行
            await c.execute(
                """INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels, status) VALUES
                     ($1::uuid, $2, 'node', 'MATH.SYN.T1', 'kp', '（合成）撤下的节点', '{apply}', 'deprecated'),
                     ($1::uuid, $3, 'node', 'MATH.SYN.T1', 'material', '（合成）素材节点', '{}', 'active'),
                     ($1::uuid, $4, 'node', 'MATH.SYN.T1', 'kp', '（合成）没有认知层次的节点', '{}', 'active'),
                     ($1::uuid, $5, 'detail', $6, 'exam_point', '（合成）考点细目', '{}', 'active')
                   ON CONFLICT DO NOTHING""", V1, NDEP, NMAT, NKP0, NDET, N1)


def _app(app_pool, configs=None):
    return create_app(settings(insight_client_credential=INSIGHT_TOKEN, orchestrator_client_credential=ORCH_TOKEN), pool=app_pool,
                      transport=httpx.MockTransport(lambda r: httpx.Response(500)), mastery=engine(app_pool, configs))


@pytest.fixture
async def app_client(mpools, extra_nodes):
    app_pool, _ = mpools
    app = _app(app_pool)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            yield client, app


def _req(student: str, subject: str = "aya", caller: str = "bff", **over) -> dict:
    return {"request_id": str(uuid.uuid4()), "caller": caller, "student_ref": student, "subject": subject, **over}


def _post(client, body, token: str | None = CREDENTIAL):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return client.post(URL, json=body, headers=headers)


async def _engine_of(mpools):
    app_pool, _ = mpools
    return engine(app_pool)


async def _new_student(mpools) -> str:
    _, admin = mpools
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute(
            """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
    return sid


def _assert_shape(body: dict) -> None:
    assert internal_contract("1.0.0").validate("StarMapNodes", body) == []
    assert set(body) == {"subject", "status", "map_version", "nodes"}
    assert all(set(n) == {"node_id", "state", "star_lit", "basis_id"} for n in body["nodes"])
    text = json.dumps(body)
    assert not any(f'"{k}"' in text for k in FORBIDDEN)


# ---------- 星态、basis_id 与口径 ----------
async def test_lit_star_basis_is_current_record(app_client, mpools, student):
    client, _ = app_client
    _, admin = mpools
    eng = await _engine_of(mpools)
    answers = await normal_path(admin, eng, student, upto=4)   # N1 的 apply（星亮层次，缺省取最高层次）到“迁移”
    cur = await current(admin, student)
    old = [h["id"] for h in await history(admin, student) if not h["is_current"]]
    assert cur["state"] == "transfer" and old                  # 有旧记录（被取代），basis 必须是当前那条
    r = await _post(client, _req(student))
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_shape(body)
    assert body["subject"] == "aya" and body["status"] == "ok" and body["map_version"] == "1.0.0"
    # 全部有效的 level = node 节点成星（统一壳 #9）：不参与掌握计算的方法、素材、无层次节点为 unseen；撤下的节点与细目不成星；按 node_id 排序
    assert body["nodes"] == [{"node_id": N1, "state": "transfer", "star_lit": True, "basis_id": cur["id"]},
                             {"node_id": N2, "state": "unseen", "star_lit": False, "basis_id": None}, *NONCOMP]
    assert not set(old) & {n["basis_id"] for n in body["nodes"]}
    text = json.dumps(body)
    assert not any(ev in text for _, ev in answers) and not any(e in text for e in cur["evidence_ids"])   # 证据 ID 不外露
    assert NDEP not in text and NDET not in text
    # star_lit 与 mastery/get 同一实现
    g = await client.post("/internal/v1/mastery/get", headers={"authorization": f"Bearer {ORCH_TOKEN}"}, json={
        "context": {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": "orchestrator", "data_class": "L2",
                    "student_ref": student}, "node_ids": [N1, N2]})
    lit = {s["node_id"]: s["star_lit"] for s in g.json()["result"]["states"]}
    assert lit == {n["node_id"]: n["star_lit"] for n in body["nodes"] if n["node_id"] in (N1, N2)}
    # 大写学生 ID 不合契约（与其他内部接口一致）
    assert (await _post(client, _req(student.upper()))).status_code == 400


async def test_execute_not_lit_and_basis_follows_new_record(app_client, mpools, student):
    client, _ = app_client
    _, admin = mpools
    eng = await _engine_of(mpools)
    await normal_path(admin, eng, student, upto=2)             # 执行
    first = await current(admin, student)
    b = (await _post(client, _req(student))).json()
    assert b["nodes"][0] == {"node_id": N1, "state": "execute", "star_lit": False, "basis_id": first["id"]}


async def test_no_records_and_unknown_student_look_the_same(app_client):
    client, _ = app_client
    r = await _post(client, _req(str(uuid.uuid4())))
    assert r.status_code == 200
    body = r.json()
    _assert_shape(body)
    assert body["nodes"] == [{"node_id": N1, "state": "unseen", "star_lit": False, "basis_id": None},
                             {"node_id": N2, "state": "unseen", "star_lit": False, "basis_id": None}, *NONCOMP]


async def test_students_are_isolated(app_client, mpools, student):
    client, _ = app_client
    _, admin = mpools
    eng = await _engine_of(mpools)
    await normal_path(admin, eng, student, upto=4)
    mine = (await current(admin, student))["id"]
    other = await _new_student(mpools)
    body = (await _post(client, _req(other))).json()
    assert [(n["state"], n["star_lit"], n["basis_id"]) for n in body["nodes"]] == [("unseen", False, None)] * 5
    assert mine not in json.dumps(body)
    assert (await _post(client, _req(student))).json()["nodes"][0]["basis_id"] == mine


async def test_star_level_rule_modes_share_one_definition(mpools, student, extra_nodes):
    """all_levels：门槛最低的层次决定星态（understand 没有记录 → unseen、不亮）；按节点配置星亮层次同理。"""
    app_pool, admin = mpools
    eng = await _engine_of(mpools)
    await normal_path(admin, eng, student, upto=4)
    for rule in ({"mode": "all_levels", "node_levels": {}}, {"mode": "star_level", "node_levels": {N1: "understand"}}):
        d = _doc("aya")
        d["star_level_rule"] = rule
        app = _app(app_pool, {"aya": parse_config(d, env="test")})
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
                b = (await _post(client, _req(student))).json()
        assert b["nodes"][0] == {"node_id": N1, "state": "unseen", "star_lit": False, "basis_id": None}, rule


# ---------- 凭据、调用方、请求校验 ----------
async def test_auth_matrix_and_caller(app_client, student):
    client, _ = app_client
    body = _req(student)
    # 只认 BFF 服务令牌：没有令牌、错误令牌、编排层令牌、洞察服务令牌一律 401
    for token in (None, "x" * 48, ORCH_TOKEN, INSIGHT_TOKEN):
        r = await _post(client, body, token)
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED", token
    assert (await _post(client, body)).status_code == 200
    for caller in ("orchestrator", "insight"):
        r = await _post(client, _req(student, caller=caller))
        assert r.status_code == 403 and r.json()["code"] == "CALLER_MISMATCH"
        assert internal_contract("1.0.0").validate("Problem", r.json()) == []
    for bad in (_req(student, caller="student"), _req(student, subject="echo"), _req(student, node_ids=[N1]),
                {k: v for k, v in body.items() if k != "caller"}):
        r = await _post(client, bad)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", bad


async def test_log_has_no_student_or_node_ids(app_client, mpools, student, caplog):
    client, _ = app_client
    _, admin = mpools
    await normal_path(admin, await _engine_of(mpools), student, upto=2)
    basis = (await current(admin, student))["id"]
    caplog.clear()
    with caplog.at_level("INFO", logger="starbuddy.learning"):
        assert (await _post(client, _req(student))).status_code == 200
        assert (await _post(client, _req(student, caller="orchestrator"))).status_code == 403
    records = [r.getMessage() for r in caplog.records if r.name == "starbuddy.learning"]
    assert any('"method": "star_map_nodes"' in m for m in records)
    text = "\n".join(records)
    assert student not in text and student.upper() not in text and basis not in text
    assert not any(n in text for n in (N1, N2, N3, NMAT, NKP0))


async def test_unconfigured_subject_is_unavailable(app_client, student):
    client, _ = app_client
    r = await _post(client, _req(student, subject="wenli"))      # 语文地图已发布，但没有掌握配置
    assert r.status_code == 409 and r.json()["code"] == "MASTERY_UNAVAILABLE" and r.json()["retryable"] is False


async def test_unpublished_map_is_no_map(app_client, mpools, student):
    client, _ = app_client
    _, admin = mpools
    r = await _post(client, _req(student, subject="zhixing"))
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["map_version"] == "1.0.0"
    await _set_map_status(admin, Z1, "retired")   # 一次性测试库：临时撤下道法地图
    try:
        r = await _post(client, _req(student, subject="zhixing"))
        assert r.status_code == 200, r.text
        assert r.json() == {"subject": "zhixing", "status": "no_map", "map_version": None, "nodes": []}
        _assert_shape(r.json())
    finally:
        await _set_map_status(admin, Z1, "published")


async def _set_map_status(admin, version: str, status: str) -> None:
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")   # 发布守卫只允许 kge.publish_map_version
            await c.execute("UPDATE kge.map_version SET status = $2 WHERE id = $1::uuid", version, status)
