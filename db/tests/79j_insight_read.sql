-- 0028 洞察服务对平台推断与事实的最小只读授权（specs/sie-02b-claims-first-report/spec.md 待人确认 #4）。
-- 夹具来自 10_fixtures（学生 c1、c2 的作答、证据、判分、掌握、观测、错因）。整个文件在一个事务里，结束回滚。
BEGIN;
CREATE FUNCTION t.as_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.as_role(text, text) TO PUBLIC;

-- ================= 能读：依据行与中圈指标需要的列 =================
SELECT t.ok(t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.mastery_estimate')::int
            = (SELECT count(*) FROM platform.mastery_estimate), '洞察服务能读全部掌握估计（判断依据行）');
SELECT t.ok(t.as_role('starbuddy_insight', 'SELECT count(verdict)::text FROM platform.grade_result')::int
            = (SELECT count(*) FROM platform.grade_result), '洞察服务能读判分结论');
SELECT t.ok(t.as_role('starbuddy_insight', 'SELECT count(hint_level_used)::text || '':'' || count(*)::text FROM platform.attempt') IS NOT NULL,
            '洞察服务能读作答的提示等级与用时');
SELECT t.ok(t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.evidence_event')::int
            = (SELECT count(*) FROM platform.evidence_event), '洞察服务能读证据的种类、等级与来源');
SELECT t.ok(t.as_role('starbuddy_insight', 'SELECT count(subject_category)::text FROM platform.error_attribution')::int
            = (SELECT count(*) FROM platform.error_attribution), '洞察服务能读错因类别');

-- ================= 不能读：原文与维度 =================
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT payload::text FROM platform.evidence_event LIMIT 1')$$, '42501', '读不到证据原文 payload');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT media_ref FROM platform.evidence_event LIMIT 1')$$, '42501', '读不到证据的媒体引用');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.grade_dimension')$$, '42501', '读不到判分维度');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT source_request_id::text FROM platform.grade_result LIMIT 1')$$, '42501', '读不到判分的请求键');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT response_evidence_id::text FROM platform.attempt LIMIT 1')$$, '42501', '读不到作答的原文证据引用');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.account')$$, '42501', '读不到账号表');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.guardian_student_link')$$, '42501', '读不到家庭绑定');

-- ================= 不能写 =================
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'UPDATE platform.mastery_estimate SET confidence = confidence RETURNING 1')$$, '42501', '不能改掌握估计');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'DELETE FROM platform.grade_result RETURNING 1')$$, '42501', '不能删判分');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c WHERE c.relnamespace = 'platform'::regnamespace AND c.relkind IN ('r', 'p')
     AND (has_any_column_privilege('starbuddy_insight', c.oid, 'INSERT') OR has_any_column_privilege('starbuddy_insight', c.oid, 'UPDATE')
          OR has_table_privilege('starbuddy_insight', c.oid, 'DELETE') OR has_table_privilege('starbuddy_insight', c.oid, 'TRUNCATE'))),
  '洞察服务对 platform 任何表都没有写权限');

-- ================= 其他角色不受影响 =================
SELECT t.ok(NOT has_column_privilege('starbuddy_kge_read', 'platform.attempt', 'student_id', 'SELECT')
            AND NOT has_column_privilege('starbuddy_report', 'platform.attempt', 'student_id', 'SELECT'),
            '只读知识服务与报表角色仍读不到作答');
ROLLBACK;
