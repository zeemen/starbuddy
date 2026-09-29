-- 0019 kge · 审核台 v0 的签字与发布规则（specs/release-gate-sprint4/spec.md 第 3–5 节、冲刺 1 PR 1）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。依赖 0011 与 0018（签字人表 platform.content_signoff / map_change_signoff）。请以单事务执行（psql -1）。
-- 要点：
--   1. 提交方不能自己签字：审核记录与变更单的结论只能经签字函数写入，签字人取当前登录的 active staff 账号（不能自报）；
--   2. 看到的就是发布的：签字时记下草稿的内容摘要，发布前重算，不同就拒绝（签字后又改过要重签）；
--   3. 发布与撤下只能由签字身份（starbuddy_review 且当前账号是 active staff）经函数执行；知识服务只能提交；
--   4. 结构性变更两名不同的 staff 签字；版本号严格递增，含结构性变更时次版本号或主版本号必须增加；
--   5. 已发布版本上新增的题目标签、时政，批准前必须有对应的批准审核记录。
-- 默认（spec 待人确认 #4、#5）：审核人改为 staff 账号，自报工号列保留为可空的展示字段；发布前批准记录在库层强制。
-- 签字的 staff 账号记在 platform 的签字人表（0018），kge 本身不存任何账号引用（规则 13，80_attack_surface 断言）。

GRANT USAGE ON SCHEMA kge TO starbuddy_review;

-- ---------- 当前账号是不是 active staff ----------
CREATE FUNCTION kge.current_staff() RETURNS uuid
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v uuid := platform.current_account_id();
BEGIN
  IF v IS NULL OR NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id = v AND a.kind = 'staff' AND a.status = 'active') THEN
    RAISE EXCEPTION 'only an active staff account can sign or publish' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v;
END $$;
ALTER FUNCTION kge.current_staff() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.current_staff() FROM PUBLIC;

-- ---------- 内容摘要：草稿版本的骨架与原子按固定顺序序列化后取 SHA-256（十六进制） ----------
-- 只返回摘要，不返回内容；审核台后端（kge）与签字身份（review）都可以算来展示
GRANT SELECT ON kge.id_migration, kge.item_family, kge.map_change, kge.content_review TO starbuddy_authz;
CREATE FUNCTION kge.map_version_digest(p_version uuid) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
  SELECT encode(sha256(convert_to(jsonb_build_object(
    'version', (SELECT jsonb_build_object('subject', v.subject, 'version', v.version, 'standard_year', v.standard_year)
                  FROM kge.map_version v WHERE v.id = p_version),
    'nodes', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'map_version_id' ORDER BY x.id), '[]') FROM kge.knowledge_node x WHERE x.map_version_id = p_version),
    'edges', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'id' - 'map_version_id' ORDER BY x.from_id, x.to_map_version_id, x.to_id, x.type), '[]')
                FROM kge.knowledge_edge x WHERE x.map_version_id = p_version),
    'textbook', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'id' - 'map_version_id' ORDER BY x.node_id, x.edition, x.volume, x.page_from, x.id), '[]')
                   FROM kge.textbook_projection x WHERE x.map_version_id = p_version),
    'region', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'id' - 'map_version_id' ORDER BY x.node_id, x.region, x.exam_year), '[]')
                 FROM kge.region_projection x WHERE x.map_version_id = p_version),
    'skill_links', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'map_version_id' ORDER BY x.node_id, x.subject, x.local_id), '[]')
                      FROM kge.skill_link x WHERE x.map_version_id = p_version),
    'id_migrations', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'id' - 'map_version_id' - 'created_at' ORDER BY x.old_id, x.new_id), '[]')
                        FROM kge.id_migration x WHERE x.map_version_id = p_version),
    'item_families', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'map_version_id' ORDER BY x.code), '[]') FROM kge.item_family x WHERE x.map_version_id = p_version),
    'item_tags', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'map_version_id' ORDER BY x.subject, x.item_id), '[]') FROM kge.item_tag x WHERE x.map_version_id = p_version),
    'scoring_points', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'id' - 'map_version_id' ORDER BY x.subject, x.item_id, x.rubric_version, x.point_id), '[]')
                         FROM kge.scoring_point x WHERE x.map_version_id = p_version),
    'current_affairs', (SELECT coalesce(jsonb_agg(to_jsonb(x) - 'map_version_id' ORDER BY x.id), '[]') FROM kge.current_affair x WHERE x.map_version_id = p_version)
  )::text, 'UTF8')), 'hex') $$;
