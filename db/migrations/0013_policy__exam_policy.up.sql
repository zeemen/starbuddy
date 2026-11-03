-- 0013 policy · 考情政策库与招录库（specs/policy-schema-v0.9/spec.md）
-- 状态：草稿 v0.9。只写文件，由人执行（CLAUDE.md 必停项：迁移、RLS 与权限）。
-- 依据：平台规划 v1.3 增补篇第五章（去向层：学校线带、指标生）、第六章（考情政策库：城市 × 中考届别、五层、审核线）。
-- 公共基础数据：不含任何学生数据，也没有学生键（规则 13）。写入方复用 starbuddy_kge（spec 待人确认 #1，按默认）。
-- 本迁移只改 policy schema（ADR 02 第 2 条）；学生档案的届别在 0014。
-- 请以单事务执行（psql -1）。只用标准 Postgres 16 能力。

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge') THEN
    RAISE EXCEPTION '角色 starbuddy_kge 不存在：请先执行 db/bootstrap/roles.sql 与迁移 0011';
  END IF;
  -- 发布的信任判断（guard_scheme 里的 current_user = 'starbuddy_authz'）依赖 authz 没有任何成员
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz' AND (rolcanlogin OR rolsuper))
     OR EXISTS (SELECT 1 FROM pg_roles r
                 WHERE NOT r.rolsuper AND r.rolname <> 'starbuddy_authz' AND pg_has_role(r.oid, 'starbuddy_authz', 'MEMBER')) THEN
    RAISE EXCEPTION 'starbuddy_authz 必须 NOLOGIN、非 SUPERUSER，且不能直接或间接授予任何角色';
  END IF;
END $$;

CREATE SCHEMA policy;
COMMENT ON SCHEMA policy IS '考情政策库（城市 × 中考届别）与招录库（学校 × 年度 × 批次）。公共基础数据；唯一写入方 starbuddy_kge；不含学生数据';
REVOKE ALL ON SCHEMA policy FROM PUBLIC;
GRANT USAGE ON SCHEMA policy TO starbuddy_kge, starbuddy_learning, starbuddy_bff, starbuddy_authz;

-- ---------- 公共类型 ----------
CREATE DOMAIN policy.city_code AS text CHECK (VALUE ~ '^[a-z][a-z0-9\-]{1,31}$');     -- 与 platform.student_profile.region 同一套编码
CREATE DOMAIN policy.cohort AS smallint CHECK (VALUE BETWEEN 2015 AND 2100);           -- 中考届别 = 中考年份
CREATE DOMAIN policy.code AS text CHECK (VALUE ~ '^[a-z][a-z0-9_\-]{0,47}$');
-- 学科码按学科，不按产品命名空间（spec 待人确认 #3：学科目录建成后改为外键）
CREATE TYPE policy.subject_code AS ENUM (
  'chinese', 'math', 'english', 'physics', 'chemistry', 'morality_law', 'history', 'biology', 'geography', 'pe', 'information_tech'
);
CREATE TYPE policy.pub_status AS ENUM ('draft', 'published', 'retired');

