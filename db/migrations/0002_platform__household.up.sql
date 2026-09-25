-- 0002 platform · 家庭与身份：账号、联系方式、家庭、学生档案、家长关系、邀请、同意、选科
-- 家长访问只能通过 guardian_student_link（CLAUDE.md 规则 5）；禁止按手机号关联学生数据。
-- 学生档案只存最小字段：不存姓名、学校（spec 待人确认 #4）。
--
-- 写入路径（安全审查阻断 1–4 之后的设计）：
--   BFF 不能直接 INSERT account / account_contact / household / student_profile / guardian_student_link，
--   只能调用下面这些 SECURITY DEFINER 函数（属主 starbuddy_authz）：
--     signup_guardian(phone)                 注册家长账号（BFF 在短信验证码通过后调用）
--     lookup_accounts_by_phone(phone)        登录时按手机号找账号（BFF 在验证码通过后调用）
--     create_student(...)                    家长新建学生：同一事务里建学生账号、档案、创建者绑定
--     create_guardian_invite(student, ttl)   学生本人或已绑定家长签发一次性邀请，库内生成邀请码
--     redeem_guardian_invite(token)          另一位家长兑换邀请
--   任何函数都不接受外部传入的 account_id 作为“要绑定的学生”，所以无法把别人的账号建进自己的家庭。

