"""证据补列（specs/evidence-attributes-sprint1/spec.md 验收；迁移 0017）。全部合成数据（规则 8）。"""
from __future__ import annotations

import copy
import uuid

import pytest

from conftest import at, counts, diagnose_body, env, grade_body, to_v1_body


@pytest.fixture(autouse=True)
def allow_candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


async def rows(admin, student: str) -> tuple[list[dict], list[dict]]:
    async with admin.acquire() as c:
        ev = [dict(r) for r in await c.fetch(
            """SELECT kind, evidence_kind, evidence_level, channel, map_version_id::text, source_type
                 FROM platform.evidence_event WHERE student_id = $1::uuid ORDER BY kind""", student)]
        at_ = [dict(r) for r in await c.fetch(
            """SELECT evidence_kind, evidence_level, channel, map_version_id::text, source_type, hint_level_used
                 FROM platform.attempt WHERE student_id = $1::uuid ORDER BY occurred_at""", student)]
    return ev, at_


def v1_grade(student: str, **attempt) -> dict:
    b = to_v1_body(grade_body(student))
    b["attempt"].update(attempt)
    return b


async def post(api, body: dict, method: str = "grade", **ingest):
    return await api.post(f"/internal/v1/{method}", json=env(body, **ingest))


# ---------- 推导规则 ----------
async def test_keypad_defaults_to_level_a_independent(api, pools, student):
    _, admin = pools
    r = await post(api, v1_grade(student))
    assert r.status_code == 200, r.text
    assert r.json()["evidence_level"] == "A" and r.json()["learner_model_updated"] is True
    ev, attempts = await rows(admin, student)
    assert {(e["kind"], e["evidence_kind"], e["evidence_level"], e["channel"], e["source_type"]) for e in ev} == {
        ("response_text", "independent", "A", "keypad", "objective"),
        ("verifier_trace", "independent", "A", "keypad", "objective")}
    assert attempts == [{"evidence_kind": "independent", "evidence_level": "A", "channel": "keypad", "map_version_id": None,
                         "source_type": "objective", "hint_level_used": 0}]


async def test_hint_used_is_hinted_even_on_retest(api, pools, student):
    _, admin = pools
    r = await post(api, v1_grade(student, hint_level_used=2), purpose="retest")
    assert r.status_code == 200, r.text
    ev, attempts = await rows(admin, student)
    assert {e["evidence_kind"] for e in ev} == {"hinted"} and attempts[0]["evidence_kind"] == "hinted"


@pytest.mark.parametrize("purpose,kind", [("practice", "independent"), ("retest", "delayed_retest"), ("transfer_check", "transfer")])
async def test_purpose_sets_evidence_kind(api, pools, student, purpose, kind):
    _, admin = pools
    assert (await post(api, v1_grade(student), purpose=purpose)).status_code == 200
    _, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] == kind


async def test_photo_without_level_is_rejected_before_adapter(api, pools, student, fake_adapter):
    _, admin = pools
    r = await post(api, v1_grade(student), channel="photo")
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and (await rows(admin, student)) == ([], [])


async def test_photo_with_level_b(api, pools, student):
    _, admin = pools
    body = v1_grade(student)
    body["evidence_level"] = "B"
    assert (await post(api, body, channel="photo")).json()["evidence_level"] == "B"
    _, attempts = await rows(admin, student)
    assert (attempts[0]["channel"], attempts[0]["evidence_level"]) == ("photo", "B")


async def test_conflicting_levels_are_rejected(api, student, fake_adapter):
    body = v1_grade(student)
    body["evidence_level"] = "A"
    r = await post(api, body, channel="canvas", evidence_level="B")
    assert r.status_code == 400 and fake_adapter.calls == 0


# ---------- C 级 ----------
async def test_appeal_is_level_c_and_skips_learner_model(api, pools, student):
    _, admin = pools
    body = v1_grade(student)  # 答案 41：适配器给出错因
    body["appeal"] = True
    r = await post(api, body)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["evidence_level"] == "C" and s["learner_model_updated"] is False
    c = await counts(admin, student)
    assert (c["evidence_event"], c["attempt"], c["grade_result"]) == (2, 1, 1), "批改照常落库，可展示、可回溯"
    assert (c["error_attribution"], c["skill_observation"]) == (0, 0), "C 级不写错因与能力观察"


async def test_level_c_diagnose_writes_no_observations(api, pools, student):
    _, admin = pools
    body = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 1), at("SYN-UNV2", "41", 2)]))
    r = await post(api, body, "diagnose", channel="canvas", evidence_level="C")
    assert r.status_code == 200, r.text
    assert r.json()["learner_model_updated"] is False and r.json()["observation_count"] == 2
    c = await counts(admin, student)
    assert (c["attempt"], c["skill_observation"]) == (2, 0)


# ---------- 幂等 ----------
async def test_same_key_other_channel_conflicts(api, pools, student, fake_adapter):
    _, admin = pools
    body = v1_grade(student)
    body["evidence_level"] = "A"
    assert (await post(api, body)).status_code == 200
    before = await counts(admin, student)
    r = await post(api, body, channel="canvas")
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"
    again = await post(api, copy.deepcopy(body), purpose="practice")  # 缺省用途与显式 practice 视为同一请求
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true"
    assert await counts(admin, student) == before and fake_adapter.calls == 1


# ---------- 地图版本 ----------
async def test_map_version_follows_content_version(api, pools, student):
    _, admin = pools
    map_id = str(uuid.uuid4())
    body = v1_grade(student)
    cv = body["context"]["content_version"]
    cv["content_version"] = f"0.{uuid.uuid4().int % 900 + 100}.7"  # 本测试专用的内容版本，不影响其他测试
    async with admin.acquire() as c:
        await c.execute("INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ($1::uuid, 'aya', 2022, $2)",
                        map_id, f"7.{uuid.uuid4().int % 1000}.0")
        await c.execute(
            """INSERT INTO platform.curriculum_version (region, exam_year, grade, textbook_version, subject, content_version, map_version_id)
               VALUES ($1, $2, $3, $4, 'aya', $5, $6::uuid)""",
            cv["region"], cv["exam_year"], cv["grade"], cv["textbook_version"], cv["content_version"], map_id)
    assert (await post(api, body)).status_code == 200
    ev, attempts = await rows(admin, student)
    assert {e["map_version_id"] for e in ev} == {map_id} and attempts[0]["map_version_id"] == map_id


# ---------- 信封与 v0 ----------
@pytest.mark.parametrize("mangle", [
    lambda b: b.pop("ingest"),
    lambda b: b["ingest"].pop("channel"),
    lambda b: b["ingest"].__setitem__("evidence_kind", "transfer"),
    lambda b: b.__setitem__("extra", 1),
])
async def test_bad_envelope_is_rejected(api, student, fake_adapter, mangle):
    b = env(v1_grade(student))
    mangle(b)
    r = await api.post("/internal/v1/grade", json=b)
    assert r.status_code == 400 and fake_adapter.calls == 0


async def test_v0_path_writes_kind_only(api, pools, student):
    _, admin = pools
    body = grade_body(student)
    body["attempt"]["hint_level_used"] = 1
    r = await api.post("/internal/v0/grade", json=body)
    assert r.status_code == 200 and "evidence_level" not in r.json()
    ev, attempts = await rows(admin, student)
    assert {(e["evidence_kind"], e["evidence_level"], e["channel"]) for e in ev} == {("hinted", None, None)}
    assert attempts[0]["evidence_kind"] == "hinted"
