-- 0047 policy · 控制线单独建表（specs/policy-decisions-v0.9.1/spec.md 第 2 节，PR1；用户结论 2）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。请以单事务执行（psql -1）。
-- 依赖 0013（policy 表与招录行守卫）、0040（签字人表 platform.policy_signoff 与导出函数）、0041（签字、摘要、发布守卫）。
-- 要点：
--   1. policy.control_line：城市 × 年份（中考届别）× 批次 × 类别（只收指标生 quota_allocation）一条控制线分数；
--      必须对应该年已发布方案里 control_line_basis <> 'none' 的招录规则；来源文件已发布、同城市、覆盖该年；分数不超过该年方案的计分总分；
--      同一（城市、年份、批次、类别）只能有一条已发布（唯一索引）；已发布只能撤下，更正走新行 + 撤下旧行（照 admission_line）；
--      草稿不能直接撤下（同 0046 对方案与来源文件的规则）；没有自报复核人列：签字人只在 platform.policy_signoff；
--   2. 发布照 0041：只能以草稿新建；两名不同 staff 经 policy.sign_record 对当前内容批准，第二名批准时发布（守卫 guard_signoff_publish 共用）；
--      签字人记在 platform.policy_signoff（新增 control_line_id 列与对象类型），policy 表上不存账号引用（规则 13）；
--   3. 归档与回填：先锁住招录行（SHARE ROW EXCLUSIVE，回填与删列期间不能有并发写入）；
--      所有行上非空的旧控制线 (招录行 id, 值, 状态) 原样存进归档表 policy.admission_line_control_line_archive（只读，无账号列）；
--      再把已发布与草稿行的值按（城市、年份、批次、类别）归并成控制线草稿（取值优先已发布行、其次草稿，同优先级取最新建立的；来源文件取同一行的）；
--      已撤下行的值只留在归档表。同组已发布行的值不一致、或回填行不满足上面第 1 条时，以明确信息中止，由人处理。
--      回填的是草稿：0041 起发布必须有两名 staff 对当前内容的签字，旧列的值没有签字记录可补，执行后由两名 staff 重新签字发布；
--   4. 回填后删除 admission_line.control_line 列（spec 待人确认 #4 默认）：招录行守卫换成不含控制线的新版本；
--      该列的 BFF 读与写入方改的列级授权随列删除；招录行摘要随之不再含控制线（尚未发布的招录行草稿要重新签字）；
--   5. BFF 只读已发布控制线，不给复核时间与建立时间；学习服务读全部；写入方只能新建草稿、改分数、来源与状态（撤下）。
-- 跨 schema：签字人表（platform）与导出函数随对象类型一起扩展，放在本迁移（ADR 02 例外：签字人表的新列外键指向 policy.control_line，
-- 签字与导出函数的函数体按对象类型分支，拆开会有一个迁移里签字函数认得控制线、签字人表却存不下的中间状态）。

-- ---------- 1. 表 ----------
CREATE TABLE policy.control_line (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  city                policy.city_code NOT NULL,
  year                policy.cohort NOT NULL,                       -- 中考年份（届别）
  batch_code          policy.code NOT NULL,
  track               text NOT NULL DEFAULT 'quota_allocation' CONSTRAINT control_line_track CHECK (track = 'quota_allocation'),
  score               numeric(5, 1) NOT NULL CHECK (score >= 0),
  source_document_id  uuid NOT NULL REFERENCES policy.source_document (id),
  status              policy.pub_status NOT NULL DEFAULT 'draft',
  reviewed_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT control_line_reviewed CHECK (status = 'draft' OR reviewed_at IS NOT NULL)
);
-- 同一城市同一年同一批次同一类别只有一条已发布；更正走新行 + 撤下旧行
CREATE UNIQUE INDEX control_line_one_published ON policy.control_line (city, year, batch_code, track) WHERE status = 'published';
COMMENT ON TABLE policy.control_line IS
  '指标生控制线（0047）：城市 × 年份 × 批次 × 类别一条；对应该年已发布方案里 control_line_basis 不是 none 的招录规则；只收官方公告；'
  '两名不同 staff 签字发布（签字人在 platform.policy_signoff），已发布只能撤下，草稿不能直接撤下';

