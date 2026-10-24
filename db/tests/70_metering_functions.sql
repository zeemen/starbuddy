-- 0009 计量写入函数与选科上限（specs/metering-impl-sprint2/spec.md 第 5 节：单连接内能测的都在这里）
-- 并发与断线在 services/bff 的集成测试里用多连接测。
-- 时钟：把 platform.meter_now() 换成读 t.clock 的版本（只在测试库里做，不进迁移），以便推进时间。
-- 60 号测试留下的订阅先全部结束，这里经函数重新开通。

CREATE TABLE t.clock (now timestamptz NOT NULL);
INSERT INTO t.clock VALUES ('2026-10-15 00:00:00+00');
GRANT SELECT ON t.clock TO starbuddy_authz;
CREATE OR REPLACE FUNCTION platform.meter_now() RETURNS timestamptz
  LANGUAGE sql STABLE SET search_path = pg_catalog AS $$ SELECT now FROM t.clock $$;
UPDATE platform.subscription SET status = 'ended' WHERE status <> 'ended';

-- 调用辅助：以 BFF 角色、某个账号的身份预留，返回 outcome
CREATE FUNCTION t.reserve(acct uuid, student uuid, res text, k text, fp text, qty numeric, cfp text) RETURNS jsonb
  LANGUAGE plpgsql AS $$
DECLARE v jsonb;
BEGIN
  PERFORM t.as_account(acct);
  SET LOCAL ROLE starbuddy_bff;
  v := platform.meter_reserve(student, res, k, fp, qty, cfp, CASE WHEN res LIKE 'diagnosis%' THEN 'diagnose' ELSE 'grade' END);
  RESET ROLE;
  RETURN v;
END $$;

-- ================= 一、函数属性与授权 =================
SELECT t.ok(platform.meter_uuidv5('6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'www.example.com') = '2ed6657d-e927-568b-95e1-2665a8aea6a2',
            'uuidv5 与 RFC 4122 的标准向量一致（DNS 命名空间 + www.example.com）');
SELECT t.ok(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001')::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab]'
            AND platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001')
                = platform.meter_uuidv5('3b1e4c1a-6f0d-4d3e-9a52-7c8e2f1b5a40', 'b0b00000-0000-4000-8000-000000000001'),
            'usage_id 是 uuidv5（版本位 5、变体位 RFC 4122）');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'platform'
     AND p.proname IN ('meter_reserve', 'meter_settle', 'meter_release', 'meter_expire_due', 'meter_renew_due',
                       'meter_open_subscription', 'meter_check_entitlement', 'meter_grant_period', 'meter_same_key',
                       'meter_lock_reservation', 'meter_authorize_usage', 'meter_current_config', 'meter_subject_limit',
                       'enroll_subject', 'pause_subject')
     AND NOT (p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
              AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false))),
  '写入函数都是 SECURITY DEFINER、属主 starbuddy_authz、固定 search_path');
SELECT t.ok(
  (SELECT bool_and(has_function_privilege('starbuddy_bff', f, 'EXECUTE')) FROM unnest(ARRAY[
     'platform.meter_reserve(uuid, text, text, text, numeric, text, text)', 'platform.meter_settle(uuid, numeric)',
     'platform.meter_release(uuid, text)', 'platform.meter_open_subscription(uuid, text)',
     'platform.enroll_subject(uuid, platform.subject)', 'platform.pause_subject(uuid, platform.subject)']::regprocedure[]) f)
  AND NOT (SELECT bool_or(has_function_privilege('starbuddy_bff', f, 'EXECUTE')) FROM unnest(ARRAY[
     'platform.meter_expire_due(integer)', 'platform.meter_renew_due(integer)', 'platform.meter_check_entitlement(uuid)',
     'platform.meter_grant_period(uuid)', 'platform.meter_lock_reservation(uuid)', 'platform.meter_authorize_usage(uuid)',
     'platform.meter_same_key(platform.usage_reservation, uuid, text, text, text, text)']::regprocedure[]) f),
  'BFF 只能调用用户请求类函数，不能调用定时任务与内部函数');
