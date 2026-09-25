-- 0002 platform · 家庭与身份：账号、联系方式、家庭、学生档案、家长关系、邀请、同意、选科
-- 家长访问只能通过 guardian_student_link（CLAUDE.md 规则 5）；禁止按手机号关联学生数据。
-- 学生档案只存最小字段：不存姓名、学校（spec 待人确认 #4）。

CREATE TABLE platform.account (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        platform.account_kind NOT NULL,
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- 联系方式只给 BFF 登录用；任何授权判断都不读它。手机号允许重复（学生可能用家长的手机号）。
CREATE TABLE platform.account_contact (
  account_id   uuid PRIMARY KEY REFERENCES platform.account (id) ON DELETE CASCADE,
  phone_e164   text NOT NULL CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  verified_at  timestamptz
);
CREATE INDEX account_contact_phone_idx ON platform.account_contact (phone_e164);
COMMENT ON COLUMN platform.account_contact.phone_e164 IS '只用于登录。加密方式待 ADR 10；禁止用于任何数据关联（规则 5）';

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

-- 邀请：学生（或已绑定的家长）签发一次性邀请码，只存哈希。家长只能通过 redeem 函数兑换，
-- 不能直接插入 link（修正文砺 guardians 表 with check 不约束 student_id 的漏洞）。
CREATE TABLE platform.guardian_invite (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id           uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  token_sha256         bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
  created_by_account   uuid NOT NULL REFERENCES platform.account (id),
  expires_at           timestamptz NOT NULL,
  used_at              timestamptz,
  used_by_account      uuid REFERENCES platform.account (id),
  CHECK ((used_at IS NULL) = (used_by_account IS NULL))
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
GRANT SELECT ON platform.account, platform.student_profile, platform.guardian_student_link,
                platform.household, platform.guardian_invite TO starbuddy_authz;
GRANT INSERT ON platform.guardian_student_link, platform.guardian_invite TO starbuddy_authz;
GRANT UPDATE (used_at, used_by_account) ON platform.guardian_invite TO starbuddy_authz;

CREATE FUNCTION platform.is_self_student(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.student_profile s
                       WHERE s.id = p_student AND s.account_id = platform.current_account_id()) $$;

CREATE FUNCTION platform.is_linked_guardian(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.guardian_student_link l
                       WHERE l.student_id = p_student AND l.status = 'active'
                         AND l.guardian_account_id = platform.current_account_id()) $$;

CREATE FUNCTION platform.can_read_student(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$ SELECT platform.is_self_student(p_student) OR platform.is_linked_guardian(p_student) $$;

CREATE FUNCTION platform.can_read_household(p_household uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$ SELECT EXISTS (SELECT 1 FROM platform.household h
                       WHERE h.id = p_household AND h.created_by_account = platform.current_account_id())
            OR EXISTS (SELECT 1 FROM platform.student_profile s
                       WHERE s.household_id = p_household AND platform.can_read_student(s.id)) $$;

-- 签发邀请：只有学生本人或已绑定的家长可以。token 由 BFF 生成，这里只存 sha256。
CREATE FUNCTION platform.create_guardian_invite(p_student uuid, p_token text, p_ttl interval DEFAULT interval '72 hours')
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$
DECLARE v_id uuid;
BEGIN
  IF NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not allowed to invite for this student' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF length(p_token) < 16 OR p_ttl > interval '7 days' THEN
    RAISE EXCEPTION 'invalid invite parameters' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.guardian_invite (student_id, token_sha256, created_by_account, expires_at)
  VALUES (p_student, sha256(convert_to(p_token, 'UTF8')), platform.current_account_id(), now() + p_ttl)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- 兑换邀请：当前账号必须是家长；邀请未过期、未使用。
CREATE FUNCTION platform.redeem_guardian_invite(p_token text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform
  AS $$
DECLARE
  v_invite platform.guardian_invite%ROWTYPE;
  v_me uuid := platform.current_account_id();
  v_link uuid;
BEGIN
  IF v_me IS NULL OR NOT EXISTS (SELECT 1 FROM platform.account a
                                 WHERE a.id = v_me AND a.kind = 'guardian' AND a.status = 'active') THEN
    RAISE EXCEPTION 'only an active guardian account can redeem' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO v_invite FROM platform.guardian_invite
   WHERE token_sha256 = sha256(convert_to(p_token, 'UTF8')) FOR UPDATE;
  IF NOT FOUND OR v_invite.used_at IS NOT NULL OR v_invite.expires_at <= now() THEN
    RAISE EXCEPTION 'invite invalid, used or expired' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.guardian_invite SET used_at = now(), used_by_account = v_me WHERE id = v_invite.id;
  INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
  VALUES (v_me, v_invite.student_id, 'invite')
  ON CONFLICT (guardian_account_id, student_id) WHERE status = 'active' DO NOTHING
  RETURNING id INTO v_link;
  RETURN v_link;
END $$;

ALTER FUNCTION platform.is_self_student(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.is_linked_guardian(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.can_read_student(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.can_read_household(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.create_guardian_invite(uuid, text, interval) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.redeem_guardian_invite(text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.is_self_student(uuid), platform.is_linked_guardian(uuid),
  platform.can_read_student(uuid), platform.can_read_household(uuid),
  platform.create_guardian_invite(uuid, text, interval), platform.redeem_guardian_invite(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.is_self_student(uuid), platform.is_linked_guardian(uuid),
  platform.can_read_student(uuid), platform.can_read_household(uuid) TO starbuddy_bff;
GRANT EXECUTE ON FUNCTION platform.create_guardian_invite(uuid, text, interval),
  platform.redeem_guardian_invite(text) TO starbuddy_bff;

-- ---------- 表权限与 RLS ----------
GRANT SELECT, INSERT ON platform.account, platform.account_contact, platform.household,
  platform.student_profile, platform.guardian_student_link, platform.consent_record,
  platform.subject_enrollment TO starbuddy_bff;
GRANT UPDATE (status, revoked_at) ON platform.guardian_student_link TO starbuddy_bff;
GRANT SELECT ON platform.student_profile, platform.subject_enrollment TO starbuddy_learning;
-- starbuddy_learning 不授予 account_contact：学习服务不接触联系方式。

ALTER TABLE platform.account ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.account FORCE ROW LEVEL SECURITY;
CREATE POLICY account_self_read ON platform.account FOR SELECT TO starbuddy_bff
  USING (id = platform.current_account_id());
-- 注册在登录前发生，尚无 app.account_id；BFF 负责注册流程的校验。
CREATE POLICY account_signup ON platform.account FOR INSERT TO starbuddy_bff WITH CHECK (kind <> 'staff');

ALTER TABLE platform.account_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.account_contact FORCE ROW LEVEL SECURITY;
-- 登录前按手机号查账号，BFF 需要全表读；这张表不参与任何授权判断。
CREATE POLICY account_contact_login ON platform.account_contact FOR SELECT TO starbuddy_bff USING (true);
CREATE POLICY account_contact_signup ON platform.account_contact FOR INSERT TO starbuddy_bff WITH CHECK (true);

ALTER TABLE platform.household ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.household FORCE ROW LEVEL SECURITY;
CREATE POLICY household_read ON platform.household FOR SELECT TO starbuddy_bff
  USING (platform.can_read_household(id));
CREATE POLICY household_create ON platform.household FOR INSERT TO starbuddy_bff
  WITH CHECK (created_by_account = platform.current_account_id());

ALTER TABLE platform.student_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.student_profile FORCE ROW LEVEL SECURITY;
CREATE POLICY student_profile_read ON platform.student_profile FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(id));
-- 只有家庭创建者可以在自己的家庭里建学生档案
CREATE POLICY student_profile_create ON platform.student_profile FOR INSERT TO starbuddy_bff
  WITH CHECK (EXISTS (SELECT 1 FROM platform.household h
                      WHERE h.id = household_id AND h.created_by_account = platform.current_account_id()));
CREATE POLICY student_profile_service_read ON platform.student_profile FOR SELECT TO starbuddy_learning USING (true);

ALTER TABLE platform.guardian_student_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.guardian_student_link FORCE ROW LEVEL SECURITY;
CREATE POLICY link_read ON platform.guardian_student_link FOR SELECT TO starbuddy_bff
  USING (guardian_account_id = platform.current_account_id() OR platform.is_self_student(student_id));
-- 直接插入只允许“家庭创建者 × 本家庭学生”；其他绑定只能走 redeem_guardian_invite。
CREATE POLICY link_household_creator ON platform.guardian_student_link FOR INSERT TO starbuddy_bff
  WITH CHECK (granted_via = 'household_creator'
              AND status = 'active'
              AND guardian_account_id = platform.current_account_id()
              AND EXISTS (SELECT 1 FROM platform.student_profile s
                          JOIN platform.household h ON h.id = s.household_id
                          WHERE s.id = student_id AND h.created_by_account = platform.current_account_id()));
-- 家长可以解除自己的绑定；学生本人也可以解除某个家长的绑定。
CREATE POLICY link_revoke ON platform.guardian_student_link FOR UPDATE TO starbuddy_bff
  USING (status = 'active' AND (guardian_account_id = platform.current_account_id()
                                OR platform.is_self_student(student_id)))
  WITH CHECK (status = 'revoked');
CREATE POLICY link_authz ON platform.guardian_student_link FOR INSERT TO starbuddy_authz WITH CHECK (true);

ALTER TABLE platform.guardian_invite ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.guardian_invite FORCE ROW LEVEL SECURITY;
-- 没有给 bff 的策略：邀请表只能通过函数访问。

ALTER TABLE platform.consent_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.consent_record FORCE ROW LEVEL SECURITY;
CREATE POLICY consent_read ON platform.consent_record FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
CREATE POLICY consent_write ON platform.consent_record FOR INSERT TO starbuddy_bff
  WITH CHECK (guardian_account_id = platform.current_account_id() AND platform.is_linked_guardian(student_id));

ALTER TABLE platform.subject_enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.subject_enrollment FORCE ROW LEVEL SECURITY;
CREATE POLICY enrollment_read ON platform.subject_enrollment FOR SELECT TO starbuddy_bff
  USING (platform.can_read_student(student_id));
CREATE POLICY enrollment_write ON platform.subject_enrollment FOR INSERT TO starbuddy_bff
  WITH CHECK (platform.can_read_student(student_id));
CREATE POLICY enrollment_service_read ON platform.subject_enrollment FOR SELECT TO starbuddy_learning USING (true);
