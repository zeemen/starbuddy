"""learning-internal v1 签字后的写入防护（specs/launch-lt04-learning-internal-v1 决定记录与决定记录（续）第 2–4 条）。全部合成数据。

- 服务身份：只有 BFF 服务令牌（LEARNING_SERVICE_TOKEN）能调 /internal/v1 的 grade、diagnose；编排层、洞察服务、路由客户端令牌与错误令牌一律 401；
- 计量核对：usage_id + 幂等键 + 学生 + 方法必须对上计量账本里预留中的预留（迁移 0058），否则 409 IDEMPOTENCY_KEY_SEALED，
  不调适配器、不落库；缺 usage_id 同样 409；
- 幂等重放在核对之前：同键已成功的请求在 BFF 结算之后重放仍拿到原结果、不重复写；
- 用途只能 practice（自报其他值 400，见 test_evidence_attributes.py）；
- v0 写入下线（决定记录（再续）第 1 条）：/internal/v0/grade、/internal/v0/diagnose 一律 410，只读接口保留。
本模块的用例标 real_meter：计量记录由用例自己写（conftest.meter_row），不代建。
"""
from __future__ import annotations

import json
import subprocess
import uuid

import asyncpg
import httpx
import pytest

from conftest import CREDENTIAL, at, counts, diagnose_body, env, grade_body, grant_consents, meter_row, settings, to_v1_body
from learning.app import create_app
from learning.contract import internal_contract

pytestmark = pytest.mark.real_meter

ORCH = "synthetic-orchestrator-to-learning-credential-01"
INSIGHT = "synthetic-insight-to-learning-credential-000001"
ROUTE = "synthetic-zhixing-route-client-credential-000001"
ONE_GRADE = {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1, "error_attribution": 1, "skill_observation": 0}
NOTHING = dict.fromkeys(ONE_GRADE, 0)


def body_for(method: str, student: str, minute: int = 1) -> dict:
    return to_v1_body(grade_body(student) if method == "grade" else diagnose_body(student, [at("SYN-UNV", "42", minute)]))


async def reserve(admin, body: dict, method: str, state: str = "reserved") -> None:
    ctx = body["context"]
    await meter_row(admin, usage_id=ctx["usage_id"], key=ctx["idempotency_key"], student=ctx["student_ref"], method=method, state=state)


async def seals(admin, key: str) -> int:
    async with admin.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM platform.replay_seal WHERE idempotency_key = $1", key)


# ---------- 服务身份 ----------
@pytest.fixture
async def multi_token_api(pools, fake_adapter):
    """配齐编排层、洞察服务与一个路由客户端令牌的学习服务：证明这些令牌都不能调写入接口。"""
    app_pool, _ = pools
    # 路由客户端令牌已配置；注入一个占位模型路由（本用例不调模型），免得启动时按部署配置建真实路由
    app = create_app(settings(insight_client_credential=INSIGHT, orchestrator_client_credential=ORCH, route_clients={"zhixing": ROUTE}),
                     pool=app_pool, transport=httpx.MockTransport(fake_adapter.handler), model_router=object())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            yield client


@pytest.mark.parametrize("method", ["grade", "diagnose"])
@pytest.mark.parametrize("header", [f"Bearer {ORCH}", f"Bearer {INSIGHT}", f"Bearer {ROUTE}", "Bearer synthetic-wrong-credential-0000000001",
                                    f"bearer {CREDENTIAL}", f"Bearer {CREDENTIAL}x", None])
async def test_other_tokens_cannot_write(multi_token_api, pools, student, fake_adapter, method, header):
    """决定第 4 条：非 BFF 身份（编排层、洞察服务、路由客户端令牌、错误令牌、缺令牌）调写入接口一律 401；
    即使计量记录是对的，也不调适配器、不落库、不封存。"""
    _, admin = pools
    body = body_for(method, student)
    await reserve(admin, body, method)
    headers = {"authorization": header} if header is not None else {}
    r = await multi_token_api.post(f"/internal/v1/{method}", json=env(body), headers=headers)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert CREDENTIAL not in r.text and ORCH not in r.text
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING
    assert await seals(admin, body["context"]["idempotency_key"]) == 0
    # 对照：同一请求换成 BFF 令牌就能写
    ok = await multi_token_api.post(f"/internal/v1/{method}", json=env(body), headers={"authorization": f"Bearer {CREDENTIAL}"})
    assert ok.status_code == 200, ok.text