SELECT t.ok(
  (SELECT bool_and(has_function_privilege('starbuddy_scheduler', f, 'EXECUTE')) FROM unnest(ARRAY[
     'platform.meter_expire_due(integer)', 'platform.meter_renew_due(integer)']::regprocedure[]) f)
  AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname IN ('platform', 'insight', 'kge', 'policy') AND p.prosecdef
                     AND has_function_privilege('starbuddy_scheduler', p.oid, 'EXECUTE')
                     AND p.oid NOT IN ('platform.meter_expire_due(integer)'::regprocedure, 'platform.meter_renew_due(integer)'::regprocedure,
                                       'platform.review_sample_spot_checks(platform.subject, timestamptz, numeric)'::regprocedure,
                                       'insight.freeze_due_predictions(timestamptz)'::regprocedure,
                                       'platform.execute_due_erasures(timestamptz)'::regprocedure,
                                       'platform.student_processing_allowed(uuid)'::regprocedure))
  AND NOT (SELECT bool_or(has_function_privilege('starbuddy_scheduler', f, 'EXECUTE')) FROM unnest(ARRAY[
     'platform.meter_reserve(uuid, text, text, text, numeric, text, text)', 'platform.meter_settle(uuid, numeric)',
     'platform.meter_release(uuid, text)', 'platform.meter_open_subscription(uuid, text)',
     'platform.enroll_subject(uuid, platform.subject)', 'platform.pause_subject(uuid, platform.subject)',
     'platform.meter_check_entitlement(uuid)', 'platform.meter_grant_period(uuid)']::regprocedure[]) f),
  '定时任务角色只能调用过期、续期与抽检（0023），以及冻结预测（0034）、执行删除请求（0035）与只读的处理判据（0059）');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_review']) r(role)
   CROSS JOIN (SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'platform' AND (p.proname LIKE 'meter\_%' OR p.proname IN ('enroll_subject', 'pause_subject'))) f
   WHERE has_function_privilege(r.role, f.oid, 'EXECUTE')),
  '学习服务、复核角色不能调用任何计量或选科函数');
-- 函数属主：只有函数需要的最小写权限（设计文档第 7 节）
SELECT t.ok(has_table_privilege('starbuddy_authz', 'platform.usage_ledger', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_authz', 'platform.usage_ledger', 'UPDATE')
            AND has_column_privilege('starbuddy_authz', 'platform.usage_reservation', 'state', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.usage_reservation', 'entitlement_id', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.usage_reservation', 'expires_at', 'UPDATE')
            AND has_column_privilege('starbuddy_authz', 'platform.entitlement', 'reserved', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.entitlement', 'fair_use_margin', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.subscription', 'source', 'UPDATE')
            AND NOT has_any_column_privilege('starbuddy_authz', 'platform.plan_allowance', 'UPDATE')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['platform.subscription', 'platform.entitlement', 'platform.usage_reservation',
                                                       'platform.usage_ledger', 'platform.plan_config', 'platform.plan_allowance',
                                                       'platform.resource_type']::regclass[]) c
                             WHERE has_table_privilege('starbuddy_authz', c, 'DELETE')
                                OR has_table_privilege('starbuddy_authz', c, 'TRUNCATE')),
            '函数属主只有列级的最小写权限，不能删、不能清空、不能改配置');

-- ================= 二、开通订阅 =================
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');  -- 学生本人不能开通
SELECT t.throws_like($$SELECT platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'trial')$$,
                     '42501', 'not permitted', '学生不能给自己开通订阅（家长是支付主体）');
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');  -- 另一户家长
SELECT t.throws_like($$SELECT platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'trial')$$,
                     '42501', 'not permitted', '不能给别家的学生开通订阅');
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws($$SELECT platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'paid')$$,
                '22023', '付费订阅不能经这个函数开通（等支付接入）');
