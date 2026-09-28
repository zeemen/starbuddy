-- 0015 计量与权益 v2（specs/metering-v2/spec.md 验收标准）。沿用 70 号测试的可推进时钟 t.clock。
-- 合成数据（规则 8）：新建两户（H3 与 H2 家长同手机号，用来测“同手机号只能领一次试用”；H4 全新），手机号用未分配的 +999。

RESET ROLE;
SELECT t.as_account(NULL);
UPDATE platform.subscription SET status = 'ended' WHERE status <> 'ended';
UPDATE platform.subject_enrollment SET status = 'paused', paused_at = NULL WHERE status = 'active';
-- 规格书 V1.2 的额度配置由人发布；测试里发布后它成为当前配置
UPDATE platform.plan_config SET status = 'published', published_at = '2026-11-30 00:00:00+00' WHERE version = 'v12-pilot@1';
UPDATE t.clock SET now = '2026-12-01 02:00:00+00';  -- 上海时间 10:00

INSERT INTO platform.account (id, kind) VALUES
  ('3a000000-0000-4000-8000-00000000003a', 'guardian'), ('3b000000-0000-4000-8000-00000000003b', 'student'),
  ('4a000000-0000-4000-8000-00000000004a', 'guardian'), ('4b000000-0000-4000-8000-00000000004b', 'student'),
  ('5a000000-0000-4000-8000-00000000005a', 'guardian');   -- H4 的第二位家长（新手机号）
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('3a000000-0000-4000-8000-00000000003a', '+99900000002', now()),   -- 与 H2 家长 gB 同号
  ('4a000000-0000-4000-8000-00000000004a', '+99900000004', now()),
  ('5a000000-0000-4000-8000-00000000005a', '+99900000005', now());
INSERT INTO platform.household (id, created_by_account) VALUES
  ('d3000000-0000-4000-8000-0000000000d3', '3a000000-0000-4000-8000-00000000003a'),
  ('d4000000-0000-4000-8000-0000000000d4', '4a000000-0000-4000-8000-00000000004a');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('c3000000-0000-4000-8000-0000000000c3', '3b000000-0000-4000-8000-00000000003b', 'd3000000-0000-4000-8000-0000000000d3', 8, 'nanjing', 'bubian-2024', '2013-02-01'),
  ('c4000000-0000-4000-8000-0000000000c4', '4b000000-0000-4000-8000-00000000004b', 'd4000000-0000-4000-8000-0000000000d4', 9, 'nanjing', 'bubian-2024', '2012-06-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('3a000000-0000-4000-8000-00000000003a', 'c3000000-0000-4000-8000-0000000000c3', 'household_creator'),
  ('4a000000-0000-4000-8000-00000000004a', 'c4000000-0000-4000-8000-0000000000c4', 'household_creator'),
  ('5a000000-0000-4000-8000-00000000005a', 'c4000000-0000-4000-8000-0000000000c4', 'invite');

-- 辅助：幂等键、以某账号身份调用（BFF 角色）
CREATE FUNCTION t.k(n integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format('b0b15000-0000-4000-8000-%s', lpad(n::text, 12, '0')) $$;
CREATE FUNCTION t.call(acct uuid, q text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE v jsonb;
BEGIN
  PERFORM t.as_account(acct);
  SET LOCAL ROLE starbuddy_bff;
  EXECUTE 'SELECT (' || q || ')::jsonb' INTO v;
  RESET ROLE;
  PERFORM t.as_account(NULL);
  RETURN v;
END $$;
CREATE FUNCTION t.renew() RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v integer;
BEGIN
  SET LOCAL ROLE starbuddy_scheduler;
  v := platform.meter_renew_due(100);
  RESET ROLE;
  RETURN v;
END $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;

-- ================= 一、函数属性与授权 =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'platform'
     AND p.proname IN ('meter_reserve_impl', 'meter_reserve_upload', 'meter_practice', 'set_time_guard', 'meter_upgrade',
                       'meter_student_state', 'meter_state_of', 'meter_main_subscription', 'meter_daily_cap',
                       'meter_plan_daily_cap', 'meter_in_curfew', 'meter_minutes_today', 'meter_slot_limit',
                       'meter_uses_slots', 'meter_apply_subject_limits')
     AND NOT (p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
              AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false))),
  '0015 的函数都是 SECURITY DEFINER、属主 starbuddy_authz、固定 search_path');
