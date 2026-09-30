"""SIE-03 库上测试（一次性 Postgres，执行 bootstrap 角色与全部迁移 0001–0035，合成数据）+ 契约严格校验：
- 异议三路端到端：“我其实会”同一事务转待验证并登记 2 道验证任务（接口桩），验证支持 / 不支持 / 冲突三种结论的流转；
  提出人与请求 actor 必须一致，不属于、不可见、不存在、身份不符一律同一个 404 且不留任何写入；“那次只是失误”只记自述、不改判断；
  “数据有误”带原始事实进复核队列，事实更正后相关判断 100% 撤回并按判断生成重算；
- 预测：区间随证据变窄、考试当天零点起不能改、调度器冻结后库层拒绝修改（洞察服务调不了冻结函数）；
- 对账卡：区间外 → 偏差节点的判断转待验证、偏差方向只用四值、每句带判断 ID 且只引用对读者可见的判断；高于区间与渲染命中
  禁用推断词表时不出卡、整笔回滚；
- 触发规则：只写建议、去重；未签字不下发、库里没有签字登记也不下发；
- 导出与删除：经 0035 库函数（测试里以 BFF 角色调用）映射成契约形状；删除执行后该学生个人层零行、别的学生不受影响。
"""
from __future__ import annotations

import copy
import dataclasses
import json
import uuid
from datetime import timedelta

import asyncpg
import pytest

from insight.claims import MasteryRow
from insight.guardian_requests import ticket_from_erasure, ticket_from_export
from insight.policy import CONFIG_DIR
from insight.predict import NoMasterySource
from insight.scheduled import SchedulerRoleError, assert_scheduler_role, connect_scheduler, execute_due_erasures, freeze_due_predictions
from insight.service.contract import contract
from insight.shared import forbidden_matcher
from insight.sie03_policy import parse_sie03, synthetic_sie03
from insight.store import InsightStore, create_pool
from insight.store_sie03 import Sie03Store, Unavailable
from pginsight import APP_ROLE, BFF_ROLE, E, GB, LEARNING_ROLE, S3, TPL_MASTERY, TPL_TRANSFER
from sie03_seed import (AT4, E4, GC, LINK_C4, LINK_C5, M4, M5, S4, S5, SA4, SA5, SCHEDULER_ROLE, TEXT_PREDICT, TEXT_RECONCILE,
                        TEXT_RECONCILE_BAD, TPL_PREDICT, TPL_PREDICT_AGENT, TPL_RECONCILE, TPL_RECONCILE_BAD, reset)
from support import NODE_A, NODE_B, NODE_C, StaticVerifier, agent_ctx, call, front_ctx, make_policy, running_app
from test_db import db_now

pytestmark = pytest.mark.anyio
P = make_policy()
PS = make_policy(signed=True)
P3 = synthetic_sie03()
C = contract()
P3_PATH = CONFIG_DIR / "sie03.synthetic.json"
# 授权核对（生产里由 ActorVerifier 按绑定关系与 link_id 核对；缺省一律不通过）。(SA4, guardian, S4) 故意放行：
# 验证库函数与洞察服务的身份一致性核对是第二道（学生账号冒充家长照样 404）
VERIFIER = StaticVerifier({(SA4, "student", S4), (GC, "guardian", S4), (SA4, "guardian", S4), (SA5, "student", S5),
                           (GC, "guardian", S5)})


def mr(basis: int, node: str, gate: str, ev: tuple[int, ...], at, level: str = "apply", conf: float = 0.5, retest=None,
       student: str = S4, basis_id: str | None = None) -> MasteryRow:
    return MasteryRow(basis_id or M4[basis], student, "aya", node, level, gate, None, conf, tuple(E4[i] for i in ev),
                      "kge-mastery@0.1.0.syn", at, retest)


async def sync(db, rows, policy=P, student: str = S4):
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    try:
        return await InsightStore(pool).sync_mastery_claims(student, rows, policy)
    finally:
        await pool.close()


class Ops:
    """测试里直接调 Sie03Store（服务内接口）"""

    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self) -> Sie03Store:
        self.pool = await create_pool(**self.db.connect_kwargs(APP_ROLE))
        return Sie03Store(self.pool)

    async def __aexit__(self, *exc) -> None:
        await self.pool.close()


def q(db, sql: str) -> str:
    return db.psql(sql).strip()


def claim_of(db, node: str, key: str = TPL_MASTERY, student: str = S4, live: bool = True) -> str:
    cond = " AND status IN ('hypothesis', 'established', 'to_verify')" if live else ""
    return q(db, f"SELECT id FROM insight.claim WHERE student_id = '{student}' AND target_id = '{node}' AND template_key = '{key}'{cond} "
                 "ORDER BY created_at DESC LIMIT 1")


def dispute_body(ctx: dict, claim_id: str, kind: str) -> dict:
    return {"context": ctx, "claim_id": claim_id, "kind": kind}


def stu(key: str, student: str = S4, actor: str = SA4) -> dict:
    return front_ctx("student", actor, student, idempotency_key=key)


def gua(key: str, student: str = S4, actor: str = GC, link: str = LINK_C4) -> dict:
    return front_ctx("guardian", actor, student, link_id=link, idempotency_key=key)


def without_rid(body: dict) -> dict:
    b = copy.deepcopy(body)
    b.pop("request_id", None)
    return b


