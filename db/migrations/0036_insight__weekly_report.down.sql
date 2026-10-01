-- 0036 回滚：删除周报快照、抽样函数、校验函数与守卫；复核队列恢复成 0027 的样子；个人层表清单去掉周报。
-- 注意：回滚会删除全部周报快照与周报抽检的复核记录（含已结案的审计痕迹），请先导出 insight.weekly_report 与
-- platform.review_task 中 source_kind = 'weekly_report' 的行。
DROP FUNCTION platform.review_sample_weekly_reports(date, numeric, integer);
DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'weekly_report';
REVOKE SELECT (student_id) ON insight.weekly_report FROM starbuddy_eraser;
REVOKE DELETE ON insight.weekly_report FROM starbuddy_eraser;
DROP TABLE insight.weekly_report;  -- 先删周报（它的抽检标记外键指向复核任务）
DROP FUNCTION insight.weekly_report_guard();
DROP FUNCTION insight.valid_weekly_objective(jsonb);
DROP FUNCTION insight.valid_weekly_sentences(jsonb, text);
DELETE FROM platform.review_task WHERE source_kind = 'weekly_report';
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
  ELSIF r.source_kind = 'diagnosis_step' THEN
    -- 过程观察（0027）：给选题引用、判分引用与过程原文证据的引用（审核台按 ID 另取原文）；不给选题理由
    SELECT jsonb_build_object(
             'kind', st.kind, 'node_id', st.node_id, 'item_id', st.item_id, 'grade_result_id', st.grade_result_id,
             'work_evidence_id', st.work_evidence_id,
             'evidence_ids', g.evidence_ids, 'verdict', g.verdict, 'answered_at', st.answered_at)
      INTO v_source FROM platform.diagnosis_step st LEFT JOIN platform.grade_result g ON g.id = st.grade_result_id
     WHERE st.id = r.source_id;
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
DELETE FROM platform.review_resolution_code WHERE code IN ('weekly_report_ok', 'weekly_report_issue');
ALTER TABLE platform.review_resolution_code DROP CONSTRAINT review_resolution_code_applies_to_check;
ALTER TABLE platform.review_resolution_code ADD CONSTRAINT review_resolution_code_applies_to_check
  CHECK (cardinality(applies_to) >= 1 AND applies_to <@ ARRAY['grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step']);
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_weekly_report;
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution', 'claim', 'diagnosis_step'));