SELECT t.ok(
  (SELECT bool_and(has_function_privilege('starbuddy_bff', f, 'EXECUTE')) FROM unnest(ARRAY[
     'platform.meter_reserve_upload(uuid, text, text, text, integer)', 'platform.meter_practice(uuid, text)',
     'platform.set_time_guard(uuid, integer, time, time)', 'platform.meter_student_state(uuid)']::regprocedure[]) f)
  AND NOT (SELECT bool_or(has_function_privilege(r, f, 'EXECUTE'))
             FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_scheduler', 'starbuddy_learning', 'starbuddy_review']) r,
                  unnest(ARRAY['platform.meter_reserve_impl(uuid, text, text, text, numeric, text, text, boolean)',
                               'platform.meter_upgrade(uuid, platform.plan_tier)',
                               'platform.meter_apply_subject_limits(uuid, text, platform.plan_tier)',
                               'platform.meter_state_of(uuid)', 'platform.meter_phone_digest(text)']::regprocedure[]) f),
  'BFF 只能调用用户请求类的新函数；内部实现、升级补差（支付接入前）任何应用角色都不能调用');
SELECT t.ok(NOT has_table_privilege('starbuddy_bff', 'platform.meter_secret', 'SELECT')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.trial_claim', 'SELECT')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.trial_claim_phone', 'SELECT')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.student_time_guard', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_bff', 'platform.student_time_guard', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.practice_log', 'INSERT')
            AND NOT has_table_privilege('starbuddy_authz', 'platform.practice_log', 'DELETE')
            AND NOT has_any_column_privilege('starbuddy_authz', 'platform.practice_log', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.subject_catalog', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_authz', 'platform.subject_catalog', 'UPDATE'),
            'BFF 读不到密钥与试用领取记录；时间设置、自练记录、学科目录都不能直接写');

-- ================= 二、试用：14 天、一次性、到期转留存 =================
SELECT t.ok(t.call('b0000000-0000-4000-8000-00000000000b',
                   $$platform.meter_open_subscription('c2000000-0000-4000-8000-0000000000c2', 'trial')$$)
            = '{"outcome": "trial_already_claimed"}', '同一家庭领过试用（70 号测试）不能再领');
SELECT t.ok(t.call('3a000000-0000-4000-8000-00000000003a',
                   $$platform.meter_open_subscription('c3000000-0000-4000-8000-0000000000c3', 'trial')$$)
            = '{"outcome": "trial_already_claimed"}', '换一个家庭，但家长手机号领过试用，也不能再领');
SELECT t.ok(t.call('4a000000-0000-4000-8000-00000000004a',
                   $$platform.meter_open_subscription('c4000000-0000-4000-8000-0000000000c4', 'trial')$$)
            @> '{"outcome": "opened", "config_version": "v12-pilot@1", "period_end": "2026-12-15T02:00:00+00:00"}',
            '新家庭开通试用：14 天，配置取当前已发布版本');
