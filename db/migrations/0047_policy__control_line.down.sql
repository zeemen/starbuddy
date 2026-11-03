-- 0047 回滚：删除控制线表，恢复招录行上的控制线列（0013，值从归档表放回）、签字人表与签字、摘要、导出函数（0040、0041 原文），删除归档表。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。先回滚 0048。
-- 控制线表里有行时中止：控制线是一城一条，放回逐校的招录行要人决定挂在哪些行上；签字记录只追加，引用控制线的签字也删不掉。由人处理。
LOCK TABLE policy.control_line, platform.policy_signoff, policy.admission_line IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE v_n bigint;
BEGIN
  SELECT count(*) INTO v_n FROM policy.control_line;
  IF v_n > 0 THEN
    RAISE EXCEPTION '0047 回滚中止：policy.control_line 有 % 行；回滚会丢失控制线（及引用它们的只追加签字记录），请人先决定处理办法', v_n
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- ---------- 招录行恢复控制线列、约束、授权与 0013 的守卫；旧值从归档表原样放回 ----------
ALTER TABLE policy.admission_line ADD COLUMN control_line numeric(5, 1) CHECK (control_line >= 0);
ALTER TABLE policy.admission_line ADD CONSTRAINT admission_line_control_line CHECK (track = 'quota_allocation' OR control_line IS NULL);
GRANT UPDATE (control_line) ON policy.admission_line TO starbuddy_kge;
GRANT SELECT (control_line) ON policy.admission_line TO starbuddy_bff;
COMMENT ON TABLE policy.admission_line IS '学校 × 年度 × 批次 × 招生类别的投档线、计划数与控制线；统招与指标生分开（v1.3 §5.1）。只收官方公告';
-- 放回旧值：已发布与已撤下的行保护触发器不许改，这里只放回原来就在这些行上的值，临时停用本表的用户触发器
ALTER TABLE policy.admission_line DISABLE TRIGGER USER;
UPDATE policy.admission_line l SET control_line = a.control_line
  FROM policy.admission_line_control_line_archive a WHERE a.admission_line_id = l.id;
ALTER TABLE policy.admission_line ENABLE TRIGGER USER;
DROP TABLE policy.admission_line_control_line_archive;
CREATE OR REPLACE FUNCTION policy.guard_admission_line() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE
  v_city text;
  v_total numeric;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'retired' THEN
      RAISE EXCEPTION '已撤下的招录行不能再改' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'published' AND (NEW.status <> 'retired' OR
        (NEW.school_id, NEW.year, NEW.batch_code, NEW.track, NEW.line_score, NEW.plan_count, NEW.control_line,
         NEW.source_document_id, NEW.reviewers, NEW.reviewed_at, NEW.created_at)
        IS DISTINCT FROM
        (OLD.school_id, OLD.year, OLD.batch_code, OLD.track, OLD.line_score, OLD.plan_count, OLD.control_line,
         OLD.source_document_id, OLD.reviewers, OLD.reviewed_at, OLD.created_at)) THEN
      RAISE EXCEPTION '已发布的招录行只能撤下；更正请新建一行' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'published' THEN
      RETURN NEW;  -- 撤下
    END IF;
  ELSE
    NEW.created_at := now();
  END IF;
  SELECT s.city INTO v_city FROM policy.school s WHERE s.id = NEW.school_id AND s.review_status = 'published';
  IF v_city IS NULL THEN
    RAISE EXCEPTION '学校未复核发布' USING ERRCODE = 'check_violation';
  END IF;
  PERFORM 1 FROM policy.source_document d WHERE d.id = NEW.source_document_id FOR SHARE;
  IF NOT EXISTS (SELECT 1 FROM policy.source_document d
                  WHERE d.id = NEW.source_document_id AND d.status = 'published' AND d.city = v_city
                    AND NEW.year BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, NEW.year)) THEN
    RAISE EXCEPTION '来源文件未发布、不是同一城市或不覆盖该年' USING ERRCODE = 'check_violation';
  END IF;
  SELECT policy.scheme_total(e.id) INTO v_total FROM policy.exam_scheme e
   WHERE e.city = v_city AND e.cohort = NEW.year AND e.status = 'published';
  IF v_total IS NULL THEN
    RAISE EXCEPTION '该城市该年没有已发布的中考方案，先录入方案' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM policy.admission_rule r JOIN policy.exam_scheme e ON e.id = r.scheme_id
                  WHERE e.city = v_city AND e.cohort = NEW.year AND e.status = 'published'
                    AND r.batch_code = NEW.batch_code AND r.track = NEW.track) THEN
    RAISE EXCEPTION '该年已发布方案的招录规则里没有这个批次与类别' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.line_score > v_total OR NEW.control_line > v_total THEN
    RAISE EXCEPTION '分数超过该年方案的计分总分 %', v_total USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- ---------- 签字、摘要、导出恢复 0041 / 0040 原文 ----------