# ---------- BFF 正常路径与幂等重放 ----------
@pytest.mark.parametrize("method", ["grade", "diagnose"])
async def test_bff_reserved_write_passes_and_replay_after_settle_writes_nothing(api, pools, student, fake_adapter, method):
    _, admin = pools
    body = body_for(method, student)
    await reserve(admin, body, method)
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "false", r.text
    schema = "GradeSummary" if method == "grade" else "DiagnoseSummary"
    assert internal_contract("1.0.0").validate(schema, r.json()) == []
    after = await counts(admin, student)
    assert fake_adapter.calls == 1 and after["attempt"] == 1
    # BFF 结算之后（预留已是 settled）用同一个键重试：重放在核对之前返回原结果，不再调适配器、不再写
    await meter_row(admin, usage_id=body["context"]["usage_id"], key=body["context"]["idempotency_key"], student=student,
                    method=method, state="settled")
    again = await api.post(f"/internal/v1/{method}", json=env(body))
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true" and again.json() == r.json()
    assert fake_adapter.calls == 1 and await counts(admin, student) == after


# ---------- 计量核对不通过 ----------
@pytest.mark.parametrize("method", ["grade", "diagnose"])
async def test_missing_usage_id_is_refused(api, pools, student, fake_adapter, method):
    """决定第 2 条：没带 usage_id 不能写。插件契约 1.0.0 的消耗类上下文（MeteredContext）本就要求 usage_id，所以先在契约校验这一步
    400 INVALID_REQUEST（确定失败、不可重试），不调适配器、不落库；带了但对不上计量记录的是 409（见下）。"""
    _, admin = pools
    body = body_for(method, student)
    await reserve(admin, body, method)
    del body["context"]["usage_id"]
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST" and r.json()["retryable"] is False
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_usage_id_of_another_key_is_409(api, pools, student, fake_adapter):
    """带了 usage_id，但它属于同一学生的另一笔预留（换键重用 usage_id）：409 IDEMPOTENCY_KEY_SEALED，不可重试。"""
    _, admin = pools
    first, second = body_for("grade", student), body_for("grade", student)
    await reserve(admin, first, "grade")
    second["context"]["usage_id"] = first["context"]["usage_id"]
    r = await api.post("/internal/v1/grade", json=env(second))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED" and r.json()["retryable"] is False
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_malformed_usage_id_is_refused_without_db_error(api, pools, student, fake_adapter):
    """usage_id 不是 UUID 形状：确定拒绝（契约校验 400 或核对 409），不能变成数据库报错的 500（结果未知会让 BFF 反复重试）。"""
    _, admin = pools
    body = body_for("grade", student)
    body["context"]["usage_id"] = "synthetic-not-a-uuid"
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code in (400, 409) and r.json()["retryable"] is False, r.text
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


@pytest.mark.parametrize("method", ["grade", "diagnose"])
async def test_no_meter_record_is_409(api, pools, student, fake_adapter, method):
    """裸调用（没有经 BFF 预留）：409 IDEMPOTENCY_KEY_SEALED，不调适配器、不落库，也不在封存表里留记录。"""
    _, admin = pools
    body = body_for(method, student)
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING
    assert await seals(admin, body["context"]["idempotency_key"]) == 0


@pytest.mark.parametrize("state", ["lapsed", "settled", "released", "expired", "counted"])
async def test_only_reserved_passes_on_direct_path(api, pools, student, fake_adapter, state):
    """决定第 2 条：直接经 HTTP 的写入只放行 reserved；预留过期未处理、已结算（没有执行过的键）、已释放、已过期、
    题库自练计次都拒绝。"""
    _, admin = pools
    body = body_for("grade", student)
    await reserve(admin, body, "grade", state)
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


@pytest.mark.parametrize("mismatch", ["usage_id", "idempotency_key", "student_ref", "method"])
async def test_any_mismatch_is_409(api, pools, student, fake_adapter, mismatch):
    """usage_id、幂等键、学生、方法任一对不上：409（另一名学生的预留、同一学生另一笔的 usage_id、diagnose 的预留拿来 grade）。"""
    _, admin = pools
    body = body_for("grade", student)
    ctx = body["context"]
    if mismatch == "usage_id":
        await meter_row(admin, usage_id=str(uuid.uuid4()), key=ctx["idempotency_key"], student=student)
    elif mismatch == "idempotency_key":
        await meter_row(admin, usage_id=ctx["usage_id"], key=str(uuid.uuid4()), student=student)
    elif mismatch == "student_ref":
        other = await _other_student(admin)
        await meter_row(admin, usage_id=ctx["usage_id"], key=ctx["idempotency_key"], student=other)
    else:
        await meter_row(admin, usage_id=ctx["usage_id"], key=ctx["idempotency_key"], student=student, method="diagnose")
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def _other_student(admin) -> str:
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
        await grant_consents(c, sid, guardian)
    return sid


async def test_rejected_key_can_run_once_metered(api, pools, student, fake_adapter):
    """核对不通过只记这次失败、不封存：同一个键在之后有了预留（例如 BFF 重新预留的同一笔）可以照常执行一次。"""
    _, admin = pools
    body = body_for("grade", student)
    assert (await api.post("/internal/v1/grade", json=env(body))).status_code == 409
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT error_code FROM platform.ingest_request WHERE idempotency_key = $1",
                                body["context"]["idempotency_key"]) == "IDEMPOTENCY_KEY_SEALED"
    await reserve(admin, body, "grade")
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code == 200 and fake_adapter.calls == 1 and await counts(admin, student) == ONE_GRADE


