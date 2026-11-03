-- 0039 platform · 内容版本与题目准入的发布闸门（specs/release-gate-sprint4/spec.md 第 7 节、冲刺 4 段；第二部分 PR B）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。依赖 0003、0012、0018–0020（staff 账号）、0011 / 0019（地图版本）。
-- 请以单事务执行（psql -1）。只改 platform schema（ADR 02）；对 kge 只读（地图版本状态、采分点的 rubric_version）。
-- 要点：
--   1. 内容版本状态机（沿用 0008 guard_plan_config 的做法）：只能以草稿登记；草稿可追加能力视图与题目登记；已发布只能改为已撤下；
--      已撤下只读；新增 retired_at 与状态一致性 CHECK。状态只能经 platform.publish_content_version / retire_content_version 改；
--   2. 发布：要求 active staff（platform.review_current_staff，0023）与“最近一条结论是批准、摘要与当前一致”的审核记录（0018–0020 的签字与摘要做法）；
--      同前五段下新发布的 content_version 必须大于已发布过的最大值；有 KGE 骨架的学科（数学 aya、道法 zhixing）map_version_id 必填，
--      且所指地图版本在发布时刻是已发布（同学科由 0012 的外键保证）；语文等其他学科可空，填了也必须指向已发布版本；
--   3. 题目准入：升降只经 platform.change_admission(review_ids[])，方向只允许 source_unverified → pending_review → admitted 与任意 → rejected；
--      只有升为 admitted 须来源已核（来源记录编号、已知来源类型、授权 cleared）与学科审核等级 ≥ Q2，且授权状态必须在签字时显式给出；
--      已撤下版本下的题只允许改为 rejected；
--      题目新增来源编号、来源类型、授权状态三列（与阿鸦内容包格式 3 的 source_ref / source_type / license_status 同一套枚举，
--      adapters/aya/src/pack/admission.ts）；登记时来源编号与类型可作为登记元数据自报，授权状态一律记为 unverified（只能经准入签字写入）；
--      题目 INSERT 时 admission = 'admitted' 被拒（只能经准入函数升级）；
--   4. 审核与发布记录 platform.release_record：只追加，签字人是 staff 账号（platform 可以存账号引用；kge.content_review 不能，规则 13），
--      对 BFF 与其他应用角色都不可见；准入降级（自 admitted 变化）在执行记录上留“受影响题目与 rubric_version 清单”，
--      重算属于 KGE-04（learning.mastery.recompute.recompute_item），学习服务经 platform.admission_change_log 读取；
--   5. 收回 starbuddy_learning 对 status、published_at、admission、admitted_at（及新增的 retired_at、来源三列）的 UPDATE；保留登记草稿的 INSERT；
--   6. BFF 可见性：能力视图要所属版本已发布；题目要已准入且所属版本已发布。
-- 不在本迁移：版本记录导出与审核台界面（PR D）、阿鸦准入决定文件导出（PR E）、内容版本与考情方案的核对（只在审核台报告里记警告）。

-- 数据前提（审查建议 3）：新约束要求已撤下的版本有撤下时间、已准入的题来源已核；现有数据补不出这些值，遇到就以明确信息中止，由人处理
DO $$
DECLARE v_retired bigint; v_admitted bigint;
BEGIN
  SELECT count(*) INTO v_retired FROM platform.curriculum_version WHERE status = 'retired';
  SELECT count(*) INTO v_admitted FROM platform.item WHERE admission = 'admitted';
  IF v_retired > 0 THEN
    RAISE EXCEPTION '0039 中止：已有 % 个状态为 retired 的内容版本，没有撤下时间（retired_at）可补；请人先确认撤下时间再执行', v_retired
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF v_admitted > 0 THEN
    RAISE EXCEPTION '0039 中止：已有 % 道 admitted 的题没有来源记录（来源编号、来源类型、授权）；请人先按发布闸门重新核定准入再执行', v_admitted
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- 角色前提：登记方（learning）与签字方（review）不能是同一个登录角色（同 0019 对 kge / review 的断言）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles r WHERE NOT r.rolsuper
               AND pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER') AND pg_has_role(r.oid, 'starbuddy_review', 'MEMBER')) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_learning（登记）与 starbuddy_review（签字）';
  END IF;
END $$;