-- ---------- 2. 保护触发器（SECURITY INVOKER） ----------
CREATE FUNCTION policy.guard_control_line() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE v_total numeric;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'retired' THEN
      RAISE EXCEPTION '已撤下的控制线不能再改' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'draft' AND NEW.status = 'retired' THEN
      RAISE EXCEPTION '草稿不能直接撤下（放弃的草稿留在草稿）' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'published' AND (NEW.status <> 'retired' OR
        (NEW.city, NEW.year, NEW.batch_code, NEW.track, NEW.score, NEW.source_document_id, NEW.reviewed_at, NEW.created_at)
        IS DISTINCT FROM
        (OLD.city, OLD.year, OLD.batch_code, OLD.track, OLD.score, OLD.source_document_id, OLD.reviewed_at, OLD.created_at)) THEN
      RAISE EXCEPTION '已发布的控制线只能撤下；更正请新建一行' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'published' THEN
      RETURN NEW;  -- 撤下
    END IF;
  ELSE
    NEW.created_at := now();
  END IF;
  -- 草稿的新建、修改与发布：来源文件、方案、招录规则与总分都按当前状态再核对一次
  PERFORM 1 FROM policy.source_document d WHERE d.id = NEW.source_document_id FOR SHARE;
  IF NOT EXISTS (SELECT 1 FROM policy.source_document d
                  WHERE d.id = NEW.source_document_id AND d.status = 'published' AND d.city = NEW.city
                    AND NEW.year BETWEEN d.applies_from_cohort AND coalesce(d.applies_to_cohort, NEW.year)) THEN
    RAISE EXCEPTION '控制线的来源文件未发布、不是同一城市或不覆盖该年' USING ERRCODE = 'check_violation';
  END IF;
  SELECT policy.scheme_total(e.id) INTO v_total FROM policy.exam_scheme e
   WHERE e.city = NEW.city AND e.cohort = NEW.year AND e.status = 'published';
  IF v_total IS NULL THEN
    RAISE EXCEPTION '该城市该年没有已发布的中考方案，先录入方案' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM policy.admission_rule r JOIN policy.exam_scheme e ON e.id = r.scheme_id
                  WHERE e.city = NEW.city AND e.cohort = NEW.year AND e.status = 'published'
                    AND r.batch_code = NEW.batch_code AND r.track = NEW.track AND r.control_line_basis <> 'none') THEN
    RAISE EXCEPTION '该年已发布方案的招录规则里没有这个批次需要控制线的指标生规则（control_line_basis 不是 none）' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.score > v_total THEN
    RAISE EXCEPTION '控制线超过该年方案的计分总分 %', v_total USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION policy.guard_control_line() FROM PUBLIC;
CREATE TRIGGER guard_control_line BEFORE INSERT OR UPDATE ON policy.control_line
  FOR EACH ROW EXECUTE FUNCTION policy.guard_control_line();
-- 发布收口（0041 的共用守卫）：只能以草稿新建、不带复核时间；改为已发布与写复核时间只能经签字函数（属主 authz）
CREATE TRIGGER guard_signoff_publish BEFORE INSERT OR UPDATE ON policy.control_line
  FOR EACH ROW EXECUTE FUNCTION policy.guard_signoff_publish();

-- ---------- 3. 授权与 RLS：任何角色都没有 DELETE（撤下走状态） ----------
GRANT SELECT, INSERT ON policy.control_line TO starbuddy_kge;
GRANT UPDATE (score, source_document_id, status) ON policy.control_line TO starbuddy_kge;
GRANT SELECT ON policy.control_line TO starbuddy_learning;
GRANT SELECT (id, city, year, batch_code, track, score, source_document_id, status) ON policy.control_line TO starbuddy_bff;
-- 签字函数属主：锁行、发布（状态与复核时间）、导出读城市与年份
GRANT SELECT ON policy.control_line TO starbuddy_authz;
GRANT UPDATE (status, reviewed_at) ON policy.control_line TO starbuddy_authz;
ALTER TABLE policy.control_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.control_line FORCE ROW LEVEL SECURITY;
CREATE POLICY control_line_kge ON policy.control_line FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY control_line_read ON policy.control_line FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY control_line_front_read ON policy.control_line FOR SELECT TO starbuddy_bff USING (status = 'published');

-- ---------- 4. 签字人表认控制线（platform；只追加，ALTER 不触发行级触发器） ----------
ALTER TABLE platform.policy_signoff ADD COLUMN control_line_id uuid REFERENCES policy.control_line (id);
ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_object_type_check;
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_object_type_check
  CHECK (object_type IN ('source_document', 'exam_scheme', 'admission_line', 'school', 'control_line'));
ALTER TABLE platform.policy_signoff DROP CONSTRAINT policy_signoff_one_object;
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_one_object CHECK (
  num_nonnulls(source_document_id, exam_scheme_id, admission_line_id, institution_id, control_line_id) = 1
  AND CASE object_type
        WHEN 'source_document' THEN source_document_id IS NOT NULL
        WHEN 'exam_scheme' THEN exam_scheme_id IS NOT NULL
        WHEN 'admission_line' THEN admission_line_id IS NOT NULL
        WHEN 'control_line' THEN control_line_id IS NOT NULL
        ELSE institution_id IS NOT NULL
      END);