SELECT t.ok(platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'pilot_virtual') = '{"outcome": "not_in_pilot"}',
            '不在试点名单里的家庭开不了试点额度');
SELECT t.as_account(NULL);
RESET ROLE;
INSERT INTO platform.pilot_household (household_id, cohort_code) VALUES ('d1000000-0000-4000-8000-0000000000d1', 'synthetic_cohort');
SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws($$SELECT * FROM platform.pilot_household$$, '42501', 'BFF 读不到试点名单');
SELECT t.ok(platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'pilot_virtual')
            @> '{"outcome": "opened", "config_version": "synthetic-plan@1"}',
            '名单里的家庭开通试点额度；配置取当前已发布版本（不由调用方指定）');
SELECT t.ok(platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'pilot_virtual')->>'outcome'
            = 'already_active', '重复开通返回“已有有效订阅”');
SELECT t.as_account('b0000000-0000-4000-8000-00000000000b');
SELECT t.ok(platform.meter_open_subscription('c2000000-0000-4000-8000-0000000000c2', 'trial')->>'outcome'
            = 'opened', '另一户开通基础会员');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok((SELECT count(*) FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
              WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active') = 7
            AND (SELECT granted FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active'
                    AND e.resource_type = 'diagnosis_full') = 6
            AND (SELECT period_start FROM platform.subscription WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1'
                  AND status = 'active') = '2026-10-15 00:00:00+00'
            AND (SELECT tier FROM platform.subscription WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active') = 'pro'
            AND (SELECT tier FROM platform.subscription WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2' AND status = 'active') = 'basic'
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation),
            '开通时按配置建 7 个额度桶、写 grant 流水；试点固定 PRO、基础会员固定 basic；周期从计量时钟起算；对账为零行');

-- ================= 三、预留、结算、释放 =================
SELECT t.throws_like($$SELECT t.reserve('a0000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '42501', 'not permitted', '家长不能代孩子发起消耗（spec 待人确认 #1 默认）');
SELECT t.throws_like($$SELECT t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '42501', 'not permitted', '别的学生不能扣这个学生的额度（同手机号也不行）');
SELECT t.throws_like($$SELECT t.reserve(NULL, 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '42501', 'not permitted', '未登录不能预留');
SELECT t.throws($$SELECT t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'B0B00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '22023', 'key 必须是小写 UUIDv4');
SELECT t.throws($$SELECT t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-1000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '22023', 'key 必须是版本 4');
SELECT t.throws($$SELECT t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 2, NULL)$$,
                '22023', '数量不能超过单次上限');
SELECT t.throws($$SELECT t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis',
                                   'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)$$,
                '22023', '错题分析必须带内容指纹（用于 24 小时复看）');

SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.throws($$SELECT platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                    'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL, 'grade')$$,
                '22023', '资源与方法必须对应（完整诊断只能记在 diagnose 上）');
SELECT t.throws($$SELECT platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                    'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL, NULL)$$,
                '22023', '来源方法必填');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)
            @> jsonb_build_object('outcome', 'reserved', 'reserved_qty', 1, 'remaining', 5,
                                  'usage_id', platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001')),
            '学生本人预留成功；usage_id 由 key 派生；剩余 5');
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)
            @> '{"outcome": "existing", "state": "reserved"}', '同 key 同请求重试：拿回同一笔预留，不再扣');
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('2', 64), 1, NULL)
            = '{"outcome": "conflict"}', '同 key 换请求体 → 冲突');
SELECT t.ok(t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c2000000-0000-4000-8000-0000000000c2', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)
            = '{"outcome": "conflict"}', '别的学生撞上同一个 key → 冲突，而且不透露已有记录的任何信息');
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, 'item:aya:synthetic-0301-07')
            = '{"outcome": "conflict"}', '同 key 换内容指纹 → 冲突');
