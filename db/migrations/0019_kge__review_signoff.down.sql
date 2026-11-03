-- 0019 回滚：恢复 0011 的发布函数、守卫与授权，删除签字函数与新列
-- 注意：已有签字数据时，恢复的 0011 约束按 NOT VALID 加回（不校验已有行；经签字函数批准的变更单没有两个自报工号）
DROP TRIGGER lock_version_share ON kge.item_family;
DROP TRIGGER lock_version_share ON kge.scoring_point;
DROP FUNCTION kge.lock_version_share();
DROP FUNCTION kge.version_record(platform.subject);
DROP FUNCTION kge.structural_diff(uuid);
DROP TRIGGER guard_atom_approval ON kge.item_tag;
DROP TRIGGER guard_atom_approval ON kge.current_affair;
DROP FUNCTION kge.guard_atom_approval();
DROP FUNCTION kge.retire_map_version(uuid, text);
DROP FUNCTION kge.sign_map_change(uuid, text, text);
DROP FUNCTION kge.map_change_digest_of(uuid);
DROP FUNCTION kge.sign_review(text, text, text, text, text);
REVOKE EXECUTE ON FUNCTION kge.publish_map_version(uuid) FROM starbuddy_review;
CREATE OR REPLACE FUNCTION kge.publish_map_version(p_version uuid) RETURNS timestamptz
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v kge.map_version%ROWTYPE;
  v_errors integer;
  v_now timestamptz := now();
BEGIN
  SELECT * INTO v FROM kge.map_version WHERE id = p_version FOR UPDATE;
  IF NOT FOUND OR v.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿地图版本' USING ERRCODE = 'check_violation';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('kge.publish_map_version:' || v.subject::text));
  PERFORM 1 FROM kge.map_version WHERE subject = v.subject AND status = 'published' FOR UPDATE;
  SELECT count(*) INTO v_errors FROM kge.validate_map_version(p_version) c WHERE c.severity = 'error';
  IF v_errors > 0 THEN
    RAISE EXCEPTION '发布校验有 % 个阻断问题，拒绝发布', v_errors USING ERRCODE = 'check_violation';
  END IF;
  UPDATE kge.map_version SET status = 'retired' WHERE subject = v.subject AND status = 'published';
  UPDATE kge.map_version SET status = 'published', published_at = v_now WHERE id = p_version;
  RETURN v_now;
END $$;
GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_kge;
DROP FUNCTION kge.version_parts(text);
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
  ELSIF NEW.published_at IS DISTINCT FROM OLD.published_at THEN
    RAISE EXCEPTION '发布时间只能在发布时写入' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
GRANT UPDATE (status) ON kge.map_version TO starbuddy_kge;
REVOKE UPDATE (status) ON kge.map_change FROM starbuddy_authz;
REVOKE UPDATE (payload, evidence_pack, confidence, target_version, reviewers) ON kge.map_change FROM starbuddy_kge;
GRANT UPDATE ON kge.map_change TO starbuddy_kge;
DROP TRIGGER guard_map_change ON kge.map_change;
DROP FUNCTION kge.guard_map_change();
DROP FUNCTION kge.map_change_digest(kge.map_change);
DROP FUNCTION kge.map_change_required_signers(text);
ALTER TABLE kge.map_change
  ADD CONSTRAINT map_change_two_reviewers CHECK (status NOT IN ('approved', 'applied') OR kge.distinct_count(reviewers) >= 2) NOT VALID;
REVOKE INSERT ON kge.content_review FROM starbuddy_authz;
DROP POLICY content_review_kge_submit ON kge.content_review;
DROP POLICY content_review_kge_read ON kge.content_review;
CREATE POLICY content_review_kge ON kge.content_review FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
GRANT UPDATE ON kge.content_review TO starbuddy_kge;
DROP TRIGGER reject_content_review_truncate ON kge.content_review;
DROP TRIGGER reject_content_review_change ON kge.content_review;
DROP FUNCTION kge.reject_content_review_change();
ALTER TABLE kge.content_review DROP CONSTRAINT content_review_decided,
  DROP CONSTRAINT content_review_version_risk, DROP CONSTRAINT content_review_retire_basis,
  ADD CONSTRAINT content_review_decided CHECK ((decision = 'pending') = (reviewer IS NULL AND decided_at IS NULL)) NOT VALID,
  DROP COLUMN action, DROP COLUMN basis_code, DROP COLUMN digest;
DROP FUNCTION kge.row_digest(jsonb);
DROP FUNCTION kge.map_version_digest(uuid);
REVOKE SELECT ON kge.id_migration, kge.item_family, kge.map_change, kge.content_review FROM starbuddy_authz;
DROP FUNCTION kge.current_staff();
REVOKE USAGE ON SCHEMA kge FROM starbuddy_review;
