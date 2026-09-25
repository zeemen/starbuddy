"""写入接口：落库映射、幂等（冲刺 1-C 验收“重复事件不重复入库”）、核对规则、错误处理。"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import pytest

from conftest import at, counts, diagnose_body, grade_body
from learning import store
from learning.contract import internal_contract
from learning.fingerprint import request_fingerprint

ONE_GRADE = {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1, "error_attribution": 1, "skill_observation": 0}
NOTHING = dict.fromkeys(ONE_GRADE, 0)


async def post(api, method, body):
    return await api.post(f"/internal/v0/{method}", json=body)


# ---------- 落库 ----------
async def test_grade_writes_all_tables_with_matching_ids(api, pools, student, fake_adapter):
    _, admin = pools
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 200, r.text
    s = r.json()
    assert internal_contract().validate("GradeSummary", s) == []
    assert r.headers["idempotent-replayed"] == "false"
    assert await counts(admin, student) == ONE_GRADE
    async with admin.acquire() as c:
        ev = {str(x) for x in await c.fetchval(
            "SELECT array_agg(evidence_id) FROM platform.evidence_event WHERE student_id = $1::uuid", student)}
        assert ev == set(s["evidence_ids"])
        g = await c.fetchrow("SELECT * FROM platform.grade_result WHERE id = $1::uuid", s["grade_result_id"])
        assert {str(x) for x in g["evidence_ids"]} == ev
        assert str(g["attempt_id"]) == s["attempt_ids"][0]
        assert g["semantics"] == "practice" and g["human_review_required"]
        a = await c.fetchrow("SELECT * FROM platform.attempt WHERE student_id = $1::uuid", student)
        resp_ev = await c.fetchval("SELECT evidence_id FROM platform.evidence_event WHERE student_id = $1::uuid AND kind = 'response_text'", student)
        assert a["response_evidence_id"] == resp_ev and a["elapsed_sec"] == 60
        attr = await c.fetchrow("SELECT * FROM platform.error_attribution WHERE student_id = $1::uuid", student)
        assert attr["cross_subject_category"] == "computation_or_grammar" and attr["evidence_ids"] == [resp_ev]
    assert s["error_categories"] == ["computation_or_grammar"]


async def test_diagnose_matches_attempts_by_item_and_time_not_order(api, pools, student):
    _, admin = pools
    body = diagnose_body(student, [at("SYN-UNV", "42", 1), at("SYN-UNV2", "41", 2), at("SYN-UNV", "40", 3)])
    r = await post(api, "diagnose", body)
    assert r.status_code == 200, r.text
    s = r.json()
    assert internal_contract().validate("DiagnoseSummary", s) == []
    assert s["observation_count"] == 3
    async with admin.acquire() as c:
        rows = await c.fetch(
            """SELECT a.item_id, a.occurred_at, e.item_id AS ev_item, e.occurred_at AS ev_time, e.payload->>'text' AS text
                 FROM platform.attempt a JOIN platform.evidence_event e
                   ON e.evidence_id = a.response_evidence_id AND e.occurred_at = a.occurred_at
                WHERE a.student_id = $1::uuid ORDER BY a.occurred_at""", student)
        assert [(r["item_id"], r["text"]) for r in rows] == [("SYN-UNV", "42"), ("SYN-UNV2", "41"), ("SYN-UNV", "40")]
        assert all(r["item_id"] == r["ev_item"] and r["occurred_at"] == r["ev_time"] for r in rows)
        obs = await c.fetch("SELECT skill_local_id, outcome FROM platform.skill_observation WHERE student_id = $1::uuid ORDER BY skill_local_id, outcome", student)
        assert [tuple(o) for o in obs] == [("K-SYN-0001", "demonstrated"), ("K-SYN-0001", "not_demonstrated"), ("K-SYN-0002", "not_demonstrated")]


async def test_diagnose_rejects_indistinguishable_attempts(api, pools, student, fake_adapter):
    r = await post(api, "diagnose", diagnose_body(student, [at("SYN-UNV", "1", 1), at("SYN-UNV", "2", 1)]))
    assert r.status_code == 422
    assert fake_adapter.calls == 0


# ---------- 幂等 ----------
async def test_sequential_retry_replays_without_new_rows(api, pools, student, fake_adapter):
    _, admin = pools
    body = grade_body(student)
    first = (await post(api, "grade", body)).json()
    retry = copy.deepcopy(body)
    retry["context"]["request_id"] = str(uuid.uuid4())  # 重试时 request_id 可以变
    r = await post(api, "grade", retry)
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    assert r.json() == first
    assert fake_adapter.calls == 1
    assert await counts(admin, student) == ONE_GRADE


async def test_same_key_different_body_conflicts_and_writes_nothing(api, pools, student):
    _, admin = pools
    body = grade_body(student)
    assert (await post(api, "grade", body)).status_code == 200
    changed = copy.deepcopy(body)
    changed["attempt"]["response"]["text"] = "43"
    r = await post(api, "grade", changed)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert await counts(admin, student) == ONE_GRADE


async def test_twenty_concurrent_requests_execute_once(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.delay = 0.2
    body = grade_body(student)
    rs = await asyncio.gather(*(post(api, "grade", body) for _ in range(20)))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs if r.status_code != 200]
    assert all(r.json() == rs[0].json() for r in rs)
    assert fake_adapter.calls == 1
    assert sum(r.headers["idempotent-replayed"] == "false" for r in rs) == 1
    assert await counts(admin, student) == ONE_GRADE


async def test_failure_mid_transaction_rolls_back_then_retry_succeeds(api, pools, student, monkeypatch):
    _, admin = pools
    original = store._insert_attempt
    boom = {"n": 0}

    async def failing(*a, **kw):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("synthetic failure after evidence insert")
        return await original(*a, **kw)

    monkeypatch.setattr(store, "_insert_attempt", failing)
    body = grade_body(student)
    r = await post(api, "grade", body)
    assert r.status_code == 500
    assert await counts(admin, student) == NOTHING, "事务中途失败：一行都不能留下"
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT status FROM platform.ingest_request WHERE idempotency_key = $1",
                                body["context"]["idempotency_key"]) == "failed"
    r2 = await post(api, "grade", body)
    assert r2.status_code == 200 and r2.headers["idempotent-replayed"] == "false"
    assert await counts(admin, student) == ONE_GRADE


async def test_expired_lease_is_taken_over_and_attempt_id_reused(api, pools, student, fake_adapter):
    _, admin = pools
    body = grade_body(student)
    stale_attempt = str(uuid.uuid4())
    async with admin.acquire() as c:  # 模拟一个中途崩溃的持有者：pending、租约已过期
        await c.execute(
            """INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint,
                 attempt_ids, lease_token, lease_until)
               VALUES ('grade', $1, 'aya', $2::uuid, $3, ARRAY[$4::uuid], gen_random_uuid(), now() - interval '1 second')""",
            body["context"]["idempotency_key"], student, request_fingerprint("grade", body), stale_attempt,
        )
    r = await post(api, "grade", body)
    assert r.status_code == 200, r.text
    assert r.json()["attempt_ids"] == [stale_attempt]
    assert await counts(admin, student) == ONE_GRADE


async def test_live_lease_makes_second_request_wait_for_the_result(api, pools, student, fake_adapter):
    fake_adapter.delay = 0.5
    body = grade_body(student)
    first = asyncio.create_task(post(api, "grade", body))
    await asyncio.sleep(0.1)
    second = await post(api, "grade", body)
    assert second.status_code == 200 and second.headers["idempotent-replayed"] == "true"
    assert (await first).json() == second.json()
    assert fake_adapter.calls == 1


async def test_evidence_id_reuse_same_content_skipped_different_content_rejected(api, pools, student, fake_adapter):
    _, admin = pools
    fixed = str(uuid.uuid4())

    def pin_trace(out):  # 两次不同请求返回同一个 verifier_trace 证据 ID
        old = out["evidence"][1]["evidence_id"]
        out["evidence"][1]["evidence_id"] = fixed
        out["evidence_ids"] = [fixed if i == old else i for i in out["evidence_ids"]]
        out["result"]["dimensions"][0]["evidence_ids"] = [fixed if i == old else i for i in out["result"]["dimensions"][0]["evidence_ids"]]
        out["evidence"][1]["occurred_at"] = "2026-10-08T11:30:00+00:00"
        return out

    fake_adapter.mutate = pin_trace
    b1 = grade_body(student)
    assert (await post(api, "grade", b1)).status_code == 200
    b2 = grade_body(student)  # 另一个 key，作答时间相同，trace 内容完全相同
    assert (await post(api, "grade", b2)).status_code == 200
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.evidence_event WHERE evidence_id = $1::uuid", fixed) == 1
    before = await counts(admin, student)

    def pin_trace_changed(out):
        out = pin_trace(out)
        out["evidence"][1]["payload"]["agreeing_points"] = 7
        return out

    fake_adapter.mutate = pin_trace_changed
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED"
    assert await counts(admin, student) == before


# ---------- 平台核对：被拒绝时一行都不写 ----------
def _tamper(kind):
    def f(out):
        if kind == "extra_field":
            out["result"]["comment"] = "free text"
        elif kind == "dangling_id":
            out["result"]["dimensions"][0]["evidence_ids"] = [str(uuid.uuid4())]
        elif kind == "other_subject":
            out["evidence"][1]["skill_node"] = "wenli:R3-L2"
        elif kind == "unregistered_item":
            out["evidence"][1]["item_ref"] = "aya:NOT-REGISTERED"
        elif kind == "unregistered_node":
            out["result"]["error_attributions"][0]["skill_node"] = "aya:K-NOT-REGISTERED"
        elif kind == "looser_semantics":
            out["semantics"] = "formal"
        elif kind == "no_response_evidence":
            out["evidence"][0]["occurred_at"] = "2026-10-09T00:00:00+00:00"
        elif kind == "media_ref_injected":  # 安全审查 B2：指向别的学生的照片
            out["evidence"][1]["media_ref"] = "objstore://students/other/paper-p1.jpg"
        elif kind == "rubric_mismatch":
            out["result"]["rubric_version"] = "aya-symbolic-ts@0.0.9"
        elif kind == "fabricated_text":  # 安全审查 S3：把讲解或答案当成学生原文
            out["evidence"][0]["payload"]["text"] = "正确答案是 42"
        elif kind == "extra_response_evidence":
            extra = dict(out["evidence"][0], evidence_id=str(uuid.uuid4()), occurred_at="2026-10-01T00:00:00+00:00")
            out["evidence"].append(extra)
        elif kind == "l3_in_l2_request":
            out["evidence"][1]["kind"] = "asr_transcription"
            out["evidence"][1]["payload"] = {"text": "synthetic"}
        elif kind == "bad_time":
            out["evidence"][1]["occurred_at"] = "epoch"
        elif kind == "nan":
            out["result"]["score"]["max_total"] = float("nan")
        elif kind == "db_constraint":
            out["result"]["score"]["total"] = 5  # 总分超过满分：契约交给平台侧校验，数据库约束拦下
        return out
    return f


@pytest.mark.parametrize("kind", ["extra_field", "dangling_id", "other_subject", "unregistered_item", "unregistered_node",
                                  "looser_semantics", "no_response_evidence", "db_constraint", "media_ref_injected",
                                  "rubric_mismatch", "fabricated_text", "extra_response_evidence", "l3_in_l2_request",
                                  "bad_time", "nan"])
async def test_rejected_results_write_nothing(api, pools, student, fake_adapter, kind):
    _, admin = pools
    fake_adapter.mutate = _tamper(kind)
    body = grade_body(student, text="學生作答-SYNTHETIC-9f2c")
    r = await post(api, "grade", body)
    assert r.status_code == 502, r.text
    p = r.json()
    assert p["code"] == "ADAPTER_RESULT_REJECTED"
    assert internal_contract().validate("Problem", p) == []
    assert "SYNTHETIC-9f2c" not in r.text
    assert await counts(admin, student) == NOTHING


async def test_admission_is_rewritten_to_platform_value(api, pools, student, fake_adapter):
    """适配器声称已准入（更松），平台登记是 source_unverified：落库用平台的，并改为 practice + 复核。"""
    _, admin = pools
    fake_adapter.admission = "admitted"
    r = await post(api, "grade", grade_body(student, item="SYN-UNV"))
    assert r.status_code == 200, r.text
    assert r.json()["semantics"] == "practice" and r.json()["human_review_required"] is True
    async with admin.acquire() as c:
        assert {x["admission"] for x in await c.fetch("SELECT admission::text FROM platform.evidence_event WHERE student_id = $1::uuid", student)} == {"source_unverified"}
        g = await c.fetchrow("SELECT semantics::text, human_review_required, review_reasons::text[] FROM platform.grade_result WHERE student_id = $1::uuid", student)
        assert g["semantics"] == "practice" and g["human_review_required"] and "content_not_admitted" in g["review_reasons"]


async def test_admitted_item_stays_formal(api, pools, student, fake_adapter):
    fake_adapter.admission = "admitted"
    r = await post(api, "grade", grade_body(student, item="SYN-ADM", text="42"))
    assert r.status_code == 200
    assert r.json()["semantics"] == "formal" and r.json()["human_review_required"] is False


# ---------- 请求校验与上游错误 ----------
async def test_request_errors_before_calling_adapter(api, pools, student, fake_adapter):
    cases = [
        (grade_body(student, item="NOT-REGISTERED"), 422, "CONTENT_NOT_REGISTERED"),
        ({"context": {}}, 400, "INVALID_REQUEST"),
        (grade_body(str(uuid.uuid4())), 404, "STUDENT_NOT_FOUND"),
    ]
    wenli = grade_body(student)
    wenli["context"]["subject"] = "wenli"
    wenli["context"]["content_version"]["subject"] = "wenli"
    wenli["attempt"]["item_ref"] = "wenli:X-1"
    cases.append((wenli, 400, "SUBJECT_NOT_SUPPORTED"))
    for body, status, code in cases:
        r = await post(api, "grade", body)
        assert (r.status_code, r.json()["code"]) == (status, code), r.text
        assert internal_contract().validate("Problem", r.json()) == []
    assert fake_adapter.calls == 0
    bad = await api.post("/internal/v0/grade", json=grade_body(student), headers={"authorization": "Bearer wrong"})
    assert bad.status_code == 401 and bad.json()["code"] == "UNAUTHENTICATED"


async def test_upstream_errors_mark_failed_and_allow_retry(api, pools, student, fake_adapter):
    _, admin = pools
    body = grade_body(student)
    fake_adapter.status = 404
    fake_adapter.problem = {"code": "ITEM_NOT_FOUND", "retryable": False, "detail": "學生原文 should never be forwarded"}
    r = await post(api, "grade", body)
    assert r.status_code == 404 and r.json()["code"] == "UPSTREAM_REJECTED" and r.json()["upstream_code"] == "ITEM_NOT_FOUND"
    assert "學生原文" not in r.text
    assert fake_adapter.calls == 1
    fake_adapter.status = 200
    r2 = await post(api, "grade", body)
    assert r2.status_code == 200
    assert await counts(admin, student) == ONE_GRADE


async def test_connect_failure_is_retried_once(api, student, fake_adapter):
    fake_adapter.connect_failures = 1
    assert (await post(api, "grade", grade_body(student))).status_code == 200
    assert fake_adapter.calls == 2
    fake_adapter.connect_failures = 5
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 503 and r.json()["retryable"] is True


async def test_retryable_upstream_error_is_retried_once_and_async_is_refused(api, student, fake_adapter):
    fake_adapter.status = 503
    fake_adapter.problem = {"code": "UPSTREAM_UNAVAILABLE", "retryable": True}
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 502 and fake_adapter.calls == 2
    fake_adapter.status = 202
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 501 and r.json()["code"] == "ASYNC_NOT_SUPPORTED"


def test_fingerprint_ignores_request_id_and_normalizes_appeal():
    b = grade_body(str(uuid.uuid4()))
    c = copy.deepcopy(b)
    c["context"]["request_id"] = str(uuid.uuid4())
    c["appeal"] = False
    assert request_fingerprint("grade", b) == request_fingerprint("grade", c)
    c["appeal"] = True
    assert request_fingerprint("grade", b) != request_fingerprint("grade", c)


async def test_appeal_forces_review_even_if_adapter_forgets(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.admission = "admitted"
    r = await post(api, "grade", grade_body(student, item="SYN-ADM", text="42", appeal=True))
    assert r.status_code == 200, r.text
    assert r.json()["human_review_required"] is True
    async with admin.acquire() as c:
        reasons = await c.fetchval("SELECT review_reasons::text[] FROM platform.grade_result WHERE student_id = $1::uuid", student)
    assert reasons == ["student_appeal"]


async def test_upstream_code_must_look_like_a_code(api, student, fake_adapter):
    """安全审查 B1：适配器在 code 里夹带文本时不转发。"""
    fake_adapter.status = 422
    for code in ("学生李某某的作答ABC", "13812345678 X", "张三A", "lower_case"):
        fake_adapter.problem = {"code": code, "retryable": False}
        r = await post(api, "grade", grade_body(student))
        assert r.status_code == 422
        assert "upstream_code" not in r.json() and code not in r.text
        assert internal_contract().validate("Problem", r.json()) == []


async def test_adapter_auth_failure_is_not_passed_through_as_401(api, student, fake_adapter):
    fake_adapter.status = 401
    fake_adapter.problem = {"code": "INVALID_REQUEST", "retryable": False}
    r = await post(api, "grade", grade_body(student))
    assert r.status_code == 502 and r.json()["code"] == "UPSTREAM_UNAVAILABLE"


async def test_request_time_must_be_rfc3339_with_zone(api, student, fake_adapter):
    for bad in ("epoch", "2026-10-08T19:30:00", "infinity"):
        b = grade_body(student)
        b["attempt"]["occurred_at"] = bad
        r = await post(api, "grade", b)
        assert r.status_code == 400, (bad, r.text)
    assert fake_adapter.calls == 0


async def test_non_finite_numbers_in_request_are_rejected(api, student):
    raw = json.dumps(grade_body(student)).replace('"elapsed_sec": 60', '"elapsed_sec": NaN')
    r = await api.post("/internal/v0/grade", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 400


async def test_request_body_limit(api):
    r = await api.post("/internal/v0/grade", content=b"x" * (16 * 1024 * 1024 + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_config_refuses_unsafe_settings():
    from learning.config import Settings
    base = {"LEARNING_ENV": "dev", "LEARNING_DATABASE_URL": "postgresql:///x", "LEARNING_SERVICE_TOKEN": "s" * 32}
    Settings.from_env(base)
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ENV": "prod"})
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ADAPTER_AYA_URL": "http://127.0.0.1:8787"})  # 缺令牌
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "LEARNING_ADAPTER_AYA_URL": "http://adapter.example", "LEARNING_ADAPTER_AYA_TOKEN": "a" * 32})
    ok = Settings.from_env({**base, "LEARNING_ADAPTER_AYA_URL": "http://127.0.0.1:8787/", "LEARNING_ADAPTER_AYA_TOKEN": "a" * 32})
    assert ok.adapters["aya"].base_url == "http://127.0.0.1:8787"
    assert ok.lease_seconds > 2 * ok.diagnose_timeout + 1, "租约必须长于适配器调用的总预算"


# ---------- 契约守护复核 ----------
def _prior_record(evidence_id: str, item: str, admission: str) -> dict:
    return {"evidence_id": evidence_id, "kind": "verifier_trace", "occurred_at": "2026-10-01T00:00:00+00:00",
            "item_ref": f"aya:{item}", "admission": admission,
            "payload": {"sampled_points": 8, "agreeing_points": 8, "reason_codes": []}}


def _cite_prior(prior_id: str):
    def f(out):
        out["result"]["observations"][0]["evidence_ids"].append(prior_id)
        out["semantics"], out["human_review_required"] = "formal", False
        out.pop("review_reasons", None)
        return out
    return f


async def test_prior_evidence_must_exist_for_this_student(api, pools, student, fake_adapter):
    """阻断 1：请求自带的既有证据必须真在库里、属于这个学生；否则调适配器之前就拒绝。"""
    other = grade_body(student)
    r = await post(api, "grade", other)
    known = r.json()["evidence_ids"][0]
    calls = fake_adapter.calls
    for pid in (str(uuid.uuid4()),):
        body = diagnose_body(student, [at("SYN-UNV", "42", 5)])
        body["prior_evidence"] = [_prior_record(pid, "SYN-UNV", "admitted")]
        r = await post(api, "diagnose", body)
        assert r.status_code == 422, r.text
    # 别的学生的证据
    _, admin = pools
    other_student = str(uuid.uuid4())
    body = diagnose_body(other_student, [at("SYN-UNV", "42", 6)])
    body["prior_evidence"] = [_prior_record(known, "SYN-UNV", "admitted")]
    r = await post(api, "diagnose", body)
    assert r.status_code == 422
    assert fake_adapter.calls == calls


async def test_prior_evidence_admission_comes_from_platform(api, pools, student, fake_adapter):
    """阻断 1：既有证据自称 admitted，平台库里是 source_unverified：结果必须是 practice + 复核。"""
    first = (await post(api, "grade", grade_body(student, item="SYN-UNV"))).json()
    prior_id = first["evidence_ids"][0]
    fake_adapter.admission = "admitted"
    fake_adapter.mutate = _cite_prior(prior_id)
    body = diagnose_body(student, [at("SYN-ADM", "42", 7)])
    body["prior_evidence"] = [_prior_record(prior_id, "SYN-UNV", "admitted")]
    r = await post(api, "diagnose", body)
    assert r.status_code == 200, r.text
    assert r.json()["semantics"] == "practice" and r.json()["human_review_required"] is True


async def test_attempt_id_is_globally_unique(api, pools, student):
    """应修 2：两个请求带同一个 attempt_id（作答时间不同）：第二个 409，不写任何行。"""
    _, admin = pools
    shared = str(uuid.uuid4())
    b1 = grade_body(student)
    b1["attempt"]["attempt_id"] = shared
    assert (await post(api, "grade", b1)).status_code == 200
    b2 = grade_body(student)
    b2["attempt"]["attempt_id"] = shared
    b2["attempt"]["occurred_at"] = "2026-10-09T08:00:00+08:00"
    r = await post(api, "grade", b2)
    assert r.status_code == 409 and r.json()["code"] == "ATTEMPT_ID_CONFLICT"
    assert await counts(admin, student) == ONE_GRADE
    # 同一请求重放不受影响
    assert (await post(api, "grade", b1)).headers["idempotent-replayed"] == "true"


async def test_too_short_lease_terminates_without_duplicate_rows(pools, student, fake_adapter):
    """阻断 2：租约配置得比适配器调用还短时，两个同 key 请求不能无限轮流接管；最终都返回，且至多写一次。"""
    import httpx
    from conftest import CREDENTIAL, settings
    from learning.app import create_app
    app_pool, admin = pools
    fake_adapter.delay = 0.5
    app = create_app(settings(lease_seconds=0.3, grade_timeout=1.0), pool=app_pool,
                     transport=httpx.MockTransport(fake_adapter.handler))
    body = grade_body(student)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://l.test",
                                     headers={"authorization": f"Bearer {CREDENTIAL}"}) as c:
            first = asyncio.create_task(c.post("/internal/v0/grade", json=body))
            await asyncio.sleep(0.4)
            second = asyncio.create_task(c.post("/internal/v0/grade", json=body))
            done = await asyncio.wait_for(asyncio.gather(first, second), timeout=20)
    assert {r.status_code for r in done} <= {200, 503}
    assert fake_adapter.calls < 20
    got = await counts(admin, student)
    assert got in (ONE_GRADE, NOTHING)
