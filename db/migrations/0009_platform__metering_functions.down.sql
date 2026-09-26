-- 0009 回滚：删函数与试点名单，恢复 BFF 直接写选科的策略与授权、流水的整表 SELECT，收回函数属主的表权限
REVOKE UPDATE (status) ON platform.subject_enrollment FROM starbuddy_authz;
REVOKE UPDATE (state, settled_qty, finished_at) ON platform.usage_reservation FROM starbuddy_authz;
REVOKE UPDATE (granted, reserved, settled) ON platform.entitlement FROM starbuddy_authz;
REVOKE UPDATE (tier, config_version, status, period_start, period_end, next_tier, next_config_version)
  ON platform.subscription FROM starbuddy_authz;
REVOKE INSERT ON platform.subscription, platform.entitlement, platform.usage_reservation, platform.usage_ledger,
  platform.subject_enrollment FROM starbuddy_authz;
REVOKE SELECT ON platform.resource_type, platform.plan_allowance, platform.plan_feature, platform.subscription,
  platform.entitlement, platform.usage_reservation, platform.usage_ledger, platform.curriculum_version,
  platform.subject_enrollment FROM starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.plan_config_lock_key(text) FROM starbuddy_authz;

REVOKE SELECT (entry_id, occurred_at, entitlement_id, student_id, usage_id, kind, quantity, corrects_entry_id,
               corrects_occurred_at, corrects_kind, actor, reason_code, source_method) ON platform.usage_ledger FROM starbuddy_bff;
GRANT SELECT ON platform.usage_ledger TO starbuddy_bff;
GRANT INSERT ON platform.subject_enrollment TO starbuddy_bff;
CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff
  WITH CHECK (platform.can_read_student(student_id));

DROP FUNCTION IF EXISTS platform.pause_subject(uuid, platform.subject);
DROP FUNCTION IF EXISTS platform.enroll_subject(uuid, platform.subject);
DROP FUNCTION IF EXISTS platform.meter_open_subscription(uuid, text);
DROP FUNCTION IF EXISTS platform.meter_renew_due(integer);
DROP FUNCTION IF EXISTS platform.meter_expire_due(integer);
DROP FUNCTION IF EXISTS platform.meter_release(uuid, text);
DROP FUNCTION IF EXISTS platform.meter_settle(uuid, numeric);
DROP FUNCTION IF EXISTS platform.meter_authorize_usage(uuid);
DROP FUNCTION IF EXISTS platform.meter_lock_reservation(uuid);
DROP FUNCTION IF EXISTS platform.meter_same_key(platform.usage_reservation, uuid, text, text, text, text);
DROP FUNCTION IF EXISTS platform.meter_reserve(uuid, text, text, text, numeric, text, text);
DROP TABLE IF EXISTS platform.pilot_household;
DROP FUNCTION IF EXISTS platform.meter_grant_period(uuid);
DROP FUNCTION IF EXISTS platform.meter_check_entitlement(uuid);
DROP FUNCTION IF EXISTS platform.meter_subject_limit(text);
DROP FUNCTION IF EXISTS platform.meter_current_config();
DROP FUNCTION IF EXISTS platform.meter_method_allowed(text, text);
DROP FUNCTION IF EXISTS platform.meter_usage_id(text);
DROP FUNCTION IF EXISTS platform.meter_uuidv5(uuid, text);
DROP FUNCTION IF EXISTS platform.meter_now();
DROP FUNCTION IF EXISTS platform.meter_sha1(bytea);
REVOKE USAGE ON SCHEMA platform FROM starbuddy_scheduler;
-- 只删 0009 自己建的 pgcrypto（装在 starbuddy_ext 里）；此前已存在于别的 schema 的 pgcrypto 保留不动
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
              WHERE e.extname = 'pgcrypto' AND n.nspname = 'starbuddy_ext') THEN
    DROP EXTENSION pgcrypto;
  END IF;
  DROP SCHEMA IF EXISTS starbuddy_ext;
END $$;
