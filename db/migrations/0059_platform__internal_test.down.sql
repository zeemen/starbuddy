-- 0059 回滚：删除问题反馈与退出测试的表、函数、触发器、授权与个人层表清单登记，判分抽检恢复 0023 的定义。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- **禁止在测试结束统一删除之前回滚**：回滚会删掉全部反馈与退出记录，已退出的档案随之不再被拦（BFF 判据、批量任务过滤、
-- 拒绝再签同意与签发学生会话都依赖本迁移），等于让退出的家庭重新进入处理。只在演练副本上，或测试结束、全部家庭已按手册走完删除流程之后执行。
-- **不导出明文**：回滚前不得把反馈文本或退出记录导出成文件（规则 8、13）；需要留存的只有审核台“退出状态”页的核对结论（不含学生标识）。
-- 退出时追加的同意撤回记录在 platform.consent_record 里，只追加，保留。
DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname IN ('feedback', 'internal_test_exit');
REVOKE INSERT ON platform.consent_record FROM starbuddy_authz;
DROP TRIGGER auth_session_exited_guard ON platform.auth_session;
DROP TRIGGER consent_record_exited_guard ON platform.consent_record;
DROP FUNCTION platform.guard_exited_session();
DROP FUNCTION platform.guard_exited_consent();
-- 判分抽检恢复 0023 的定义（属主与授权保持不变）
CREATE OR REPLACE FUNCTION platform.review_sample_spot_checks(p_subject platform.subject, p_since timestamptz, p_ratio numeric)
  RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  IF p_subject IS NULL OR p_since IS NULL OR p_ratio IS NULL OR p_ratio <= 0 OR p_ratio > 0.5 THEN
    RAISE EXCEPTION 'subject, since and a ratio in (0, 0.5] are required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_since < now() - interval '31 days' OR p_since > now() THEN
    RAISE EXCEPTION 'since must be within the last 31 days' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
  SELECT g.subject, g.student_id, 'grade_result', g.id, ARRAY['spot_check']::platform.review_reason[]
    FROM platform.grade_result g
   WHERE g.subject = p_subject AND g.created_at >= p_since AND g.superseded_by IS NULL
     AND ('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(p_ratio * 4294967296)
     AND NOT EXISTS (SELECT 1 FROM platform.review_task t WHERE t.source_kind = 'grade_result' AND t.source_id = g.id)
  ON CONFLICT (source_kind, source_id) DO NOTHING;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;
DROP FUNCTION platform.student_processing_allowed(uuid);
DROP FUNCTION platform.review_internal_test_exits(integer);
DROP FUNCTION platform.review_feedback_set_status(uuid, text);
DROP FUNCTION platform.review_feedback_list(text, integer, timestamptz, uuid);
DROP FUNCTION platform.internal_test_ref(uuid);
DROP FUNCTION platform.exit_internal_test(uuid);
-- 策略引用 internal_test_exited：先删表（策略、表级与列级授权随表删除）
DROP TABLE platform.feedback;
DROP FUNCTION platform.internal_test_exited(uuid);
DROP TABLE platform.internal_test_exit;