-- ---------- 学科规则（纯函数，审核台后端与测试也用） ----------
-- 有 KGE 骨架的学科：数学（aya）、道法（zhixing）；语文阶段 0 没有地图版本（spec 第 7 节、待人确认 #2）
CREATE FUNCTION platform.content_requires_map(p_subject platform.subject) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT p_subject IN ('aya', 'zhixing') $$;
-- 升为 admitted 所需的学科审核等级（legacy Q0–Q5 的序号）：常量 2（Q2）。阿鸦定为 ≥ Q2（spec 第 7 节）；
-- 其他学科的等级口径未定，暂按同一门槛（lessons 待人确认）；参数留着，学科口径定了再按学科区分
CREATE FUNCTION platform.admission_min_review_rank(p_subject platform.subject) RETURNS integer
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT 2 $$;
-- 准入方向：source_unverified → pending_review → admitted，任意（rejected 除外）→ rejected
CREATE FUNCTION platform.admission_transition_ok(p_from platform.admission, p_to platform.admission) RETURNS boolean
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$ SELECT (p_from = 'source_unverified' AND p_to = 'pending_review')
            OR (p_from = 'pending_review' AND p_to = 'admitted')
            OR (p_from <> 'rejected' AND p_to = 'rejected') $$;
REVOKE EXECUTE ON FUNCTION platform.content_requires_map(platform.subject), platform.admission_min_review_rank(platform.subject),
  platform.admission_transition_ok(platform.admission, platform.admission) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.content_requires_map(platform.subject), platform.admission_min_review_rank(platform.subject),
  platform.admission_transition_ok(platform.admission, platform.admission) TO starbuddy_authz, starbuddy_review, starbuddy_learning;

-- ---------- 内容版本：撤下时间与状态一致性 ----------
ALTER TABLE platform.curriculum_version
  ADD COLUMN retired_at timestamptz,
  DROP CONSTRAINT curriculum_version_check,
  ADD CONSTRAINT curriculum_version_status_times CHECK (
    (status = 'draft' AND published_at IS NULL AND retired_at IS NULL)
    OR (status = 'published' AND published_at IS NOT NULL AND retired_at IS NULL)
    -- 草稿也可以直接撤下（放弃），此时没有发布时间
    OR (status = 'retired' AND retired_at IS NOT NULL AND (published_at IS NULL OR retired_at >= published_at)));
COMMENT ON COLUMN platform.curriculum_version.retired_at IS '撤下时间：只由 platform.retire_content_version 写入（0039）';