# ---------- 异议：我其实会 ----------
async def test_i_know_supported_end_to_end(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now)])
    cid = claim_of(db, NODE_A)
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        body = dispute_body(stu("syn-dispute-iknow-0001"), cid, "i_know")
        r = await call(client, "insight.dispute", body, "bff")
        assert r.status_code == 200, r.text
        out = r.json()
        assert C.validate("InsightDisputeResponse", out) == []
        res = out["result"]
        assert out["evidence_ids"] == [E4[0], E4[1]]  # 被异议判断的证据（契约审查 8）
        assert res["next_step"] == "verification_scheduled" and len(res["verification_item_refs"]) == 2
        assert all(x.startswith("aya:stub-verify-") for x in res["verification_item_refs"])  # 接口桩（KGE-04 第二步前）
        did = res["dispute_id"]
        row = q(db, f"SELECT status || '|' || cardinality(verification_task_refs) || '|' || source_type || '|' || raised_by_role || '|' || "
                    f"extract(epoch FROM ('{res['verify_by']}'::timestamptz - created_at))::int FROM insight.dispute WHERE id = '{did}'")
        assert row == "verifying|2|self|student|86400"
        assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "to_verify"
        assert q(db, f"SELECT reason FROM insight.claim_history WHERE claim_id = '{cid}' ORDER BY at DESC LIMIT 1") == "dispute"
        # 幂等：同 key 同体（换 request_id）返回原结果；同 key 异体 409；换 key 同体：库里返回同一条未结束异议
        retry = copy.deepcopy(body)
        retry["context"]["request_id"] = str(uuid.uuid4())
        r2 = await call(client, "insight.dispute", retry, "bff")
        assert r2.status_code == 200 and r2.json() == out
        other = copy.deepcopy(retry)
        other["kind"] = "data_error"
        r3 = await call(client, "insight.dispute", other, "bff")
        assert r3.status_code == 409 and r3.json()["code"] == "IDEMPOTENCY_CONFLICT" and C.problem_errors(r3.json()) == []
        r4 = await call(client, "insight.dispute", dispute_body(stu("syn-dispute-iknow-0002"), cid, "i_know"), "bff")
        assert r4.status_code == 200 and r4.json()["result"] == res
    assert q(db, f"SELECT count(*) FROM insight.dispute WHERE claim_id = '{cid}'") == "1"

    # 验证结果以 KGE 掌握行回来：门槛高于判断所述 → 异议成立：原判断推翻，同一事务按新行重算出新判断（上调）
    later = await db_now(db)
    res = await sync(db, [mr(0, NODE_A, "stable", (0, 1, 4), later), mr(1, NODE_B, "execute", (2, 3), now)])
    assert res.settled == ((did, "supported"),)
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "refuted"
    assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{did}'") == \
        "resolved_supported|verification_supported"
    new = claim_of(db, NODE_A)
    assert new != cid and q(db, f"SELECT status || '|' || (value ->> 'gate') FROM insight.claim WHERE id = '{new}'") == "hypothesis|stable"
    async with Ops(db) as ops:
        m = await ops.dispute_metrics(now - timedelta(minutes=5))
    assert m["i_know_total"] >= 1 and m["stub_scheduled"] >= 1 and m["valid"] is False and m["rate"] is None


async def test_i_know_not_supported_needs_signed_policy(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now)])
    cid = claim_of(db, NODE_A)
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        r = await call(client, "insight.dispute", dispute_body(gua("syn-dispute-iknow-g001"), cid, "i_know"), "bff")
        assert r.status_code == 200 and C.validate("InsightDisputeResponse", r.json()) == []
    did = r.json()["result"]["dispute_id"]
    assert q(db, f"SELECT source_type || '|' || raised_by_role FROM insight.dispute WHERE id = '{did}'") == "guardian|guardian"
    later = await db_now(db)
    rows = [mr(0, NODE_A, "execute", (0, 1, 4), later)]
    res = await sync(db, rows, P)  # 未签字：不能回到成立，保持待验证
    assert res.settled == ((did, "pending_signoff"),)
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "to_verify"
    res = await sync(db, rows, PS)
    assert res.settled == ((did, "not_supported"),)
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "established"
    assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{did}'") == \
        "resolved_unsupported|verification_not_supported"
    assert q(db, f"SELECT reason FROM insight.claim_history WHERE claim_id = '{cid}' ORDER BY at DESC LIMIT 1") == "verification_supported"


async def test_i_know_conflict_stays_verifying(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(2, NODE_A, "stable", (4, 5), now)])
    cid = claim_of(db, NODE_A)
    async with Ops(db) as ops:
        from insight.dispute import StubVerificationScheduler
        out = await ops.raise_dispute(S4, cid, "i_know", "student", SA4, StubVerificationScheduler(), P3)
        res = await sync(db, [mr(2, NODE_A, "understand", (4, 5, 6), await db_now(db))])
        assert res.settled == ((out.dispute_id, "conflict"),)
        # 常规生命周期照走（验证不支持判断所述 → 推翻）；异议没有对应的结案方式，保持验证中并计入 stuck（待人确认）
        assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "refuted"
        assert q(db, f"SELECT status FROM insight.dispute WHERE id = '{out.dispute_id}'") == "verifying"
        assert (await ops.sweep_disputes(P3))["i_know_stuck"] >= 1


async def test_dispute_identity_and_existence_hiding(sie03db):
    db = sie03db
    reset(db)
    reset(db, (S5,))
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now),
                    dataclasses.replace(mr(1, NODE_B, "execute", (2, 3), now), subject="wenli", evidence_ids=(E[0],))])  # 文砺：只给 agent
    cid = claim_of(db, NODE_A)
    wenli = q(db, f"SELECT id FROM insight.claim WHERE student_id = '{S4}' AND subject = 'wenli'")
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        missing = await call(client, "insight.dispute", dispute_body(stu("syn-dispute-miss-0001"), str(uuid.uuid4()), "i_know"), "bff")
        assert missing.status_code == 404
        cases = [
            dispute_body(gua("syn-dispute-case-0001", actor=SA4), cid, "i_know"),           # 自称家长、实为学生账号：身份不符
            dispute_body(gua("syn-dispute-case-0002", actor=GB), cid, "data_error"),         # 没有绑定的家长
            dispute_body(stu("syn-dispute-case-0003", actor=SA5), cid, "i_know"),            # 别的学生的账号
            dispute_body(stu("syn-dispute-case-0004", student=S5, actor=SA5), cid, "i_know"),  # 判断不属于 student_ref
            dispute_body(stu("syn-dispute-case-0005"), wenli, "i_know"),                      # 判断对学生不可见
            dispute_body(gua("syn-dispute-case-0007"), wenli, "data_error"),                   # 不可见且事实定位不了：先 404（F3）
        ]
        for b in cases:
            r = await call(client, "insight.dispute", b, "bff")
            assert r.status_code == 404
            got, want = r.json(), missing.json()
            got.pop("request_id", None), want.pop("request_id", None)
            assert got == want and "detail" not in got
        # “那次只是失误”只能由学生本人提出：家长 → 契约校验不过
        r = await call(client, "insight.dispute", dispute_body(gua("syn-dispute-case-0006"), cid, "one_off_slip"), "bff")
        assert r.status_code == 400
    # 全部回滚：没有异议、没有复核任务、判断没动
    assert q(db, f"SELECT count(*) FROM insight.dispute WHERE student_id = '{S4}'") == "0"
    assert q(db, f"SELECT count(*) FROM platform.review_task WHERE student_id = '{S4}'") == "0"
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "hypothesis"


