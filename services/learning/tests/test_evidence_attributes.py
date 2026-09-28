"""证据补列（specs/evidence-attributes-sprint1/spec.md 验收；迁移 0017）。全部合成数据（规则 8）。"""
from __future__ import annotations

import copy
import uuid

import pytest

import json

from conftest import at, counts, diagnose_body, env, grade_body, to_v1_body
from learning.contract import internal_contract


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


async def test_grade_level_only_in_request(api, student, fake_adapter):
    """契约审查建议 7：grade 的证据等级只写在 request.evidence_level，ingest 里再给一次就拒绝（即使取值相同）。"""
    body = v1_grade(student)
    body["evidence_level"] = "B"
    r = await post(api, body, channel="voice", evidence_level="B")
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
    assert s["error_categories"] == [], "没落库的错因不出现在摘要里（规则 11）"
    assert internal_contract("1.0.0").validate("GradeSummary", s) == []
    c = await counts(admin, student)
    assert (c["evidence_event"], c["attempt"], c["grade_result"]) == (2, 1, 1), "批改照常落库，可展示、可回溯"
    assert (c["error_attribution"], c["skill_observation"]) == (0, 0), "C 级不写错因与能力观察"


async def test_level_c_diagnose_writes_no_observations(api, pools, student):
    _, admin = pools
    body = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 1), at("SYN-UNV2", "41", 2)]))
    r = await post(api, body, "diagnose", channel="canvas", evidence_level="C")
    assert r.status_code == 200, r.text
    assert r.json()["learner_model_updated"] is False and r.json()["observation_count"] == 0, "没落库的观察不报数"
    assert internal_contract("1.0.0").validate("DiagnoseSummary", r.json()) == []
    c = await counts(admin, student)
    assert (c["attempt"], c["skill_observation"]) == (2, 0)


# ---------- 幂等 ----------
async def test_same_key_other_channel_conflicts(api, pools, student, fake_adapter):
    _, admin = pools
    body = v1_grade(student)
    assert (await post(api, body)).status_code == 200
    before = await counts(admin, student)
    body_b = copy.deepcopy(body)
    body_b["evidence_level"] = "B"
    r = await post(api, body_b, channel="voice")
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


# ---------- 安全审查必须改 1–3 与建议 3、4 ----------
@pytest.mark.parametrize("channel,level,ref,ok", [
    ("photo", "A", False, False),        # 拍照最高 B
    ("canvas", "A", False, False),       # 画布记 A 要确认记录
    ("voice", "A", False, False),
    ("canvas", "A", True, True),
    ("voice", "B", False, True),
])
async def test_level_a_needs_confirmed_channel(api, student, fake_adapter, channel, level, ref, ok):
    body = v1_grade(student)
    body["evidence_level"] = level
    if ref:
        body["answer_evidence_ref"] = str(uuid.uuid4())
    r = await post(api, body, channel=channel)
    assert (r.status_code == 200) is ok, r.text
    if not ok:
        assert r.status_code == 400 and fake_adapter.calls == 0


async def test_missing_hint_level_leaves_kind_empty(api, pools, student):
    _, admin = pools
    body = v1_grade(student)
    del body["attempt"]["hint_level_used"]
    assert (await post(api, body, purpose="retest")).status_code == 200
    ev, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] is None and {e["evidence_kind"] for e in ev} == {None}, "不知道有没有用提示：不推成复测"


async def test_hint_signal_forces_hinted(api, pools, student, fake_adapter):
    _, admin = pools

    def add_signal(out):
        out["evidence"].append({"evidence_id": str(uuid.uuid4()), "kind": "interaction_signal", "occurred_at": out["evidence"][0]["occurred_at"],
                                "admission": out["evidence"][0]["admission"], "payload": {"signal_code": "hint_opened", "value": 1}})
        out["evidence_ids"].append(out["evidence"][-1]["evidence_id"])
        return out
    fake_adapter.mutate = add_signal
    r = await post(api, v1_grade(student), purpose="transfer_check")
    assert r.status_code == 200, r.text
    ev, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] == "hinted"
    assert {(e["kind"], e["evidence_kind"]) for e in ev} >= {("response_text", "hinted"), ("interaction_signal", None)}


