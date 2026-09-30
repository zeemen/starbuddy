-- 0029 platform · 家庭登录与会话（specs/unified-shell-sprint4/spec.md 第 2、3、17 节，H1）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。不在任何共享环境执行。
-- 依赖：0002（account、account_contact、student_profile、guardian_student_link、consent_record）、0009（pgcrypto）。
-- 做法与 0020（审核台 staff 登录）一致：
--   - 三张表（验证码挑战、会话、限流计数）ENABLE + FORCE RLS，没有任何策略：只有函数属主 starbuddy_authz（BYPASSRLS）能读写，
--     应用角色一律读不到（待人确认 #2 默认）；
--   - 七个 SECURITY DEFINER 函数（属主 starbuddy_authz、search_path 固定且以 pg_temp 结尾），只授 starbuddy_bff；
--   - 验证码不进库：BFF 生成 6 位码，经 SmsSender 发出，只把 HMAC（BFF 持有密钥）交给库；库里只存这个 MAC，
--     核对也只比较 MAC（比较的不是秘密本身，逐字节比较的时间差对攻击者没有用处；BFF 侧的比较用常量时间）；
--   - 会话令牌由库生成（256 位随机数），只返回一次，库里只存 sha256；
--   - “核对验证码 → 查账号（没有就注册）→ 建会话”在同一个函数里完成：BFF 没有“为任意账号建会话”的函数可调，
--     会话只能从验证码（origin = otp）或家长在本设备打开学生模式（origin = guardian_handoff）产生；
--   - 手机号只用于登录家长账号（规则 5）：只按 kind = 'guardian' 的账号匹配；同号多个家长账号时拒绝登录（待人确认 #6）；
--     任何函数都不按手机号返回或关联学生数据；
--   - 验证码有效期、错误次数上限、会话绝对与空闲时长由调用方（BFF 的配置文件 services/bff/config/auth.json）传入，
--     库里只卡上下界，防止配置写错成“永不过期”；
--   - 时钟 platform.auth_now()：测试库可以替换成可推进的时钟，生产就是当前时间。
-- 数据库服务器日志：auth_otp_login 的参数有手机号原文（验证码不在库里，只有 MAC）。部署时须对 BFF 的登录角色关掉语句与参数日志
-- （做法同 0020：log_statement = 'none'、log_parameter_max_length = 0 等），见 services/bff/README.md。

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
    RAISE EXCEPTION 'pgcrypto 未安装（0009 应已安装）';
  END IF;
END $$;

-- ---------- 时钟 ----------
CREATE FUNCTION platform.auth_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog
  AS $$ SELECT clock_timestamp() $$;
REVOKE EXECUTE ON FUNCTION platform.auth_now() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_now() TO starbuddy_authz;

-- ---------- 随机数（pgcrypto 装在 0009 选定的 schema 里，名字在执行时才知道） ----------
DO $$
DECLARE v_schema text;
BEGIN
  SELECT n.nspname INTO v_schema FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pgcrypto';
  EXECUTE format($f$CREATE FUNCTION platform.auth_random_bytes(p_n integer) RETURNS bytea
                   LANGUAGE sql VOLATILE STRICT SET search_path = pg_catalog AS 'SELECT %I.gen_random_bytes(p_n)'$f$, v_schema);
