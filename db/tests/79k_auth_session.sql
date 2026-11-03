-- 0029 家庭登录与会话（specs/unified-shell-sprint4/spec.md 第 2、17 节与验收“登录”“会话”“学生模式”“RLS 测试”）。
-- 全部合成：号码用未分配的国家码 +999；验证码的 MAC 用 sha256 代替 BFF 的 HMAC（库里只比较 32 字节的 MAC，不知道密钥）。
-- 时钟：把 platform.auth_now() 换成读 t.aclock 的版本（只在测试库里做，文件末尾恢复），以便推进时间。
-- 本文件自建一户家庭（gK 与孩子 K1–K4）和几个家长账号，结束时全部删掉，不影响 80 的矩阵。

CREATE TABLE t.aclock (now timestamptz NOT NULL);
INSERT INTO t.aclock VALUES ('2027-03-01 02:00:00+00');   -- 北京时间 10:00
GRANT SELECT ON t.aclock TO starbuddy_authz;
CREATE OR REPLACE FUNCTION platform.auth_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $$ SELECT now FROM t.aclock $$;
CREATE FUNCTION t.aadvance(p interval) RETURNS void LANGUAGE sql AS $$ UPDATE t.aclock SET now = now + p $$;
-- 以 BFF 身份（可带账号）执行一段 SQL，结束后回到调用前的角色（与 79d 的 t.ob 同一做法）
CREATE FUNCTION t.ab(p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM t.as_account(p_acct);
  SET LOCAL ROLE starbuddy_bff;
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM t.as_account(NULL);
  RETURN v_out;
END $$;
CREATE FUNCTION t.mac(p_phone text, p_code text) RETURNS bytea LANGUAGE sql IMMUTABLE AS $$
  SELECT sha256(convert_to('otp:' || p_phone || ':' || p_code, 'UTF8')) $$;
CREATE FUNCTION t.issue(p_phone text, p_code text) RETURNS text LANGUAGE sql AS $$
  SELECT t.ab(NULL, format('SELECT platform.auth_otp_issue(%L, %L::bytea, 300, 5)::text', p_phone, t.mac(p_phone, p_code))) $$;
-- 返回 {"outcome", "session_token", "account_id", "account_kind"} 的 JSON 文本
CREATE FUNCTION t.login(p_phone text, p_code text, p_prev text DEFAULT NULL) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.ab(NULL, format('SELECT row_to_json(r)::text FROM platform.auth_otp_login(%L, %L::bytea, %s, %s, %L) r',
                           p_phone, t.mac(p_phone, p_code), 7 * 86400, 86400, p_prev))::jsonb $$;
CREATE FUNCTION t.resolve(p_token text) RETURNS text LANGUAGE sql AS $$
  SELECT t.ab(NULL, format('SELECT account_id::text || %L || account_kind::text || %L || origin FROM platform.auth_session_resolve(%L)',
                           '|', '|', p_token)) $$;
-- 以家长 p_acct 的身份、用会话令牌打开学生模式；返回令牌或错误（SQLSTATE:消息，不抛出）
CREATE FUNCTION t.handoff(p_acct uuid, p_student uuid, p_token text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  v := t.ab(p_acct, format('SELECT platform.auth_issue_student_session(%L::uuid, %L, %s, %s)', p_student, p_token, 7 * 86400, 86400));
  RETURN v;
EXCEPTION WHEN OTHERS THEN
  RETURN SQLSTATE || ':' || SQLERRM;
END $$;
CREATE FUNCTION t.rate(p_bucket text, p_key text, p_window integer, p_limit integer) RETURNS boolean LANGUAGE sql AS $$
  SELECT t.ab(NULL, format('SELECT platform.auth_rate_hit(%L, %L::bytea, %s, %s)::text', p_bucket, sha256(convert_to(p_key, 'UTF8')),
                           p_window, p_limit))::boolean $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;

\set gK '''7a000000-0000-4000-8000-0000000000a1'''
\set gK2 '''7a000000-0000-4000-8000-0000000000a2'''
\set gR '''7a000000-0000-4000-8000-0000000000a3'''
\set gD1 '''7a000000-0000-4000-8000-0000000000a4'''
\set gD2 '''7a000000-0000-4000-8000-0000000000a5'''
\set gX '''7a000000-0000-4000-8000-0000000000a6'''
\set hK '''7a000000-0000-4000-8000-0000000000d1'''
\set K1 '''7a000000-0000-4000-8000-0000000000c1'''
\set K2 '''7a000000-0000-4000-8000-0000000000c2'''
\set K3 '''7a000000-0000-4000-8000-0000000000c3'''
\set K4 '''7a000000-0000-4000-8000-0000000000c4'''
\set aK1 '''7a000000-0000-4000-8000-0000000000b1'''
\set aK2 '''7a000000-0000-4000-8000-0000000000b2'''
\set aK3 '''7a000000-0000-4000-8000-0000000000b3'''
\set aK4 '''7a000000-0000-4000-8000-0000000000b4'''
\set pK '''+99900000901'''
\set pK2 '''+99900000902'''
\set pR '''+99900000903'''
\set pDup '''+99900000904'''
\set pX '''+99900000905'''
\set pNew '''+99900000906'''
\set pStu '''+99900000907'''
\set dv '''privacy-policy@synthetic-0'''

-- 家庭 hK：家长 gK，孩子 K1（出生年月空，按不满 14）、K2（2012-02，已满 14）、K3（2013-03，本月才满 14：按月末算仍不满）、K4（账号停用）
-- gK2：没有任何绑定的家长；gR：曾绑定 K1、已解除；gD1、gD2：同一号码的两个家长账号（历史数据）；gX：停用的家长
INSERT INTO platform.account (id, kind) VALUES (:gK, 'guardian'), (:gK2, 'guardian'), (:gR, 'guardian'), (:gD1, 'guardian'),
  (:gD2, 'guardian'), (:gX, 'guardian'), (:aK1, 'student'), (:aK2, 'student'), (:aK3, 'student'), (:aK4, 'student');
UPDATE platform.account SET status = 'disabled' WHERE id IN (:gX, :aK4);
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  (:gK, :pK, now()), (:gK2, :pK2, now()), (:gR, :pR, now()), (:gD1, :pDup, now()), (:gD2, :pDup, now()), (:gX, :pX, now()),
  (:aK1, :pStu, now());   -- 只有学生账号有这个号码：登录只认家长账号，会新注册一个家长
INSERT INTO platform.household (id, created_by_account) VALUES (:hK, :gK);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, birth_year_month) VALUES
  (:K1, :aK1, :hK, 8, 'nanjing', NULL), (:K2, :aK2, :hK, 9, 'nanjing', '2012-02-01'),
  (:K3, :aK3, :hK, 8, 'nanjing', '2013-03-01'), (:K4, :aK4, :hK, 9, 'nanjing', '2012-01-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:gK, :K1, 'household_creator'), (:gK, :K2, 'household_creator'), (:gK, :K3, 'household_creator'), (:gK, :K4, 'household_creator'),
  (:gR, :K1, 'invite');
UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = :gR;

-- ================= 权限：表不给任何应用角色；函数只授 BFF =================
DO $$
DECLARE r text; tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['platform.auth_otp_challenge', 'platform.auth_session', 'platform.auth_rate_counter'] LOOP
    FOREACH r IN ARRAY ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                             'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report'] LOOP
      PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT count(*) FROM %s', r, tbl), '42501', r || ' 不能读 ' || tbl);
      PERFORM t.throws(format('SET LOCAL ROLE %I; DELETE FROM %s', r, tbl), '42501', r || ' 不能删 ' || tbl);
    END LOOP;
  END LOOP;
END $$;
RESET ROLE;
SELECT t.throws(format($$SELECT t.ab(NULL, %L)$$, 'INSERT INTO platform.auth_session (token_hash, account_id, origin, created_at, last_seen_at, expires_at, idle_timeout) VALUES (sha256(''x''), ''7a000000-0000-4000-8000-0000000000a1'', ''otp'', now(), now(), now() + interval ''1 day'', interval ''1 hour'') RETURNING 1'),
                '42501', 'BFF 不能直接插入会话（只能经验证码或家长签发产生）');
SELECT t.throws(format($$SELECT t.ab(NULL, %L)$$, 'UPDATE platform.auth_session SET revoked_at = NULL RETURNING 1'), '42501', 'BFF 不能直接改会话');
SELECT t.throws(format($$SELECT t.ab(NULL, %L)$$, 'UPDATE platform.auth_otp_challenge SET failed_attempts = 0 RETURNING 1'), '42501',
                'BFF 不能直接改验证码挑战');
SELECT t.throws(format($$SELECT t.ab(NULL, %L)$$, 'DELETE FROM platform.auth_rate_counter RETURNING 1'), '42501', 'BFF 不能直接清限流计数');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p, unnest(ARRAY['starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                        'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) r(role)
   WHERE p.pronamespace = 'platform'::regnamespace AND p.proname LIKE 'auth\_%' AND has_function_privilege(r.role, p.oid, 'EXECUTE')),
  'BFF 以外的应用角色不能调用任何 auth_ 函数（含内部函数）');