# ---------- 异议：那次只是失误 ----------
async def test_one_off_slip_records_self_statement_only(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now)])
    cid, cid_b = claim_of(db, NODE_A), claim_of(db, NODE_B)
    hist_before = q(db, f"SELECT count(*) FROM insight.claim_history WHERE claim_id = '{cid}'")
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        r = await call(client, "insight.dispute", dispute_body(stu("syn-dispute-slip-0001"), cid, "one_off_slip"), "bff")
        assert r.status_code == 200 and C.validate("InsightDisputeResponse", r.json()) == []
        res = r.json()["result"]
        assert res["next_step"] == "observe_followups" and set(res) == {"dispute_id", "next_step"}
        r = await call(client, "insight.dispute", dispute_body(stu("syn-dispute-slip-0002"), cid_b, "one_off_slip"), "bff")
        other = r.json()["result"]["dispute_id"]
    did = res["dispute_id"]
    # 只记学生自述：判断不变（没有新的历史），异议行来源 self；没有任何标签或评价列
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "hypothesis"
    assert q(db, f"SELECT count(*) FROM insight.claim_history WHERE claim_id = '{cid}'") == hist_before
    assert q(db, f"SELECT status || '|' || source_type FROM insight.dispute WHERE id = '{did}'") == "open|self"
    cols = set(q(db, "SELECT string_agg(column_name, ',') FROM information_schema.columns WHERE table_schema = 'insight' "
                     "AND table_name = 'dispute'").split(","))
    assert not {c for c in cols if any(w in c for w in ("label", "tag", "careless", "trait", "comment", "note_text"))}
    async with Ops(db) as ops:
        assert (await ops.sweep_disputes(P3))["slip_recorded"] == 0
        db.psql(f"SET session_replication_role = replica; UPDATE insight.dispute SET created_at = now() - interval '15 days' WHERE id = '{did}';")
        await sync(db, [mr(0, NODE_A, "execute", (0, 1), now)])  # NODE_B 的依据撤下 → 判断撤回
        counts = await ops.sweep_disputes(P3)
    assert counts["slip_recorded"] == 1 and counts["slip_rejected"] == 1
    assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{did}'") == \
        "resolved_unsupported|self_statement_recorded"
    assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{other}'") == "rejected|claim_no_longer_visible"
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "hypothesis"


