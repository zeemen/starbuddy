-- 0020 platform · 审核台 staff 登录（specs/release-gate-sprint4/pr4-staff-login.md，冲刺 1 PR 4）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。只依赖 0002（account）与 0009（pgcrypto）。
-- 做法与 4-H 家庭登录一致：凭据核对与建会话在同一个数据库函数里完成，令牌只存哈希，应用只转交令牌。
--   - 凭据表、会话表开启并强制 RLS，没有任何策略：只有函数属主 starbuddy_authz（BYPASSRLS）能读写，应用角色一律读不到；
--   - 登录、解析会话、退出三个函数只授 starbuddy_review（审核台后端的签字连接）；
--   - 建立与重置凭据的函数不授任何应用角色，只能由人（迁移执行者）在库里调用；
--   - 口令用 bcrypt（cost 12），第二因素是验证器 App 的 6 位码（RFC 6238：HMAC-SHA1、30 秒步长、前后各容 1 步、同一步不能重用）；
--   - 失败一律返回 NULL（不区分没有这个人、口令错、码错、已锁定、账号停用），连错 5 次锁 15 分钟；
--   - 会话绝对 12 小时、空闲 30 分钟；一个账号同时只有一个有效会话（新登录撤销旧会话）；账号停用后已有会话立即失效（每次解析都核对）；
--   - 失败计次只看最近 15 分钟：上一次失败超过 15 分钟就从头计（安全审查 PR 4 建议 9）；
--   - 口令最多 72 字节（bcrypt 只看前 72 字节，超出的部分不参与核对，安全审查建议 7）。
-- 数据库服务器日志：登录函数的参数是明文口令与 6 位码。部署时须对审核台的签字登录角色关掉语句与参数日志（安全审查 B-2），例如：
--   ALTER ROLE <签字登录角色> SET log_statement = 'none';  ALTER ROLE <签字登录角色> SET log_min_duration_statement = -1;
--   ALTER ROLE <签字登录角色> SET log_parameter_max_length = 0;  ALTER ROLE <签字登录角色> SET log_parameter_max_length_on_error = 0;
-- 见 services/learning/README.md“审核台部署要求”。

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
    RAISE EXCEPTION 'pgcrypto 未安装（0009 应已安装）';
  END IF;
END $$;

-- ---------- 时钟（测试库可以替换成可推进的时钟；生产就是当前时间） ----------
CREATE FUNCTION platform.staff_now() RETURNS timestamptz
  LANGUAGE sql VOLATILE SET search_path = pg_catalog
  AS $$ SELECT clock_timestamp() $$;
REVOKE EXECUTE ON FUNCTION platform.staff_now() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_now() TO starbuddy_authz;

-- ---------- pgcrypto 包装（扩展装在 0009 选定的 schema 里，名字在执行时才知道） ----------
DO $$
DECLARE v_schema text;
BEGIN
  SELECT n.nspname INTO v_schema FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pgcrypto';
  EXECUTE format($f$CREATE FUNCTION platform.staff_crypt(p_password text, p_salt text) RETURNS text
                   LANGUAGE sql STABLE STRICT SET search_path = pg_catalog AS 'SELECT %I.crypt(p_password, p_salt)'$f$, v_schema);
  EXECUTE format($f$CREATE FUNCTION platform.staff_bf_salt() RETURNS text
                   LANGUAGE sql VOLATILE SET search_path = pg_catalog AS 'SELECT %I.gen_salt(''bf'', 12)'$f$, v_schema);
  EXECUTE format($f$CREATE FUNCTION platform.staff_hmac_sha1(p_data bytea, p_key bytea) RETURNS bytea
                   LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS 'SELECT %I.hmac(p_data, p_key, ''sha1'')'$f$, v_schema);
  EXECUTE format($f$CREATE FUNCTION platform.staff_random_bytes(p_n integer) RETURNS bytea
                   LANGUAGE sql VOLATILE STRICT SET search_path = pg_catalog AS 'SELECT %I.gen_random_bytes(p_n)'$f$, v_schema);