CREATE OR REPLACE FUNCTION policy.object_digest(p_object_type text, p_id uuid) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
  SELECT encode(sha256(convert_to(q.content::text, 'UTF8')), 'hex') FROM (
    SELECT to_jsonb(d) - 'status' - 'reviewers' - 'reviewed_at' - 'created_at' AS content
      FROM policy.source_document d WHERE p_object_type = 'source_document' AND d.id = p_id
    UNION ALL
    SELECT jsonb_build_object(
             'scheme', to_jsonb(s) - 'status' - 'reviewers' - 'reviewed_at' - 'published_at' - 'created_at',
             'subjects', (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'scheme_id' ORDER BY c.subject), '[]')
                            FROM policy.subject_config c WHERE c.scheme_id = s.id),
             'transitions', (SELECT coalesce(jsonb_agg(to_jsonb(t) - 'id' - 'scheme_id' ORDER BY t.change_kind), '[]')
                               FROM policy.transition_rule t WHERE t.scheme_id = s.id),
             'admission_rules', (SELECT coalesce(jsonb_agg(to_jsonb(r) - 'scheme_id' ORDER BY r.batch_code, r.track), '[]')
                                   FROM policy.admission_rule r WHERE r.scheme_id = s.id))
      FROM policy.exam_scheme s WHERE p_object_type = 'exam_scheme' AND s.id = p_id
    UNION ALL
    SELECT to_jsonb(l) - 'status' - 'reviewers' - 'reviewed_at' - 'created_at'
      FROM policy.admission_line l WHERE p_object_type = 'admission_line' AND l.id = p_id
    UNION ALL
    SELECT to_jsonb(h) - 'review_status' - 'reviewers' - 'reviewed_at'
      FROM policy.school h WHERE p_object_type = 'school' AND h.id = p_id
  ) q $$;

