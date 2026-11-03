-- 0010 insight · 判断账本：判断模板、判断、判断历史与状态机（specs/sie-00-field-dictionary/spec.md 设计要点 3）
-- 状态：草稿 v0.9。只写文件，由人执行（CLAUDE.md 必停项：迁移、RLS、数据库角色、判断模板）。
-- 依据：SIE V1.1 第三章“判断账本”、第五章图 2“判断的生命周期”、第九章 insight 数据模型；ADR 13（草稿）判断账本唯一写入方。
-- 按对照文档 #3：已有的个人层表留在 platform.*，insight.* 只放新增对象。本迁移只建判断账本，SIE 第九章其余表由 SIE-01/02/03 建。
-- 判断是对外陈述层，不是计算输入：掌握、观测、错因的真相源仍是 platform.* 的推断表，判断经 basis_kind / basis_id 引用它们（规则 2）。
-- 请以单事务执行（psql -1）。只用标准 Postgres 16 能力。

-- 洞察服务角色由人在 db/bootstrap/roles.sql 创建；这里只断言它存在且属性安全，并且不与其他应用角色共存于同一登录角色
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_insight') THEN
    RAISE EXCEPTION '角色 starbuddy_insight 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_insight' AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'starbuddy_insight 不能是 SUPERUSER 或 BYPASSRLS';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_insight', 'MEMBER')
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_review', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_scheduler', 'MEMBER'))) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_insight 与 bff / learning / review / scheduler';
  END IF;
END $$;

CREATE SCHEMA insight;
COMMENT ON SCHEMA insight IS '学生洞察引擎（SIE）：判断账本等新增对象。写入方只有洞察服务（ADR 13 草稿）';
REVOKE ALL ON SCHEMA insight FROM PUBLIC;
GRANT USAGE ON SCHEMA insight TO starbuddy_insight, starbuddy_learning, starbuddy_bff, starbuddy_authz;
-- 洞察服务要用 platform 的公共类型（confidence、model_version、subject）；不授予任何 platform 表权限
GRANT USAGE ON SCHEMA platform TO starbuddy_insight;

CREATE TYPE insight.claim_status AS ENUM ('hypothesis', 'established', 'to_verify', 'refuted', 'withdrawn');
COMMENT ON TYPE insight.claim_status IS '假设 / 成立 / 待验证 / 推翻 / 撤回（完整方案 v1.1 4.4）；推翻与撤回是终态';
CREATE TYPE insight.claim_target AS ENUM ('node', 'skill', 'metric', 'exam', 'preference');
CREATE TYPE insight.source_type AS ENUM ('inferred', 'self', 'guardian', 'objective');
-- 判断依据的 platform 推断（真相源）；metric / exam / preference 类的依据表由 SIE-01/02/03 建，届时扩展本枚举
CREATE TYPE insight.basis_kind AS ENUM ('mastery_estimate', 'skill_observation', 'error_attribution');