-- ---------- 题目：来源三列（与阿鸦内容包格式 3 同一套枚举） ----------
ALTER TABLE platform.item
  ADD COLUMN source_ref text CHECK (source_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$'),
  ADD COLUMN source_type text NOT NULL DEFAULT 'unknown'
    CHECK (source_type IN ('unknown', 'self_authored', 'adapted', 'textbook', 'exam_paper', 'reference_book')),
  ADD COLUMN license_status text NOT NULL DEFAULT 'unverified' CHECK (license_status IN ('unverified', 'cleared', 'denied')),
  -- 已准入的题必须来源已核：有来源记录编号、已知来源类型、授权 cleared
  ADD CONSTRAINT item_admitted_source CHECK (admission <> 'admitted'
                                             OR (source_ref IS NOT NULL AND source_type <> 'unknown' AND license_status = 'cleared'));
COMMENT ON COLUMN platform.item.source_ref IS '来源记录编号（材料由法务或教研保管，不进库，spec 待人确认 #15）；准入升级时由 platform.change_admission 按审核记录写入（0039）';
COMMENT ON COLUMN platform.item.source_type IS '来源类型：unknown（未登记）、self_authored、adapted、textbook、exam_paper、reference_book（阿鸦内容包格式 3）';
COMMENT ON COLUMN platform.item.license_status IS '授权状态：unverified（待核）、cleared（已核）、denied（不得使用）';

-- ---------- 审核与发布记录：只追加，签字人是 staff 账号 ----------
-- line = content_version：action review（签字，结论批准或驳回，摘要为整个内容版本）、publish、retire；
-- line = admission：action review（对一道题“从当前准入改为目标准入”签字，摘要为签字时的题目登记）、apply（change_admission 执行，指回所依据的审核）
-- 记录的学科必须与内容版本一致（复合外键，审查可选项）
ALTER TABLE platform.curriculum_version ADD CONSTRAINT curriculum_version_id_subject_uq UNIQUE (id, subject);
CREATE TABLE platform.release_record (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  line                      text NOT NULL CHECK (line IN ('content_version', 'admission')),
  action                    text NOT NULL CHECK (action IN ('review', 'publish', 'retire', 'apply')),
  curriculum_version_id     uuid NOT NULL,
  subject                   platform.subject NOT NULL,
  item_id                   platform.skill_local_id,
  decision                  text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  digest                    text CHECK (digest ~ '^[0-9a-f]{64}$'),
  basis_code                text CHECK (basis_code ~ '^[a-z0-9_]{1,48}$'),
  from_admission            platform.admission,
  to_admission              platform.admission,
  review_level              text CHECK (review_level ~ '^Q[0-5]$'),
  source_ref                text CHECK (source_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$'),
  source_type               text CHECK (source_type IN ('unknown', 'self_authored', 'adapted', 'textbook', 'exam_paper', 'reference_book')),
  license_status            text CHECK (license_status IN ('unverified', 'cleared', 'denied')),
  applies_review_id         uuid UNIQUE REFERENCES platform.release_record (id),  -- 一条准入审核只能执行一次
  affected_rubric_versions  text[],  -- 准入降级（自 admitted 变化）时受影响的 rubric_version 清单（KGE-04 重算用）
  signer                    uuid NOT NULL REFERENCES platform.account (id),
  decided_at                timestamptz NOT NULL,
  FOREIGN KEY (subject, item_id) REFERENCES platform.item (subject, item_id),
  CONSTRAINT release_record_cv_subject FOREIGN KEY (curriculum_version_id, subject) REFERENCES platform.curriculum_version (id, subject),
  CONSTRAINT release_record_shape CHECK (
    CASE line
      WHEN 'content_version' THEN
        item_id IS NULL AND from_admission IS NULL AND to_admission IS NULL AND review_level IS NULL AND source_ref IS NULL
        AND source_type IS NULL AND license_status IS NULL AND applies_review_id IS NULL AND affected_rubric_versions IS NULL
        AND action IN ('review', 'publish', 'retire')
        AND (action = 'retire' OR digest IS NOT NULL)
        AND (action <> 'retire' OR basis_code IS NOT NULL)
        AND (action = 'review' OR decision = 'approved')
      ELSE
        item_id IS NOT NULL AND from_admission IS NOT NULL AND to_admission IS NOT NULL AND digest IS NOT NULL
        AND source_type IS NOT NULL AND license_status IS NOT NULL
        AND action IN ('review', 'apply')
        AND ((action = 'apply') = (applies_review_id IS NOT NULL))
        AND (action = 'review' OR decision = 'approved')
        AND (affected_rubric_versions IS NULL OR action = 'apply')
    END)
);
CREATE INDEX release_record_cv_idx ON platform.release_record (curriculum_version_id, line, action, decided_at);
CREATE INDEX release_record_item_idx ON platform.release_record (subject, item_id, action, decided_at) WHERE line = 'admission';
COMMENT ON TABLE platform.release_record IS
  '内容版本与题目准入的审核、发布、撤下、执行记录（0039）：只追加，只由 platform.sign_content_version / publish_content_version / '
  'retire_content_version / sign_admission / change_admission 写入；签字人取当前登录的 active staff，调用方不能传';
-- 只追加（0018 的触发器函数，连超级用户也拦）
CREATE TRIGGER reject_release_record_change BEFORE UPDATE OR DELETE ON platform.release_record
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_release_record_truncate BEFORE TRUNCATE ON platform.release_record
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();
ALTER TABLE platform.release_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.release_record FORCE ROW LEVEL SECURITY;
-- 没有任何策略、不授任何应用角色：只有 BYPASSRLS 的 starbuddy_authz（函数属主）能读写；BFF 看不到审核记录
GRANT SELECT, INSERT ON platform.release_record TO starbuddy_authz;
-- 个人层表清单（0035）：签字人是账号键，但只会是教研账号，按豁免登记（同 content_signoff）
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason)
VALUES ('platform', 'release_record', 'signer（只有教研账号）',
        'signer = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
        '内容版本与题目准入的审核与发布记录，签字人只含教研账号');

-- ---------- 保护触发器（SECURITY INVOKER：按写入方身份读版本状态） ----------
-- 内容版本：只能以草稿登记；状态、发布与撤下时间只能经函数（属主 authz）改；已发布只能撤下且其他字段不变；已撤下只读；非草稿不能删
CREATE FUNCTION platform.guard_curriculum_version() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' OR NEW.published_at IS NOT NULL OR NEW.retired_at IS NOT NULL THEN
      RAISE EXCEPTION '内容版本只能以草稿登记，发布只能经 platform.publish_content_version' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION '已发布或已撤下的内容版本不能删除' USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION '已撤下的内容版本只读' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.id <> OLD.id OR NEW.subject <> OLD.subject THEN
    RAISE EXCEPTION '内容版本的 ID 与学科不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status OR NEW.published_at IS DISTINCT FROM OLD.published_at
     OR NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
    IF current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '内容版本的状态只能经 platform.publish_content_version / retire_content_version 修改' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT ((OLD.status = 'draft' AND NEW.status IN ('published', 'retired')) OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
      RAISE EXCEPTION '不允许的内容版本状态变化：% → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF OLD.status = 'published' AND (to_jsonb(NEW) - 'status' - 'retired_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'retired_at') THEN
    RAISE EXCEPTION '已发布的内容版本只能撤下，不能改其他字段' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guard_curriculum_version BEFORE INSERT OR UPDATE OR DELETE ON platform.curriculum_version
  FOR EACH ROW EXECUTE FUNCTION platform.guard_curriculum_version();

-- 能力视图与题目登记：只能追加到草稿版本（对版本行加共享锁，与发布的 FOR UPDATE 互斥：发布期间的登记等发布提交后按已发布状态拒绝）；
-- 题目不能以 admitted 登记；准入与来源列只能经 change_admission（属主 authz）改，所属版本已撤下时也不能改；登记不能挪到别的版本
CREATE FUNCTION platform.guard_content_registration() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_status text;
  v_admission_only boolean := false;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id THEN
    RAISE EXCEPTION '登记不能挪到别的内容版本' USING ERRCODE = 'check_violation';
  END IF;
  SELECT v.status INTO v_status FROM platform.curriculum_version v
   WHERE v.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.curriculum_version_id ELSE NEW.curriculum_version_id END FOR SHARE;
  IF TG_TABLE_NAME = 'item' THEN
    IF TG_OP = 'INSERT' AND NEW.admission = 'admitted' THEN
      RAISE EXCEPTION '题目不能以 admitted 登记：准入升级只能经 platform.change_admission' USING ERRCODE = 'check_violation';
    END IF;
    -- 登记方自报的授权不算数（审查阻断 1）：来源编号与类型留作登记元数据，授权状态一律从“待核”开始，只能经准入签字写入
    IF TG_OP = 'INSERT' THEN
      NEW.license_status := 'unverified';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.admission IS DISTINCT FROM OLD.admission OR NEW.admitted_at IS DISTINCT FROM OLD.admitted_at
                             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref OR NEW.source_type IS DISTINCT FROM OLD.source_type
                             OR NEW.license_status IS DISTINCT FROM OLD.license_status) THEN
      IF current_user <> 'starbuddy_authz' THEN
        RAISE EXCEPTION '题目的准入与来源只能经 platform.change_admission 修改' USING ERRCODE = 'insufficient_privilege';
      END IF;
      v_admission_only := (to_jsonb(NEW) - 'admission' - 'admitted_at' - 'source_ref' - 'source_type' - 'license_status')
                          = (to_jsonb(OLD) - 'admission' - 'admitted_at' - 'source_ref' - 'source_type' - 'license_status');
    END IF;
  END IF;
  IF v_admission_only THEN
    -- 准入可以在草稿与已发布版本上变化（升级不追溯、降级追溯）；已撤下的版本只允许改为 rejected（止损，审查建议 6）
    IF v_status IS DISTINCT FROM 'draft' AND v_status IS DISTINCT FROM 'published' AND NEW.admission IS DISTINCT FROM 'rejected' THEN
      RAISE EXCEPTION '已撤下内容版本下的题目只读（只能改为 rejected）' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION '只能在草稿内容版本上登记或修改%（当前 %）', TG_TABLE_NAME, coalesce(v_status, 'missing') USING ERRCODE = 'check_violation';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER guard_content_registration BEFORE INSERT OR UPDATE OR DELETE ON platform.skill_node
  FOR EACH ROW EXECUTE FUNCTION platform.guard_content_registration();