SELECT t.ok((SELECT tier FROM platform.subscription WHERE student_id = 'c4000000-0000-4000-8000-0000000000c4' AND status = 'active') = 'basic'
            AND (SELECT count(*) FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c4000000-0000-4000-8000-0000000000c4') = 10
            AND (SELECT granted FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c4000000-0000-4000-8000-0000000000c4' AND e.resource_type = 'ai_active_time') = 600
            AND (SELECT count(*) FROM platform.trial_claim_phone WHERE household_id = 'd4000000-0000-4000-8000-0000000000d4') = 1
            AND NOT EXISTS (SELECT 1 FROM platform.trial_claim_phone WHERE phone_digest LIKE '%99900000004%')
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation),
            '试用 = 基本档完整额度（10 个额度桶，学时 600 分钟不折算）；只存手机号摘要');
SELECT t.ok(t.call('4a000000-0000-4000-8000-00000000004a', $$to_jsonb(platform.meter_student_state('c4000000-0000-4000-8000-0000000000c4'))$$)
            = '"trialing"', '试用期内的状态是 trialing');
SELECT t.throws_like($$SELECT t.call('a0000000-0000-4000-8000-00000000000a', 'to_jsonb(platform.meter_student_state(''c4000000-0000-4000-8000-0000000000c4''))')$$,
                     '42501', 'not permitted', '别家家长查不了状态');
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b', $$platform.enroll_subject('c4000000-0000-4000-8000-0000000000c4', 'aya')$$)
            = '{"outcome": "not_launched"}', '试用与付费不能选 beta 学科（只对试点开放）');
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b', $$platform.meter_practice('c4000000-0000-4000-8000-0000000000c4', t.k(1))$$)
            @> '{"outcome": "allowed"}', '试用期题库自练不限');
SELECT t.ok((SELECT count(*) FROM platform.practice_log WHERE student_id = 'c4000000-0000-4000-8000-0000000000c4') = 1, '自练记一条');

UPDATE t.clock SET now = '2026-12-15 02:00:01+00';
SELECT t.ok(t.renew() >= 1, '续期任务处理到期试用');
SELECT t.ok((SELECT status FROM platform.subscription WHERE student_id = 'c4000000-0000-4000-8000-0000000000c4') = 'ended'
            AND (SELECT count(*) FROM platform.subscription WHERE student_id = 'c4000000-0000-4000-8000-0000000000c4') = 1,
            '试用到期结束，不续期');
SELECT t.ok(t.call('4a000000-0000-4000-8000-00000000004a', $$to_jsonb(platform.meter_student_state('c4000000-0000-4000-8000-0000000000c4'))$$)
            = '"retained"', '试用结束未付费 → 留存态');
SELECT t.ok(t.call('4a000000-0000-4000-8000-00000000004a',
                   $$platform.meter_open_subscription('c4000000-0000-4000-8000-0000000000c4', 'trial')$$)
            = '{"outcome": "trial_already_claimed"}', '试用结束后不能再领');
SELECT t.ok(t.call('5a000000-0000-4000-8000-00000000005a',
                   $$platform.meter_open_subscription('c4000000-0000-4000-8000-0000000000c4', 'trial')$$)
            = '{"outcome": "trial_already_claimed"}', '同一家庭换一位（新手机号的）家长也不能再领');

-- ================= 三、留存态：不调用 AI，每天 5 次题库自练 =================
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b',
                   $$platform.meter_reserve('c4000000-0000-4000-8000-0000000000c4', 'ai_active_time', t.k(2), repeat('2', 64), 10, NULL, 'teach')$$)
            = '{"outcome": "retained_no_ai"}', '留存态预留 AI 学时被拒');
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b',
                   $$platform.meter_reserve_upload('c4000000-0000-4000-8000-0000000000c4', t.k(3), repeat('3', 64), 'synthetic-photo-0001', NULL)$$)
            = '{"outcome": "retained_no_ai"}', '留存态上传（含识别与错因）被拒');
SELECT t.ok((SELECT bool_and(t.call('4b000000-0000-4000-8000-00000000004b',
                                    format('platform.meter_practice(%L, t.k(%s))', 'c4000000-0000-4000-8000-0000000000c4', 10 + g))
                             @> '{"outcome": "allowed"}') FROM generate_series(1, 5) g),
            '留存态每天 5 次自练');
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b', $$platform.meter_practice('c4000000-0000-4000-8000-0000000000c4', t.k(16))$$)
            = '{"outcome": "daily_limit_reached", "remaining_today": 0}', '第 6 次被拒');
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b', $$platform.meter_practice('c4000000-0000-4000-8000-0000000000c4', t.k(15))$$)
            @> '{"outcome": "existing"}', '同 key 重试返回已有记录，不再计数');
