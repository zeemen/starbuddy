-- 0059 platform · 内部成员测试运营：问题反馈与退出测试（specs/launch-lt06-internal-test-ops 范围第 3、4 条，决定记录第 1–3 条）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、画像可见范围）。不在任何共享环境执行。
-- 依赖：0002（账号、档案、绑定、同意、can_read_student / is_self_student / is_linked_guardian）、0023（review_current_staff、
--       review_student_ref）、0029（auth_session、auth_now）、0035（个人层表清单、删除执行身份 starbuddy_eraser、erasure_request）。
--
-- 一、问题反馈 platform.feedback（决定记录第 1、2 条）
--   - 提交者：学生本人（来源 student_self_report）或该学生的有效绑定家长（来源 guardian_observation）；员工来源（staff）只留枚举，
--     本迁移没有任何写入路径（待人确认）。学生与家长的反馈分开存、不合并（规则 6）。
--   - 只有 BFF 能插入（列级 INSERT：id、状态、时间都只能取缺省值），而且只能以自己会话的账号为提交者（RLS WITH CHECK）；
--     退出测试的档案不能再写反馈。BFF 读不到、改不了、删不掉反馈（没有 SELECT / UPDATE / DELETE）。
--   - 审核台员工（starbuddy_review）只经两个 SECURITY DEFINER 函数读列表、改状态（new / triaged / closed），库里核对 active staff；
--     学生只给内测专用的不透明指针（platform.internal_test_ref，前缀 it-，与复核队列的 review_student_ref 不互通），不给 student_id
--     与提交者账号。其余角色（学习、洞察、知识、调度、报表）一律无权。没有单独的“值守”库角色：凡在职 staff 都能读与改状态（待人确认）。
--   - 固定分类 + 文本 1–500 字（不含控制字符与零宽、方向覆盖等格式字符）+ 可选的页面路由模板（只是模板，不含任何标识）。
--   - 自由文本只作反馈处理：不进判断账本、画像、任何关于学生的陈述（规则 11、12），不进日志、SLS 与指标（BFF 只记枚举）。
--   - 登记进个人层表清单（delete）：测试结束统一删除时随现有删除流程删掉（决定记录第 3 条）。
-- 二、退出测试 platform.internal_test_exit（决定记录第 3 条）
--   - 只能由该学生的有效绑定家长经 platform.exit_internal_test 发起：同一事务里撤回同意（每类最新一条是 granted 的，追加一条
--     revoked，版本沿用原来那条；同意记录只追加）、作废该学生账号的全部会话、写一行退出记录；重复调用不重复写（幂等），返回同样的
--     结果。返回需要作废 staging 白名单条目的家长账号 ID：该学生的有效绑定家长里，已经没有别的在测孩子（有效绑定、未退出、未删除）
--     的那些（决定记录（续）第 5 条；不返回手机号或其摘要）。BFF 按账号作废它在固定码登录成功时记下的条目（staging-otp.ts）。
--   - BFF 经 platform.internal_test_exited(学生) 判断档案是否已退出（无权读与不存在同为 false）：迁移执行之后无条件生效（不挂内测开关），
--     已退出的档案 BFF 拒绝一切新写入与触发模型处理的请求（services/bff/src/http/internal-test-routes.ts）；库里另在反馈的插入策略上同口径拒绝。
-- 三、库层判据（决定记录（续）第 4 条）
--   - platform.student_processing_allowed(学生)：不依赖调用者身份，档案存在、未删除（erased_at 为空）、未退出测试、而且有有效同意
--     （与 0029 学生模式同口径：隐私政策最新一条是 granted；不满 14 周岁另要监护人同意最新一条是 granted；同一时刻撤回优先）。
--     只授学习、洞察、调度三个服务角色：洞察服务的周报与指标选人、学习服务的写入与模型处理入口、掌握对账、判分抽检据此过滤或拒绝。
--   - 判分抽检 platform.review_sample_spot_checks（0023）在这里重建：只抽 student_processing_allowed 为真的学生（回滚恢复原定义）。
--   - 已退出的档案：库里拒绝再签同意（platform.consent_record 插入 granted）与给该学生账号签发会话（platform.auth_session 插入），
--     两个触发器，与“无权”同一个 insufficient_privilege。撤回同意（revoked）照常可写。
--   - 已有数据保留到测试结束统一删除（不在这里删）；退出记录登记为豁免表（与同意记录同口径留存，期限待合规确认）。
--   - 审核台员工经 platform.review_internal_test_exits 核对退出状态（不透明指针、退出时间、撤回的同意类型、是否仍有有效同意、
--     作废的会话数、删除请求状态、是否已删除）。
-- 请以单事务执行（psql -1）。

