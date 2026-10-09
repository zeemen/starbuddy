"""learning-internal v1 写入路径（插件契约 1.0.0；内部 v1 未签字，测试里开 LEARNING_INTERNAL_V1=1）与只查询的重放接口（specs/plugin-api-v1.0-step2/spec.md 验收第 1、2 条）。全部合成数据。"""
from __future__ import annotations

import asyncio
import copy
import uuid

import pytest

from conftest import CREDENTIAL, at, counts, diagnose_body, env, grade_body, to_v1_body
from learning import store

ONE_GRADE = {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1, "error_attribution": 1, "skill_observation": 0}
NOTHING = dict.fromkeys(ONE_GRADE, 0)


@pytest.fixture(autouse=True)
def internal_v1(monkeypatch):
    # learning-internal v1 未签字：只在测试里开 LEARNING_INTERNAL_V1=1。插件契约 1.0.0 已签字（specs/contract-v1.0-signoff），
    # 本模块一律在候选版开关关着时跑，证明 v1 写入不靠候选版开关
    monkeypatch.setenv("LEARNING_INTERNAL_V1", "1")
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)


def replay_url(method: str, body: dict) -> str:
    ctx = body["context"]
    return f"/internal/v1/replay/{method}/{ctx['idempotency_key']}?student_ref={ctx['student_ref']}&usage_id={ctx['usage_id']}"


async def seals(admin, key: str) -> int:
    async with admin.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM platform.replay_seal WHERE idempotency_key = $1", key)


# ---------- 双版本写入 ----------
async def test_same_attempt_writes_via_v0_and_v1(api, pools, student, fake_adapter):
    _, admin = pools
    v0 = grade_body(student)
    v1 = to_v1_body(grade_body(student))
    r0 = await api.post("/internal/v0/grade", json=v0)
    r1 = await api.post("/internal/v1/grade", json=env(v1))
    assert r0.status_code == 200 and r1.status_code == 200, (r0.text, r1.text)
    assert r1.json()["attempt_ids"] == [v1["attempt"]["attempt_id"]], "v1.0 用平台在请求里给的 attempt_id"
    assert await counts(admin, student) == {k: 2 * v for k, v in ONE_GRADE.items()}
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT usage_id::text FROM platform.ingest_request WHERE idempotency_key = $1",
                                v1["context"]["idempotency_key"]) == v1["context"]["usage_id"]


async def test_v1_request_without_attempt_id_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    del body["attempt"]["attempt_id"]
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_v1_evidence_without_attempt_id_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools

    def drop(out):
        for e in out["evidence"]:
            e.pop("attempt_id", None)
        return out
    fake_adapter.mutate = drop
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED"
    assert await counts(admin, student) == NOTHING


async def test_v1_evidence_pointing_at_foreign_attempt_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools

    def foreign(out):
        for e in out["evidence"]:
            if "attempt_id" in e:
                e["attempt_id"] = str(uuid.uuid4())
        return out
    fake_adapter.mutate = foreign
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 502 and await counts(admin, student) == NOTHING


@pytest.mark.parametrize("path,version", [("/internal/v0/grade", "1.0.0"), ("/internal/v1/grade", "0.9.0")])
async def test_declared_version_must_match_endpoint(api, student, fake_adapter, path, version):
    body = to_v1_body(grade_body(student)) if version == "1.0.0" else grade_body(student)
    r = await api.post(path, json=body)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0