# ---------- 异议：数据有误 ----------
async def test_data_error_fact_correction_withdraws_and_recomputes(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    rows = [mr(0, NODE_A, "execute", (0, 1), now), mr(3, NODE_C, "transfer", (6, 7), now)]
    await sync(db, rows)
    c_mastery, c_transfer, a_mastery = claim_of(db, NODE_C), claim_of(db, NODE_C, TPL_TRANSFER), claim_of(db, NODE_A)
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        r = await call(client, "insight.dispute", dispute_body(gua("syn-dispute-data-0001"), c_mastery, "data_error"), "bff")
        assert r.status_code == 200, r.text
        assert C.validate("InsightDisputeResponse", r.json()) == []
        res = r.json()["result"]
        # 事实：判断证据里最近一条对上的作答
        assert res["next_step"] == "open_fact_for_correction" and res["fact_ref"] == f"attempt:{AT4[7]}"
        r = await call(client, "insight.dispute", dispute_body(gua("syn-dispute-data-0002"), a_mastery, "data_error"), "bff")
        confirm_id = r.json()["result"]["dispute_id"]
    did = res["dispute_id"]
    assert q(db, f"SELECT status || '|' || cardinality(reasons) FROM platform.review_task WHERE source_kind = 'claim' "
                 f"AND source_id = '{c_mastery}'") == "open|1"
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{c_mastery}'") == "hypothesis"  # 进队不改判断
    async with Ops(db) as ops:
        before = await ops.settle_fact_corrections(S4, rows, P)
        assert before.settled == ()  # 事实还没更正
        # 原录入通道更正事实（改判：新判分取代旧判分）——以超级用户模拟判分改判，只为夹具
        db.psql(f"""SET session_replication_role = replica;
          WITH old AS (SELECT id FROM platform.grade_result WHERE attempt_id = '{AT4[7]}' AND superseded_by IS NULL),
               ins AS (INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                         score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics,
                         evidence_ids, source_request_id)
                       SELECT student_id, subject, attempt_id, attempt_occurred_at, 'correct', rubric_version, 1, 1, true, 1, source_version,
                              model_version, false, semantics, ARRAY[gen_random_uuid()], gen_random_uuid()
                         FROM platform.grade_result WHERE id = (SELECT id FROM old) RETURNING id)
          UPDATE platform.grade_result SET superseded_by = (SELECT id FROM ins), superseded_at = now() WHERE id = (SELECT id FROM old);""")
        after_rows = [mr(0, NODE_A, "execute", (0, 1), now), mr(3, NODE_C, "transfer", (6, 7, 8), await db_now(db))]
        out = await ops.settle_fact_corrections(S4, after_rows, P)
        assert out.settled == ((did, "fact_corrected"),)
        # 相关判断 100% 撤回：同一依据行生成的知识掌握与知识迁移判断
        assert set(out.withdrawn_claims) == {c_mastery, c_transfer}
        assert q(db, f"SELECT string_agg(DISTINCT status::text, ',') FROM insight.claim WHERE id IN ('{c_mastery}', '{c_transfer}')") == "withdrawn"
        assert q(db, f"SELECT reason FROM insight.claim_history WHERE claim_id = '{c_mastery}' ORDER BY at DESC LIMIT 1") == "fact_corrected"
        # 重算：同一事务按判断生成重新生成
        assert out.sync is not None and {(o.unit[2], o.to_status) for o in out.sync.ops if o.claim_id is None} == \
            {(NODE_C, "hypothesis")}
        assert claim_of(db, NODE_C) not in ("", c_mastery) and claim_of(db, NODE_C, TPL_TRANSFER) not in ("", c_transfer)
        assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{did}'") == "fact_corrected|fact_corrected"
        assert q(db, f"SELECT status FROM insight.claim WHERE id = '{a_mastery}'") == "hypothesis"
        # 复核结论“数据无误”：维持
        await ops.confirm_data(S4, confirm_id)
        assert q(db, f"SELECT status || '|' || result_note_code FROM insight.dispute WHERE id = '{confirm_id}'") == \
            "resolved_unsupported|data_confirmed"
        with pytest.raises(Exception):
            await ops.confirm_data(S4, confirm_id)


# ---------- 预测、冻结与对账 ----------
class StaticMastery:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    async def rows(self, student, subject, node_ids):
        return [r for r in self._rows if r.student_id == student and r.subject == subject and r.node_id in node_ids]


async def bff(db, account: str) -> asyncpg.Connection:
    c = await asyncpg.connect(**db.connect_kwargs(BFF_ROLE))
    await c.execute("SELECT set_config('app.account_id', $1, false)", account)
    return c


async def plan_exam(db, exam_type: str, day, full: int, nodes: list[str], student: str = S4) -> str:
    c = await bff(db, GC)
    try:
        return str(await c.fetchval("SELECT insight.submit_exam_plan($1::uuid, 'aya', $2, $3::date, $4::numeric, $5::text[])",
                                    student, exam_type, day, full, nodes))
    finally:
        await c.close()


async def score_exam(db, plan: str, raw: float, student: str = S4) -> str:
    c = await bff(db, GC)
    try:
        return str(await c.fetchval("SELECT insight.submit_exam_score($1::uuid, $2::uuid, $3::numeric)", student, plan, raw))
    finally:
        await c.close()


async def shanghai_today(db):
    c = await asyncpg.connect(**db.connect_kwargs("postgres"))
    try:
        return await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
    finally:
        await c.close()


def exam_body(key: str, exam_type: str, day, full: int, nodes: list[str], student: str = S4) -> dict:
    return {"context": agent_ctx(student, idempotency_key=key),
            "exam": {"subject": "aya", "exam_type": exam_type, "exam_date": day.isoformat(), "full_score": full, "scope_node_ids": nodes}}


async def freeze_today(db, plan: str) -> int:
    """模拟到了考试当天：把考试安排的日期改成今天（超级用户 replica，只为夹具），再由调度器冻结"""
    db.psql(f"SET session_replication_role = replica; UPDATE insight.exam_record SET exam_date = "
            f"(now() AT TIME ZONE 'Asia/Shanghai')::date WHERE id = '{plan}';")
    conn = await connect_scheduler(**db.connect_kwargs(SCHEDULER_ROLE))
    try:
        return await freeze_due_predictions(conn)
    finally:
        await conn.close()


async def test_predict_freeze_and_reconcile_end_to_end(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "stable", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now)])
    node_a = claim_of(db, NODE_A)
    tomorrow = await shanghai_today(db) + timedelta(days=1)
    plan = await plan_exam(db, "monthly", tomorrow, 100, [NODE_A, NODE_B])
    few = StaticMastery([mr(0, NODE_A, "stable", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now)])
    many = StaticMastery([mr(0, NODE_A, "stable", tuple(range(0, 8)), now), mr(1, NODE_B, "execute", tuple(range(8, 16)), now)])
    async with running_app(db, verifier=VERIFIER, sie03=P3, mastery=few) as client:
        r = await call(client, "insight.predict", exam_body("syn-predict-0001", "monthly", tomorrow, 100, [NODE_A, NODE_B]))
        assert r.status_code == 200, r.text
        out = r.json()
        assert C.validate("InsightPredictResponse", out) == []
        res = out["result"]
        assert 0 <= res["low"] <= res["high"] <= 100 and res["interval_level"] == 0.8
        assert res["basis"] == {"node_count": 2, "evidence_count": 4} and set(out["evidence_ids"]) == {E4[i] for i in range(4)}
        freeze_at = q(db, f"SELECT extract(epoch FROM insight.exam_freeze_at('{tomorrow.isoformat()}'))::bigint")
        assert q(db, f"SELECT extract(epoch FROM '{res['frozen_at']}'::timestamptz)::bigint") == freeze_at  # 预定冻结时间
        cl = q(db, f"SELECT target_type || '|' || target_id || '|' || status || '|' || array_to_string(visibility, ',') || '|' || source_type "
                   f"FROM insight.claim WHERE id = '{res['claim_id']}'")
        assert cl == f"exam|{plan}|hypothesis|agent|inferred"
        assert q(db, f"SELECT claim_id || '|' || level || '|' || coalesce(frozen_at::text, '-') FROM insight.exam_prediction "
                     f"WHERE id = '{res['prediction_id']}'") == f"{res['claim_id']}|0.80|-"
        width_few = res["high"] - res["low"]
    async with running_app(db, verifier=VERIFIER, sie03=P3, mastery=many) as client:
        r = await call(client, "insight.predict", exam_body("syn-predict-0002", "monthly", tomorrow, 100, [NODE_A, NODE_B]))
        res2 = r.json()["result"]
        assert r.status_code == 200 and res2["prediction_id"] == res["prediction_id"] and res2["claim_id"] == res["claim_id"]
        assert res2["high"] - res2["low"] < width_few  # 证据多则窄
        assert q(db, f"SELECT reason FROM insight.claim_history WHERE claim_id = '{res['claim_id']}' ORDER BY at DESC LIMIT 1") == "revised"
        # 找不到考试安排 → 404；与安排不符 → 400；超出契约响应的分数上限 → 400
        r = await call(client, "insight.predict", exam_body("syn-predict-0003", "monthly", tomorrow + timedelta(days=1), 100, [NODE_A]))
        assert r.status_code == 404
        r = await call(client, "insight.predict", exam_body("syn-predict-0004", "monthly", tomorrow, 120, [NODE_A, NODE_B]))
        assert r.status_code == 400
        r = await call(client, "insight.predict", exam_body("syn-predict-0005", "monthly", tomorrow, 100, [NODE_A]))
        assert r.status_code == 400
        await plan_exam(db, "final", tomorrow, 200, [NODE_A])
        r = await call(client, "insight.predict", exam_body("syn-predict-0006", "final", tomorrow, 200, [NODE_A]))
        assert r.status_code == 400
    async with running_app(db, verifier=VERIFIER, sie03=P3, mastery=NoMasterySource()) as client:
        await plan_exam(db, "midterm", tomorrow, 100, [NODE_C])
        r = await call(client, "insight.predict", exam_body("syn-predict-0007", "midterm", tomorrow, 100, [NODE_C]))
        assert r.status_code == 409 and C.problem_errors(r.json()) == []  # 没有掌握行：证据不足，不编造区间

    # 冻结：洞察服务调不了冻结函数；调度器在考试当天零点后冻结；冻结后库层拒绝修改，predict 返回冻结的原结果
    ins = await asyncpg.connect(**db.connect_kwargs(APP_ROLE))
    try:
        await ins.execute("SET ROLE starbuddy_insight")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await ins.fetchval("SELECT insight.freeze_due_predictions(now())")
        with pytest.raises(SchedulerRoleError):
            await assert_scheduler_role(ins)
    finally:
        await ins.close()
    assert await freeze_today(db, plan) == 1
    frozen = q(db, f"SELECT frozen_at IS NOT NULL FROM insight.exam_prediction WHERE id = '{res['prediction_id']}'")
    assert frozen == "t"
    ins = await asyncpg.connect(**db.connect_kwargs(APP_ROLE))
    try:
        await ins.execute("SET ROLE starbuddy_insight")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await ins.execute(f"UPDATE insight.exam_prediction SET interval_low = 0 WHERE id = '{res['prediction_id']}'")
    finally:
        await ins.close()
    sch = await connect_scheduler(**db.connect_kwargs(SCHEDULER_ROLE))
    try:  # 调度器不能写判断（判断只由洞察服务写）
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await sch.execute("UPDATE insight.claim SET change_reason = 'revised'")
    finally:
        await sch.close()
    today = await shanghai_today(db)
    async with running_app(db, verifier=VERIFIER, sie03=P3, mastery=few) as client:
        r = await call(client, "insight.predict", exam_body("syn-predict-0008", "monthly", today, 100, [NODE_A, NODE_B]))
        assert r.status_code == 200 and C.validate("InsightPredictResponse", r.json()) == []
        assert (r.json()["result"]["low"], r.json()["result"]["high"]) == (res2["low"], res2["high"])  # 冻结后不重算

    # 成绩（按安排录入，远低于区间）→ 对账卡：偏差节点的判断转待验证，偏差方向只用四值，每句带判断 ID
    score = await score_exam(db, plan, 0)
    async with Ops(db) as ops:
        card = await ops.reconcile_exam(S4, score, P3, forbidden_matcher(), node_results={NODE_A: 0.0, NODE_B: 0.5})
        assert card.blocked is None and card.adjustments == ("claims_to_verify",) and card.moved_claims == (node_a,)
        again = await ops.reconcile_exam(S4, score, P3, forbidden_matcher())
        assert again.card_id == card.card_id  # 一条预测一张卡
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{node_a}'") == "to_verify"
    assert q(db, f"SELECT reason FROM insight.claim_history WHERE claim_id = '{node_a}' ORDER BY at DESC LIMIT 1") == "exam_deviation"
    row = json.loads(q(db, f"SELECT to_jsonb(r) FROM insight.exam_reconciliation r WHERE id = '{card.card_id}'"))
    assert row["in_interval"] is False and row["deviation_directions"] == ["diagnosis_optimistic"]
    assert row["deviation_nodes"] == [NODE_A] and row["audience"] == ["student", "guardian"]
    rc = {s["claim_id"] for s in row["sentences"]}
    assert len(rc) == 1 and len(row["sentences"]) == 2 and all(s["template_key"] == TPL_RECONCILE for s in row["sentences"])
    rc_id = rc.pop()
    assert q(db, f"SELECT array_to_string(visibility, ',') || '|' || status || '|' || (value ->> 'position') FROM insight.claim "
                 f"WHERE id = '{rc_id}'") == "agent,student,guardian|hypothesis|below"
    # 卡片的每个方向、每个偏差节点都能在判断账本（对账判断的 value）里找到（契约审查 M2）
    value = json.loads(q(db, f"SELECT value FROM insight.claim WHERE id = '{rc_id}'"))
    assert value["directions"] == row["deviation_directions"] and value["deviation_nodes"] == row["deviation_nodes"]
    assert all(s["direction"] in value["directions"] for s in row["sentences"] if "direction" in s)
    async with running_app(db, verifier=VERIFIER, sie03=P3) as client:
        for ref in (score, plan):
            r = await call(client, "insight.reconcile", {"context": agent_ctx(S4), "exam_record_id": ref})
            assert r.status_code == 200, r.text
            out = r.json()
            assert C.validate("InsightReconcileResponse", out) == []
            res = out["result"]
            assert res["within_interval"] is False and res["actual_score"] == 0 and res["deviation_directions"] == ["diagnosis_optimistic"]
            assert res["adjustments"] == ["claims_to_verify"] and res["deviation_nodes"] == [NODE_A]
            assert [s["claim_id"] for s in res["statements"]] == [rc_id]
            assert res["statements"][0]["text"] == TEXT_RECONCILE.replace("{position}", "below")
        r = await call(client, "insight.reconcile", {"context": agent_ctx(S5), "exam_record_id": score})
        assert r.status_code == 404  # 不属于 student_ref：与不存在相同
    # 库层：卡片的句子不能引用对读者不可见的判断（预测判断只给 agent）
    ins = await asyncpg.connect(**db.connect_kwargs(APP_ROLE))
    try:
        await ins.execute("SET ROLE starbuddy_insight")
        pred = await ins.fetchrow(f"SELECT id, claim_id FROM insight.exam_prediction WHERE exam_record_id = '{plan}'")
        with pytest.raises(asyncpg.CheckViolationError):
            await ins.execute(
                """INSERT INTO insight.exam_reconciliation (student_id, prediction_id, exam_record_id, in_interval, deviation_directions,
                     adjustments, audience, sentences, algorithm_version)
                   VALUES ($1::uuid, $2, $3::uuid, false, '{time_shortage}', '{none}', '{student}', $4::jsonb, 'sie-reconcile@0.1.0')""",
                S4, pred["id"], score, json.dumps([{"claim_id": str(pred["claim_id"]), "template_key": TPL_PREDICT}]))
    finally:
        await ins.close()