# ---------- v0 写入下线（决定记录（再续）第 1 条） ----------
@pytest.mark.parametrize("method", ["grade", "diagnose"])
@pytest.mark.parametrize("header", [f"Bearer {CREDENTIAL}", "Bearer synthetic-wrong-credential-0000000001", None])
async def test_v0_writes_are_gone(api, pools, student, fake_adapter, method, header):
    """/internal/v0/grade、/internal/v0/diagnose 一律 410（retryable = false：BFF 按“确定没有写入”释放预留）：
    带 BFF 令牌、计量记录也对得上时同样 410；不读请求体、不调适配器、不落库、不留幂等记录，也不封存键。"""
    _, admin = pools
    body = grade_body(student) if method == "grade" else diagnose_body(student, [at("SYN-UNV", "42", 1)])
    await reserve(admin, body, method)
    consumed: list[int] = []

    async def stream():
        consumed.append(1)
        yield json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if header is not None:
        headers["authorization"] = header
    r = await api.post(f"/internal/v0/{method}", content=stream(), headers=headers)
    assert r.status_code == 410, r.text
    p = r.json()
    assert p["code"] == "INVALID_REQUEST" and p["retryable"] is False and r.headers["content-type"] == "application/problem+json"
    assert internal_contract().validate("Problem", p) == [], "v0 契约的问题体形状"
    assert consumed == [], "下线的 v0 写入不读请求体"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING
    key = body["context"]["idempotency_key"]
    assert await seals(admin, key) == 0
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.ingest_request WHERE idempotency_key = $1", key) == 0
    assert CREDENTIAL not in r.text and student not in r.text


# ---------- 测试夹具的防护（安全审查阻断：学习服务角色不能自己造预留；夹具只进一次性测试库） ----------
PROVISION = "learningtest.provision_meter(uuid, text, uuid, text, boolean, boolean)"


async def test_meter_fixture_is_not_granted_to_learning_role(pools):
    """代建函数与 schema 都不授给 starbuddy_learning（只经 conftest 的超级用户池调用）；学习服务的登录角色调用被拒。"""
    app_pool, admin = pools
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT has_schema_privilege('starbuddy_learning', 'learningtest', 'USAGE')") is False
        assert await c.fetchval("SELECT has_function_privilege('starbuddy_learning', $1, 'EXECUTE')", PROVISION) is False
        assert await c.fetchval("SELECT has_function_privilege('learning_app', $1, 'EXECUTE')", PROVISION) is False
        assert await c.fetchval("SELECT has_function_privilege('starbuddy_bff', $1, 'EXECUTE')", PROVISION) is False
    async with app_pool.acquire() as c:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("SELECT learningtest.provision_meter(gen_random_uuid(), 'synthetic-provision-key-0001', gen_random_uuid(), "
                            "'grade', false)")


def test_meter_fixture_refuses_non_test_database(pg):
    """夹具开头的防护：库名不是 starbuddy_dev* / starbuddy_test* 时立即报错（夹具自己开 ON_ERROR_STOP），什么都不建。"""
    from conftest import METER_PROVISION_SQL
    probe = "lt04_fixture_probe"
    pg.psql(f"CREATE DATABASE {probe}")
    try:
        with pytest.raises(subprocess.CalledProcessError) as ei:
            pg._as_pg(f"{pg.pgbin}/psql", "-h", pg.work, "-U", "postgres", "-d", probe, "-X", "-q", "-tA",
                      input=METER_PROVISION_SQL.encode())
        assert "only for disposable" in ei.value.stderr.decode()
        left = pg._as_pg(f"{pg.pgbin}/psql", "-h", pg.work, "-U", "postgres", "-d", probe, "-X", "-q", "-tA", "-c",
                         "SELECT count(*) FROM pg_namespace WHERE nspname = 'learningtest'").stdout.decode().strip()
        assert left == "0", "防护之前什么都没建"
    finally:
        pg.psql(f"DROP DATABASE {probe}")


async def test_v0_read_only_paths_are_kept(api, v0, pools, student, fake_adapter):
    """只读保留：0.9.0 写下的结果（下线前经 v0 写入；这里经进程内写）仍可经 v1 重放接口查回，写入不因下线丢失。"""
    _, admin = pools
    body = grade_body(student)
    body["context"]["idempotency_key"] = str(uuid.uuid4())
    first = await v0("grade", body)
    assert first.status_code == 200
    ctx = body["context"]
    r = await api.get(f"/internal/v1/replay/grade/{ctx['idempotency_key']}?student_ref={ctx['student_ref']}&usage_id={ctx['usage_id']}")
    assert r.status_code == 200 and r.json() == first.json() and r.headers["idempotent-replayed"] == "true"
    assert fake_adapter.calls == 1 and await counts(admin, student) == ONE_GRADE