CREATE TABLE platform.account (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        platform.account_kind NOT NULL,
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- 联系方式只用于登录；任何授权判断都不读它。v0.9 只有家长账号有联系方式。
CREATE TABLE platform.account_contact (
  account_id   uuid PRIMARY KEY REFERENCES platform.account (id) ON DELETE CASCADE,
  phone_e164   text NOT NULL CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  verified_at  timestamptz NOT NULL
);
CREATE INDEX account_contact_phone_idx ON platform.account_contact (phone_e164);
COMMENT ON COLUMN platform.account_contact.phone_e164 IS
  '只用于登录。存明文还是哈希 / 加密待 ADR 10（spec 待人确认 #3）；禁止用于任何数据关联（规则 5）';

CREATE TABLE platform.household (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_by_account   uuid NOT NULL REFERENCES platform.account (id),
  created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.student_profile (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id         uuid NOT NULL UNIQUE REFERENCES platform.account (id),
  household_id       uuid NOT NULL REFERENCES platform.household (id),
  grade              smallint NOT NULL CHECK (grade BETWEEN 7 AND 9),
  region             text NOT NULL CHECK (region ~ '^[a-z][a-z0-9\-]{1,31}$'),
  textbook_version   text CHECK (textbook_version ~ '^[a-z0-9][a-z0-9\-]{1,31}$'),
  birth_year_month   date CHECK (birth_year_month = date_trunc('month', birth_year_month)::date),
  created_at         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN platform.student_profile.birth_year_month IS '只存年月（当月 1 日），用于判断是否满 14 周岁（监护人同意）';

CREATE TABLE platform.guardian_student_link (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  guardian_account_id   uuid NOT NULL REFERENCES platform.account (id),
  student_id            uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  status                text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  granted_via           text NOT NULL CHECK (granted_via IN ('household_creator', 'invite')),
  created_at            timestamptz NOT NULL DEFAULT now(),
  revoked_at            timestamptz,
  CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE UNIQUE INDEX guardian_student_link_active_uq
  ON platform.guardian_student_link (guardian_account_id, student_id) WHERE status = 'active';
CREATE INDEX guardian_student_link_student_idx ON platform.guardian_student_link (student_id);

-- 邀请：邀请码由库内生成，只存 sha256，一次性、会过期（安全审查建议 4）。
CREATE TABLE platform.guardian_invite (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id           uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  token_sha256         bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
  created_by_account   uuid NOT NULL REFERENCES platform.account (id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  expires_at           timestamptz NOT NULL,
  used_at              timestamptz,
  used_by_account      uuid REFERENCES platform.account (id),
  CHECK ((used_at IS NULL) = (used_by_account IS NULL)),
  CHECK (expires_at > created_at - interval '1 second')
);

-- 同意记录只追加：撤回也是一条新记录（decision = revoked）。
CREATE TABLE platform.consent_record (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id            uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  guardian_account_id   uuid NOT NULL REFERENCES platform.account (id),
  consent_type          text NOT NULL CHECK (consent_type IN (
                          'guardian_consent_under14', 'privacy_policy', 'voice_processing', 'pilot_participation')),
  document_version      platform.versioned_name NOT NULL,
  decision              text NOT NULL CHECK (decision IN ('granted', 'revoked')),
  decided_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consent_record_student_idx ON platform.consent_record (student_id, consent_type, decided_at DESC);

CREATE TABLE platform.subject_enrollment (
  student_id    uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject       platform.subject NOT NULL,
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
  enrolled_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (student_id, subject)
);

-- ---------- 授权函数（属主 starbuddy_authz，BYPASSRLS，避免策略递归） ----------
GRANT SELECT ON platform.account, platform.account_contact, platform.household, platform.student_profile,
                platform.guardian_student_link, platform.guardian_invite TO starbuddy_authz;
GRANT INSERT ON platform.account, platform.account_contact, platform.household, platform.student_profile,
                platform.guardian_student_link, platform.guardian_invite TO starbuddy_authz;
GRANT UPDATE (used_at, used_by_account, expires_at) ON platform.guardian_invite TO starbuddy_authz;

-- 当前账号是有效的家长账号
CREATE FUNCTION platform.is_active_guardian() RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.account a
                       WHERE a.id = platform.current_account_id() AND a.kind = 'guardian' AND a.status = 'active') $$;

-- 学生本人（学生账号必须有效；安全审查建议 3：禁用账号不能再读）
CREATE FUNCTION platform.is_self_student(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.student_profile s
                       JOIN platform.account a ON a.id = s.account_id
                       WHERE s.id = p_student AND a.id = platform.current_account_id()
                         AND a.kind = 'student' AND a.status = 'active') $$;

-- 已绑定的有效家长
CREATE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT platform.is_active_guardian()
           AND EXISTS (SELECT 1 FROM platform.guardian_student_link l
                       WHERE l.student_id = p_student AND l.status = 'active'
                         AND l.guardian_account_id = platform.current_account_id()) $$;

CREATE FUNCTION platform.can_read_student(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT platform.is_self_student(p_student) OR platform.is_linked_guardian(p_student) $$;

CREATE FUNCTION platform.can_read_household(p_household uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT (platform.is_active_guardian() AND EXISTS (
                  SELECT 1 FROM platform.household h
                   WHERE h.id = p_household AND h.created_by_account = platform.current_account_id()))
            OR EXISTS (SELECT 1 FROM platform.student_profile s
                       WHERE s.household_id = p_household AND platform.can_read_student(s.id)) $$;

-- 注册家长账号。BFF 只在短信验证码通过后调用，所以同时写入 verified_at。
-- 不接受外部传入的 account_id（安全审查阻断 1）。
CREATE FUNCTION platform.signup_guardian(p_phone_e164 text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO platform.account (kind) VALUES ('guardian') RETURNING id INTO v_id;
  INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (v_id, p_phone_e164, now());
  RETURN v_id;
END $$;

-- 登录：按手机号找有效账号。只返回账号 ID 与类型，不返回任何学生数据。
CREATE FUNCTION platform.lookup_accounts_by_phone(p_phone_e164 text)
  RETURNS TABLE (account_id uuid, kind platform.account_kind)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT a.id, a.kind FROM platform.account_contact c JOIN platform.account a ON a.id = c.account_id
         WHERE c.phone_e164 = p_phone_e164 AND a.status = 'active' $$;

-- 家长新建学生：同一事务里建学生账号、档案与创建者绑定（安全审查阻断 2、3）。
-- p_household 为空时新建家庭；否则必须是当前家长创建的家庭。
CREATE FUNCTION platform.create_student(p_household uuid, p_grade smallint, p_region text,
                                        p_textbook_version text, p_birth_year_month date)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_me uuid := platform.current_account_id();
  v_household uuid := p_household;
  v_account uuid;
  v_student uuid;
BEGIN
  IF NOT platform.is_active_guardian() THEN
    RAISE EXCEPTION 'only an active guardian can create a student' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF v_household IS NULL THEN
    INSERT INTO platform.household (created_by_account) VALUES (v_me) RETURNING id INTO v_household;
  ELSIF NOT EXISTS (SELECT 1 FROM platform.household h WHERE h.id = v_household AND h.created_by_account = v_me) THEN
    RAISE EXCEPTION 'not the creator of this household' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO platform.account (kind) VALUES ('student') RETURNING id INTO v_account;
  INSERT INTO platform.student_profile (account_id, household_id, grade, region, textbook_version, birth_year_month)
  VALUES (v_account, v_household, p_grade, p_region, p_textbook_version, p_birth_year_month)
  RETURNING id INTO v_student;
  INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
  VALUES (v_me, v_student, 'household_creator');
  RETURN v_student;
END $$;

-- 签发邀请：学生本人或已绑定的有效家长。邀请码由库内生成并返回，只存哈希（安全审查建议 4）。
CREATE FUNCTION platform.create_guardian_invite(p_student uuid, p_ttl interval DEFAULT interval '72 hours')
  RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_token text;
BEGIN
  IF NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not allowed to invite for this student' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_ttl IS NULL OR p_ttl <= interval '0' OR p_ttl > interval '7 days' THEN
    RAISE EXCEPTION 'invalid invite ttl' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 两个随机 UUID（各 122 位随机）+ 时钟，经 sha256 得到 64 位十六进制邀请码
  v_token := encode(sha256(convert_to(gen_random_uuid()::text || gen_random_uuid()::text || clock_timestamp()::text, 'UTF8')), 'hex');
  INSERT INTO platform.guardian_invite (student_id, token_sha256, created_by_account, expires_at)
  VALUES (p_student, sha256(convert_to(v_token, 'UTF8')), platform.current_account_id(), now() + p_ttl);
  RETURN v_token;
END $$;

-- 兑换邀请：当前账号必须是有效家长，且不是签发人；邀请未过期、未使用；
-- 签发人在兑换时仍然必须是学生本人或有效绑定的家长（安全审查阻断 4）。
CREATE FUNCTION platform.redeem_guardian_invite(p_token text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_invite platform.guardian_invite%ROWTYPE;
  v_me uuid := platform.current_account_id();
  v_link uuid;
BEGIN
  IF NOT platform.is_active_guardian() THEN
    RAISE EXCEPTION 'only an active guardian account can redeem' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO v_invite FROM platform.guardian_invite
   WHERE token_sha256 = sha256(convert_to(p_token, 'UTF8')) FOR UPDATE;
  IF NOT FOUND OR v_invite.used_at IS NOT NULL OR v_invite.expires_at <= now()
     OR v_invite.created_by_account = v_me
     OR NOT (
       EXISTS (SELECT 1 FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
                WHERE s.id = v_invite.student_id AND a.id = v_invite.created_by_account AND a.status = 'active')
       OR EXISTS (SELECT 1 FROM platform.guardian_student_link l JOIN platform.account a ON a.id = l.guardian_account_id
                   WHERE l.student_id = v_invite.student_id AND l.guardian_account_id = v_invite.created_by_account
                     AND l.status = 'active' AND a.status = 'active')) THEN
    RAISE EXCEPTION 'invite invalid, used or expired' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.guardian_invite SET used_at = now(), used_by_account = v_me WHERE id = v_invite.id;
  INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
  VALUES (v_me, v_invite.student_id, 'invite')
  ON CONFLICT (guardian_account_id, student_id) WHERE status = 'active' DO NOTHING
  RETURNING id INTO v_link;
  RETURN v_link;
END $$;

-- 解除绑定：只允许 active → revoked，revoked_at 由库写 now()，其他列不能改（安全审查建议 5）；
-- 同时作废被解除的家长为这个学生签发、尚未使用的邀请（安全审查阻断 4）。
CREATE FUNCTION platform.guard_link_update() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF OLD.status <> 'active' OR NEW.status <> 'revoked'
     OR NEW.guardian_account_id <> OLD.guardian_account_id OR NEW.student_id <> OLD.student_id
     OR NEW.granted_via <> OLD.granted_via OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'a link can only be revoked' USING ERRCODE = 'check_violation';
  END IF;
  NEW.revoked_at := now();
  UPDATE platform.guardian_invite SET expires_at = now()
   WHERE student_id = OLD.student_id AND created_by_account = OLD.guardian_account_id
     AND used_at IS NULL AND expires_at > now();
  RETURN NEW;
END $$;
CREATE TRIGGER guard_link_update BEFORE UPDATE ON platform.guardian_student_link
  FOR EACH ROW EXECUTE FUNCTION platform.guard_link_update();

ALTER FUNCTION platform.is_active_guardian() OWNER TO starbuddy_authz;
ALTER FUNCTION platform.is_self_student(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.is_linked_guardian(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.can_read_student(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.can_read_household(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.signup_guardian(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.lookup_accounts_by_phone(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.create_student(uuid, smallint, text, text, date) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.create_guardian_invite(uuid, interval) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.redeem_guardian_invite(text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.guard_link_update() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.is_active_guardian(), platform.is_self_student(uuid),
  platform.is_linked_guardian(uuid), platform.can_read_student(uuid), platform.can_read_household(uuid),
  platform.signup_guardian(text), platform.lookup_accounts_by_phone(text),
  platform.create_student(uuid, smallint, text, text, date), platform.create_guardian_invite(uuid, interval),
  platform.redeem_guardian_invite(text), platform.guard_link_update() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.is_active_guardian(), platform.is_self_student(uuid),
  platform.is_linked_guardian(uuid), platform.can_read_student(uuid), platform.can_read_household(uuid),
  platform.signup_guardian(text), platform.lookup_accounts_by_phone(text),
  platform.create_student(uuid, smallint, text, text, date), platform.create_guardian_invite(uuid, interval),
  platform.redeem_guardian_invite(text) TO starbuddy_bff;

-- ---------- 表权限与 RLS ----------
-- BFF 对账号、联系方式、家庭、档案、绑定都没有 INSERT；联系方式与邀请没有 SELECT。
GRANT SELECT ON platform.account, platform.household, platform.student_profile,
  platform.guardian_student_link, platform.consent_record, platform.subject_enrollment TO starbuddy_bff;
GRANT INSERT ON platform.consent_record, platform.subject_enrollment TO starbuddy_bff;
GRANT UPDATE (status) ON platform.guardian_student_link TO starbuddy_bff;
GRANT SELECT ON platform.student_profile, platform.subject_enrollment TO starbuddy_learning;
-- starbuddy_learning 不授予 account / account_contact：学习服务不接触账号与联系方式。

ALTER TABLE platform.account ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.account FORCE ROW LEVEL SECURITY;
CREATE POLICY account_self_read ON platform.account FOR SELECT TO starbuddy_bff
  USING (id = platform.current_account_id());

ALTER TABLE platform.account_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.account_contact FORCE ROW LEVEL SECURITY;
-- 没有给任何应用角色的策略：只能经 signup_guardian / lookup_accounts_by_phone 访问。

ALTER TABLE platform.household ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.household FORCE ROW LEVEL SECURITY;
CREATE POLICY household_read ON platform.household FOR SELECT TO starbuddy_bff
  USING (platform.can_read_household(id));

ALTER TABLE platform.student_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.student_profile FORCE ROW LEVEL SECURITY;
CREATE POLICY student_profile_read ON platform.student_profile FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(id));
CREATE POLICY student_profile_service_read ON platform.student_profile FOR SELECT TO starbuddy_learning USING (true);

ALTER TABLE platform.guardian_student_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.guardian_student_link FORCE ROW LEVEL SECURITY;
CREATE POLICY link_read ON platform.guardian_student_link FOR SELECT TO starbuddy_bff
  USING ((guardian_account_id = platform.current_account_id() AND platform.is_active_guardian())
         OR platform.is_self_student(student_id));
-- 家长可以解除自己的绑定；学生本人也可以解除某个家长的绑定（spec 待人确认 #7）。
CREATE POLICY link_revoke ON platform.guardian_student_link FOR UPDATE TO starbuddy_bff
  USING (status = 'active' AND ((guardian_account_id = platform.current_account_id() AND platform.is_active_guardian())
                                OR platform.is_self_student(student_id)))
  WITH CHECK (status = 'revoked');

ALTER TABLE platform.guardian_invite ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.guardian_invite FORCE ROW LEVEL SECURITY;
-- 没有给应用角色的策略：邀请表只能通过函数访问。

ALTER TABLE platform.consent_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.consent_record FORCE ROW LEVEL SECURITY;
CREATE POLICY consent_read ON platform.consent_record FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
-- 只有已绑定的有效家长能写同意记录（is_linked_guardian 要求账号是家长；安全审查阻断 2）
CREATE POLICY consent_write ON platform.consent_record FOR INSERT TO starbuddy_bff
  WITH CHECK (guardian_account_id = platform.current_account_id() AND platform.is_linked_guardian(student_id));

ALTER TABLE platform.subject_enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.subject_enrollment FORCE ROW LEVEL SECURITY;
CREATE POLICY enrollment_read ON platform.subject_enrollment FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff
  WITH CHECK (platform.can_read_student(student_id));
CREATE POLICY enrollment_service_read ON platform.subject_enrollment FOR SELECT TO starbuddy_learning USING (true);