END $$;
REVOKE EXECUTE ON FUNCTION platform.auth_random_bytes(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_random_bytes(integer) TO starbuddy_authz;

-- ---------- 表 ----------
-- 验证码挑战：每个号码同时最多一个未关闭的挑战（新申请作废旧的）
CREATE TABLE platform.auth_otp_challenge (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone_digest     bytea NOT NULL CHECK (octet_length(phone_digest) = 32),   -- sha256(手机号)，只作查找键
  code_mac         bytea NOT NULL CHECK (octet_length(code_mac) = 32),       -- BFF 算的 HMAC(密钥, 手机号 + 验证码)；验证码原文不入库
  created_at       timestamptz NOT NULL,
  expires_at       timestamptz NOT NULL,
  failed_attempts  smallint NOT NULL DEFAULT 0,
  max_attempts     smallint NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
  closed_at        timestamptz,
  closed_reason    text CHECK (closed_reason IN ('used', 'superseded', 'exhausted')),
  CONSTRAINT auth_otp_closed CHECK ((closed_at IS NULL) = (closed_reason IS NULL)),
  CONSTRAINT auth_otp_times CHECK (expires_at > created_at),
  CONSTRAINT auth_otp_attempts CHECK (failed_attempts BETWEEN 0 AND max_attempts)
);
CREATE UNIQUE INDEX auth_otp_open_uq ON platform.auth_otp_challenge (phone_digest) WHERE closed_at IS NULL;
COMMENT ON TABLE platform.auth_otp_challenge IS
  '家长登录验证码挑战（0029）。只存验证码的 HMAC；只有函数属主 starbuddy_authz 能读写';
COMMENT ON COLUMN platform.auth_otp_challenge.phone_digest IS
  'sha256(手机号)：只作查找键。手机号原文在 account_contact 里（明文还是加密待 ADR 10），这里不再多存一份';

-- 会话：PLT-01 的 PIN 与设备登录在这张表上扩展 origin 与 device_id，不另建第二套会话（待人确认 #1）
CREATE TABLE platform.auth_session (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash         bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),  -- sha256(令牌)；令牌本身不入库
  account_id         uuid NOT NULL REFERENCES platform.account (id),
  origin             text NOT NULL CHECK (origin IN ('otp', 'guardian_handoff')),
  issued_by_account  uuid REFERENCES platform.account (id),   -- guardian_handoff：打开学生模式的家长
  device_id          uuid,                                     -- 预留：PLT-01 设备表上线后加外键
  created_at         timestamptz NOT NULL,
  last_seen_at       timestamptz NOT NULL,
  expires_at         timestamptz NOT NULL,
  idle_timeout       interval NOT NULL CHECK (idle_timeout >= interval '1 minute'),
  revoked_at         timestamptz,
  revoked_reason     text CHECK (revoked_reason IN ('logout', 'logout_all', 'replaced', 'student_mode')),
  CONSTRAINT auth_session_revoked CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL)),
  CONSTRAINT auth_session_times CHECK (last_seen_at >= created_at AND expires_at > created_at),
  CONSTRAINT auth_session_handoff CHECK ((origin = 'guardian_handoff') = (issued_by_account IS NOT NULL))
);
CREATE INDEX auth_session_account_idx ON platform.auth_session (account_id) WHERE revoked_at IS NULL;
COMMENT ON TABLE platform.auth_session IS
  '家庭会话（0029）：otp = 家长短信登录；guardian_handoff = 家长在本设备打开学生模式（PLT-01 之前的过渡入口）。只有函数属主 starbuddy_authz 能读写';

-- 限流计数：固定窗口，多实例共享；键是 BFF 算的 HMAC（手机号、IP、账号都不以原文入库）
CREATE TABLE platform.auth_rate_counter (
  bucket        text NOT NULL CHECK (bucket ~ '^[a-z][a-z0-9_.]{1,47}$'),
  key_digest    bytea NOT NULL CHECK (octet_length(key_digest) = 32),
  window_start  timestamptz NOT NULL,
  hits          integer NOT NULL CHECK (hits >= 1),
  PRIMARY KEY (bucket, key_digest, window_start)
);
COMMENT ON TABLE platform.auth_rate_counter IS '登录与监护操作的限流计数（0029）；只有函数属主 starbuddy_authz 能读写';

ALTER TABLE platform.auth_otp_challenge ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.auth_otp_challenge FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.auth_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.auth_session FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.auth_rate_counter ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.auth_rate_counter FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的 starbuddy_authz（函数属主）能读写
REVOKE ALL ON platform.auth_otp_challenge, platform.auth_session, platform.auth_rate_counter FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON platform.auth_otp_challenge TO starbuddy_authz;
GRANT SELECT, INSERT, UPDATE ON platform.auth_session TO starbuddy_authz;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.auth_rate_counter TO starbuddy_authz;
-- 登录时首次注册家长（0002 已授 SELECT、INSERT）；同意记录的读取（学生模式前提）
GRANT SELECT ON platform.consent_record TO starbuddy_authz;