@pytest.mark.parametrize("method", ["grade", "diagnose"])
async def test_v1_refused_when_internal_v1_not_enabled(api, pools, student, fake_adapter, monkeypatch, method):
    """learning-internal v1 未签字：没开 LEARNING_INTERNAL_V1=1 时 /internal/v1 写入与签字前同样 400，不调适配器、不落库
    （即使开着候选版开关也一样：插件 1.0.0 签字不连带放开内部 v1）。"""
    _, admin = pools
    monkeypatch.delenv("LEARNING_INTERNAL_V1")
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    body = to_v1_body(grade_body(student) if method == "grade" else diagnose_body(student, [at("SYN-UNV", "42", 1)]))
    r = await api.post(f"/internal/v1/{method}", json=env(body))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_v1_accepted_without_candidate_switch(api, pools, student, fake_adapter, monkeypatch):
    """开着 LEARNING_INTERNAL_V1、关着候选版开关：/internal/v1 照常写入（插件 1.0.0 已签字，不再靠候选版开关）。"""
    _, admin = pools
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 200, r.text
    assert fake_adapter.calls == 1 and await counts(admin, student) == ONE_GRADE


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
async def test_v1_endpoint_refuses_other_declared_versions(api, pools, student, fake_adapter, monkeypatch, version):
    """1.1.0 仍是候选版、2.0.0 不存在：即使开着候选版开关，/internal/v1 只认 1.0.0，不调适配器、不落库。"""
    _, admin = pools
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")
    body = to_v1_body(grade_body(student))
    body["context"]["contract_version"] = version
    r = await api.post("/internal/v1/grade", json=env(body))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_adapter_answering_in_other_version_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.mutate = lambda out: {**out, "contract_version": "0.9.0"}
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 502 and await counts(admin, student) == NOTHING


