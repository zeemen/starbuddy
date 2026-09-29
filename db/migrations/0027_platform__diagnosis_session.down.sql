-- 0027 回滚：删除诊断会话与步骤（数据一并删除；诊断不产生对外结论，没有依赖它的展示）。
-- review_reason 的新值 process_observation 无法从枚举里删除（Postgres 不支持），保留不用；约束会禁止再写入它。
DELETE FROM platform.review_task WHERE source_kind = 'diagnosis_step';
CREATE OR REPLACE FUNCTION platform.review_task_detail(p_task uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); r platform.review_task; v_source jsonb;
BEGIN
  SELECT * INTO r FROM platform.review_task WHERE id = p_task;
  IF NOT FOUND THEN
    RAISE EXCEPTION '复核任务不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF r.source_kind = 'grade_result' THEN
    SELECT jsonb_build_object(
             'verdict', g.verdict, 'score_total', g.score_total, 'max_total', g.max_total, 'confidence', g.confidence,
             'calibrated', g.calibrated, 'rubric_version', g.rubric_version, 'source_version', g.source_version,
             'model_version', g.model_version, 'review_reasons', g.review_reasons::text[], 'evidence_ids', g.evidence_ids,
             'superseded', g.superseded_by IS NOT NULL, 'created_at', g.created_at,
             'dimensions', coalesce((SELECT jsonb_agg(jsonb_build_object(
                 'dimension_id', d.dimension_id, 'score', d.score, 'max_score', d.max_score, 'judged_by', d.judged_by,
                 'subjective', d.subjective, 'rule_ref', d.rule_ref, 'confidence', d.confidence, 'evidence_ids', d.evidence_ids)
                 ORDER BY d.dimension_id) FROM platform.grade_dimension d WHERE d.grade_result_id = g.id), '[]'::jsonb))
      INTO v_source FROM platform.grade_result g WHERE g.id = r.source_id;
  ELSIF r.source_kind = 'claim' THEN
    SELECT jsonb_build_object(
             'template_key', c.template_key, 'template_version', c.template_version, 'target_type', c.target_type,
             'status', c.status, 'confidence', c.confidence, 'source_type', c.source_type, 'model_version', c.model_version,
             'evidence_ids', c.evidence_ids, 'created_at', c.created_at)
      INTO v_source FROM insight.claim c WHERE c.id = r.source_id;
  END IF;
  RETURN jsonb_build_object(
    'id', r.id, 'subject', r.subject, 'student_ref', platform.review_student_ref(r.student_id), 'source_kind', r.source_kind,
    'source_id', r.source_id, 'reasons', r.reasons::text[], 'status', r.status, 'created_at', r.created_at,
    'assigned', r.assigned_to IS NOT NULL, 'assigned_to_me', r.assigned_to IS NOT NULL AND r.assigned_to = v_staff,
    'assigned_at', r.assigned_at, 'resolution_code', r.resolution_code, 'resolved_at', r.resolved_at,
    'resolved_by_me', r.resolved_by_account IS NOT NULL AND r.resolved_by_account = v_staff,
    'source', v_source);
END $$;
UPDATE platform.review_resolution_code SET applies_to = array_remove(applies_to, 'diagnosis_step') WHERE code = 'not_actionable';
DELETE FROM platform.review_resolution_code WHERE code = 'process_noted';
ALTER TABLE platform.review_resolution_code DROP CONSTRAINT review_resolution_code_applies_to_check;
ALTER TABLE platform.review_resolution_code ADD CONSTRAINT review_resolution_code_applies_to_check
  CHECK (cardinality(applies_to) >= 1 AND applies_to <@ ARRAY['grade_result', 'skill_observation', 'error_attribution', 'claim']);
DROP POLICY review_service_open ON platform.review_task;
CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning
  WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL
              AND assigned_to IS NULL AND assigned_at IS NULL
              AND NOT ('profile_dispute' = ANY (reasons::text[]))
              AND source_kind = 'grade_result' AND EXISTS (
                     SELECT 1 FROM platform.grade_result g
                      WHERE g.id = source_id AND g.student_id = review_task.student_id AND g.subject = review_task.subject
                        AND review_task.reasons::text[] <@ g.review_reasons::text[]));
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_diagnosis_step;
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution', 'claim'));
-- 防止回滚后再写入过程观察原因
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_no_process_observation CHECK (NOT ('process_observation' = ANY (reasons::text[])));
REVOKE SELECT ON platform.diagnosis_step FROM starbuddy_authz;
DROP TABLE platform.diagnosis_step;
DROP TABLE platform.diagnosis_session;
DROP FUNCTION platform.diagnosis_step_guard();
DROP FUNCTION platform.diagnosis_session_guard();
