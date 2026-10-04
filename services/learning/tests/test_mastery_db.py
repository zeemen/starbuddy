"""KGE-04 第二步 PR 2 集成测试：学习服务读、算、写掌握记录与复测任务（specs/kge-04-mastery-step2/spec.md 第 2、6 节）。

一次性 Postgres（本模块独占一个实例，执行 bootstrap 与全部迁移 0001–0037），合成学生、合成地图版本（数学 V1；道法 Z1 → Z2 用于改版；
语文 W1 没有掌握配置）、合成题目族与合成配置（services/learning/config/mastery/synthetic-test.json，“签字”只在内存里构造）。
覆盖第一步 spec“验收标准”里依赖库表的场景：刷同题、证据等级、待复核、未准入、未签字、自述、衰减、正常路径、复测任务按时生成与对账、
退级、能力视图不点亮、地图版本、并发、重算失败不影响落库、规则 6、没有配置的学科不写；每个测试结束跑全库不变量。
测试夹具以超级用户直接写合成证据（只在一次性测试库），服务代码只以 starbuddy_learning 身份读写（规则 1、8、9）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest

from conftest import CREDENTIAL, FIXTURE_SQL, FakeAdapter, env, grade_body, settings, to_v1_body
from learning import jobs as jobs_cli
from learning.app import create_app
from learning.mastery import explain_gates, parse_config, replay, star_lit
from learning.mastery import recompute as R
from learning.mastery import store as S
from learning.mastery.config import CONFIG_DIR
from learning.mastery.evidence import Unit
from learning.mastery.jobs import mastery_sweep
from pgharness import LOGIN_ROLE, EphemeralPG

T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)   # 合成时间（在 0004 的分区范围内）
MIN, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
V1 = "5b8e1c52-0000-4000-8000-00000000a001"   # 数学合成地图（已发布）
Z1 = "5b8e1c52-0000-4000-8000-00000000b001"   # 道法合成地图：先发布，改版测试里撤下
Z2 = "5b8e1c52-0000-4000-8000-00000000b002"   # 道法改版（草稿 → 发布）
W1 = "5b8e1c52-0000-4000-8000-00000000c001"   # 语文合成地图（已发布；语文没有掌握配置）
CV_AYA = "e0000000-0000-4000-8000-0000000000e1"
CV_ZX = "e0000000-0000-4000-8000-0000000000e2"
CV_WL = "e0000000-0000-4000-8000-0000000000e3"
N1, N2, N3 = "MATH.SYN.T1.N1", "MATH.SYN.T1.N2", "MATH.SYN.T1.N3"
LEVEL = "apply"
U1 = Unit(N1, LEVEL)
ITEM_TYPES: dict[str, str] = {}   # 题型注入（来源待人确认 #4；生产默认没有题型）
NOW = [T0]

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _doc(subject: str = "aya", signed: bool = True) -> dict:
    with open(CONFIG_DIR / "synthetic-test.json", encoding="utf-8") as f:
        d = json.load(f)
    d["subject"] = subject
    if signed:
        d["signed_by"], d["signed_at"] = "synthetic-test-signer", "2026-10-01T00:00:00+08:00"
    return d


SIGNED = {"aya": parse_config(_doc("aya"), env="test"), "zhixing": parse_config(_doc("zhixing"), env="test")}
UNSIGNED = {"aya": parse_config(_doc("aya", signed=False), env="test")}

SEED_SQL = f"""
SET session_replication_role = replica;  -- 发布守卫只允许 kge.publish_map_version；一次性测试库里直接写合成行
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
  ('{V1}', 'aya', 2022, '1.0.0', 'published', '2026-09-01T00:00:00Z'),
  ('{Z1}', 'zhixing', 2022, '1.0.0', 'published', '2026-09-01T00:00:00Z'),
  ('{Z2}', 'zhixing', 2022, '1.1.0', 'draft', NULL),
  ('{W1}', 'wenli', 2022, '1.0.0', 'published', '2026-09-01T00:00:00Z');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{V1}', 'MATH.SYN', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{V1}', 'MATH.SYN.T1', 'topic', 'MATH.SYN', NULL, '（合成）主题', '{{}}'),
  ('{V1}', '{N1}', 'node', 'MATH.SYN.T1', 'kp', '（合成）节点一', '{{understand,apply}}'),
  ('{V1}', '{N2}', 'node', 'MATH.SYN.T1', 'kp', '（合成）节点二', '{{apply}}'),
  ('{W1}', 'CHN.SYN', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{W1}', 'CHN.SYN.T1', 'topic', 'CHN.SYN', NULL, '（合成）主题', '{{}}'),
  ('{W1}', 'CHN.SYN.T1.N1', 'node', 'CHN.SYN.T1', 'kp', '（合成）节点', '{{apply}}'),
  ('{Z1}', 'MORAL.SYN', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{Z1}', 'MORAL.SYN.T1', 'topic', 'MORAL.SYN', NULL, '（合成）主题', '{{}}'),
  ('{Z2}', 'MORAL.SYN', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{Z2}', 'MORAL.SYN.T1', 'topic', 'MORAL.SYN', NULL, '（合成）主题', '{{}}');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels)
  SELECT '{Z1}', 'MORAL.SYN.T1.' || n, 'node', 'MORAL.SYN.T1', 'kp', '（合成）' || n, '{{apply}}'
    FROM unnest(ARRAY['N3', 'N4', 'N5', 'N6', 'N7', 'N8']) n;
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels)
  SELECT '{Z2}', 'MORAL.SYN.T1.' || n, 'node', 'MORAL.SYN.T1', 'kp', '（合成）' || n, '{{apply}}'
    FROM unnest(ARRAY['N3R', 'N4A', 'N4B', 'N67', 'N8M']) n;
INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule) VALUES
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N3', 'MORAL.SYN.T1.N3R', 'rename'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N4', 'MORAL.SYN.T1.N4A', 'split'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N4', 'MORAL.SYN.T1.N4B', 'split'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N5', NULL, 'retire'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N6', 'MORAL.SYN.T1.N67', 'merge'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N7', 'MORAL.SYN.T1.N67', 'merge'),
  ('{Z2}', 'zhixing', 'MORAL.SYN.T1.N8', 'MORAL.SYN.T1.N8M', 'manual');
-- 不参与掌握计算的节点（方法类，不带认知层次）：挂到能力点上，它的节点键记录不能进能力视图（审查意见 7）
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{V1}', '{N3}', 'node', 'MATH.SYN.T1', 'method', '（合成）方法节点', '{{}}');
INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight) VALUES
  ('{V1}', '{N1}', 'aya', 'K-SYN-0001', 1), ('{V1}', '{N2}', 'aya', 'K-SYN-0001', 0.5), ('{V1}', '{N2}', 'aya', 'K-SYN-0002', 1),
  ('{V1}', '{N3}', 'aya', 'K-SYN-0002', 1);
SET session_replication_role = origin;
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, map_version_id) VALUES
  ('{CV_AYA}', 'nanjing', 2027, 9, 'suke-2024', 'aya', '0.1.0', '{V1}'),
  ('{CV_ZX}', 'nanjing', 2027, 9, 'syn-2024', 'zhixing', '0.1.0', '{Z1}'),
  ('{CV_WL}', 'nanjing', 2027, 9, 'syn-2024', 'wenli', '0.1.0', '{W1}');
