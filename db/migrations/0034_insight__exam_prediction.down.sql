-- 0034 回滚：删除对账卡、预测、冻结函数与考试安排、成绩录入函数；建档读取恢复成 0022 的样子；exam_record 恢复成绩必填。
-- 回滚前库里若已有考试安排（raw_score 为空），恢复 NOT NULL 会失败（有意如此：不静默删学生录入的记录，由人先处理）。
DROP TABLE insight.exam_reconciliation;
DROP TABLE insight.exam_prediction;
DROP FUNCTION insight.exam_reconciliation_guard();
DROP FUNCTION insight.exam_prediction_guard();
DROP FUNCTION insight.valid_reconciliation_sentences(jsonb);
DROP FUNCTION insight.freeze_due_predictions(timestamptz);
REVOKE USAGE ON SCHEMA insight FROM starbuddy_scheduler;
DROP FUNCTION insight.exam_freeze_at(date);
DROP FUNCTION insight.submit_exam_score(uuid, uuid, numeric);
DROP FUNCTION insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[]);
DROP FUNCTION insight.valid_code_set(text[], text[]);
DROP FUNCTION insight.valid_node_ids(text[], integer, integer);

CREATE OR REPLACE FUNCTION insight.onboarding_view(p_student uuid) RETURNS jsonb
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_role text := insight.onboarding_role(p_student);
  v_me uuid := platform.current_account_id();
BEGIN
  IF v_role IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object(
    'role', v_role,
    'reports', CASE v_role
      WHEN 'self' THEN (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), '{}') FROM insight.self_report r
                         WHERE r.student_id = p_student AND r.superseded_at IS NULL)
      ELSE (SELECT coalesce(jsonb_object_agg(r.field_id, r.value), '{}') FROM insight.guardian_report r
             WHERE r.student_id = p_student AND r.guardian_account_id = v_me AND r.superseded_at IS NULL) END,
    'exams', (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'id', e.id, 'subject', e.subject, 'exam_type', e.exam_type, 'exam_date', e.exam_date,
                'full_score', e.full_score, 'raw_score', e.raw_score, 'scope_note', e.scope_note, 'entered_by_role', e.entered_by_role,
                'rank_band', CASE WHEN v_role = 'guardian' THEN e.rank_band END,
                'difficulty_note', CASE WHEN v_role = 'guardian' THEN e.difficulty_note END)) ORDER BY e.exam_date DESC, e.created_at DESC), '[]')
               FROM insight.exam_record e
              WHERE e.student_id = p_student AND e.superseded_at IS NULL AND (v_role = 'guardian' OR e.entered_by_account_id = v_me)),
    'textbooks', (SELECT coalesce(jsonb_object_agg(t.subject, t.textbook_version), '{}') FROM platform.student_subject_textbook t
                   WHERE t.student_id = p_student AND t.updated_by = v_me),
    'progress', (SELECT coalesce(jsonb_agg(jsonb_build_object('step', p.step, 'status', p.status) ORDER BY p.at, p.id), '[]')
                   FROM insight.onboarding_progress p WHERE p.student_id = p_student AND p.respondent_account_id = v_me));
END $$;

DROP INDEX insight.exam_record_plan_link_idx;
DROP INDEX insight.exam_record_plan_uq;
ALTER TABLE insight.exam_record DROP CONSTRAINT exam_record_plan_link;
ALTER TABLE insight.exam_record DROP CONSTRAINT exam_record_plan;
ALTER TABLE insight.exam_record DROP COLUMN plan_record_id;
ALTER TABLE insight.exam_record ALTER COLUMN raw_score SET NOT NULL;
COMMENT ON COLUMN insight.exam_record.raw_score IS NULL;