END $$;
REVOKE EXECUTE ON FUNCTION platform.staff_crypt(text, text), platform.staff_bf_salt(), platform.staff_hmac_sha1(bytea, bytea),
  platform.staff_random_bytes(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_crypt(text, text), platform.staff_bf_salt(), platform.staff_hmac_sha1(bytea, bytea),
  platform.staff_random_bytes(integer) TO starbuddy_authz;

-- RFC 6238 的 6 位码（HOTP 动态截断）；p_step 是 30 秒时间步
CREATE FUNCTION platform.staff_totp(p_secret bytea, p_step bigint) RETURNS text
  LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, platform
  AS $$
DECLARE
  h bytea := platform.staff_hmac_sha1(int8send(p_step), p_secret);
  o integer := get_byte(h, 19) & 15;
BEGIN
  -- 注意：Postgres 里 << 与 | 同级、从左到右结合，每一项都要单独加括号
  RETURN lpad(((((get_byte(h, o) & 127)::bigint << 24) | (get_byte(h, o + 1)::bigint << 16) | (get_byte(h, o + 2)::bigint << 8)
                | get_byte(h, o + 3)::bigint) % 1000000)::text, 6, '0');
END $$;
REVOKE EXECUTE ON FUNCTION platform.staff_totp(bytea, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_totp(bytea, bigint) TO starbuddy_authz;

-- ---------- 表 ----------
CREATE TABLE platform.staff_credential (
  account_id      uuid PRIMARY KEY REFERENCES platform.account (id),
  login           text NOT NULL UNIQUE CHECK (login ~ '^[a-z][a-z0-9_.-]{2,31}$'),  -- 登录名（列名不用 *_name：渗透夹具禁止 platform 出现姓名类列）
  password_hash   text NOT NULL CHECK (password_hash ~ '^\$2[abxy]\$[0-9]{2}\$'),
  totp_secret     bytea NOT NULL CHECK (octet_length(totp_secret) BETWEEN 16 AND 64),
  totp_last_step  bigint,                 -- 最近一次登录用掉的时间步：同一步（及更早）的码不能再用
  failed_count    integer NOT NULL DEFAULT 0 CHECK (failed_count BETWEEN 0 AND 5),
  last_failed_at  timestamptz,            -- 最近一次失败：超过 15 分钟没有失败，计次从头算
  locked_until    timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.staff_credential IS
  '审核台 staff 凭据（0020）。只有函数属主 starbuddy_authz 能读写；口令只存 bcrypt 哈希；TOTP 密钥只在库里用于核对，不出库（pr4 待人确认 #6）';

CREATE TABLE platform.staff_session (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash    bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),  -- sha256(令牌)；令牌本身不入库
  account_id    uuid NOT NULL REFERENCES platform.account (id),
  created_at    timestamptz NOT NULL,
  last_seen_at  timestamptz NOT NULL,
  expires_at    timestamptz NOT NULL,
  revoked_at    timestamptz,
  CONSTRAINT staff_session_times CHECK (last_seen_at >= created_at AND expires_at > created_at)
);
CREATE INDEX staff_session_account_idx ON platform.staff_session (account_id);
COMMENT ON TABLE platform.staff_session IS '审核台 staff 会话（0020）：绝对 12 小时、空闲 30 分钟；只有函数属主 starbuddy_authz 能读写';

-- 凭据只能属于 staff 账号
CREATE FUNCTION platform.guard_staff_credential() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id = NEW.account_id AND a.kind = 'staff') THEN
    RAISE EXCEPTION '只有 staff 账号能有审核台凭据' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.guard_staff_credential() FROM PUBLIC;
CREATE TRIGGER guard_staff_credential BEFORE INSERT OR UPDATE ON platform.staff_credential
  FOR EACH ROW EXECUTE FUNCTION platform.guard_staff_credential();

ALTER TABLE platform.staff_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.staff_credential FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.staff_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.staff_session FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的 starbuddy_authz（函数属主）能读写
REVOKE ALL ON platform.staff_credential, platform.staff_session FROM PUBLIC;
GRANT SELECT, UPDATE ON platform.staff_credential TO starbuddy_authz;
GRANT SELECT, INSERT, UPDATE ON platform.staff_session TO starbuddy_authz;

-- ---------- 登录：一个函数里核对凭据并建会话 ----------
CREATE FUNCTION platform.staff_login(p_login text, p_password text, p_code text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  c platform.staff_credential%ROWTYPE;
  v_now timestamptz := platform.staff_now();
  v_step bigint := floor(extract(epoch FROM v_now) / 30)::bigint;
  v_hit bigint;
  v_fails integer;
  v_ok boolean;
  v_token text;
BEGIN
  -- 格式不对：直接失败，不查凭据、不计次（避免借格式错误撞锁别人）
  IF p_login IS NULL OR p_login !~ '^[a-z][a-z0-9_.-]{2,31}$' OR p_password IS NULL OR octet_length(p_password) NOT BETWEEN 1 AND 72
     OR p_code IS NULL OR p_code !~ '^[0-9]{6}$' THEN
    RETURN NULL;
  END IF;
  -- 同一登录名的并发登录不排队占连接：拿不到行锁就直接失败（安全审查建议 1）
  BEGIN
    SELECT * INTO c FROM platform.staff_credential WHERE login = p_login FOR UPDATE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN
    RETURN NULL;
  END;
  IF c.account_id IS NULL THEN
    -- 没有这个人：也算一次 bcrypt，耗时与口令错时相近
    PERFORM platform.staff_crypt(p_password, '$2a$12$abcdefghijklmnopqrstuu');
    RETURN NULL;
  END IF;
  IF c.locked_until IS NOT NULL AND c.locked_until > v_now THEN
    PERFORM platform.staff_crypt(p_password, c.password_hash);
    RETURN NULL;
  END IF;
  -- 前后各容 1 步；同一步及更早的码用过就不能再用
  SELECT s INTO v_hit FROM generate_series(v_step - 1, v_step + 1) s
   WHERE s > coalesce(c.totp_last_step, -1) AND platform.staff_totp(c.totp_secret, s) = p_code
   ORDER BY s LIMIT 1;
  v_ok := platform.staff_crypt(p_password, c.password_hash) = c.password_hash
          AND v_hit IS NOT NULL
          AND EXISTS (SELECT 1 FROM platform.account a WHERE a.id = c.account_id AND a.kind = 'staff' AND a.status = 'active');
  IF NOT v_ok THEN
    -- 失败计次（函数返回 NULL 而不是抛错，计次才能提交）；只看最近 15 分钟；满 5 次锁 15 分钟并清零
    v_fails := CASE WHEN c.last_failed_at IS NOT NULL AND c.last_failed_at > v_now - interval '15 minutes' THEN c.failed_count ELSE 0 END + 1;
    UPDATE platform.staff_credential
       SET failed_count = CASE WHEN v_fails >= 5 THEN 0 ELSE v_fails END,
           locked_until = CASE WHEN v_fails >= 5 THEN v_now + interval '15 minutes' ELSE NULL END,
           last_failed_at = v_now,
           updated_at = v_now
     WHERE account_id = c.account_id;
    RETURN NULL;
  END IF;
  UPDATE platform.staff_credential SET failed_count = 0, last_failed_at = NULL, locked_until = NULL, totp_last_step = v_hit, updated_at = v_now
   WHERE account_id = c.account_id;
  -- 一个账号同时只有一个有效会话：新登录撤销旧会话（安全审查建议 6）
  UPDATE platform.staff_session SET revoked_at = v_now WHERE account_id = c.account_id AND revoked_at IS NULL;
  v_token := encode(platform.staff_random_bytes(32), 'hex');
  INSERT INTO platform.staff_session (token_hash, account_id, created_at, last_seen_at, expires_at)
  VALUES (sha256(convert_to(v_token, 'UTF8')), c.account_id, v_now, v_now, v_now + interval '12 hours');
  RETURN v_token;
END $$;
ALTER FUNCTION platform.staff_login(text, text, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.staff_login(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_login(text, text, text) TO starbuddy_review;

-- ---------- 解析会话：有效才返回账号与登录名，并刷新最近活动时间 ----------
CREATE FUNCTION platform.staff_session_resolve(p_token text) RETURNS TABLE (account_id uuid, login_name text)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_now timestamptz := platform.staff_now();
  v_id uuid;
BEGIN
  IF p_token IS NULL OR p_token !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;
  UPDATE platform.staff_session s SET last_seen_at = v_now
   WHERE s.token_hash = sha256(convert_to(p_token, 'UTF8'))
     AND s.revoked_at IS NULL AND s.expires_at > v_now AND s.last_seen_at > v_now - interval '30 minutes'
     AND EXISTS (SELECT 1 FROM platform.account a WHERE a.id = s.account_id AND a.kind = 'staff' AND a.status = 'active')
  RETURNING s.account_id INTO v_id;
  IF v_id IS NULL THEN
    RETURN;
  END IF;
  RETURN QUERY SELECT c.account_id, c.login FROM platform.staff_credential c WHERE c.account_id = v_id;
END $$;
ALTER FUNCTION platform.staff_session_resolve(text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.staff_session_resolve(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_session_resolve(text) TO starbuddy_review;

-- ---------- 退出：撤销本会话 ----------
CREATE FUNCTION platform.staff_logout(p_token text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_token IS NULL OR p_token !~ '^[0-9a-f]{64}$' THEN
    RETURN;
  END IF;
  UPDATE platform.staff_session SET revoked_at = platform.staff_now()
   WHERE token_hash = sha256(convert_to(p_token, 'UTF8')) AND revoked_at IS NULL;
END $$;
ALTER FUNCTION platform.staff_logout(text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.staff_logout(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.staff_logout(text) TO starbuddy_review;

-- ---------- 建立与重置凭据：只由人（迁移执行者）在库里调用，不授任何应用角色 ----------
-- 用法（staging，工程负责人执行；TOTP 密钥当面交给教研，pr4 待人确认 #4）：
--   执行者必须是超级用户或 BYPASSRLS 角色（两张表强制 RLS 且没有策略）；
--   先关掉本会话的语句日志，并用不写历史文件的 psql（HISTFILE=/dev/null），口令不进任何日志（安全审查 B-2）：
--     SET log_statement = 'none'; SET log_min_error_statement = 'panic'; SET log_min_duration_statement = -1;
--   密钥：SELECT encode(<pgcrypto schema>.gen_random_bytes(20), 'hex'); 交给验证器 App 时换成 base32；
--   SELECT platform.staff_enroll('<staff 账号>', 'login.name', '<12 位以上、72 字节以内的口令>', '\x<40 位十六进制>'::bytea);
CREATE FUNCTION platform.staff_enroll(p_account uuid, p_login text, p_password text, p_totp_secret bytea) RETURNS void
  LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_password IS NULL OR char_length(p_password) < 12 OR octet_length(p_password) > 72 THEN
    RAISE EXCEPTION '口令须至少 12 位、不超过 72 字节' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.staff_credential (account_id, login, password_hash, totp_secret)
  VALUES (p_account, p_login, platform.staff_crypt(p_password, platform.staff_bf_salt()), p_totp_secret);
END $$;
REVOKE EXECUTE ON FUNCTION platform.staff_enroll(uuid, text, text, bytea) FROM PUBLIC;

-- 重置口令与密钥：清除锁定与已用时间步，撤销该账号全部会话
CREATE FUNCTION platform.staff_reset(p_account uuid, p_password text, p_totp_secret bytea) RETURNS void
  LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_password IS NULL OR char_length(p_password) < 12 OR octet_length(p_password) > 72 THEN
    RAISE EXCEPTION '口令须至少 12 位、不超过 72 字节' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.staff_credential
     SET password_hash = platform.staff_crypt(p_password, platform.staff_bf_salt()), totp_secret = p_totp_secret,
         totp_last_step = NULL, failed_count = 0, last_failed_at = NULL, locked_until = NULL, updated_at = platform.staff_now()
   WHERE account_id = p_account;
  IF NOT FOUND THEN
    RAISE EXCEPTION '这个账号没有审核台凭据' USING ERRCODE = 'no_data_found';
  END IF;
  UPDATE platform.staff_session SET revoked_at = platform.staff_now() WHERE account_id = p_account AND revoked_at IS NULL;
END $$;
REVOKE EXECUTE ON FUNCTION platform.staff_reset(uuid, text, bytea) FROM PUBLIC;
