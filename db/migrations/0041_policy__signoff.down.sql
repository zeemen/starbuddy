-- 0041 回滚：删除考情签字与发布收口（签字、摘要、计数函数与守卫），恢复 0013 的两人 CHECK、校验、发布函数与授权（含写入方改复核人）。
-- 导出函数 platform.policy_signoff_record() 属于 0040，随 0040 回滚删除；本回滚收回属主 authz 读学校与招录行的权限后，它在 0040 回滚前不能再用。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。先回滚本迁移，再回滚 0040。
-- 签字记录只追加（触发器连超级用户也拦删除），回滚不能也不应让已有的签字失去作用：签字人表里有记录时中止，由人决定是否放弃回滚。
-- 先锁表：检查与改函数之间不会有新的签字插进来
LOCK TABLE platform.policy_signoff IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM platform.policy_signoff;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0041 回滚中止：platform.policy_signoff 有 % 条考情签字记录（只追加，不能删除），回滚会让这些签字不再作为发布依据', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- 授权恢复 0013：发布函数只授写入方
REVOKE EXECUTE ON FUNCTION policy.publish_scheme(uuid) FROM starbuddy_review;
GRANT EXECUTE ON FUNCTION policy.publish_scheme(uuid) TO starbuddy_kge;
DROP FUNCTION policy.sign_record(text, uuid, text, text);
DROP FUNCTION policy.sign_scheme(uuid, text, text);

-- 校验与发布恢复 0013 原文
CREATE OR REPLACE FUNCTION policy.validate_scheme(p_scheme uuid)
  RETURNS TABLE (check_name text, severity text, object_id text, detail text)
  LANGUAGE plpgsql STABLE
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE s policy.exam_scheme%ROWTYPE;
BEGIN
  SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme;
  IF NOT FOUND THEN
    RAISE EXCEPTION '方案不存在' USING ERRCODE = 'no_data_found';
  END IF;
  -- 1. 来源文件已发布、同城市、覆盖该届别
  RETURN QUERY
    SELECT 'source_document', 'error', s.source_document_id::text, '来源文件未发布、不是同一城市或不覆盖该届别'
     WHERE NOT EXISTS (SELECT 1 FROM policy.source_document d
                        WHERE d.id = s.source_document_id AND d.status = 'published' AND d.city = s.city
                          AND s.cohort BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, s.cohort));
  -- 2. 至少有一门计分科目
  RETURN QUERY
    SELECT 'no_scored_subject', 'error', p_scheme::text, '没有计分科目'
     WHERE NOT EXISTS (SELECT 1 FROM policy.subject_config c WHERE c.scheme_id = p_scheme AND c.scored);
  -- 3. 合场分组至少两门
  RETURN QUERY
    SELECT 'combined_group_single', 'error', c.combined_group::text, '合场分组只有一门科目'
      FROM policy.subject_config c WHERE c.scheme_id = p_scheme AND c.combined_group IS NOT NULL
     GROUP BY c.combined_group HAVING count(*) < 2;
  -- 4. 至少有一个批次
  RETURN QUERY
    SELECT 'no_admission_rule', 'error', p_scheme::text, '没有招录规则'
     WHERE NOT EXISTS (SELECT 1 FROM policy.admission_rule r WHERE r.scheme_id = p_scheme);
  -- 5. 复核人（发布时两人确认；这里提前报出来）
  RETURN QUERY
    SELECT 'reviewers', 'error', p_scheme::text, '复核人不足两位'
     WHERE policy.distinct_count(s.reviewers) < 2 OR s.reviewed_at IS NULL;
  -- 5a. 版本号必须高于当前已发布版本（不能用旧版本撤下新版本）
  RETURN QUERY
    SELECT 'version_not_newer', 'error', p_scheme::text, '版本号不高于当前已发布版本 ' || cur.version
      FROM policy.exam_scheme cur
     WHERE cur.city = s.city AND cur.cohort = s.cohort AND cur.status = 'published'
       AND string_to_array(s.version, '.')::int[] <= string_to_array(cur.version, '.')::int[];
  -- 6. 与上一届相比总分或计分科目变了却没有过渡规则（警告）
  RETURN QUERY
    SELECT 'missing_transition', 'warning', p_scheme::text, '与上一届相比计分总分或计分科目变化，未登记过渡规则'
      FROM policy.exam_scheme prev
     WHERE prev.city = s.city AND prev.cohort = s.cohort - 1 AND prev.status = 'published'
       AND (policy.scheme_total(prev.id) IS DISTINCT FROM policy.scheme_total(p_scheme)
            OR (SELECT array_agg(subject ORDER BY subject) FROM policy.subject_config WHERE scheme_id = prev.id AND scored)
               IS DISTINCT FROM (SELECT array_agg(subject ORDER BY subject) FROM policy.subject_config WHERE scheme_id = p_scheme AND scored))
       AND NOT EXISTS (SELECT 1 FROM policy.transition_rule t WHERE t.scheme_id = p_scheme);
