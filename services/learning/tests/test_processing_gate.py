"""LT-06 决定记录（续）第 4 条：学习服务的库层处理判据（迁移 0059 platform.student_processing_allowed）。全部合成数据。

已退出内部测试、没有有效同意（或已删除）的学生，学习服务的写入与模型处理入口一律 404 STUDENT_NOT_FOUND（learning-internal 契约已有的
问题码，与“学生不存在”同一个），不调适配器、不落库、不留幂等记录：/internal/v1 判分与诊断写入、今日计划、诊断会话、再练、复测、
今日编排、档案页 ensure、建验证任务；掌握对账（mastery-sweep）的选人跳过这些学生。没退出、有同意的学生照常（得到原来的结果或
“模块没配”的 501 / 409，证明拦下的是判据而不是别的校验）。判据函数不在时（迁移 0059 未执行）不拦。
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from conftest import CREDENTIAL, counts, env, grade_body, settings, to_v1_body
from learning import processing
from learning.app import create_app

ORCH = "synthetic-orchestrator-to-learning-credential-01"
INSIGHT = "synthetic-insight-to-learning-credential-000001"
NOTHING = {"evidence_event": 0, "attempt": 0, "grade_result": 0, "grade_dimension": 0, "error_attribution": 0, "skill_observation": 0}
AUTH = {"authorization": f"Bearer {CREDENTIAL}"}


async def exit_student(admin, student: str) -> None:
    """库里记一条退出（以超级用户直接写，绕开家长发起的函数；那一层由 db/tests/79zg 与 BFF 集成测试覆盖）"""
    async with admin.acquire() as c:
        g = await c.fetchval("SELECT guardian_account_id FROM platform.consent_record WHERE student_id = $1::uuid LIMIT 1", student)
        await c.execute("INSERT INTO platform.internal_test_exit (student_id, requested_by) VALUES ($1::uuid, $2::uuid)", student, g)


async def no_consent_student(admin) -> str:
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
    return sid


@pytest.fixture
async def gate_api(pools, fake_adapter):
    """配齐编排层与洞察服务令牌（档案页 ensure、验证任务要用）；诊断、再练、复测、今日编排、档案页都没配（对照组得到 501 / 409）"""
    app_pool, _ = pools
    app = create_app(settings(insight_client_credential=INSIGHT, orchestrator_client_credential=ORCH), pool=app_pool,
                     transport=httpx.MockTransport(fake_adapter.handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            yield client


def _requests(student: str) -> list[tuple[str, dict, dict]]:
    sid = str(uuid.uuid4())
    key = str(uuid.uuid4())
    usage = str(uuid.uuid4())
    return [
        ("/internal/v1/diagnosis/sessions", {"student_ref": student, "subject": "aya", "usage_id": usage, "idempotency_key": key}, AUTH),
        (f"/internal/v1/diagnosis/sessions/{sid}/probe", {"student_ref": student, "seq": 1, "probe_key": key}, AUTH),
        (f"/internal/v1/diagnosis/sessions/{sid}/answer",
         {"student_ref": student, "seq": 1, "answer": "42", "elapsed_sec": 30, "idempotency_key": key}, AUTH),
        (f"/internal/v1/diagnosis/sessions/{sid}/pause", {"student_ref": student}, AUTH),
        (f"/internal/v1/diagnosis/sessions/{sid}/abandon", {"student_ref": student}, AUTH),
        ("/internal/v1/practice/queue", {"student_ref": student, "subject": "aya"}, AUTH),
        ("/internal/v1/practice/next", {"student_ref": student, "subject": "aya", "entry_id": sid}, AUTH),
        ("/internal/v1/practice/answer", {"student_ref": student, "subject": "aya", "ticket": "t", "answer": "42", "idempotency_key": key,
                                          "usage_id": usage}, AUTH),
        ("/internal/v1/retest/queue", {"student_ref": student, "subject": "aya"}, AUTH),
        ("/internal/v1/retest/next", {"student_ref": student, "subject": "aya", "entry_id": sid}, AUTH),
        ("/internal/v1/retest/answer", {"student_ref": student, "subject": "aya", "ticket": "t", "answer": "42", "idempotency_key": key,
                                        "usage_id": usage}, AUTH),
        ("/internal/v1/retest/check", {"student_ref": student, "subject": "aya", "ticket": "t"}, AUTH),
        ("/internal/v1/retest/replay", {"student_ref": student, "subject": "aya", "ticket": "t", "idempotency_key": key, "usage_id": usage}, AUTH),
        ("/internal/v1/today", {"student_ref": student}, AUTH),
        ("/internal/v1/plan", {"student_ref": student}, AUTH),
        ("/internal/v1/profile-page/ensure", {"request_id": str(uuid.uuid4()), "student_ref": student, "subject": "aya", "trigger": "session_start"},
         {"authorization": f"Bearer {ORCH}"}),
    ]


def _is_gate(r: httpx.Response) -> bool:
    return r.status_code == 404 and r.json().get("code") == "STUDENT_NOT_FOUND" and r.json().get("title") == processing.NOT_AVAILABLE


async def test_exited_student_refused_everywhere_no_adapter_call_no_rows(gate_api, pools, student, fake_adapter):
    _, admin = pools
    # 对照：没退出时这些请求不是被判据拦下（模块没配 501、档案页没配 409、计划请求不完整 400）
    for path, body, headers in _requests(student):
        r = await gate_api.post(path, json=body, headers=headers)
        assert not _is_gate(r), f"{path}：没退出的学生不该被判据拦（{r.status_code} {r.text}）"
    await exit_student(admin, student)
    for path, body, headers in _requests(student):
        r = await gate_api.post(path, json=body, headers=headers)
        assert _is_gate(r), f"{path}：{r.status_code} {r.text}"
        assert student not in r.text
    g = to_v1_body(grade_body(student))
    r = await gate_api.post("/internal/v1/grade", json=env(g), headers=AUTH)
    assert _is_gate(r), r.text
    with pytest.raises(Exception) as up:  # 大写写法的同一学生同样拦下（库里的 uuid 不分大小写）
        await processing.require(admin, True, student.upper())
    assert getattr(up.value, "code", None) == "STUDENT_NOT_FOUND"
    assert fake_adapter.calls == 0, "不调适配器（对外调用）"
    assert await counts(admin, student) == NOTHING
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.ingest_request WHERE student_id = $1::uuid", student) == 0, "不留幂等记录"


async def test_no_consent_refused_and_grade_passes_once_consented(gate_api, pools, fake_adapter):
    from conftest import grant_consents
    _, admin = pools
    s = await no_consent_student(admin)
    g = to_v1_body(grade_body(s))
    r = await gate_api.post("/internal/v1/grade", json=env(g), headers=AUTH)
    assert _is_gate(r), r.text
    assert fake_adapter.calls == 0 and await counts(admin, s) == NOTHING
    async with admin.acquire() as c:
        await grant_consents(c, s)
    r = await gate_api.post("/internal/v1/grade", json=env(g), headers=AUTH)
    assert r.status_code == 200, r.text
    assert fake_adapter.calls == 1


async def test_verification_refused_after_exit(gate_api, pools, student):
    _, admin = pools
    body = {"request_id": str(uuid.uuid4()), "student_ref": student, "subject": "aya", "node_id": "MATH.SYN.A", "cognitive_level": "apply",
            "reason": "verify_self_report", "dispute_ref": str(uuid.uuid4()), "item_count": 1}
    headers = {"authorization": f"Bearer {INSIGHT}"}
    before = await gate_api.post("/internal/v1/verification", json=body, headers=headers)
    assert before.status_code == 409 and before.json()["code"] == "MASTERY_UNAVAILABLE", before.text  # 没配掌握：照常走到原来的判断
    await exit_student(admin, student)
    r = await gate_api.post("/internal/v1/verification", json=body, headers=headers)
    assert _is_gate(r), r.text


async def test_sweep_skips_exited_students(pools, student):
    from learning.mastery.jobs import sweep_students

    _, admin = pools
    other = str(uuid.uuid4())
    async with admin.acquire() as c:
        await c.execute("""INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, status, due_at)
                           VALUES ($1::uuid, 'aya', 'MATH.SYN.A', 'apply', 'decay', 'open', now())""", student)

    class Eng:
        pool = admin

        @staticmethod
        def config(_s):
            return {"sweep": {"lookback_seconds": 3600}}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    assert student in await sweep_students(Eng, "aya", now)
    await exit_student(admin, student)
    assert student not in await sweep_students(Eng, "aya", now)
    assert other not in await sweep_students(Eng, "aya", now)


async def test_gate_off_when_function_missing():
    class P:
        async def fetchval(self, *_a):
            return False
    assert await processing.available(P()) is False
    assert await processing.available(None) is False
    await processing.require(P(), False, str(uuid.uuid4()))  # 判据不可用：不拦
    await processing.require(None, True, str(uuid.uuid4()))
    await processing.require(P(), True, "not-a-uuid")  # 不是 UUID：交给原来的参数校验