async def frozen_exam(db, exam_type: str, raw: float, mastery_rows) -> tuple[str, str]:
    tomorrow = await shanghai_today(db) + timedelta(days=1)
    plan = await plan_exam(db, exam_type, tomorrow, 100, [NODE_A, NODE_B])
    async with Ops(db) as ops:
        await ops.predict(S4, {"subject": "aya", "exam_type": exam_type, "exam_date": tomorrow.isoformat(), "full_score": 100,
                               "scope_node_ids": [NODE_A, NODE_B]}, mastery_rows, P3)
    assert await freeze_today(db, plan) == 1
    return plan, await score_exam(db, plan, raw)


async def test_reconcile_blocks_without_direction_or_on_forbidden_text(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    rows = [mr(0, NODE_A, "execute", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now)]
    await sync(db, rows)
    counts = "SELECT (SELECT count(*) FROM insight.claim WHERE student_id = '{s}') || '|' || " \
             "(SELECT count(*) FROM insight.exam_reconciliation WHERE student_id = '{s}')".format(s=S4)
    _plan, above = await frozen_exam(db, "monthly", 100, rows)
    before = q(db, counts)
    async with Ops(db) as ops:
        out = await ops.reconcile_exam(S4, above, P3, forbidden_matcher(), node_results={NODE_A: 1.0})
        assert out.blocked == "no_direction_for_above_interval" and out.card_id is None
        assert q(db, counts) == before  # 整笔回滚：没有新判断、没有卡、偏差节点的判断没动
        ok = await ops.reconcile_exam(S4, above, P3, forbidden_matcher(), directions=["item_type_transfer"])
        assert ok.card_id and ok.adjustments == ("none",)
        # 禁用推断 0：对账判断的模板渲染命中词表 → 不出卡、整笔回滚
        d = json.loads(P3_PATH.read_text(encoding="utf-8"))
        d["exam"]["templates"]["reconcile"]["key"] = TPL_RECONCILE_BAD
        bad_cfg = parse_sie03(d, env="test")
        _p2, below = await frozen_exam(db, "midterm", 0, rows)
        before = q(db, counts)
        out = await ops.reconcile_exam(S4, below, bad_cfg, forbidden_matcher(), node_results={NODE_A: 0.0})
        assert out.blocked == "render_blocked" and q(db, counts) == before
        assert q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S4}' AND status = 'to_verify'") == "0"
    fm = forbidden_matcher()
    assert fm(TEXT_PREDICT) == [] and fm(TEXT_RECONCILE) == [] and fm(TEXT_RECONCILE_BAD) != []