SELECT t.ok((SELECT count(*) FROM pg_proc p WHERE p.pronamespace = 'platform'::regnamespace AND p.proname LIKE 'auth\_%'
               AND has_function_privilege('starbuddy_bff', p.oid, 'EXECUTE')) = 7
            AND NOT has_function_privilege('starbuddy_bff', 'platform.auth_new_session(uuid, text, uuid, timestamptz, integer, integer)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_bff', 'platform.auth_now()', 'EXECUTE'),
            'BFF 只能调用七个对外函数，不能直接建会话、不能读时钟替身');
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['starbuddy_learning', 'starbuddy_review', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_scheduler'] LOOP
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT * FROM platform.auth_session_resolve(%L)', r, repeat('a', 64)), '42501',
                     r || ' 不能解析家庭会话');
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT platform.auth_otp_issue(%L, %L::bytea, 300, 5)', r, '+99900000999',
                            sha256('x')), '42501', r || ' 不能申请验证码');
  END LOOP;
END $$;
RESET ROLE;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.pronamespace = 'platform'::regnamespace AND p.proname LIKE 'auth\_%' AND p.prosecdef
                          AND (pg_get_userbyid(p.proowner) <> 'starbuddy_authz'
                               OR NOT (SELECT bool_or(c ~ '^search_path=.*pg_temp$') FROM unnest(p.proconfig) c))),
            '七个 SECURITY DEFINER 函数属主是 authz、search_path 固定且以 pg_temp 结尾');

