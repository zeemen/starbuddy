-- 0023 回滚：删掉领取、放回、读函数、抽检、异议入队与结案码表，结案函数与学习服务进队策略恢复成 0006 的样子。
-- 枚举值 profile_dispute 删不掉（Postgres 不支持），留在 platform.review_reason 里无害；画像异议任务在回滚前要先由人处理掉，
-- 否则恢复 source_kind 约束会失败（有意如此：不静默删复核记录）。
DROP FUNCTION platform.enqueue_claim_dispute(uuid);
DROP FUNCTION platform.review_sample_spot_checks(platform.subject, timestamptz, numeric);
DROP FUNCTION platform.review_task_detail(uuid);
DROP FUNCTION platform.review_queue(platform.subject, text, text, integer, timestamptz, uuid);
DROP FUNCTION platform.release_review_task(uuid);
DROP FUNCTION platform.take_review_task(uuid);

CREATE OR REPLACE FUNCTION platform.resolve_review_task(p_task uuid, p_resolution_code text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.account a
                  WHERE a.id = platform.current_account_id() AND a.kind = 'staff' AND a.status = 'active') THEN
    RAISE EXCEPTION 'only an active staff account can resolve review tasks' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE platform.review_task
     SET status = 'resolved', resolved_at = now(), resolved_by_account = platform.current_account_id(),
         resolution_code = p_resolution_code
   WHERE id = p_task AND status <> 'resolved';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'review task not found or already resolved' USING ERRCODE = 'invalid_parameter_value';
  END IF;
END $$;

DROP FUNCTION platform.review_student_ref(uuid);
DROP FUNCTION platform.review_current_staff();

GRANT SELECT ON platform.review_task TO starbuddy_review;
CREATE POLICY review_staff_read ON platform.review_task FOR SELECT TO starbuddy_review USING (true);
REVOKE INSERT (subject, student_id, source_kind, source_id, reasons) ON platform.review_task FROM starbuddy_learning;
GRANT INSERT ON platform.review_task TO starbuddy_learning;
DROP POLICY review_service_open ON platform.review_task;
CREATE POLICY review_service_open ON platform.review_task FOR INSERT TO starbuddy_learning
  WITH CHECK (status = 'open' AND resolved_at IS NULL AND resolved_by_account IS NULL);

-- 只收回 0023 新授的维度表；grade_result 的 SELECT 是 0005 给 check_supersede_target 的，不能动（安全审查 B2）
REVOKE SELECT ON platform.grade_dimension FROM starbuddy_authz;
REVOKE INSERT ON platform.review_task FROM starbuddy_authz;
REVOKE UPDATE (assigned_to, assigned_at) ON platform.review_task FROM starbuddy_authz;
-- 0023 之后领取过、仍在处理中的任务放回待处理，再删领取列
UPDATE platform.review_task SET status = 'open', assigned_to = NULL, assigned_at = NULL WHERE status = 'in_progress';
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_assignment;
ALTER TABLE platform.review_task DROP COLUMN assigned_at, DROP COLUMN assigned_to;
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_claim_is_dispute;
ALTER TABLE platform.review_task DROP CONSTRAINT review_task_source_kind_check;
ALTER TABLE platform.review_task ADD CONSTRAINT review_task_source_kind_check
  CHECK (source_kind IN ('grade_result', 'skill_observation', 'error_attribution'));

DROP TABLE platform.review_resolution_code;
COMMENT ON TABLE platform.review_task IS NULL;
