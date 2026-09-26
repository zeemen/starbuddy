-- 0009 回滚：删函数，恢复 BFF 直接写选科的策略与授权，收回函数属主的表权限
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

GRANT INSERT ON platform.subject_enrollment TO starbuddy_bff;
CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff
  WITH CHECK (platform.can_read_student(student_id));

DROP FUNCTION IF EXISTS platform.pause_subject(uuid, platform.subject);
DROP FUNCTION IF EXISTS platform.enroll_subject(uuid, platform.subject);
DROP FUNCTION IF EXISTS platform.meter_open_subscription(uuid, platform.plan_tier, text, text);
DROP FUNCTION IF EXISTS platform.meter_renew_due(integer);
DROP FUNCTION IF EXISTS platform.meter_expire_due(integer);
DROP FUNCTION IF EXISTS platform.meter_release(uuid, text);
DROP FUNCTION IF EXISTS platform.meter_settle(uuid, numeric);
DROP FUNCTION IF EXISTS platform.meter_lock_reservation(uuid);
DROP FUNCTION IF EXISTS platform.meter_same_key(platform.usage_reservation, uuid, text, text);
DROP FUNCTION IF EXISTS platform.meter_reserve(uuid, text, text, text, numeric, text, text);
DROP FUNCTION IF EXISTS platform.meter_grant_period(uuid);
DROP FUNCTION IF EXISTS platform.meter_check_entitlement(uuid);
DROP FUNCTION IF EXISTS platform.meter_usage_id(text);
DROP FUNCTION IF EXISTS platform.meter_uuidv5(uuid, text);
DROP FUNCTION IF EXISTS platform.meter_now();
REVOKE USAGE ON SCHEMA platform FROM starbuddy_scheduler;
-- pgcrypto 由本迁移启用；如果其他 schema 也在用，执行回滚的人删掉下面这一行
DROP EXTENSION IF EXISTS pgcrypto;