"""


@pytest.fixture(scope="module")
def mpg():
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("LEARNING_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.start()
    db.psql(FIXTURE_SQL)
    db.psql(SEED_SQL)
    yield db
    db.stop()


@pytest.fixture(scope="module")
async def mpools(mpg):
    app_pool = await asyncpg.create_pool(**mpg.dsn(LOGIN_ROLE), min_size=1, max_size=20)
    admin = await asyncpg.create_pool(**mpg.dsn("postgres"), min_size=1, max_size=4)
    yield app_pool, admin
    await app_pool.close()
    await admin.close()


def engine(app_pool, configs=None, **kw) -> R.MasteryEngine:
    return R.MasteryEngine(app_pool, SIGNED if configs is None else configs, clock=lambda: NOW[0],
                           item_type_of=lambda s, i: ITEM_TYPES.get(i), **kw)


@pytest.fixture
async def student(mpools) -> str:
    _, admin = mpools
    acct, guardian, household, sid = (str(uuid.uuid4()) for _ in range(4))
    async with admin.acquire() as c:
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", guardian, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", household, guardian)
        await c.execute(
            """INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", sid, acct, household)
    return sid


# ---------- 全库不变量（第一步 spec 第 17 节；每个测试结束时跑） ----------
INVARIANTS = {
    "lit_record_cites_unqualified_evidence": """
      SELECT count(*) FROM platform.mastery_estimate m, unnest(m.evidence_ids) eid
       WHERE m.is_current AND m.node_id IS NOT NULL AND m.state IN ('transfer', 'retain')
         AND NOT EXISTS (SELECT 1 FROM platform.evidence_event e
                           JOIN platform.item i ON i.subject = e.subject AND i.item_id = e.item_id
                           JOIN platform.attempt a ON a.response_evidence_id = e.evidence_id
                           JOIN platform.grade_result g ON g.attempt_id = a.attempt_id AND g.superseded_by IS NULL
                          WHERE e.evidence_id = eid AND e.evidence_level = 'A' AND e.admission = 'admitted'
                            AND i.admission = 'admitted' AND NOT g.human_review_required AND g.semantics = 'formal')""",
    "more_than_one_current_per_unit": """
      SELECT count(*) FROM (SELECT 1 FROM platform.mastery_estimate WHERE is_current AND node_id IS NOT NULL
                             GROUP BY student_id, subject, node_id, cognitive_level HAVING count(*) > 1) x""",
    "retest_record_without_exactly_one_task": """
      SELECT count(*) FROM platform.mastery_estimate m
       WHERE m.is_current AND m.node_id IS NOT NULL AND m.next_retest_at IS NOT NULL
         AND (EXISTS (SELECT 1 FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
                       WHERE v.status = 'published' AND v.subject = m.subject AND n.id = m.node_id AND n.status = 'active')
              OR EXISTS (SELECT 1 FROM kge.id_migration x WHERE x.subject = m.subject AND x.old_id = m.node_id AND x.rule = 'retire'))
         AND (SELECT count(*) FROM platform.retest_task t
               WHERE t.student_id = m.student_id AND t.subject = m.subject AND t.node_id = m.node_id
                 AND t.cognitive_level = m.cognitive_level AND t.status = 'open' AND t.reason IN ('scheduled', 'early')
                 AND t.due_at = m.next_retest_at) <> 1""",
    "retest_task_without_record": """
      SELECT count(*) FROM platform.retest_task t
       WHERE t.status = 'open' AND t.reason IN ('scheduled', 'early')
         AND NOT EXISTS (SELECT 1 FROM platform.mastery_estimate m
                          WHERE m.is_current AND m.student_id = t.student_id AND m.subject = t.subject AND m.node_id = t.node_id
                            AND m.cognitive_level = t.cognitive_level AND m.next_retest_at = t.due_at)""",
    "evidence_ids_not_this_students": """
      SELECT count(*) FROM platform.mastery_estimate m, unnest(m.evidence_ids) eid
       WHERE m.node_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM platform.evidence_event e
                                                    WHERE e.evidence_id = eid AND e.student_id = m.student_id AND e.subject = m.subject)""",
    "legacy_key_rows_written": "SELECT count(*) FROM platform.mastery_estimate WHERE skill_local_id IS NOT NULL",
    "algorithm_version_without_config": """
      SELECT count(*) FROM platform.mastery_estimate WHERE node_id IS NOT NULL
         AND (algorithm_version::text NOT LIKE 'kge-mastery@0.1.0.syn-0.1.0.%' OR confidence IS NULL)""",
    "profile_page_claim_missing": """
      SELECT count(*) FROM platform.profile_page p, jsonb_array_elements(p.sections) s,
             jsonb_array_elements(s -> 'statements' || s -> 'todo') i
       WHERE NOT EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = (i ->> 'claim_id')::uuid)""",
    "no_config_subject_written": "SELECT count(*) FROM platform.mastery_estimate WHERE subject = 'wenli'",
}


@pytest.fixture(autouse=True)
async def invariants(mpools):
    yield
    _, admin = mpools
    async with admin.acquire() as c:
        bad = {k: n for k, q in INVARIANTS.items() if (n := await c.fetchval(q))}
    assert not bad, f"全库不变量不成立：{bad}"


# ---------- 合成题目与证据 ----------
_n = [0]


async def item(admin, subject: str, version: str, node: str, *, family: str | None = None, level: str = LEVEL,
               admission: str = "admitted", item_type: str | None = "syn-solve", secondary: tuple[str, ...] = (),
               item_id: str | None = None, cv: str | None = None) -> str:
    _n[0] += 1
    iid = item_id or f"SYNM-{_n[0]:05d}"
    cv = cv or {"aya": CV_AYA, "zhixing": CV_ZX, "wenli": CV_WL}[subject]
    async with admin.acquire() as c:
        if not await c.fetchval("SELECT 1 FROM platform.item WHERE subject = $1::platform.subject AND item_id = $2", subject, iid):
            # 0039 起题目不能以 admitted 登记（准入只经发布闸门）；本文件的合成题与下面的打标一样以复制模式直接写入，
            # 已准入的题仍须满足“来源已核”约束（约束不随复制模式跳过）
            async with c.transaction():
                await c.execute("SET LOCAL session_replication_role = replica")
                await c.execute(
                    """INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at, source_ref, source_type, license_status)
                       VALUES ($1::platform.subject, $2, $3::uuid, $4::platform.admission, CASE WHEN $4 = 'admitted' THEN now() END,
                               CASE WHEN $4 = 'admitted' THEN 'SYN-SRC-MASTERY' END,
                               CASE WHEN $4 = 'admitted' THEN 'self_authored' ELSE 'unknown' END,
                               CASE WHEN $4 = 'admitted' THEN 'cleared' ELSE 'unverified' END)""",
                    subject, iid, cv, admission)
        fid = None
        if family:
            fid = await c.fetchval("SELECT id::text FROM kge.item_family WHERE map_version_id = $1::uuid AND code = $2", version, family)
            if fid is None:
                fid = await c.fetchval(
                    """INSERT INTO kge.item_family (map_version_id, main_node_id, code) VALUES ($1::uuid, $2, $3) RETURNING id::text""",
                    version, node, family)
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute(
                """INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, family_id,
                                             source_kind, tag_review_status)
                   VALUES ($1::uuid, $2::platform.subject, $3, $4, $5::text[], $6, $7::uuid, 'original', 'approved')""",
                version, subject, iid, node, list(secondary), level, fid)
    if item_type:
        ITEM_TYPES[iid] = item_type
    return iid


