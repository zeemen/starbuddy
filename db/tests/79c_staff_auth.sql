-- 0020 审核台 staff 登录（specs/release-gate-sprint4/pr4-staff-login.md 第 7 节验收）。全部合成：账号、口令、TOTP 密钥都是测试用的。
-- 时钟：把 platform.staff_now() 换成读 t.sclock 的版本（只在测试库里做，文件末尾恢复），以便推进时间。

CREATE TABLE t.sclock (now timestamptz NOT NULL);
INSERT INTO t.sclock VALUES ('2027-03-01 08:00:10+00');
GRANT SELECT ON t.sclock TO starbuddy_authz;
CREATE OR REPLACE FUNCTION platform.staff_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $$ SELECT now FROM t.sclock $$;
CREATE FUNCTION t.sadvance(p interval) RETURNS void LANGUAGE sql AS $$ UPDATE t.sclock SET now = now + p $$;
-- 当前时钟下第 p_offset 个时间步的 6 位码（测试方按 RFC 6238 独立计算：与函数同一公式，另有 RFC 测试向量核对）
CREATE FUNCTION t.scode(p_secret bytea, p_offset integer DEFAULT 0) RETURNS text LANGUAGE sql AS $$
  SELECT platform.staff_totp(p_secret, floor(extract(epoch FROM (SELECT now FROM t.sclock)) / 30)::bigint + p_offset) $$;
-- 以审核台后端的签字身份（starbuddy_review）调用
CREATE FUNCTION t.slogin(p_login text, p_password text, p_code text) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(NULL, format('SELECT platform.staff_login(%L, %L, %L)', p_login, p_password, p_code)) $$;
CREATE FUNCTION t.sresolve(p_token text) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(NULL, format('SELECT account_id::text || %L || login_name FROM platform.staff_session_resolve(%L)', '|', p_token)) $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;

\set sa '''5a5a0000-0000-4000-8000-00000000005a'''
\set sd '''5d5d0000-0000-4000-8000-00000000005d'''
\set secret '''\\x3132333435363738393031323334353637383930'''
INSERT INTO platform.account (id, kind) VALUES (:sa, 'staff'), (:sd, 'staff');

-- ================= TOTP 与 RFC 6238 测试向量 =================
-- 密钥 "12345678901234567890"；T = 59 秒 → 第 1 步 → 94287082（取后 6 位）；T = 1111111109 → 07081804
SELECT t.ok(platform.staff_totp(:secret::bytea, 1) = '287082', 'RFC 6238 测试向量：T = 59');
SELECT t.ok(platform.staff_totp(:secret::bytea, 1111111109 / 30) = '081804', 'RFC 6238 测试向量：T = 1111111109');

-- ================= 建立凭据（只由人调用） =================
SELECT t.throws(format('SELECT platform.staff_enroll(%L, %L, %L, %L::bytea)', 'a0000000-0000-4000-8000-00000000000a', 'guardian.a',
                       'synthetic-password-01', :secret), '23514', '家长账号不能有审核台凭据');
SELECT t.throws(format('SELECT platform.staff_enroll(%L, %L, %L, %L::bytea)', :sa, 'teacher.a', 'short', :secret), '22023', '口令至少 12 位');
SELECT t.throws(format('SELECT platform.staff_enroll(%L, %L, %L, %L::bytea)', :sa, 'teacher.a', repeat('口', 25), :secret), '22023',
                '口令超过 72 字节被拒（bcrypt 只看前 72 字节）');
SELECT platform.staff_enroll(:sa, 'teacher.a', 'synthetic-password-01', :secret::bytea);
SELECT platform.staff_enroll(:sd, 'teacher.d', 'synthetic-password-02', :secret::bytea);
SELECT t.ok((SELECT password_hash ~ '^\$2a\$12\$' AND password_hash NOT LIKE '%synthetic%' FROM platform.staff_credential WHERE account_id = :sa),
            '口令只存 bcrypt 哈希（cost 12）');

-- ================= 权限 =================
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', 'SELECT count(*)::text FROM platform.staff_credential'), '42501', '签字身份读不到凭据表');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', 'SELECT count(*)::text FROM platform.staff_session'), '42501', '签字身份读不到会话表');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', format('SELECT platform.staff_enroll(%L, %L, %L, %L::bytea)::text', :sd, 'x.y.z',
                'synthetic-password-03', :secret)), '42501', '签字身份不能建凭据');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', format('SELECT platform.staff_reset(%L, %L, %L::bytea)::text', :sa,
                'synthetic-password-03', :secret)), '42501', '签字身份不能重置凭据');