-- 生成列的表达式在 PG16 不能改：删掉重建（一并重建以它为键的“同一账号同一内容只签一次”唯一约束）
ALTER TABLE platform.policy_signoff DROP COLUMN object_id;
ALTER TABLE platform.policy_signoff
  ADD COLUMN object_id uuid GENERATED ALWAYS AS (coalesce(source_document_id, exam_scheme_id, admission_line_id, institution_id, control_line_id)) STORED;
ALTER TABLE platform.policy_signoff ADD CONSTRAINT policy_signoff_once UNIQUE (object_type, object_id, action, account_id, digest);
COMMENT ON TABLE platform.policy_signoff IS
  '考情政策线（来源文件、方案、招录行、学校、控制线）的签字人：只由 policy.sign_scheme / sign_record / publish_scheme 写入（0041、0047）；'
  '两名不同 staff 对当前内容批准才能发布；导出经 platform.policy_signoff_record()';

-- ---------- 5. 摘要与签字认控制线 ----------
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
    UNION ALL
    SELECT to_jsonb(k) - 'status' - 'reviewed_at' - 'created_at'
      FROM policy.control_line k WHERE p_object_type = 'control_line' AND k.id = p_id
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
  ELSIF p_object_type = 'control_line' THEN
    SELECT k.status::text INTO v_status FROM policy.control_line k WHERE k.id = p_id FOR UPDATE;
  ELSE
    RAISE EXCEPTION '对象类型只能是 source_document、admission_line、school、control_line（方案用 policy.sign_scheme 签字、policy.publish_scheme 发布）'
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
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, control_line_id, action, decision,
                                         digest, account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, CASE WHEN p_object_type = 'control_line' THEN p_id END,
            'review', p_decision, v_digest, v_staff, v_ts);
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION '同一账号对同一内容只能签一次' USING ERRCODE = 'unique_violation';
  END;
  IF p_decision = 'approved' AND policy.approval_count(p_object_type, p_id) >= policy.signers_required() THEN
    -- 发布：表上的保护触发器照常执行（招录行与控制线再核对来源、方案、招录规则与总分；同一键只能有一条已发布）
    IF p_object_type = 'source_document' THEN
      UPDATE policy.source_document SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSIF p_object_type = 'admission_line' THEN
      UPDATE policy.admission_line SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSIF p_object_type = 'control_line' THEN
      UPDATE policy.control_line SET status = 'published', reviewed_at = v_ts WHERE id = p_id;
    ELSE
      UPDATE policy.school SET review_status = 'published', reviewed_at = v_ts WHERE id = p_id;
    END IF;
    INSERT INTO platform.policy_signoff (object_type, source_document_id, admission_line_id, institution_id, control_line_id, action, decision,
                                         digest, account_id, signed_at)
    VALUES (p_object_type, CASE WHEN p_object_type = 'source_document' THEN p_id END, CASE WHEN p_object_type = 'admission_line' THEN p_id END,
            CASE WHEN p_object_type = 'school' THEN p_id END, CASE WHEN p_object_type = 'control_line' THEN p_id END,
            'publish', 'approved', v_digest, v_staff, clock_timestamp());
    RETURN 'published';
  END IF;
  RETURN 'draft';
END $$;