CREATE TRIGGER guard_content_registration BEFORE INSERT OR UPDATE OR DELETE ON platform.item
  FOR EACH ROW EXECUTE FUNCTION platform.guard_content_registration();
REVOKE EXECUTE ON FUNCTION platform.guard_curriculum_version(), platform.guard_content_registration() FROM PUBLIC;

-- ---------- 摘要：只返回 SHA-256，不返回内容 ----------
-- 内容版本：六段、所指地图版本、能力视图登记、题目登记（题目 ID 与所挂能力点）。准入与来源走单独的准入审核线，不进版本摘要
CREATE FUNCTION platform.content_version_digest(p_cv uuid) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
  SELECT encode(sha256(convert_to(jsonb_build_object(
    'version', jsonb_build_object('region', v.region, 'exam_year', v.exam_year, 'grade', v.grade, 'textbook_version', v.textbook_version,
                                  'subject', v.subject, 'content_version', v.content_version, 'map_version_id', v.map_version_id),
    'skill_nodes', (SELECT coalesce(jsonb_agg(jsonb_build_object('local_id', n.local_id, 'parent_local_id', n.parent_local_id,
                                                                 'label_code', n.label_code) ORDER BY n.local_id), '[]')
                      FROM platform.skill_node n WHERE n.curriculum_version_id = v.id),
    'items', (SELECT coalesce(jsonb_agg(jsonb_build_object('item_id', i.item_id, 'skill_local_id', i.skill_local_id) ORDER BY i.item_id), '[]')
                FROM platform.item i WHERE i.curriculum_version_id = v.id)
  )::text, 'UTF8')), 'hex')
    FROM platform.curriculum_version v WHERE v.id = p_cv $$;
