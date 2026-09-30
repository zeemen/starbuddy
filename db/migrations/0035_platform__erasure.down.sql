-- 0035 回滚：删除个人层表清单、删除请求、导出任务与相关函数；0022 的两个守卫恢复成不可删除；收回 0035 的授权；删档案“已删除”标记。
-- 注意：回滚会删掉删除回执与导出任务记录。已执行过删除请求的库回滚前，须由人先把回执另行留档（lessons 待人确认）。
-- 角色 starbuddy_eraser 是集群级的，不在这里删（由人处理 db/bootstrap/roles.sql）。
DROP FUNCTION platform.execute_due_erasures(timestamptz);
DROP FUNCTION platform.export_student_data(uuid);
DROP FUNCTION platform.cancel_erasure(uuid);
DROP FUNCTION platform.confirm_erasure(uuid);
DROP FUNCTION platform.request_erasure(uuid);
DROP TABLE platform.export_job;
DROP TABLE platform.erasure_request;
DROP FUNCTION platform.reject_export_job_change();
DROP FUNCTION platform.erasure_request_guard();
DROP TABLE platform.personal_table_registry;

CREATE OR REPLACE FUNCTION insight.guard_fact_update() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$
BEGIN
  IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION '建档事实只追加，不能删除' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL
     OR (to_jsonb(NEW) - 'superseded_at') IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_at') THEN
    RAISE EXCEPTION '建档事实只能把当前行标为已被替换' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION insight.reject_progress_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog
  AS $$ BEGIN RAISE EXCEPTION '建档进度只追加' USING ERRCODE = 'insufficient_privilege'; END $$;

REVOKE SELECT (student_id, guardian_account_id, topic, content, created_at) ON platform.guardian_report FROM starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.erasure_in_progress(uuid) FROM starbuddy_eraser;
REVOKE DELETE ON insight.exam_reconciliation, insight.exam_prediction, insight.dispute, insight.intervention_log,
  insight.claim, insight.claim_history, insight.behavior_event, insight.confidence_probe, insight.self_report, insight.guardian_report,
  insight.onboarding_progress, insight.exam_record, platform.review_task, platform.diagnosis_step, platform.diagnosis_session,
  platform.grade_dimension, platform.grade_result, platform.mastery_estimate, platform.skill_observation, platform.error_attribution,
  platform.attempt, platform.evidence_event, platform.evidence_registry, platform.ingest_request, platform.replay_seal,
  platform.guardian_report, platform.student_subject_textbook, platform.subject_enrollment, platform.student_time_guard,
  platform.guardian_invite, platform.auth_session, platform.account_contact FROM starbuddy_eraser;
REVOKE SELECT (student_id) ON insight.exam_reconciliation, insight.exam_prediction, insight.dispute, insight.intervention_log,
  insight.behavior_event, insight.confidence_probe, insight.self_report, insight.guardian_report, insight.onboarding_progress,
  insight.exam_record, platform.review_task, platform.mastery_estimate, platform.skill_observation, platform.error_attribution,
  platform.attempt, platform.evidence_event, platform.evidence_registry, platform.ingest_request, platform.replay_seal,
  platform.guardian_report, platform.student_subject_textbook, platform.subject_enrollment, platform.student_time_guard,
  platform.guardian_invite FROM starbuddy_eraser;
REVOKE SELECT (id, student_id) ON insight.claim, platform.diagnosis_session, platform.grade_result FROM starbuddy_eraser;
REVOKE SELECT (claim_id) ON insight.claim_history FROM starbuddy_eraser;
REVOKE SELECT (session_id) ON platform.diagnosis_step FROM starbuddy_eraser;
REVOKE SELECT (grade_result_id) ON platform.grade_dimension FROM starbuddy_eraser;
REVOKE SELECT (account_id) ON platform.auth_session, platform.account_contact FROM starbuddy_eraser;
REVOKE SELECT (id, account_id, household_id, erased_at) ON platform.student_profile FROM starbuddy_eraser;
REVOKE UPDATE (erased_at) ON platform.student_profile FROM starbuddy_eraser;
REVOKE SELECT (id, kind, status) ON platform.account FROM starbuddy_eraser;
REVOKE UPDATE (status) ON platform.account FROM starbuddy_eraser;
REVOKE SELECT (student_id, guardian_account_id, status) ON platform.guardian_student_link FROM starbuddy_eraser;
REVOKE USAGE ON SCHEMA platform, insight FROM starbuddy_eraser;
ALTER TABLE platform.student_profile DROP COLUMN erased_at;