-- ---------- 内部：令牌 → 会话行（不做授权，只供本迁移的函数调用） ----------
CREATE FUNCTION platform.auth_token_hash(p_token text) RETURNS bytea
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT CASE WHEN p_token ~ '^[0-9a-f]{64}$' THEN sha256(convert_to(p_token, 'UTF8')) END $$;
REVOKE EXECUTE ON FUNCTION platform.auth_token_hash(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_token_hash(text) TO starbuddy_authz;

CREATE FUNCTION platform.auth_phone_ok(p_phone text) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT coalesce(p_phone ~ '^\+[1-9][0-9]{6,14}$', false) $$;
REVOKE EXECUTE ON FUNCTION platform.auth_phone_ok(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_phone_ok(text) TO starbuddy_authz;

-- 新建会话并返回令牌（内部）：会话只能由 auth_otp_login 与 auth_issue_student_session 产生
CREATE FUNCTION platform.auth_new_session(p_account uuid, p_origin text, p_issued_by uuid, p_now timestamptz,
                                          p_absolute_seconds integer, p_idle_seconds integer) RETURNS text
  LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_token text;
BEGIN
  IF p_absolute_seconds IS NULL OR p_absolute_seconds NOT BETWEEN 300 AND 30 * 86400
     OR p_idle_seconds IS NULL OR p_idle_seconds < 60 OR p_idle_seconds > p_absolute_seconds THEN
    RAISE EXCEPTION 'invalid session lifetime' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_token := encode(platform.auth_random_bytes(32), 'hex');
  INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at, idle_timeout)
  VALUES (sha256(convert_to(v_token, 'UTF8')), p_account, p_origin, p_issued_by, p_now, p_now,
          p_now + make_interval(secs => p_absolute_seconds), make_interval(secs => p_idle_seconds));
  RETURN v_token;
END $$;
REVOKE EXECUTE ON FUNCTION platform.auth_new_session(uuid, text, uuid, timestamptz, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_new_session(uuid, text, uuid, timestamptz, integer, integer) TO starbuddy_authz;

-- ---------- 申请验证码 ----------
-- 无论号码是否注册都建挑战（响应不区分，第一轮 C3、第二轮 N1）；同号旧挑战作废
CREATE FUNCTION platform.auth_otp_issue(p_phone text, p_code_mac bytea, p_ttl_seconds integer, p_max_attempts integer) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  v_digest bytea;
BEGIN
  IF NOT platform.auth_phone_ok(p_phone) OR p_code_mac IS NULL OR octet_length(p_code_mac) <> 32
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 60 AND 900 OR p_max_attempts IS NULL OR p_max_attempts NOT BETWEEN 1 AND 10 THEN
    RAISE EXCEPTION 'invalid otp request' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_digest := sha256(convert_to(p_phone, 'UTF8'));
  -- 同一号码的申请与登录串行（新申请作废旧挑战、首次注册一号一个家长账号）
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.auth_phone:' || encode(v_digest, 'hex'), 0));
  UPDATE platform.auth_otp_challenge SET closed_at = v_now, closed_reason = 'superseded'
   WHERE phone_digest = v_digest AND closed_at IS NULL;
  INSERT INTO platform.auth_otp_challenge (phone_digest, code_mac, created_at, expires_at, max_attempts)
  VALUES (v_digest, p_code_mac, v_now, v_now + make_interval(secs => p_ttl_seconds), p_max_attempts);
END $$;

-- ---------- 校验验证码并登录（没有账号就注册），建会话 ----------
-- outcome：ok（带令牌）；failed（码错、过期、用过、作废、换号、账号停用，一律相同）；
-- contact_support（码对了，但同号有多个家长账号：历史数据，拒绝登录，待人确认 #6。只有持有该号码的人能看到这个结果）。
-- 不抛错而是返回 failed：错码计次才能提交。p_prev_token 是本设备原来的会话令牌（可空）：登录成功时作废它（防会话固定）。
CREATE FUNCTION platform.auth_otp_login(p_phone text, p_code_mac bytea, p_absolute_seconds integer, p_idle_seconds integer,
                                        p_prev_token text)
  RETURNS TABLE (outcome text, session_token text, account_id uuid, account_kind platform.account_kind)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  v_digest bytea;
  c platform.auth_otp_challenge%ROWTYPE;
  v_n integer;
  v_acct uuid;
  v_status text;
  v_token text;
BEGIN
  IF p_absolute_seconds IS NULL OR p_absolute_seconds NOT BETWEEN 300 AND 30 * 86400
     OR p_idle_seconds IS NULL OR p_idle_seconds < 60 OR p_idle_seconds > p_absolute_seconds THEN
    RAISE EXCEPTION 'invalid session lifetime' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF NOT platform.auth_phone_ok(p_phone) OR p_code_mac IS NULL OR octet_length(p_code_mac) <> 32 THEN
    RETURN QUERY SELECT 'failed'::text, NULL::text, NULL::uuid, NULL::platform.account_kind;
    RETURN;
  END IF;
  v_digest := sha256(convert_to(p_phone, 'UTF8'));
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.auth_phone:' || encode(v_digest, 'hex'), 0));
  SELECT * INTO c FROM platform.auth_otp_challenge WHERE phone_digest = v_digest AND closed_at IS NULL FOR UPDATE;
  IF NOT FOUND OR c.expires_at <= v_now THEN
    RETURN QUERY SELECT 'failed'::text, NULL::text, NULL::uuid, NULL::platform.account_kind;
    RETURN;
  END IF;
  IF c.code_mac <> p_code_mac THEN
    -- 错码计次；满上限作废
    UPDATE platform.auth_otp_challenge
       SET failed_attempts = c.failed_attempts + 1,
           closed_at = CASE WHEN c.failed_attempts + 1 >= c.max_attempts THEN v_now END,
           closed_reason = CASE WHEN c.failed_attempts + 1 >= c.max_attempts THEN 'exhausted' END
     WHERE id = c.id;
    RETURN QUERY SELECT 'failed'::text, NULL::text, NULL::uuid, NULL::platform.account_kind;
    RETURN;
  END IF;
  -- 码对了：挑战立即用掉（一次性）
  UPDATE platform.auth_otp_challenge SET closed_at = v_now, closed_reason = 'used' WHERE id = c.id;
  -- 只按家长账号匹配（手机号只用于登录家长账号，规则 5）；停用的也算在内：不能靠“换个新账号”绕过停用
  SELECT count(*), min(a.id::text)::uuid, min(a.status) INTO v_n, v_acct, v_status
    FROM platform.account_contact ct JOIN platform.account a ON a.id = ct.account_id
   WHERE ct.phone_e164 = p_phone AND a.kind = 'guardian';
  IF v_n > 1 THEN
    RETURN QUERY SELECT 'contact_support'::text, NULL::text, NULL::uuid, NULL::platform.account_kind;
    RETURN;
  END IF;
  IF v_n = 0 THEN
    INSERT INTO platform.account (kind) VALUES ('guardian') RETURNING id INTO v_acct;
    INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (v_acct, p_phone, v_now);
  ELSIF v_status <> 'active' THEN
    RETURN QUERY SELECT 'failed'::text, NULL::text, NULL::uuid, NULL::platform.account_kind;
    RETURN;
  END IF;
  -- 本设备原来的会话作废（登录发新令牌，旧令牌立即失效）
  UPDATE platform.auth_session SET revoked_at = v_now, revoked_reason = 'replaced'
   WHERE token_hash = platform.auth_token_hash(p_prev_token) AND revoked_at IS NULL;
  v_token := platform.auth_new_session(v_acct, 'otp', NULL, v_now, p_absolute_seconds, p_idle_seconds);
  RETURN QUERY SELECT 'ok'::text, v_token, v_acct, 'guardian'::platform.account_kind;
END $$;

-- ---------- 解析会话（续期：刷新最近活动时间） ----------
-- 未撤销、未到绝对期限、未空闲超时、账号仍有效、来源与账号类型相符才返回；只返回账号 ID、账号类型、会话来源
CREATE FUNCTION platform.auth_session_resolve(p_token text)
  RETURNS TABLE (account_id uuid, account_kind platform.account_kind, origin text)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  v_hash bytea := platform.auth_token_hash(p_token);
  v_id uuid;
  v_origin text;
BEGIN
  IF v_hash IS NULL THEN
    RETURN;
  END IF;
  UPDATE platform.auth_session s SET last_seen_at = greatest(s.last_seen_at, v_now)
   WHERE s.token_hash = v_hash
     AND s.revoked_at IS NULL AND s.expires_at > v_now AND s.last_seen_at + s.idle_timeout > v_now
     AND EXISTS (SELECT 1 FROM platform.account a
                  WHERE a.id = s.account_id AND a.status = 'active'
                    AND ((s.origin = 'otp' AND a.kind = 'guardian') OR (s.origin = 'guardian_handoff' AND a.kind = 'student')))
  RETURNING s.account_id, s.origin INTO v_id, v_origin;
  IF v_id IS NULL THEN
    RETURN;
  END IF;
  RETURN QUERY SELECT a.id, a.kind, v_origin FROM platform.account a WHERE a.id = v_id;
END $$;

-- ---------- 注销本会话 ----------
CREATE FUNCTION platform.auth_session_revoke(p_token text) RETURNS boolean
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  UPDATE platform.auth_session SET revoked_at = platform.auth_now(), revoked_reason = 'logout'
   WHERE token_hash = platform.auth_token_hash(p_token) AND revoked_at IS NULL;
  RETURN FOUND;
END $$;

-- ---------- 退出所有设备：作废该令牌所属账号的全部会话（令牌本身须仍有效） ----------
CREATE FUNCTION platform.auth_session_revoke_all(p_token text) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  v_acct uuid;
  v_n integer;
BEGIN
  SELECT s.account_id INTO v_acct FROM platform.auth_session s
   WHERE s.token_hash = platform.auth_token_hash(p_token)
     AND s.revoked_at IS NULL AND s.expires_at > v_now AND s.last_seen_at + s.idle_timeout > v_now;
  IF v_acct IS NULL THEN
    RETURN 0;
  END IF;
  UPDATE platform.auth_session SET revoked_at = v_now, revoked_reason = 'logout_all'
   WHERE account_id = v_acct AND revoked_at IS NULL;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

-- ---------- 家长在本设备打开学生模式（PLT-01 之前的过渡入口，待人确认 #1） ----------
-- 要求：p_token 是有效的家长短信会话，且就是当前账号（app.account_id）；家长对 p_student 有有效绑定；学生账号有效。
-- 以上任一不满足一律 insufficient_privilege，与“学生不存在”逐字相同（不泄露存在性）。
-- 绑定核对通过后再查同意：必须有 privacy_policy 的 granted；不满 14 周岁（出生年月为空按不满 14，按该月最后一天算）
-- 还必须有 guardian_consent_under14 的 granted（取每类最新一条；同一时刻的撤回优先）。缺同意报 object_not_in_prerequisite_state。
-- 成功：家长的这个会话作废（孩子拿不到家长会话），为学生账号签发新会话并返回令牌。回家长模式要重新短信验证。
CREATE FUNCTION platform.auth_issue_student_session(p_student uuid, p_token text, p_absolute_seconds integer, p_idle_seconds integer)
  RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  s platform.auth_session%ROWTYPE;
  v_student_account uuid;
  v_birth date;
  v_today date := (v_now AT TIME ZONE 'Asia/Shanghai')::date;
BEGIN
  SELECT * INTO s FROM platform.auth_session x
   WHERE x.token_hash = platform.auth_token_hash(p_token)
     AND x.revoked_at IS NULL AND x.expires_at > v_now AND x.last_seen_at + x.idle_timeout > v_now
   FOR UPDATE;
  SELECT sp.account_id, sp.birth_year_month INTO v_student_account, v_birth
    FROM platform.student_profile sp JOIN platform.account sa ON sa.id = sp.account_id
   WHERE sp.id = p_student AND sa.kind = 'student' AND sa.status = 'active';
  IF s.id IS NULL OR s.origin <> 'otp'
     OR platform.current_account_id() IS DISTINCT FROM s.account_id
     OR NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id = s.account_id AND a.kind = 'guardian' AND a.status = 'active')
     OR v_student_account IS NULL
     OR NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l
                     WHERE l.student_id = p_student AND l.guardian_account_id = s.account_id AND l.status = 'active') THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF coalesce((SELECT cr.decision FROM platform.consent_record cr
                WHERE cr.student_id = p_student AND cr.consent_type = 'privacy_policy'
                ORDER BY cr.decided_at DESC, (cr.decision = 'revoked') DESC LIMIT 1), 'none') <> 'granted'
     OR ((v_birth IS NULL OR (v_birth + interval '1 month' - interval '1 day' + interval '14 years')::date > v_today)
         AND coalesce((SELECT cr.decision FROM platform.consent_record cr
                        WHERE cr.student_id = p_student AND cr.consent_type = 'guardian_consent_under14'
                        ORDER BY cr.decided_at DESC, (cr.decision = 'revoked') DESC LIMIT 1), 'none') <> 'granted') THEN
    RAISE EXCEPTION 'consent required' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  UPDATE platform.auth_session SET revoked_at = v_now, revoked_reason = 'student_mode' WHERE id = s.id;
  RETURN platform.auth_new_session(v_student_account, 'guardian_handoff', s.account_id, v_now, p_absolute_seconds, p_idle_seconds);
