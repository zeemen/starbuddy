"""调度器库上测试的合成夹具（规则 8：全部是占位编码，不是真实数据），在 pginsight 的一次性库上补。

会话级只建共用的东西（SEED_SQL）：登录角色 scheduler_app（只继承 starbuddy_scheduler）、kge_app（只继承 starbuddy_kge）；计量流水分区；
一个草稿地图版本（时政用）；一个已发布的数学合成地图版本（节点、内容版本、已准入且已打标的题，掌握对账用）。
每个端到端测试自己造自己的数据（下面的 seed_* 函数，每次新的随机 ID），断言只看自己的数据，与测试顺序无关。
平台事实行以超级用户在 session_replication_role = replica 下直接插入（与 pginsight 同一做法），只为构造夹具。
"""
from __future__ import annotations

import uuid

from pginsight import E

SCHEDULER_ROLE, KGE_ROLE = "scheduler_app", "kge_app"
MAPV_DRAFT = "5e300000-0000-4000-8000-000000000001"       # 时政用的草稿地图（语文）
MAPV_MATH = "5e300000-0000-4000-8000-000000000002"        # 掌握对账用的已发布数学地图
CV_MATH = "5e300000-0000-4000-8000-0000000000c2"
NODE = "MATH.SCHED.T1.N1"
LEVEL = "apply"
ITEM = "SYN-SCHED-ITEM-001"

SEED_SQL = f"""
CREATE ROLE {SCHEDULER_ROLE} LOGIN IN ROLE starbuddy_scheduler;
CREATE ROLE {KGE_ROLE} LOGIN IN ROLE starbuddy_kge;
SELECT platform.ensure_monthly_partitions('platform.usage_ledger'::regclass, (now() - interval '40 days')::date, 4);

-- 时政：草稿地图版本
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('{MAPV_DRAFT}', 'wenli', 2022, '9.8.0');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
  ('{MAPV_DRAFT}', 'CHN.RC', 'domain', NULL, NULL, '合成领域', '{{}}', '{{}}', '{{}}', '{{}}'),
  ('{MAPV_DRAFT}', 'CHN.RC.T', 'topic', 'CHN.RC', NULL, '合成主题', '{{}}', '{{}}', '{{}}', '{{}}'),
  ('{MAPV_DRAFT}', 'CHN.RC.T.A', 'node', 'CHN.RC.T', 'kp', '合成节点', '{{a}}', '{{b}}', '{{c}}', '{{understand}}');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
  VALUES ('{MAPV_DRAFT}', 'CHN.RC.T.A', 'syn-2024', 2024, 'v', 'c', 1, 1);

-- 掌握对账：已发布的数学合成地图、内容版本、一道已准入且已打标的题（发布守卫只放行发布流程；一次性库里以复制模式直接写）
SET session_replication_role = replica;
INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES
  ('{MAPV_MATH}', 'aya', 2022, '9.9.0', 'published', now() - interval '30 days');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels) VALUES
  ('{MAPV_MATH}', 'MATH.SCHED', 'domain', NULL, NULL, '（合成）领域', '{{}}'),
  ('{MAPV_MATH}', 'MATH.SCHED.T1', 'topic', 'MATH.SCHED', NULL, '（合成）主题', '{{}}'),
  ('{MAPV_MATH}', '{NODE}', 'node', 'MATH.SCHED.T1', 'kp', '（合成）节点', '{{understand,apply}}');
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, status, published_at,
  map_version_id) VALUES
  ('{CV_MATH}', 'nanjing', 2027, 9, 'syn-sched-2024', 'aya', '0.1.0', 'published', now() - interval '30 days', '{MAPV_MATH}');
INSERT INTO platform.item (subject, item_id, curriculum_version_id, admission, admitted_at, source_ref, source_type, license_status) VALUES
  ('aya', '{ITEM}', '{CV_MATH}', 'admitted', now() - interval '30 days', 'SYN-SRC-SCHED', 'self_authored', 'cleared');
INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES
  ('5e300000-0000-4000-8000-0000000000f1', '{MAPV_MATH}', '{NODE}', 'syn-sched-fam-a');
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, family_id, source_kind, tag_review_status)
  VALUES ('{MAPV_MATH}', 'aya', '{ITEM}', '{NODE}', '{{}}', '{LEVEL}', '5e300000-0000-4000-8000-0000000000f1', 'original', 'approved');
SET session_replication_role = origin;
"""


def u() -> str:
    return str(uuid.uuid4())


