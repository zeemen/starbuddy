"""SIE-03 库上测试的合成夹具（规则 8：全部是占位编码，不是真实数据），在 pginsight 的一次性库上补一次（会话级夹具 sie03db）。

- S4（家长 GC 绑定，学生账号 SA4）：16 条证据（B 级，不影响既有断言里的 A 级计数）与同一时刻、同一题的作答（“数据有误”按证据
  定位作答用），第 1 题有一条判分（改判用）；4 条当前掌握行 M4（依据行）；
- S5（家长 GC 绑定，学生账号 SA5）：删除执行用；1 条当前掌握行；
- 模板：考试预测、考后对账（已审核，文本是明显占位）；另有一条“对账·命中禁用词”的模板（只为验证阻断：渲染命中词表不出卡）；
- 角色：scheduler_app（只继承 starbuddy_scheduler，调度器入口用）。
平台事实行以超级用户在 session_replication_role = replica 下直接插入（与 pginsight 同一做法），只为构造夹具。
"""
from __future__ import annotations

GC = "c0000000-0000-4000-8000-00000000000c"
SA4, SA5 = "a4000000-0000-4000-8000-0000000000a4", "a5000000-0000-4000-8000-0000000000a5"
S4, S5 = "c4000000-0000-4000-8000-0000000000c4", "c5000000-0000-4000-8000-0000000000c5"
LINK_C4, LINK_C5 = "11000000-0000-4000-8000-0000000000c4", "11000000-0000-4000-8000-0000000000c5"
E4 = [f"e4000000-0000-4000-8000-0000000000{i:02d}" for i in range(1, 17)]
AT4 = [f"a4a00000-0000-4000-8000-0000000000{i:02d}" for i in range(1, 17)]
M4 = [f"6d400000-0000-4000-8000-0000000000{i:02d}" for i in range(1, 5)]
M5 = "6d500000-0000-4000-8000-000000000001"
TPL_PREDICT, TPL_RECONCILE, TPL_RECONCILE_BAD = "syn.exam.prediction", "syn.exam.reconcile", "syn.exam.reconcile_bad"
TPL_PREDICT_AGENT = "syn.exam.prediction_agent"  # 上限只到 agent（可见范围交集为空的反例）
TEXT_PREDICT = "【占位模板·未审核】预测｜{low}｜{high}"
TEXT_RECONCILE = "【占位模板·未审核】对账｜{position}"
TEXT_RECONCILE_BAD = "【占位模板·反例】你很聪明｜{position}"  # 只为验证阻断（命中“智力或天赋”）
SCHEDULER_ROLE = "scheduler_app"

_ev = ",\n".join(
    f"('{E4[i]}', date_trunc('minute', now()) - interval '{20 - i} days', '{S4}', 'aya', 'response_text', 'syn-item-{i + 1:03d}', "
    f"'admitted', '{{\"text\": \"syn\"}}', 'grade', gen_random_uuid(), 'synthetic-sie03-ev-{i:04d}', 'independent', 'B', 'keypad', 'objective')"
    for i in range(16))
_at = ",\n".join(
    f"('{AT4[i]}', date_trunc('minute', now()) - interval '{20 - i} days', '{S4}', 'aya', 'syn-item-{i + 1:03d}', '{E4[i]}', 0, "
    f"'synthetic-sie03-at-{i:04d}', 'independent', 'B', 'keypad')"
    for i in range(16))