SELECT t.throws_like($$SELECT t.call('a1000000-0000-4000-8000-0000000000a1', 'platform.meter_practice(''c4000000-0000-4000-8000-0000000000c4'', t.k(17))')$$,
                     '42501', 'not permitted', '不能替别人做自练计数');
SELECT t.ok(t.call('3b000000-0000-4000-8000-00000000003b', $$platform.meter_practice('c3000000-0000-4000-8000-0000000000c3', t.k(18))$$)
            = '{"outcome": "no_entitlement"}', '从未开通过的学生不是留存态');
UPDATE t.clock SET now = '2026-12-15 16:00:00+00';  -- 上海时间次日 0:00
SELECT t.ok(t.call('4b000000-0000-4000-8000-00000000004b', $$platform.meter_practice('c4000000-0000-4000-8000-0000000000c4', t.k(19))$$)
            @> '{"outcome": "allowed", "remaining_today": 4}', '按上海时间换日后重新计数');

-- ================= 四、单日上限 =================
UPDATE t.clock SET now = '2026-12-16 02:00:00+00';  -- 上海时间 10:00
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.meter_open_subscription('c1000000-0000-4000-8000-0000000000c1', 'pilot_virtual')$$)
            @> '{"outcome": "opened", "config_version": "v12-pilot@1"}', '试点家庭开通（PRO，单日上限 150 分钟）');
SELECT t.ok((SELECT bool_and(t.call('a1000000-0000-4000-8000-0000000000a1',
                                    format('platform.meter_reserve(%L, %L, t.k(%s), repeat(%L, 64), 30, NULL, %L)',
                                           'c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', 100 + g, '4', 'teach'))
                             @> '{"outcome": "reserved"}') FROM generate_series(1, 5) g),
            '5 笔 30 分钟，共 150 分钟');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'voice_realtime', t.k(106), repeat('4', 64), 1, NULL, 'teach')$$)
            = '{"outcome": "daily_cap_reached", "remaining_today": 0}', '已预留 + 已结算达到上限后，下一次分钟类预留被拒（语音与学时合计）');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(101), repeat('4', 64), 30, NULL, 'teach')$$)
            @> '{"outcome": "existing"}', '上限到达后，同 key 重试仍返回已有预留');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full', t.k(107), repeat('4', 64), 1, NULL, 'diagnose')$$)
            @> '{"outcome": "reserved"}', '按次计的资源不受分钟上限影响');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   format('platform.meter_release(%L, %L)', platform.meter_usage_id(t.k(105)), 'student_quit')) @> '{"outcome": "released"}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1',
                       $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(108), repeat('4', 64), 30, NULL, 'teach')$$)
                @> '{"outcome": "reserved"}',
            '释放的预留不计入当日用量');
-- 家长只能调低
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.set_time_guard('c1000000-0000-4000-8000-0000000000c1', 200, NULL, NULL)$$)
            = '{"outcome": "cap_above_plan", "plan_cap": 150}', '家长不能把上限调到档位之上');
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.set_time_guard('c1000000-0000-4000-8000-0000000000c1', 60, NULL, NULL)$$)
            = '{"outcome": "saved", "daily_cap": 60}', '家长调低到 60 分钟');
SELECT t.throws_like($$SELECT t.call('a1000000-0000-4000-8000-0000000000a1', 'platform.set_time_guard(''c1000000-0000-4000-8000-0000000000c1'', 180, NULL, NULL)')$$,
                     '42501', 'not permitted', '学生不能改自己的上限');