END $$;

CREATE OR REPLACE FUNCTION policy.publish_scheme(p_scheme uuid) RETURNS timestamptz
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE
  s policy.exam_scheme%ROWTYPE;
  v_errors integer;
  v_now timestamptz := now();
BEGIN
  SELECT * INTO s FROM policy.exam_scheme WHERE id = p_scheme FOR UPDATE;
  IF NOT FOUND OR s.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿方案' USING ERRCODE = 'check_violation';
  END IF;
  -- 同一城市同一届别的发布串行执行
  PERFORM pg_advisory_xact_lock(hashtext('policy.publish_scheme:' || s.city || ':' || s.cohort));
  PERFORM 1 FROM policy.exam_scheme WHERE city = s.city AND cohort = s.cohort AND status = 'published' FOR UPDATE;
  -- 锁住来源文件，校验通过后提交前它不能被并发撤下
  PERFORM 1 FROM policy.source_document WHERE id = s.source_document_id FOR SHARE;
  SELECT count(*) INTO v_errors FROM policy.validate_scheme(p_scheme) c WHERE c.severity = 'error';
  IF v_errors > 0 THEN
    RAISE EXCEPTION '发布校验有 % 个阻断问题，拒绝发布', v_errors USING ERRCODE = 'check_violation';
  END IF;
  UPDATE policy.exam_scheme SET status = 'retired' WHERE city = s.city AND cohort = s.cohort AND status = 'published';
  UPDATE policy.exam_scheme SET status = 'published', published_at = v_now WHERE id = p_scheme;
  RETURN v_now;
END $$;

DROP FUNCTION policy.approval_count(text, uuid);
DROP FUNCTION policy.object_digest(text, uuid);

DROP TRIGGER guard_signoff_publish ON policy.school;
DROP TRIGGER guard_signoff_publish ON policy.admission_line;
DROP TRIGGER guard_signoff_publish ON policy.exam_scheme;
DROP TRIGGER guard_signoff_publish ON policy.source_document;
DROP FUNCTION policy.guard_signoff_publish();

-- 0013 的两人 CHECK 以 NOT VALID 恢复：经签字函数发布的行自报工号可能为空，不能让回滚被这些行卡住（同 0019 的做法）
ALTER TABLE policy.school
  DROP CONSTRAINT school_reviewed,
  ADD CONSTRAINT school_two_reviewers CHECK (review_status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL)) NOT VALID;
ALTER TABLE policy.admission_line
  DROP CONSTRAINT admission_line_reviewed,
  ADD CONSTRAINT admission_line_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL)) NOT VALID;
ALTER TABLE policy.exam_scheme
  DROP CONSTRAINT exam_scheme_reviewed,
  ADD CONSTRAINT exam_scheme_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL)) NOT VALID;
ALTER TABLE policy.source_document
  DROP CONSTRAINT source_document_reviewed,
  ADD CONSTRAINT source_document_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL)) NOT VALID;
COMMENT ON COLUMN policy.source_document.reviewers IS NULL;
COMMENT ON COLUMN policy.exam_scheme.reviewers IS NULL;
COMMENT ON COLUMN policy.admission_line.reviewers IS NULL;
COMMENT ON COLUMN policy.school.reviewers IS NULL;
COMMENT ON COLUMN policy.exam_scheme.reviewed_at IS NULL;

-- 写入方恢复写复核人与复核时间；收回函数属主的新增授权（0040 的导出函数随 0040 回滚删除）
GRANT UPDATE (reviewed_at, reviewers) ON policy.source_document, policy.exam_scheme, policy.admission_line, policy.school TO starbuddy_kge;
REVOKE UPDATE (reviewed_at) ON policy.exam_scheme FROM starbuddy_authz;
REVOKE UPDATE (review_status, reviewed_at) ON policy.school FROM starbuddy_authz;
REVOKE UPDATE (status, reviewed_at) ON policy.source_document, policy.admission_line FROM starbuddy_authz;
REVOKE SELECT ON policy.school, policy.admission_line, policy.source_domain FROM starbuddy_authz;

DROP FUNCTION policy.signers_required();
REVOKE USAGE ON SCHEMA policy FROM starbuddy_review;