async def answer(admin, student: str, subject: str, item_id: str, t: datetime, *, correct: bool = True,
                 level: str | None = "A", kind: str | None = "independent", hint: int | None = 0, version: str | None = V1,
                 admission: str = "admitted", review: bool = False, semantics: str = "formal", self_report: str | None = None,
                 occurred: datetime | None = None, grade: bool = True, confidence: float = 1.0) -> tuple[str, str]:
    """一次合成作答：作答证据 + 作答 + 判分（超级用户直接写，只在一次性测试库）。返回 (attempt_id, evidence_id)。"""
    att, ev, req = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    occ = occurred or t
    channel = None if level is None else ("keypad" if level == "A" else "photo")
    key = f"synthetic-mastery-key-{uuid.uuid4().hex}"
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute(
                """INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload,
                     source_method, request_id, request_idempotency_key, recorded_at, evidence_kind, evidence_level, channel, map_version_id)
                   VALUES ($1::uuid, $2, $3::uuid, $4::platform.subject, 'response_text', $5, $6::platform.admission,
                           '{"text": "（合成）"}'::jsonb, 'grade', $7::uuid, $8, $9, $10, $11, $12, $13::uuid)""",
                ev, occ, student, subject, item_id, admission, req, key, t, kind, level, channel, version)
            await c.execute(
                """INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
                     self_report, request_idempotency_key, recorded_at, evidence_kind, evidence_level, channel, map_version_id)
                   VALUES ($1::uuid, $2, $3::uuid, $4::platform.subject, $5, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14::uuid)""",
                att, occ, student, subject, item_id, ev, hint, self_report, key, t, kind, level, channel, version)
            if grade:
                await c.execute(
                    """INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                         score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required,
                         review_reasons, semantics, evidence_ids, source_request_id)
                       VALUES ($1::uuid, $2::platform.subject, $3::uuid, $4, $5, 'aya-symbolic-ts@0.1.0', $6, 1, false, $7,
                               'verifier:aya-symbolic-ts@0.1.0;items:syn-pack@0.0.1', 'algo:aya-symbolic-ts@0.1.0', $8,
                               $9::platform.review_reason[], $10::platform.semantics, ARRAY[$11::uuid], $12::uuid)""",
                    student, subject, att, occ, "correct" if correct else "incorrect", 1 if correct else 0, confidence, review,
                    ["content_not_admitted"] if review else [], semantics, ev, req)
    return att, ev


async def rc(eng, student, unit=U1, subject="aya", now=None) -> R.UnitOutcome:
    return await R.recompute(eng, student, subject, unit.node_id, unit.cognitive_level, now=now or NOW[0])


async def current(admin, student, unit=U1, subject="aya"):
    async with admin.acquire() as c:
        return await c.fetchrow(
            """SELECT id::text, state::text, p_mastery::float8 AS p, confidence::float8 AS conf, next_retest_at,
                      evidence_ids::text[] AS evidence_ids, algorithm_version::text AS algorithm_version, map_version_id::text AS mv
                 FROM platform.mastery_estimate WHERE student_id = $1::uuid AND subject = $2::platform.subject
                  AND node_id = $3 AND cognitive_level = $4 AND is_current""", student, subject, unit.node_id, unit.cognitive_level)


async def history(admin, student, unit=U1, subject="aya") -> list:
    async with admin.acquire() as c:
        return await c.fetch(
            """SELECT id::text, state::text, is_current, superseded_by::text FROM platform.mastery_estimate
                WHERE student_id = $1::uuid AND subject = $2::platform.subject AND node_id = $3 AND cognitive_level = $4
                ORDER BY computed_at, id""", student, subject, unit.node_id, unit.cognitive_level)


async def tasks(admin, student, status=None, subject="aya") -> list:
    async with admin.acquire() as c:
        return await c.fetch(
            """SELECT id::text, node_id, cognitive_level, rung, reason, due_at, status, closing_attempt_id::text,
                      scheduled_by_estimate_id::text FROM platform.retest_task
                WHERE student_id = $1::uuid AND subject = $2::platform.subject AND ($3::text IS NULL OR status = $3)
                ORDER BY created_at, id""", student, subject, status)


async def explain(app_pool, student, unit=U1, subject="aya", cfg=None, now=None) -> dict:
    """解释（第一步 explain_gates）：以学习服务身份读同一份证据重放。"""
    from learning.store import learning_tx
    async with learning_tx(app_pool) as conn:
        ctx = await S.load_map(conn, subject)
        ev = await S.load_student(conn, student, subject, ctx, lambda s, i: ITEM_TYPES.get(i))
    return explain_gates(replay(ev.for_unit(unit), unit, cfg or SIGNED[subject], now or NOW[0]))


async def normal_path(admin, eng, student, *, upto: int = 5, check=None) -> list[tuple[str, str]]:
    """按合成配置：执行 → 当日复测 → 第二轮 → 第三轮（同第一步 tests/test_mastery_support.normal_path）。每条作答后同步重算。"""
    steps = [(T0, "fam-a", "syn-solve", "independent"), (T0 + 5 * MIN, "fam-b", "syn-choice", "independent"),
             (T0 + 3 * HOUR, "fam-c", "syn-proof", "independent"), (T0 + 3 * DAY, "fam-d", "syn-solve", "transfer"),
             (T0 + 13 * DAY, "fam-e", "syn-solve", "delayed_retest")]
    out = []
    for i, (t, fam, typ, kind) in enumerate(steps[:upto]):
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-{fam}", item_type=typ)
        out.append(await answer(admin, student, "aya", it, t, kind=kind))
        await rc(eng, student, now=t)
        if check:
            await check(i, t)
    return out