# ---------- 重放 ----------
async def test_replay_found_returns_summary_and_writes_nothing(api, pools, student, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    first = await api.post("/internal/v1/grade", json=env(body))
    assert first.status_code == 200
    r = await api.get(replay_url("grade", body))
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    assert r.json() == first.json()
    assert fake_adapter.calls == 1 and await counts(admin, student) == ONE_GRADE
    assert await seals(admin, body["context"]["idempotency_key"]) == 0


async def test_replay_not_found_seals_key_and_late_original_is_refused(api, pools, student, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    r = await api.get(replay_url("grade", body))
    assert r.status_code == 404 and r.json()["code"] == "REPLAY_NOT_FOUND"
    assert await seals(admin, body["context"]["idempotency_key"]) == 1
    late = await api.post("/internal/v1/grade", json=env(body))
    assert late.status_code == 409 and late.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING
    again = await api.get(replay_url("grade", body))
    assert again.status_code == 404, "再查一次仍是 404，封存幂等"


async def test_replay_after_failure_seals(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.status = 422
    fake_adapter.problem = {"code": "ITEM_NOT_FOUND", "retryable": False}
    body = to_v1_body(grade_body(student))
    assert (await api.post("/internal/v1/grade", json=env(body))).status_code >= 400
    fake_adapter.status = 200
    assert (await api.get(replay_url("grade", body))).status_code == 404
    assert (await api.post("/internal/v1/grade", json=env(body))).json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert await counts(admin, student) == NOTHING


@pytest.mark.parametrize("field", ["student_ref", "usage_id"])
async def test_replay_mismatch_is_404_without_seal(api, pools, student, fake_adapter, field):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    assert (await api.post("/internal/v1/grade", json=env(body))).status_code == 200
    other = copy.deepcopy(body)
    other["context"][field] = str(uuid.uuid4())
    r = await api.get(replay_url("grade", other))
    assert r.status_code == 404 and r.json()["code"] == "REPLAY_NOT_FOUND"
    assert await seals(admin, body["context"]["idempotency_key"]) == 0
    # 对得上的查询仍然拿到原结果
    assert (await api.get(replay_url("grade", body))).status_code == 200


async def test_replay_unknown_student_does_not_seal(api, pools, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(str(uuid.uuid4())))
    assert (await api.get(replay_url("grade", body))).status_code == 404
    assert await seals(admin, body["context"]["idempotency_key"]) == 0


async def test_replay_while_running_is_busy_and_does_not_seal(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.delay = 0.5
    body = to_v1_body(grade_body(student))
    running = asyncio.create_task(api.post("/internal/v1/grade", json=env(body)))
    for _ in range(50):
        async with admin.acquire() as c:
            if await c.fetchval("SELECT 1 FROM platform.ingest_request WHERE idempotency_key = $1", body["context"]["idempotency_key"]):
                break
        await asyncio.sleep(0.02)
    r = await api.get(replay_url("grade", body))
    assert r.status_code == 409 and r.json()["code"] == "BUSY" and r.json()["retryable"] is True
    assert (await running).status_code == 200
    assert await seals(admin, body["context"]["idempotency_key"]) == 0


async def test_replay_and_original_race_never_both_lose(api, pools, student, fake_adapter):
    """咨询锁串行：要么重放先到（封存，原请求 409、零写入），要么原请求先认领（重放 200 或 BUSY）。"""
    _, admin = pools
    for _ in range(5):
        body = to_v1_body(grade_body(student))
        before = await counts(admin, student)
        post, rep = await asyncio.gather(api.post("/internal/v1/grade", json=env(body)), api.get(replay_url("grade", body)))
        sealed = await seals(admin, body["context"]["idempotency_key"])
        if sealed:
            assert post.status_code == 409 and rep.status_code == 404
            assert await counts(admin, student) == before
        else:
            assert post.status_code == 200 and rep.status_code in (200, 409)


async def test_replay_input_validation_and_auth(api, student):
    body = to_v1_body(grade_body(student))
    bad = [
        replay_url("teach", body),
        replay_url("grade", body).replace(body["context"]["idempotency_key"], "synthetic-readable-key"),
        replay_url("grade", body).replace(f"usage_id={body['context']['usage_id']}", "usage_id=x"),
        f"/internal/v1/replay/grade/{body['context']['idempotency_key']}",
    ]
    for url in bad:
        r = await api.get(url)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", url
    r = await api.get(replay_url("grade", body), headers={"authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert CREDENTIAL not in r.text


# ---------- N18：5xx 一律结果未知 ----------
async def test_internal_error_is_retryable(api, pools, student, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("synthetic commit-phase failure")
    monkeypatch.setattr(store, "_insert_attempt", boom)
    r = await api.post("/internal/v1/grade", json=env(to_v1_body(grade_body(student))))
    assert r.status_code == 500 and r.json() == {**r.json(), "code": "INTERNAL", "retryable": True}


async def test_holder_with_expired_lease_cannot_commit_after_seal(api, pools, student, fake_adapter, monkeypatch):
    """持有者租约过期、仍在调适配器时，重放接口封存了 key：持有者回来后不能落库，原请求得到 409。"""
    _, admin = pools
    body = to_v1_body(grade_body(student))
    key = body["context"]["idempotency_key"]
    gate = asyncio.Event()
    original = store.persist

    async def slow_persist(*a, **kw):
        await gate.wait()
        return await original(*a, **kw)
    monkeypatch.setattr(store, "persist", slow_persist)
    running = asyncio.create_task(api.post("/internal/v1/grade", json=env(body)))
    for _ in range(100):
        if fake_adapter.calls:
            break
        await asyncio.sleep(0.01)
    async with admin.acquire() as c:
        await c.execute("UPDATE platform.ingest_request SET lease_until = now() - interval '1 second' WHERE idempotency_key = $1", key)
    assert (await api.get(replay_url("grade", body))).status_code == 404
    gate.set()
    r = await running
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert await counts(admin, student) == NOTHING


async def _legacy_row(admin, student: str, key: str, status: str, summary: str | None = None) -> None:
    """0016 之前写入的旧行：没有 usage_id。"""
    async with admin.acquire() as c:
        await c.execute(
            """INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids,
                 status, summary, error_code)
               VALUES ('grade', $1, 'aya', $2::uuid, repeat('a', 64), ARRAY[gen_random_uuid()], $3, $4::jsonb,
                       CASE WHEN $3 = 'failed' THEN 'INTERNAL' END)""", key, student, status, summary)


async def test_legacy_row_without_usage_id_is_matched_by_student(api, pools, student):
    """安全审查必须改 2：旧行 usage_id 为空时只核对学生——成功的拿到结果，失败的被封存。"""
    _, admin = pools
    ok = to_v1_body(grade_body(student))
    await _legacy_row(admin, student, ok["context"]["idempotency_key"], "succeeded", '{"verdict": "correct"}')
    r = await api.get(replay_url("grade", ok))
    assert r.status_code == 200 and r.json() == {"verdict": "correct"}
    failed = to_v1_body(grade_body(student))
    await _legacy_row(admin, student, failed["context"]["idempotency_key"], "failed")
    assert (await api.get(replay_url("grade", failed))).status_code == 404
    assert await seals(admin, failed["context"]["idempotency_key"]) == 1
    other = copy.deepcopy(failed)
    other["context"]["student_ref"] = str(uuid.uuid4())
    other["context"]["idempotency_key"] = str(uuid.uuid4())
    await _legacy_row(admin, student, other["context"]["idempotency_key"], "failed")
    assert (await api.get(replay_url("grade", other))).status_code == 404
    assert await seals(admin, other["context"]["idempotency_key"]) == 0, "旧行学生对不上：不封存"


async def test_sealed_key_on_v0_path_uses_v0_error_code(api, pools, student, fake_adapter):
    """安全审查必须改 3：v0 契约没有 IDEMPOTENCY_KEY_SEALED，v0 路径返回 409 IDEMPOTENCY_CONFLICT。"""
    _, admin = pools
    body = grade_body(student)
    body["context"]["idempotency_key"] = str(uuid.uuid4())
    assert (await api.get(replay_url("grade", body))).status_code == 404
    r = await api.post("/internal/v0/grade", json=body)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT" and r.json()["retryable"] is False
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_startup_refuses_without_migration_0016():
    """安全审查必须改 1：封存表不存在时拒绝启动（v0 写入也依赖它）。"""
    from conftest import settings
    from learning.app import create_app

    class NoSealPool:
        async def fetchval(self, q, *a):
            return False
    app = create_app(settings(), pool=NoSealPool())
    with pytest.raises(RuntimeError, match="0016"):
        async with app.router.lifespan_context(app):
            pass


async def test_v1_diagnose_swapped_attempt_ids_are_rejected(api, pools, student, fake_adapter):
    """契约审查建议 1：两道作答的 attempt_id 对调（都属于本次请求）也要拒绝。"""
    from conftest import at, diagnose_body
    _, admin = pools
    body = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 1), at("SYN-UNV2", "41", 2)]))
    ids = [a["attempt_id"] for a in body["attempts"]]

    def swap(out):
        for e in out["evidence"]:
            if "attempt_id" in e:
                e["attempt_id"] = ids[1] if e["attempt_id"] == ids[0] else ids[0]
        return out
    assert (await api.post("/internal/v1/diagnose", json=env(body))).status_code == 200, "对照：不对调时通过"
    body2 = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 3), at("SYN-UNV2", "41", 4)]))
    ids[:] = [a["attempt_id"] for a in body2["attempts"]]
    before = await counts(admin, student)
    fake_adapter.mutate = swap
    r = await api.post("/internal/v1/diagnose", json=env(body2))
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED"
    assert await counts(admin, student) == before


@pytest.mark.parametrize("sqlstate,status,code,retryable", [
    ("23505", 502, "ADAPTER_RESULT_REJECTED", False),
    ("08006", 500, "INTERNAL", True),
    ("57P01", 500, "INTERNAL", True),
    ("40001", 500, "INTERNAL", True),
])
def test_db_error_mapping(sqlstate, status, code, retryable):
    """契约审查建议 2：只有约束类错误是确定失败，其余提交阶段错误是结果未知（N18 b）。"""
    import asyncpg
    e = asyncpg.PostgresError.new({"C": sqlstate, "M": "synthetic", "S": "ERROR"})
    err = store.db_error(e)
    assert (err.status, err.code, err.retryable) == (status, code, retryable)