-- ================= 申请验证码：注册与未注册的号码结果相同 =================
SELECT t.ok(t.issue(:pK, '111111') IS NOT DISTINCT FROM t.issue(:pNew, '222222'), '已注册与未注册号码：申请结果相同');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.account_contact WHERE phone_e164 = :pNew), '申请验证码不建账号');
SELECT t.ok((SELECT count(*) FROM platform.auth_otp_challenge WHERE phone_digest IN (sha256(convert_to(:pK, 'UTF8')), sha256(convert_to(:pNew, 'UTF8')))
               AND closed_at IS NULL) = 2, '两个号码各有一个未关闭的挑战');
SELECT t.ok((SELECT code_mac = t.mac(:pK, '111111') FROM platform.auth_otp_challenge
              WHERE phone_digest = sha256(convert_to(:pK, 'UTF8')) AND closed_at IS NULL)
            AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'platform' AND table_name = 'auth_otp_challenge'
                              AND data_type IN ('text', 'character varying') AND column_name <> 'closed_reason'),
            '库里只存验证码的 MAC，没有能放验证码或手机号原文的文本列');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT platform.auth_otp_issue(%L, %L::bytea, 300, 5)::text', '13800000000', sha256('x'))),
                '22023', '号码不是 E.164：拒绝');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT platform.auth_otp_issue(%L, %L::bytea, 86400, 5)::text', :pK, sha256('x'))),
                '22023', '验证码有效期超过上界：拒绝（配置写错不能变成长期有效）');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT platform.auth_otp_issue(%L, %L::bytea, 300, 50)::text', :pK, sha256('x'))),
                '22023', '错误次数上限超过上界：拒绝');