SEED_SQL = f"""
INSERT INTO platform.account (id, kind) VALUES ('{GC}', 'guardian'), ('{SA4}', 'student'), ('{SA5}', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('d4000000-0000-4000-8000-0000000000d4', '{GC}'), ('d5000000-0000-4000-8000-0000000000d5', '{GC}');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('{S4}', '{SA4}', 'd4000000-0000-4000-8000-0000000000d4', 8, 'nanjing', 'bubian-2024', '2013-04-01'),
  ('{S5}', '{SA5}', 'd5000000-0000-4000-8000-0000000000d5', 9, 'nanjing', 'bubian-2024', '2012-06-01');
INSERT INTO platform.guardian_student_link (id, guardian_account_id, student_id, granted_via) VALUES
  ('{LINK_C4}', '{GC}', '{S4}', 'household_creator'), ('{LINK_C5}', '{GC}', '{S5}', 'household_creator');
-- 两份同意（合成文本版本）：0059 起周报、指标选人只取 platform.student_processing_allowed 为真的学生（LT-06）
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
  SELECT s, '{GC}', t, replace(t, '_', '-') || '@synthetic-0', 'granted', now() - interval '30 days'
    FROM unnest(ARRAY['{S4}', '{S5}']::uuid[]) s, unnest(ARRAY['privacy_policy', 'guardian_consent_under14']) t;
SET session_replication_role = replica;
INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload, source_method,
  request_id, request_idempotency_key, evidence_kind, evidence_level, channel, source_type) VALUES
{_ev};
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
  request_idempotency_key, evidence_kind, evidence_level, channel) VALUES
{_at};
INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
  calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
  SELECT '{S4}', 'aya', attempt_id, occurred_at, 'incorrect', 'aya-symbolic-ts@0.1.0', 0, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0',
         'algo:aya-symbolic-ts@0.1.0', false, 'formal', ARRAY[gen_random_uuid()], gen_random_uuid()
    FROM platform.attempt WHERE student_id = '{S4}';
INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version,
  evidence_ids, is_current) VALUES
  ('{M4[0]}', '{S4}', 'aya', 'SYN-M4-01', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E4[0]},{E4[1]}}}', true),
  ('{M4[1]}', '{S4}', 'aya', 'SYN-M4-02', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E4[2]},{E4[3]}}}', true),
  ('{M4[2]}', '{S4}', 'aya', 'SYN-M4-03', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E4[4]},{E4[5]}}}', true),
  ('{M4[3]}', '{S4}', 'aya', 'SYN-M4-04', 'transfer', 0.9, 0.9, 'kge-mastery@0.1.0.syn', '{{{E4[6]},{E4[7]}}}', true),
  ('{M5}', '{S5}', 'aya', 'SYN-M5-01', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn', '{{{E4[0]}}}', true);
SET session_replication_role = origin;
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('{TPL_PREDICT}', 1, 'exam', '{{agent,student,guardian}}', '120 days', '{TEXT_PREDICT}', 'approved', 'test-reviewer', now()),
  ('{TPL_RECONCILE}', 1, 'exam', '{{agent,student,guardian}}', '60 days', '{TEXT_RECONCILE}', 'approved', 'test-reviewer', now()),
  ('{TPL_PREDICT_AGENT}', 1, 'exam', '{{agent}}', '120 days', NULL, 'approved', 'test-reviewer', now()),
  ('{TPL_RECONCILE_BAD}', 1, 'exam', '{{agent,student,guardian}}', '60 days', '{TEXT_RECONCILE_BAD}', 'approved', 'test-reviewer', now());
CREATE ROLE {SCHEDULER_ROLE} LOGIN IN ROLE starbuddy_scheduler;
"""


def reset(db, students: tuple[str, ...] = (S4,)) -> None:
    """清掉这些学生的 SIE-03 行与判断（超级用户 replica 模式，只为夹具；守卫不许应用角色删）"""
    ids = ", ".join(f"'{s}'" for s in students)
    db.psql(f"""SET session_replication_role = replica;
      DELETE FROM insight.exam_reconciliation WHERE student_id IN ({ids});
      DELETE FROM insight.exam_prediction WHERE student_id IN ({ids});
      DELETE FROM insight.intervention_log WHERE student_id IN ({ids});
      DELETE FROM insight.dispute WHERE student_id IN ({ids});
      DELETE FROM platform.review_task WHERE student_id IN ({ids});
      DELETE FROM insight.claim_history WHERE claim_id IN (SELECT id FROM insight.claim WHERE student_id IN ({ids}));
      DELETE FROM insight.claim WHERE student_id IN ({ids});
      DELETE FROM insight.exam_record WHERE student_id IN ({ids});
      DELETE FROM insight.behavior_event WHERE student_id IN ({ids});""")
