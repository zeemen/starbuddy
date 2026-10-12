"""月度计划的库上测试（一次性 Postgres，全部迁移含 0054 草稿；合成数据，规则 8）：
- 生成落库：本月一份快照，每个优先知识点引用存在且成立 / 假设的“待点亮”判断；同一学生同一月份第二次调用不写新行（exists）；
- 权益：当前主订阅的 weekly_report 取值含 monthly 才生成（Pro、Max）；基础档与没有订阅不生成、不写行；已有快照先返回，再查权益；
- 诉求：只读当前有效绑定家长的当前版（已解绑家长写的诉求不进计划），不读补充说明与学校；
- 上月回顾：上月快照的计划节点里、按 claim_history 在上个月进入“迁移”的才算点亮；
- 可见范围：学生视图只有学习计划（没有家长注意事项、回顾与诉求），家长视图三段、分差只给本人当前版诉求对应的项、5 分一档的区间；
  别家一律 NULL；引用的判断事后被撤回、诉求换了新版，视图读时不再给这一项；
- 重放：按快照的 input_refs 取回输入，逐字节重建同一份快照；
- 内部接口 POST /internal/v1/monthly-plan/generate：鉴权、BFF 的 actor 库内再核、统一的“不可用”回答、错误码与响应形状
  （services/insight/openapi/monthly-plan-internal.v0.yaml）。
本文件自建一户（两位家长：一位有效、一位已解除绑定）与另一户，不改 pginsight 的 S1–S3。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import asyncpg
import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from insight import monthly as M
from insight.claims import MasteryRow
from insight.shared import forbidden_matcher
from insight.store import InsightStore, create_pool
from insight.store_monthly import MonthlyError, generate_monthly_plan, read_plan, replay_inputs
from pginsight import APP_ROLE, BFF_ROLE, E
from support import auth, make_policy, running_app

pytestmark = pytest.mark.anyio
POLICY = make_policy()
CFG = M.synthetic_monthly_config()
TPL = M.load_templates(env="test")
FM = forbidden_matcher()
OPENAPI = Path(__file__).resolve().parents[1] / "openapi" / "monthly-plan-internal.v0.yaml"

GM, GMR, GN = "9a540000-0000-4000-8000-0000000000a1", "9a540000-0000-4000-8000-0000000000a2", "9a540000-0000-4000-8000-0000000000a3"
AM, AN, AZ = "9a540000-0000-4000-8000-0000000000b1", "9a540000-0000-4000-8000-0000000000b2", "9a540000-0000-4000-8000-0000000000b3"
HM, HN = "9a540000-0000-4000-8000-0000000000d1", "9a540000-0000-4000-8000-0000000000d2"
SM, SN, SZ = "9a540000-0000-4000-8000-0000000000c1", "9a540000-0000-4000-8000-0000000000c2", "9a540000-0000-4000-8000-0000000000c3"
MB = [f"9a540000-0000-4000-8000-00000000{i:04d}" for i in range(1, 5)]
REC = "9a540000-0000-4000-8000-0000000000e1"
GOAL_M, GOAL_R = "9a540000-0000-4000-8000-0000000000f1", "9a540000-0000-4000-8000-0000000000f2"
EXAM_CLAIM = "9a540000-0000-4000-8000-0000000000f9"
NA, NB, NC = "MATH.SYN.MP.ALPHA", "MATH.SYN.MP.BETA", "MATH.SYN.MP.GAMMA"

SEED = f"""
INSERT INTO platform.account (id, kind) VALUES ('{GM}', 'guardian'), ('{GMR}', 'guardian'), ('{GN}', 'guardian'),
  ('{AM}', 'student'), ('{AN}', 'student'), ('{AZ}', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES ('{HM}', '{GM}'), ('{HN}', '{GN}');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('{SM}', '{AM}', '{HM}', 8, 'nanjing', 'bubian-2024', '2013-03-01'),
  ('{SN}', '{AN}', '{HN}', 8, 'nanjing', 'bubian-2024', '2013-04-01'),
  ('{SZ}', '{AZ}', '{HN}', 8, 'nanjing', 'bubian-2024', '2013-05-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('{GM}', '{SM}', 'household_creator'), ('{GMR}', '{SM}', 'invite'), ('{GN}', '{SN}', 'household_creator'), ('{GN}', '{SZ}', 'household_creator');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = '{GMR}';
-- 已发布的测试额度配置：照抄草稿 v12-pilot@1（Pro、Max 的 weekly_report 是 full_and_monthly，即有月度计划权益；基础档没有）
INSERT INTO platform.plan_config (version, note_code) VALUES ('monthly-test@1', 'monthly_plan_test');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
  SELECT 'monthly-test@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
  SELECT 'monthly-test@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'v12-pilot@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'monthly-test@1';
-- SM：专业档（试点）；SN：基础档；SZ：没有订阅
INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end) VALUES
  ('{HM}', '{SM}', 'pro', 'monthly-test@1', 'pilot_virtual', now() - interval '1 day', now() + interval '29 days'),
  ('{HN}', '{SN}', 'basic', 'monthly-test@1', 'pilot_virtual', now() - interval '1 day', now() + interval '29 days');
-- 诉求：有效家长 GM 关注数学、数学目标 105（满分 120）；已解除绑定的 GMR 写过关注语文与语文目标（不得进入计划）
INSERT INTO insight.guardian_goal (id, student_id, guardian_account_id, version, focus_subjects, target_scores, note) VALUES
  ('{GOAL_M}', '{SM}', '{GM}', 1, '{{aya}}', '[{{"subject": "aya", "full_score": 120, "target_score": 105}}]', '合成说明：只回显给作者'),
  ('{GOAL_R}', '{SM}', '{GMR}', 1, '{{wenli}}', '[{{"subject": "wenli", "full_score": 120, "target_score": 100}}]', NULL);
-- 最近一次数学成绩（学生自录）与本月一场考试安排；考试类判断模板与一条引用那条成绩的对账判断（对家长可见）
INSERT INTO insight.exam_record (id, student_id, subject, exam_type, exam_date, full_score, raw_score, entered_by_role, entered_by_account_id, source_type)
VALUES ('{REC}', '{SM}', 'aya', 'midterm', (now() AT TIME ZONE 'Asia/Shanghai')::date - 3, 120, 92.5, 'self', '{AM}', 'self');
INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, scope_node_ids, entered_by_role, entered_by_account_id, source_type)
VALUES ('{SM}', 'aya', 'monthly', (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') + interval '1 month - 1 day')::date, 100, NULL,
        ARRAY['{NA}'], 'self', '{AM}', 'self');
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('syn.exam.reconcile', 1, 'exam', '{{agent,student,guardian}}', '60 days', '【占位模板·未审核】对账｜{{position}}', 'approved', 'test-reviewer', now())
ON CONFLICT DO NOTHING;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, evidence_ids,
                           source_type, model_version, status, change_reason, visibility, expires_at)