ALTER FUNCTION kge.map_version_digest(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.map_version_digest(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.map_version_digest(uuid) TO starbuddy_kge, starbuddy_review;

-- 单个原子的摘要（签字对象是已发布版本上的题目标签、时政时用）：不含审核状态列
CREATE FUNCTION kge.row_digest(p jsonb) RETURNS text LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT encode(sha256(convert_to(p::text, 'UTF8')), 'hex') $$;
REVOKE EXECUTE ON FUNCTION kge.row_digest(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.row_digest(jsonb) TO starbuddy_kge, starbuddy_authz;

-- ---------- 审核记录：只追加；结论只能经签字函数写入 ----------
ALTER TABLE kge.content_review
  ADD COLUMN digest text CHECK (digest ~ '^[0-9a-f]{64}$'),
  ADD COLUMN basis_code text CHECK (basis_code ~ '^[a-z0-9_]{1,48}$'),
  ADD COLUMN action text NOT NULL DEFAULT 'review' CHECK (action IN ('review', 'retire')),
  DROP CONSTRAINT content_review_decided,
  -- 有结论就必须有时间与摘要（签字人在 platform.content_signoff）；自报工号 reviewer 只作展示，不再是身份依据（spec 待人确认 #4）
  ADD CONSTRAINT content_review_decided CHECK ((decision = 'pending') = (decided_at IS NULL)
                                               AND (decision = 'pending' OR action = 'retire' OR digest IS NOT NULL));
COMMENT ON COLUMN kge.content_review.reviewer IS '自报工号，仅作展示（0019 起签字人以 platform.content_signoff 为准）';
COMMENT ON COLUMN kge.content_review.digest IS '签字时对象的内容摘要（kge.map_version_digest 或 kge.row_digest）；发布前重算比对';

CREATE FUNCTION kge.reject_content_review_change() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION 'content_review is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE EXECUTE ON FUNCTION kge.reject_content_review_change() FROM PUBLIC;
CREATE TRIGGER reject_content_review_change BEFORE UPDATE OR DELETE ON kge.content_review
  FOR EACH ROW EXECUTE FUNCTION kge.reject_content_review_change();
CREATE TRIGGER reject_content_review_truncate BEFORE TRUNCATE ON kge.content_review
  FOR EACH STATEMENT EXECUTE FUNCTION kge.reject_content_review_change();

REVOKE UPDATE ON kge.content_review FROM starbuddy_kge;
DROP POLICY content_review_kge ON kge.content_review;
-- 知识服务（审核台后端）只能读、提交“待审”记录；结论由签字函数（属主 authz，BYPASSRLS）写
CREATE POLICY content_review_kge_read ON kge.content_review FOR SELECT TO starbuddy_kge USING (true);
CREATE POLICY content_review_kge_submit ON kge.content_review FOR INSERT TO starbuddy_kge
  WITH CHECK (decision = 'pending' AND digest IS NULL AND action = 'review');
GRANT INSERT ON kge.content_review TO starbuddy_authz;

-- ---------- 变更单：签字人改为 staff 账号；状态只能经签字函数推进 ----------
-- 两人确认改由签字人表判断（自报工号不再算签字）：批准与应用时至少两个不同的 staff 账号签了 approved（守卫触发器）
ALTER TABLE kge.map_change DROP CONSTRAINT map_change_two_reviewers;
COMMENT ON COLUMN kge.map_change.reviewers IS '自报工号，仅作展示（0019 起签字人以 platform.map_change_signoff 为准）';

-- 变更单内容摘要（不含状态与展示用的自报工号）：签字时记下，批准时只数摘要与当前相同的签字
CREATE FUNCTION kge.map_change_digest(c kge.map_change) RETURNS text LANGUAGE sql STABLE SET search_path = pg_catalog, kge, pg_temp
  AS $$ SELECT kge.row_digest(to_jsonb(c) - 'status' - 'reviewers' - 'created_at') $$;
REVOKE EXECUTE ON FUNCTION kge.map_change_digest(kge.map_change) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.map_change_digest(kge.map_change) TO starbuddy_authz;

CREATE FUNCTION kge.guard_map_change() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'proposed' THEN
      RAISE EXCEPTION '变更单只能以“待审”新建，签字只能经 kge.sign_map_change' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '变更单的状态只能经签字与发布函数修改' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- 结构性变更两人确认（KGE 第五章）：批准与应用时至少两名不同的 staff 签了 approved
    IF NEW.status = 'approved'
       AND (SELECT count(DISTINCT s.account_id) FROM platform.map_change_signoff s
             WHERE s.map_change_id = NEW.id AND s.decision = 'approved' AND s.digest = kge.map_change_digest(NEW)) < 2 THEN
      RAISE EXCEPTION '变更单批准需要两名不同 staff 签字' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  -- 批准后内容不能再改；待审期间改了内容，之前的签字按摘要不再计数（kge.sign_map_change 只数与当前摘要相同的签字）
  IF OLD.status <> 'proposed' THEN
    IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.evidence_pack IS DISTINCT FROM OLD.evidence_pack
       OR NEW.change_type IS DISTINCT FROM OLD.change_type OR NEW.target_version IS DISTINCT FROM OLD.target_version
       OR NEW.subject IS DISTINCT FROM OLD.subject THEN
      RAISE EXCEPTION '已有签字的变更单内容不能修改' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION kge.guard_map_change() FROM PUBLIC;
CREATE TRIGGER guard_map_change BEFORE INSERT OR UPDATE ON kge.map_change FOR EACH ROW EXECUTE FUNCTION kge.guard_map_change();
REVOKE UPDATE ON kge.map_change FROM starbuddy_kge;
GRANT UPDATE (payload, evidence_pack, confidence, target_version, reviewers) ON kge.map_change TO starbuddy_kge;
GRANT UPDATE (status) ON kge.map_change TO starbuddy_authz;

-- ---------- 地图版本：发布与撤下只能经函数 ----------
REVOKE UPDATE (status) ON kge.map_version FROM starbuddy_kge;
CREATE OR REPLACE FUNCTION kge.guard_map_version() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' OR NEW.published_at IS NOT NULL THEN
      RAISE EXCEPTION '地图版本只能以草稿新建' USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF NEW.id <> OLD.id OR NEW.subject <> OLD.subject OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION '地图版本的 ID、学科与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status <> 'draft' AND (NEW.version <> OLD.version OR NEW.standard_year <> OLD.standard_year) THEN
    RAISE EXCEPTION '非草稿地图版本只能改状态' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status = 'draft' AND NEW.status IN ('published', 'retired')) OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
      RAISE EXCEPTION '不允许的地图版本状态变化：% → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'published' AND current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '地图版本只能经 kge.publish_map_version 发布' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- 0018：撤下同样只能经函数（kge.retire_map_version，或发布新版本时自动撤下）
    IF NEW.status = 'retired' AND current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '地图版本只能经 kge.retire_map_version 撤下' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF NEW.published_at IS DISTINCT FROM OLD.published_at THEN
    RAISE EXCEPTION '发布时间只能在发布时写入' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- 版本号三段比较
CREATE FUNCTION kge.version_parts(p text) RETURNS integer[] LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT ARRAY[split_part(p, '.', 1)::integer, split_part(p, '.', 2)::integer, split_part(p, '.', 3)::integer] $$;
REVOKE EXECUTE ON FUNCTION kge.version_parts(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.version_parts(text) TO starbuddy_authz;

-- ---------- 签字：审核记录 ----------
-- 对象：map_version（草稿，摘要为整个版本）；item_tag（'<版本>:<学科>:<题目>'）与 current_affair（ID），用于已发布版本上新增的原子
CREATE FUNCTION kge.sign_review(p_object_type text, p_object_id text, p_decision text, p_risk_level text, p_basis_code text)
  RETURNS uuid
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v_staff uuid := kge.current_staff();
  v_digest text;
  v_id uuid;
  v_parts text[];
BEGIN
  IF p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_object_type = 'map_version' THEN
    IF NOT EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id::text = p_object_id AND v.status = 'draft') THEN
      RAISE EXCEPTION '只能对草稿地图版本签字' USING ERRCODE = 'check_violation';
    END IF;
    v_digest := kge.map_version_digest(p_object_id::uuid);
  ELSIF p_object_type = 'item_tag' THEN
    v_parts := string_to_array(p_object_id, ':');
    SELECT kge.row_digest(to_jsonb(t) - 'tag_review_status') INTO v_digest FROM kge.item_tag t
     WHERE cardinality(v_parts) = 3 AND t.map_version_id::text = v_parts[1] AND t.subject::text = v_parts[2] AND t.item_id::text = v_parts[3];
  ELSIF p_object_type = 'current_affair' THEN
    SELECT kge.row_digest(to_jsonb(c) - 'review_status') INTO v_digest FROM kge.current_affair c WHERE c.id::text = p_object_id;
  ELSE
    RAISE EXCEPTION '审核台 v0 不支持对 % 单独签字', p_object_type USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF v_digest IS NULL THEN
    RAISE EXCEPTION '审核对象不存在' USING ERRCODE = 'no_data_found';
  END IF;
  INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, digest, basis_code)
  VALUES (p_object_type, p_object_id, p_risk_level, p_decision, clock_timestamp(), v_digest, p_basis_code)
  RETURNING id INTO v_id;
  INSERT INTO platform.content_signoff (content_review_id, account_id) VALUES (v_id, v_staff);
  RETURN v_id;
END $$;
ALTER FUNCTION kge.sign_review(text, text, text, text, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.sign_review(text, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.sign_review(text, text, text, text, text) TO starbuddy_review;

-- ---------- 签字：变更单（两名不同 staff 批准才转为 approved；任何一人驳回即 rejected） ----------
CREATE FUNCTION kge.sign_map_change(p_change uuid, p_decision text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v_staff uuid := kge.current_staff();
  c kge.map_change%ROWTYPE;
BEGIN
  IF p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '签字结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO c FROM kge.map_change WHERE id = p_change FOR UPDATE;
  IF NOT FOUND OR c.status <> 'proposed' THEN
    RAISE EXCEPTION '只能对待审的变更单签字' USING ERRCODE = 'check_violation';
  END IF;
  BEGIN
    INSERT INTO platform.map_change_signoff (map_change_id, account_id, decision, digest)
    VALUES (p_change, v_staff, p_decision, kge.map_change_digest(c));
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION '同一账号对同一变更单只能签一次' USING ERRCODE = 'unique_violation';
  END;
  IF p_decision = 'rejected' THEN
    UPDATE kge.map_change SET status = 'rejected' WHERE id = p_change;
    RETURN 'rejected';
  END IF;
  IF (SELECT count(DISTINCT s.account_id) FROM platform.map_change_signoff s
       WHERE s.map_change_id = p_change AND s.decision = 'approved' AND s.digest = kge.map_change_digest(c)) >= 2 THEN
    UPDATE kge.map_change SET status = 'approved' WHERE id = p_change;
    RETURN 'approved';
  END IF;
  RETURN 'proposed';
END $$;
ALTER FUNCTION kge.sign_map_change(uuid, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.sign_map_change(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.sign_map_change(uuid, text) TO starbuddy_review;

-- ---------- 发布：在 0011 的规则上加签字、摘要、变更单、版本号检查；改由签字身份执行 ----------
CREATE OR REPLACE FUNCTION kge.publish_map_version(p_version uuid) RETURNS timestamptz
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v_staff uuid := kge.current_staff();
  v kge.map_version%ROWTYPE;
  v_errors integer;
  v_now timestamptz := now();
  r kge.content_review%ROWTYPE;
  v_max integer[];
  v_new integer[];
BEGIN
  SELECT * INTO v FROM kge.map_version WHERE id = p_version FOR UPDATE;
  IF NOT FOUND OR v.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿地图版本' USING ERRCODE = 'check_violation';
  END IF;
  -- 同一学科的发布串行执行（事务级咨询锁）：两个草稿并发发布时，后一个等前一个提交，不会静默撤下刚发布的版本（安全审查建议 2）
  PERFORM pg_advisory_xact_lock(hashtext('kge.publish_map_version:' || v.subject::text));
  PERFORM 1 FROM kge.map_version WHERE subject = v.subject AND status = 'published' FOR UPDATE;
  SELECT count(*) INTO v_errors FROM kge.validate_map_version(p_version) c WHERE c.severity = 'error';
  IF v_errors > 0 THEN
    RAISE EXCEPTION '发布校验有 % 个阻断问题，拒绝发布', v_errors USING ERRCODE = 'check_violation';
  END IF;
  -- 1. 最近一条审核结论必须是批准，且签字时的摘要与现在相同（签字后又改过要重签）
  SELECT * INTO r FROM kge.content_review
   WHERE object_type = 'map_version' AND object_id = p_version::text AND action = 'review' AND decision <> 'pending'
   ORDER BY decided_at DESC, id DESC LIMIT 1;
  IF NOT FOUND OR r.decision <> 'approved' THEN
    RAISE EXCEPTION '地图版本没有批准的审核记录，拒绝发布' USING ERRCODE = 'check_violation';
  END IF;
  IF r.digest IS DISTINCT FROM kge.map_version_digest(p_version) THEN
    RAISE EXCEPTION '签字后草稿又被修改（摘要不符），请重新审核签字' USING ERRCODE = 'check_violation';
  END IF;
  -- 2. 目标为该版本的变更单全部已批准（两名不同 staff，由约束保证）
  IF EXISTS (SELECT 1 FROM kge.map_change m WHERE m.target_version = p_version AND m.status <> 'approved') THEN
    RAISE EXCEPTION '目标为该版本的变更单未全部批准，拒绝发布' USING ERRCODE = 'check_violation';
  END IF;
  -- 3. 版本号严格递增；含结构性变更时次版本号或主版本号必须增加
  v_new := kge.version_parts(v.version);
  SELECT max(kge.version_parts(o.version)) INTO v_max FROM kge.map_version o
   WHERE o.subject = v.subject AND o.id <> p_version AND o.status IN ('published', 'retired');
  IF v_max IS NOT NULL THEN
    IF v_new <= v_max THEN
      RAISE EXCEPTION '版本号 % 必须大于同学科已有的最大版本', v.version USING ERRCODE = 'check_violation';
    END IF;
    IF v_new[1:2] = v_max[1:2] AND EXISTS (SELECT 1 FROM kge.map_change m WHERE m.target_version = p_version
         AND m.change_type IN ('node_add', 'node_split', 'node_merge', 'node_retire', 'edge_add', 'edge_remove')) THEN
      RAISE EXCEPTION '含结构性变更时次版本号或主版本号必须增加' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  UPDATE kge.map_version SET status = 'retired' WHERE subject = v.subject AND status = 'published';
  UPDATE kge.map_version SET status = 'published', published_at = v_now WHERE id = p_version;
  UPDATE kge.map_change SET status = 'applied' WHERE target_version = p_version AND status = 'approved';
  RETURN v_now;
END $$;
REVOKE EXECUTE ON FUNCTION kge.publish_map_version(uuid) FROM starbuddy_kge;
GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_review;

-- ---------- 撤下 ----------
CREATE FUNCTION kge.retire_map_version(p_version uuid, p_basis_code text) RETURNS void
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE v_staff uuid := kge.current_staff(); v_id uuid;
BEGIN
  UPDATE kge.map_version SET status = 'retired' WHERE id = p_version AND status IN ('draft', 'published');
  IF NOT FOUND THEN
    RAISE EXCEPTION '只能撤下草稿或已发布的地图版本' USING ERRCODE = 'check_violation';
  END IF;
  INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, decided_at, basis_code, action)
  VALUES ('map_version', p_version::text, 'high', 'approved', clock_timestamp(), p_basis_code, 'retire')
  RETURNING id INTO v_id;
  INSERT INTO platform.content_signoff (content_review_id, account_id) VALUES (v_id, v_staff);
END $$;
ALTER FUNCTION kge.retire_map_version(uuid, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.retire_map_version(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.retire_map_version(uuid, text) TO starbuddy_review;

-- ---------- 已发布版本上新增原子：批准前必须有对应的批准审核记录（摘要一致） ----------
CREATE FUNCTION kge.guard_atom_approval() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, pg_temp
  AS $$
DECLARE
  v_key text;
  v_digest text;
  v_now_ok boolean;
  v_was_ok boolean := false;
BEGIN
  IF TG_TABLE_NAME = 'item_tag' THEN
    v_now_ok := NEW.tag_review_status = 'approved';
    IF TG_OP = 'UPDATE' THEN v_was_ok := OLD.tag_review_status = 'approved'; END IF;
    v_key := NEW.map_version_id || ':' || NEW.subject || ':' || NEW.item_id;
    v_digest := kge.row_digest(to_jsonb(NEW) - 'tag_review_status');
  ELSE
    v_now_ok := NEW.review_status = 'published';
    IF TG_OP = 'UPDATE' THEN v_was_ok := OLD.review_status = 'published'; END IF;
    v_key := NEW.id::text;
    v_digest := kge.row_digest(to_jsonb(NEW) - 'review_status');
  END IF;
  -- 草稿版本上的原子随整个版本签字发布；这里只管已发布、已撤下版本上新增或改为批准的原子
  IF v_now_ok AND NOT v_was_ok
     AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = NEW.map_version_id AND v.status <> 'draft')
     AND NOT EXISTS (SELECT 1 FROM kge.content_review r
                      WHERE r.object_type = TG_TABLE_NAME AND r.object_id = v_key AND r.decision = 'approved'
                        AND r.action = 'review' AND r.digest = v_digest) THEN
    RAISE EXCEPTION '已发布地图版本上的 % 需要批准的审核记录才能生效', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION kge.guard_atom_approval() FROM PUBLIC;
CREATE TRIGGER guard_atom_approval BEFORE INSERT OR UPDATE ON kge.item_tag FOR EACH ROW EXECUTE FUNCTION kge.guard_atom_approval();
CREATE TRIGGER guard_atom_approval BEFORE INSERT OR UPDATE ON kge.current_affair FOR EACH ROW EXECUTE FUNCTION kge.guard_atom_approval();