async def test_level_c_evidence_cannot_be_cited_as_prior(api, pools, student, fake_adapter):
    """必须改 2：C 级证据不能经“既有证据”间接进入学习者模型。"""
    _, admin = pools
    appeal = v1_grade(student)
    appeal["appeal"] = True
    assert (await post(api, appeal)).status_code == 200
    async with admin.acquire() as c:
        c_id = await c.fetchval("""SELECT evidence_id::text FROM platform.evidence_event
                                    WHERE student_id = $1::uuid AND kind = 'verifier_trace'""", student)
        occurred = await c.fetchval("SELECT occurred_at FROM platform.evidence_event WHERE evidence_id = $1::uuid", c_id)
    calls = fake_adapter.calls
    later = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 30)]))
    later["prior_evidence"] = [{"evidence_id": c_id, "kind": "verifier_trace", "occurred_at": occurred.isoformat(),
                                "item_ref": "aya:SYN-UNV", "admission": "source_unverified",
                                "payload": {"sampled_points": 8, "agreeing_points": 8, "reason_codes": []}}]
    r = await post(api, later, "diagnose")
    assert r.status_code == 422, r.text
    assert fake_adapter.calls == calls and (await counts(admin, student))["skill_observation"] == 0


async def test_keypad_with_recognition_evidence_is_rejected(api, pools, student, fake_adapter):
    _, admin = pools

    def to_ocr(out):
        e = out["evidence"][0]
        e["kind"] = "ocr_transcription"
        e["payload"] = {"text": e["payload"]["text"], "confidence": 0.99}
        return out
    fake_adapter.mutate = to_ocr
    r = await post(api, v1_grade(student))
    assert r.status_code == 502 and (await counts(admin, student))["evidence_event"] == 0


async def test_v0_appeal_skips_learner_model(api, pools, student):
    """建议 4：v0 路径的申诉同样不写能力观察与错因（ADR 15“申诉中”）。"""
    _, admin = pools
    body = grade_body(student)
    body["appeal"] = True
    assert (await api.post("/internal/v0/grade", json=body)).status_code == 200
    c = await counts(admin, student)
    assert (c["grade_result"], c["error_attribution"]) == (1, 0)


async def test_appeal_on_photo_needs_no_level(api, student):
    """契约审查建议 8：申诉先于“非键盘必须给等级”，结果一律 C。"""
    body = v1_grade(student)
    body["appeal"] = True
    r = await post(api, body, channel="photo")
    assert r.status_code == 200 and r.json()["evidence_level"] == "C"


async def test_same_evidence_with_other_attributes_is_rejected(api, pools, student, fake_adapter):
    """契约审查建议 2：同一 evidence_id、同样内容，但通道或等级不同，不能静默沿用旧属性。"""
    _, admin = pools
    fixed: dict[str, str] = {}

    def pin_ids(out):
        text = json.dumps(out)
        for e in out["evidence"]:
            fixed.setdefault(e["kind"], str(uuid.uuid5(uuid.NAMESPACE_URL, f"{student}:{e['kind']}")))
            text = text.replace(e["evidence_id"], fixed[e["kind"]])
        return json.loads(text)
    fake_adapter.mutate = pin_ids
    first = v1_grade(student)
    assert (await post(api, first)).status_code == 200
    second = v1_grade(student)  # 新 key、同样作答内容与时间
    second["evidence_level"] = "B"
    r = await post(api, second, channel="voice")
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED"
    assert (await counts(admin, student))["attempt"] == 1


async def test_v1_summaries_match_contract(api, student):
    r = await post(api, v1_grade(student))
    assert internal_contract("1.0.0").validate("GradeSummary", r.json()) == []