END $$;

-- ---------- 限流：固定窗口计数，返回“本次是否在上限内” ----------
-- 超限也计数；键由 BFF 算好（HMAC），库里不见手机号、IP、账号原文
CREATE FUNCTION platform.auth_rate_hit(p_bucket text, p_key bytea, p_window_seconds integer, p_limit integer) RETURNS boolean
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.auth_now();
  v_start timestamptz;
  v_hits integer;
BEGIN
  IF p_bucket IS NULL OR p_bucket !~ '^[a-z][a-z0-9_.]{1,47}$' OR p_key IS NULL OR octet_length(p_key) <> 32
     OR p_window_seconds IS NULL OR p_window_seconds NOT BETWEEN 1 AND 7 * 86400 OR p_limit IS NULL OR p_limit < 1 THEN
    RAISE EXCEPTION 'invalid rate limit request' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_start := to_timestamp(floor(extract(epoch FROM v_now) / p_window_seconds) * p_window_seconds);
  DELETE FROM platform.auth_rate_counter WHERE bucket = p_bucket AND key_digest = p_key AND window_start < v_start;
  INSERT INTO platform.auth_rate_counter AS r (bucket, key_digest, window_start, hits) VALUES (p_bucket, p_key, v_start, 1)
  ON CONFLICT (bucket, key_digest, window_start) DO UPDATE SET hits = r.hits + 1
  RETURNING r.hits INTO v_hits;
  RETURN v_hits <= p_limit;
END $$;

-- ---------- 属主与授权：七个函数只授 starbuddy_bff ----------
ALTER FUNCTION platform.auth_otp_issue(text, bytea, integer, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_otp_login(text, bytea, integer, integer, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_session_resolve(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_session_revoke(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_session_revoke_all(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_issue_student_session(uuid, text, integer, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.auth_rate_hit(text, bytea, integer, integer) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.auth_otp_issue(text, bytea, integer, integer),
  platform.auth_otp_login(text, bytea, integer, integer, text), platform.auth_session_resolve(text),
  platform.auth_session_revoke(text), platform.auth_session_revoke_all(text),
  platform.auth_issue_student_session(uuid, text, integer, integer), platform.auth_rate_hit(text, bytea, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.auth_otp_issue(text, bytea, integer, integer),
  platform.auth_otp_login(text, bytea, integer, integer, text), platform.auth_session_resolve(text),
  platform.auth_session_revoke(text), platform.auth_session_revoke_all(text),
  platform.auth_issue_student_session(uuid, text, integer, integer), platform.auth_rate_hit(text, bytea, integer, integer) TO starbuddy_bff;