SELECT t.ok((SELECT reserved FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
              WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active' AND e.resource_type = 'diagnosis_full') = 1,
            '重试与冲突都没有多扣');

SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws_like($$SELECT platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 1)$$,
                '42501', 'not permitted', '家长不能替孩子结算');
SELECT t.throws_like($$SELECT platform.meter_release(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 'upstream_failed')$$,
                     '42501', 'not permitted', '家长不能替孩子释放');
SELECT t.throws_like($$SELECT platform.meter_settle('00000000-0000-4000-8000-000000000000', 1)$$,
                '42501', 'not permitted', '不存在的 usage_id 与无权限同样拒绝（不泄露是否存在）');
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.throws($$SELECT platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 2)$$,
                '22023', '结算数量不能超过预留');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 1)
            @> '{"outcome": "settled", "idempotent": false}', '成功结算');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 1)
            @> '{"outcome": "settled", "idempotent": true}', '重复结算返回首次结果，不报错、不再扣');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 0)
            @> '{"outcome": "conflict"}', '换数量重复结算 → 冲突');
SELECT t.ok(platform.meter_release(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000001'), 'upstream_failed')
            @> '{"outcome": "conflict", "state": "settled"}', '已结算的不能释放');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000001', repeat('1', 64), 1, NULL)
            @> '{"outcome": "existing", "state": "settled", "settled_qty": 1}', '已结算后同 key 重试：返回首次结果，不扣第二次');

-- 失败回滚
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000002', repeat('1', 64), 1, NULL)->>'outcome' = 'reserved', '第二次预留');
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok(platform.meter_release(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000002'), 'upstream_failed')
            @> '{"outcome": "released", "idempotent": false}', '确定失败 → 释放');
SELECT t.ok(platform.meter_release(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000002'), 'upstream_failed')
            @> '{"outcome": "released", "idempotent": true}', '重复释放是幂等的');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000002'), 1)
            @> '{"outcome": "conflict", "state": "released"}', '释放后迟到的成功不能再结算');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok((SELECT reserved = 0 AND settled = 1 FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
              WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active' AND e.resource_type = 'diagnosis_full'),
            '失败释放退回额度：已用 1、预留 0');

-- 额度不足（基础会员完整诊断 1 次）
SELECT t.ok(t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c2000000-0000-4000-8000-0000000000c2', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000003', repeat('3', 64), 1, NULL)->>'outcome' = 'reserved', 'S2 用掉唯一一次');
SELECT t.ok(t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c2000000-0000-4000-8000-0000000000c2', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000004', repeat('4', 64), 1, NULL)
            = '{"outcome": "insufficient", "remaining": 0}', '额度用完 → 额度不足（预留中的也算占用）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), '预留、结算、释放之后对账为零行');
SELECT t.ok(t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c2000000-0000-4000-8000-0000000000c2', 'mock_exam',
                      'b0b00000-0000-4000-8000-000000000005', repeat('5', 64), 1, NULL)
            = '{"outcome": "insufficient", "remaining": 0}', '档位没有的资源（基础会员模考 0 次）→ 额度不足');

-- ================= 四、24 小时复看（按学生去重） =================
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis',
                      'b0b00000-0000-4000-8000-000000000011', repeat('a', 64), 1, 'img:' || repeat('7', 64))->>'outcome' = 'reserved',
            '首次分析一道错题：扣费预留');
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis',
                      'b0b00000-0000-4000-8000-000000000012', repeat('b', 64), 1, 'img:' || repeat('7', 64))
            = '{"outcome": "dedup_in_progress"}', '首次还没结算时复看 → 处理中（不会两次都扣）');
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000011'), 1);
SELECT t.as_account(NULL);
RESET ROLE;
UPDATE t.clock SET now = now + interval '23 hours';
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis',
                      'b0b00000-0000-4000-8000-000000000012', repeat('b', 64), 1, 'img:' || repeat('7', 64))
            @> jsonb_build_object('outcome', 'dedup', 'reserved_qty', 0,
                                  'dedup_of', platform.meter_usage_id('b0b00000-0000-4000-8000-000000000011')),
            '23 小时后复看：预留 0，指向首次');
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000012'), 0) @> '{"outcome": "settled"}',
            '复看结算 0');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok(t.reserve('b1000000-0000-4000-8000-0000000000b1', 'c2000000-0000-4000-8000-0000000000c2', 'mistake_analysis',
                      'b0b00000-0000-4000-8000-000000000013', repeat('c', 64), 1, 'img:' || repeat('7', 64))->>'outcome' = 'reserved',
            '另一个学生看同一道题：各扣各的（按学生去重）');