SELECT t.throws_like($$SELECT t.call('b0000000-0000-4000-8000-00000000000b', 'platform.set_time_guard(''c1000000-0000-4000-8000-0000000000c1'', 10, NULL, NULL)')$$,
                     '42501', 'not permitted', '别家家长不能改');
SELECT t.ok(t.call('b0000000-0000-4000-8000-00000000000b',
                   $$platform.set_time_guard('c2000000-0000-4000-8000-0000000000c2', NULL, time '21:00', time '07:00')$$)
            = '{"outcome": "saved", "daily_cap": 180}', 'H2 家长收紧宵禁（无订阅时上限按 180）');
UPDATE t.clock SET now = '2026-12-17 02:00:00+00';
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(110), repeat('4', 64), 30, NULL, 'teach')$$)
            @> '{"outcome": "reserved"}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1',
                       $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(111), repeat('4', 64), 30, NULL, 'teach')$$)
                @> '{"outcome": "reserved"}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1',
                       $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(112), repeat('4', 64), 1, NULL, 'teach')$$)
                = '{"outcome": "daily_cap_reached", "remaining_today": 0}',
            '次日按家长设定的 60 分钟计');

-- ================= 五、宵禁 =================
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.set_time_guard('c1000000-0000-4000-8000-0000000000c1', 60, time '23:00', time '06:00')$$)
            = '{"outcome": "curfew_must_cover_default"}', '宵禁只能收紧，不能晚于 22:00 开始');
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.set_time_guard('c1000000-0000-4000-8000-0000000000c1', 60, time '22:00', time '05:30')$$)
            = '{"outcome": "curfew_must_cover_default"}', '宵禁只能收紧，不能早于 6:00 结束');
SELECT t.throws($$SELECT t.call('a0000000-0000-4000-8000-00000000000a', 'platform.set_time_guard(''c1000000-0000-4000-8000-0000000000c1'', 60, time ''22:00'', NULL)')$$,
                '22023', '宵禁起止必须成对');
UPDATE t.clock SET now = '2026-12-17 15:30:00+00';  -- 上海 23:30
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full', t.k(120), repeat('5', 64), 1, NULL, 'diagnose')$$)
            = '{"outcome": "curfew"}', '23:30 默认宵禁内拒绝 AI 互动');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full', t.k(107), repeat('4', 64), 1, NULL, 'diagnose')$$)
            @> '{"outcome": "existing"}', '宵禁内同 key 重试仍返回已有预留');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'deep_report', t.k(121), repeat('5', 64), 1, NULL, NULL)$$)
            @> '{"outcome": "reserved"}', '深度报告不受宵禁限制');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1', $$platform.meter_practice('c1000000-0000-4000-8000-0000000000c1', t.k(122))$$)
            @> '{"outcome": "allowed"}', '题库自练不受宵禁限制（spec 待人确认 #6 默认）');
UPDATE t.clock SET now = '2026-12-17 21:59:00+00';  -- 上海 05:59
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve_upload('c1000000-0000-4000-8000-0000000000c1', t.k(123), repeat('5', 64), 'synthetic-photo-0002', NULL)$$)
            = '{"outcome": "curfew"}', '05:59 仍在宵禁内（上传含识别与错因）');
UPDATE t.clock SET now = '2026-12-17 22:00:00+00';  -- 上海 06:00
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full', t.k(124), repeat('5', 64), 1, NULL, 'diagnose')$$)
            @> '{"outcome": "reserved"}', '06:00 放行');
UPDATE t.clock SET now = '2026-12-18 13:30:00+00';  -- 上海 21:30
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a',
                   $$platform.set_time_guard('c1000000-0000-4000-8000-0000000000c1', 60, time '21:00', time '07:00')$$)
            @> '{"outcome": "saved"}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1',
                       $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'diagnosis_full', t.k(125), repeat('5', 64), 1, NULL, 'diagnose')$$)
                = '{"outcome": "curfew"}',
            '家长把宵禁提前到 21:00 后，21:30 被拒');