# ---------- 触发规则 ----------
async def test_triggers_suggest_only_and_dispatch_needs_signoff(sie03db):
    db = sie03db
    reset(db)
    db.psql(f"SET session_replication_role = replica; DELETE FROM insight.intervention_log WHERE student_id = '{S3}';")
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now, retest=now + timedelta(days=1))])
    # 由假设升为成立（证据够、策略已签字）：证据化鼓励的依据
    await sync(db, [mr(0, NODE_A, "stable", (0, 1, 2, 3, 4, 5), now, conf=0.8), mr(1, NODE_B, "execute", (2, 3), now, retest=now + timedelta(days=1))], PS)
    a, b = claim_of(db, NODE_A), claim_of(db, NODE_B)
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{a}'") == "established"
    async with Ops(db) as ops:
        got = await ops.evaluate_triggers(S4, P3)
        keys = {(s.rule_key, s.action, s.claim_ids) for _id, s in got}
        assert ("reminder.review_due", "plan_review_node", (b,)) in keys
        assert ("encourage.progress", "encourage_with_evidence", (a,)) in keys
        assert not any(k[0].startswith("reduce_load") for k in keys)  # S4 没有中圈指标判断：不降负
        assert await ops.evaluate_triggers(S4, P3) == []  # 去重
        assert q(db, f"SELECT string_agg(DISTINCT status || '@' || params_version, ',') FROM insight.intervention_log "
                     f"WHERE student_id = '{S4}'") == "suggested@syn-triggers@0.1.0"
        # 未签字：一律不下发
        with pytest.raises(Unavailable):
            await ops.dispatch_triggers(S4, P3)
        d = json.loads(P3_PATH.read_text(encoding="utf-8"))
        d.update(synthetic=False, signed_by="test-signer", signed_at="2026-09-30")
        signed = parse_sie03(d, env="test")
        # 配置已签字但库里没有签字登记：库层拒绝，整笔回滚
        with pytest.raises(Unavailable):
            await ops.dispatch_triggers(S4, signed)
        assert q(db, f"SELECT count(*) FROM insight.intervention_log WHERE student_id = '{S4}' AND status = 'dispatched'") == "0"
        db.psql("INSERT INTO insight.intervention_rule_signoff (rule_key, params_version, signed_by, signed_at) VALUES "
                "('reminder.review_due', 'syn-triggers@0.1.0', 'test-signer', now()), "
                "('encourage.progress', 'syn-triggers@0.1.0', 'test-signer', now()) ON CONFLICT DO NOTHING;")
        with pytest.raises(Unavailable):
            await ops.dispatch_triggers(S4, P3)  # 库里有签字登记，但配置未签字：服务这一道照样不下发
        assert q(db, f"SELECT count(*) FROM insight.intervention_log WHERE student_id = '{S4}' AND status = 'dispatched'") == "0"
        # 下发时再核判断：鼓励引用的判断已转待验证 → 库层拒绝
        db.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '{a}';")
        with pytest.raises(Unavailable):
            await ops.dispatch_triggers(S4, signed)
        db.psql(f"SET session_replication_role = replica; DELETE FROM insight.intervention_log WHERE student_id = '{S4}' "
                "AND rule_key = 'encourage.progress';")
        assert await ops.dispatch_triggers(S4, signed) == 1
        assert q(db, f"SELECT status FROM insight.intervention_log WHERE student_id = '{S4}'") == "dispatched"
        # 降负：S3 有中圈指标判断；最近一周“连错后退出”多于上一周 → 缩短、插熟悉题、询问原因
        if not q(db, f"SELECT 1 FROM insight.claim WHERE student_id = '{S3}' AND target_type = 'metric' LIMIT 1"):
            pool = await create_pool(**db.connect_kwargs(APP_ROLE))
            try:
                tags = {("aya", f"syn-item-{i:03d}"): ("MATH.SYN.EQ.LINEAR", "apply") for i in range(1, 27)}
                await InsightStore(pool).recompute_metrics(S3, P, forbidden_matcher(), tags)
            finally:
                await pool.close()
        db.psql(f"""INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key) VALUES
          ('{S3}', 'exit_after_errors', '{{}}', now() - interval '1 day', 'orchestrator', gen_random_uuid()),
          ('{S3}', 'exit_after_errors', '{{}}', now() - interval '2 days', 'orchestrator', gen_random_uuid());""")
        try:
            got = await ops.evaluate_triggers(S3, P3)
            reduce = [s for _id, s in got if s.rule_key == "reduce_load.exit_after_errors"]
            assert {s.action for s in reduce} == {"shorten_task", "insert_familiar_item", "ask_reason"}
            metric_ids = set(q(db, f"SELECT string_agg(id::text, ',') FROM insight.claim WHERE student_id = '{S3}' AND target_type = 'metric' "
                                   "AND split_part(target_id, '.', 1) IN ('persistence', 'plan_completion_rate')").split(","))
            assert all(set(s.claim_ids) == metric_ids for s in reduce)
        finally:
            db.psql(f"SET session_replication_role = replica; DELETE FROM insight.behavior_event WHERE student_id = '{S3}' "
                    f"AND kind = 'exit_after_errors'; DELETE FROM insight.intervention_log WHERE student_id = '{S3}';")
    # 学科服务（编排层、今日计划）可读，BFF 不可读
    for login, ok in ((LEARNING_ROLE, True), (BFF_ROLE, False)):
        c = await asyncpg.connect(**db.connect_kwargs(login))
        try:
            if ok:
                assert await c.fetchval(f"SELECT count(*) FROM insight.intervention_log WHERE student_id = '{S4}'") == 1
            else:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.fetch("SELECT * FROM insight.intervention_log")
        finally:
            await c.close()