-- ---------- 问题反馈 ----------
CREATE TABLE platform.feedback (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  submitter_account_id  uuid NOT NULL REFERENCES platform.account (id),
  student_id            uuid REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  source                text NOT NULL CHECK (source IN ('student_self_report', 'guardian_observation', 'staff')),
  category              text NOT NULL CHECK (category IN ('malfunction', 'wrong_content', 'display', 'slow', 'login', 'suggestion', 'other')),
  -- 不收的字符：控制字符（换行、回车、制表除外）与零宽、方向覆盖、行段分隔等格式字符（U+200B–200F、2028–202E、2060–2069、FEFF），
  -- 用 chr() 拼出来，文件里不出现不可见字符
  body                  text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 500 AND btrim(body, E' \t\n\r') <> ''
                                             AND body !~ ('[' || chr(1) || '-' || chr(8) || chr(11) || chr(12) || chr(14) || '-' || chr(31)
                                                          || chr(127) || '-' || chr(159) || chr(8203) || '-' || chr(8207) || chr(8232) || '-'
                                                          || chr(8238) || chr(8288) || '-' || chr(8297) || chr(65279) || ']')),
  route_template        text CHECK (char_length(route_template) <= 120
                                    AND route_template ~ '^/[a-z]+(/([a-z][a-z-]*|\[[a-zA-Z]+\]))*$'),
  status                text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'triaged', 'closed')),
  status_changed_at     timestamptz,
  status_changed_by     uuid REFERENCES platform.account (id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT feedback_student_for_family CHECK ((source = 'staff') = (student_id IS NULL)),
  CONSTRAINT feedback_status_change CHECK ((status_changed_at IS NULL) = (status_changed_by IS NULL))
);
CREATE INDEX feedback_list_idx ON platform.feedback (created_at DESC, id DESC);
CREATE INDEX feedback_student_idx ON platform.feedback (student_id);
COMMENT ON TABLE platform.feedback IS
  '内部测试问题反馈（0059，LT-06）：固定分类 + 1–500 字文本 + 可选路由模板；来源分学生自述 / 家长观察 / 员工（规则 6）。'
  '只作反馈处理，不进判断账本、画像与任何关于学生的陈述（规则 11、12），不进日志与 SLS。BFF 只能插入自己的；审核台只经函数读与改状态';
COMMENT ON COLUMN platform.feedback.route_template IS '提交时所在页面的路由模板（如 /s/[studentId]/today），不含任何标识；可空';

-- ---------- 退出测试 ----------
CREATE TABLE platform.internal_test_exit (
  student_id        uuid PRIMARY KEY REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  requested_by      uuid NOT NULL REFERENCES platform.account (id),
  exited_at         timestamptz NOT NULL DEFAULT now(),
  consents_revoked  text[] NOT NULL DEFAULT '{}'
                    CHECK (consents_revoked <@ ARRAY['guardian_consent_under14', 'privacy_policy', 'voice_processing', 'pilot_participation']),
  sessions_revoked  integer NOT NULL DEFAULT 0 CHECK (sessions_revoked >= 0)
);
COMMENT ON TABLE platform.internal_test_exit IS
  '退出内部测试的档案（0059，LT-06）：只经 platform.exit_internal_test 写入（撤回同意 + 作废学生会话 + 本行），之后 BFF 拒绝该档案一切'
  '新写入与触发模型处理的请求；已有数据保留到测试结束统一删除（走 0035 删除流程）';