-- ================= 登录 =================
SELECT t.login(:pK, '999999') AS f_wrong \gset
SELECT t.ok((:'f_wrong')::jsonb ->> 'outcome' = 'failed', '验证码错误：失败');
SELECT t.ok((SELECT failed_attempts FROM platform.auth_otp_challenge WHERE phone_digest = sha256(convert_to(:pK, 'UTF8')) AND closed_at IS NULL) = 1,
            '错码计次');
SELECT t.login(:pK, '111111') AS l1 \gset
SELECT (:'l1')::jsonb ->> 'session_token' AS tk1 \gset
SELECT t.ok((:'l1')::jsonb ->> 'outcome' = 'ok' AND (:'l1')::jsonb ->> 'account_id' = :gK AND :'tk1' ~ '^[0-9a-f]{64}$',
            '验证码正确：登录已注册的家长，得到 256 位令牌');
SELECT t.login(:pK, '111111') AS f_used \gset
SELECT t.ok((:'f_used')::jsonb = (:'f_wrong')::jsonb, '同一个验证码用第二次：失败，结果与错码相同');
SELECT t.ok((SELECT count(*) FROM platform.auth_session WHERE token_hash = sha256(convert_to(:'tk1', 'UTF8'))) = 1
            AND NOT EXISTS (SELECT 1 FROM platform.auth_session WHERE encode(token_hash, 'hex') = :'tk1'), '库里只存令牌的 sha256');
-- 新号码：首次登录注册家长；第二次登录不新建账号
SELECT t.login(:pNew, '222222') AS lnew \gset
SELECT t.ok((:'lnew')::jsonb ->> 'outcome' = 'ok' AND (:'lnew')::jsonb ->> 'account_kind' = 'guardian'
            AND (SELECT count(*) FROM platform.account_contact c JOIN platform.account a ON a.id = c.account_id
                  WHERE c.phone_e164 = :pNew AND a.kind = 'guardian') = 1, '未注册号码：验证通过后注册一个家长账号');
SELECT count(*) AS n_accounts FROM platform.account \gset
SELECT t.issue(:pNew, '333333');
SELECT t.ok(t.login(:pNew, '333333') ->> 'account_id' = (:'lnew')::jsonb ->> 'account_id'
            AND (SELECT count(*) FROM platform.account) = :n_accounts, '同一号码第二次登录：同一账号，账号数不变');
-- 过期
SELECT t.issue(:pK, '444444');
SELECT t.aadvance('4 minutes 59 seconds');
SELECT t.ok(t.login(:pK, '444444') ->> 'outcome' = 'ok', '4 分 59 秒：仍有效');
SELECT t.issue(:pK, '555555');
SELECT t.aadvance('5 minutes');
SELECT t.ok(t.login(:pK, '555555') = (:'f_wrong')::jsonb, '满 5 分钟：过期，结果与错码相同');
-- 错满 5 次作废：之后正确的码也不行
SELECT t.issue(:pK, '666666');
DO $$ BEGIN FOR i IN 1..5 LOOP PERFORM t.login('+99900000901', '000000'); END LOOP; END $$;
SELECT t.ok((SELECT closed_reason = 'exhausted' AND failed_attempts = 5 FROM platform.auth_otp_challenge
              WHERE phone_digest = sha256(convert_to(:pK, 'UTF8')) ORDER BY created_at DESC LIMIT 1), '错 5 次：挑战作废');
SELECT t.ok(t.login(:pK, '666666') = (:'f_wrong')::jsonb, '作废后正确的码也失败，结果与错码相同');
-- 新申请作废旧码
SELECT t.issue(:pK, '121212');
SELECT t.issue(:pK, '343434');
SELECT t.ok(t.login(:pK, '121212') = (:'f_wrong')::jsonb, '新申请之后旧码失效');
SELECT t.ok(t.login(:pK, '343434') ->> 'outcome' = 'ok', '新码可以登录');
SELECT t.ok((SELECT count(*) FROM platform.auth_otp_challenge WHERE phone_digest = sha256(convert_to(:pK, 'UTF8')) AND closed_at IS NULL) = 0,
            '每个号码最多一个未关闭的挑战');