CREATE OR REPLACE FUNCTION policy.sign_record(p_object_type text, p_id uuid, p_decision text, p_expected_digest text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET TimeZone = 'UTC'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_status text;
  v_digest text;
  v_ts timestamptz := clock_timestamp();
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION '审核结论只能是 approved 或 rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 锁住对象行：同一对象的签字串行（两名 staff 同时签时，后一个等前一个提交后再计数），也与写入方的修改互斥
  IF p_object_type = 'source_document' THEN
    SELECT d.status::text INTO v_status FROM policy.source_document d WHERE d.id = p_id FOR UPDATE;
  ELSIF p_object_type = 'admission_line' THEN
    SELECT l.status::text INTO v_status FROM policy.admission_line l WHERE l.id = p_id FOR UPDATE;
  ELSIF p_object_type = 'school' THEN
    SELECT h.review_status::text INTO v_status FROM policy.school h WHERE h.id = p_id FOR UPDATE;
  ELSE
    RAISE EXCEPTION '对象类型只能是 source_document、admission_line、school（方案用 policy.sign_scheme 签字、policy.publish_scheme 发布）'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF v_status IS NULL THEN
    RAISE EXCEPTION '签字对象不存在' USING ERRCODE = 'no_data_found';
  END IF;
  IF v_status <> 'draft' THEN
    RAISE EXCEPTION '只能对草稿签字（当前 %）', v_status USING ERRCODE = 'check_violation';
  END IF;
  v_digest := policy.object_digest(p_object_type, p_id);
  IF p_expected_digest IS DISTINCT FROM v_digest THEN
    RAISE EXCEPTION '你看到的内容与当前不一致（摘要不符），请刷新后再签' USING ERRCODE = 'check_violation';
  END IF;
  BEGIN
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, action, decision, digest,
                                         account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, 'review', p_decision, v_digest, v_staff, v_ts);
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION '同一账号对同一内容只能签一次' USING ERRCODE = 'unique_violation';
  END;
  IF p_decision = 'approved' AND policy.approval_count(p_object_type, p_id) >= policy.signers_required() THEN
    -- 发布：表上的保护触发器照常执行（招录行再核对学校、来源、方案与总分；同一行只能有一条已发布）
    IF p_object_type = 'source_document' THEN
      UPDATE policy.source_document SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSIF p_object_type = 'admission_line' THEN
      UPDATE policy.admission_line SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSE
      UPDATE policy.school SET review_status = 'published', reviewed_at = v_ts WHERE id = p_id;
    END IF;
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, action, decision, digest,
                                         account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, 'publish', 'approved', v_digest, v_staff, clock_timestamp());
    RETURN 'published';
  END IF;
  RETURN 'draft';
END $$;

CREATE OR REPLACE FUNCTION platform.policy_signoff_record()
  RETURNS TABLE (object_type text, object_id uuid, city text, version text, cohort integer, action text, decision text,
                 digest text, signed_at timestamptz, signer uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT x.object_type, x.object_id, coalesce(d.city, e.city, lh.city, h.city)::text, e.version,
         coalesce(e.cohort, l.year)::integer, x.action, x.decision, x.digest, x.signed_at, x.account_id
    FROM platform.policy_signoff x
    LEFT JOIN policy.source_document d ON d.id = x.source_document_id
    LEFT JOIN policy.exam_scheme e ON e.id = x.exam_scheme_id
    LEFT JOIN policy.admission_line l ON l.id = x.admission_line_id
    LEFT JOIN policy.school lh ON lh.id = l.school_id
    LEFT JOIN policy.school h ON h.id = x.institution_id
   ORDER BY x.signed_at, x.action = 'publish', x.id $$;

-- ---------- 签字人表恢复 0040 的对象类型与生成列 ----------
ALTER TABLE platform.policy_signoff DROP COLUMN object_id;
ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_one_object;
ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_object_type_check;
ALTER TABLE platform.policy_signoff DROP COLUMN control_line_id;
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_object_type_check
  CHECK (object_type IN ('source_document', 'exam_scheme', 'admission_line', 'school'));
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_one_object CHECK (
  num_nonnulls(source_document_id, exam_scheme_id, admission_line_id, institution_id) = 1
  AND CASE object_type
        WHEN 'source_document' THEN source_document_id IS NOT NULL
        WHEN 'exam_scheme' THEN exam_scheme_id IS NOT NULL
        WHEN 'admission_line' THEN admission_line_id IS NOT NULL
        ELSE institution_id IS NOT NULL
      END);
ALTER TABLE platform.policy_signoff
  ADD COLUMN object_id uuid GENERATED ALWAYS AS (coalesce(source_document_id, exam_scheme_id, admission_line_id, institution_id)) STORED;
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_once UNIQUE (object_type, object_id, action, account_id, digest);
COMMENT ON TABLE platform.policy_signoff IS
  '考情政策线（来源文件、方案、招录行、学校）的签字人：只由 policy.sign_scheme / sign_record / publish_scheme 写入（0041）；'
  '两名不同 staff 对当前内容批准才能发布；导出经 platform.policy_signoff_record()';

DROP TABLE policy.control_line;
DROP FUNCTION policy.guard_control_line();