ALTER TABLE platform.feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.feedback FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.internal_test_exit ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.internal_test_exit FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.feedback, platform.internal_test_exit FROM PUBLIC;

-- ---------- 判断档案是否已退出（BFF 的统一判据与反馈插入策略共用） ----------
-- 无权读与不存在同为 false（不泄露存在性）；只看当前账号能读的档案（学生本人或有效绑定家长）
CREATE FUNCTION platform.internal_test_exited(p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT coalesce(platform.can_read_student(p_student), false)
               AND EXISTS (SELECT 1 FROM platform.internal_test_exit x WHERE x.student_id = p_student) $$;

-- BFF：列级 INSERT（状态与时间只能取缺省值）；提交者必须是当前账号，来源与账号关系一致，退出测试的档案不收
GRANT INSERT (submitter_account_id, student_id, source, category, body, route_template) ON platform.feedback TO starbuddy_bff;
CREATE POLICY feedback_submit ON platform.feedback FOR INSERT TO starbuddy_bff
  WITH CHECK (submitter_account_id = platform.current_account_id()
              AND status = 'new' AND status_changed_at IS NULL AND status_changed_by IS NULL
              AND ((source = 'student_self_report' AND platform.is_self_student(student_id))
                   OR (source = 'guardian_observation' AND platform.is_linked_guardian(student_id)))
              AND NOT platform.internal_test_exited(student_id));
-- 函数属主（BYPASSRLS）：审核台的读与改状态、退出记录的写入；删除执行身份：反馈随删除请求删除（只读学生键列）
GRANT SELECT ON platform.feedback TO starbuddy_authz;
GRANT UPDATE (status, status_changed_at, status_changed_by) ON platform.feedback TO starbuddy_authz;
GRANT SELECT, INSERT ON platform.internal_test_exit TO starbuddy_authz;
-- 函数属主 starbuddy_authz 对同意记录的 INSERT：只为 exit_internal_test 追加“撤回”（同意记录只追加，表上的 RLS 与 BFF 的写入策略不变）；
-- 只有这一个函数用到，db/tests/79zg 与权限快照固定这一项（回滚时收回）
GRANT INSERT ON platform.consent_record TO starbuddy_authz;
GRANT DELETE ON platform.feedback TO starbuddy_eraser;
GRANT SELECT (student_id) ON platform.feedback TO starbuddy_eraser;

-- ---------- 家长发起退出测试 ----------
-- 授权：当前账号必须是该学生的有效绑定家长（与“不存在”同一个 insufficient_privilege）。锁住档案行，同一学生的并发退出串行。
-- 返回：退出时间、这次是否新退出、撤回了哪些同意类型、需要作废白名单条目的家长账号 ID（排好序）：该学生的有效绑定家长里，
-- 已经没有别的在测孩子（另一个有效绑定、未退出、未删除的档案）的那些（决定记录（续）第 5 条；不返回手机号或其摘要）。
CREATE FUNCTION platform.exit_internal_test(p_student uuid)
  RETURNS TABLE (exit_at timestamptz, newly boolean, revoked_types text[], revoke_accounts uuid[])
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_me uuid := platform.current_account_id();
  v_account uuid;
  v_row platform.internal_test_exit;
  v_types text[];
  v_n integer := 0;
  v_accounts uuid[];
BEGIN
  IF NOT coalesce(platform.is_linked_guardian(p_student), false) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT sp.account_id INTO v_account FROM platform.student_profile sp WHERE sp.id = p_student FOR UPDATE;
  SELECT * INTO v_row FROM platform.internal_test_exit x WHERE x.student_id = p_student;
  IF NOT FOUND THEN
    -- 撤回同意：每类最新一条（同一时刻撤回优先，与 0029 同口径）是 granted 的，追加一条 revoked（版本沿用那一条）
    WITH latest AS (
      SELECT DISTINCT ON (cr.consent_type) cr.consent_type AS t, cr.document_version AS v, cr.decision AS d
        FROM platform.consent_record cr WHERE cr.student_id = p_student
       ORDER BY cr.consent_type, cr.decided_at DESC, (cr.decision = 'revoked') DESC),
    ins AS (
      INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
      SELECT p_student, v_me, l.t, l.v, 'revoked' FROM latest l WHERE l.d = 'granted'
      RETURNING consent_type)
    SELECT coalesce(array_agg(ins.consent_type ORDER BY ins.consent_type), '{}') INTO v_types FROM ins;
    -- 学生账号的会话一律作废（之后学生模式打不开：同意已撤回，0029 的学生模式要求有效同意）
    UPDATE platform.auth_session SET revoked_at = platform.auth_now(), revoked_reason = 'logout_all'
     WHERE account_id = v_account AND revoked_at IS NULL;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    INSERT INTO platform.internal_test_exit (student_id, requested_by, consents_revoked, sessions_revoked)
    VALUES (p_student, v_me, v_types, v_n) RETURNING * INTO v_row;
    newly := true;
  ELSE
    newly := false;
  END IF;
  SELECT coalesce(array_agg(DISTINCT l.guardian_account_id ORDER BY l.guardian_account_id), '{}') INTO v_accounts
    FROM platform.guardian_student_link l
   WHERE l.student_id = p_student AND l.status = 'active'
     AND NOT EXISTS (SELECT 1 FROM platform.guardian_student_link o JOIN platform.student_profile os ON os.id = o.student_id
                      WHERE o.guardian_account_id = l.guardian_account_id AND o.status = 'active' AND o.student_id <> p_student
                        AND os.erased_at IS NULL
                        AND NOT EXISTS (SELECT 1 FROM platform.internal_test_exit ox WHERE ox.student_id = o.student_id));
  exit_at := v_row.exited_at;
  revoked_types := v_row.consents_revoked;
  revoke_accounts := v_accounts;
  RETURN NEXT;
END $$;

-- ---------- 内测专用的学生不透明指针（审查建议：与复核队列的 review_student_ref 不互通，前缀与盐都不同；不能反查） ----------
CREATE FUNCTION platform.internal_test_ref(p_student uuid) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog
  AS $$ SELECT 'it-' || substr(encode(sha256(convert_to('starbuddy-internal-test:' || p_student::text, 'UTF8')), 'hex'), 1, 12) $$;
REVOKE EXECUTE ON FUNCTION platform.internal_test_ref(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.internal_test_ref(uuid) TO starbuddy_authz;

-- ---------- 库层判据：是否还能为这个学生写入新数据、做模型处理（决定记录（续）第 4 条） ----------
-- 不依赖调用者身份（批量任务、服务身份都能用）；档案存在、未删除、未退出测试，且有有效同意（与 0029 学生模式同口径）。
-- 空参数一律 invalid_parameter_value（与其他定时任务函数同口径，80 的定时任务函数矩阵核对）
CREATE FUNCTION platform.student_processing_allowed(p_student uuid) RETURNS boolean
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_student IS NULL THEN
    RAISE EXCEPTION 'student is required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN EXISTS (
  SELECT 1 FROM platform.student_profile sp
   WHERE sp.id = p_student AND sp.erased_at IS NULL
     AND NOT EXISTS (SELECT 1 FROM platform.internal_test_exit x WHERE x.student_id = sp.id)
     AND coalesce((SELECT cr.decision FROM platform.consent_record cr
                    WHERE cr.student_id = sp.id AND cr.consent_type = 'privacy_policy'
                    ORDER BY cr.decided_at DESC, (cr.decision = 'revoked') DESC LIMIT 1), 'none') = 'granted'
     AND ((sp.birth_year_month IS NOT NULL
           AND (sp.birth_year_month + interval '1 month' - interval '1 day' + interval '14 years')::date
               <= (platform.auth_now() AT TIME ZONE 'Asia/Shanghai')::date)
          OR coalesce((SELECT cr.decision FROM platform.consent_record cr
                        WHERE cr.student_id = sp.id AND cr.consent_type = 'guardian_consent_under14'
                        ORDER BY cr.decided_at DESC, (cr.decision = 'revoked') DESC LIMIT 1), 'none') = 'granted'));
END $$;

-- ---------- 已退出的档案：库里拒绝再签同意、拒绝给学生账号签发会话 ----------
CREATE FUNCTION platform.guard_exited_consent() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NEW.decision = 'granted' AND EXISTS (SELECT 1 FROM platform.internal_test_exit x WHERE x.student_id = NEW.student_id) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER consent_record_exited_guard BEFORE INSERT ON platform.consent_record
  FOR EACH ROW EXECUTE FUNCTION platform.guard_exited_consent();

CREATE FUNCTION platform.guard_exited_session() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM platform.student_profile sp JOIN platform.internal_test_exit x ON x.student_id = sp.id
              WHERE sp.account_id = NEW.account_id) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER auth_session_exited_guard BEFORE INSERT ON platform.auth_session
  FOR EACH ROW EXECUTE FUNCTION platform.guard_exited_session();