UPDATE t.clock SET now = now + interval '2 hours';
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'mistake_analysis',
                      'b0b00000-0000-4000-8000-000000000014', repeat('d', 64), 1, 'img:' || repeat('7', 64))->>'outcome' = 'reserved',
            '25 小时后再看：重新扣费');
SELECT t.ok((SELECT reserved = 1 AND settled = 1 FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
              WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active' AND e.resource_type = 'mistake_analysis'),
            '错题分析：首次扣 1、复看扣 0、25 小时后又预留 1');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), '复看之后对账为零行');

-- ================= 五、过期释放（定时任务） =================
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000021', repeat('1', 64), 1, NULL)->>'outcome' = 'reserved', '预留后断线（不再回来）');
UPDATE t.clock SET now = now + interval '11 minutes';
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000021'), 1)
            @> '{"outcome": "conflict", "state": "expired"}', '过了过期时间、过期任务还没跑到时也不能结算（是否扣费不取决于任务节奏）');
SELECT t.as_account(NULL);
SELECT t.throws($$SELECT platform.meter_expire_due(100)$$, '42501', 'BFF 不能调用过期任务');
RESET ROLE;
SET ROLE starbuddy_scheduler;
SELECT t.throws($$SELECT platform.meter_expire_due(0)$$, '22023', '批量大小必须在 1–1000');
SELECT t.ok(platform.meter_expire_due(100) = 4, '定时任务过期了 4 笔（断线的诊断、S1 与 S2 未结算的错题、S2 未结算的诊断）');
SELECT t.ok(platform.meter_expire_due(100) = 0, '再跑一次没有可过期的（幂等）');
SELECT t.throws($$SELECT * FROM platform.usage_reservation$$, '42501', '定时任务角色读不到任何计量表');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok(platform.meter_settle(platform.meter_usage_id('b0b00000-0000-4000-8000-000000000021'), 1)
            @> '{"outcome": "conflict", "state": "expired"}', '过期后迟到的成功不扣费（宁可少扣）');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000021', repeat('1', 64), 1, NULL)
            @> '{"outcome": "existing", "state": "expired"}', '过期后同 key 重试：返回已过期，不重新预留');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '到这里为止，两个对账视图都是零行');

-- 单桶核对：计数被篡改时，下一次写入整体失败
BEGIN;
UPDATE platform.entitlement e SET granted = granted + 1 FROM platform.subscription s
 WHERE s.id = e.subscription_id AND s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active'
   AND e.resource_type = 'diagnosis_full';
SELECT t.throws($$SELECT t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                                   'b0b00000-0000-4000-8000-000000000031', repeat('1', 64), 1, NULL)$$,
                '23000', '额度桶计数与流水对不上时，写入函数拒绝执行');
ROLLBACK;

-- ================= 六、续期（含降级到期生效） =================
SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') = '{"outcome": "enrolled", "max_active_subjects": 4}',
            'PRO 可以同时开通 4 科：再选文砺');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'zhixing') = '{"outcome": "not_launched"}',
            '没有已发布课程的学科不能选');
SELECT t.as_account(NULL);
RESET ROLE;
UPDATE platform.subscription SET next_tier = 'basic', next_config_version = 'synthetic-plan@1'
 WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active';
