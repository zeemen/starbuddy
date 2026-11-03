-- 学习服务测试库的计量夹具（LT-04，只在一次性测试库里执行，不属于迁移；全部合成数据，规则 8）。
-- 学习服务写入前经 0058 的 platform.learning_meter_check 核对计量；生产里预留与计次由 BFF（meter_reserve / meter_practice）建，
-- 学习服务的测试不经 BFF，所以由这个只在测试库里存在的函数代建：
--   learningtest.provision_meter(usage_id, 幂等键, 学生, 方法, counted)：
--     counted = true  → 题库自练计次一行（再练、复测作答）；
--     counted = false → 该生的测试订阅与额度桶（没有就建）上一笔预留中的预留（grade → mistake_analysis，diagnose → diagnosis_full）。
--   usage_id 或幂等键已有记录、学生不存在时什么都不做（核对照常按真实记录判定）。
-- 只由超级用户调用（conftest 的 admin 池、联调栈的 admin 池）：schema 与函数一律不授给 starbuddy_learning 或任何业务角色——
-- 否则学习服务角色自己就能造预留，计量核对形同虚设（安全审查阻断）。
-- 防护：库名必须是一次性开发 / 测试库（starbuddy_dev* / starbuddy_test*），否则立即报错、什么都不建；迁移后的库里不应有
-- learningtest（db/tests/79zf 断言）。
-- 可重复执行（联调栈与 conftest 都会执行它）。
\set ON_ERROR_STOP on
DO $$
BEGIN
  IF current_database() !~ '^starbuddy_(dev|test)' THEN
    RAISE EXCEPTION 'meter_provision.sql: only for disposable starbuddy_dev* / starbuddy_test* databases (current: %)', current_database();
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS learningtest;
REVOKE ALL ON SCHEMA learningtest FROM PUBLIC;
REVOKE ALL ON SCHEMA learningtest FROM starbuddy_learning;

INSERT INTO platform.plan_config (version, note_code)
SELECT 'learningtest-plan@1', 'learning_test' WHERE NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE version = 'learningtest-plan@1');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'learningtest-plan@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance a
 WHERE config_version = 'ch8-hypothesis@1'
   AND (SELECT status FROM platform.plan_config WHERE version = 'learningtest-plan@1') = 'draft'
   AND NOT EXISTS (SELECT 1 FROM platform.plan_allowance b WHERE b.config_version = 'learningtest-plan@1' AND b.tier = a.tier
                                                             AND b.resource_type = a.resource_type);
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'learningtest-plan@1', tier, feature_code, value_code FROM platform.plan_feature f
 WHERE config_version = 'ch8-hypothesis@1'
   AND (SELECT status FROM platform.plan_config WHERE version = 'learningtest-plan@1') = 'draft'
   AND NOT EXISTS (SELECT 1 FROM platform.plan_feature g WHERE g.config_version = 'learningtest-plan@1' AND g.tier = f.tier
                                                           AND g.feature_code = f.feature_code);
UPDATE platform.plan_config SET status = 'published', published_at = now()
 WHERE version = 'learningtest-plan@1' AND status = 'draft';

CREATE OR REPLACE FUNCTION learningtest.provision_meter(p_usage uuid, p_key text, p_student uuid, p_method text, p_counted boolean,
                                                       p_lapsed boolean DEFAULT false)
  RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_res text := CASE WHEN p_method = 'diagnose' THEN 'diagnosis_full' ELSE 'mistake_analysis' END;
  v_sub uuid;
  v_ent uuid;
  v_now timestamptz := platform.meter_now();
BEGIN
  IF current_database() !~ '^starbuddy_(dev|test)' THEN
    RAISE EXCEPTION 'learningtest.provision_meter: only for disposable starbuddy_dev* / starbuddy_test* databases';
  END IF;
  IF p_usage IS NULL OR p_key IS NULL OR p_student IS NULL OR length(p_key) < 16
     OR NOT EXISTS (SELECT 1 FROM platform.student_profile WHERE id = p_student) THEN
    RETURN;
  END IF;
  IF p_counted THEN
    INSERT INTO platform.practice_log (usage_id, idempotency_key, student_id, occurred_at)
    VALUES (p_usage, p_key, p_student, v_now) ON CONFLICT DO NOTHING;
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM platform.usage_reservation WHERE usage_id = p_usage OR idempotency_key = p_key) THEN
    RETURN;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('learningtest.provision:' || p_student, 0));
  SELECT id INTO v_sub FROM platform.subscription
   WHERE student_id = p_student AND status IN ('active', 'grace') AND source <> 'addon' AND period_end > v_now;
  IF v_sub IS NULL THEN
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    SELECT household_id, id, 'pro', 'learningtest-plan@1', 'pilot_virtual', v_now - interval '1 day', v_now + interval '29 days'
      FROM platform.student_profile WHERE id = p_student
    RETURNING id INTO v_sub;
  END IF;
  SELECT id INTO v_ent FROM platform.entitlement
   WHERE subscription_id = v_sub AND resource_type = v_res AND v_now >= period_start AND v_now < period_end;
  IF v_ent IS NULL THEN
    INSERT INTO platform.entitlement (subscription_id, student_id, resource_type, period_start, period_end, granted)
    SELECT v_sub, p_student, v_res, period_start, period_end, 100000 FROM platform.subscription WHERE id = v_sub
    RETURNING id INTO v_ent;
  END IF;
  INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type,
                                          reserved_qty, source_method, reserved_at, expires_at)
  -- p_lapsed：已过期但过期任务还没跑（行仍是 reserved，到期时间已过；预留行的时间建好后不能再改，所以只能建时给定）
  VALUES (p_usage, p_key, repeat('0', 64), v_ent, p_student, v_res, 1, p_method,
          CASE WHEN p_lapsed THEN v_now - interval '9 minutes' ELSE v_now END,
          CASE WHEN p_lapsed THEN v_now - interval '1 minute' ELSE v_now + interval '10 minutes' END)
  ON CONFLICT DO NOTHING;
END $$;
REVOKE EXECUTE ON FUNCTION learningtest.provision_meter(uuid, text, uuid, text, boolean, boolean) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION learningtest.provision_meter(uuid, text, uuid, text, boolean, boolean) FROM starbuddy_learning;

-- 测试用的状态改写（只在测试库）：把一笔预留推到 settled / released / expired，或改成“已过期未处理”（lapsed）
CREATE OR REPLACE FUNCTION learningtest.set_reservation(p_usage uuid, p_state text) RETURNS void
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_state = 'settled' THEN
    UPDATE platform.usage_reservation SET state = 'settled', settled_qty = reserved_qty, finished_at = platform.meter_now() WHERE usage_id = p_usage;
  ELSIF p_state IN ('released', 'expired') THEN
    UPDATE platform.usage_reservation SET state = p_state::platform.reservation_state, finished_at = platform.meter_now() WHERE usage_id = p_usage;
  ELSE
    RAISE EXCEPTION 'unsupported state %', p_state;
  END IF;
END $$;
REVOKE EXECUTE ON FUNCTION learningtest.set_reservation(uuid, text) FROM PUBLIC;
