-- 0046 policy · 开放已撤下的往年方案；草稿不能直接撤下（specs/policy-decisions-v0.9.1/spec.md 第 5 节，PR1；用户结论 5）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。请以单事务执行（psql -1）。只改 policy（ADR 02）。
-- 依赖 0013（policy 表、BFF 读取策略、方案与来源文件的保护触发器）、0041（发布收口）。
-- 要点：
--   1. BFF 对 policy.exam_scheme、policy.source_document 的读取放宽到已发布与已撤下；已撤下方案随方案冻结的科目配置、过渡规则、
--      招录规则一并可读（不然“往年政策”只有空壳）；草稿仍不可见，复核人列（列级授权）仍不给；学校、招录行仍只读已发布（未在结论内）；
--   2. 草稿不能直接撤下（审查阻断 1）：放宽之后“已撤下”对前端可见，而草稿从未经两人签字；如果写入方能把草稿改成已撤下，
--      没复核过的内容就会以“往年政策”出现。放弃的草稿留在草稿。方案的状态只允许 草稿 → 已发布（经 publish_scheme）→ 已撤下；
--      来源文件同样禁止 草稿 → 已撤下。守卫以 CREATE OR REPLACE 换新版本（其余与 0013 相同）；控制线（0047）自带同样的规则。
--      0041 的 *_reviewed CHECK（非草稿必须有复核时间）是同一条规则的兜底：草稿没有复核时间。
-- 读取约定（docs/design/policy/data-entry-rules.md）：同城市同届别优先读已发布；该届没有已发布方案时才展示已撤下的往年方案。

-- 数据前提：库里已有“草稿直接撤下”的方案或来源文件（复核时间为空的已撤下行）时，放宽后它们会对前端可见；0041 的 CHECK 已保证不会有，这里再核一次
DO $$
DECLARE v_scheme bigint; v_doc bigint;
BEGIN
  SELECT count(*) INTO v_scheme FROM policy.exam_scheme WHERE status = 'retired' AND reviewed_at IS NULL;
  SELECT count(*) INTO v_doc FROM policy.source_document WHERE status = 'retired' AND reviewed_at IS NULL;
  IF v_scheme + v_doc > 0 THEN
    RAISE EXCEPTION '0046 中止：有 % 个方案、% 份来源文件未经复核就已撤下，放宽读取后会对前端可见；请人先处理', v_scheme, v_doc
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- ---------- 1. 方案守卫：草稿不能直接撤下 ----------
CREATE OR REPLACE FUNCTION policy.guard_scheme() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' OR NEW.published_at IS NOT NULL THEN
      RAISE EXCEPTION '方案只能以草稿新建' USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF NEW.id <> OLD.id OR NEW.city <> OLD.city OR NEW.cohort <> OLD.cohort OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION '方案的 ID、城市、届别与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status <> 'draft' AND (NEW.version, NEW.source_document_id, NEW.reviewers, NEW.reviewed_at)
                               IS DISTINCT FROM (OLD.version, OLD.source_document_id, OLD.reviewers, OLD.reviewed_at) THEN
    RAISE EXCEPTION '非草稿方案只能改状态' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF OLD.status = 'draft' AND NEW.status = 'retired' THEN
      RAISE EXCEPTION '草稿不能直接撤下（放弃的草稿留在草稿；已撤下的方案对前端可见）' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT ((OLD.status = 'draft' AND NEW.status = 'published') OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
      RAISE EXCEPTION '不允许的方案状态变化：% → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'published' AND current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '方案只能经 policy.publish_scheme 发布' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF NEW.published_at IS DISTINCT FROM OLD.published_at THEN
    RAISE EXCEPTION '发布时间只能在发布时写入' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- ---------- 2. 来源文件守卫：草稿不能直接撤下 ----------
CREATE OR REPLACE FUNCTION policy.guard_source_document() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  -- 只在新建或改链接时核对主机名：主机名下线后，挂在它下面的文件仍要能更新核对日期或撤下
  IF (TG_OP = 'INSERT' OR NEW.url IS DISTINCT FROM OLD.url)
     AND NOT EXISTS (SELECT 1 FROM policy.source_domain d WHERE d.host = policy.url_host(NEW.url) AND d.status = 'active') THEN
    RAISE EXCEPTION '来源链接的主机名不在官方来源清单里' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF OLD.status = 'retired' THEN
    RAISE EXCEPTION '已撤下的来源文件不能再改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'draft' AND NEW.status = 'retired' THEN
    RAISE EXCEPTION '草稿不能直接撤下（放弃的草稿留在草稿；已撤下的来源文件对前端可见）' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'published' AND (NEW.city, NEW.issuer, NEW.doc_number, NEW.title, NEW.published_on, NEW.url, NEW.applies_from_cohort,
                                   NEW.applies_to_cohort, NEW.reviewers, NEW.reviewed_at, NEW.created_at)
                                  IS DISTINCT FROM
                                  (OLD.city, OLD.issuer, OLD.doc_number, OLD.title, OLD.published_on, OLD.url, OLD.applies_from_cohort,
                                   OLD.applies_to_cohort, OLD.reviewers, OLD.reviewed_at, OLD.created_at) THEN
    RAISE EXCEPTION '已发布的来源文件只能更新核对日期或撤下' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'draft' AND OLD.status <> 'draft' THEN
    RAISE EXCEPTION '来源文件不能退回草稿' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- ---------- 3. 往年方案：BFF 可读已发布与已撤下（草稿仍不可见；复核人列的列级授权不变） ----------
DROP POLICY source_document_front_read ON policy.source_document;
CREATE POLICY source_document_front_read ON policy.source_document FOR SELECT TO starbuddy_bff USING (status IN ('published', 'retired'));
DROP POLICY exam_scheme_front_read ON policy.exam_scheme;
CREATE POLICY exam_scheme_front_read ON policy.exam_scheme FOR SELECT TO starbuddy_bff USING (status IN ('published', 'retired'));
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['subject_config', 'transition_rule', 'admission_rule'] LOOP
    EXECUTE format('DROP POLICY %I ON policy.%I', r || '_front_read', r);
    EXECUTE format('CREATE POLICY %I ON policy.%I FOR SELECT TO starbuddy_bff
                      USING (EXISTS (SELECT 1 FROM policy.exam_scheme s WHERE s.id = scheme_id AND s.status IN (''published'', ''retired'')))',
                   r || '_front_read', r);
  END LOOP;
END $$;