UPDATE t.clock SET now = '2026-11-14 00:00:01+00';
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000041', repeat('1', 64), 1, NULL)
            = '{"outcome": "no_entitlement"}', '周期结束、还没续期时没有可用额度桶');
SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT platform.meter_renew_due(100)$$, '42501', 'BFF 不能调用续期任务');
RESET ROLE;
SET ROLE starbuddy_scheduler;
SELECT t.ok(platform.meter_renew_due(100) = 2, '续期了两个不收费的订阅');
RESET ROLE;
SELECT t.ok((SELECT tier = 'basic' AND next_tier IS NULL AND period_start = '2026-11-14 00:00:00+00'
               FROM platform.subscription WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active')
            AND (SELECT granted FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active'
                    AND e.resource_type = 'diagnosis_full' AND e.period_start = s.period_start) = 1
            AND (SELECT count(*) FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active') = 14,
            '续期应用降级：新周期按基础会员发放；上一周期的额度桶保留');
SELECT t.ok((SELECT array_agg(subject::text ORDER BY subject) FROM platform.subject_enrollment
              WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active') = ARRAY['aya'],
            '降级到基础会员后超出上限的科目被暂停（保留先选的阿鸦）');
SELECT t.ok(t.reserve('a1000000-0000-4000-8000-0000000000a1', 'c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full',
                      'b0b00000-0000-4000-8000-000000000041', repeat('1', 64), 1, NULL)->>'outcome' = 'reserved',
            '续期后可以继续预留');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation)
            AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch), '续期后对账仍为零行');

SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.throws($$SELECT source_idempotency_key FROM platform.usage_ledger$$, '42501', 'BFF 读不到流水的来源 key（数据最小化）');
SELECT t.ok((SELECT count(*) FROM (SELECT source_method, kind, quantity FROM platform.usage_ledger) x) > 0, 'BFF 可以读流水的展示列');
SELECT t.as_account(NULL);
RESET ROLE;

-- ================= 七、选科上限（RLS 变更附测试） =================
SET ROLE starbuddy_bff;
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.throws($$INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'wenli')$$,
                '42501', 'BFF 不能再直接写选科表');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya') = '{"outcome": "already_active"}',
            '已选的科目再选 → 已是有效');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') = '{"outcome": "limit_reached", "max_active_subjects": 1}',
            '基础会员只能同时开通 1 科');
SELECT t.ok(platform.pause_subject('c1000000-0000-4000-8000-0000000000c1', 'aya') = '{"outcome": "paused"}', '暂停一科');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') @> '{"outcome": "enrolled"}', '暂停后可以换一科');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya') @> '{"outcome": "limit_reached"}',
            '恢复暂停的科目同样受上限约束');
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok(platform.pause_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') = '{"outcome": "paused"}', '已绑定的家长可以替孩子暂停');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya') @> '{"outcome": "enrolled"}', '已绑定的家长可以替孩子选科');
SELECT t.throws_like($$SELECT platform.enroll_subject('c2000000-0000-4000-8000-0000000000c2', 'wenli')$$, '42501', 'not permitted', '不能给别家的学生选科');
SELECT t.throws_like($$SELECT platform.pause_subject('c2000000-0000-4000-8000-0000000000c2', 'aya')$$, '42501', 'not permitted', '不能给别家的学生暂停选科');
SELECT t.as_account(NULL);
RESET ROLE;
BEGIN;
UPDATE platform.subscription SET tier = 'max' WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active';
SET LOCAL ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') = '{"outcome": "enrolled", "max_active_subjects": 2}',
            'MAX 的 all_launched 按已发布课程的学科数计（夹具里是阿鸦、文砺两科）');
ROLLBACK;
SELECT t.as_account(NULL);
BEGIN;
UPDATE platform.subscription SET status = 'ended' WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active';
SET LOCAL ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok(platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli') = '{"outcome": "no_subscription"}',
            '没有有效订阅不能选科');
ROLLBACK;
SELECT t.as_account(NULL);
