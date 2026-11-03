"""证据补列（specs/evidence-attributes-sprint1/spec.md 验收；迁移 0017）。全部合成数据（规则 8）。

LT-04 决定记录（续）第 3 条起，用途与证据等级由学习服务推导、不接受调用方自报：直接经 HTTP 的写入只能是 practice、键盘作答（记 A，
申诉记 C）；自报等级、非键盘通道（平台还没有确认记录）一律 400。复测、迁移检验的用途只由进程内调用方（复测按票据）写入，
这里用进程内的 IngestService（trusted 依据，计量按自练计次由 conftest 代建）测它们的作答情境。
"""
from __future__ import annotations

import copy
import json
import uuid

import httpx
import pytest

from conftest import at, counts, diagnose_body, env, grade_body, settings, to_v1_body
from learning import meter as M
from learning.attributes import derive
from learning.contract import internal_contract
from learning.errors import IngestError
from learning.service import IngestService


@pytest.fixture(autouse=True)
def no_candidate_switch(monkeypatch):
    # 插件契约 1.0.0、learning-internal v1 都已签字：候选版开关关着
    monkeypatch.delenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", raising=False)


@pytest.fixture
async def trusted(pools, fake_adapter):
    """进程内的写入服务：模拟复测等按票据写用途的调用方（meter.COUNTED，trusted_purpose）。"""
    app_pool, _ = pools
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_adapter.handler), trust_env=False) as client:
        svc = IngestService(settings(), app_pool, client)

        async def post(body: dict, method: str = "grade", **ingest) -> dict:
            summary, _ = await svc.ingest(method, env(body, **ingest), "1.0.0", meter=M.COUNTED)
            return summary
        yield post


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


async def test_hint_used_is_hinted_even_on_retest(trusted, pools, student):
    _, admin = pools
    await trusted(v1_grade(student, hint_level_used=2), purpose="retest")
    ev, attempts = await rows(admin, student)
    assert {e["evidence_kind"] for e in ev} == {"hinted"} and attempts[0]["evidence_kind"] == "hinted"


@pytest.mark.parametrize("purpose,kind", [("practice", "independent"), ("retest", "delayed_retest"), ("transfer_check", "transfer")])
async def test_trusted_purpose_sets_evidence_kind(trusted, pools, student, purpose, kind):
    """进程内调用方（复测按票据）写的用途决定作答情境。"""
    _, admin = pools
    await trusted(v1_grade(student), purpose=purpose)
    _, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] == kind


@pytest.mark.parametrize("purpose", ["retest", "transfer_check"])
async def test_direct_write_cannot_self_report_purpose(api, pools, student, fake_adapter, purpose):
    """决定第 3 条：直接经 HTTP 的写入用途只能是 practice，自报其他值 400，不调适配器、不落库。"""
    _, admin = pools
    r = await post(api, v1_grade(student), purpose=purpose)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and (await rows(admin, student)) == ([], [])


async def test_direct_write_explicit_practice_is_accepted(api, pools, student):
    _, admin = pools
    assert (await post(api, v1_grade(student), purpose="practice")).status_code == 200
    _, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] == "independent"


@pytest.mark.parametrize("where,level", [("request", "A"), ("request", "B"), ("request", "C")])
async def test_grade_level_cannot_be_declared(api, pools, student, fake_adapter, where, level):
    """决定第 3 条：证据等级由学习服务推导，请求里自报（哪怕与推导结果相同）一律 400。"""
    _, admin = pools
    body = v1_grade(student)
    body["evidence_level"] = level
    r = await post(api, body)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and (await rows(admin, student)) == ([], [])


@pytest.mark.parametrize("level", ["A", "B", "C"])
async def test_diagnose_level_cannot_be_declared(api, pools, student, fake_adapter, level):
    _, admin = pools
    body = to_v1_body(diagnose_body(student, [at("SYN-UNV", "42", 1)]))
    r = await post(api, body, "diagnose", evidence_level=level)
    assert r.status_code == 400 and fake_adapter.calls == 0 and (await rows(admin, student)) == ([], [])


@pytest.mark.parametrize("channel,level,ref", [
    ("photo", None, False), ("photo", "B", False), ("canvas", "A", True), ("canvas", None, True),
    ("voice", "B", False), ("voice", None, False),
])
async def test_non_keypad_channel_needs_platform_confirmation(api, pools, student, fake_adapter, channel, level, ref):
    """决定第 3 条：非键盘通道须有平台侧确认记录才能定级；平台还没有这类记录（ADR 15 输入通道服务），调用方给的等级与
    answer_evidence_ref 都不能代替，一律 400、不调适配器。"""
    _, admin = pools
    body = v1_grade(student)
    if level:
        body["evidence_level"] = level
    if ref:
        body["answer_evidence_ref"] = str(uuid.uuid4())
    r = await post(api, body, channel=channel)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
    assert fake_adapter.calls == 0 and (await rows(admin, student)) == ([], [])