-- ---------- 判断模板登记表（必停项：由人审核后登记；应用角色都不能写） ----------
CREATE TABLE insight.claim_template (
  template_key    text NOT NULL CHECK (template_key ~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$'),
  version         integer NOT NULL CHECK (version >= 1),
  target_type     insight.claim_target NOT NULL,
  max_visibility  text[] NOT NULL
                  CHECK (max_visibility <@ ARRAY['agent', 'student', 'guardian'] AND cardinality(max_visibility) >= 1),
  -- 判断的最长有效期：到期时间不能超过“写入时刻 + 本值”，续期也受限（会过期，不能靠延长到期时间绕开待验证）
  max_ttl         interval NOT NULL CHECK (max_ttl BETWEEN interval '1 day' AND interval '365 days'),
  text_template   text CHECK (char_length(text_template) BETWEEN 1 AND 500),
  status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
  reviewed_by     text CHECK (char_length(reviewed_by) BETWEEN 1 AND 64),  -- 审核人工号或角色名，不是账号 ID
  reviewed_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (template_key, version),
  CHECK (status = 'draft' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  -- 对学生或家长可见的模板必须有结论文本模板（模板渲染为主，模型只写衔接语：KGE V1.1 第六章）
  CHECK (max_visibility <@ ARRAY['agent'] OR text_template IS NOT NULL)
);
COMMENT ON TABLE insight.claim_template IS
  '判断模板：新判断只能引用已审核（approved）的模板；没有模板的结论写不进账本。登记前须过禁用推断词表检查（docs/design/sie/sie_lexicon.py）';

-- ---------- 流转与修改原因 ----------
-- 状态变化（SIE V1.1 图 2；spec 待人确认 #1 按默认：假设不能直接推翻）与不改状态的修改，都必须写明原因。
-- 原因写在判断行的 change_reason 列里，与修改同一条语句提交，由写入前触发器校验、写入后触发器原样记入历史（不经会话变量）。
CREATE FUNCTION insight.change_allowed(p_from insight.claim_status, p_to insight.claim_status, p_reason text)
  RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$
    SELECT EXISTS (
      SELECT 1 FROM (VALUES
        (NULL::insight.claim_status, 'hypothesis'::insight.claim_status, 'created'),
        (NULL,                       'established',                      'created'),
        ('hypothesis',               'established',                      'evidence_threshold'),
        ('hypothesis',               'to_verify',                        'expired'),
        ('hypothesis',               'to_verify',                        'dispute'),
        ('hypothesis',               'to_verify',                        'exam_deviation'),
        ('established',              'to_verify',                        'expired'),
        ('established',              'to_verify',                        'dispute'),
        ('established',              'to_verify',                        'exam_deviation'),
        ('to_verify',                'established',                      'verification_supported'),
        ('to_verify',                'refuted',                          'verification_refuted'),
        ('hypothesis',               'withdrawn',                        'fact_corrected'),
        ('established',              'withdrawn',                        'fact_corrected'),
        ('to_verify',                'withdrawn',                        'fact_corrected'),
        -- 不改状态的修改：revised = 数值、置信度、证据、依据、模型版本或到期时间；visibility_changed = 可见范围
        ('hypothesis',               'hypothesis',                       'revised'),
        ('established',              'established',                      'revised'),
        ('to_verify',                'to_verify',                        'revised'),
        ('hypothesis',               'hypothesis',                       'visibility_changed'),
        ('established',              'established',                      'visibility_changed'),
        ('to_verify',                'to_verify',                        'visibility_changed')
      ) t(f, s, r)
      WHERE t.f IS NOT DISTINCT FROM p_from AND t.s = p_to AND t.r = p_reason) $$;
REVOKE EXECUTE ON FUNCTION insight.change_allowed(insight.claim_status, insight.claim_status, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.change_allowed(insight.claim_status, insight.claim_status, text) TO starbuddy_insight, starbuddy_authz;

-- ---------- 判断 ----------
CREATE TABLE insight.claim (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id        uuid NOT NULL REFERENCES platform.student_profile (id) ON DELETE CASCADE,
  subject           platform.subject,
  target_type       insight.claim_target NOT NULL,
  -- KGE-01 之前节点类判断指向 platform.skill_node 的 local_id；之后按 ADR“星图统一定义”改指 knowledge_node
  target_id         text NOT NULL CHECK (target_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$'),
  template_key      text NOT NULL,
  template_version  integer NOT NULL,
  -- 只放模板渲染要用的少量变量；数值以依据行为准，不在这里另存一份（规则 2）
  value             jsonb NOT NULL DEFAULT '{}'
                    CONSTRAINT claim_value_small CHECK (jsonb_typeof(value) = 'object' AND pg_column_size(value) <= 2048),
  confidence        platform.confidence NOT NULL,
  evidence_ids      uuid[] NOT NULL DEFAULT '{}',
  basis_kind        insight.basis_kind,
  basis_id          uuid,
  source_type       insight.source_type NOT NULL,
  model_version     platform.model_version,
  status            insight.claim_status NOT NULL,
  change_reason     text NOT NULL CHECK (change_reason ~ '^[a-z_]{1,32}$'),
  visibility        text[] NOT NULL CHECK (visibility <@ ARRAY['agent', 'student', 'guardian'] AND cardinality(visibility) >= 1),
  created_at        timestamptz NOT NULL DEFAULT now(),
  expires_at        timestamptz NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (template_key, template_version) REFERENCES insight.claim_template (template_key, version),
  CONSTRAINT claim_expires_after_created CHECK (expires_at > created_at),
  -- 推断必须带证据与模型版本（CLAUDE.md 规则 6）
  CONSTRAINT claim_inferred_has_evidence CHECK (source_type <> 'inferred' OR (cardinality(evidence_ids) >= 1 AND model_version IS NOT NULL)),
  -- 节点与能力类的推断必须引用它所依据的 platform 推断行（真相源）
  CONSTRAINT claim_basis_pair CHECK ((basis_kind IS NULL) = (basis_id IS NULL)),
  -- coalesce：basis_kind 为空时比较结果是 NULL，CHECK 会把 NULL 当作通过
  CONSTRAINT claim_inferred_has_basis CHECK (coalesce(
    source_type <> 'inferred' OR target_type NOT IN ('node', 'skill')
    OR (target_type = 'node' AND basis_kind = 'mastery_estimate')
    OR (target_type = 'skill' AND basis_kind IN ('skill_observation', 'error_attribution')), false)),
  -- 只有跨科指标与偏好可以不属于某一科
  CONSTRAINT claim_subject_required CHECK (subject IS NOT NULL OR target_type IN ('metric', 'preference'))
);
CREATE INDEX claim_student_status_idx ON insight.claim (student_id, status);
CREATE INDEX claim_target_idx ON insight.claim (student_id, target_type, target_id);
COMMENT ON TABLE insight.claim IS
  '判断账本：对学生的任何陈述必须对应一条 hypothesis / established 判断（CLAUDE.md 规则 11）。只有 starbuddy_insight 能写；是陈述层，不作为计算输入';

-- ---------- 判断历史：只能追加，由触发器写 ----------
CREATE TABLE insight.claim_history (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id       uuid NOT NULL REFERENCES insight.claim (id) ON DELETE CASCADE,
  from_status    insight.claim_status,  -- 新建时为空
  to_status      insight.claim_status NOT NULL,
  reason         text NOT NULL,
  -- 每次写入时的快照：可见范围扩大、数值改写、到期时间变化都有记录
  visibility     text[] NOT NULL,
  value          jsonb NOT NULL,
  confidence     platform.confidence NOT NULL,
  evidence_ids   uuid[] NOT NULL,
  basis_kind     insight.basis_kind,
  basis_id       uuid,
  model_version  platform.model_version,
  expires_at     timestamptz NOT NULL,
  at             timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT history_reason_allowed CHECK (insight.change_allowed(from_status, to_status, reason))
);
CREATE INDEX claim_history_claim_idx ON insight.claim_history (claim_id, at);
COMMENT ON TABLE insight.claim_history IS '判断的每次新建与修改（含原因与快照）：只能由触发器追加；除随判断级联删除外不能修改或删除';

-- 依据行必须存在且属于同一个学生（属主 authz 才读得到 platform 推断表；只返回真假）
CREATE FUNCTION insight.basis_belongs(p_kind insight.basis_kind, p_id uuid, p_student uuid) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
    SELECT CASE p_kind
      WHEN 'mastery_estimate' THEN EXISTS (SELECT 1 FROM platform.mastery_estimate m WHERE m.id = p_id AND m.student_id = p_student)
      WHEN 'skill_observation' THEN EXISTS (SELECT 1 FROM platform.skill_observation o WHERE o.id = p_id AND o.student_id = p_student)
      WHEN 'error_attribution' THEN EXISTS (SELECT 1 FROM platform.error_attribution e WHERE e.id = p_id AND e.student_id = p_student)
    END $$;
ALTER FUNCTION insight.basis_belongs(insight.basis_kind, uuid, uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.basis_belongs(insight.basis_kind, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insight.basis_belongs(insight.basis_kind, uuid, uuid) TO starbuddy_insight;
GRANT SELECT (id, student_id) ON platform.mastery_estimate, platform.skill_observation, platform.error_attribution TO starbuddy_authz;

-- 写入前：原因与状态变化匹配；模板、可见范围、有效期、依据、不可变列、终态
CREATE FUNCTION insight.claim_guard() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE
  v_tpl insight.claim_template%ROWTYPE;
  v_from insight.claim_status := CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END;
  v_moving_down boolean;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status IN ('refuted', 'withdrawn') THEN
    RAISE EXCEPTION '终态判断（%）不能再修改', OLD.status USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.created_at := now();
  ELSIF NEW.id <> OLD.id OR NEW.student_id <> OLD.student_id OR NEW.subject IS DISTINCT FROM OLD.subject
     OR NEW.target_type <> OLD.target_type OR NEW.target_id <> OLD.target_id OR NEW.template_key <> OLD.template_key
     OR NEW.template_version <> OLD.template_version OR NEW.source_type <> OLD.source_type
     OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION '判断的主体、对象、模板、来源与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();

  -- 原因：每次写入都要带，并与状态变化匹配；可见范围变化只能用 visibility_changed，且不能与其他修改混在一次
  IF NOT insight.change_allowed(v_from, NEW.status, NEW.change_reason) THEN
    RAISE EXCEPTION '不允许的状态流转或修改：% → %（原因 %）', v_from, NEW.status, NEW.change_reason USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.visibility IS DISTINCT FROM OLD.visibility AND NEW.change_reason <> 'visibility_changed' THEN
    RAISE EXCEPTION '修改可见范围必须单独提交，原因为 visibility_changed' USING ERRCODE = 'check_violation';
  END IF;

  -- 模板：新建与向“可展示”方向的修改必须引用已审核模板；转为待验证、推翻、撤回时，模板已退役也要允许（事实更正后能撤下）
  SELECT * INTO v_tpl FROM insight.claim_template WHERE template_key = NEW.template_key AND version = NEW.template_version;
  v_moving_down := TG_OP = 'UPDATE' AND NEW.status IN ('to_verify', 'refuted', 'withdrawn') AND NEW.status <> OLD.status;
  IF NOT FOUND OR NOT (v_tpl.status = 'approved' OR (v_moving_down AND v_tpl.status = 'retired')) THEN
    RAISE EXCEPTION '判断模板 %@% 未登记、未审核或已退役', NEW.template_key, NEW.template_version USING ERRCODE = 'check_violation';
  END IF;
  IF v_tpl.target_type <> NEW.target_type THEN
    RAISE EXCEPTION '判断对象类型 % 与模板 % 不符', NEW.target_type, v_tpl.target_type USING ERRCODE = 'check_violation';
  END IF;
  IF NOT (NEW.visibility <@ v_tpl.max_visibility) THEN
    RAISE EXCEPTION '可见范围 % 超出模板上限 %', NEW.visibility, v_tpl.max_visibility USING ERRCODE = 'check_violation';
  END IF;
  -- 有效期：到期时间不能超过“现在 + 模板最长有效期”（新建与每次修改都算，不能靠续期绕开待验证）
  IF (TG_OP = 'INSERT' OR NEW.expires_at IS DISTINCT FROM OLD.expires_at) AND NEW.expires_at > now() + v_tpl.max_ttl THEN
    RAISE EXCEPTION '到期时间超过模板最长有效期 %', v_tpl.max_ttl USING ERRCODE = 'check_violation';
  END IF;
  -- 依据：必须存在且属于同一个学生
  IF NEW.basis_kind IS NOT NULL
     AND (TG_OP = 'INSERT' OR NEW.basis_id IS DISTINCT FROM OLD.basis_id OR NEW.basis_kind IS DISTINCT FROM OLD.basis_kind)
     AND NOT insight.basis_belongs(NEW.basis_kind, NEW.basis_id, NEW.student_id) THEN
    RAISE EXCEPTION '判断依据 % 不存在或不属于该学生', NEW.basis_kind USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION insight.claim_guard() FROM PUBLIC;
CREATE TRIGGER claim_guard BEFORE INSERT OR UPDATE ON insight.claim
  FOR EACH ROW EXECUTE FUNCTION insight.claim_guard();

-- 写入后：每次新建与修改都写一行历史（原因取自已校验的行，不读会话变量）。属主 authz：洞察服务自己不能写历史
CREATE FUNCTION insight.claim_log() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
BEGIN
  INSERT INTO insight.claim_history (claim_id, from_status, to_status, reason, visibility, value, confidence,
                                     evidence_ids, basis_kind, basis_id, model_version, expires_at)
  VALUES (NEW.id, CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.status END, NEW.status, NEW.change_reason,
          NEW.visibility, NEW.value, NEW.confidence, NEW.evidence_ids, NEW.basis_kind, NEW.basis_id,
          NEW.model_version, NEW.expires_at);
  RETURN NULL;
END $$;
CREATE TRIGGER claim_log AFTER INSERT OR UPDATE ON insight.claim
  FOR EACH ROW EXECUTE FUNCTION insight.claim_log();

-- 历史只能追加：不能修改；只有所属判断已被删除（学生档案删除时的级联）才能删除
CREATE FUNCTION insight.claim_history_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION '判断历史只能追加，不能修改' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = OLD.claim_id) THEN
    RAISE EXCEPTION '判断历史不能单独删除（只随判断级联删除）' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER claim_history_guard BEFORE UPDATE OR DELETE ON insight.claim_history
  FOR EACH ROW EXECUTE FUNCTION insight.claim_history_guard();

ALTER FUNCTION insight.claim_log() OWNER TO starbuddy_authz;
ALTER FUNCTION insight.claim_history_guard() OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION insight.claim_log(), insight.claim_history_guard() FROM PUBLIC;

-- ---------- 表权限 ----------
GRANT SELECT ON insight.claim_template TO starbuddy_insight, starbuddy_learning, starbuddy_bff;
GRANT SELECT, INSERT ON insight.claim TO starbuddy_insight;
GRANT UPDATE (value, confidence, evidence_ids, basis_kind, basis_id, model_version, status, change_reason, visibility,
              expires_at, updated_at)
  ON insight.claim TO starbuddy_insight;
GRANT SELECT ON insight.claim TO starbuddy_learning, starbuddy_bff;
GRANT SELECT ON insight.claim_history TO starbuddy_insight;
-- 触发器函数的属主：写历史、核对所属判断是否还在
GRANT SELECT ON insight.claim TO starbuddy_authz;
GRANT INSERT ON insight.claim_history TO starbuddy_authz;

-- ---------- RLS ----------
ALTER TABLE insight.claim_template ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.claim_template FORCE ROW LEVEL SECURITY;
-- 前端与学科服务只看已审核模板；洞察服务还要看到已退役的（撤下使用退役模板的判断）
CREATE POLICY template_read ON insight.claim_template FOR SELECT TO starbuddy_learning, starbuddy_bff
  USING (status = 'approved');
CREATE POLICY template_insight_read ON insight.claim_template FOR SELECT TO starbuddy_insight
  USING (status IN ('approved', 'retired'));

ALTER TABLE insight.claim ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.claim FORCE ROW LEVEL SECURITY;
-- 学生本人只看可见范围含 student 的；有效绑定家长只看含 guardian 的。推翻、撤回、已过期、模板已退役的任何前端都看不到。
-- 模板条件经模板表自身的 RLS（前端只看得到 approved）生效
CREATE POLICY claim_front_read ON insight.claim FOR SELECT TO starbuddy_bff
  USING (status IN ('hypothesis', 'established', 'to_verify') AND expires_at > now()
         AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = claim.template_key AND t.version = claim.template_version)
         AND ((platform.is_self_student(student_id) AND 'student' = ANY (visibility))
              OR (platform.is_linked_guardian(student_id) AND 'guardian' = ANY (visibility))));
-- 学科服务（Agent 上下文）：只读可见范围含 agent、未过期、模板仍有效的非终态判断。按学生隔离由调用方负责（与其他 learning 策略一致，spec 待人确认）
CREATE POLICY claim_agent_read ON insight.claim FOR SELECT TO starbuddy_learning
  USING (status IN ('hypothesis', 'established', 'to_verify') AND expires_at > now() AND 'agent' = ANY (visibility)
         AND EXISTS (SELECT 1 FROM insight.claim_template t WHERE t.template_key = claim.template_key AND t.version = claim.template_version));
-- 洞察服务：唯一写入方；能力由列级授权限定（没有 DELETE）
CREATE POLICY claim_insight ON insight.claim FOR ALL TO starbuddy_insight USING (true) WITH CHECK (true);

ALTER TABLE insight.claim_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.claim_history FORCE ROW LEVEL SECURITY;
CREATE POLICY history_insight_read ON insight.claim_history FOR SELECT TO starbuddy_insight USING (true);