-- 审核人去重后的个数（与 kge.distinct_count 相同，本 schema 自带一份以免跨 schema 依赖）
CREATE FUNCTION policy.distinct_count(p text[]) RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT count(DISTINCT x)::integer FROM unnest(p) x $$;
-- 从 https 链接取出主机名（小写）
CREATE FUNCTION policy.url_host(p text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT lower(substring(p FROM '^https://([^/:?#]+)')) $$;
REVOKE EXECUTE ON FUNCTION policy.distinct_count(text[]), policy.url_host(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.distinct_count(text[]), policy.url_host(text) TO starbuddy_kge, starbuddy_learning, starbuddy_authz;

-- ---------- 官方来源域名（由人维护；应用角色都不能写：spec 待人确认 #4） ----------
CREATE TABLE policy.source_domain (
  host        text PRIMARY KEY CHECK (host ~ '^[a-z0-9][a-z0-9.\-]{2,252}$'),
  issuer      text NOT NULL CHECK (char_length(issuer) BETWEEN 1 AND 64),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  added_by    text NOT NULL CHECK (char_length(added_by) BETWEEN 1 AND 64),  -- 登记人工号
  added_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE policy.source_domain IS '可接受的官方来源主机名。新增主机走考情政策线审核后由人登记';

-- ---------- 第 1 层：来源文件（元数据） ----------
CREATE TABLE policy.source_document (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  city                policy.city_code NOT NULL,
  issuer              text NOT NULL CHECK (char_length(issuer) BETWEEN 1 AND 64),
  doc_number          text NOT NULL CHECK (char_length(doc_number) BETWEEN 1 AND 64),  -- 文号
  title               text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
  published_on        date NOT NULL,
  url                 text NOT NULL CHECK (url ~ '^https://[a-z0-9][a-z0-9.\-]{2,252}(/[A-Za-z0-9._~/+\-]*)?$'),
  applies_from_cohort policy.cohort NOT NULL,
  applies_to_cohort   policy.cohort,
  last_verified_on    date NOT NULL,
  status              policy.pub_status NOT NULL DEFAULT 'draft',
  reviewers           text[] NOT NULL DEFAULT '{}' CHECK (cardinality(reviewers) <= 4),   -- 复核人工号
  reviewed_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (city, issuer, doc_number),
  CONSTRAINT source_document_cohort_range CHECK (applies_to_cohort IS NULL OR applies_to_cohort >= applies_from_cohort),
  CONSTRAINT source_document_verified_after_published CHECK (last_verified_on >= published_on),
  -- 考情政策线：发布须两位不同的复核人
  CONSTRAINT source_document_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL))
);
COMMENT ON TABLE policy.source_document IS '官方来源文件。所有方案与招录行都必须引用已发布的来源文件；无法核对来源的不收录（v1.3 §6.4）';

-- ---------- 方案（城市 × 届别，版本化） ----------
CREATE TABLE policy.exam_scheme (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  city                policy.city_code NOT NULL,
  cohort              policy.cohort NOT NULL,
  version             text NOT NULL CHECK (version ~ '^\d{1,3}\.\d{1,3}\.\d{1,4}$'),
  source_document_id  uuid NOT NULL REFERENCES policy.source_document (id),
  status              policy.pub_status NOT NULL DEFAULT 'draft',
  reviewers           text[] NOT NULL DEFAULT '{}' CHECK (cardinality(reviewers) <= 4),
  reviewed_at         timestamptz,
  published_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (city, cohort, version),
  CONSTRAINT exam_scheme_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL)),
  CONSTRAINT exam_scheme_published_at CHECK ((status <> 'published' OR published_at IS NOT NULL) AND (status <> 'draft' OR published_at IS NULL))
);
CREATE UNIQUE INDEX exam_scheme_one_published ON policy.exam_scheme (city, cohort) WHERE status = 'published';
COMMENT ON TABLE policy.exam_scheme IS '某城市某届别的中考方案。发布只能经 policy.publish_scheme；非草稿方案的内容不能再改，改就发新版本';

-- ---------- 第 2、3 层：科目配置 ----------
CREATE TABLE policy.subject_config (
  scheme_id           uuid NOT NULL REFERENCES policy.exam_scheme (id),
  subject             policy.subject_code NOT NULL,
  scored              boolean NOT NULL,                                     -- 是否计入中考总分
  full_score          numeric(5, 1) CHECK (full_score > 0 AND full_score <= 300),
  proposition         text NOT NULL CHECK (proposition IN ('city', 'province')),  -- 命题方式
  exam_form           text NOT NULL CHECK (exam_form IN ('closed_book', 'open_book', 'practical', 'oral', 'assessment')),
  duration_minutes    smallint CHECK (duration_minutes BETWEEN 10 AND 300),
  combined_group      policy.code,                                          -- 合场分组（例如道法与历史合场）
  taken_in_grade      smallint NOT NULL CHECK (taken_in_grade BETWEEN 7 AND 9),
  exam_month          smallint CHECK (exam_month BETWEEN 1 AND 12),  -- 考查时间（月份），驱动点点日程提醒（v1.3 §6.1 第 3 层）
  eligibility_effect  text NOT NULL DEFAULT 'none' CHECK (eligibility_effect IN ('none', 'graduation', 'admission', 'graduation_and_admission')),
  PRIMARY KEY (scheme_id, subject),
  -- 计分科目必须有满分；非计分科目（第 3 层考查科目）不写满分，总分只由计分科目算
  CONSTRAINT subject_config_scored_full_score CHECK (scored = (full_score IS NOT NULL))
);

