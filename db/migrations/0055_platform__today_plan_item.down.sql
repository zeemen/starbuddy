-- 0055 回滚：删除执行函数恢复 0035 的写法（去掉保留期清理）；个人层表清单去掉今日计划快照；删除快照表、守卫与保留期配置。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。须先回滚 0056（行为事件写入函数要读这张表）。
-- 注意：回滚会删除全部今日计划快照（任务 ID 随之失效；已写的 plan_done / plan_skip 事件里的 plan_task_id 不再能对上快照行），
-- 请先导出 platform.today_plan_item。
-- 0056 的写入函数在函数体里读这张表（plpgsql 不登记依赖）：没先回滚 0056 时中止，免得计划事件写入一律报错
DO $$
BEGIN
  IF position('today_plan_item' IN (SELECT p.prosrc FROM pg_proc p
                                     WHERE p.oid = 'insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid)'::regprocedure)) > 0 THEN
    RAISE EXCEPTION '0055 回滚中止：insight.record_behavior_event 仍读 platform.today_plan_item，请先回滚 0056'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;
CREATE OR REPLACE FUNCTION platform.execute_due_erasures(p_now timestamptz) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  r platform.erasure_request;
  t platform.personal_table_registry;
  v_n integer := 0;
  v_count bigint;
  v_receipt jsonb;
  v_account uuid;
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'confirm_expired'
   WHERE status = 'pending' AND confirmed_at IS NULL AND requested_at < p_now - interval '24 hours';
  FOR r IN SELECT * FROM platform.erasure_request x
            WHERE x.status = 'pending' AND x.confirmed_at IS NOT NULL AND x.cooling_off_until <= p_now
            ORDER BY x.cooling_off_until, x.id FOR UPDATE SKIP LOCKED LOOP
    IF NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                    WHERE l.student_id = r.student_id AND l.guardian_account_id = r.requested_by AND l.status = 'active'
                      AND a.kind = 'guardian' AND a.status = 'active') THEN
      UPDATE platform.erasure_request SET status = 'cancelled', cancelled_at = p_now, cancel_reason = 'link_revoked' WHERE id = r.id;
      CONTINUE;
    END IF;
    SELECT s.account_id INTO v_account FROM platform.student_profile s WHERE s.id = r.student_id FOR UPDATE;
    PERFORM set_config('starbuddy.erasure_student', r.student_id::text, true);
    v_receipt := '{}';
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      v_receipt := v_receipt || jsonb_build_object(t.nspname || '.' || t.relname, v_count);
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('DELETE FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id;
    END LOOP;
    FOR t IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' ORDER BY delete_order LOOP
      EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', t.nspname, t.relname, t.student_predicate) USING r.student_id INTO v_count;
      IF v_count <> 0 THEN
        RAISE EXCEPTION '删除后 %.% 仍有 % 行', t.nspname, t.relname, v_count USING ERRCODE = 'internal_error';
      END IF;
    END LOOP;
    UPDATE platform.account SET status = 'disabled' WHERE id = v_account;
    UPDATE platform.student_profile SET erased_at = p_now WHERE id = r.student_id;
    PERFORM set_config('starbuddy.erasure_student', '', true);
    UPDATE platform.erasure_request
       SET status = 'executed', executed_at = p_now, receipt = v_receipt,
           scopes = (SELECT array_agg(DISTINCT g.erase_scope ORDER BY g.erase_scope) FROM platform.personal_table_registry g WHERE g.strategy = 'delete')
     WHERE id = r.id;
    v_n := v_n + 1;
  END LOOP;
  RETURN v_n;
END $$;

DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'today_plan_item';
REVOKE SELECT (student_id, plan_date) ON platform.today_plan_item FROM starbuddy_eraser;
REVOKE SELECT (id, student_id, plan_date) ON platform.today_plan_item FROM starbuddy_authz;
-- 表级授权与策略随表删除
DROP TABLE platform.today_plan_item;
DROP FUNCTION platform.today_plan_item_guard();
DROP FUNCTION platform.today_plan_retention_days();
