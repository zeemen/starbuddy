-- 0028 platform · 洞察服务对平台推断与事实的最小只读授权（specs/sie-02b-claims-first-report/spec.md 待人确认 #4，按默认）
-- 状态：草稿。只写文件，由人执行（CLAUDE.md 必停项：RLS 与权限策略）。只改 platform schema（ADR 02 第 2 条）。
-- 洞察服务是判断账本的唯一写入方（ADR 13），判断经 basis_kind / basis_id 引用 platform 推断行；中圈指标（SIE 第五章）要读作答、判分、证据属性与错因。
-- 只授列级 SELECT，**不授任何原文列**：证据的 payload、media_ref，判分的维度与证据片段，都不给洞察服务（它不需要原文，也不应拿到）。
-- 不授任何写权限；按学生隔离由洞察服务负责（与学习服务的策略一致，ADR 13 待人确认 #1）。
-- 请以单事务执行（psql -1）。

GRANT SELECT ON platform.mastery_estimate TO starbuddy_insight;   -- 判断的依据行（KGE-04 第二步加节点键后同样可读）
GRANT SELECT (id, student_id, subject, skill_local_id, confidence, algorithm_version, evidence_ids, created_at, superseded_by)
  ON platform.skill_observation TO starbuddy_insight;
GRANT SELECT (id, student_id, subject, skill_local_id, subject_category, cross_subject_category, confidence, algorithm_version,
              evidence_ids, created_at, superseded_by)
  ON platform.error_attribution TO starbuddy_insight;
GRANT SELECT (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, confidence, human_review_required, created_at,
              superseded_by)
  ON platform.grade_result TO starbuddy_insight;
GRANT SELECT (attempt_id, occurred_at, student_id, subject, item_id, hint_level_used, elapsed_sec, evidence_kind, evidence_level, channel)
  ON platform.attempt TO starbuddy_insight;
GRANT SELECT (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, evidence_kind, evidence_level, channel, source_type)
  ON platform.evidence_event TO starbuddy_insight;

CREATE POLICY mastery_insight_read ON platform.mastery_estimate FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY observation_insight_read ON platform.skill_observation FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY attribution_insight_read ON platform.error_attribution FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY grade_insight_read ON platform.grade_result FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY attempt_insight_read ON platform.attempt FOR SELECT TO starbuddy_insight USING (true);
CREATE POLICY evidence_insight_read ON platform.evidence_event FOR SELECT TO starbuddy_insight USING (true);