-- 题目准入：签字时与执行时的题目登记（含当前准入与来源），执行前重算比对
CREATE FUNCTION platform.item_admission_digest(p_subject platform.subject, p_item_id text) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT encode(sha256(convert_to(jsonb_build_object(
    'subject', i.subject, 'item_id', i.item_id, 'curriculum_version_id', i.curriculum_version_id, 'skill_local_id', i.skill_local_id,
    'admission', i.admission, 'source_ref', i.source_ref, 'source_type', i.source_type, 'license_status', i.license_status
  )::text, 'UTF8')), 'hex')
    FROM platform.item i WHERE i.subject = p_subject AND i.item_id = p_item_id $$;

-- ---------- 签字：内容版本 ----------
-- 签字人取当前登录的 active staff；调用方回传看到的摘要，与当前不符就拒绝（看到的就是签的）
CREATE FUNCTION platform.sign_content_version(p_cv uuid, p_decision text, p_expected_digest text, p_basis_code text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_subject platform.subject;
  v_digest text;
  v_id uuid;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 对版本行加共享锁：与发布的 FOR UPDATE 互斥
  SELECT v.subject INTO v_subject FROM platform.curriculum_version v WHERE v.id = p_cv AND v.status = 'draft' FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '只能对草稿内容版本签字' USING ERRCODE = 'check_violation';
  END IF;
  v_digest := platform.content_version_digest(p_cv);
  IF p_expected_digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '你看到的内容版本与当前不一致（摘要不符），请刷新后再签' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, decision, digest, basis_code, signer, decided_at)
  VALUES ('content_version', 'review', p_cv, v_subject, p_decision, v_digest, p_basis_code, v_staff, clock_timestamp())
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- ---------- 发布：内容版本 ----------
CREATE FUNCTION platform.publish_content_version(p_cv uuid) RETURNS timestamptz
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  c platform.curriculum_version%ROWTYPE;
  r platform.release_record%ROWTYPE;
  v_map_status text;
  v_max numeric[];
  v_digest text;
  v_now timestamptz := now();
BEGIN
  SELECT * INTO c FROM platform.curriculum_version WHERE id = p_cv FOR UPDATE;
  IF NOT FOUND OR c.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿内容版本' USING ERRCODE = 'check_violation';
  END IF;
  -- 同前五段的发布串行（双参数咨询锁）：两个草稿并发发布时，版本号比较不会交错
  PERFORM pg_advisory_xact_lock(hashtext('platform.publish_content_version'),
                                hashtext(concat_ws('|', c.region, c.exam_year, c.grade, c.textbook_version, c.subject)));
  -- 1. 地图版本：有骨架的学科必填；填了就必须在发布时刻是已发布（对地图版本行加共享锁，发布期间不能被撤下）
  IF c.map_version_id IS NULL THEN
    IF platform.content_requires_map(c.subject) THEN
      RAISE EXCEPTION '% 有知识地图骨架：发布内容版本必须指定已发布的地图版本', c.subject USING ERRCODE = 'check_violation';
    END IF;
  ELSE
    SELECT m.status INTO v_map_status FROM kge.map_version m WHERE m.id = c.map_version_id AND m.subject = c.subject FOR SHARE;
    IF v_map_status IS DISTINCT FROM 'published' THEN
      RAISE EXCEPTION '内容版本所指的地图版本不是已发布状态（%）', coalesce(v_map_status, 'missing') USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  -- 2. 同前五段下，版本号必须大于已发布过的最大值（含之后撤下的；从草稿直接撤下的不算）
  SELECT max(string_to_array(o.content_version, '.')::numeric[]) INTO v_max FROM platform.curriculum_version o
   WHERE o.region = c.region AND o.exam_year = c.exam_year AND o.grade = c.grade AND o.textbook_version = c.textbook_version
     AND o.subject = c.subject AND o.id <> c.id AND o.published_at IS NOT NULL;
  IF v_max IS NOT NULL AND string_to_array(c.content_version, '.')::numeric[] <= v_max THEN
    RAISE EXCEPTION '内容版本号 % 必须大于同前五段已发布过的最大版本', c.content_version USING ERRCODE = 'check_violation';
  END IF;
  -- 3. 最近一条审核结论必须是批准，且签字时的摘要与现在相同（签字后又追加了登记要重签）
  v_digest := platform.content_version_digest(p_cv);
  SELECT * INTO r FROM platform.release_record x
   WHERE x.line = 'content_version' AND x.curriculum_version_id = p_cv AND x.action = 'review'
   ORDER BY x.decided_at DESC, x.id DESC LIMIT 1;
  IF NOT FOUND OR r.decision <> 'approved' THEN
    RAISE EXCEPTION '内容版本没有批准的审核记录，拒绝发布' USING ERRCODE = 'check_violation';
  END IF;
  IF r.digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '签字后内容版本又被修改（摘要不符），请重新审核签字' USING ERRCODE = 'check_violation';
  END IF;
  UPDATE platform.curriculum_version SET status = 'published', published_at = v_now WHERE id = p_cv;
  INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, decision, digest, basis_code, signer, decided_at)
  VALUES ('content_version', 'publish', p_cv, c.subject, 'approved', v_digest, 'publish', v_staff, clock_timestamp());
  RETURN v_now;