-- 导出（0040 的函数，platform）：控制线的签字行带城市与年份
CREATE OR REPLACE FUNCTION platform.policy_signoff_record()
  RETURNS TABLE (object_type text, object_id uuid, city text, version text, cohort integer, action text, decision text,
                 digest text, signed_at timestamptz, signer uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
  SELECT x.object_type, x.object_id, coalesce(d.city, e.city, lh.city, h.city, k.city)::text, e.version,
         coalesce(e.cohort, l.year, k.year)::integer, x.action, x.decision, x.digest, x.signed_at, x.account_id
    FROM platform.policy_signoff x
    LEFT JOIN policy.source_document d ON d.id = x.source_document_id
    LEFT JOIN policy.exam_scheme e ON e.id = x.exam_scheme_id
    LEFT JOIN policy.admission_line l ON l.id = x.admission_line_id
    LEFT JOIN policy.school lh ON lh.id = l.school_id
    LEFT JOIN policy.school h ON h.id = x.institution_id
    LEFT JOIN policy.control_line k ON k.id = x.control_line_id
   ORDER BY x.signed_at, x.action = 'publish', x.id $$;

-- ---------- 6. 旧列归档与回填（草稿，需两名 staff 重新签字发布） ----------
CREATE TABLE policy.admission_line_control_line_archive (
  admission_line_id  uuid PRIMARY KEY REFERENCES policy.admission_line (id),
  control_line       numeric(5, 1) NOT NULL,
  line_status        policy.pub_status NOT NULL,
  archived_at        timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE policy.admission_line_control_line_archive IS
  '0047 删列前招录行上的旧控制线原样归档（招录行 id、值、当时状态）：只读，供人核对；已撤下行的值只在这里。无账号列（规则 13），BFF 无权限';
ALTER TABLE policy.admission_line_control_line_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.admission_line_control_line_archive FORCE ROW LEVEL SECURITY;
REVOKE ALL ON policy.admission_line_control_line_archive FROM PUBLIC;
GRANT SELECT ON policy.admission_line_control_line_archive TO starbuddy_kge, starbuddy_learning;
CREATE POLICY control_line_archive_read ON policy.admission_line_control_line_archive FOR SELECT TO starbuddy_kge, starbuddy_learning USING (true);

DO $$
DECLARE g record; v_n integer := 0;
BEGIN
  -- 锁住招录行：归档、回填与删列之间不能有并发写入（不挡读；按要求以单事务执行时锁持有到事务结束）
  LOCK TABLE policy.admission_line IN SHARE ROW EXCLUSIVE MODE;
  INSERT INTO policy.admission_line_control_line_archive (admission_line_id, control_line, line_status)
  SELECT l.id, l.control_line, l.status FROM policy.admission_line l WHERE l.control_line IS NOT NULL;
  FOR g IN SELECT h.city, l.year, l.batch_code, l.track, count(DISTINCT l.control_line) AS n
             FROM policy.admission_line l JOIN policy.school h ON h.id = l.school_id
            WHERE l.control_line IS NOT NULL AND l.status = 'published'
            GROUP BY h.city, l.year, l.batch_code, l.track HAVING count(DISTINCT l.control_line) > 1 LOOP
    RAISE EXCEPTION '0047 中止：% % 届 % % 的已发布招录行上有 % 个不同的控制线，控制线按城市一条，请人先核对来源再执行',
                    g.city, g.year, g.batch_code, g.track, g.n USING ERRCODE = 'object_not_in_prerequisite_state';
  END LOOP;
  FOR g IN SELECT DISTINCT ON (h.city, l.year, l.batch_code, l.track) h.city, l.year, l.batch_code, l.track, l.control_line, l.source_document_id
             FROM policy.admission_line l JOIN policy.school h ON h.id = l.school_id
            WHERE l.control_line IS NOT NULL AND l.status IN ('published', 'draft')
            ORDER BY h.city, l.year, l.batch_code, l.track, (l.status = 'published') DESC, l.created_at DESC, l.id LOOP
    BEGIN
      INSERT INTO policy.control_line (city, year, batch_code, track, score, source_document_id)
      VALUES (g.city, g.year, g.batch_code, g.track, g.control_line, g.source_document_id);
    EXCEPTION WHEN check_violation THEN
      RAISE EXCEPTION '0047 中止：回填 % % 届 % % 的控制线 % 不满足控制线规则（%），请人先处理再执行',
                      g.city, g.year, g.batch_code, g.track, g.control_line, SQLERRM USING ERRCODE = 'object_not_in_prerequisite_state';
    END;
    v_n := v_n + 1;
  END LOOP;
  RAISE NOTICE '0047：回填 % 条控制线草稿，需两名 staff 重新签字发布', v_n;
END $$;

-- ---------- 7. 招录行去掉控制线 ----------
-- 守卫换成不含控制线的版本（其余与 0013 相同）
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
        (NEW.school_id, NEW.year, NEW.batch_code, NEW.track, NEW.line_score, NEW.plan_count,
         NEW.source_document_id, NEW.reviewers, NEW.reviewed_at, NEW.created_at)
        IS DISTINCT FROM
        (OLD.school_id, OLD.year, OLD.batch_code, OLD.track, OLD.line_score, OLD.plan_count,
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
  IF NEW.line_score > v_total THEN
    RAISE EXCEPTION '分数超过该年方案的计分总分 %', v_total USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
ALTER TABLE policy.admission_line DROP CONSTRAINT admission_line_control_line;
ALTER TABLE policy.admission_line DROP COLUMN control_line;
COMMENT ON TABLE policy.admission_line IS '学校 × 年度 × 批次 × 招生类别的投档线与计划数；统招与指标生分开（v1.3 §5.1）。只收官方公告。控制线在 policy.control_line（0047）';