SELECT t.throws(format('SELECT t.as_staff(NULL, %L)', 'SELECT platform.staff_totp(''\x00''::bytea, 1)'), '42501', '签字身份不能直接算 6 位码');
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['starbuddy_kge', 'starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_scheduler'] LOOP
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT platform.staff_login(%L, %L, %L)', r, 'teacher.a', 'synthetic-password-01', '000000'),
                     '42501', r || ' 不能调用 staff 登录');
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT * FROM platform.staff_session_resolve(%L)', r, repeat('a', 64)),
                     '42501', r || ' 不能解析 staff 会话');
    PERFORM t.throws(format('SET LOCAL ROLE %I; SELECT count(*) FROM platform.staff_credential', r), '42501', r || ' 读不到凭据表');
  END LOOP;
END $$;
RESET ROLE;

-- ================= 登录 =================
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) ~ '^[0-9a-f]{64}$', '口令与当前 6 位码正确：得到会话令牌');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL, '同一个 6 位码不能用两次');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea, -1)) IS NULL, '更早一步的码也不能再用');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea, 1)) ~ '^[0-9a-f]{64}$', '下一步的码（时钟容差 1 步）可以用');
SELECT t.sadvance('2 minutes');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea, 2)) IS NULL, '超出容差的码被拒');
SELECT t.ok((SELECT failed_count FROM platform.staff_credential WHERE account_id = :sa) = 1, '失败计次');
SELECT t.ok(t.slogin('teacher.a', 'wrong-password-000', t.scode(:secret::bytea)) IS NULL, '口令错被拒');
SELECT t.ok(t.slogin('nobody.here', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL, '没有这个登录名：同样只是失败');
SELECT t.ok(t.slogin('Bad Login', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL
            AND t.slogin('teacher.a', 'synthetic-password-01', '12345') IS NULL, '格式不对：失败');
SELECT t.ok((SELECT failed_count FROM platform.staff_credential WHERE account_id = :sa) = 2, '格式不对不计次（不能借格式错误撞锁）');
-- 连错 5 次：锁 15 分钟，锁定期间正确凭据也被拒，也不计次
DO $$ BEGIN FOR i IN 1..3 LOOP PERFORM t.slogin('teacher.a', 'wrong-password-000', t.scode('\x3132333435363738393031323334353637383930'::bytea)); END LOOP; END $$;
SELECT t.ok((SELECT failed_count = 0 AND locked_until = (SELECT now FROM t.sclock) + interval '15 minutes'
               FROM platform.staff_credential WHERE account_id = :sa), '连错 5 次锁 15 分钟');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL, '锁定期间正确凭据也被拒');
SELECT t.sadvance('14 minutes');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL, '锁定 14 分钟时仍被拒');
SELECT t.sadvance('2 minutes');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) ~ '^[0-9a-f]{64}$', '锁定期过后可以登录');
SELECT t.ok((SELECT failed_count = 0 AND locked_until IS NULL FROM platform.staff_credential WHERE account_id = :sa), '成功后清零');
-- 失败计次只看最近 15 分钟
SELECT t.sadvance('1 minute');
DO $$ BEGIN FOR i IN 1..4 LOOP PERFORM t.slogin('teacher.a', 'wrong-password-000', '000000'); END LOOP; END $$;
SELECT t.sadvance('16 minutes');
SELECT t.ok(t.slogin('teacher.a', 'wrong-password-000', '000000') IS NULL, '又错一次');
-- 同一条语句里的子查询看不到函数刚写的计次（语句快照），所以分成两条
SELECT t.ok((SELECT failed_count = 1 AND locked_until IS NULL FROM platform.staff_credential WHERE account_id = :sa),
            '上一次失败超过 15 分钟：从 1 重新计，不会被旧的失败凑满锁定');
SELECT t.sadvance('1 minute');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NOT NULL, '之后正确凭据可以登录');
-- 口令超过 72 字节：bcrypt 只看前 72 字节，不拦就会让“72 字节口令 + 任意后缀”也登录成功
INSERT INTO platform.account (id, kind) VALUES ('5b5b0000-0000-4000-8000-00000000005b', 'staff');
SELECT platform.staff_enroll('5b5b0000-0000-4000-8000-00000000005b', 'teacher.l', repeat('p', 72), :secret::bytea);
SELECT t.ok(t.slogin('teacher.l', repeat('p', 72) || 'suffix', t.scode(:secret::bytea)) IS NULL, '口令超过 72 字节：失败（不因截断误放行）');
SELECT t.ok(t.slogin('teacher.l', repeat('p', 72), t.scode(:secret::bytea, 1)) IS NOT NULL, '72 字节口令本身可以登录');
-- 停用账号
UPDATE platform.account SET status = 'disabled' WHERE id = :sd;
SELECT t.ok(t.slogin('teacher.d', 'synthetic-password-02', t.scode(:secret::bytea)) IS NULL, '停用的 staff 不能登录');