-- 换号使用：A 号的码拿去登录 B 号
SELECT t.issue(:pK, '565656');
SELECT t.issue(:pK2, '787878');
SELECT t.ok(t.login(:pK2, '565656') = (:'f_wrong')::jsonb, '换号使用：失败，结果与错码相同');
SELECT t.ok(t.login(:pK2, '787878') ->> 'account_id' = :gK2, '（对照）本号的码可以登录');
SELECT t.ok(t.login('not-a-phone', '565656') = (:'f_wrong')::jsonb, '号码格式不对：同样只是失败');
-- 同号多个家长账号（历史数据）：码对了也拒绝登录，提示联系客服；码错时与普通失败相同
SELECT t.issue(:pDup, '909090');
SELECT t.ok(t.login(:pDup, '000000') = (:'f_wrong')::jsonb, '同号多账号、码错：普通失败（不泄露同号账号数）');
SELECT t.ok(t.login(:pDup, '909090') = '{"outcome": "contact_support", "session_token": null, "account_id": null, "account_kind": null}'::jsonb
            AND NOT EXISTS (SELECT 1 FROM platform.auth_session WHERE account_id IN (:gD1, :gD2)), '同号多个家长账号：拒绝登录，不建会话');
-- 停用的家长：失败，也不借“没有账号”注册一个新的
SELECT t.issue(:pX, '010101');
SELECT t.ok(t.login(:pX, '010101') = (:'f_wrong')::jsonb
            AND (SELECT count(*) FROM platform.account_contact WHERE phone_e164 = :pX) = 1, '停用的家长不能登录，也不会被重新注册');
-- 只有学生账号有这个号码：手机号只登录家长账号，不会登进学生账号
SELECT t.issue(:pStu, '020202');
SELECT t.login(:pStu, '020202') AS lstu \gset
SELECT t.ok((:'lstu')::jsonb ->> 'account_kind' = 'guardian' AND (:'lstu')::jsonb ->> 'account_id' <> :aK1,
            '号码只挂在学生账号上：新注册家长，不会登进学生账号（规则 5）');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT outcome FROM platform.auth_otp_login(%L, %L::bytea, %s, 3600, NULL)', :pK,
                t.mac(:pK, '1'), 90 * 86400)), '22023', '会话绝对时长超过上界：拒绝');

-- ================= 会话 =================
SELECT t.ok(t.resolve(:'tk1') = :gK || '|guardian|otp', '有效会话：只解析出账号 ID、账号类型、会话来源');
SELECT t.ok(t.resolve(repeat('0', 64)) IS NULL AND t.resolve('not-a-token') IS NULL AND t.resolve(NULL) IS NULL
            AND t.resolve(upper(:'tk1')) IS NULL, '伪造或格式不对的令牌解析不出');
-- 登录时带上本设备旧令牌：旧令牌立即作废（防会话固定）
SELECT t.issue(:pK, '131313');
SELECT t.login(:pK, '131313', :'tk1') AS l2 \gset
SELECT (:'l2')::jsonb ->> 'session_token' AS tk2 \gset
SELECT t.ok(:'tk2' <> :'tk1' AND t.resolve(:'tk1') IS NULL AND t.resolve(:'tk2') = :gK || '|guardian|otp', '重新登录：令牌变化，旧令牌失效');
-- 空闲 24 小时
SELECT t.aadvance('23 hours 59 minutes');
SELECT t.ok(t.resolve(:'tk2') IS NOT NULL, '空闲 23 小时 59 分：仍有效（解析会刷新最近活动时间）');
SELECT t.aadvance('24 hours');
SELECT t.ok(t.resolve(:'tk2') IS NULL, '空闲满 24 小时：失效');
-- 绝对 7 天：每 20 小时用一次，到 7 天仍然失效
SELECT t.issue(:pK, '141414');
SELECT t.login(:pK, '141414') ->> 'session_token' AS tk3 \gset
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_1;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_2;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_3;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_4;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_5;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_6;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_7;
SELECT t.aadvance('20 hours'); SELECT t.resolve(:'tk3') IS NOT NULL AS alive_8;
SELECT t.ok(t.resolve(:'tk3') IS NOT NULL, '6 天 16 小时、一直在用：仍有效');
SELECT t.aadvance('8 hours');
SELECT t.ok(t.resolve(:'tk3') IS NULL, '满 7 天：失效（绝对时长）');
-- 登出
SELECT t.issue(:pK, '151515');
SELECT t.login(:pK, '151515') ->> 'session_token' AS tk4 \gset
SELECT t.ok(t.ab(NULL, format('SELECT platform.auth_session_revoke(%L)::text', :'tk4'))::boolean
            AND t.resolve(:'tk4') IS NULL, '登出：本会话失效');
