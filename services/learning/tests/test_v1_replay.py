"""插件契约 1.0.0 候选版路径与只查询的重放接口（specs/plugin-api-v1.0-step2/spec.md 验收第 1、2 条）。全部合成数据。"""
from __future__ import annotations

import asyncio
import copy
import uuid

import pytest

from conftest import CREDENTIAL, counts, grade_body, to_v1_body
from learning import store

ONE_GRADE = {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1, "error_attribution": 1, "skill_observation": 0}
NOTHING = dict.fromkeys(ONE_GRADE, 0)


@pytest.fixture(autouse=True)
def allow_candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


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
    r1 = await api.post("/internal/v1/grade", json=v1)
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
    r = await api.post("/internal/v1/grade", json=body)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING


async def test_v1_evidence_without_attempt_id_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools

    def drop(out):
        for e in out["evidence"]:
            e.pop("attempt_id", None)
        return out
    fake_adapter.mutate = drop
    r = await api.post("/internal/v1/grade", json=to_v1_body(grade_body(student)))
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
    r = await api.post("/internal/v1/grade", json=to_v1_body(grade_body(student)))
    assert r.status_code == 502 and await counts(admin, student) == NOTHING


@pytest.mark.parametrize("path,version", [("/internal/v0/grade", "1.0.0"), ("/internal/v1/grade", "0.9.0")])
async def test_declared_version_must_match_endpoint(api, student, fake_adapter, path, version):
    body = to_v1_body(grade_body(student)) if version == "1.0.0" else grade_body(student)
    r = await api.post(path, json=body)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0


async def test_v1_refused_when_candidate_not_enabled(api, student, fake_adapter, monkeypatch):
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT")
    r = await api.post("/internal/v1/grade", json=to_v1_body(grade_body(student)))
    assert r.status_code == 400 and fake_adapter.calls == 0


async def test_adapter_answering_in_other_version_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.mutate = lambda out: {**out, "contract_version": "0.9.0"}
    r = await api.post("/internal/v1/grade", json=to_v1_body(grade_body(student)))
    assert r.status_code == 502 and await counts(admin, student) == NOTHING


# ---------- 重放 ----------
async def test_replay_found_returns_summary_and_writes_nothing(api, pools, student, fake_adapter):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    first = await api.post("/internal/v1/grade", json=body)
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
    late = await api.post("/internal/v1/grade", json=body)
    assert late.status_code == 409 and late.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert fake_adapter.calls == 0 and await counts(admin, student) == NOTHING
    again = await api.get(replay_url("grade", body))
    assert again.status_code == 404, "再查一次仍是 404，封存幂等"


async def test_replay_after_failure_seals(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.status = 422
    fake_adapter.problem = {"code": "ITEM_NOT_FOUND", "retryable": False}
    body = to_v1_body(grade_body(student))
    assert (await api.post("/internal/v1/grade", json=body)).status_code >= 400
    fake_adapter.status = 200
    assert (await api.get(replay_url("grade", body))).status_code == 404
    assert (await api.post("/internal/v1/grade", json=body)).json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert await counts(admin, student) == NOTHING


@pytest.mark.parametrize("field", ["student_ref", "usage_id"])
async def test_replay_mismatch_is_404_without_seal(api, pools, student, fake_adapter, field):
    _, admin = pools
    body = to_v1_body(grade_body(student))
    assert (await api.post("/internal/v1/grade", json=body)).status_code == 200
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
    running = asyncio.create_task(api.post("/internal/v1/grade", json=body))
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
        post, rep = await asyncio.gather(api.post("/internal/v1/grade", json=body), api.get(replay_url("grade", body)))
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
    r = await api.post("/internal/v1/grade", json=to_v1_body(grade_body(student)))
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
    running = asyncio.create_task(api.post("/internal/v1/grade", json=body))
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