-- 单日按上海时间：07:30（UTC 前一天 23:30）与 09:00（UTC 当天 01:00）算同一天
UPDATE t.clock SET now = '2026-12-18 23:30:00+00';
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(126), repeat('5', 64), 30, NULL, 'teach')$$)
            @> '{"outcome": "reserved"}', '上海 07:30 预留 30 分钟');
UPDATE t.clock SET now = '2026-12-19 01:00:00+00';
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(127), repeat('5', 64), 30, NULL, 'teach')$$)
            @> '{"outcome": "reserved"}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1',
                       $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'ai_active_time', t.k(128), repeat('5', 64), 1, NULL, 'teach')$$)
                = '{"outcome": "daily_cap_reached", "remaining_today": 0}',
            '单日上限按上海时间换日（跨 UTC 零点仍是同一天）');

-- ================= 六、上传、存储 =================
UPDATE t.clock SET now = '2026-12-19 02:00:00+00';
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve_upload('c1000000-0000-4000-8000-0000000000c1', t.k(130), repeat('6', 64), 'synthetic-pdf-0001', 9)$$)
            @> '{"outcome": "reserved", "reserved_qty": 3}', '9 页 PDF 预留 3 次（ceil(9 / 4)）');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1', format('platform.meter_settle(%L, 3)', platform.meter_usage_id(t.k(130))))
            @> '{"outcome": "settled"}', '识别成功后结算');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve_upload('c1000000-0000-4000-8000-0000000000c1', t.k(131), repeat('6', 64), 'synthetic-pdf-0001', 9)$$)
            @> '{"outcome": "dedup", "reserved_qty": 0}', '同一文件 24 小时内再次上传不扣');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve_upload('c1000000-0000-4000-8000-0000000000c1', t.k(132), repeat('6', 64), 'synthetic-photo-0003', NULL)$$)
            @> '{"outcome": "reserved", "reserved_qty": 1}'
            AND t.call('a1000000-0000-4000-8000-0000000000a1', format('platform.meter_release(%L, %L)', platform.meter_usage_id(t.k(132)), 'ocr_failed'))
                @> '{"outcome": "released"}', '图片 1 次；识别失败释放');
SELECT t.ok((SELECT settled + reserved FROM platform.entitlement e JOIN platform.subscription s ON s.id = e.subscription_id
                  WHERE s.student_id = 'c1000000-0000-4000-8000-0000000000c1' AND s.status = 'active' AND e.resource_type = 'upload') = 3,
            '释放后不扣（只剩 PDF 的 3 次）');
SELECT t.throws($$SELECT t.call('a1000000-0000-4000-8000-0000000000a1', 'platform.meter_reserve_upload(''c1000000-0000-4000-8000-0000000000c1'', t.k(133), repeat(''6'', 64), ''synthetic-pdf-0002'', 33)')$$,
                '22023', 'PDF 超过 32 页被拒');
SELECT t.throws_like($$SELECT t.call('a1000000-0000-4000-8000-0000000000a1', 'platform.meter_reserve(''c1000000-0000-4000-8000-0000000000c1'', ''upload'', t.k(134), repeat(''6'', 64), 1, ''synthetic-pdf-0003'', NULL)')$$,
                     '22023', 'meter_reserve_upload', '上传不能经通用预留直接指定数量');
SELECT t.throws_like($$SELECT t.call('a1000000-0000-4000-8000-0000000000a1', 'platform.meter_reserve(''c1000000-0000-4000-8000-0000000000c1'', ''storage_bytes'', t.k(135), repeat(''6'', 64), 1000, NULL, ''grade'')')$$,
                     '22023', 'not tied to a plugin method', '存储不对应插件方法');
SELECT t.ok(t.call('a1000000-0000-4000-8000-0000000000a1',
                   $$platform.meter_reserve('c1000000-0000-4000-8000-0000000000c1', 'storage_bytes', t.k(136), repeat('6', 64), 2400000, NULL, NULL)$$)
            @> '{"outcome": "reserved", "reserved_qty": 2400000}', '存储按字节预留');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation) AND NOT EXISTS (SELECT 1 FROM platform.reservation_ledger_mismatch),
            '对账为零行');