END $$;

-- ---------- 撤下：内容版本（草稿或已发布 → 已撤下；必须写依据） ----------
-- 与 kge.retire_map_version 一致：一名 staff 写明依据即可撤下，不要求事前批准（撤下是止损动作；lessons 冲刺 1 待人确认 #6）
CREATE FUNCTION platform.retire_content_version(p_cv uuid, p_basis_code text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_subject platform.subject;
  v_ts timestamptz := clock_timestamp();
BEGIN
  IF p_basis_code IS NULL THEN
    RAISE EXCEPTION '撤下必须写依据' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.curriculum_version SET status = 'retired', retired_at = v_ts
   WHERE id = p_cv AND status IN ('draft', 'published') RETURNING subject INTO v_subject;
  IF NOT FOUND THEN
    RAISE EXCEPTION '只能撤下草稿或已发布的内容版本' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, decision, basis_code, signer, decided_at)
  VALUES ('content_version', 'retire', p_cv, v_subject, 'approved', p_basis_code, v_staff, v_ts);
END $$;

-- ---------- 准入目标的前提（签字与执行都核对） ----------
CREATE FUNCTION platform.check_admission_target(p_subject platform.subject, p_to platform.admission, p_review_level text,
                                                p_source_ref text, p_source_type text, p_license_status text) RETURNS void
  LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  -- 只有 admitted 要求来源已核；pending_review 只表示“进入学科审核”（与三科 register-content 一致，审查阻断 1）
  IF p_to = 'admitted'
     AND NOT (p_source_ref IS NOT NULL AND p_source_type IS DISTINCT FROM 'unknown' AND p_license_status IS NOT DISTINCT FROM 'cleared') THEN
    RAISE EXCEPTION '升为 % 须来源已核：来源记录编号、已知来源类型、授权 cleared', p_to USING ERRCODE = 'check_violation';
  END IF;
  IF p_to = 'admitted'
     AND (p_review_level IS NULL OR substr(p_review_level, 2)::integer < platform.admission_min_review_rank(p_subject)) THEN
    RAISE EXCEPTION '升为 admitted 须学科审核等级不低于 Q%', platform.admission_min_review_rank(p_subject) USING ERRCODE = 'check_violation';
  END IF;
END $$;
REVOKE EXECUTE ON FUNCTION platform.check_admission_target(platform.subject, platform.admission, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.check_admission_target(platform.subject, platform.admission, text, text, text, text) TO starbuddy_authz;

-- ---------- 签字：题目准入 ----------
-- 一条记录 = 一名 staff 对“某题从当前准入改为目标准入”的结论；来源三列为空时沿用题目当前值；摘要是签字时的题目登记
CREATE FUNCTION platform.sign_admission(p_subject platform.subject, p_item_id text, p_to platform.admission, p_decision text,
                                        p_review_level text, p_source_ref text, p_source_type text, p_license_status text,
                                        p_expected_digest text, p_basis_code text) RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  i platform.item%ROWTYPE;
  v_cv_status text;
  v_digest text;
  v_ref text;
  v_type text;
  v_license text;
  v_id uuid;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_to IS NULL THEN
    RAISE EXCEPTION '必须写目标准入状态' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO i FROM platform.item x WHERE x.subject = p_subject AND x.item_id = p_item_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '题目不存在' USING ERRCODE = 'no_data_found';
  END IF;
  SELECT v.status INTO v_cv_status FROM platform.curriculum_version v WHERE v.id = i.curriculum_version_id;
  IF v_cv_status = 'retired' AND p_to <> 'rejected' THEN
    RAISE EXCEPTION '已撤下内容版本下的题目只读（只能改为 rejected）' USING ERRCODE = 'check_violation';
  END IF;
  v_digest := platform.item_admission_digest(p_subject, p_item_id);
  IF p_expected_digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '你看到的题目登记与当前不一致（摘要不符），请刷新后再签' USING ERRCODE = 'check_violation';
  END IF;
  -- 升为 admitted 时授权状态必须由签字人显式给出，不沿用题目上的值（审查阻断 1）
  IF p_decision = 'approved' AND p_to = 'admitted' AND p_license_status IS NULL THEN
    RAISE EXCEPTION '升为 admitted 须由签字人显式写明授权状态' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  v_ref := coalesce(p_source_ref, i.source_ref);
  v_type := coalesce(p_source_type, i.source_type);
  v_license := coalesce(p_license_status, i.license_status);
  IF p_decision = 'approved' THEN
    IF NOT platform.admission_transition_ok(i.admission, p_to) THEN
      RAISE EXCEPTION '不允许的准入变化：% → %（只允许 source_unverified → pending_review → admitted，任意 → rejected）', i.admission, p_to
        USING ERRCODE = 'check_violation';
    END IF;
    PERFORM platform.check_admission_target(p_subject, p_to, p_review_level, v_ref, v_type, v_license);
  END IF;
  INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, item_id, decision, digest, basis_code, from_admission,
                                       to_admission, review_level, source_ref, source_type, license_status, signer, decided_at)
  VALUES ('admission', 'review', i.curriculum_version_id, p_subject, p_item_id, p_decision, v_digest, p_basis_code, i.admission,
          p_to, p_review_level, v_ref, v_type, v_license, v_staff, clock_timestamp())
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- ---------- 执行准入变化：一批批准的审核记录，全部合格才生效（一条不合格整批回滚） ----------
-- 每条：是准入审核、结论批准、是该题最近一条审核、没有执行过、题目登记自签字后没变（摘要一致）、方向与前提仍合格。
-- 自 admitted 变化（降级）时在执行记录上留该题的 rubric_version 清单；升级不追溯、降级追溯（store.py 取更严），重算由 KGE-04 读执行记录做
CREATE FUNCTION platform.change_admission(p_review_ids uuid[]) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_review uuid;
  r platform.release_record%ROWTYPE;
  i platform.item%ROWTYPE;
  v_cv_status text;
  v_affected text[];
  v_n integer := 0;