SELECT t.ok(NOT t.ab(NULL, format('SELECT platform.auth_session_revoke(%L)::text', :'tk4'))::boolean, '重复登出：没有可作废的会话');
-- 退出所有设备：只作废本账号的会话
SELECT t.issue(:pK, '161616');
SELECT t.login(:pK, '161616') ->> 'session_token' AS tk5 \gset
SELECT t.issue(:pK, '171717');
SELECT t.login(:pK, '171717') ->> 'session_token' AS tk6 \gset
SELECT t.issue(:pK2, '181818');
SELECT t.login(:pK2, '181818') ->> 'session_token' AS tko \gset
SELECT t.ok(t.ab(NULL, format('SELECT platform.auth_session_revoke_all(%L)::text', :'tk5'))::integer >= 2
            AND t.resolve(:'tk5') IS NULL AND t.resolve(:'tk6') IS NULL AND t.resolve(:'tko') = :gK2 || '|guardian|otp',
            '退出所有设备：本账号的两个会话都失效，别的账号不受影响');
SELECT t.ok(t.ab(NULL, format('SELECT platform.auth_session_revoke_all(%L)::text', :'tk5'))::integer = 0,
            '已失效的令牌不能再“退出所有设备”');
-- 账号停用：已有会话下一次解析即失效
SELECT t.issue(:pK, '191919');
SELECT t.login(:pK, '191919') ->> 'session_token' AS tk7 \gset
UPDATE platform.account SET status = 'disabled' WHERE id = :gK;
SELECT t.ok(t.resolve(:'tk7') IS NULL, '账号停用后已有会话失效');
UPDATE platform.account SET status = 'active' WHERE id = :gK;
SELECT t.ok(t.resolve(:'tk7') = :gK || '|guardian|otp', '恢复后同一会话仍在有效期内');

-- ================= 家长在本设备打开学生模式 =================
-- 没有同意记录：绑定核对通过后才报“缺同意”
SELECT t.ok(t.handoff(:gK, :K2, :'tk7') = '55000:consent required', '缺隐私政策同意：不能打开');
SELECT t.ok(t.resolve(:'tk7') IS NOT NULL, '打开失败时家长会话不受影响');
SELECT t.ab(:gK, format($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                          VALUES (%L, %L, 'privacy_policy', %L, 'granted') RETURNING 'x'$$, :K2, :gK, :dv));
SELECT t.handoff(:gK, :K2, :'tk7') AS sk2 \gset
SELECT t.ok(:'sk2' ~ '^[0-9a-f]{64}$', '已满 14 周岁、有隐私政策同意：打开学生模式，得到学生会话令牌');
SELECT t.ok(t.resolve(:'sk2') = :aK2 || '|student|guardian_handoff', '学生会话解析出学生账号，来源是家长签发');
SELECT t.ok(t.resolve(:'tk7') IS NULL, '家长的会话同时作废：孩子拿不到家长会话');
SELECT t.ok((SELECT issued_by_account = :gK FROM platform.auth_session WHERE token_hash = sha256(convert_to(:'sk2', 'UTF8'))),
            '学生会话记下签发的家长');