-- ================= 七、选科：核心 + 选修槽 =================
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya')$$)
            = '{"slot": "core", "limit": null, "outcome": "enrolled"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli')$$)
                = '{"slot": "core", "limit": null, "outcome": "enrolled"}',
            'PRO：已上线核心科目全部可选（试点可选 beta）');
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'zhixing')$$)
            = '{"outcome": "not_launched"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'echo')$$)
                = '{"outcome": "not_launched"}',
            '没有已发布课程、或目录里是 planned（英语搁置）的学科不能选');
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a', $$platform.pause_subject('c1000000-0000-4000-8000-0000000000c1', 'aya')$$)
            = '{"outcome": "paused"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.pause_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli')$$)
                = '{"outcome": "change_limit_reached"}',
            '每个周期只能换下 1 科');
BEGIN;
UPDATE platform.subscription SET tier = 'basic' WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND status = 'active';
UPDATE platform.subject_enrollment SET status = 'paused', paused_at = NULL WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1';
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya')$$)
            = '{"slot": "core", "limit": 1, "outcome": "enrolled"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli')$$)
                = '{"slot": "core", "limit": 1, "outcome": "limit_reached"}',
            '基本档（pick_one）：核心科目任选 1 科');
SELECT t.ok(t.call('a0000000-0000-4000-8000-00000000000a', $$platform.pause_subject('c1000000-0000-4000-8000-0000000000c1', 'aya')$$)
            = '{"outcome": "paused"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'wenli')$$)
                = '{"slot": "core", "outcome": "change_next_period"}'
            AND t.call('a0000000-0000-4000-8000-00000000000a', $$platform.enroll_subject('c1000000-0000-4000-8000-0000000000c1', 'aya')$$)
                @> '{"outcome": "enrolled"}',
            '换下的科目本周期仍占槽，新科目下个周期才能开；换回原科目可以');
ROLLBACK;

-- ================= 八、升级补差、宽限期 =================
RESET ROLE;
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, period_start, period_end)
VALUES ('5b000000-0000-4000-8000-00000000005b', 'd2000000-0000-4000-8000-0000000000d2', 'c2000000-0000-4000-8000-0000000000c2',
        'basic', 'v12-pilot@1', 'paid', '2026-12-19 00:00:00+00', '2027-01-18 00:00:00+00');
SELECT platform.meter_grant_period('5b000000-0000-4000-8000-00000000005b');
SELECT t.ok(t.call('b1000000-0000-4000-8000-0000000000b1',
                   $$platform.meter_reserve('c2000000-0000-4000-8000-0000000000c2', 'ai_active_time', t.k(140), repeat('7', 64), 30, NULL, 'teach')$$)
            @> '{"outcome": "reserved"}'
            AND t.call('b1000000-0000-4000-8000-0000000000b1', format('platform.meter_settle(%L, 25)', platform.meter_usage_id(t.k(140))))
                @> '{"outcome": "settled"}',
            '付费基本档先用 25 分钟');
SELECT t.ok(platform.meter_upgrade('5b000000-0000-4000-8000-00000000005b', 'plus') @> '{"outcome": "upgraded", "tier": "plus"}', '升级');
SELECT t.ok((SELECT granted || '/' || settled FROM platform.entitlement
                  WHERE subscription_id = '5b000000-0000-4000-8000-00000000005b' AND resource_type = 'ai_active_time') = '1200/25'
            AND (SELECT count(*) FROM platform.usage_ledger l JOIN platform.entitlement e ON e.id = l.entitlement_id
                  WHERE e.subscription_id = '5b000000-0000-4000-8000-00000000005b' AND l.reason_code = 'upgrade_topup') = 9
            AND NOT EXISTS (SELECT 1 FROM platform.entitlement_reconciliation),
            '升级即时生效：当期额度补足差额（600 → 1200），已用量保留；9 种增加的资源补差，初筛不变不补');
