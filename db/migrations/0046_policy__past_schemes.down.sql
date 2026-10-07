-- 0046 回滚：BFF 读取范围恢复为只读已发布（0013），方案与来源文件的保护触发器恢复 0013 原文（允许草稿直接撤下）。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。先回滚 0047 及之后的迁移。不动数据。

DROP POLICY source_document_front_read ON policy.source_document;
CREATE POLICY source_document_front_read ON policy.source_document FOR SELECT TO starbuddy_bff USING (status = 'published');
DROP POLICY exam_scheme_front_read ON policy.exam_scheme;
CREATE POLICY exam_scheme_front_read ON policy.exam_scheme FOR SELECT TO starbuddy_bff USING (status = 'published');
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['subject_config', 'transition_rule', 'admission_rule'] LOOP
    EXECUTE format('DROP POLICY %I ON policy.%I', r || '_front_read', r);
    EXECUTE format('CREATE POLICY %I ON policy.%I FOR SELECT TO starbuddy_bff
                      USING (EXISTS (SELECT 1 FROM policy.exam_scheme s WHERE s.id = scheme_id AND s.status = ''published''))',
                   r || '_front_read', r);
  END LOOP;
END $$;

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
    IF NOT ((OLD.status = 'draft' AND NEW.status IN ('published', 'retired')) OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
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
