-- 0022 回滚：删除建档事实层（学生自述、家长观察、考试记录、建档进度）与相关函数。角色 starbuddy_report 不删（集群级）
DROP FUNCTION insight.onboarding_stats(timestamptz, timestamptz);
DROP FUNCTION insight.onboarding_view(uuid);
DROP FUNCTION insight.record_onboarding_step(uuid, text, text);
DROP FUNCTION insight.submit_exam_record(uuid, platform.subject, text, date, numeric, numeric, text, text, text);
DROP FUNCTION insight.onboarding_role(uuid);
DROP FUNCTION insight.submit_guardian_report(uuid, text, jsonb);
DROP FUNCTION insight.submit_self_report(uuid, text, jsonb);
DROP TABLE insight.onboarding_progress;
DROP TABLE insight.exam_record;
DROP TABLE insight.guardian_report;
DROP TABLE insight.self_report;
DROP FUNCTION insight.reject_progress_change();
DROP FUNCTION insight.guard_fact_update();
DROP FUNCTION insight.valid_report_value(text, jsonb);
DROP FUNCTION insight.valid_set(jsonb, text[], integer);
DROP FUNCTION insight.valid_text(jsonb);
DROP TABLE insight.report_field;
REVOKE USAGE ON SCHEMA insight FROM starbuddy_report;