# ---------- 导出与删除 ----------
class DbGuardianRequests:
    """测试专用：以 BFF 角色、家长会话调用 0035 的真实库函数（生产里洞察服务没有这条路，见 guardian_requests 说明）"""

    def __init__(self, db) -> None:
        self.db = db

    async def export(self, actor_ref, link_id, student):
        c = await bff(self.db, actor_ref)
        try:
            pkg = await c.fetchval("SELECT platform.export_student_data($1::uuid)", student)
        except asyncpg.InsufficientPrivilegeError:
            raise PermissionError() from None
        finally:
            await c.close()
        pkg = json.loads(pkg)
        assert set(pkg["topics"]) and all(str(r.get("student_id", student)) == student for rows in pkg["topics"].values() for r in rows)
        return ticket_from_export(pkg, student)

    async def erase(self, actor_ref, link_id, student, confirmation_code):
        c = await bff(self.db, actor_ref)
        try:
            rid = await c.fetchval("SELECT platform.request_erasure($1::uuid)", student)
            await c.fetchval("SELECT platform.confirm_erasure($1::uuid)", rid)
            row = await c.fetchrow("SELECT id, status, confirmed_at, scopes FROM platform.erasure_request WHERE id = $1", rid)
        except asyncpg.InsufficientPrivilegeError:
            raise PermissionError() from None
        finally:
            await c.close()
        return ticket_from_erasure(row, P3.erase_scopes)