-- ================= 会话 =================
SELECT t.sadvance('1 minute');
CREATE TEMP TABLE tok AS SELECT t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) AS v;
GRANT SELECT ON tok TO PUBLIC;
SELECT t.ok(t.sresolve((SELECT v FROM tok)) = :sa || '|teacher.a', '有效会话解析出账号与登录名');
SELECT t.ok((SELECT count(*) FROM platform.staff_session WHERE token_hash = sha256(convert_to((SELECT v FROM tok), 'UTF8'))) = 1
            AND NOT EXISTS (SELECT 1 FROM platform.staff_session s, tok WHERE encode(s.token_hash, 'hex') = tok.v), '库里只存令牌哈希');
SELECT t.ok(t.sresolve(repeat('0', 64)) IS NULL AND t.sresolve('not-a-token') IS NULL AND t.sresolve(NULL) IS NULL, '伪造或格式不对的令牌解析不出');
-- 空闲 30 分钟失效
SELECT t.sadvance('29 minutes');
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NOT NULL, '空闲 29 分钟仍有效（解析会刷新最近活动时间）');
SELECT t.sadvance('31 minutes');
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NULL, '空闲超过 30 分钟失效');
-- 绝对 12 小时失效：每 20 分钟用一次，到 12 小时仍然失效
SELECT t.sadvance('1 minute');
UPDATE tok SET v = t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea));
DO $$ BEGIN FOR i IN 1..35 LOOP PERFORM t.sadvance('20 minutes'); PERFORM t.sresolve((SELECT v FROM tok)); END LOOP; END $$;
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NOT NULL, '11 小时 40 分钟、一直在用：仍有效');
SELECT t.sadvance('20 minutes');
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NULL, '满 12 小时失效（绝对时长）');
-- 退出
SELECT t.sadvance('1 minute');
UPDATE tok SET v = t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea));
SELECT t.as_staff(NULL, format('SELECT platform.staff_logout(%L)::text', (SELECT v FROM tok)));
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NULL, '退出后令牌失效');
-- 一个账号同时只有一个有效会话
SELECT t.sadvance('1 minute');
UPDATE tok SET v = t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea));
SELECT t.sadvance('1 minute');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NOT NULL
            AND t.sresolve((SELECT v FROM tok)) IS NULL, '新登录撤销同一账号的旧会话');
-- 账号停用：已有会话立即失效
SELECT t.sadvance('1 minute');
UPDATE tok SET v = t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea));
UPDATE platform.account SET status = 'disabled' WHERE id = :sa;
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NULL, '账号停用后已有会话立即失效');
UPDATE platform.account SET status = 'active' WHERE id = :sa;
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NOT NULL, '恢复后同一会话仍在有效期内');
-- 重置凭据：撤销全部会话，旧口令失效
SELECT platform.staff_reset(:sa, 'synthetic-password-09', :secret::bytea);
SELECT t.ok(t.sresolve((SELECT v FROM tok)) IS NULL, '重置凭据撤销该账号全部会话');
SELECT t.sadvance('1 minute');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-01', t.scode(:secret::bytea)) IS NULL, '重置后旧口令失效');
SELECT t.ok(t.slogin('teacher.a', 'synthetic-password-09', t.scode(:secret::bytea, 1)) IS NOT NULL, '新口令可以登录');
-- 凭据不可挂到非 staff（改 account_id 也拦）
SELECT t.throws(format('UPDATE platform.staff_credential SET account_id = %L WHERE account_id = %L', 'a0000000-0000-4000-8000-00000000000a', :sa),
                '23514', '凭据不能改挂到非 staff 账号');

-- 恢复时钟函数（与迁移里的定义一致），清理本文件的数据
CREATE OR REPLACE FUNCTION platform.staff_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog AS $$ SELECT clock_timestamp() $$;
DELETE FROM platform.staff_session;
DELETE FROM platform.staff_credential;
DELETE FROM platform.account WHERE id IN (:sa, :sd, '5b5b0000-0000-4000-8000-00000000005b');
DROP TABLE tok;