VALUES ('{EXAM_CLAIM}', '{SM}', 'aya', 'exam', '{REC}', 'syn.exam.reconcile', 1, '{{"position": "below"}}', 1, '{{{E[0]}}}', 'inferred',
        'algo:sie-reconcile@0.1.0', 'hypothesis', 'created', '{{agent,student,guardian}}', now() + interval '20 days');
SET session_replication_role = replica;
-- 掌握依据行（技能键，只为满足判断“依据属于同一学生”）与复测任务：本月到期两条（一条逾期按月初计）、上月完成一条、下月一条
INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version, evidence_ids) VALUES
  ('{MB[0]}', '{SM}', 'aya', 'SYN-MP-01', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E[0]}}}'),
  ('{MB[1]}', '{SM}', 'aya', 'SYN-MP-02', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[1]}}}'),
  ('{MB[2]}', '{SM}', 'aya', 'SYN-MP-03', 'transfer', 0.9, 0.9, 'kge-mastery@0.1.0.syn', '{{{E[2]}}}'),
  ('{MB[3]}', '{SN}', 'aya', 'SYN-MP-04', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E[3]}}}');
INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at, status, closed_at, closing_attempt_id) VALUES
  ('{SM}', 'aya', '{NA}', 'apply', 'decay', date_trunc('month', now()) - interval '2 days', 'open', NULL, NULL),
  ('{SM}', 'aya', '{NB}', 'apply', 'decay', now() + interval '1 hour', 'open', NULL, NULL),
  ('{SM}', 'aya', '{NB}', 'know', 'hint_followup', (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') - interval '10 days') AT TIME ZONE 'Asia/Shanghai',
   'passed', (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') - interval '9 days') AT TIME ZONE 'Asia/Shanghai', gen_random_uuid()),
  ('{SM}', 'aya', '{NC}', 'apply', 'decay', now() + interval '45 days', 'open', NULL, NULL);
SET session_replication_role = origin;
"""


class StaticMastery:
    def __init__(self, rows: Sequence[MasteryRow]) -> None:
        self._rows = tuple(rows)

    async def all_rows(self, student: str) -> Sequence[MasteryRow]:
        return tuple(r for r in self._rows if r.student_id == student)

    async def rows(self, student: str, subject: str, node_ids: Sequence[str]) -> Sequence[MasteryRow]:
        return tuple(r for r in self._rows if r.student_id == student and r.subject == subject and r.node_id in node_ids)

    async def by_ids(self, student: str, ids: Sequence[str]) -> Sequence[MasteryRow]:
        return tuple(r for r in self._rows if r.student_id == student and r.basis_id in set(ids))


@pytest.fixture(scope="module")
def mdb(insightdb):
    insightdb.psql(SEED)
    yield insightdb
    insightdb.psql(f"""SET session_replication_role = replica;
        DELETE FROM insight.monthly_plan WHERE student_id IN ('{SM}', '{SN}', '{SZ}');
        DELETE FROM insight.claim WHERE student_id IN ('{SM}', '{SN}', '{SZ}');""")


def mrows(now) -> list[MasteryRow]:
    def r(i: int, node: str, gate: str, ev: int, student: str = SM) -> MasteryRow:
        return MasteryRow(MB[i], student, "aya", node, "apply", gate, 0.6, 0.5, (E[ev],), "kge-mastery@0.1.0.syn", now)
    return [r(0, NA, "execute", 0), r(1, NB, "stable", 1), r(2, NC, "transfer", 2), r(3, NA, "execute", 3, SN)]


async def su(db) -> asyncpg.Connection:
    return await asyncpg.connect(**db.connect_kwargs("postgres"))


async def setup(db) -> tuple[InsightStore, StaticMastery]:
    """清掉本文件的快照与节点判断，按合成掌握行重新生成判断"""
    db.psql(f"""SET session_replication_role = replica;
        DELETE FROM insight.monthly_plan WHERE student_id IN ('{SM}', '{SN}', '{SZ}');
        DELETE FROM insight.claim WHERE student_id IN ('{SM}', '{SN}', '{SZ}') AND target_type = 'node';""")
    pool = await create_pool(**db.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    c = await su(db)
    try:
        now = await c.fetchval("SELECT now()")
    finally:
        await c.close()
    rows = mrows(now)
    await store.sync_mastery_claims(SM, [r for r in rows if r.student_id == SM], POLICY)
    await store.sync_mastery_claims(SN, [r for r in rows if r.student_id == SN], POLICY)
    return store, StaticMastery(rows)


async def this_month(db) -> date:
    c = await su(db)
    try:
        return await c.fetchval("SELECT date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date")
    finally:
        await c.close()


async def gen(store, mastery, student, month):
    return await generate_monthly_plan(store, student, month, POLICY, CFG, TPL, FM, mastery)


async def as_bff(db, account: str, sql: str, *args):
    c = await asyncpg.connect(**db.connect_kwargs(BFF_ROLE))
    try:
        await c.execute("SELECT set_config('app.account_id', $1, false)", account)
        v = await c.fetchval(sql, *args)
        return None if v is None else json.loads(v)
    finally:
        await c.close()


async def count_plans(db, student: str) -> int:
    c = await su(db)
    try:
        return await c.fetchval("SELECT count(*) FROM insight.monthly_plan WHERE student_id = $1::uuid", student)
    finally:
        await c.close()


async def test_generate_once_cites_claims_and_is_idempotent(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        first = await gen(store, mastery, SM, month)
        assert first.status == "generated" and first.month == month and len(first.inputs_digest) == 64
        again = await gen(store, mastery, SM, month)
        assert again.status == "exists" and again.plan_id == first.plan_id and again.inputs_digest == first.inputs_digest
        assert await count_plans(mdb, SM) == 1  # 第二次调用不写新行
        plan = await read_plan(store, SM, month)
    finally:
        await store.pool.close()
    pn = plan["student_plan"]["priority_nodes"]
    assert [n["node_id"] for n in pn] == [NA, NB]  # NC 已进入“迁移”，不是候选
    c = await su(mdb)
    try:
        for n in pn:
            row = await c.fetchrow("SELECT status::text AS s, target_id, value FROM insight.claim WHERE id = $1::uuid", n["claim_id"])
            assert row is not None and row["s"] in ("hypothesis", "established") and row["target_id"] == n["node_id"]
            assert json.loads(row["value"])["gate"] not in ("transfer", "retain")
    finally:
        await c.close()
    sp = plan["student_plan"]
    assert sp["retests"]["count"] == 2 and sp["retests"]["dates"][0] == month.isoformat()  # 逾期的按月初计；下月到期的不算
    assert sp["weekly_rhythm"]["minutes_per_session"] == 30
    assert [n["key"] for n in plan["guardian_notes"]] == ["quiet_time_for_retests", "exam_month_routine", "mini_review_dates"]
    gaps = plan["review"]["score_gaps"]
    assert [(g["subject"], g["goal_id"], g["exam_record_id"], g["claim_id"]) for g in gaps] == [("aya", GOAL_M, REC, EXAM_CLAIM)]
    assert "92.5" not in json.dumps(plan["review"]) and "105" not in json.dumps(plan["review"])  # 不存成绩与目标分的值
    assert plan["review"]["last_month"] is None
    assert plan["policy_weights"] == "default" and plan["algorithm_version"] == M.ALGORITHM_VERSION
    refs = plan["input_refs"]
    assert refs["goals"] == [GOAL_M] and refs["exam_records"] == [REC] and len(refs["exam_plans"]) == 1 and len(refs["retest_tasks"]) == 2
    assert refs["fingerprints"] == M.fingerprints(POLICY, CFG, TPL)


async def test_replay_from_stored_refs_is_byte_identical(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        await gen(store, mastery, SM, month)
        # 生成之后判断又有了新版本（历史多一行、置信度变了）：重放按快照登记的历史版本取回，不受影响（只为夹具，复制模式直接写）
        mdb.psql(f"""SET session_replication_role = replica;
            INSERT INTO insight.claim_history (claim_id, from_status, to_status, reason, visibility, value, confidence, evidence_ids, basis_kind,
                                               basis_id, model_version, expires_at, at)
            SELECT h.claim_id, h.from_status, h.to_status, h.reason, h.visibility, h.value, h.confidence / 2, h.evidence_ids, h.basis_kind,
                   h.basis_id, h.model_version, h.expires_at, now() + interval '1 second'
              FROM insight.claim_history h JOIN insight.claim c ON c.id = h.claim_id WHERE c.student_id = '{SM}';""")
        inputs, row = await replay_inputs(store, SM, month, mastery.by_ids)
    finally:
        await store.pool.close()
    again = M.build_monthly_plan(inputs, POLICY, CFG, TPL, FM)
    for k in ("student_plan", "guardian_notes", "review", "plan_basis", "input_refs", "inputs_digest", "config_version", "template_version",
              "policy_weights", "algorithm_version"):
        assert M.to_json(json.loads(json.dumps(again[k]))) == M.to_json(row[k]), k


async def test_last_month_lit_uses_claim_history_and_last_plan(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    prev = M.previous_month(month)
    # 上月快照（计划里有 NC、NA）与 NC 的迁移判断按历史在上月进入“迁移”：以超级用户在复制模式下造出“过去”的状态（只为夹具）
    prev_plan = {"priority_nodes": [{"position": 1, "subject": "aya", "node_id": NC, "cognitive_level": "apply", "claim_id": EXAM_CLAIM,
                                     "template_key": "syn.math.node_mastery", "template_version": 1,
                                     "copy_key": "monthly_plan.student.priority_node"},
                                    {"position": 2, "subject": "aya", "node_id": NA, "cognitive_level": "apply", "claim_id": EXAM_CLAIM,
                                     "template_key": "syn.math.node_mastery", "template_version": 1,
                                     "copy_key": "monthly_plan.student.priority_node"}],
                 "weekly_rhythm": {"sessions_per_week": 5, "minutes_per_session": 30, "copy_key": "monthly_plan.student.weekly_rhythm"},
                 "retests": {"count": 0, "dates": [], "by_subject": [], "copy_key": "monthly_plan.student.retests"}, "mini_reviews": []}
    refs = {"as_of": "2026-01-01T00:00:00+08:00", "claims": [], "mastery": [], "exam_records": [], "exam_plans": [], "goals": [],
            "retest_tasks": [], "prev_plan_id": None, "fingerprints": M.fingerprints(POLICY, CFG, TPL)}
    mdb.psql(f"""SET session_replication_role = replica;
        INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version,
                                          config_version, template_version, policy_weights, inputs_digest)
        VALUES ('{SM}', '{prev}', '{json.dumps(prev_plan)}', '[]', '{{"last_month": null, "score_gaps": []}}', '{{"items": []}}',
                '{json.dumps(refs)}', 'sie-monthly@0.1.0', 'syn', 'syn', 'default', '{"a" * 64}');
        UPDATE insight.claim_history h SET at = ('{prev}'::date + 10)::timestamp AT TIME ZONE 'Asia/Shanghai'
          FROM insight.claim c WHERE c.id = h.claim_id AND c.student_id = '{SM}' AND c.target_id = '{NC}';""")
    try:
        await gen(store, mastery, SM, month)
        plan = await read_plan(store, SM, month)
    finally:
        await store.pool.close()
    lm = plan["review"]["last_month"]
    assert lm["month"] == prev.isoformat() and lm["planned_nodes"] == 2 and set(lm) == {"month", "planned_nodes", "lit", "copy_key"}
    assert [x["node_id"] for x in lm["lit"]] == [NC] and lm["lit"][0]["template_key"] == POLICY.templates["node_transfer"].key
    g = await as_bff(mdb, GM, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month)
    assert [x["node_id"] for x in g["review"]["last_month"]["lit"]] == [NC]
    mdb.psql(f"SET session_replication_role = replica; DELETE FROM insight.monthly_plan WHERE student_id = '{SM}' AND month = '{prev}';")


async def test_revoked_guardian_goal_and_note_never_read(mdb):
    store, _ = await setup(mdb)
    month = await this_month(mdb)
    try:
        async with store.pool.acquire() as conn:
            facts = await conn.fetchval("SELECT insight.monthly_plan_facts($1::uuid, $2::date)", SM, month)
    finally:
        await store.pool.close()
    assert facts["entitled"] is True
    assert [g["goal_id"] for g in facts["goals"]] == [GOAL_M]  # 已解除绑定家长的诉求不读
    assert set(facts["goals"][0]) == {"goal_id", "focus_subjects", "target_scores"}  # 不给补充说明、学校、账号
    assert "合成说明" not in json.dumps(facts, ensure_ascii=False)
    assert set(facts) == {"entitled", "goals", "retests_due"} and len(facts["retests_due"]) == 2
    assert all(set(r) == {"task_id", "subject", "node_id", "date"} for r in facts["retests_due"])


async def test_not_entitled_writes_nothing(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        for student in (SN, SZ):  # 基础档、没有订阅
            with pytest.raises(MonthlyError) as e:
                await gen(store, mastery, student, month)
            assert e.value.kind == "unavailable"
            assert await count_plans(mdb, student) == 0
        with pytest.raises(MonthlyError) as e:
            await gen(store, mastery, SM, month - timedelta(days=1))
        assert e.value.kind == "month_not_current"
        # 已有快照先返回，再查权益：快照写下之后订阅降为基础档，再调仍返回原快照（不写新行）
        await gen(store, mastery, SM, month)
        mdb.psql(f"UPDATE platform.subscription SET tier = 'basic' WHERE student_id = '{SM}'")
        try:
            assert (await gen(store, mastery, SM, month)).status == "exists"
            async with store.pool.acquire() as conn:
                again = await conn.fetchval("SELECT insight.save_monthly_plan($1::uuid, $2::date, $3::jsonb)", SM, month,
                                            M.build_monthly_plan(M.MonthlyInputs(student=SM, month=month, as_of=await su_now(mdb)),
                                                                 POLICY, CFG, TPL, FM))
            assert again["created"] is False
        finally:
            mdb.psql(f"UPDATE platform.subscription SET tier = 'pro' WHERE student_id = '{SM}'")
        # 绕过服务直接调写入函数：库里同样拒绝（55000 PLAN_NOT_ENTITLED）
        plan = M.build_monthly_plan(M.MonthlyInputs(student=SN, month=month, as_of=(await su_now(mdb))), POLICY, CFG, TPL, FM)
        async with store.pool.acquire() as conn:
            with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="PLAN_NOT_ENTITLED"):
                await conn.fetchval("SELECT insight.save_monthly_plan($1::uuid, $2::date, $3::jsonb)", SN, month, plan)
    finally:
        await store.pool.close()
    assert await count_plans(mdb, SN) == 0


async def su_now(db):
    c = await su(db)
    try:
        return await c.fetchval("SELECT now()")
    finally:
        await c.close()


async def test_views_by_audience(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        await gen(store, mastery, SM, month)
    finally:
        await store.pool.close()
    g = await as_bff(mdb, GM, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month)
    assert set(g) == {"month", "generated_at", "config_version", "template_version", "student_plan", "guardian_notes", "review"}
    assert [n["node_id"] for n in g["student_plan"]["priority_nodes"]] == [NA, NB]
    assert len(g["guardian_notes"]) == 3
    gap = g["review"]["score_gaps"]
    assert [x["gap_band"] for x in gap] == [{"reached": False, "low": 10, "high": 15}]  # 105 − 92.5：5 分一档的区间
    assert set(gap[0]) == {"subject", "exam_type", "exam_date", "gap_band", "claim_id", "template_key", "template_version", "copy_key"}
    assert "92.5" not in json.dumps(g) and "plan_basis" not in json.dumps(g) and "input_refs" not in json.dumps(g)
    s = await as_bff(mdb, AM, "SELECT insight.monthly_plan_student_view($1::uuid, $2::date)::text", SM, month)
    assert set(s) == {"month", "generated_at", "student_plan"}
    text = json.dumps(s, ensure_ascii=False)
    for word in ("guardian", "\"review\"", "score", "goal", "focus", "priority\"", "note", GOAL_M, REC):
        assert word not in text, word
    assert s["student_plan"] == g["student_plan"]
    # 学生读不到家长视图、家长读不到学生视图；别家家长、已解除绑定的家长、别人家的学生一律 NULL
    assert await as_bff(mdb, AM, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month) is None
    assert await as_bff(mdb, GM, "SELECT insight.monthly_plan_student_view($1::uuid, $2::date)::text", SM, month) is None
    for acct in (GN, GMR, AN):
        assert await as_bff(mdb, acct, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month) is None
        assert await as_bff(mdb, acct, "SELECT insight.monthly_plan_student_view($1::uuid, $2::date)::text", SM, month) is None
    # BFF 不能直读快照表
    c = await asyncpg.connect(**mdb.connect_kwargs(BFF_ROLE))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.fetch("SELECT * FROM insight.monthly_plan")
    finally:
        await c.close()


async def test_view_drops_items_whose_claim_was_withdrawn(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        await gen(store, mastery, SM, month)
        plan = await read_plan(store, SM, month)
        first = plan["student_plan"]["priority_nodes"][0]["claim_id"]
        async with store.pool.acquire() as conn:
            await conn.execute("UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = $1::uuid", first)
    finally:
        await store.pool.close()
    g = await as_bff(mdb, GM, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month)
    s = await as_bff(mdb, AM, "SELECT insight.monthly_plan_student_view($1::uuid, $2::date)::text", SM, month)
    assert [n["node_id"] for n in g["student_plan"]["priority_nodes"]] == [NB]
    assert [n["node_id"] for n in s["student_plan"]["priority_nodes"]] == [NB]
    # 诉求换了新版：旧版诉求对应的分差不再给（读时再核当前版）；恢复（以超级用户在复制模式下，只为夹具）
    mdb.psql(f"UPDATE insight.guardian_goal SET superseded_at = now() WHERE id = '{GOAL_M}'")
    try:
        g = await as_bff(mdb, GM, "SELECT insight.monthly_plan_guardian_view($1::uuid, $2::date)::text", SM, month)
        assert g["review"]["score_gaps"] == []
    finally:
        mdb.psql(f"SET session_replication_role = replica; UPDATE insight.guardian_goal SET superseded_at = NULL WHERE id = '{GOAL_M}'")


async def test_guard_rejects_claims_of_another_student(mdb):
    store, mastery = await setup(mdb)
    month = await this_month(mdb)
    try:
        other = await store.visible_claims(SN, "student", target_type="node")
        plan = M.build_monthly_plan(M.MonthlyInputs(student=SM, month=month, as_of=await su_now(mdb)), POLICY, CFG, TPL, FM)
        plan["student_plan"]["priority_nodes"] = [{"position": 1, "subject": "aya", "node_id": NA, "cognitive_level": "apply",
                                                   "claim_id": other[0].row.id, "template_key": other[0].row.template_key,
                                                   "template_version": 1, "copy_key": "monthly_plan.student.priority_node"}]
        async with store.pool.acquire() as conn:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.fetchval("SELECT insight.save_monthly_plan($1::uuid, $2::date, $3::jsonb)", SM, month, plan)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch("SELECT * FROM insight.monthly_plan")  # 洞察服务也只经函数读写
    finally:
        await store.pool.close()
    assert await count_plans(mdb, SM) == 0


# ---------------------------------------------------------------- 内部接口

def schema(name: str) -> Draft202012Validator:
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": doc["components"]}, format_checker=FormatChecker())


async def test_internal_route(mdb):
    store, mastery = await setup(mdb)
    await store.pool.close()
    month = (await this_month(mdb)).strftime("%Y-%m")
    path = "/internal/v1/monthly-plan/generate"
    async with running_app(mdb, policy=POLICY, mastery=mastery) as client:
        r = await client.post(path, json={"student_id": SM, "month": month})
        assert r.status_code == 401
        r = await client.post(path, json={"student_id": SM, "month": month, "extra": 1}, headers=auth("bff"))
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
        r = await client.post(path, json={"student_id": SM.upper(), "month": month}, headers=auth("bff"))
        assert r.status_code == 400
        r = await client.post(path, content=b'{"student_id": "x", "student_id": "y"}', headers={**auth("bff"), "content-type": "application/json"})
        assert r.status_code == 400
        def g(sid: str = SM, acct: str | None = GM) -> dict:
            body = {"student_id": sid, "month": month}
            if acct:
                body["actor"] = {"role": "guardian", "actor_ref": acct}
            return body
        # 编排层不带 actor；带了 400
        r = await client.post(path, json=g(SM, GM), headers=auth("orchestrator"))
        assert r.status_code == 400
        # 不泄露存在性与套餐档位：BFF 没带 actor、actor 是别家家长 / 已解除绑定的家长 / 学生账号、档案不存在、没有权益
        # （基础档、没有订阅）——同一个状态码与正文
        unavailable = [await client.post(path, json=g(SM, None), headers=auth("bff")),
                       await client.post(path, json=g(SM, GN), headers=auth("bff")),
                       await client.post(path, json=g(SM, GMR), headers=auth("bff")),
                       await client.post(path, json={"student_id": SM, "month": month, "actor": {"role": "student", "actor_ref": AM}},
                                         headers=auth("bff")),
                       await client.post(path, json=g(SN, GN), headers=auth("bff")),
                       await client.post(path, json=g(SZ, GN), headers=auth("bff")),
                       await client.post(path, json=g("9a540000-0000-4000-8000-00000000ffff", GN), headers=auth("bff")),
                       await client.post(path, json=g(SN, None), headers=auth("orchestrator")),
                       await client.post(path, json=g("9a540000-0000-4000-8000-00000000ffff", None), headers=auth("orchestrator"))]
        assert {r.status_code for r in unavailable} == {404} and len({r.content for r in unavailable}) == 1
        assert unavailable[0].json()["code"] == "CONTENT_VERSION_NOT_FOUND"
        r = await client.post(path, json={"student_id": SM, "month": "2020-01", "actor": {"role": "guardian", "actor_ref": GM}},
                              headers=auth("bff"))
        assert r.status_code == 409 and r.json()["code"] == "MONTH_NOT_CURRENT"
        r = await client.post(path, json=g(SM, GM), headers=auth("bff"))
        assert r.status_code == 200 and r.json()["status"] == "generated"
        assert not list(schema("MonthlyPlanResult").iter_errors(r.json()))
        r2 = await client.post(path, json=g(SM, None), headers=auth("orchestrator"))
        assert r2.status_code == 200 and r2.json()["status"] == "exists" and r2.json()["plan_id"] == r.json()["plan_id"]
        assert set(r.json()) == {"status", "plan_id", "month", "generated_at", "inputs_digest"}  # 不回快照内容
        r3 = await client.post(path, json=g(SM, GN), headers=auth("bff"))
        assert r3.status_code == 404  # 已有快照也不给别家家长任何回答
    assert await count_plans(mdb, SM) == 1 and await count_plans(mdb, SN) == 0