-- 学生会话不能再签发学生会话（也不能冒充家长）
SELECT t.ok(t.handoff(:aK2, :K2, :'sk2') = '42501:not allowed', '学生会话不能打开学生模式');
SELECT t.ok(t.handoff(:gK, :K2, :'sk2') = '42501:not allowed', '学生会话令牌配家长身份也不行（来源不是短信登录）');
-- 不满 14：出生年月为空按不满 14；还需要监护人同意
SELECT t.issue(:pK, '212121');
SELECT t.login(:pK, '212121') ->> 'session_token' AS tk8 \gset
SELECT t.ab(:gK, format($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                          VALUES (%L, %L, 'privacy_policy', %L, 'granted'), (%L, %L, 'privacy_policy', %L, 'granted') RETURNING 'x'$$,
                        :K1, :gK, :dv, :K3, :gK, :dv));
SELECT t.ok(t.handoff(:gK, :K1, :'tk8') = '55000:consent required', '出生年月为空：按不满 14 处理，缺监护人同意不能打开');
SELECT t.ok(t.handoff(:gK, :K3, :'tk8') = '55000:consent required', '本月才满 14（按该月最后一天算）：仍按不满 14 处理');
SELECT t.ab(:gK, format($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                          VALUES (%L, %L, 'guardian_consent_under14', %L, 'granted') RETURNING 'x'$$, :K1, :gK, 'guardian-consent@synthetic-0'));
SELECT t.ok(t.handoff(:gK, :K1, :'tk8') ~ '^[0-9a-f]{64}$', '补上监护人同意：可以打开');
-- 撤回同意（最新一条是 revoked）：不能打开
SELECT t.issue(:pK, '222223');
SELECT t.login(:pK, '222223') ->> 'session_token' AS tk9 \gset
SELECT t.aadvance('1 minute');
SELECT t.ab(:gK, format($$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                          VALUES (%L, %L, 'privacy_policy', %L, 'revoked') RETURNING 'x'$$, :K2, :gK, :dv));
SELECT t.ok(t.handoff(:gK, :K2, :'tk9') = '55000:consent required', '隐私政策同意已撤回：不能打开');
-- 未绑定、已解除绑定、学生不存在、学生账号停用：一律同一个拒绝
SELECT t.ok(t.handoff(:gK2, :K1, :'tko') = '42501:not allowed', '未绑定的家长：拒绝');
SELECT t.issue(:pR, '232323');
SELECT t.login(:pR, '232323') ->> 'session_token' AS tkr \gset
SELECT t.ok(t.handoff(:gR, :K1, :'tkr') = '42501:not allowed', '已解除绑定的家长：拒绝');
SELECT t.ok(t.handoff(:gK, gen_random_uuid(), :'tk9') = t.handoff(:gK2, :K1, :'tko')
            AND t.handoff(:gR, :K1, :'tkr') = t.handoff(:gK, gen_random_uuid(), :'tk9'), '未绑定、已解除绑定与学生不存在：响应逐字相同');
SELECT t.ok(t.handoff(:gK, :K4, :'tk9') = '42501:not allowed', '学生账号停用：拒绝');
-- 会话不能跨账号使用：拿 gK 的令牌、以 gK2 的身份调用
SELECT t.ok(t.handoff(:gK2, :K2, :'tk9') = '42501:not allowed', '令牌属于 gK、当前账号是 gK2：拒绝（会话不能跨账号使用）');
SELECT t.ok(t.handoff(NULL, :K2, :'tk9') = '42501:not allowed', '没有设当前账号：拒绝');
SELECT t.ok(t.resolve(:'tk9') = :gK || '|guardian|otp', '被拒绝的尝试不影响本人的会话');
SELECT t.ok(t.handoff(:gK, :K1, :'tk9') ~ '^[0-9a-f]{64}$', '（对照）同一令牌、本人身份、有同意的孩子：可以打开');
SELECT t.issue(:pK, '242424');
SELECT t.login(:pK, '242424') ->> 'session_token' AS tk10 \gset
SELECT t.throws(format('SELECT t.ab(%L, %L)', :gK, format('SELECT platform.auth_issue_student_session(%L, %L, %s, 3600)', :K1, :'tk10', 99 * 86400)),
                '22023', '学生会话时长超过上界：拒绝（授权核对通过之后）');

-- ================= 限流 =================
SELECT t.ok(t.rate('test.bucket', 'k1', 60, 3) AND t.rate('test.bucket', 'k1', 60, 3) AND t.rate('test.bucket', 'k1', 60, 3)
            AND NOT t.rate('test.bucket', 'k1', 60, 3) AND NOT t.rate('test.bucket', 'k1', 60, 3), '同一窗口第 4 次起超限');