SELECT t.ok(platform.meter_upgrade('5b000000-0000-4000-8000-00000000005b', 'plus') = '{"outcome": "already_at_tier"}', '重复升级');
SELECT t.ok((SELECT granted FROM platform.entitlement
                  WHERE subscription_id = '5b000000-0000-4000-8000-00000000005b' AND resource_type = 'ai_active_time') = 1200,
            '重复升级不重复发放');
SELECT t.ok(platform.meter_upgrade('5b000000-0000-4000-8000-00000000005b', 'basic') = '{"outcome": "not_an_upgrade"}',
            '降级不走升级函数（到期生效）');
UPDATE t.clock SET now = '2027-01-18 00:00:01+00';
SELECT t.ok(t.renew() >= 1, '续期任务处理付费到期');
SELECT t.ok((SELECT status FROM platform.subscription WHERE id = '5b000000-0000-4000-8000-00000000005b') = 'grace'
            AND t.call('b0000000-0000-4000-8000-00000000000b', $$to_jsonb(platform.meter_student_state('c2000000-0000-4000-8000-0000000000c2'))$$)
                = '"grace"',
            '付费到期未续 → 宽限期');
UPDATE t.clock SET now = '2027-01-24 23:59:59+00';
SELECT t.renew();
SELECT t.ok((SELECT status FROM platform.subscription WHERE id = '5b000000-0000-4000-8000-00000000005b') = 'grace',
            '宽限期 7 天内保持');
UPDATE t.clock SET now = '2027-01-25 00:00:01+00';
SELECT t.ok(t.renew() >= 1, '续期任务处理宽限期结束');
SELECT t.ok((SELECT status FROM platform.subscription WHERE id = '5b000000-0000-4000-8000-00000000005b') = 'ended'
            AND t.call('b0000000-0000-4000-8000-00000000000b', $$to_jsonb(platform.meter_student_state('c2000000-0000-4000-8000-0000000000c2'))$$)
                = '"retained"',
            '宽限期结束 → 留存态');

-- ================= 九、配置 =================
SELECT t.throws($$INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
                  VALUES ('ch8-hypothesis@1', 'basic', 'daily_minutes_cap', '1441')$$, '23514', '单日上限不超过 1440 分钟');
SELECT t.throws($$INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
                  VALUES ('ch8-hypothesis@1', 'basic', 'core_subjects', 'pick_two')$$, '23514', '核心科目规则只有 pick_one 与 all_launched');
INSERT INTO platform.plan_config (version, note_code) VALUES ('synthetic-v12@1', 'synthetic_test_plan');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'synthetic-v12@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'synthetic-v12@1', tier, feature_code, value_code FROM platform.plan_feature
 WHERE config_version = 'v12-pilot@1' AND NOT (tier = 'plus' AND feature_code = 'elective_slots');
SELECT t.throws_like($$UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-v12@1'$$,
                     '23514', 'incomplete', '某档缺选科规则不能发布');
DELETE FROM platform.plan_allowance WHERE config_version = 'synthetic-v12@1' AND tier = 'max' AND resource_type = 'upload';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code) VALUES ('synthetic-v12@1', 'plus', 'elective_slots', '2');
SELECT t.throws_like($$UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-v12@1'$$,
                     '23514', 'incomplete', '四档资源集合不一致不能发布');
DELETE FROM platform.plan_allowance WHERE config_version = 'synthetic-v12@1' AND resource_type = 'upload';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'synthetic-v12@1';
SELECT t.ok(true, '四档资源集合一致（可以不含某个资源）即可发布');
UPDATE platform.plan_config SET status = 'retired', retired_at = now() WHERE version = 'synthetic-v12@1';

SELECT t.as_account(NULL);
RESET ROLE;
