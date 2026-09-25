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

-- 以某个角色、某个账号的身份计数
CREATE FUNCTION t.as_account(acct uuid) RETURNS void LANGUAGE sql AS $$
  SELECT set_config('app.account_id', coalesce(acct::text, ''), false) $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;