async def test_export_and_erase_via_library_functions(sie03db):
    db = sie03db
    reset(db, (S5,))
    now = await db_now(db)
    await sync(db, [MasteryRow(M5, S5, "aya", NODE_A, "apply", "execute", None, 0.5, (E4[0],), "kge-mastery@0.1.0.syn", now)], student=S5)
    s5_claim = claim_of(db, NODE_A, student=S5)
    async with Ops(db) as ops:
        from insight.dispute import StubVerificationScheduler
        await ops.raise_dispute(S5, s5_claim, "one_off_slip", "student", SA5, StubVerificationScheduler(), P3)
    s4_claims = q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S4}'")
    g = DbGuardianRequests(db)
    async with running_app(db, verifier=VERIFIER, sie03=P3, guardian=g) as client:
        r = await call(client, "insight.export", {"context": gua("syn-export-key-0001", S5, GC, LINK_C5)}, "bff")
        assert r.status_code == 200, r.text
        assert C.validate("InsightExportResponse", r.json()) == [] and r.json()["result"]["status"] == "ready"
        r = await call(client, "insight.export", {"context": gua("syn-export-key-0002", S5, GB, LINK_C5)}, "bff")
        assert r.status_code == 404  # 没有绑定的家长
        body = {"context": gua("syn-erase-key-0001", S5, GC, LINK_C5), "confirmation_code": "SYNTH001"}
        r = await call(client, "insight.erase", body, "bff")
        assert r.status_code == 200, r.text
        out = r.json()
        assert C.validate("InsightEraseResponse", out) == [] and out["result"]["status"] == "scheduled"
        registry = q(db, "SELECT string_agg(DISTINCT erase_scope, ',' ORDER BY erase_scope) FROM platform.personal_table_registry "
                         "WHERE strategy = 'delete'")
        assert sorted(out["result"]["scopes"]) == registry.split(",") == sorted(P3.erase_scopes)
        retry = copy.deepcopy(body)
        retry["context"]["request_id"] = str(uuid.uuid4())
        r = await call(client, "insight.erase", retry, "bff")
        assert r.json() == out
    rid = out["result"]["erase_receipt_id"]
    # 冷静期满（超级用户 replica 改时间，只为夹具）→ 调度器执行
    db.psql(f"SET session_replication_role = replica; UPDATE platform.erasure_request SET requested_at = now() - interval '9 days', "
            f"confirmed_at = now() - interval '8 days', cooling_off_until = now() - interval '1 day' WHERE id = '{rid}';")
    conn = await connect_scheduler(**db.connect_kwargs(SCHEDULER_ROLE))
    try:
        assert await execute_due_erasures(conn) == 1
    finally:
        await conn.close()
    pg = await asyncpg.connect(**db.connect_kwargs("postgres"))
    try:
        row = await pg.fetchrow("SELECT id, status, confirmed_at, scopes FROM platform.erasure_request WHERE id = $1::uuid", rid)
    finally:
        await pg.close()
    done = ticket_from_erasure(row, P3.erase_scopes)
    assert done.status == "completed" and sorted(done.scopes) == registry.split(",")
    for sql in (f"SELECT count(*) FROM insight.claim WHERE student_id = '{S5}'", f"SELECT count(*) FROM insight.dispute WHERE student_id = '{S5}'",
                f"SELECT count(*) FROM insight.claim_history h WHERE h.claim_id = '{s5_claim}'"):
        assert q(db, sql) == "0"
    assert q(db, f"SELECT status FROM platform.account WHERE id = '{SA5}'") == "disabled"
    assert q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S4}'") == s4_claims  # 别的学生不受影响



# ---------- 安全审查 F2、F4、F6 ----------
async def test_dispute_actor_checks_default_verifier_and_cache(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    await sync(db, [mr(0, NODE_A, "execute", (0, 1), now)])
    cid = claim_of(db, NODE_A)
    body = dispute_body(stu("syn-dispute-cache-0001"), cid, "one_off_slip")
    async with running_app(db, sie03=P3) as client:  # 缺省授权核对：一律不通过
        r = await call(client, "insight.dispute", body, "bff")
        assert r.status_code == 404
    v = StaticVerifier({(SA4, "student", S4)})
    async with running_app(db, verifier=v, sie03=P3) as client:
        r = await call(client, "insight.dispute", body, "bff")
        assert r.status_code == 200
        v.allowed.clear()  # 授权被撤销：同 key 同体的重试也要先重新核对 actor，不能直接回缓存（F6）
        retry = copy.deepcopy(body)
        retry["context"]["request_id"] = str(uuid.uuid4())
        r = await call(client, "insight.dispute", retry, "bff")
        assert r.status_code == 404 and "detail" not in r.json()
    # 非 test 环境没有注入真实验证任务接口：i_know 失败即关闭（501），不写任何东西；其他两路照常（F2）
    from insight.service.tools import ToolCore
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    try:
        core = ToolCore(InsightStore(pool), C, P, forbidden_matcher(), VERIFIER, sie03=P3, ops=Sie03Store(pool))
        status, out = await core.call("insight.dispute", dispute_body(stu("syn-dispute-nosch-0001"), cid, "i_know"), "bff")
        assert status == 501 and out["code"] == "METHOD_NOT_IMPLEMENTED"
    finally:
        await pool.close()
    assert q(db, f"SELECT count(*) FROM insight.dispute WHERE kind = 'i_know' AND student_id = '{S4}'") == "0"
    assert q(db, f"SELECT status FROM insight.claim WHERE id = '{cid}'") == "hypothesis"


# ---------- 安全审查 F1、契约审查 M3 与 6 ----------
async def test_predict_visibility_final_claim_and_current_basis(sie03db):
    db = sie03db
    reset(db)
    now = await db_now(db)
    tomorrow = await shanghai_today(db) + timedelta(days=1)
    plan = await plan_exam(db, "monthly", tomorrow, 100, [NODE_A, NODE_B])
    bogus = mr(0, NODE_A, "retain", (10, 11, 12), now, basis_id=str(uuid.uuid4()))  # 不是库里的当前掌握行
    rows = [mr(0, NODE_A, "stable", (0, 1), now), mr(1, NODE_B, "execute", (2, 3), now), bogus]
    exam = {"subject": "aya", "exam_type": "monthly", "exam_date": tomorrow.isoformat(), "full_score": 100,
            "scope_node_ids": [NODE_A, NODE_B]}
    d = json.loads(P3_PATH.read_text(encoding="utf-8"))
    d["exam"]["templates"]["prediction"]["key"] = TPL_PREDICT_AGENT
    d["exam"]["prediction_visibility"] = ["student"]
    no_vis = parse_sie03(d, env="test")
    async with Ops(db) as ops:
        with pytest.raises(Unavailable):  # 配置与模板上限没有交集：不回退（F1）
            await ops.predict(S4, exam, rows, no_vis)
        assert q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S4}' AND target_type = 'exam'") == "0"
        out = await ops.predict(S4, exam, rows, P3)
        assert out.evidence_count == 4 and E4[10] not in out.evidence_ids  # 只用当前掌握行（6）
        # 预测挂的判断已终结：再预测回 409，不另建判断（M3）
        db.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '{out.claim_id}';"
                f"UPDATE insight.claim SET status = 'refuted', change_reason = 'verification_refuted' WHERE id = '{out.claim_id}';")
        from insight.store_sie03 import Conflict
        with pytest.raises(Conflict):
            await ops.predict(S4, exam, rows[:2], P3)
    assert q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S4}' AND target_type = 'exam'") == "1"
    assert q(db, f"SELECT claim_id FROM insight.exam_prediction WHERE exam_record_id = '{plan}'") == out.claim_id