def new_student(db) -> dict[str, str]:
    """一户合成家庭：家长、学生账号、家庭、学生档案与有效绑定"""
    ids = {"guardian": u(), "account": u(), "household": u(), "student": u()}
    db.psql(f"""
      INSERT INTO platform.account (id, kind) VALUES ('{ids['guardian']}', 'guardian'), ('{ids['account']}', 'student');
      INSERT INTO platform.household (id, created_by_account) VALUES ('{ids['household']}', '{ids['guardian']}');
      INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
        ('{ids['student']}', '{ids['account']}', '{ids['household']}', 8, 'nanjing', 'bubian-2024', '2013-02-01');
      INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
        ('{ids['guardian']}', '{ids['student']}', 'household_creator');""")
    return ids


def seed_expired_reservation(db) -> dict[str, str]:
    """有效订阅 + 额度桶（授予 5、预留 1）+ 授予与预留流水 + 一笔已过预留时长的预留"""
    s = new_student(db)
    s.update(sub=u(), ent=u(), usage=u())
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, status, period_start, period_end) VALUES
        ('{s['sub']}', '{s['household']}', '{s['student']}', 'pro', 'v12-pilot@1', 'pilot_virtual', 'active', now() - interval '1 day',
         now() + interval '29 days');
      INSERT INTO platform.entitlement (id, subscription_id, student_id, resource_type, period_start, period_end, granted, reserved, settled)
        VALUES ('{s['ent']}', '{s['sub']}', '{s['student']}', 'diagnosis_full', now() - interval '1 day', now() + interval '29 days', 5, 1, 0);
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, kind, quantity, actor, reason_code) VALUES
        (now() - interval '1 day', '{s['ent']}', '{s['student']}', 'grant', 5, 'system', 'period_grant');
      INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
        state, reserved_qty, reserved_at, expires_at) VALUES
        ('{s['usage']}', 'synthetic-scheduler-{s['usage']}', repeat('5', 64), '{s['ent']}', '{s['student']}', 'diagnosis_full', 'reserved', 1,
         now() - interval '20 minutes', now() - interval '10 minutes');
      INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
        (now() - interval '20 minutes', '{s['ent']}', '{s['student']}', '{s['usage']}', 'reserve', 1, 'bff', 'reserve');""")
    return s


def seed_due_trial(db) -> dict[str, str]:
    """一个已到期的试用订阅（续期任务把它结束）与一个没到期的（不动）"""
    s, t = new_student(db), new_student(db)
    s.update(sub=u(), live_sub=u())
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, status, period_start, period_end) VALUES
        ('{s['sub']}', '{s['household']}', '{s['student']}', 'basic', 'v12-pilot@1', 'trial', 'active', now() - interval '20 days',
         now() - interval '6 days'),
        ('{s['live_sub']}', '{t['household']}', '{t['student']}', 'basic', 'v12-pilot@1', 'trial', 'active', now() - interval '2 days',
         now() + interval '12 days');""")
    return s


def seed_due_erasure(db) -> dict[str, str]:
    s = new_student(db)
    s["erase"] = u()
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.erasure_request (id, student_id, requested_by, requested_at, confirmed_at, cooling_off_until, status) VALUES
        ('{s['erase']}', '{s['student']}', '{s['guardian']}', now() - interval '9 days', now() - interval '8 days', now() - interval '1 day',
         'pending');""")
    return s


def seed_due_prediction(db) -> dict[str, str]:
    """今天（上海）的考试安排、考试类判断（只给 agent）与未冻结的预测"""
    s = new_student(db)
    s.update(exam=u(), claim=u(), pred=u())
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO insight.exam_record (id, student_id, subject, exam_type, exam_date, full_score, raw_score, scope_node_ids, entered_by_role,
        entered_by_account_id, source_type) VALUES
        ('{s['exam']}', '{s['student']}', 'aya', 'monthly', (now() AT TIME ZONE 'Asia/Shanghai')::date, 100, NULL, '{{MATH.SYN.EQ.LINEAR}}',
         'guardian', '{s['guardian']}', 'guardian');
      INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, evidence_ids,
        source_type, model_version, status, change_reason, visibility, expires_at) VALUES
        ('{s['claim']}', '{s['student']}', 'aya', 'exam', '{s['exam']}', 'syn.math.node_mastery', 1, 0.5, '{{{E[0]}}}', 'inferred',
         'algo:syn-predict@0.1.0', 'hypothesis', 'created', '{{agent}}', now() + interval '30 days');
      INSERT INTO insight.exam_prediction (id, student_id, exam_record_id, claim_id, interval_low, interval_high, basis, algorithm_version)
        VALUES ('{s['pred']}', '{s['student']}', '{s['exam']}', '{s['claim']}', 40, 60, '{{}}', 'syn-predict@0.1.0');""")
    return s