-- ---------- 第 4 层：过渡规则 ----------
CREATE TABLE policy.transition_rule (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scheme_id           uuid NOT NULL REFERENCES policy.exam_scheme (id),
  change_kind         text NOT NULL CHECK (change_kind IN ('total_score', 'scored_subjects', 'exam_form', 'batches', 'other')),
  replaces_scheme_id  uuid REFERENCES policy.exam_scheme (id),
  CONSTRAINT transition_not_self CHECK (replaces_scheme_id IS DISTINCT FROM scheme_id),
  UNIQUE (scheme_id, change_kind)
);
COMMENT ON TABLE policy.transition_rule IS '与上一届方案相比的变化；去向引擎据此在“直接比较”与“折算比较”之间切换（v1.3 §5.1）';

-- ---------- 第 5 层：招录规则 ----------
CREATE TABLE policy.admission_rule (
  scheme_id           uuid NOT NULL REFERENCES policy.exam_scheme (id),
  batch_code          policy.code NOT NULL,
  batch_order         smallint NOT NULL CHECK (batch_order BETWEEN 1 AND 20),
  track               text NOT NULL CHECK (track IN ('unified', 'quota_allocation', 'other')),  -- 统招 / 指标生 / 其他
  control_line_basis  text NOT NULL DEFAULT 'none' CHECK (control_line_basis IN ('none', 'total_score', 'batch_line')),
  PRIMARY KEY (scheme_id, batch_code, track),
  -- 控制线只对指标生有意义
  CONSTRAINT admission_rule_control_line CHECK (track = 'quota_allocation' OR control_line_basis = 'none')
);

-- ---------- 招录库 ----------
CREATE TABLE policy.school (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  city          policy.city_code NOT NULL,
  code          policy.code NOT NULL,
  public_name   text NOT NULL CHECK (char_length(public_name) BETWEEN 1 AND 64),  -- 学校公开名称；不收任何师生个人信息
  level_code    policy.code NOT NULL,                                               -- 办学层级（编码）
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  source_document_id  uuid NOT NULL REFERENCES policy.source_document (id),
  review_status policy.pub_status NOT NULL DEFAULT 'draft',
  reviewers     text[] NOT NULL DEFAULT '{}' CHECK (cardinality(reviewers) <= 4),
  reviewed_at   timestamptz,
  UNIQUE (city, code),
  CONSTRAINT school_two_reviewers CHECK (review_status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL))
);
COMMENT ON TABLE policy.school IS '高中学校的公开信息。初中学校与指标生按初中分配的名额 v0.9 不收（spec 待人确认 #5）';

