-- 0030 计量只读函数 meter_guard_status（specs/unified-shell-sprint4/spec.md 第 9 节、验收“计量只读函数（H3）”）。
-- 合成数据（规则 8）：新建一户（家长 gL，孩子 L1 试用中、L2 留存态、L3 从未开通）与一位已解除绑定的家长 gLr；
-- 另一户用 10 号夹具（gB、S2 的账号）。手机号用未分配的 +999。沿用 70 号测试的可推进时钟 t.clock 与 78 号的 t.call、t.k。
-- 整个文件在一个事务里，结束回滚（时钟、订阅、预留一并回滚，不影响 80 的矩阵）。
BEGIN;

\set gL '''6d000000-0000-4000-8000-0000000000a1'''
\set gLr '''6d000000-0000-4000-8000-0000000000a2'''
\set hL '''6d000000-0000-4000-8000-0000000000d1'''
\set L1 '''6d000000-0000-4000-8000-0000000000c1'''
\set L2 '''6d000000-0000-4000-8000-0000000000c2'''
\set L3 '''6d000000-0000-4000-8000-0000000000c3'''
\set aL1 '''6d000000-0000-4000-8000-0000000000b1'''
\set aL2 '''6d000000-0000-4000-8000-0000000000b2'''
\set aL3 '''6d000000-0000-4000-8000-0000000000b3'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''

RESET ROLE;
SELECT t.as_account(NULL);
-- 当前配置是 78 号发布的规格书 V1.2 配置 v12-pilot@1（基本档单日上限 90 分钟）
SELECT t.ok(platform.meter_current_config() = 'v12-pilot@1', '合成：当前额度配置是 v12-pilot@1');
UPDATE t.clock SET now = '2027-01-10 02:00:00+00';  -- 上海 10:00

INSERT INTO platform.account (id, kind) VALUES (:gL, 'guardian'), (:gLr, 'guardian'),
  (:aL1, 'student'), (:aL2, 'student'), (:aL3, 'student');
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (:gL, '+99900000961', now()), (:gLr, '+99900000962', now());
INSERT INTO platform.household (id, created_by_account) VALUES (:hL, :gL);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  (:L1, :aL1, :hL, 8, 'nanjing', 'bubian-2024', '2013-04-01'),
  (:L2, :aL2, :hL, 8, 'nanjing', 'bubian-2024', '2013-04-01'),
  (:L3, :aL3, :hL, 7, 'nanjing', 'bubian-2024', '2014-04-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:gL, :L1, 'household_creator'), (:gL, :L2, 'household_creator'), (:gL, :L3, 'household_creator');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via, status, revoked_at) VALUES
  (:gLr, :L1, 'invite', 'revoked', now());
-- L1 试用（家长领取，14 天）；L2 曾有试点订阅、已结束 → 留存态；L3 从未开通
SELECT t.ok(t.call(:gL, format('platform.meter_open_subscription(%L, %L)', :L1, 'trial')) @> '{"outcome": "opened"}', '合成：L1 开通试用');
INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end, status)
VALUES (:hL, :L2, 'pro', 'v12-pilot@1', 'pilot_virtual', '2026-11-01 00:00:00+00', '2026-12-01 00:00:00+00', 'ended');

-- 以 BFF 身份、某账号调用；出错时返回 SQLSTATE:消息（不抛出）
CREATE FUNCTION t.mg(p_acct uuid, p_student uuid) RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
  RETURN t.call(p_acct, format('platform.meter_guard_status(%L::uuid)', p_student));
EXCEPTION WHEN OTHERS THEN
  RESET ROLE;
  PERFORM t.as_account(NULL);
  RETURN to_jsonb(SQLSTATE || ':' || SQLERRM);
END $$;
GRANT EXECUTE ON FUNCTION t.mg(uuid, uuid) TO PUBLIC;

-- ================= 一、函数属性与授权 =================
SELECT t.ok((SELECT p.prosecdef AND p.provolatile = 's' AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                    AND p.proconfig = ARRAY['search_path=pg_catalog, platform, pg_temp']
               FROM pg_proc p WHERE p.oid = 'platform.meter_guard_status(uuid)'::regprocedure),
            '计量只读：SECURITY DEFINER、STABLE（只读）、属主 authz、search_path 固定且以 pg_temp 结尾');
SELECT t.ok(has_function_privilege('starbuddy_bff', 'platform.meter_guard_status(uuid)', 'EXECUTE')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                                       'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) r
                             WHERE has_function_privilege(r, 'platform.meter_guard_status(uuid)', 'EXECUTE'))
            AND NOT EXISTS (SELECT 1 FROM aclexplode((SELECT proacl FROM pg_proc WHERE oid = 'platform.meter_guard_status(uuid)'::regprocedure)) a
                             WHERE a.grantee = 0),
            '计量只读：只授 starbuddy_bff（其他应用角色与 PUBLIC 都不能执行）');
CREATE FUNCTION t.mg_as(p_role text, p_student uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  PERFORM platform.meter_guard_status(p_student);
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN 'returned';
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE;
END $$;
SELECT t.ok((SELECT bool_and(t.mg_as(r, :L1) = '42501')
               FROM unnest(ARRAY['starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_report']) r),
            '计量只读：学习服务、审核台、定时任务、洞察服务、报告角色调用都被拒');

-- ================= 二、谁能读（规则 5：本人或按 guardian_student_link 已绑定的家长） =================
SELECT t.ok(t.mg(:aL1, :L1) ->> 'state' = 'trialing', '计量只读：学生本人能读');
SELECT t.ok(t.mg(:gL, :L1) = t.mg(:aL1, :L1), '计量只读：已绑定家长能读，结果与本人相同');
SELECT t.ok(t.mg(NULL, :L1) = '"42501:not permitted"', '计量只读：未登录被拒');
SELECT t.ok(t.mg(:gB, :L1) = '"42501:not permitted"', '计量只读：另一户家长被拒');
SELECT t.ok(t.mg(:aS2, :L1) = '"42501:not permitted"', '计量只读：另一户学生被拒');
SELECT t.ok(t.mg(:aL2, :L1) = '"42501:not permitted"', '计量只读：兄弟姐妹（同户另一个学生）被拒');
SELECT t.ok(t.mg(:gLr, :L1) = '"42501:not permitted"', '计量只读：已解除绑定的家长被拒');
SELECT t.ok(t.mg(:gL, '6d000000-0000-4000-8000-00000000ffff') = t.mg(:gB, :L1)
            AND t.mg(:gL, NULL) = t.mg(:gB, :L1),
            '计量只读：学生不存在、参数为空与无权逐字相同（不泄露存在性）');
UPDATE platform.account SET status = 'disabled' WHERE id = :aL3;
SELECT t.ok(t.mg(:aL3, :L3) = '"42501:not permitted"', '计量只读：停用的学生账号被拒');
UPDATE platform.account SET status = 'active' WHERE id = :aL3;
SELECT t.ok(t.mg('5f000000-0000-4000-8000-00000000005f', :L1) = '"42501:not permitted"', '计量只读：staff 账号被拒');

-- ================= 三、返回值与 0015 的推导函数一致 =================
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(t.mg(:gL, :L1)) k)
            = ARRAY['as_of', 'curfew_end', 'curfew_start', 'daily_cap', 'in_curfew', 'minutes_remaining_today', 'minutes_until_curfew',
                    'minutes_used_today', 'plan_daily_cap', 'practice_remaining_today', 'state', 'trial_ends_at'],
            '计量只读：键固定（没有设置人、账号 ID）');
SELECT t.ok(t.mg(:gL, :L1) @> jsonb_build_object(
              'state', 'trialing', 'plan_daily_cap', 90, 'daily_cap', 90, 'minutes_used_today', 0, 'minutes_remaining_today', 90,
              'curfew_start', '22:00', 'curfew_end', '06:00', 'in_curfew', false, 'minutes_until_curfew', 720,
              'practice_remaining_today', NULL)
            AND (t.mg(:gL, :L1) ->> 'trial_ends_at')::timestamptz
                = (SELECT period_end FROM platform.subscription WHERE student_id = :L1 AND source = 'trial')
            AND (t.mg(:gL, :L1) ->> 'trial_ends_at')::timestamptz = timestamptz '2027-01-24 02:00:00+00'
            AND (t.mg(:gL, :L1) ->> 'as_of')::timestamptz = (SELECT now FROM t.clock),
            '计量只读：试用中——档位上限 90、今日未用、上海 10:00 距宵禁 720 分钟、试用结束时间取试用订阅的 period_end');
SELECT t.ok(t.mg(:gL, :L3) @> '{"state": "none", "trial_ends_at": null, "plan_daily_cap": 180, "daily_cap": 180, "practice_remaining_today": null}',
            '计量只读：从未开通——none，上限按 0015 的缺省 180');

-- 用量：两笔 30 分钟预留（一笔结算 20），与 meter_minutes_today 一致
SELECT t.ok(t.call(:aL1, format('platform.meter_reserve(%L, %L, t.k(%s), repeat(%L, 64), 30, NULL, %L)', :L1, 'ai_active_time', 901, '9', 'teach'))
              @> '{"outcome": "reserved"}'
            AND t.call(:aL1, format('platform.meter_settle(%L, 20)', platform.meter_usage_id(t.k(901)))) @> '{"outcome": "settled"}'
            AND t.call(:aL1, format('platform.meter_reserve(%L, %L, t.k(%s), repeat(%L, 64), 30, NULL, %L)', :L1, 'ai_active_time', 902, '9', 'teach'))
              @> '{"outcome": "reserved"}',
            '合成：L1 用了 20 分钟（已结算）、预留中 30 分钟');
SELECT t.ok(t.mg(:aL1, :L1) @> '{"minutes_used_today": 50, "minutes_remaining_today": 40}'
            AND (t.mg(:aL1, :L1) ->> 'minutes_used_today')::numeric = platform.meter_minutes_today(:L1, (SELECT now FROM t.clock))
            AND (t.mg(:aL1, :L1) ->> 'daily_cap')::int = platform.meter_daily_cap(:L1)
            AND (t.mg(:aL1, :L1) ->> 'plan_daily_cap')::int = platform.meter_plan_daily_cap(:L1),
            '计量只读：今日已用 = 已结算 + 预留中，剩余 = 生效上限 − 已用；与 meter_minutes_today / meter_daily_cap 一致');
-- 已过期、过期任务还没跑到的预留不计入（与 meter_reserve 同口径）
UPDATE t.clock SET now = '2027-01-10 02:31:00+00';
SELECT t.ok(t.mg(:aL1, :L1) @> '{"minutes_used_today": 20, "minutes_remaining_today": 70}'
            AND (SELECT state FROM platform.usage_reservation WHERE usage_id = platform.meter_usage_id(t.k(902))) = 'reserved',
            '计量只读：已过期未回收的预留不计入今日已用');
-- 家长调低上限：生效上限取小、档位上限不变；调到已用之下时剩余为 0，不为负
SELECT t.ok(t.call(:gL, format('platform.set_time_guard(%L, 30, NULL, NULL)', :L1)) @> '{"outcome": "saved"}'
            AND t.mg(:aL1, :L1) @> '{"plan_daily_cap": 90, "daily_cap": 30, "minutes_remaining_today": 10}',
            '计量只读：家长调低到 30 分钟后，生效上限 30、档位上限仍 90');
SELECT t.ok(t.call(:gL, format('platform.set_time_guard(%L, 10, NULL, NULL)', :L1)) @> '{"outcome": "saved"}'
            AND t.mg(:aL1, :L1) @> '{"daily_cap": 10, "minutes_used_today": 20, "minutes_remaining_today": 0}',
            '计量只读：上限低于已用时剩余为 0');
SELECT t.ok(t.call(:aL1, format('platform.meter_reserve(%L, %L, t.k(%s), repeat(%L, 64), 1, NULL, %L)', :L1, 'ai_active_time', 903, '9', 'teach'))
              = '{"outcome": "daily_cap_reached", "remaining_today": 0}',
            '计量只读：剩余为 0 时预留也被拒（两处同一口径）');
-- 只读：调用前后账本、预留、自练记录都不变
CREATE TEMP TABLE mg_before AS SELECT (SELECT count(*) FROM platform.usage_ledger) AS ledger,
                                      (SELECT count(*) FROM platform.usage_reservation) AS resv,
                                      (SELECT count(*) FROM platform.practice_log) AS practice;
DO $$ BEGIN
  PERFORM t.mg('6d000000-0000-4000-8000-0000000000b1', '6d000000-0000-4000-8000-0000000000c1');
  PERFORM t.mg('6d000000-0000-4000-8000-0000000000a1', '6d000000-0000-4000-8000-0000000000c2');
  PERFORM t.mg('6d000000-0000-4000-8000-0000000000a1', '6d000000-0000-4000-8000-0000000000c3');
END $$;
SELECT t.ok((SELECT ledger = (SELECT count(*) FROM platform.usage_ledger) AND resv = (SELECT count(*) FROM platform.usage_reservation)
                    AND practice = (SELECT count(*) FROM platform.practice_log) FROM mg_before),
            '计量只读：调用不写账本、预留、自练记录');

-- ================= 四、宵禁边界与时区（上海时间） =================
UPDATE t.clock SET now = '2027-01-10 13:59:00+00';  -- 上海 21:59
SELECT t.ok(t.mg(:aL1, :L1) @> '{"in_curfew": false, "minutes_until_curfew": 1}', '计量只读：21:59 不在宵禁内、距宵禁 1 分钟');
UPDATE t.clock SET now = '2027-01-10 14:00:00+00';  -- 上海 22:00（UTC 14:00：按 UTC 算不在任何默认时段）
SELECT t.ok(t.mg(:aL1, :L1) @> '{"in_curfew": true, "minutes_until_curfew": 0}', '计量只读：22:00（上海）进入宵禁，按上海时间而不是 UTC');
UPDATE t.clock SET now = '2027-01-10 15:30:00+00';  -- 上海 23:30
SELECT t.ok(t.mg(:gL, :L1) @> '{"in_curfew": true}', '计量只读：23:30 在宵禁内');
UPDATE t.clock SET now = '2027-01-10 21:59:00+00';  -- 上海 次日 05:59（跨午夜）
SELECT t.ok(t.mg(:gL, :L1) @> '{"in_curfew": true, "minutes_until_curfew": 0}', '计量只读：05:59 仍在宵禁内（跨午夜）');
UPDATE t.clock SET now = '2027-01-10 22:00:00+00';  -- 上海 次日 06:00（UTC 仍是前一天）
SELECT t.ok(t.mg(:gL, :L1) @> '{"in_curfew": false, "minutes_until_curfew": 960, "minutes_used_today": 0}',
            '计量只读：06:00 解除宵禁；上海已换日（UTC 仍是前一天），今日已用归零');
SELECT t.ok((t.mg(:gL, :L1) ->> 'in_curfew')::boolean = platform.meter_in_curfew(:L1, (SELECT now FROM t.clock))
            AND (t.mg(:gL, :L1) ->> 'minutes_until_curfew')::int = platform.meter_minutes_until_curfew(:L1, (SELECT now FROM t.clock)),
            '计量只读：与 meter_in_curfew / meter_minutes_until_curfew 一致');
-- 家长收紧宵禁到 21:00–07:00：起止与判断都取家长设置
SELECT t.ok(t.call(:gL, format('platform.set_time_guard(%L, 10, time %L, time %L)', :L1, '21:00', '07:00')) @> '{"outcome": "saved"}'
            AND t.mg(:aL1, :L1) @> '{"curfew_start": "21:00", "curfew_end": "07:00", "in_curfew": true, "minutes_until_curfew": 0}',
            '计量只读：家长收紧到 21:00–07:00 后，06:00 仍在宵禁内，起止取家长设置');
UPDATE t.clock SET now = '2027-01-11 12:30:00+00';  -- 上海 20:30
SELECT t.ok(t.mg(:aL1, :L1) @> '{"in_curfew": false, "minutes_until_curfew": 30}', '计量只读：20:30 距家长设定的 21:00 宵禁 30 分钟（curfew_soon 的依据）');
SELECT t.ok(t.mg(:aL2, :L2) @> '{"curfew_start": "22:00", "curfew_end": "06:00", "minutes_until_curfew": 90}',
            '计量只读：设置按学生分开，兄弟姐妹仍是缺省时段');

-- ================= 五、留存态的题库自练次数（与 meter_practice 同一口径） =================
SELECT t.ok(t.mg(:aL2, :L2) @> '{"state": "retained", "trial_ends_at": null, "practice_remaining_today": 5}',
            '计量只读：留存态今日剩余 5 次自练');
SELECT t.ok((SELECT bool_and(t.call(:aL2, format('platform.meter_practice(%L, t.k(%s))', :L2, 910 + g)) @> '{"outcome": "allowed"}')
               FROM generate_series(1, 3) g)
            AND t.mg(:aL2, :L2) @> '{"practice_remaining_today": 2}',
            '计量只读：做了 3 次后剩 2 次');
SELECT t.ok((SELECT bool_and(t.call(:aL2, format('platform.meter_practice(%L, t.k(%s))', :L2, 920 + g)) @> '{"outcome": "allowed"}')
               FROM generate_series(1, 2) g)
            AND t.mg(:gL, :L2) @> '{"practice_remaining_today": 0}'
            AND t.call(:aL2, format('platform.meter_practice(%L, t.k(%s))', :L2, 930)) @> '{"outcome": "daily_limit_reached"}',
            '计量只读：第 5 次后剩 0 次，此时 meter_practice 也拒绝（两处同一口径）');
-- 上海 07:30（UTC 前一天 23:30）与 09:00（UTC 当天 01:00）是同一天
UPDATE t.clock SET now = '2027-01-11 23:30:00+00';  -- 上海 01-12 07:30
SELECT t.ok(t.mg(:aL2, :L2) @> '{"practice_remaining_today": 5}', '计量只读：上海换日后重新计数');
SELECT t.ok(t.call(:aL2, format('platform.meter_practice(%L, t.k(%s))', :L2, 940)) @> '{"outcome": "allowed"}', '合成：上海 07:30 做一次');
UPDATE t.clock SET now = '2027-01-12 01:00:00+00';  -- 上海 01-12 09:00
SELECT t.ok(t.mg(:aL2, :L2) @> '{"practice_remaining_today": 4}', '计量只读：自练按上海时间换日（跨 UTC 零点仍是同一天）');
SELECT t.ok(t.mg(:aL1, :L1) @> '{"practice_remaining_today": null}', '计量只读：非留存态不给自练次数');

-- 试用到期（续期任务跑过）→ 留存态
UPDATE t.clock SET now = '2027-01-24 02:00:01+00';
SELECT t.ok(t.renew() >= 1 AND t.mg(:aL1, :L1) @> '{"state": "retained", "trial_ends_at": null, "practice_remaining_today": 5}',
            '计量只读：试用到期后显示留存态');

ROLLBACK;