def seed_affairs(db) -> dict[str, str]:
    ids = {"old": u(), "new": u()}
    db.psql(f"""INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
      ('{ids['old']}', '{MAPV_DRAFT}', '{{CHN.RC.T.A}}', '合成来源：已过期', '2019-01-01', '2019-03-01', 'published'),
      ('{ids['new']}', '{MAPV_DRAFT}', '{{CHN.RC.T.A}}', '合成来源：仍有效', '2019-01-01', '2100-01-01', 'published');""")
    return ids


def seed_grades(db, subject: str, n: int) -> dict[str, str]:
    """一名学生 n 条当前判分（判分 ID 随机，抽样按其哈希）"""
    s = new_student(db)
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
        calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
        SELECT '{s['student']}', '{subject}', gen_random_uuid(), now() - interval '2 hours', 'correct', 'syn-rubric@0.1.0', 1, 1, true, 1,
               'rubric:syn-rubric@0.1.0', 'algo:syn-rubric@0.1.0', false, 'formal', ARRAY[gen_random_uuid()], gen_random_uuid()
          FROM generate_series(1, {n});""")
    return s


def seed_answer(db) -> dict[str, str]:
    """掌握对账：一名学生一次已判分的独立作答（A 级证据），题目已准入并打标到合成节点；还没有掌握记录"""
    s = new_student(db)
    s.update(attempt=u(), evidence=u())
    key = f"synthetic-sched-mastery-{s['attempt']}"
    db.psql(f"""
      INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload, source_method,
        request_id, request_idempotency_key, recorded_at, evidence_kind, evidence_level, channel, map_version_id) VALUES
        ('{s['evidence']}', now() - interval '1 hour', '{s['student']}', 'aya', 'response_text', '{ITEM}', 'admitted',
         '{{"text": "（合成）"}}', 'grade', gen_random_uuid(), '{key}', now() - interval '1 hour', 'independent', 'A', 'keypad', '{MAPV_MATH}');
      INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
        request_idempotency_key, recorded_at, evidence_kind, evidence_level, channel, map_version_id) VALUES
        ('{s['attempt']}', now() - interval '1 hour', '{s['student']}', 'aya', '{ITEM}', '{s['evidence']}', 0, '{key}', now() - interval '1 hour',
         'independent', 'A', 'keypad', '{MAPV_MATH}');
      INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
        calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
        VALUES ('{s['student']}', 'aya', '{s['attempt']}', now() - interval '1 hour', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, false, 1,
                'verifier:aya-symbolic-ts@0.1.0;items:syn-pack@0.0.1', 'algo:aya-symbolic-ts@0.1.0', false, 'formal',
                ARRAY['{s['evidence']}'::uuid], gen_random_uuid());""")
    return s


def seed_metric_inputs(db, n: int = 16) -> dict[str, str]:
    """中圈指标重算（SIE-05）：一名学生一条当前节点键掌握行（--all 才会选到他）与 n 道近一天内带提示的已判分作答
    （提示等级 1–6 轮换；n ≥ 合成策略的最低样本 15 时，提示依赖度出结果），另有一道 30 天前的作答
    （在提示依赖度 14 天窗口外，但让学习持续性满 4 周、能出结果：定时作业 --skip persistence 时不应写它）。
    掌握行放在道法（zhixing）：掌握对账只配了数学，不会把这行没有证据的合成掌握当作 stale_estimate（测试互不影响）"""
    s = new_student(db)
    s["mastery"] = u()
    db.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
        algorithm_version, evidence_ids, is_current) VALUES
        ('{s['mastery']}', '{s['student']}', 'zhixing', 'SYN.SCHED.ZX.N1', '{LEVEL}', '{MAPV_MATH}', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn',
         ARRAY[gen_random_uuid()], true);
      CREATE TEMP TABLE sched_metric_attempt AS
        SELECT i AS n, gen_random_uuid() AS attempt_id, now() - interval '1 day' + i * interval '1 minute' AS at, 1 + (i - 1) % 6 AS hint
          FROM generate_series(1, {n}) i
        UNION ALL SELECT 0, gen_random_uuid(), now() - interval '30 days', 6;
      INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
        request_idempotency_key, evidence_kind, evidence_level, channel)
        SELECT attempt_id, at, '{s['student']}', 'aya', format('syn-sched-metric-%s', n), gen_random_uuid(), hint,
               format('synthetic-sched-metric-%s', attempt_id), 'hinted', 'A', 'keypad' FROM sched_metric_attempt;
      INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
        calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
        SELECT '{s['student']}', 'aya', attempt_id, at, 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0',
               'algo:aya-symbolic-ts@0.1.0', false, 'formal', ARRAY[gen_random_uuid()], gen_random_uuid() FROM sched_metric_attempt;""")
    return s