CREATE TABLE policy.admission_line (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id           uuid NOT NULL REFERENCES policy.school (id),
  year                policy.cohort NOT NULL,
  batch_code          policy.code NOT NULL,
  track               text NOT NULL CHECK (track IN ('unified', 'quota_allocation', 'other')),
  line_score          numeric(5, 1) CHECK (line_score >= 0),
  plan_count          integer CHECK (plan_count >= 0),
  control_line        numeric(5, 1) CHECK (control_line >= 0),
  source_document_id  uuid NOT NULL REFERENCES policy.source_document (id),
  status              policy.pub_status NOT NULL DEFAULT 'draft',
  reviewers           text[] NOT NULL DEFAULT '{}' CHECK (cardinality(reviewers) <= 4),
  reviewed_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admission_line_has_value CHECK (line_score IS NOT NULL OR plan_count IS NOT NULL),
  CONSTRAINT admission_line_control_line CHECK (track = 'quota_allocation' OR control_line IS NULL),
  CONSTRAINT admission_line_two_reviewers CHECK (status = 'draft' OR (policy.distinct_count(reviewers) >= 2 AND reviewed_at IS NOT NULL))
);
-- 同一学校同一年同一批次同一类别只有一条已发布行；更正走新行 + 撤下旧行
CREATE UNIQUE INDEX admission_line_one_published ON policy.admission_line (school_id, year, batch_code, track) WHERE status = 'published';
COMMENT ON TABLE policy.admission_line IS '学校 × 年度 × 批次 × 招生类别的投档线、计划数与控制线；统招与指标生分开（v1.3 §5.1）。只收官方公告';

-- ---------- 计分总分（由库计算，不另存：规则 2） ----------
CREATE FUNCTION policy.scheme_total(p_scheme uuid) RETURNS numeric
  LANGUAGE sql STABLE SET search_path = pg_catalog, policy, pg_temp
  AS $$ SELECT sum(full_score) FROM policy.subject_config WHERE scheme_id = p_scheme AND scored $$;
REVOKE EXECUTE ON FUNCTION policy.scheme_total(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.scheme_total(uuid) TO starbuddy_kge, starbuddy_learning, starbuddy_bff, starbuddy_authz;

-- ---------- 保护触发器（SECURITY INVOKER） ----------
-- 来源文件：主机名必须在官方来源清单里；已发布的只能改核对日期与状态（→ 撤下）
CREATE FUNCTION policy.guard_source_document() RETURNS trigger LANGUAGE plpgsql
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
CREATE TRIGGER guard_source_document BEFORE INSERT OR UPDATE ON policy.source_document
  FOR EACH ROW EXECUTE FUNCTION policy.guard_source_document();

-- 方案：新建只能是草稿；发布只能经 policy.publish_scheme（属主 authz）；非草稿只能改状态；撤下是终态
CREATE FUNCTION policy.guard_scheme() RETURNS trigger LANGUAGE plpgsql
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
CREATE TRIGGER guard_scheme BEFORE INSERT OR UPDATE ON policy.exam_scheme
  FOR EACH ROW EXECUTE FUNCTION policy.guard_scheme();

-- 方案内容冻结：已发布或已撤下方案的科目配置、过渡规则、招录规则不能新增或修改。
-- 对方案行加共享锁，与发布函数的 FOR UPDATE 互斥（与 kge.guard_frozen 同一做法，防发布竞态）
CREATE FUNCTION policy.guard_scheme_frozen() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE
  v_ids uuid[] := ARRAY[NEW.scheme_id] || CASE WHEN TG_OP = 'UPDATE' THEN ARRAY[OLD.scheme_id] ELSE '{}'::uuid[] END;
  v_status policy.pub_status;
  v_seen integer := 0;
BEGIN
  FOR v_status IN SELECT s.status FROM policy.exam_scheme s WHERE s.id = ANY (v_ids) ORDER BY s.id FOR SHARE LOOP
    v_seen := v_seen + 1;
    IF v_status <> 'draft' THEN
      RAISE EXCEPTION '方案已发布或已撤下，% 不能再改（改就发新版本）', TG_TABLE_NAME USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  IF v_seen = 0 THEN
    RAISE EXCEPTION '% 引用的方案不存在或不可见', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guard_scheme_frozen BEFORE INSERT OR UPDATE ON policy.subject_config FOR EACH ROW EXECUTE FUNCTION policy.guard_scheme_frozen();
CREATE TRIGGER guard_scheme_frozen BEFORE INSERT OR UPDATE ON policy.transition_rule FOR EACH ROW EXECUTE FUNCTION policy.guard_scheme_frozen();
CREATE TRIGGER guard_scheme_frozen BEFORE INSERT OR UPDATE ON policy.admission_rule FOR EACH ROW EXECUTE FUNCTION policy.guard_scheme_frozen();

-- 招录行：
--   - 来源文件已发布、同城市、覆盖该年；该年有已发布方案，分数不超过方案的计分总分；
--   - 已发布的行只能撤下，不能改数值（更正走新行，保留追溯）
CREATE FUNCTION policy.guard_admission_line() RETURNS trigger LANGUAGE plpgsql
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
CREATE TRIGGER guard_admission_line BEFORE INSERT OR UPDATE ON policy.admission_line
  FOR EACH ROW EXECUTE FUNCTION policy.guard_admission_line();
-- 学校：来源文件已发布且同城市；复核发布后只能改办学状态（停办），更正走新的来源与复核
CREATE FUNCTION policy.guard_school() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM policy.source_document d WHERE d.id = NEW.source_document_id AND d.status = 'published' AND d.city = NEW.city) THEN
    RAISE EXCEPTION '学校的来源文件未发布或不是同一城市' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.review_status = 'published'
     AND (NEW.city, NEW.code, NEW.public_name, NEW.level_code, NEW.review_status, NEW.reviewers, NEW.reviewed_at)
         IS DISTINCT FROM (OLD.city, OLD.code, OLD.public_name, OLD.level_code, OLD.review_status, OLD.reviewers, OLD.reviewed_at) THEN
    RAISE EXCEPTION '已复核发布的学校只能改办学状态' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guard_school BEFORE INSERT OR UPDATE ON policy.school FOR EACH ROW EXECUTE FUNCTION policy.guard_school();