# ---------- 正常路径、复测任务按时生成、能力视图 ----------
async def test_normal_path_writes_chain_and_tasks(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    expected = ["understand", "execute", "stable", "transfer", "retain"]
    rung_gap = {"execute": 2 * HOUR, "stable": 2 * DAY, "transfer": 9 * DAY}

    async def check(i, t):
        cur = await current(admin, student)
        assert cur["state"] == expected[i]
        if expected[i] in rung_gap:
            # 到达时刻 + 配置间隔 = next_retest_at = 同一事务里建的复测轮任务的到期时间
            assert cur["next_retest_at"] == t + rung_gap[expected[i]]
            open_ = [r for r in await tasks(admin, student, "open") if r["reason"] == "scheduled"]
            assert len(open_) == 1 and open_[0]["due_at"] == cur["next_retest_at"]
            assert open_[0]["scheduled_by_estimate_id"] == cur["id"]
            assert open_[0]["rung"] == {"execute": "rung0", "stable": "rung1", "transfer": "rung2"}[expected[i]]
            # 到期前查不到、到期后查得到
            async with app_pool.acquire() as c:
                assert await S.due_tasks(c, student, "aya", cur["next_retest_at"] - MIN) == []
                assert len(await S.due_tasks(c, student, "aya", cur["next_retest_at"])) == 1
        else:
            assert cur["next_retest_at"] is None
        lit = star_lit(N1, ("understand", "apply"), {"apply": cur["state"]}, SIGNED["aya"])
        assert lit == (expected[i] in ("transfer", "retain"))

    answers = await normal_path(admin, eng, student, check=check)
    hist = await history(admin, student)
    assert [h["state"] for h in hist] == expected                      # 每步一条新记录
    for old, new in zip(hist, hist[1:]):
        assert old["is_current"] is False and old["superseded_by"] == new["id"]
    assert hist[-1]["is_current"] and hist[-1]["superseded_by"] is None
    # 三轮复测都通过并记下关闭它的作答
    closed = [r for r in await tasks(admin, student) if r["status"] != "open" and r["reason"] == "scheduled"]
    assert [(r["rung"], r["status"], r["closing_attempt_id"]) for r in closed] == [
        ("rung0", "passed", answers[2][0]), ("rung1", "passed", answers[3][0]), ("rung2", "passed", answers[4][0])]
    assert await tasks(admin, student, "open") == []                   # 到达保持后不再排（#14）
    # 同样输入再算一次：不写行
    assert (await rc(eng, student, now=T0 + 13 * DAY)).status == "unchanged"
    assert len(await history(admin, student)) == 5
    # 规则 6：algorithm_version 含配置版本，confidence 非空，evidence_ids 全部存在、属于本学生本单元（迁移及以上只列严格证据）
    cur = await current(admin, student)
    assert cur["algorithm_version"] == SIGNED["aya"].algorithm_version and ".syn-0.1.0." in cur["algorithm_version"]
    assert cur["conf"] > 0 and set(cur["evidence_ids"]) == {ev for _, ev in answers}
    assert cur["mv"] == V1
    ex = await explain(app_pool, student, now=T0 + 13 * DAY)
    assert ex["gate"] == "retain" and set(ex["evidence_ids"]) == set(cur["evidence_ids"])
    # 能力视图：只由节点结果与 skill_link 权重读时算出，不点亮、不写库
    async with app_pool.acquire() as c:
        view = await S.skill_view(c, student, "aya")
    # N1 有 understand、apply 两个层次，只有 apply 有记录：缺的层次按 unseen 计入（保守，待人确认 M）；p 只由有记录的层次算
    assert set(view) == {"K-SYN-0001"} and view["K-SYN-0001"]["state"] == "unseen"
    assert view["K-SYN-0001"]["p_mastery"] == pytest.approx(cur["p"])
    assert "star_lit" not in view["K-SYN-0001"]


async def test_retest_tasks_reconcile_by_sweep(mpools, student):
    """删掉一条复测任务后跑对账 → 补回且只有一条；连跑三次任务数不变（第一步验收“复测任务按时生成”）。"""
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=2)   # 执行：rung0 开着
    cur = await current(admin, student)
    async with admin.acquire() as c:
        await c.execute("DELETE FROM platform.retest_task WHERE student_id = $1::uuid", student)
    now = T0 + HOUR
    out = await mastery_sweep(eng, subjects=["aya"], now=now)
    assert out["aya"]["tasks_created"] >= 1
    open_ = await tasks(admin, student, "open")
    assert [(r["reason"], r["rung"], r["due_at"]) for r in open_] == [("scheduled", "rung0", cur["next_retest_at"])]
    for _ in range(3):
        await mastery_sweep(eng, subjects=["aya"], now=now)
    assert len(await tasks(admin, student)) == 1