def test_derive_rules_are_pure_and_conservative():
    """推导函数本身：键盘 A、申诉 C；非 practice 用途只认可信调用方；非键盘、自报等级都拒绝（进程内调用方也一样）。"""
    req = {"context": {}, "attempt": {}}
    assert derive("grade", req, {"channel": "keypad"}).evidence_level == "A"
    assert derive("grade", {**req, "appeal": True}, {"channel": "keypad"}).evidence_level == "C"
    assert derive("grade", req, {"channel": "keypad", "purpose": "retest"}, trusted_purpose=True).purpose == "retest"
    for bad in (dict(ingest={"channel": "keypad", "purpose": "retest"}),
                dict(ingest={"channel": "photo"}, trusted_purpose=True),
                dict(ingest={"channel": "keypad", "evidence_level": "A"}, trusted_purpose=True)):
        with pytest.raises(IngestError) as e:
            derive("diagnose", req, bad["ingest"], trusted_purpose=bad.get("trusted_purpose", False))
        assert e.value.status == 400
    with pytest.raises(IngestError):
        derive("grade", {**req, "evidence_level": "B"}, {"channel": "keypad"}, trusted_purpose=True)


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


# ---------- 幂等 ----------
async def test_same_key_other_channel_is_rejected_and_practice_alias_replays(api, pools, student, fake_adapter):
    _, admin = pools
    body = v1_grade(student)
    assert (await post(api, body)).status_code == 200
    before = await counts(admin, student)
    r = await post(api, copy.deepcopy(body), channel="voice")
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", "换通道在推导这一步就拒绝，不进幂等"
    again = await post(api, copy.deepcopy(body), purpose="practice")  # 缺省用途与显式 practice 视为同一请求
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true"
    appeal = copy.deepcopy(body)
    appeal["appeal"] = True
    conflict = await post(api, appeal)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
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


async def test_v0_path_writes_kind_only(v0, pools, student):
    """插件契约 0.9.0 的写入（HTTP 已下线、只剩进程内，LT-04 决定记录（再续）第 1 条）只写证据种类、不写等级与通道。"""
    _, admin = pools
    body = grade_body(student)
    body["attempt"]["hint_level_used"] = 1
    r = await v0("grade", body)
    assert r.status_code == 200 and "evidence_level" not in r.json()
    ev, attempts = await rows(admin, student)
    assert {(e["evidence_kind"], e["evidence_level"], e["channel"]) for e in ev} == {("hinted", None, None)}
    assert attempts[0]["evidence_kind"] == "hinted"


# ---------- 安全审查必须改 2、3 与建议 3、4 ----------
async def test_missing_hint_level_leaves_kind_empty(trusted, pools, student):
    _, admin = pools
    body = v1_grade(student)
    del body["attempt"]["hint_level_used"]
    await trusted(body, purpose="retest")
    ev, attempts = await rows(admin, student)
    assert attempts[0]["evidence_kind"] is None and {e["evidence_kind"] for e in ev} == {None}, "不知道有没有用提示：不推成复测"


async def test_hint_signal_forces_hinted(trusted, pools, student, fake_adapter):
    _, admin = pools

    def add_signal(out):
        out["evidence"].append({"evidence_id": str(uuid.uuid4()), "kind": "interaction_signal", "occurred_at": out["evidence"][0]["occurred_at"],
                                "admission": out["evidence"][0]["admission"], "payload": {"signal_code": "hint_opened", "value": 1}})
        out["evidence_ids"].append(out["evidence"][-1]["evidence_id"])
        return out
    fake_adapter.mutate = add_signal
    await trusted(v1_grade(student), purpose="transfer_check")
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


async def test_v0_appeal_skips_learner_model(v0, pools, student):
    """建议 4：v0（0.9.0，进程内）的申诉同样不写能力观察与错因（ADR 15“申诉中”）。"""
    _, admin = pools
    body = grade_body(student)
    body["appeal"] = True
    assert (await v0("grade", body)).status_code == 200
    c = await counts(admin, student)
    assert (c["grade_result"], c["error_attribution"]) == (1, 0)


async def test_appeal_on_photo_is_still_rejected(api, student, fake_adapter):
    """申诉不绕过通道规则：非键盘通道没有平台确认记录，申诉也 400（以前记 C 放行）。"""
    body = v1_grade(student)
    body["appeal"] = True
    r = await post(api, body, channel="photo")
    assert r.status_code == 400 and fake_adapter.calls == 0


async def test_same_evidence_with_other_attributes_is_rejected(api, pools, student, fake_adapter):
    """契约审查建议 2：同一 evidence_id、同样内容，但等级不同（这里是申诉的 C 对照键盘的 A），不能静默沿用旧属性。"""
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
    second["appeal"] = True
    r = await post(api, second)
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED"
    assert (await counts(admin, student))["attempt"] == 1


async def test_v1_summaries_match_contract(api, student):
    r = await post(api, v1_grade(student))
    assert internal_contract("1.0.0").validate("GradeSummary", r.json()) == []