REVOKE EXECUTE ON FUNCTION policy.guard_source_document(), policy.guard_scheme(), policy.guard_scheme_frozen(),
  policy.guard_admission_line(), policy.guard_school() FROM PUBLIC;

-- ---------- 发布前校验与发布 ----------
CREATE FUNCTION policy.validate_scheme(p_scheme uuid)
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
REVOKE EXECUTE ON FUNCTION policy.validate_scheme(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.validate_scheme(uuid) TO starbuddy_kge, starbuddy_learning, starbuddy_authz;

CREATE FUNCTION policy.publish_scheme(p_scheme uuid) RETURNS timestamptz
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
ALTER FUNCTION policy.publish_scheme(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION policy.publish_scheme(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.publish_scheme(uuid) TO starbuddy_kge;

-- ---------- 表权限：只有写入方能写；任何角色都没有 DELETE（撤下走状态） ----------
GRANT SELECT ON policy.source_domain TO starbuddy_kge, starbuddy_learning;
GRANT SELECT, INSERT ON policy.source_document, policy.exam_scheme, policy.admission_line TO starbuddy_kge;
GRANT UPDATE (last_verified_on, status, reviewers, reviewed_at, issuer, doc_number, title, published_on, url,
              applies_from_cohort, applies_to_cohort)
  ON policy.source_document TO starbuddy_kge;
GRANT UPDATE (version, source_document_id, status, reviewers, reviewed_at) ON policy.exam_scheme TO starbuddy_kge;
GRANT UPDATE (line_score, plan_count, control_line, source_document_id, status, reviewers, reviewed_at)
  ON policy.admission_line TO starbuddy_kge;
GRANT SELECT, INSERT, UPDATE ON policy.subject_config, policy.transition_rule, policy.admission_rule TO starbuddy_kge;
-- 学校的城市与编码写入后不能改（已发布招录行“来源同城市”的前提）
GRANT SELECT, INSERT ON policy.school TO starbuddy_kge;
GRANT UPDATE (public_name, level_code, status, source_document_id, review_status, reviewers, reviewed_at) ON policy.school TO starbuddy_kge;
GRANT SELECT ON policy.source_document, policy.exam_scheme, policy.subject_config, policy.transition_rule, policy.admission_rule,
                policy.school, policy.admission_line
  TO starbuddy_learning;
-- BFF（中考百科、学校卡、目标学校追踪）：只读已发布数据，复核人不给前端
GRANT SELECT (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, applies_to_cohort, last_verified_on, status)
  ON policy.source_document TO starbuddy_bff;
GRANT SELECT (id, city, cohort, version, source_document_id, status, published_at) ON policy.exam_scheme TO starbuddy_bff;
GRANT SELECT ON policy.subject_config, policy.transition_rule, policy.admission_rule TO starbuddy_bff;
GRANT SELECT (id, city, code, public_name, level_code, status, source_document_id, review_status) ON policy.school TO starbuddy_bff;
GRANT SELECT (id, school_id, year, batch_code, track, line_score, plan_count, control_line, source_document_id, status)
  ON policy.admission_line TO starbuddy_bff;
-- 发布函数的属主：跑校验、改方案状态
GRANT SELECT ON policy.source_document, policy.exam_scheme, policy.subject_config, policy.transition_rule, policy.admission_rule
  TO starbuddy_authz;
GRANT UPDATE (status, published_at) ON policy.exam_scheme TO starbuddy_authz;
-- 只为发布时对来源文件加 FOR SHARE 锁（行锁要求 UPDATE 权限）；函数体不更新来源文件
GRANT UPDATE (last_verified_on) ON policy.source_document TO starbuddy_authz;

-- ---------- RLS ----------
ALTER TABLE policy.source_domain ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.source_domain FORCE ROW LEVEL SECURITY;
CREATE POLICY source_domain_read ON policy.source_domain FOR SELECT TO starbuddy_kge, starbuddy_learning USING (true);

ALTER TABLE policy.source_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.source_document FORCE ROW LEVEL SECURITY;
CREATE POLICY source_document_kge ON policy.source_document FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY source_document_read ON policy.source_document FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY source_document_front_read ON policy.source_document FOR SELECT TO starbuddy_bff USING (status = 'published');

ALTER TABLE policy.exam_scheme ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.exam_scheme FORCE ROW LEVEL SECURITY;
CREATE POLICY exam_scheme_kge ON policy.exam_scheme FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY exam_scheme_read ON policy.exam_scheme FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY exam_scheme_front_read ON policy.exam_scheme FOR SELECT TO starbuddy_bff USING (status = 'published');

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['subject_config', 'transition_rule', 'admission_rule'] LOOP
    EXECUTE format('ALTER TABLE policy.%I ENABLE ROW LEVEL SECURITY', r);
    EXECUTE format('ALTER TABLE policy.%I FORCE ROW LEVEL SECURITY', r);
    EXECUTE format('CREATE POLICY %I ON policy.%I FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true)', r || '_kge', r);
    EXECUTE format('CREATE POLICY %I ON policy.%I FOR SELECT TO starbuddy_learning USING (true)', r || '_read', r);
    EXECUTE format('CREATE POLICY %I ON policy.%I FOR SELECT TO starbuddy_bff
                      USING (EXISTS (SELECT 1 FROM policy.exam_scheme s WHERE s.id = scheme_id AND s.status = ''published''))',
                   r || '_front_read', r);
  END LOOP;
END $$;

ALTER TABLE policy.school ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.school FORCE ROW LEVEL SECURITY;
CREATE POLICY school_kge ON policy.school FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY school_read ON policy.school FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY school_front_read ON policy.school FOR SELECT TO starbuddy_bff USING (review_status = 'published');

ALTER TABLE policy.admission_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy.admission_line FORCE ROW LEVEL SECURITY;
CREATE POLICY admission_line_kge ON policy.admission_line FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY admission_line_read ON policy.admission_line FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY admission_line_front_read ON policy.admission_line FOR SELECT TO starbuddy_bff USING (status = 'published');

-- kge 地图发布前校验会读政策库（地区投影的城市与考试年份要有已发布方案，警告级）：给 kge 校验的调用方读方案的权限已在上面