BEGIN
  IF p_review_ids IS NULL OR cardinality(p_review_ids) = 0 OR array_position(p_review_ids, NULL) IS NOT NULL
     OR cardinality(p_review_ids) <> (SELECT count(DISTINCT x) FROM unnest(p_review_ids) x) THEN
    RAISE EXCEPTION '审核记录编号不能为空或重复' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  FOREACH v_review IN ARRAY p_review_ids LOOP
    SELECT * INTO r FROM platform.release_record x WHERE x.id = v_review AND x.line = 'admission' AND x.action = 'review';
    IF NOT FOUND THEN
      RAISE EXCEPTION '审核记录不存在或不是准入审核' USING ERRCODE = 'no_data_found';
    END IF;
    IF r.decision <> 'approved' THEN
      RAISE EXCEPTION '审核结论不是批准，不能执行' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO i FROM platform.item x WHERE x.subject = r.subject AND x.item_id = r.item_id FOR UPDATE;
    SELECT v.status INTO v_cv_status FROM platform.curriculum_version v WHERE v.id = i.curriculum_version_id;
    IF v_cv_status = 'retired' AND r.to_admission <> 'rejected' THEN
      RAISE EXCEPTION '已撤下内容版本下的题目只读（只能改为 rejected）' USING ERRCODE = 'check_violation';
    END IF;
    -- 只认该题最近一条审核结论（先批准后驳回、或之后另有审核，以最近一条为准）
    IF EXISTS (SELECT 1 FROM platform.release_record x
                WHERE x.line = 'admission' AND x.action = 'review' AND x.subject = r.subject AND x.item_id = r.item_id
                  AND (x.decided_at, x.id) > (r.decided_at, r.id)) THEN
      RAISE EXCEPTION '该题在这条审核之后还有新的审核结论，以最近一条为准' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM platform.release_record x WHERE x.applies_review_id = r.id) THEN
      RAISE EXCEPTION '这条审核记录已经执行过' USING ERRCODE = 'unique_violation';
    END IF;
    IF platform.item_admission_digest(r.subject, r.item_id) IS DISTINCT FROM r.digest THEN
      RAISE EXCEPTION '签字后题目登记又被修改（摘要不符），请重新审核签字' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT platform.admission_transition_ok(i.admission, r.to_admission) THEN
      RAISE EXCEPTION '不允许的准入变化：% → %', i.admission, r.to_admission USING ERRCODE = 'check_violation';
    END IF;
    PERFORM platform.check_admission_target(r.subject, r.to_admission, r.review_level, r.source_ref, r.source_type, r.license_status);
    v_affected := NULL;
    IF i.admission = 'admitted' THEN
      SELECT coalesce(array_agg(DISTINCT s.rubric_version::text ORDER BY s.rubric_version::text), '{}') INTO v_affected
        FROM kge.scoring_point s WHERE s.subject = r.subject AND s.item_id = r.item_id;
    END IF;
    UPDATE platform.item
       SET admission = r.to_admission, admitted_at = CASE WHEN r.to_admission = 'admitted' THEN clock_timestamp() END,
           source_ref = r.source_ref, source_type = r.source_type, license_status = r.license_status
     WHERE subject = r.subject AND item_id = r.item_id;
    INSERT INTO platform.release_record (line, action, curriculum_version_id, subject, item_id, decision, digest, basis_code, from_admission,
                                         to_admission, review_level, source_ref, source_type, license_status, applies_review_id,
                                         affected_rubric_versions, signer, decided_at)
    VALUES ('admission', 'apply', i.curriculum_version_id, r.subject, r.item_id, 'approved', r.digest, r.basis_code, i.admission,
            r.to_admission, r.review_level, r.source_ref, r.source_type, r.license_status, r.id, v_affected, v_staff, clock_timestamp());
    v_n := v_n + 1;
  END LOOP;
  RETURN v_n;