SELECT t.ok(t.rate('test.bucket', 'k2', 60, 3) AND t.rate('test.other', 'k1', 60, 3), '不同的键、不同的桶各自计数');
SELECT t.aadvance('60 seconds');
SELECT t.ok(t.rate('test.bucket', 'k1', 60, 3), '下一个窗口重新计数');
SELECT t.ok((SELECT count(*) FROM platform.auth_rate_counter WHERE bucket = 'test.bucket' AND key_digest = sha256(convert_to('k1', 'UTF8'))) = 1,
            '旧窗口的计数被清掉');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT platform.auth_rate_hit(%L, %L::bytea, 60, 3)::text', 'Bad Bucket', sha256('x'))),
                '22023', '桶名不合格式：拒绝');
SELECT t.throws(format('SELECT t.ab(NULL, %L)', format('SELECT platform.auth_rate_hit(%L, %L::bytea, 60, 3)::text', 'test.bucket', '\x00')),
                '22023', '键不是 32 字节摘要：拒绝（原文不入库）');

-- ================= 约束 =================
SELECT t.throws($$INSERT INTO platform.auth_session (token_hash, account_id, origin, created_at, last_seen_at, expires_at, idle_timeout)
                  VALUES (sha256('y'), '7a000000-0000-4000-8000-0000000000b1', 'guardian_handoff', now(), now(), now() + interval '1 day', interval '1 hour')$$,
                '23514', '家长签发的会话必须记下签发人');
SELECT t.issue('+99900000908', '252525');
SELECT t.throws($$INSERT INTO platform.auth_otp_challenge (phone_digest, code_mac, created_at, expires_at, max_attempts)
                  SELECT phone_digest, code_mac, created_at, expires_at, max_attempts FROM platform.auth_otp_challenge
                   WHERE closed_at IS NULL LIMIT 1$$, '23505', '同一号码不能有两个未关闭的挑战');

-- 会话来源与账号类型不符（只能由超级用户造出来）：解析不出（纵深防御）
INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at, idle_timeout) VALUES
  (sha256(convert_to(repeat('ab', 32), 'UTF8')), :aK1, 'otp', NULL, (SELECT now FROM t.aclock), (SELECT now FROM t.aclock),
   (SELECT now FROM t.aclock) + interval '1 day', interval '1 hour'),
  (sha256(convert_to(repeat('cd', 32), 'UTF8')), :gK, 'guardian_handoff', :gK2, (SELECT now FROM t.aclock), (SELECT now FROM t.aclock),
   (SELECT now FROM t.aclock) + interval '1 day', interval '1 hour');
SELECT t.ok(t.resolve(repeat('ab', 32)) IS NULL AND t.resolve(repeat('cd', 32)) IS NULL,
            '短信来源的会话挂在学生账号上、家长签发的会话挂在家长账号上：都解析不出');

-- 恢复时钟函数（与迁移里的定义一致），清理本文件的数据
CREATE OR REPLACE FUNCTION platform.auth_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $$ SELECT clock_timestamp() $$;
DELETE FROM platform.auth_session;
DELETE FROM platform.auth_otp_challenge;
DELETE FROM platform.auth_rate_counter;
DELETE FROM platform.consent_record WHERE student_id IN (:K1, :K2, :K3, :K4);
DELETE FROM platform.guardian_student_link WHERE student_id IN (:K1, :K2, :K3, :K4);
DELETE FROM platform.student_profile WHERE id IN (:K1, :K2, :K3, :K4);
DELETE FROM platform.household WHERE id = :hK;
DELETE FROM platform.account_contact WHERE phone_e164 LIKE '+9990000090_';
DELETE FROM platform.account WHERE id IN (:gK, :gK2, :gR, :gD1, :gD2, :gX, :aK1, :aK2, :aK3, :aK4,
                                          ((:'lnew')::jsonb ->> 'account_id')::uuid, ((:'lstu')::jsonb ->> 'account_id')::uuid);
DROP TABLE t.aclock CASCADE;