-- ---------- 判分抽检（0023）重建：已退出、已删除、没有有效同意的学生不再抽进复核队列 ----------
CREATE OR REPLACE FUNCTION platform.review_sample_spot_checks(p_subject platform.subject, p_since timestamptz, p_ratio numeric)
  RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  IF p_subject IS NULL OR p_since IS NULL OR p_ratio IS NULL OR p_ratio <= 0 OR p_ratio > 0.5 THEN
    RAISE EXCEPTION 'subject, since and a ratio in (0, 0.5] are required' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_since < now() - interval '31 days' OR p_since > now() THEN
    RAISE EXCEPTION 'since must be within the last 31 days' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
  SELECT g.subject, g.student_id, 'grade_result', g.id, ARRAY['spot_check']::platform.review_reason[]
    FROM platform.grade_result g
   WHERE g.subject = p_subject AND g.created_at >= p_since AND g.superseded_by IS NULL
     AND ('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(p_ratio * 4294967296)
     AND NOT EXISTS (SELECT 1 FROM platform.review_task t WHERE t.source_kind = 'grade_result' AND t.source_id = g.id)
     AND platform.student_processing_allowed(g.student_id)
  ON CONFLICT (source_kind, source_id) DO NOTHING;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

-- ---------- 审核台：反馈列表与改状态、退出状态（只授 starbuddy_review；库里核对 active staff） ----------
-- 列表：按状态筛，按 (创建时间, id) 倒序，(p_before, p_before_id) 作游标；学生只给不透明指针，不给 student_id 与提交者账号
CREATE FUNCTION platform.review_feedback_list(p_status text, p_limit integer, p_before timestamptz, p_before_id uuid)
  RETURNS TABLE (id uuid, source text, category text, body text, route_template text, status text, created_at timestamptz,
                 status_changed_at timestamptz, student_ref text)
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff();
BEGIN
  IF p_status IS NOT NULL AND p_status NOT IN ('new', 'triaged', 'closed') THEN
    RAISE EXCEPTION 'unknown status' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 200 THEN
    RAISE EXCEPTION 'limit must be 1..200' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
    SELECT f.id, f.source, f.category, f.body, f.route_template, f.status, f.created_at, f.status_changed_at,
           CASE WHEN f.student_id IS NULL THEN NULL ELSE platform.internal_test_ref(f.student_id) END
      FROM platform.feedback f
     WHERE (p_status IS NULL OR f.status = p_status)
       AND (p_before IS NULL OR (f.created_at, f.id) < (p_before, coalesce(p_before_id, 'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid)))
     ORDER BY f.created_at DESC, f.id DESC
     LIMIT p_limit;
END $$;

-- 改状态：new / triaged / closed 之间任意改（可重新打开）；与当前状态相同时不改、不记；记下改的人与时间
CREATE FUNCTION platform.review_feedback_set_status(p_feedback uuid, p_status text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); v_cur text;
BEGIN
  IF p_status IS NULL OR p_status NOT IN ('new', 'triaged', 'closed') THEN
    RAISE EXCEPTION 'unknown status' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT f.status INTO v_cur FROM platform.feedback f WHERE f.id = p_feedback FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '反馈不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF v_cur <> p_status THEN
    UPDATE platform.feedback SET status = p_status, status_changed_at = now(), status_changed_by = v_staff WHERE id = p_feedback;
  END IF;
  RETURN p_status;
END $$;

-- 退出状态：每个退出的档案一行（不透明指针）；consent_active = 仍有任一类同意的最新一条是 granted；删除请求取最近一条的状态
CREATE FUNCTION platform.review_internal_test_exits(p_limit integer)
  RETURNS TABLE (student_ref text, exited_at timestamptz, consents_revoked text[], consent_active boolean, sessions_revoked integer,
                 erasure_status text, erased boolean)
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff();
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
    RAISE EXCEPTION 'limit must be 1..500' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
    SELECT platform.internal_test_ref(x.student_id), x.exited_at, x.consents_revoked,
           EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (cr.consent_type) cr.decision FROM platform.consent_record cr
                                   WHERE cr.student_id = x.student_id
                                   ORDER BY cr.consent_type, cr.decided_at DESC, (cr.decision = 'revoked') DESC) l
                    WHERE l.decision = 'granted'),
           x.sessions_revoked,
           (SELECT r.status FROM platform.erasure_request r WHERE r.student_id = x.student_id ORDER BY r.requested_at DESC, r.id LIMIT 1),
           sp.erased_at IS NOT NULL
      FROM platform.internal_test_exit x JOIN platform.student_profile sp ON sp.id = x.student_id
     ORDER BY x.exited_at DESC, x.student_id
     LIMIT p_limit;
END $$;

ALTER FUNCTION platform.internal_test_exited(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.exit_internal_test(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.review_feedback_list(text, integer, timestamptz, uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.review_feedback_set_status(uuid, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.review_internal_test_exits(integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.student_processing_allowed(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.guard_exited_consent() OWNER TO starbuddy_authz;
ALTER FUNCTION platform.guard_exited_session() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.internal_test_exited(uuid), platform.exit_internal_test(uuid),
  platform.review_feedback_list(text, integer, timestamptz, uuid), platform.review_feedback_set_status(uuid, text),
  platform.review_internal_test_exits(integer), platform.student_processing_allowed(uuid),
  platform.guard_exited_consent(), platform.guard_exited_session() FROM PUBLIC;
-- 库层判据只授三个服务角色（学习服务写入与模型处理入口、掌握对账；洞察服务周报与指标选人；调度器判分抽检经函数内部调用，另授以便核对）
GRANT EXECUTE ON FUNCTION platform.student_processing_allowed(uuid) TO starbuddy_learning, starbuddy_insight, starbuddy_scheduler;
GRANT EXECUTE ON FUNCTION platform.internal_test_exited(uuid), platform.exit_internal_test(uuid) TO starbuddy_bff;
GRANT EXECUTE ON FUNCTION platform.review_feedback_list(text, integer, timestamptz, uuid), platform.review_feedback_set_status(uuid, text),
  platform.review_internal_test_exits(integer) TO starbuddy_review;

-- ---------- 个人层表清单（0035）：反馈随删除请求删除；退出记录与同意记录同口径豁免 ----------
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason, delete_order,
                                              erase_scope, export_topic, export_columns, export_filter) VALUES
  ('platform', 'feedback', 'student_id（员工来源没有学生键）', 'student_id = $1', 'delete', NULL, 66, 'facts', NULL, NULL, NULL),
  ('platform', 'internal_test_exit', 'student_id', 'student_id = $1', 'exempt',
   '退出测试记录：证明已撤回同意、停止新数据写入与模型处理（只有时间、发起家长、撤回的同意类型与作废的会话数）；与同意记录同口径留存，期限待合规确认',
   NULL, NULL, NULL, NULL, NULL);