END $$;

-- ---------- 准入变化记录（只读）：给学习服务（KGE-04 按题重算）读执行记录，不含签字人 ----------
CREATE FUNCTION platform.admission_change_log(p_since timestamptz)
  RETURNS TABLE (applied_at timestamptz, subject platform.subject, item_id text, from_admission platform.admission,
                 to_admission platform.admission, affected_rubric_versions text[])
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT x.decided_at, x.subject, x.item_id::text, x.from_admission, x.to_admission, x.affected_rubric_versions
    FROM platform.release_record x
   WHERE x.line = 'admission' AND x.action = 'apply' AND x.decided_at >= coalesce(p_since, '-infinity')
   ORDER BY x.decided_at, x.id $$;

-- ---------- 函数属主与授权 ----------
ALTER FUNCTION platform.content_version_digest(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.item_admission_digest(platform.subject, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.sign_content_version(uuid, text, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.publish_content_version(uuid) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.retire_content_version(uuid, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.sign_admission(platform.subject, text, platform.admission, text, text, text, text, text, text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.change_admission(uuid[]) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.admission_change_log(timestamptz) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.content_version_digest(uuid), platform.item_admission_digest(platform.subject, text),
  platform.sign_content_version(uuid, text, text, text), platform.publish_content_version(uuid), platform.retire_content_version(uuid, text),
  platform.sign_admission(platform.subject, text, platform.admission, text, text, text, text, text, text, text),
  platform.change_admission(uuid[]), platform.admission_change_log(timestamptz) FROM PUBLIC;
-- 摘要：登记方（学习服务）与签字方都能算来展示与回传
GRANT EXECUTE ON FUNCTION platform.content_version_digest(uuid), platform.item_admission_digest(platform.subject, text)
  TO starbuddy_learning, starbuddy_review;
-- 签字、发布、撤下、准入执行：只授签字身份
GRANT EXECUTE ON FUNCTION platform.sign_content_version(uuid, text, text, text), platform.publish_content_version(uuid),
  platform.retire_content_version(uuid, text),
  platform.sign_admission(platform.subject, text, platform.admission, text, text, text, text, text, text, text),
  platform.change_admission(uuid[]) TO starbuddy_review;
GRANT EXECUTE ON FUNCTION platform.admission_change_log(timestamptz) TO starbuddy_learning;

-- 函数属主（authz）：读登记、改状态与准入（列级）
GRANT SELECT ON platform.skill_node TO starbuddy_authz;
GRANT UPDATE (status, published_at, retired_at) ON platform.curriculum_version TO starbuddy_authz;
GRANT UPDATE (admission, admitted_at, source_ref, source_type, license_status) ON platform.item TO starbuddy_authz;

-- 收回学习服务对状态、发布与撤下时间、准入与来源的 UPDATE；登记草稿的 INSERT 保留，其他列的修改只在草稿上（触发器）
REVOKE UPDATE ON platform.curriculum_version, platform.skill_node, platform.item FROM starbuddy_learning;
GRANT UPDATE (region, exam_year, grade, textbook_version, content_version, map_version_id) ON platform.curriculum_version TO starbuddy_learning;
GRANT UPDATE (parent_local_id, label_code) ON platform.skill_node TO starbuddy_learning;
GRANT UPDATE (skill_local_id) ON platform.item TO starbuddy_learning;

-- ---------- BFF 可见性：只看已发布版本下的能力视图，已准入且所属版本已发布的题目 ----------
DROP POLICY skill_node_read ON platform.skill_node;
CREATE POLICY skill_node_read ON platform.skill_node FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM platform.curriculum_version v WHERE v.id = curriculum_version_id AND v.status = 'published'));
DROP POLICY item_read ON platform.item;
CREATE POLICY item_read ON platform.item FOR SELECT TO starbuddy_bff
  USING (admission = 'admitted'
         AND EXISTS (SELECT 1 FROM platform.curriculum_version v WHERE v.id = curriculum_version_id AND v.status = 'published'));
