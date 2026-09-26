-- 测试辅助：断言函数。只在测试库里创建，不属于迁移。
\set QUIET on
SET client_min_messages = notice;
CREATE SCHEMA t;
GRANT USAGE ON SCHEMA t TO PUBLIC;

CREATE FUNCTION t.ok(cond boolean, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL: %', msg; END IF;
  RAISE NOTICE 'ok - %', msg;
END $$;

-- 执行 sql，期望以指定 SQLSTATE 失败
CREATE FUNCTION t.throws(sql text, expected_state text, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE sql;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = expected_state THEN
      RAISE NOTICE 'ok - % (%)', msg, SQLSTATE;
      RETURN;
    END IF;
    RAISE EXCEPTION 'FAIL: % — expected %, got % %', msg, expected_state, SQLSTATE, SQLERRM;
  END;
  RAISE EXCEPTION 'FAIL: % — expected error %, but statement succeeded', msg, expected_state;
END $$;

-- 同 t.throws，并要求错误信息包含指定片段：同一 SQLSTATE 可能来自不同约束，用它确认拦住语句的正是目标规则
CREATE FUNCTION t.throws_like(sql text, expected_state text, expected_fragment text, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE sql;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = expected_state AND position(expected_fragment IN SQLERRM) > 0 THEN
      RAISE NOTICE 'ok - % (%)', msg, SQLSTATE;
      RETURN;
    END IF;
    RAISE EXCEPTION 'FAIL: % — expected % "%", got % %', msg, expected_state, expected_fragment, SQLSTATE, SQLERRM;
  END;
  RAISE EXCEPTION 'FAIL: % — expected error %, but statement succeeded', msg, expected_state;
END $$;

-- 以某个角色、某个账号的身份计数
CREATE FUNCTION t.as_account(acct uuid) RETURNS void LANGUAGE sql AS $$
  SELECT set_config('app.account_id', coalesce(acct::text, ''), false) $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;