# ---------- 刷同题 ----------
async def test_same_item_many_days_stays_understand(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-one")
    for d in range(5):
        await answer(admin, student, "aya", it, T0 + d * DAY)  # 每天换 key 独立做对
        await rc(eng, student, now=T0 + d * DAY)
    assert (await current(admin, student))["state"] == "understand"
    ex = await explain(app_pool, student, now=T0 + 5 * DAY)
    assert sum(1 for x in ex["excluded"] if "repeat_family" in x["reasons"]) == 4


async def test_same_family_and_single_type_group_capped(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    for i in range(4):  # 同一题目族的不同题：按一族计，不超过“理解”
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-fam", item_type="syn-choice")
        await answer(admin, student, "aya", it, T0 + i * HOUR)
    await rc(eng, student, now=T0 + 4 * HOUR)
    assert (await current(admin, student))["state"] == "understand"
    for i in range(4):  # 不同族都做对，但只覆盖一个题型组（syn-choice / syn-fill 同属 syn-g1）→ 不超过“执行”
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-g{i}", item_type=("syn-choice", "syn-fill")[i % 2])
        await answer(admin, student, "aya", it, T0 + 5 * HOUR + i * 3 * HOUR)
    await rc(eng, student, now=T0 + 2 * DAY)
    assert (await current(admin, student))["state"] == "execute"


async def test_high_hint_only_creates_hint_followup(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    units = [U1, Unit(N2, LEVEL)]
    for u in units:
        for i in range(2):
            it = await item(admin, "aya", V1, u.node_id, family=f"{student[:8]}-{u.node_id[-2:]}-{i}")
            await answer(admin, student, "aya", it, T0 + i * HOUR, kind="hinted", hint=5)
        await rc(eng, student, u, now=T0 + 3 * HOUR)
        assert await current(admin, student, u) is None            # 掌握与门槛不变：没有记录
    open_ = await tasks(admin, student, "open")
    assert sorted((r["node_id"], r["reason"]) for r in open_) == [(N1, "hint_followup"), (N2, "hint_followup")]
    await rc(eng, student, now=T0 + 4 * HOUR)                       # 重算不重复建
    assert len(await tasks(admin, student, "open")) == 2


async def test_retest_interval_uses_server_time(mpools, student):
    """复测轮到期前作答不算复测（retest_too_early）；客户端时间 occurred_at 写到期后也没用，只按服务器 recorded_at（#8）。"""
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=2)                  # 执行，rung0 到期 T0+5m+2h
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-early", item_type="syn-proof")
    await answer(admin, student, "aya", it, T0 + 30 * MIN, occurred=T0 + 5 * HOUR)
    await rc(eng, student, now=T0 + 6 * HOUR)
    assert (await current(admin, student))["state"] == "execute"
    ex = await explain(app_pool, student, now=T0 + 6 * HOUR)
    assert any(a["code"] == "retest_too_early" for a in ex["annotations"])


async def test_failed_retest_then_other_correct_still_failed(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=2)
    t_fail = T0 + 3 * HOUR
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-x1", item_type="syn-proof")
    att_fail, _ = await answer(admin, student, "aya", it, t_fail, correct=False)
    it2 = await item(admin, "aya", V1, N1, family=f"{student[:8]}-x2", item_type="syn-proof")
    await answer(admin, student, "aya", it2, t_fail + 10 * MIN)     # 换题做对：本轮仍记失败
    await rc(eng, student, now=t_fail + HOUR)
    cur = await current(admin, student)
    assert cur["state"] == "execute" and cur["next_retest_at"] == t_fail + 2 * HOUR  # 从失败时刻重排
    closed = [r for r in await tasks(admin, student) if r["status"] != "open" and r["reason"] == "scheduled"]
    assert [(r["status"], r["closing_attempt_id"]) for r in closed] == [("failed", att_fail)]


# ---------- 证据等级、旧证据、待复核、未准入、未签字 ----------
async def _full_path(admin, eng, student, **answer_kw) -> None:
    steps = [(T0, "a", "syn-solve", "independent"), (T0 + 5 * MIN, "b", "syn-choice", "independent"),
             (T0 + 3 * HOUR, "c", "syn-proof", "independent"), (T0 + 3 * DAY, "d", "syn-solve", "transfer")]
    for t, fam, typ, kind in steps:
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-{fam}", item_type=typ,
                        admission=answer_kw.get("admission", "admitted"))
        kw = dict(answer_kw)
        if kw.get("kind", "") is None:
            kind = None
        kw.pop("kind", None)
        await answer(admin, student, "aya", it, t, kind=kind, **kw)
    await rc(eng, student, now=T0 + 4 * DAY)


async def test_level_b_capped_at_stable_with_verify_task(mpools, student):
    app_pool, admin = mpools
    await _full_path(admin, engine(app_pool), student, level="B")
    assert (await current(admin, student))["state"] == "stable"
    open_ = {(r["reason"], r["rung"]) for r in await tasks(admin, student, "open")}
    assert {("verify_in_platform", None), ("scheduled", "rung1")} <= open_


async def test_legacy_evidence_capped_at_stable(mpools, student):
    app_pool, admin = mpools
    await _full_path(admin, engine(app_pool), student, level=None, kind=None)
    assert (await current(admin, student))["state"] in ("execute", "stable")
    ex = await explain(app_pool, student, now=T0 + 4 * DAY)
    assert any("legacy_no_kind" in c["caps"] for c in ex["capped"])


async def test_unsigned_config_capped_at_stable(mpools, student):
    app_pool, admin = mpools
    await _full_path(admin, engine(app_pool, UNSIGNED), student)
    assert (await current(admin, student))["state"] == "stable"


async def test_not_admitted_capped_then_recompute_item_drops_star(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    other = str(uuid.uuid4())
    # 条件全满足但题目未准入 → 最高“稳定”
    await _full_path(admin, eng, student, admission="pending_review")
    assert (await current(admin, student))["state"] == "stable"
    # 另一名学生走完正常路径到迁移，然后其中一道题被撤销准入（rejected），证据自身的准入仍是 admitted：取更严者
    async with admin.acquire() as c:
        acct, g, h = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", g, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", h, g)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", other, acct, h)
    answers = await normal_path(admin, eng, other, upto=4)
    lit = await current(admin, other)
    assert lit["state"] == "transfer"
    async with admin.acquire() as c:
        item_id = await c.fetchval("SELECT item_id::text FROM platform.attempt WHERE attempt_id = $1::uuid", answers[3][0])
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("UPDATE platform.item SET admission = 'rejected', admitted_at = NULL WHERE subject = 'aya' AND item_id = $1",
                            item_id)
    out = await R.recompute_item(eng, "aya", item_id, now=T0 + 4 * DAY)
    assert out["written"] == 1
    cur = await current(admin, other)
    assert cur["state"] == "stable"                                   # 亮星退回“稳定”
    hist = await history(admin, other)
    assert any(h["id"] == lit["id"] and not h["is_current"] and h["superseded_by"] == cur["id"] for h in hist)


async def test_pending_review_then_corrected_by_sweep(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-r")
    att, ev = await answer(admin, student, "aya", it, T0, review=True)
    await rc(eng, student, now=T0 + MIN)
    assert await current(admin, student) is None                     # 待复核不推动门槛
    ex = await explain(app_pool, student, now=T0 + MIN)
    assert ex["excluded"] == [{"evidence_id": ev, "reasons": ["pending_review"]}]
    # 复核结案：写一条更正后的判分并取代原判分（夹具以超级用户在一次性库里模拟结案），之后对账按更正后的判分计入
    now = datetime.now(timezone.utc)
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            old = await c.fetchrow("SELECT id::text, attempt_occurred_at FROM platform.grade_result WHERE attempt_id = $1::uuid", att)
            new = await c.fetchval(
                """INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                     score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics,
                     evidence_ids, source_request_id)
                   VALUES ($1::uuid, 'aya', $2::uuid, $3, 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, false, 1,
                           'verifier:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, 'formal', ARRAY[$4::uuid],
                           gen_random_uuid()) RETURNING id::text""", student, att, old["attempt_occurred_at"], ev)
            await c.execute("UPDATE platform.grade_result SET superseded_by = $2::uuid, superseded_at = now() WHERE id = $1::uuid",
                            old["id"], new)
    NOW[0] = now
    try:
        out = await mastery_sweep(eng, subjects=["aya"], now=now)
    finally:
        NOW[0] = T0
    assert out["aya"]["written"] >= 1
    assert (await current(admin, student))["state"] == "understand"


# ---------- 自述 ----------
async def test_self_report_does_not_change_mastery(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=2)
    before = await history(admin, student)
    dispute = str(uuid.uuid4())
    a = await R.schedule_verification(eng, student, "aya", N1, LEVEL, "verify_self_report", dispute, now=T0 + HOUR)
    b = await R.schedule_verification(eng, student, "aya", N1, LEVEL, "verify_self_report", dispute, now=T0 + 2 * HOUR)
    assert a["task_id"] == b["task_id"] and a["created"] and not b["created"]   # 同一异议重复调用：同一条任务
    assert set(a) == {"task_id", "node_id", "cognitive_level", "status", "due_at", "created"}  # 不含题目内容
    assert await history(admin, student) == before                    # 不改掌握
    await rc(eng, student, now=T0 + 3 * HOUR)                         # 重算不碰自述验证任务
    open_ = {r["reason"] for r in await tasks(admin, student, "open")}
    assert "verify_self_report" in open_
    # 任务关闭后同一异议再调用：仍返回那一条，不另建
    async with admin.acquire() as c:
        await c.execute("UPDATE platform.retest_task SET status = 'cancelled' WHERE id = $1::uuid", a["task_id"])
    c2 = await R.schedule_verification(eng, student, "aya", N1, LEVEL, "verify_self_report", dispute, now=T0 + 4 * HOUR)
    assert c2["task_id"] == a["task_id"] and c2["status"] == "cancelled" and not c2["created"]
    with pytest.raises(ValueError):
        await R.schedule_verification(eng, student, "aya", N1, LEVEL, "scheduled", dispute)
    with pytest.raises(R.MasteryUnavailable):
        await R.schedule_verification(eng, student, "aya", "MATH.SYN.T1.N9", LEVEL, "verify_self_report", dispute)


async def test_attempt_self_report_confident_same_result(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    other = str(uuid.uuid4())
    async with admin.acquire() as c:
        acct, g, h = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        await c.execute("INSERT INTO platform.account (id, kind) VALUES ($1::uuid, 'guardian'), ($2::uuid, 'student')", g, acct)
        await c.execute("INSERT INTO platform.household (id, created_by_account) VALUES ($1::uuid, $2::uuid)", h, g)
        await c.execute("""INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, 9, 'nanjing', 'suke-2024', '2012-03-01')""", other, acct, h)
    for i in range(3):
        it = await item(admin, "aya", V1, N1, family=f"same-{student[:8]}-{i}")
        await answer(admin, student, "aya", it, T0 + i * HOUR, self_report="confident")
        await answer(admin, other, "aya", it, T0 + i * HOUR)
    await rc(eng, student, now=T0 + 4 * HOUR)
    await rc(eng, other, now=T0 + 4 * HOUR)
    a, b = await current(admin, student), await current(admin, other)
    assert (a["state"], a["p"], a["conf"], a["next_retest_at"]) == (b["state"], b["p"], b["conf"], b["next_retest_at"])


# ---------- 衰减与退级 ----------
async def test_decay_lowers_confidence_only(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=4)                 # 迁移（星亮），rung2 到期 T0+12d
    lit = await current(admin, student)
    confs = [lit["conf"]]
    for d in (20, 40, 60, 90):
        now = T0 + d * DAY
        await mastery_sweep(eng, subjects=["aya"], now=now)
        cur = await current(admin, student)
        assert cur["state"] == "transfer" and cur["p"] == lit["p"]  # 不降掌握、不退门槛、星不熄灭
        assert cur["conf"] <= confs[-1]
        confs.append(cur["conf"])
    assert confs[-1] < SIGNED["aya"]["review_confidence_floor"] < confs[0]
    decay = [r for r in await tasks(admin, student, "open") if r["reason"] == "decay"]
    assert len(decay) == 1
    await mastery_sweep(eng, subjects=["aya"], now=T0 + 91 * DAY)
    assert len([r for r in await tasks(admin, student, "open") if r["reason"] == "decay"]) == 1


async def test_rung2_failure_drops_to_stable(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    await normal_path(admin, eng, student, upto=4)
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-fail", item_type="syn-solve")
    att, _ = await answer(admin, student, "aya", it, T0 + 13 * DAY, correct=False, kind="delayed_retest")
    await rc(eng, student, now=T0 + 13 * DAY)
    cur = await current(admin, student)
    assert cur["state"] == "stable" and cur["next_retest_at"] == T0 + 13 * DAY + 2 * DAY   # 退回半亮，重排第二轮
    closed = [r for r in await tasks(admin, student) if r["status"] == "failed"]
    assert [(r["rung"], r["closing_attempt_id"]) for r in closed] == [("rung2", att)]


# ---------- 并发与写入事务 ----------
async def test_concurrent_recompute_single_write_no_retry(mpools, student):
    app_pool, admin = mpools

    async def slow():
        await asyncio.sleep(0.05)   # 拉长“读完当前记录 → 写入”之间的窗口：没有 advisory lock 就会撞唯一索引

    eng = engine(app_pool, after_read=slow)
    for i in range(3):
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-c{i}", item_type=("syn-solve", "syn-choice", "syn-proof")[i])
        await answer(admin, student, "aya", it, T0 + i * MIN)
    outs = await asyncio.gather(*(rc(eng, student, now=T0 + HOUR) for _ in range(6)))
    assert sorted(o.status for o in outs) == ["unchanged"] * 5 + ["written"]
    assert eng.stats["retries"] == 0 and eng.stats["failed"] == 0
    hist = await history(admin, student)
    assert len(hist) == 1 and hist[0]["is_current"]


async def test_task_sync_failure_rolls_back_estimate(mpools, student, monkeypatch):
    """复测任务与掌握记录在同一事务：任务写不进去时掌握记录也不落库（留给对账）。"""
    app_pool, admin = mpools
    eng = engine(app_pool)
    for i in range(2):
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-t{i}")
        await answer(admin, student, "aya", it, T0 + i * MIN)

    async def boom(*a, **kw):
        raise RuntimeError("synthetic task failure")

    monkeypatch.setattr(R, "_sync_tasks", boom)
    out = await rc(eng, student, now=T0 + HOUR)
    assert out.status == "failed" and list(eng.failed_units) == [("aya", N1, LEVEL)]
    assert await current(admin, student) is None
    monkeypatch.undo()
    assert (await rc(eng, student, now=T0 + HOUR)).status == "written"
    assert len(await tasks(admin, student, "open")) == 1


# ---------- 地图版本 ----------
ZN = {k: f"MORAL.SYN.T1.{k}" for k in ("N3", "N4", "N5", "N6", "N7", "N8", "N3R", "N4A", "N4B", "N67", "N8M")}


async def test_map_version_switch(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)
    types = ("syn-solve", "syn-choice", "syn-proof", "syn-solve")
    by_node: dict[str, list[str]] = {}
    for node in ("N3", "N4", "N5", "N6", "N8"):
        by_node[node] = []
        for i, (dt, typ) in enumerate(zip((0 * MIN, 5 * MIN, 3 * HOUR, 3 * DAY), types)):
            it = await item(admin, "zhixing", Z1, ZN[node], family=f"{student[:8]}-{node}-{i}", item_type=typ)
            by_node[node].append(it)
            await answer(admin, student, "zhixing", it, T0 + dt, version=Z1, kind="transfer" if i == 3 else "independent")
    for node in ("N3", "N4", "N5", "N6", "N8"):
        await rc(eng, student, Unit(ZN[node], LEVEL), "zhixing", now=T0 + 4 * DAY)
        assert (await current(admin, student, Unit(ZN[node], LEVEL), "zhixing"))["state"] == "transfer"
    # 拆分题在新版本里另有打标（新节点 N4A）：按证据自身版本解析，旧证据不归属到拆出的新节点
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            for it in by_node["N4"]:
                await c.execute("""INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, cognitive_level, source_kind,
                                                             tag_review_status)
                                   VALUES ($1::uuid, 'zhixing', $2, $3, 'apply', 'original', 'approved')""", Z2, it, ZN["N4A"])
            await c.execute("UPDATE kge.map_version SET status = 'retired' WHERE id = $1::uuid", Z1)
            await c.execute("UPDATE kge.map_version SET status = 'published', published_at = '2026-11-20T00:00:00Z' WHERE id = $1::uuid", Z2)
    try:
        now = T0 + 5 * DAY                                     # 第三轮复测（T0+12d）尚未到期
        out = await R.recompute_map(eng, "zhixing", Z1, Z2, now=now)
        assert out["students"] >= 1
        # 改名、合并：门槛延续
        for new in ("N3R", "N67"):
            cur = await current(admin, student, Unit(ZN[new], LEVEL), "zhixing")
            assert cur is not None and cur["state"] == "transfer" and cur["mv"] == Z2
        # 拆分的新节点重新判定：旧证据不延续；人工迁移（manual）按拆分处理（审查意见 8）
        for new in ("N4A", "N4B", "N8M"):
            assert await current(admin, student, Unit(ZN[new], LEVEL), "zhixing") is None
        # 撤销：当前记录置待复测（next_retest_at = 当下），门槛不变（星不熄灭），有一条对应的复测轮任务
        ret = await current(admin, student, Unit(ZN["N5"], LEVEL), "zhixing")
        assert ret["state"] == "transfer" and ret["next_retest_at"] == now
        # 旧单元（改名、合并、拆分前）不再计算，开着的重放任务撤销
        open_ = await tasks(admin, student, "open", subject="zhixing")
        assert {(r["node_id"], r["reason"]) for r in open_} == {(ZN["N3R"], "scheduled"), (ZN["N67"], "scheduled"),
                                                                (ZN["N5"], "scheduled")}
        # 再跑一次：幂等
        again = await R.recompute_map(eng, "zhixing", Z1, Z2, now=now + HOUR)
        assert again.get("written", 0) == 0
        with pytest.raises(R.MasteryUnavailable):
            await R.recompute_map(eng, "zhixing", Z2, Z1)
    finally:
        async with admin.acquire() as c:
            async with c.transaction():
                await c.execute("SET LOCAL session_replication_role = replica")
                await c.execute("UPDATE kge.map_version SET status = 'retired' WHERE id = $1::uuid", Z2)
                await c.execute("UPDATE kge.map_version SET status = 'published' WHERE id = $1::uuid", Z1)
            # 恢复后旧单元重新有效：对账把它们的任务对齐（不变量按当前地图检查）
        await mastery_sweep(eng, subjects=["zhixing"], now=T0 + 21 * DAY)


# ---------- 没有配置的学科 ----------
async def test_subject_without_config_writes_nothing(mpools, student):
    app_pool, admin = mpools
    eng = engine(app_pool)                                   # 只有数学、道法的合成配置；语文没有
    wu = Unit("CHN.SYN.T1.N1", LEVEL)
    for i in range(3):
        it = await item(admin, "wenli", W1, wu.node_id, family=f"{student[:8]}-w{i}")
        await answer(admin, student, "wenli", it, T0 + i * MIN, version=W1)
    assert (await rc(eng, student, wu, "wenli")).status == "skipped_no_config"
    assert (await R.after_ingest(eng, student, "wenli", []))["skipped_no_config"] == 1
    assert (await mastery_sweep(eng, subjects=["wenli"], now=T0 + DAY))["wenli"] == {"skipped_no_config": 1}
    assert await current(admin, student, wu, "wenli") is None
    with pytest.raises(R.MasteryUnavailable):
        await R.schedule_verification(eng, student, "wenli", wu.node_id, LEVEL, "verify_self_report", str(uuid.uuid4()))


# ---------- 接入 grade：同步重算、重放不触发、重算失败不影响落库 ----------
@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


@pytest.fixture
async def mapi(mpools, candidate, mpg):
    app_pool, admin = mpools
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, cognitive_level, source_kind,
                                                         tag_review_status)
                               VALUES ($1::uuid, 'aya', 'SYN-ADM', $2, 'apply', 'original', 'approved') ON CONFLICT DO NOTHING""", V1, N2)
    fake = FakeAdapter()
    fake.admission = "admitted"
    eng = R.MasteryEngine(app_pool, SIGNED)   # 真实时钟：grade 的 recorded_at 是服务器当前时间
    app = create_app(settings(), pool=app_pool, transport=httpx.MockTransport(fake.handler), mastery=eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            client.headers["authorization"] = f"Bearer {CREDENTIAL}"
            yield client, eng


def _v1(student: str, text: str = "42") -> dict:
    return env(to_v1_body(grade_body(student, item="SYN-ADM", text=text)))


async def test_grade_recomputes_synchronously_and_replay_does_not(mapi, mpools, student):
    client, eng = mapi
    _, admin = mpools
    body = _v1(student)
    r = await client.post("/internal/v1/grade", json=body)
    assert r.status_code == 200, r.text
    cur = await current(admin, student, Unit(N2, LEVEL))
    assert cur is not None and cur["state"] == "understand"           # 接口返回时节点键记录已经在
    triggered = eng.stats["triggered"]
    again = await client.post("/internal/v1/grade", json=copy.deepcopy(body))
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true"
    assert eng.stats["triggered"] == triggered                         # 幂等重放不触发重算
    assert len(await history(admin, student, Unit(N2, LEVEL))) == 1


async def test_recompute_failure_does_not_affect_grade(mapi, mpools, student):
    client, eng = mapi
    _, admin = mpools

    async def boom():
        raise RuntimeError("synthetic recompute failure")

    eng.after_read = boom
    r = await client.post("/internal/v1/grade", json=_v1(student))
    assert r.status_code == 200, r.text                                  # 接口照常 200
    assert eng.stats["failed"] >= 1 and (("aya", N2, LEVEL) in eng.failed_units)
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.grade_result WHERE student_id = $1::uuid", student) == 1
        assert await c.fetchval("SELECT count(*) FROM platform.evidence_event WHERE student_id = $1::uuid", student) >= 1
    assert await current(admin, student, Unit(N2, LEVEL)) is None
    eng.after_read = None
    out = await mastery_sweep(eng, subjects=["aya"])                     # 之后对账补上
    assert out["aya"]["written"] >= 1
    assert (await current(admin, student, Unit(N2, LEVEL)))["state"] == "understand"


async def test_jobs_cli_sweep(mpools, student):
    app_pool, admin = mpools
    await normal_path(admin, engine(app_pool), student, upto=2)
    environ = {"LEARNING_ENV": "test", "LEARNING_MASTERY_CONFIG_AYA": str(CONFIG_DIR / "synthetic-test.json")}
    out = await jobs_cli.run(["mastery-sweep", "--subject", "aya", "--now", (T0 + HOUR).isoformat()], environ, pool=app_pool)
    assert out["job"] == "mastery-sweep" and out["failed_units"] == 0 and out["subjects"]["aya"]["students"] >= 1
    assert all(isinstance(v, int) for v in out["subjects"]["aya"].values())
    assert jobs_cli.exit_code(out) == (1 if out["stale_estimate"] else 0)
    with pytest.raises(jobs_cli.JobError):                              # 不认识的环境
        await jobs_cli.run(["mastery-sweep"], {"LEARNING_ENV": "prod"}, pool=app_pool)
    with pytest.raises(jobs_cli.JobError):                              # 注入时钟只允许 test
        await jobs_cli.run(["mastery-sweep", "--now", T0.isoformat()], {**environ, "LEARNING_ENV": "dev"}, pool=app_pool)
    with pytest.raises(jobs_cli.JobError):                              # 一个学科都没配：不静默成功
        await jobs_cli.run(["mastery-sweep"], {"LEARNING_ENV": "test"}, pool=app_pool)
    with pytest.raises(jobs_cli.JobError):                              # 配置文件的学科与变量名不符
        await jobs_cli.run(["mastery-sweep"], {"LEARNING_ENV": "test",
                                               "LEARNING_MASTERY_CONFIG_ZHIXING": str(CONFIG_DIR / "synthetic-test.json")},
                           pool=app_pool)
    assert jobs_cli.exit_code({"failed_units": 0, "stale_estimate": 1}) == 1
    assert jobs_cli.exit_code({"failed_units": 2, "stale_estimate": 0}) == 1
    assert jobs_cli.exit_code({"failed_units": 0, "stale_estimate": 0}) == 0


def test_jobs_cli_main_hides_error_messages(monkeypatch, capsys):
    """main 捕获异常只输出错误类名，不输出消息（消息可能带出数据）。同步测试：main 自己起事件循环。"""
    async def boom(argv, environ=None, *, pool=None):
        raise LookupError("synthetic message with student 5b8e1c52-0000-4000-8000-000000000001")

    monkeypatch.setattr(jobs_cli, "run", boom)
    assert jobs_cli.main(["mastery-sweep"]) == 2
    err = capsys.readouterr().err
    assert "LookupError" in err and "5b8e1c52" not in err and "synthetic message" not in err

    async def bad_config(argv, environ=None, *, pool=None):
        raise jobs_cli.JobError("没有配置任何学科的掌握配置")

    monkeypatch.setattr(jobs_cli, "run", bad_config)
    assert jobs_cli.main(["mastery-sweep"]) == 2
    assert "没有配置任何学科" in capsys.readouterr().err


# ---------- 审查意见修订 ----------
async def test_uppercase_student_id_same_lock_and_task(mpools, student):
    """学生 ID 规范化：大写与小写的同一学生拿同一把锁（并发无重试）、同一异议得到同一条自述验证任务。"""
    app_pool, admin = mpools

    async def slow():
        await asyncio.sleep(0.05)

    eng = engine(app_pool, after_read=slow)
    for i in range(3):
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-u{i}", item_type=("syn-solve", "syn-choice", "syn-proof")[i])
        await answer(admin, student, "aya", it, T0 + i * MIN)
    ids = [student.upper(), student, student.upper(), student]
    outs = await asyncio.gather(*(rc(eng, s, now=T0 + HOUR) for s in ids))
    assert sorted(o.status for o in outs) == ["unchanged"] * 3 + ["written"]
    assert eng.stats["retries"] == 0 and len(await history(admin, student)) == 1
    dispute = str(uuid.uuid4())
    a = await R.schedule_verification(eng, student.upper(), "aya", N1, LEVEL, "verify_self_report", dispute.upper(), now=T0)
    b = await R.schedule_verification(eng, student, "aya", N1, LEVEL, "verify_self_report", dispute, now=T0)
    assert a["task_id"] == b["task_id"] and a["created"] and not b["created"]


async def test_sync_recompute_time_budget(mpools, student, candidate):
    """落库后同步重算有时间预算：超时接口照常 200，记 failed，交给对账补上。"""
    app_pool, admin = mpools
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, cognitive_level, source_kind,
                                                         tag_review_status)
                               VALUES ($1::uuid, 'aya', 'SYN-ADM', $2, 'apply', 'original', 'approved') ON CONFLICT DO NOTHING""", V1, N2)

    async def slow():
        await asyncio.sleep(1.0)

    fake = FakeAdapter()
    fake.admission = "admitted"
    eng = R.MasteryEngine(app_pool, SIGNED, after_read=slow)
    app = create_app(settings(mastery_sync_budget_seconds=0.2), pool=app_pool, transport=httpx.MockTransport(fake.handler),
                     mastery=eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            client.headers["authorization"] = f"Bearer {CREDENTIAL}"
            r = await client.post("/internal/v1/grade", json=_v1(student))
    assert r.status_code == 200, r.text
    assert eng.stats["sync_timeout"] == 1 and eng.stats["failed"] >= 1
    assert await current(admin, student, Unit(N2, LEVEL)) is None
    eng.after_read = None
    await mastery_sweep(eng, subjects=["aya"])
    assert (await current(admin, student, Unit(N2, LEVEL)))["state"] == "understand"


async def test_failure_log_has_no_student_or_evidence_ids(mpools, student, caplog):
    app_pool, admin = mpools

    async def boom():
        raise RuntimeError(f"synthetic failure {student}")

    eng = engine(app_pool, after_read=boom)
    evs = []
    for i in range(2):
        it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-l{i}")
        evs.append(await answer(admin, student, "aya", it, T0 + i * MIN))
    with caplog.at_level("WARNING", logger="starbuddy.learning.mastery"):
        out = await R.after_ingest(eng, student, "aya", [a for a, _ in evs], now=T0 + HOUR)
    assert out["failed"] == 1
    text = caplog.text
    assert "mastery_recompute_failed" in text and N1 in text and "RuntimeError" in text
    assert student not in text and student.upper() not in text
    assert not any(x in text for pair in evs for x in pair)            # 不含证据 ID、作答 ID
    assert "synthetic failure" not in text                              # 不含异常消息
    eng.after_read = None
    await rc(eng, student, now=T0 + HOUR)                               # 补上，保持不变量


async def test_skill_view_only_current_map_units(mpools, student):
    """能力视图与 plan 快照只认当前地图有效单元：层次被删、类型变了（不参与计算）的节点的记录不进 MasterySnapshot。"""
    from learning.plan import _mastery
    app_pool, admin = mpools
    it = await item(admin, "aya", V1, N2, family=f"{student[:8]}-v")
    _, ev = await answer(admin, student, "aya", it, T0)
    async with admin.acquire() as c:
        for node, level in ((N2, "understand"), (N3, "apply")):   # N2 没有 understand 层次；N3 是方法类节点
            await c.execute(
                """INSERT INTO platform.mastery_estimate (student_id, subject, node_id, cognitive_level, map_version_id, state,
                                                          p_mastery, confidence, algorithm_version, evidence_ids)
                   VALUES ($1::uuid, 'aya', $2, $3, $4::uuid, 'retain', 0.99, 0.9, $5, ARRAY[$6::uuid])""",
                student, node, level, V1, SIGNED["aya"].algorithm_version, ev)
    async with app_pool.acquire() as c:
        assert await S.skill_view(c, student, "aya") == {}
    assert await _mastery(app_pool, student, "aya") == []
    await rc(eng := engine(app_pool), student, Unit(N2, LEVEL), now=T0 + HOUR)   # 有效单元写入后才出现
    snap = {m["skill_node"]: m for m in await _mastery(app_pool, student, "aya")}
    assert set(snap) == {"aya:K-SYN-0001", "aya:K-SYN-0002"}
    assert snap["aya:K-SYN-0002"]["state"] == "understand" and "star_lit" not in snap["aya:K-SYN-0002"]
    assert eng.stats["failed"] == 0


async def test_stale_estimate_counted_and_nonzero_exit(mpools, student):
    """已有记录的证据全部不再计入（判分改回待复核）：记录原样保留，stale_estimate 进对账输出，命令行退出码 1（待人确认 L）。
    放在文件最后：之后的对账都会看到这条 stale 记录。"""
    app_pool, admin = mpools
    it = await item(admin, "aya", V1, N1, family=f"{student[:8]}-s")
    att, ev = await answer(admin, student, "aya", it, T0)
    await rc(engine(app_pool), student, now=T0 + MIN)
    before = await current(admin, student)
    assert before["state"] == "understand"
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            old = await c.fetchrow("SELECT id::text, attempt_occurred_at FROM platform.grade_result WHERE attempt_id = $1::uuid", att)
            new = await c.fetchval(
                """INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
                     score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required,
                     review_reasons, semantics, evidence_ids, source_request_id)
                   VALUES ($1::uuid, 'aya', $2::uuid, $3, 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, false, 0.5,
                           'verifier:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', true, '{spot_check}', 'formal',
                           ARRAY[$4::uuid], gen_random_uuid()) RETURNING id::text""", student, att, old["attempt_occurred_at"], ev)
            await c.execute("UPDATE platform.grade_result SET superseded_by = $2::uuid, superseded_at = now() WHERE id = $1::uuid",
                            old["id"], new)
    environ = {"LEARNING_ENV": "test", "LEARNING_MASTERY_CONFIG_AYA": str(CONFIG_DIR / "synthetic-test.json")}
    out = await jobs_cli.run(["mastery-sweep", "--subject", "aya", "--now", (T0 + HOUR).isoformat()], environ, pool=app_pool)
    assert out["stale_estimate"] >= 1 and out["subjects"]["aya"]["stale_estimate"] >= 1
    assert jobs_cli.exit_code(out) == 1
    assert (await current(admin, student))["id"] == before["id"]       # 记录原样保留
